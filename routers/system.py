"""
Sous-routeur pour les fonctions système, réglages, diagnostics et exports.
Regroupe les snapshots de registres, exports CSV, consultation des journaux applicatifs (app_logs),
enregistrement de configuration, test de connexion Agua IOT/Passerelle et notifications Webhook.
"""
from datetime import datetime
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Body, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from clients import (
    agua_client,
    borrow_http_client,
    is_safe_gateway_url,
    is_safe_webhook_url,
    local_gateway_client,
    notification_service,
    weather_service,
)
from config import MODE_DB_PATHS, TELEMETRY_COLUMNS
from database import (
    add_container_log,
    db_log,
    get_active_mode,
    get_container_logs,
    get_db,
    get_public_settings_dict,
    get_registers_dict,
    get_settings_dict,
    get_sys_db,
)
from matter_service import matter_manager
from routers.installer import clear_installer_sessions, require_installer_auth_if_configured
from scheduler import scheduler
from schemas import (
    ClientErrorPayload,
    SettingsPayload,
    SnapshotCreatePayload,
    TestConnectionPayload,
    TestWebhookPayload,
)
from telemetry import collect_telemetry_cycle, execute_registers_batch_write, sse_subscribers

router = APIRouter(prefix="/api", tags=["System & Settings"])

_SENSITIVE_SETTINGS = {
    "comm_mode",
    "agua_email",
    "agua_password",
    "agua_uuid",
    "agua_id_device",
    "agua_id_product",
    "local_gateway_url",
    "webhook_url",
    "matter_enabled",
    "matter_port",
}


def sanitize_csv_cell(val: Any) -> str:
    """Neutralise le risque d'injection de formules CSV (CWE-1236 / OWASP Formula Injection)."""
    s = str(val if val is not None else "")
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s.replace(";", ",").replace("\r", " ").replace("\n", " ")


@router.get("/snapshots")
async def api_list_snapshots():
    """Liste tous les snapshots de registres enregistrés."""
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        rows = conn.execute(
            "SELECT id, timestamp, name, comm_mode, registers_json, raw_buffer_json FROM registers_snapshots ORDER BY id DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        regs_d = json.loads(r["registers_json"] or "{}")
        raw_d = json.loads(r["raw_buffer_json"] or "{}")
        result.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "name": r["name"],
            "comm_mode": r["comm_mode"],
            "registers_count": len(regs_d),
            "raw_offsets_count": len(raw_d),
            "registers": regs_d,
            "raw_buffer": raw_d,
        })
    return result


@router.post("/snapshots")
async def api_create_snapshot(request: Request, payload: SnapshotCreatePayload = Body(default=SnapshotCreatePayload())):
    """Crée un snapshot instantané de tous les registres et du buffer brut."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    if payload.refresh_live:
        await collect_telemetry_cycle(active_mode)
    regs = get_registers_dict(active_mode)
    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT raw_buffer_json FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        raw_json_str = last_row["raw_buffer_json"] if (last_row and last_row["raw_buffer_json"]) else "{}"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snap_name = (payload.name or "").strip() or f"Snapshot {now_str}"
        regs_snapshot = {
            k: {"offset": v["offset"], "label": v["label"], "category": v["category"], "unit": v["unit"], "value": v["current_val"]}
            for k, v in regs.items()
        }
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO registers_snapshots (timestamp, name, comm_mode, registers_json, raw_buffer_json) VALUES (?, ?, ?, ?, ?)",
            (now_str, snap_name, active_mode, json.dumps(regs_snapshot), raw_json_str),
        )
        snap_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    db_log("INFO", "SNAPSHOT", f"Snapshot #{snap_id} créé : '{snap_name}'.", active_mode)
    return {"status": "ok", "id": snap_id, "name": snap_name, "timestamp": now_str}


@router.post("/snapshots/{snap_id}/restore", responses={404: {"description": "Snapshot introuvable"}})
async def api_restore_snapshot(request: Request, snap_id: int = Path(..., ge=1, le=9223372036854775807)):
    """Restaure les valeurs de registres depuis un snapshot sauvegardé."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        row = conn.execute("SELECT * FROM registers_snapshots WHERE id = ?", (snap_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot introuvable")
    snap_regs = json.loads(row["registers_json"] or "{}")
    current_regs = get_registers_dict(active_mode)
    batch_cmds: List[Dict[str, Any]] = []
    for rk, meta in snap_regs.items():
        if rk == "status_code":
            continue
        target_val = float(meta.get("value", 0))
        cur_val = float(current_regs.get(rk, {}).get("current_val", target_val))
        if abs(target_val - cur_val) > 1e-4:
            batch_cmds.append({"reg_key": rk, "value": target_val, "offset": int(meta.get("offset", 0))})
    if batch_cmds:
        await execute_registers_batch_write(batch_cmds, skip_poll=True)
    latest = await collect_telemetry_cycle(active_mode)
    return {"status": "ok", "restored_registers": len(batch_cmds), "latest": latest}


@router.delete("/snapshots/{snap_id}", responses={404: {"description": "Snapshot introuvable"}})
async def api_delete_snapshot(request: Request, snap_id: int = Path(..., ge=1, le=9223372036854775807)):
    """Supprime un snapshot."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        conn.execute("DELETE FROM registers_snapshots WHERE id = ?", (snap_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.get("/snapshots/{snap_id}/export-csv", responses={200: {"content": {"text/csv": {}}}, 404: {"description": "Snapshot introuvable"}})
async def api_export_snapshot_csv(snap_id: int = Path(..., ge=1, le=9223372036854775807)):
    """Exporte un snapshot au format CSV."""
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        row = conn.execute("SELECT * FROM registers_snapshots WHERE id = ?", (snap_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Snapshot introuvable")

    snap_regs = json.loads(row["registers_json"] or "{}")
    raw_buf = json.loads(row["raw_buffer_json"] or "{}")

    def generate():
        yield "\ufefftype_donnee;cle_registre;offset;categorie;description;valeur;unite\r\n"
        for rk, m in snap_regs.items():
            val_str = f"{float(m.get('value', 0)):.2f}".replace(".", ",")
            desc = sanitize_csv_cell(m.get("label", ""))
            yield f"CATALOG_REGISTER;{sanitize_csv_cell(rk)};{m.get('offset')};{sanitize_csv_cell(m.get('category'))};{desc};{val_str};{sanitize_csv_cell(m.get('unit', ''))}\r\n"
        for off_k, off_v in sorted(raw_buf.items(), key=lambda x: int(x[0])):
            val_str = f"{float(off_v):.2f}".replace(".", ",")
            yield f"RAW_BUFFER_OFFSET;OFFSET_{off_k};{off_k};RAW_BUFFER;Offset mémoire brut #{off_k};{val_str};raw\r\n"

    clean_mode_snap = "".join(c for c in str(active_mode).lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789-_")[:32] or "simulation"
    filename = f"snapshot_{snap_id}_{clean_mode_snap}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit")
async def api_get_audit():
    """Retourne l'historique d'audit des commandes exécutées."""
    conn = get_db(get_active_mode())
    try:
        rows = conn.execute("SELECT * FROM command_audit ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/logs")
async def api_get_logs(severity: str = Query("ALL")):
    """Retourne les journaux applicatifs filtrés par sévérité (ou les logs conteneur en mémoire)."""
    sev = (severity or "ALL").strip().upper()
    if sev in ("CONTAINER", "CONTENEUR", "DOCKER"):
        return get_container_logs(limit=250)

    conn = get_db(get_active_mode())
    try:
        if sev == "ALL":
            rows = conn.execute("SELECT * FROM app_logs ORDER BY id DESC LIMIT 250").fetchall()
        elif sev in ("WARNING", "WARN"):
            rows = conn.execute("SELECT * FROM app_logs WHERE severity IN ('WARNING', 'WARN') ORDER BY id DESC LIMIT 250").fetchall()
        else:
            rows = conn.execute("SELECT * FROM app_logs WHERE severity = ? ORDER BY id DESC LIMIT 250", (sev,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/logs/client-error")
async def api_log_client_error(payload: ClientErrorPayload):
    """Enregistre une anomalie JavaScript côté client dans les journaux système."""
    severity = payload.severity.strip().upper()
    source = payload.source.strip()[:25]
    message = payload.message[:500]
    active_mode = get_active_mode()
    db_log(severity, source, message, active_mode)
    return {"status": "ok"}


@router.get("/export-logs-csv", responses={200: {"content": {"text/csv": {}}}})
async def api_export_logs_csv(severity: str = Query("ALL")):
    """Exporte les logs au format CSV."""
    active_mode = get_active_mode()
    sev = (severity or "ALL").strip().upper()
    if sev in ("CONTAINER", "CONTENEUR", "DOCKER"):
        rows = [
            {
                "id": idx + 1,
                "timestamp": r.get("timestamp", ""),
                "severity": r.get("severity", "INFO"),
                "source": r.get("source", "CONTAINER"),
                "message": r.get("message", ""),
            }
            for idx, r in enumerate(get_container_logs(limit=500))
        ]
    else:
        conn = get_db(active_mode)
        try:
            if sev == "ALL":
                rows = conn.execute("SELECT id, timestamp, severity, source, message FROM app_logs ORDER BY id DESC").fetchall()
            elif sev in ("WARNING", "WARN"):
                rows = conn.execute("SELECT id, timestamp, severity, source, message FROM app_logs WHERE severity IN ('WARNING', 'WARN') ORDER BY id DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, timestamp, severity, source, message FROM app_logs WHERE severity = ? ORDER BY id DESC",
                    (sev,),
                ).fetchall()
        finally:
            conn.close()

    def generate():
        yield "\ufeffid;timestamp;severity;source;message\r\n"
        for r in rows:
            msg = sanitize_csv_cell(r["message"])
            src = sanitize_csv_cell(r["source"])
            sev_clean = sanitize_csv_cell(r["severity"])
            yield f"{r['id']};{r['timestamp']};{sev_clean};{src};{msg}\r\n"

    clean_sev = "".join(c for c in str(severity).lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789-_")[:32] or "all"
    clean_mode = "".join(c for c in str(active_mode).lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789-_")[:32] or "simulation"
    filename = f"nobis_a10_logs_{clean_mode}_{clean_sev}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/settings")
async def api_save_settings(payload: SettingsPayload, request: Request):
    """Sauvegarde les paramètres de configuration dans la base système SQLite."""
    raw_dict = payload.model_dump(exclude_none=True)
    current_settings = get_settings_dict()
    sensitive_changed = any(
        k in _SENSITIVE_SETTINGS
        and raw_dict[k] is not None
        and str(raw_dict[k]) != "********"
        and str(raw_dict[k]) != str(current_settings.get(k, ""))
        for k in raw_dict
    )
    if sensitive_changed:
        require_installer_auth_if_configured(request)

    if "webhook_url" in raw_dict:
        wh_url = str(raw_dict["webhook_url"] or "").strip()
        if wh_url and not is_safe_webhook_url(wh_url):
            raise HTTPException(status_code=400, detail="URL de webhook invalide ou rejetée par le filtre de sécurité anti-SSRF.")
        raw_dict["webhook_url"] = wh_url

    if "local_gateway_url" in raw_dict:
        gw_url_in = str(raw_dict["local_gateway_url"] or "").strip()
        if gw_url_in and not is_safe_gateway_url(gw_url_in):
            raise HTTPException(status_code=400, detail="URL de passerelle locale invalide ou rejetée par le filtre de sécurité anti-SSRF.")
        raw_dict["local_gateway_url"] = gw_url_in

    if "duct_left_name" in raw_dict:
        raw_dict["duct_left_name"] = (str(raw_dict["duct_left_name"] or "").strip()[:20]) or "SX"
    if "duct_right_name" in raw_dict:
        raw_dict["duct_right_name"] = (str(raw_dict["duct_right_name"] or "").strip()[:20]) or "DX"

    conn = get_sys_db()
    try:
        for k, v in raw_dict.items():
            if v is not None and str(v) != "********":
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(k), str(v)))
        conn.commit()
    finally:
        conn.close()

    if "agua_password" in raw_dict and raw_dict["agua_password"] != "********":
        clear_installer_sessions()

    if "poll_interval_s" in raw_dict and str(raw_dict["poll_interval_s"]).strip():
        try:
            interval_s = max(15, min(3600, int(float(str(raw_dict["poll_interval_s"]).strip()))))
        except (ValueError, TypeError):
            interval_s = 120
        if scheduler.get_job("telemetry_job"):
            scheduler.reschedule_job("telemetry_job", trigger="interval", seconds=interval_s)
        else:
            scheduler.add_job(collect_telemetry_cycle, "interval", seconds=interval_s, id="telemetry_job", replace_existing=True)

    new_mode = get_active_mode()
    db_file_name = os.path.basename(MODE_DB_PATHS.get(new_mode, MODE_DB_PATHS["simulation"]))
    db_log("INFO", "SETTINGS", f"Configuration enregistrée. Base active : {db_file_name}", new_mode)

    matter_keys = {"matter_enabled", "matter_device_name", "matter_port"}
    if any(k in raw_dict for k in matter_keys):
        try:
            if matter_manager.is_enabled():
                await matter_manager.stop()
                await matter_manager.ensure_started()
            else:
                await matter_manager.stop()
        except Exception as e:
            db_log("WARNING", "MATTER", f"Erreur ajustement pont Matter post-settings: {e}", new_mode)

    await collect_telemetry_cycle(new_mode)
    return {"status": "ok", "settings": get_public_settings_dict(), "active_db": db_file_name}


@router.post("/test-webhook", responses={200: {"description": "Notification de test envoyée avec succès"}, 400: {"description": "URL de webhook invalide ou rejetée"}})
async def api_test_webhook(payload: TestWebhookPayload):
    """Envoie une notification push de test avec vérification stricte anti-SSRF."""
    url = (payload.webhook_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Aucune URL de Webhook / ntfy.sh renseignée.")
    if not is_safe_webhook_url(url):
        raise HTTPException(status_code=400, detail="URL de webhook invalide ou rejetée par le filtre de sécurité anti-SSRF.")
    active_mode = get_active_mode()
    notification_service._last_sent_by_key.pop("test_alert", None)
    ok = await notification_service.send_webhook(
        url,
        "🔥 Test Superviseur Nobis A10",
        "Connexion notifications Push/Webhook opérationnelle (Trémie 35 kg).",
        "test_alert",
        active_mode,
    )
    if not ok:
        return {"status": "error", "message": "Échec de l'envoi vers l'URL Webhook."}
    return {"status": "ok", "message": "Notification de test envoyée avec succès !"}


@router.post("/test-connection", responses={200: {"description": "Résultat du test de communication"}})
async def api_test_connection(payload: TestConnectionPayload):
    """Teste la communication avec le protocole sélectionné (Agua Cloud, Passerelle UART ou Simulation)."""
    raw_dict = payload.model_dump(exclude_none=True)
    mode = raw_dict.get("comm_mode", "agua_cloud")
    settings = get_settings_dict()
    settings.update({k: str(v) for k, v in raw_dict.items() if str(v) != "********"})
    weather = await weather_service.fetch_current(settings.get("weather_lat", "48.1636"), settings.get("weather_lon", "2.0436"))
    if mode == "simulation":
        return {"status": "ok", "mode": "simulation", "active_db": os.path.basename(MODE_DB_PATHS["simulation"]), "weather": weather}
    elif mode == "agua_cloud":
        prev_state = (
            agua_client.token_jwt,
            agua_client.refresh_token,
            agua_client.id_device,
            agua_client.id_product,
            agua_client.working_write_endpoint,
        )
        try:
            res = await agua_client.login_and_discover(
                settings.get("agua_email", ""),
                settings.get("agua_password", ""),
                settings.get("agua_uuid", ""),
                persist_discovery=False,
            )
            return {"status": "ok", "mode": "agua_cloud", "active_db": os.path.basename(MODE_DB_PATHS["agua_cloud"]), "details": res, "weather": weather}
        except Exception as e:
            db_log("ERROR", "AGUA_CLOUD", f"Échec test connexion Agua IOT : {str(e)}", "agua_cloud")
            return JSONResponse(status_code=200, content={"status": "error", "details": f"Erreur Cloud Agua IOT : {str(e)}"})
        finally:
            (
                agua_client.token_jwt,
                agua_client.refresh_token,
                agua_client.id_device,
                agua_client.id_product,
                agua_client.working_write_endpoint,
            ) = prev_state
    elif mode == "local_gateway":
        gw_url = settings.get("local_gateway_url", "http://192.168.1.150")
        if not is_safe_gateway_url(gw_url):
            raise HTTPException(status_code=400, detail="URL de passerelle locale invalide ou rejetée par le filtre de sécurité anti-SSRF.")
        try:
            res = await local_gateway_client.test_connection(gw_url)
            return {"status": "ok", "mode": "local_gateway", "details": res, "weather": weather}
        except Exception as e:
            db_log("ERROR", "LOCAL_GATEWAY", f"Échec test connexion Passerelle UART : {str(e)}", "local_gateway")
            return JSONResponse(status_code=200, content={"status": "error", "details": f"Erreur Passerelle UART : {str(e)}"})


@router.get("/agua-registers-map")
async def api_agua_registers_map():
    """Scanne et retourne la cartographie exhaustive des registres Agua IOT."""
    settings = get_settings_dict()
    try:
        reg_map = await agua_client.scan_registers_map(settings)
        raw_buffer = await agua_client.read_buffer(settings)
    except Exception as e:
        return {
            "id_device": agua_client.id_device,
            "id_product": agua_client.id_product,
            "working_write_endpoint": agua_client.working_write_endpoint,
            "cached_offsets_count": len(agua_client.registers_map_by_offset),
            "raw_buffer_count": 0,
            "raw_buffer": {},
            "registers_map": [],
            "status": "offline",
            "details": f"Agua IOT non accessible : {str(e)}",
        }
    return {
        "id_device": agua_client.id_device,
        "id_product": agua_client.id_product,
        "working_write_endpoint": agua_client.working_write_endpoint,
        "cached_offsets_count": len(agua_client.registers_map_by_offset),
        "raw_buffer_count": len(raw_buffer),
        "raw_buffer": raw_buffer,
        "registers_map": reg_map,
    }


@router.get("/export-csv", responses={200: {"content": {"text/csv": {}}}})
async def api_export_csv(range_key: str = Query("24h")):
    """Exporte les enregistrements de télémétrie et registres décodés au format CSV."""
    active_mode = get_active_mode()
    now_epoch = int(time.time())
    durations = {
        "3h": 3 * 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7j": 7 * 86400,
        "30j": 30 * 86400,
        "3M": 90 * 86400,
    }
    min_epoch = now_epoch - durations.get(range_key, 86400)

    conn = get_db(active_mode)
    try:
        if active_mode == "agua_cloud":
            rows = conn.execute("""
                SELECT * FROM telemetry
                WHERE epoch >= ?
                  AND id NOT BETWEEN 1 AND 27
                  AND NOT (status_code != -1 AND (raw_buffer_json = '{}' OR raw_buffer_json IS NULL))
                ORDER BY epoch DESC
            """, (min_epoch,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM telemetry WHERE epoch >= ? ORDER BY epoch DESC", (min_epoch,)).fetchall()
    finally:
        conn.close()

    regs_catalog = get_registers_dict(active_mode)
    reg_keys = list(regs_catalog.keys())

    all_raw_offsets_set = set()
    parsed_buffers: List[Dict[str, Any]] = []
    for r in rows:
        try:
            b = json.loads(r["raw_buffer_json"] or "{}")
        except Exception:
            b = {}
        parsed_buffers.append(b)
        for k in b.keys():
            if str(k).isdigit():
                all_raw_offsets_set.add(int(k))
        sorted_offsets = sorted(all_raw_offsets_set)

    base_cols = [c for c in TELEMETRY_COLUMNS.keys() if c != "raw_buffer_json"]
    reg_cols = [f"REG_{rk}" for rk in reg_keys]
    off_cols = [f"OFFSET_{off}" for off in sorted_offsets]
    header_cols = base_cols + reg_cols + off_cols

    def generate():
        yield "\ufeff" + ";".join(header_cols) + "\r\n"
        for idx, r in enumerate(rows):
            row_dict = dict(r)
            buf = parsed_buffers[idx]
            vals = []
            for c in base_cols:
                v = row_dict.get(c)
                if isinstance(v, float):
                    vals.append(f"{v:.2f}".replace(".", ","))
                elif v is None:
                    vals.append("")
                else:
                    vals.append(sanitize_csv_cell(v))
            for rk in reg_keys:
                meta = regs_catalog[rk]
                off_str = str(meta["offset"])
                if off_str in buf:
                    rv = buf[off_str]
                elif rk in row_dict and row_dict[rk] is not None and rk != "status_code":
                    rv = row_dict[rk]
                else:
                    rv = meta["current_val"]
                if isinstance(rv, float):
                    vals.append(f"{rv:.2f}".replace(".", ","))
                else:
                    vals.append(sanitize_csv_cell(rv))
            for off in sorted_offsets:
                ov = buf.get(str(off), "")
                if isinstance(ov, float):
                    vals.append(f"{ov:.2f}".replace(".", ","))
                else:
                    vals.append(sanitize_csv_cell(ov))
            yield ";".join(vals) + "\r\n"

    clean_range = "".join(c for c in str(range_key).lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789-_")[:32] or "24h"
    clean_mode = "".join(c for c in str(active_mode).lower() if c in "abcdefghijklmnopqrstuvwxyz0123456789-_")[:32] or "simulation"
    filename = f"nobis_a10_supervision_et_registres_{clean_mode}_{clean_range}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _delayed_process_restart(delay_s: float = 0.5) -> None:
    """Ferme les flux SSE et redémarre le processus (SIGTERM en Docker, respawning en local)."""
    await asyncio.sleep(delay_s)
    for q in list(sse_subscribers):
        try:
            q.put_nowait(None)
        except Exception:
            pass
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        await matter_manager.stop()
    except Exception:
        pass

    if os.path.exists("/.dockerenv"):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(3.0)
        os._exit(0)
    elif "--reload" in sys.argv:
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        if os.path.isfile(main_py):
            os.utime(main_py, None)
            return
    else:
        argv0 = sys.argv[0] if sys.argv else ""
        if sys.platform == "win32" and argv0 and not argv0.lower().endswith(".py"):
            exe_candidate = argv0 if argv0.lower().endswith(".exe") else f"{argv0}.exe"
            if os.path.isfile(exe_candidate):
                cmd = [exe_candidate] + sys.argv[1:]
            elif os.path.basename(argv0).lower().startswith("uvicorn"):
                cmd = [sys.executable, "-m", "uvicorn"] + sys.argv[1:]
            else:
                cmd = [sys.executable] + sys.argv
        else:
            cmd = [sys.executable] + sys.argv
        subprocess.Popen(cmd, close_fds=(sys.platform != "win32"))
        os._exit(0)


@router.post("/system/restart", responses={403: {"description": "Authentification installateur requise"}})
async def api_system_restart(request: Request):
    """Déclenche un redémarrage propre du backend (relancé automatiquement par Docker)."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    db_log("COMMAND", "SYSTEM", "Redémarrage du backend demandé depuis l'interface.", active_mode)
    add_container_log("WARNING", "SYSTEM", "Redémarrage du backend demandé depuis l'interface.")
    asyncio.create_task(_delayed_process_restart(0.5))
    return {"status": "ok", "message": "Redémarrage du backend déclenché. Reconnexion en cours..."}


@router.post(
    "/system/check-update",
    responses={
        403: {"description": "Authentification installateur requise"},
        502: {"description": "Watchtower injoignable ou en erreur"},
    },
)
async def api_system_check_update(request: Request):
    """Déclenche une vérification/mise à jour via l'API HTTP de Watchtower."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    env_watchtower_url = os.environ.get("WATCHTOWER_URL", "").strip()
    if not env_watchtower_url and not os.path.exists("/.dockerenv"):
        # Hors Docker : vérification directe de la version Git locale vs dépôt distant
        try:
            repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            res_local = await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            local_sha = (res_local.stdout or "").strip()

            res_remote = await asyncio.to_thread(
                subprocess.run,
                ["git", "ls-remote", "origin", "HEAD"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            remote_line = (res_remote.stdout or "").strip()
            remote_sha = remote_line.split()[0] if remote_line else ""

            if res_local.returncode == 0 and res_remote.returncode == 0 and local_sha and remote_sha:
                if local_sha == remote_sha:
                    msg = f"Application à jour ({local_sha[:7]})."
                    up_to_date = True
                else:
                    msg = f"Mise à jour disponible sur GitHub ({local_sha[:7]} → {remote_sha[:7]}). Lancez 'git pull'."
                    up_to_date = False
                db_log("INFO", "UPDATE", msg, active_mode)
                add_container_log("INFO", "UPDATE", msg)
                return {"status": "ok", "update_available": not up_to_date, "message": msg}
            err_txt = (res_remote.stderr or res_local.stderr or "").strip() or "Dépôt distant inaccessible"
            msg = f"Vérification Git impossible : {err_txt[:150]}"
        except Exception as exc:
            msg = f"Vérification Git impossible : {exc or type(exc).__name__}"
        db_log("WARNING", "UPDATE", msg, active_mode)
        add_container_log("WARNING", "UPDATE", msg)
        return {"status": "unavailable", "message": msg}

    watchtower_url = env_watchtower_url or "http://watchtower:8080/v1/update"
    watchtower_token = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "nobis-watchtower").strip()
    headers = {"Authorization": f"Bearer {watchtower_token}"} if watchtower_token else {}

    add_container_log("INFO", "WATCHTOWER", f"Vérification de mise à jour demandée ({watchtower_url}).")
    try:
        async with borrow_http_client(timeout=15.0) as client:
            resp = await client.post(watchtower_url, headers=headers)
        if 200 <= resp.status_code < 300:
            db_log("INFO", "WATCHTOWER", "Vérification de mise à jour Watchtower déclenchée avec succès.", active_mode)
            add_container_log("INFO", "WATCHTOWER", "Vérification de mise à jour Watchtower déclenchée avec succès.")
            return {
                "status": "ok",
                "message": "Vérification de mise à jour Watchtower déclenchée avec succès.",
            }
        err_detail = f"Watchtower a répondu HTTP {resp.status_code} : {resp.text[:200]}"
        db_log("WARNING", "WATCHTOWER", err_detail, active_mode)
        add_container_log("WARNING", "WATCHTOWER", err_detail)
        raise HTTPException(status_code=502, detail=err_detail)
    except httpx.RequestError as exc:
        err_detail = f"Impossible de joindre Watchtower ({watchtower_url}) : {exc}"
        db_log("WARNING", "WATCHTOWER", err_detail, active_mode)
        add_container_log("WARNING", "WATCHTOWER", err_detail)
        raise HTTPException(status_code=502, detail=err_detail)


@router.get("/reverse-geocode")
async def api_reverse_geocode(lat: float, lon: float):
    """Résout le nom de la commune à partir de coordonnées GPS (BAN France puis Nominatim)."""
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise HTTPException(status_code=400, detail="Coordonnées GPS invalides.")

    city_label = ""
    try:
        async with borrow_http_client(timeout=6.0) as client:
            # 1. API Adresse officielle France (BAN - rapide et précise)
            try:
                r_ban = await client.get(
                    "https://api-adresse.data.gouv.fr/reverse/",
                    params={"lon": f"{lon:.5f}", "lat": f"{lat:.5f}", "limit": 1},
                )
                if r_ban.status_code == 200:
                    feats = (r_ban.json() or {}).get("features") or []
                    if feats:
                        props = feats[0].get("properties") or {}
                        city = (props.get("city") or props.get("municipality") or "").strip()
                        postcode = (props.get("postcode") or "").strip()
                        if city:
                            city_label = f"{city} ({postcode})" if postcode else city
            except Exception:
                pass

            # 2. Repli mondial Nominatim OpenStreetMap si hors France ou échec BAN
            if not city_label:
                try:
                    r_nom = await client.get(
                        "https://nominatim.openstreetmap.org/reverse",
                        params={"format": "json", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "zoom": 10, "addressdetails": 1},
                        headers={"User-Agent": "Nobis.Supervisor/2.1"},
                    )
                    if r_nom.status_code == 200:
                        addr = (r_nom.json() or {}).get("address") or {}
                        city = (
                            addr.get("city")
                            or addr.get("town")
                            or addr.get("village")
                            or addr.get("municipality")
                            or ""
                        ).strip()
                        postcode = (addr.get("postcode") or "").strip()
                        if city:
                            city_label = f"{city} ({postcode})" if postcode else city
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "status": "ok",
        "city": city_label,
        "lat": f"{lat:.4f}",
        "lon": f"{lon:.4f}",
    }
