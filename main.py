import os
import sys
import time
import json
import hmac
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import (
    STATIC_DIR,
    MODE_DB_PATHS,
    HOPPER_CAPACITY_KG,
    PELLET_BAG_WEIGHT_KG,
    TELEMETRY_COLUMNS,
)
from schemas import (
    CommandPayload,
    BatchCommandPayload,
    RefillHopperPayload,
    SnapshotCreatePayload,
    SettingsPayload,
    TestConnectionPayload,
    SilenceActivatePayload,
    SilenceDeactivatePayload,
    MatterWebhookPayload,
    TestWebhookPayload,
    ClientErrorPayload,
    InstallerVerifyPayload,
)
from database import (
    get_sys_db,
    get_db,
    init_all_databases,
    get_settings_dict,
    get_public_settings_dict,
    get_active_mode,
    get_registers_dict,
    get_rolling_consumption_stats,
    db_log,
    seed_simulation_db_if_empty,
)
from clients import (
    init_http_client,
    close_http_client,
    borrow_http_client,
    weather_service,
    agua_client,
    local_gateway_client,
    notification_service,
    is_safe_webhook_url,
)
from telemetry import (
    sse_subscribers,
    collect_telemetry_cycle,
    execute_registers_batch_write,
    execute_register_write,
    evaluate_pellet_quality,
    evaluate_exhaust_quality,
    check_power_stability,
    enrich_telemetry_record,
    prune_database_retention,
    reset_pellet_batch_calibration,
    get_pellet_batch_state,
)
from matter_service import matter_manager

# ==============================================================================
# INSTANCE FASTAPI & ROUTES REST (COMPATIBLE UVICORN app.main:app & main:app)
# ==============================================================================
scheduler = AsyncIOScheduler()
_is_shutting_down = False


def _notify_sse_shutdown() -> None:
    """Réveille immédiatement tous les flux SSE actifs pour libérer Uvicorn à l'arrêt."""
    global _is_shutting_down
    _is_shutting_down = True
    for q in list(sse_subscribers):
        try:
            while not q.empty():
                q.get_nowait()
            q.put_nowait(None)
        except Exception:
            pass


try:
    import uvicorn.server
    _orig_uvicorn_handle_exit = uvicorn.server.Server.handle_exit
    _orig_uvicorn_shutdown = uvicorn.server.Server.shutdown

    def _patched_uvicorn_handle_exit(self, sig, frame):
        _notify_sse_shutdown()
        return _orig_uvicorn_handle_exit(self, sig, frame)

    async def _patched_uvicorn_shutdown(self, sockets=None):
        _notify_sse_shutdown()
        return await _orig_uvicorn_shutdown(self, sockets=sockets)

    uvicorn.server.Server.handle_exit = _patched_uvicorn_handle_exit
    uvicorn.server.Server.shutdown = _patched_uvicorn_shutdown
except Exception:
    pass


# ==============================================================================
# GESTIONNAIRE DU MODE SILENCE ! (CONVECTION NATURELLE, P1, TIMER & RESTAURATION)
# ==============================================================================
SILENCE_KEYS_TO_SAVE = [
    "power_level",
    "fan_front_speed",
    "duct_left_speed",
    "duct_right_speed",
    "fun_auto_set",
    "standby_set",
    "fun_pwf_set",
]


def get_silence_state() -> Dict[str, Any]:
    conn = get_sys_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'silence_timer_state'").fetchone()
        if row and row["value"]:
            try:
                return json.loads(row["value"])
            except Exception:
                pass
    finally:
        conn.close()
    return {
        "active": False,
        "duration_hours": 2.0,
        "start_epoch": 0.0,
        "end_epoch": 0.0,
        "initial_state": {}
    }


def save_silence_state(state: Dict[str, Any]) -> None:
    conn = get_sys_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('silence_timer_state', ?)",
            (json.dumps(state),)
        )
        conn.commit()
    finally:
        conn.close()


def get_silence_status() -> Dict[str, Any]:
    state = get_silence_state()
    is_active = bool(state.get("active", False))
    end_epoch = float(state.get("end_epoch", 0.0))
    now_ts = time.time()
    remaining = max(0, int(end_epoch - now_ts)) if is_active else 0
    if is_active and remaining <= 0:
        is_active = False
    return {
        "active": is_active,
        "duration_hours": float(state.get("duration_hours", 2.0)),
        "start_epoch": float(state.get("start_epoch", 0.0)),
        "end_epoch": end_epoch,
        "remaining_seconds": remaining,
        "initial_state": state.get("initial_state", {})
    }


async def activate_silence_mode(duration_hours: float = 2.0) -> Dict[str, Any]:
    active_mode = get_active_mode()
    regs = get_registers_dict(active_mode)

    # 1. Sauvegarde des valeurs initiales
    initial_state = {}
    for k in SILENCE_KEYS_TO_SAVE:
        if k in regs:
            initial_state[k] = float(regs[k].get("current_val", 0.0))
        else:
            initial_state[k] = 0.0

    # 2. Ordres de bascule en mode Silence ! (P1, ventilation à 0, désactivation auto/eco/boost)
    activation_cmds = [
        {"reg_key": "power_level", "value": 1.0, "offset": 33120},
        {"reg_key": "fan_front_speed", "value": 0.0, "offset": 33132},
        {"reg_key": "duct_left_speed", "value": 0.0, "offset": 33136},
        {"reg_key": "duct_right_speed", "value": 0.0, "offset": 33134},
        {"reg_key": "fun_auto_set", "value": 0.0, "offset": 33128},
        {"reg_key": "standby_set", "value": 0.0, "offset": 33130},
        {"reg_key": "fun_pwf_set", "value": 0.0, "offset": 33154},
        {"reg_key": "set_silece_set", "value": 1.0, "offset": 33250},
    ]

    write_res = await execute_registers_batch_write(activation_cmds)

    now_ts = time.time()
    dur_s = max(60, int(duration_hours * 3600))
    end_ts = now_ts + dur_s
    end_dt = datetime.fromtimestamp(end_ts)

    state = {
        "active": True,
        "duration_hours": float(duration_hours),
        "start_epoch": now_ts,
        "end_epoch": end_ts,
        "initial_state": initial_state
    }
    save_silence_state(state)

    scheduler.add_job(
        _auto_expire_silence_job,
        "date",
        run_date=end_dt,
        id="silence_timer_job",
        replace_existing=True
    )

    db_log(
        "COMMAND",
        "SILENCE",
        f"Mode Silence ! activé pour {duration_hours:g}H (P1, ventilation coupée, auto/eco/boost désactivés). Restauration programmée à {end_dt.strftime('%H:%M:%S')}.",
        active_mode
    )

    status = get_silence_status()
    return {
        "status": "ok",
        "silence_mode": status,
        "write_result": write_res,
        "latest": write_res.get("latest", {})
    }


async def deactivate_silence_mode(is_auto_expired: bool = False, override_commands: Optional[List[Any]] = None) -> Dict[str, Any]:
    active_mode = get_active_mode()
    state = get_silence_state()
    initial = state.get("initial_state", {})
    regs = get_registers_dict(active_mode)

    if scheduler.get_job("silence_timer_job"):
        try:
            scheduler.remove_job("silence_timer_job")
        except Exception:
            pass

    overrides_dict: Dict[str, float] = {}
    if override_commands:
        for c in override_commands:
            rk = getattr(c, "reg_key", None) or (c.get("reg_key") if isinstance(c, dict) else None)
            val = getattr(c, "value", None) if hasattr(c, "value") else (c.get("value") if isinstance(c, dict) else None)
            if rk is not None and val is not None:
                try:
                    overrides_dict[str(rk)] = float(val)
                except (ValueError, TypeError):
                    pass

    keys_config = [
        ("power_level", 4.0, 33120),
        ("fan_front_speed", 3.0, 33132),
        ("duct_left_speed", 0.0, 33136),
        ("duct_right_speed", 0.0, 33134),
        ("fun_auto_set", 1.0, 33128),
        ("standby_set", 0.0, 33130),
        ("fun_pwf_set", 0.0, 33154),
    ]

    restore_cmds = []
    for k, def_val, def_off in keys_config:
        off = int(regs.get(k, {}).get("offset", def_off))
        if k in overrides_dict:
            target_val = overrides_dict[k]
        else:
            target_val = float(initial.get(k, def_val))
        restore_cmds.append({"reg_key": k, "value": target_val, "offset": off})

    restore_cmds.append({"reg_key": "set_silece_set", "value": 0.0, "offset": 33250})

    write_res = await execute_registers_batch_write(restore_cmds)

    state["active"] = False
    state["end_epoch"] = 0.0
    save_silence_state(state)

    if overrides_dict:
        over_summary = ", ".join(f"{k}={v:g}" for k, v in overrides_dict.items())
        reason = f"Désactivation manuelle avec consigne ({over_summary})"
    elif is_auto_expired:
        reason = "Fin du compte à rebours"
    else:
        reason = "Désactivation manuelle"

    db_log(
        "COMMAND",
        "SILENCE",
        f"Mode Silence ! désactivé ({reason}) : paramètres restaurés/mis à jour.",
        active_mode
    )

    status = get_silence_status()
    return {
        "status": "ok",
        "silence_mode": status,
        "write_result": write_res,
        "latest": write_res.get("latest", {})
    }


async def _auto_expire_silence_job():
    try:
        await deactivate_silence_mode(is_auto_expired=True)
        active_mode = get_active_mode()
        await collect_telemetry_cycle(active_mode)
    except Exception as e:
        db_log("ERROR", "SILENCE", f"Erreur lors de la fin automatique du mode Silence ! : {str(e)}", get_active_mode())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_shutting_down
    _is_shutting_down = False
    await init_http_client()
    init_all_databases()
    seed_simulation_db_if_empty()
    settings = get_settings_dict()
    interval_s = max(15, int(settings.get("poll_interval_s", "120")))
    scheduler.add_job(collect_telemetry_cycle, "interval", seconds=interval_s, id="telemetry_job", replace_existing=True)
    scheduler.add_job(prune_database_retention, "interval", hours=12, id="retention_prune_job", replace_existing=True)
    scheduler.start()
    active_mode = get_active_mode()
    db_log("INFO", "SYSTEM", f"Superviseur Nobis A10 démarré sur [{active_mode}] (cycle={interval_s}s).", active_mode)

    # Démarrage automatique du pont Thermostat Matter (Google Home / Nest Hub Max)
    try:
        await matter_manager.ensure_started()
    except Exception as e:
        db_log("ERROR", "MATTER", f"Erreur démarrage pont Matter: {e}", active_mode)

    # Vérification et reprise d'un compte à rebours Silence ! actif
    st = get_silence_state()
    if st.get("active"):
        end_ep = float(st.get("end_epoch", 0.0))
        now_ep = time.time()
        if now_ep >= end_ep:
            db_log("INFO", "SILENCE", "Mode Silence ! expiré pendant l'arrêt du superviseur : restauration immédiate.", active_mode)
            try:
                await deactivate_silence_mode(is_auto_expired=True)
            except Exception as e:
                db_log("ERROR", "SILENCE", f"Échec restauration Silence au démarrage: {e}", active_mode)
        else:
            rem_s = int(end_ep - now_ep)
            end_dt = datetime.fromtimestamp(end_ep)
            scheduler.add_job(
                _auto_expire_silence_job,
                "date",
                run_date=end_dt,
                id="silence_timer_job",
                replace_existing=True
            )
            db_log("INFO", "SILENCE", f"Reprise du compte à rebours Silence ! ({rem_s}s restantes jusqu'à {end_dt.strftime('%H:%M:%S')}).", active_mode)

    try:
        yield
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _notify_sse_shutdown()
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await matter_manager.stop()
        await close_http_client()


app = FastAPI(title="Nobis A10 C Light Style Coax - Supervision Micronova", lifespan=lifespan)


class DbLogHandler(logging.Handler):
    """Redirige les logs ERROR et CRITICAL de Python directement dans app_logs."""
    def emit(self, record: logging.LogRecord):
        if record.levelno >= logging.ERROR:
            try:
                active_mode = get_active_mode()
                msg = self.format(record)
                if "app_logs" not in msg:
                    db_log("ERROR", record.name[:25], msg[:500], active_mode)
            except Exception:
                pass


_db_log_handler = DbLogHandler()
_db_log_handler.setLevel(logging.ERROR)
logging.getLogger().addHandler(_db_log_handler)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    active_mode = get_active_mode()
    err_msg = f"{request.method} {request.url.path} -> {type(exc).__name__}: {str(exc)}"
    db_log("ERROR", "API", err_msg, active_mode)
    return JSONResponse(status_code=500, content={"detail": f"Erreur interne : {str(exc)}"})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    active_mode = get_active_mode()
    severity = "ERROR" if exc.status_code >= 400 else "WARNING"
    db_log(severity, "API", f"{request.method} {request.url.path} [{exc.status_code}] : {exc.detail}", active_mode)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_spa():
    index_file = os.path.join(STATIC_DIR, "index.html")
    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=no_cache)
    return HTMLResponse(content="<h1>Nobis A10 Supervisor API Active</h1>", headers=no_cache)


@app.api_route("/manifest.json", methods=["GET", "HEAD"])
async def serve_manifest():
    manifest_file = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(manifest_file):
        return FileResponse(
            manifest_file,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    raise HTTPException(status_code=404, detail="manifest.json introuvable")


@app.api_route("/sw.js", methods=["GET", "HEAD"])
async def serve_service_worker():
    sw_file = os.path.join(STATIC_DIR, "sw.js")
    if os.path.exists(sw_file):
        return FileResponse(
            sw_file,
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache, no-store, must-revalidate"
            }
        )
    raise HTTPException(status_code=404, detail="sw.js introuvable")


@app.get("/health")
async def healthcheck():
    mode = get_active_mode()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_mode": mode,
        "active_db": os.path.basename(MODE_DB_PATHS[mode])
    }


@app.get("/api/events")
async def api_sse_events(request: Request):
    """Flux temps réel Server-Sent Events (SSE) poussant chaque mise à jour de télémétrie."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    sse_subscribers.add(queue)

    async def event_generator():
        idle_ticks = 0
        try:
            yield f"data: {json.dumps({'event': 'connected', 'active_mode': get_active_mode()})}\n\n"
            while not _is_shutting_down:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    if msg is None or _is_shutting_down:
                        break
                    idle_ticks = 0
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    idle_ticks += 1
                    if idle_ticks >= 12:
                        idle_ticks = 0
                        yield ": keepalive\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/latest")
async def api_latest():
    """Endpoint ultra-léger retournant uniquement le dernier point, les registres et les paramètres."""
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        latest = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest_dict = dict(latest) if latest else {}
        if latest_dict:
            enrich_telemetry_record(conn, latest_dict)
    finally:
        conn.close()

    stats = {
        "conso_week_kg": latest_dict.get("conso_week_kg", 0.0),
        "conso_month_kg": latest_dict.get("conso_month_kg", 0.0)
    }
    return {
        "active_mode": active_mode,
        "active_db_file": os.path.basename(MODE_DB_PATHS[active_mode]),
        "latest": latest_dict,
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(active_mode),
        "consumption_stats": stats,
        "pellet_quality": latest_dict.get("pellet_quality"),
        "exhaust_quality": latest_dict.get("exhaust_quality"),
        "silence_mode": get_silence_status()
    }


@app.get("/api/dashboard")
async def api_dashboard(range_key: str = Query("24h")):
    active_mode = get_active_mode()
    now_epoch = int(time.time())
    durations = {
        "3h": 3 * 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7j": 7 * 86400,
        "30j": 30 * 86400,
        "3M": 90 * 86400
    }
    duration_s = durations.get(range_key, 86400)
    min_epoch = now_epoch - duration_s

    conn = get_db(active_mode)
    try:
        latest = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
    finally:
        conn.close()

    if not latest:
        await collect_telemetry_cycle(active_mode)

    # Agrégation par tranche SQL directe pour les longues périodes (jusqu'à ~360 points max)
    bucket_s = max(1, duration_s // 360)
    conn = get_db(active_mode)
    try:
        latest = conn.execute("SELECT * FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        latest_dict = dict(latest) if latest else {}
        if latest_dict:
            enrich_telemetry_record(conn, latest_dict)

        # Filtre d'exclusion des données reconstituées ou orphelines (ex: id 1 à 27)
        where_clause = "epoch >= ?"
        params: List[Any] = [min_epoch]
        if active_mode == "agua_cloud":
            where_clause += " AND id NOT BETWEEN 1 AND 27 AND NOT (status_code != -1 AND (raw_buffer_json = '{}' OR raw_buffer_json IS NULL))"

        if bucket_s <= 120:
            rows = conn.execute(
                f"SELECT * FROM telemetry WHERE {where_clause} ORDER BY epoch ASC",
                tuple(params)
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT * FROM telemetry
                WHERE id IN (
                    SELECT MAX(id) FROM telemetry
                    WHERE {where_clause}
                    GROUP BY CAST(epoch / ? AS INTEGER)
                )
                ORDER BY epoch ASC
                """,
                tuple(params + [bucket_s])
            ).fetchall()
    finally:
        conn.close()

    sampled = [dict(r) for r in rows] if rows else []
    if latest_dict and sampled and sampled[-1]["epoch"] != latest_dict["epoch"]:
        sampled.append(latest_dict)

    if len(sampled) == 1:
        dup = dict(sampled[0])
        base_epoch = int(sampled[0].get("epoch") or int(time.time()))
        dup_epoch = base_epoch - 300
        dup["epoch"] = dup_epoch
        dup["timestamp"] = datetime.fromtimestamp(dup_epoch).strftime("%Y-%m-%d %H:%M:%S")
        sampled = [dup, sampled[0]]

    stats = {
        "conso_week_kg": latest_dict.get("conso_week_kg", 0.0),
        "conso_month_kg": latest_dict.get("conso_month_kg", 0.0)
    }

    return {
        "active_mode": active_mode,
        "active_db_file": os.path.basename(MODE_DB_PATHS[active_mode]),
        "latest": latest_dict,
        "series": sampled,
        "settings": get_public_settings_dict(),
        "registers": get_registers_dict(active_mode),
        "consumption_stats": stats,
        "pellet_quality": latest_dict.get("pellet_quality"),
        "exhaust_quality": latest_dict.get("exhaust_quality"),
        "silence_mode": get_silence_status()
    }


@app.post("/api/poll-now")
async def api_poll_now():
    record = await collect_telemetry_cycle()
    return {"status": "ok", "latest": record}


@app.post("/api/command")
async def api_command(payload: CommandPayload):
    return await execute_register_write(payload.reg_key, payload.value, payload.offset)


@app.post("/api/command-batch")
async def api_command_batch(payload: BatchCommandPayload):
    cmds = [{"reg_key": c.reg_key, "value": c.value, "offset": c.offset} for c in payload.commands]
    return await execute_registers_batch_write(cmds)


@app.post("/api/silence/activate")
async def api_silence_activate(payload: SilenceActivatePayload = Body(default=SilenceActivatePayload())):
    """Active le mode Silence ! : P1, ventilation=0, arrêt auto/eco/boost, compte à rebours temporisé."""
    return await activate_silence_mode(payload.duration_hours)


@app.post("/api/silence/deactivate")
async def api_silence_deactivate(payload: Optional[SilenceDeactivatePayload] = Body(default=None)):
    """Désactive le mode Silence ! et restaure immédiatement les paramètres initiaux (avec surcharges éventuelles)."""
    override_cmds = payload.override_commands if payload else None
    return await deactivate_silence_mode(is_auto_expired=False, override_commands=override_cmds)


@app.get("/api/silence/status")
async def api_silence_status():
    """Retourne l'état actuel du mode Silence !, le compte à rebours restant et les valeurs initiales."""
    return get_silence_status()


# ==============================================================================
# PONT THERMOSTAT MATTER LOCAL (GOOGLE HOME / NEST HUB MAX / APPLE HOME)
# ==============================================================================
@app.get("/api/matter/status")
async def api_matter_status():
    """Retourne l'état du pont Thermostat Matter, le QR Code SVG et le code d'appairage à 11 chiffres."""
    return await matter_manager.get_status()


@app.post("/api/matter/restart")
async def api_matter_restart():
    """Redémarre le pont Thermostat Matter."""
    await matter_manager.stop()
    return await matter_manager.ensure_started()


@app.post("/api/matter/factory-reset")
async def api_matter_factory_reset():
    """Réinitialise l'appairage Matter et génère un nouveau code d'appairage."""
    return await matter_manager.factory_reset()


@app.post("/api/matter/webhook")
async def api_matter_webhook(payload: MatterWebhookPayload, request: Request):
    """Point d'entrée interne appelé par le pont Matter lors d'une commande Google Home / Nest Hub Max."""
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="Accès réservé au pont local Matter (127.0.0.1)")

    action = payload.action.strip()
    active_mode = get_active_mode()

    if action == "set_power":
        target_state = (payload.state or "OFF").upper()
        cmd_val = 85.0 if target_state == "ON" else 170.0
        label_action = "Allumage (85)" if target_state == "ON" else "Extinction (170)"
        db_log(
            "COMMAND",
            "GOOGLE_MATTER",
            f"Commande Google Home / Nest Hub Max reçue : {label_action} du poêle.",
            active_mode,
        )
        res = await execute_register_write("status_code", cmd_val, 232)
        return {"status": "ok", "action": action, "state": target_state, "result": res}

    elif action == "set_temperature":
        if payload.temperature is None:
            raise HTTPException(status_code=400, detail="Température Matter requise")
        temp_c = float(max(7, min(40, round(payload.temperature))))
        db_log(
            "COMMAND",
            "GOOGLE_MATTER",
            f"Commande Google Home / Nest Hub Max reçue : Consigne T° Ambiante réglée à {temp_c:g} °C.",
            active_mode,
        )
        res = await execute_register_write("temp_amb_setpoint", temp_c, 33122)
        return {"status": "ok", "action": action, "temperature": temp_c, "result": res}

    raise HTTPException(status_code=400, detail=f"Action Matter inconnue : {action}")



@app.post("/api/refill-hopper")
async def api_refill_hopper(payload: Optional[RefillHopperPayload] = Body(default=None)):
    bags_added = payload.bags_added if payload is not None else 2.0
    active_mode = get_active_mode()

    conn = get_db(active_mode)
    try:
        last_row = conn.execute("SELECT id, pellet_hopper_pct FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        prev_pct = float(last_row["pellet_hopper_pct"]) if last_row else 50.0
        if payload is not None and payload.bags_added == 1.0:
            added_pct = (PELLET_BAG_WEIGHT_KG / HOPPER_CAPACITY_KG) * 100.0
            new_pct = round(min(100.0, prev_pct + added_pct), 1)
        else:
            new_pct = round(min(100.0, payload.target_pct if payload is not None else 100.0), 1)

        if last_row:
            conn.execute(
                "UPDATE telemetry SET pellet_hopper_pct = ?, popup_pellet_empty = 0, epoch = ? WHERE id = ?",
                (new_pct, int(time.time()), last_row["id"])
            )
            conn.commit()
    finally:
        conn.close()

    # Mise à jour du stock de sacs de 15 kg et du compteur saisonnier
    settings = get_settings_dict()
    try:
        cur_stock = float(settings.get("pellet_bags_stock", "40"))
    except (ValueError, TypeError):
        cur_stock = 40.0
    try:
        cur_used = float(settings.get("pellet_bags_used_season", "0"))
    except (ValueError, TypeError):
        cur_used = 0.0

    new_stock = max(0.0, round(cur_stock - bags_added, 1))
    new_used = round(cur_used + bags_added, 1)

    sconn = get_sys_db()
    try:
        sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('pellet_bags_stock', ?)", (str(new_stock),))
        sconn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('pellet_bags_used_season', ?)", (str(new_used),))
        sconn.commit()
    finally:
        sconn.close()

    kg_added = round(bags_added * PELLET_BAG_WEIGHT_KG, 1)
    db_log(
        "COMMAND",
        "HOPPER",
        f"Trémie rechargée (+{bags_added:g} sac(s) / +{kg_added:g} kg) -> {new_pct}% (Capacité {int(HOPPER_CAPACITY_KG)} kg | Stock restant: {new_stock:g} sacs).",
        active_mode
    )
    latest = await collect_telemetry_cycle(active_mode)
    conn = get_db(active_mode)
    try:
        new_last = conn.execute("SELECT id FROM telemetry ORDER BY epoch DESC LIMIT 1").fetchone()
        if new_last:
            conn.execute("UPDATE telemetry SET pellet_hopper_pct = ? WHERE id = ?", (new_pct, new_last["id"]))
            conn.commit()
    finally:
        conn.close()
    latest["pellet_hopper_pct"] = new_pct
    return {
        "status": "ok",
        "pellet_hopper_pct": new_pct,
        "bags_stock": new_stock,
        "bags_used_season": new_used,
        "latest": latest
    }


@app.post("/api/pellet-batch/raz")
async def api_pellet_batch_raz(payload: Dict[str, Any] = Body(default={})):
    """Réinitialise l'évaluation algorithmique lors d'un changement de marque de granulés."""
    brand = str(payload.get("pellet_brand") or "Nouveau Lot Certifié A1")
    return await reset_pellet_batch_calibration(brand)


@app.get("/api/pellet-batch/status")
async def api_pellet_batch_status():
    """Retourne l'état courant du lot de granulés évalué."""
    return get_pellet_batch_state()


@app.post("/api/sync-clock")
async def api_sync_clock():
    now = datetime.now()
    clock_commands = [
        {"reg_key": "clock_hour_set", "offset": 34048, "value": float(now.hour)},
        {"reg_key": "clock_minute_set", "offset": 34049, "value": float(now.minute)},
        {"reg_key": "calendar_day_set", "offset": 34050, "value": float(now.day)},
        {"reg_key": "calendar_month_set", "offset": 34051, "value": float(now.month)},
        {"reg_key": "calendar_year_set", "offset": 34052, "value": float(now.year - 2000)},
        {"reg_key": "calendar_day_week_set", "offset": 34053, "value": float(now.isoweekday())},
    ]
    await execute_registers_batch_write(clock_commands, skip_poll=True)
    active_mode = get_active_mode()
    db_log("COMMAND", "RTC_SYNC", f"Horloge synchronisée sur {now.strftime('%H:%M %d/%m/%Y')}", active_mode)
    latest = await collect_telemetry_cycle(active_mode)
    return {"status": "ok", "synced_to": now.strftime("%H:%M %d/%m/%Y"), "latest": latest}


@app.post("/api/rollback/{audit_id}")
async def api_rollback(audit_id: int):
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        row = conn.execute("SELECT * FROM command_audit WHERE id = ?", (audit_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Entrée d'audit introuvable")
    return await execute_register_write(row["reg_key"], float(row["old_value"]), int(row["offset"]))


# ==============================================================================
# SÉCURITÉ ACCÈS INSTALLATEUR (AUTHENTIFICATION PAR MOT DE PASSE AGUA CLOUD)
# ==============================================================================
_installer_sessions: Dict[str, float] = {}  # token -> expires_at
_failed_auth_attempts: Dict[str, List[float]] = {}  # ip -> timestamps


def _clean_expired_installer_sessions():
    now_ts = time.time()
    for tok in list(_installer_sessions.keys()):
        if _installer_sessions[tok] <= now_ts:
            _installer_sessions.pop(tok, None)


def is_installer_authenticated(request: Request) -> bool:
    _clean_expired_installer_sessions()
    tok = request.headers.get("X-Installer-Token", "").strip()
    return bool(tok and tok in _installer_sessions and _installer_sessions[tok] > time.time())


@app.post("/api/installer/verify")
async def api_installer_verify(payload: InstallerVerifyPayload, request: Request):
    """Vérifie le mot de passe Agua Cloud pour déverrouiller l'accès au menu installateur et registres."""
    client_ip = request.client.host if request.client else "unknown"
    now_ts = time.time()

    # Protection anti force-brute (max 5 échecs consécutifs en 60 secondes par IP)
    attempts = [t for t in _failed_auth_attempts.get(client_ip, []) if now_ts - t < 60.0]
    if len(attempts) >= 5:
        db_log("WARNING", "SECURITY", f"Blocage anti force-brute accès installateur déclenché pour l'IP {client_ip}.", get_active_mode())
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives erronées. Veuillez patienter 60 secondes avant de réessayer."
        )

    settings = get_settings_dict()
    stored_pwd = (settings.get("agua_password") or "").strip()
    active_mode = get_active_mode()

    if not stored_pwd:
        db_log("WARNING", "SECURITY", "Tentative d'accès installateur rejetée : aucun mot de passe Agua Cloud configuré.", active_mode)
        raise HTTPException(
            status_code=400,
            detail="Aucun mot de passe Agua Cloud n'est configuré dans les réglages. Veuillez d'abord le renseigner."
        )

    # Comparaison à temps constant contre les attaques temporelles (timing attacks)
    submitted_bytes = payload.password.strip().encode("utf-8")
    stored_bytes = stored_pwd.encode("utf-8")
    if not hmac.compare_digest(submitted_bytes, stored_bytes):
        attempts.append(now_ts)
        _failed_auth_attempts[client_ip] = attempts
        db_log("WARNING", "SECURITY", f"Échec d'authentification installateur depuis {client_ip} : mot de passe incorrect.", active_mode)
        raise HTTPException(status_code=401, detail="Mot de passe Agua Cloud incorrect.")

    # Succès : réinitialiser le compteur d'échecs
    _failed_auth_attempts.pop(client_ip, None)
    token = secrets.token_hex(24)
    # Session valide 2 heures (7200s)
    expires_at = now_ts + 7200.0
    _installer_sessions[token] = expires_at

    db_log("INFO", "SECURITY", f"Accès menu installateur déverrouillé avec succès (IP {client_ip}).", active_mode)
    return {
        "status": "ok",
        "success": True,
        "authenticated": True,
        "token": token,
        "expires_in_s": 7200
    }


@app.get("/api/installer/status")
async def api_installer_status(request: Request):
    """Retourne l'état d'authentification actuel du menu installateur."""
    settings = get_settings_dict()
    has_pwd = bool((settings.get("agua_password") or "").strip())
    authenticated = is_installer_authenticated(request)
    return {
        "authenticated": authenticated,
        "has_password_configured": has_pwd
    }


@app.post("/api/installer/lock")
async def api_installer_lock(request: Request):
    """Verrouille immédiatement la session du menu installateur."""
    tok = request.headers.get("X-Installer-Token", "").strip()
    if tok:
        _installer_sessions.pop(tok, None)
    active_mode = get_active_mode()
    db_log("INFO", "SECURITY", "Verrouillage manuel du menu installateur.", active_mode)
    return {"status": "ok", "authenticated": False}


@app.get("/api/snapshots")
async def api_list_snapshots():
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        rows = conn.execute("SELECT id, timestamp, name, comm_mode, registers_json, raw_buffer_json FROM registers_snapshots ORDER BY id DESC LIMIT 100").fetchall()
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
            "raw_buffer": raw_d
        })
    return result


@app.post("/api/snapshots")
async def api_create_snapshot(payload: SnapshotCreatePayload = Body(default=SnapshotCreatePayload())):
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
            (now_str, snap_name, active_mode, json.dumps(regs_snapshot), raw_json_str)
        )
        snap_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    db_log("INFO", "SNAPSHOT", f"Snapshot #{snap_id} créé : '{snap_name}'.", active_mode)
    return {"status": "ok", "id": snap_id, "name": snap_name, "timestamp": now_str}


@app.post("/api/snapshots/{snap_id}/restore")
async def api_restore_snapshot(snap_id: int):
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


@app.delete("/api/snapshots/{snap_id}")
async def api_delete_snapshot(snap_id: int):
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        conn.execute("DELETE FROM registers_snapshots WHERE id = ?", (snap_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


def sanitize_csv_cell(val: Any) -> str:
    """Neutralise le risque d'injection de formules CSV (CWE-1236 / OWASP Formula Injection)."""
    s = str(val if val is not None else "")
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s.replace(";", ",").replace("\r", " ").replace("\n", " ")


@app.get("/api/snapshots/{snap_id}/export-csv")
async def api_export_snapshot_csv(snap_id: int):
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

    filename = f"snapshot_{snap_id}_{active_mode}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/audit")
async def api_get_audit():
    conn = get_db(get_active_mode())
    try:
        rows = conn.execute("SELECT * FROM command_audit ORDER BY id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/logs")
async def api_get_logs(severity: str = Query("ALL")):
    conn = get_db(get_active_mode())
    try:
        sev = (severity or "ALL").strip().upper()
        if sev == "ALL":
            rows = conn.execute("SELECT * FROM app_logs ORDER BY id DESC LIMIT 250").fetchall()
        elif sev in ("WARNING", "WARN"):
            rows = conn.execute("SELECT * FROM app_logs WHERE severity IN ('WARNING', 'WARN') ORDER BY id DESC LIMIT 250").fetchall()
        else:
            rows = conn.execute("SELECT * FROM app_logs WHERE severity = ? ORDER BY id DESC LIMIT 250", (sev,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/logs/client-error")
async def api_log_client_error(payload: ClientErrorPayload):
    severity = payload.severity.strip().upper()
    source = payload.source.strip()[:25]
    message = payload.message[:500]
    active_mode = get_active_mode()
    db_log(severity, source, message, active_mode)
    return {"status": "ok"}


@app.get("/api/export-logs-csv")
async def api_export_logs_csv(severity: str = Query("ALL")):
    active_mode = get_active_mode()
    conn = get_db(active_mode)
    try:
        sev = (severity or "ALL").strip().upper()
        if sev == "ALL":
            rows = conn.execute("SELECT id, timestamp, severity, source, message FROM app_logs ORDER BY id DESC").fetchall()
        elif sev in ("WARNING", "WARN"):
            rows = conn.execute("SELECT id, timestamp, severity, source, message FROM app_logs WHERE severity IN ('WARNING', 'WARN') ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT id, timestamp, severity, source, message FROM app_logs WHERE severity = ? ORDER BY id DESC",
                (sev,)
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

    filename = f"nobis_a10_logs_{active_mode}_{severity.lower()}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.post("/api/settings")
async def api_save_settings(payload: SettingsPayload):
    raw_dict = payload.model_dump(exclude_none=True)
    conn = get_sys_db()
    try:
        for k, v in raw_dict.items():
            if v is not None and str(v) != "********":
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (str(k), str(v)))
        conn.commit()
    finally:
        conn.close()

    if "agua_password" in raw_dict and raw_dict["agua_password"] != "********":
        _installer_sessions.clear()

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
    db_log("INFO", "SETTINGS", f"Configuration enregistrée. Base active : {os.path.basename(MODE_DB_PATHS[new_mode])}", new_mode)

    # Réévaluation de l'état du pont Matter selon la configuration
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
    return {"status": "ok", "settings": get_public_settings_dict(), "active_db": os.path.basename(MODE_DB_PATHS[new_mode])}


@app.post("/api/test-webhook")
async def api_test_webhook(payload: TestWebhookPayload = Body(default=TestWebhookPayload())):
    settings = get_settings_dict()
    url = (payload.webhook_url or settings.get("webhook_url") or "").strip()
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
        active_mode
    )
    if not ok:
        raise HTTPException(status_code=502, detail="Échec de l'envoi vers l'URL Webhook.")
    return {"status": "ok", "message": "Notification de test envoyée avec succès !"}


@app.post("/api/test-connection")
async def api_test_connection(payload: TestConnectionPayload):
    raw_dict = payload.model_dump(exclude_none=True)
    mode = raw_dict.get("comm_mode", "simulation")
    settings = get_settings_dict()
    settings.update({k: str(v) for k, v in raw_dict.items() if str(v) != "********"})
    weather = await weather_service.fetch_current(settings.get("weather_lat", "48.1636"), settings.get("weather_lon", "2.0436"))
    if mode == "simulation":
        return {"status": "ok", "mode": "simulation", "active_db": os.path.basename(MODE_DB_PATHS["simulation"]), "weather": weather}
    elif mode == "agua_cloud":
        try:
            res = await agua_client.login_and_discover(settings.get("agua_email", ""), settings.get("agua_password", ""), settings.get("agua_uuid", ""))
            return {"status": "ok", "mode": "agua_cloud", "active_db": os.path.basename(MODE_DB_PATHS["agua_cloud"]), "details": res, "weather": weather}
        except Exception as e:
            db_log("ERROR", "AGUA_CLOUD", f"Échec test connexion Agua IOT : {str(e)}", "agua_cloud")
            return JSONResponse(status_code=502, content={"status": "error", "details": f"Erreur Cloud Agua IOT : {str(e)}"})
    elif mode == "local_gateway":
        gw_url = settings.get("local_gateway_url", "http://192.168.1.150")
        try:
            res = await local_gateway_client.test_connection(gw_url)
            return {"status": "ok", "mode": "local_gateway", "details": res, "weather": weather}
        except Exception as e:
            db_log("ERROR", "LOCAL_GATEWAY", f"Échec test connexion Passerelle UART : {str(e)}", "local_gateway")
            return JSONResponse(status_code=502, content={"status": "error", "details": f"Erreur Passerelle UART : {str(e)}"})


@app.get("/api/agua-registers-map")
async def api_agua_registers_map():
    settings = get_settings_dict()
    reg_map = await agua_client.scan_registers_map(settings)
    raw_buffer = await agua_client.read_buffer(settings)
    return {
        "id_device": agua_client.id_device,
        "id_product": agua_client.id_product,
        "working_write_endpoint": agua_client.working_write_endpoint,
        "cached_offsets_count": len(agua_client.registers_map_by_offset),
        "raw_buffer_count": len(raw_buffer),
        "raw_buffer": raw_buffer,
        "registers_map": reg_map
    }


@app.get("/api/export-csv")
async def api_export_csv(range_key: str = Query("24h")):
    active_mode = get_active_mode()
    now_epoch = int(time.time())
    durations = {
        "3h": 3 * 3600,
        "6h": 6 * 3600,
        "24h": 24 * 3600,
        "7j": 7 * 86400,
        "30j": 30 * 86400,
        "3M": 90 * 86400
    }
    min_epoch = now_epoch - durations.get(range_key, 86400)

    conn = get_db(active_mode)
    try:
        # Exclusion stricte des lignes reconstituées (id 1 à 27) et buffers vides invalides
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

    filename = f"nobis_a10_supervision_et_registres_{active_mode}_{range_key}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
