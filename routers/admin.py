"""Sous-routeur d'administration système : redémarrage backend, stockage/sauvegarde ZIP et MAJ Git / Watchtower."""
import asyncio
from datetime import datetime
import os
import subprocess

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import httpx

from database import add_container_log, db_log, get_active_mode
from services.installer_auth import clear_installer_sessions, require_installer_auth_if_configured
from services.lifecycle import spawn_background_task
from services.storage_service import (
    MAX_BACKUP_UPLOAD_BYTES,
    create_backup_zip_bytes,
    inspect_storage_persistence,
    restore_backup_zip_bytes,
)

router = APIRouter(prefix="/api", tags=["System Admin"])
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.get("/system/storage")
async def api_system_storage():
    """Retourne le diagnostic de persistance du dossier DATA_DIR (bind mount, volume nommé, anonyme ou éphémère)."""
    return await asyncio.to_thread(inspect_storage_persistence)


@router.get(
    "/system/backup",
    responses={
        200: {"content": {"application/zip": {}}},
        403: {"description": "Authentification installateur requise"},
    },
)
async def api_system_backup_download(request: Request):
    """Télécharge une sauvegarde ZIP atomique complète (bases SQLite, .secret.key, certificats TLS et Matter)."""
    require_installer_auth_if_configured(request)
    zip_bytes = await asyncio.to_thread(create_backup_zip_bytes)
    active_mode = await asyncio.to_thread(get_active_mode)
    db_log("INFO", "BACKUP", f"Export sauvegarde complète généré ({len(zip_bytes)} octets).", active_mode)
    add_container_log("INFO", "BACKUP", f"Export sauvegarde complète généré ({len(zip_bytes)} octets).")
    filename = f"nobis_supervisor_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/system/backup/restore",
    responses={
        400: {"description": "Archive ZIP invalide ou rejetée"},
        403: {"description": "Authentification installateur requise"},
        413: {"description": "Archive trop volumineuse"},
    },
)
async def api_system_backup_restore(request: Request):
    """Restaure atomiquement une sauvegarde ZIP validée dans DATA_DIR (bases SQLite, .secret.key, certs, Matter)."""
    require_installer_auth_if_configured(request)
    body = await request.body()
    if len(body) > MAX_BACKUP_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive de sauvegarde trop volumineuse (limite 100 Mo).")

    try:
        result = await asyncio.to_thread(restore_backup_zip_bytes, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "nobis_a10_system.db" in result.get("restored_files", []):
        clear_installer_sessions()

    active_mode = await asyncio.to_thread(get_active_mode)
    msg = f"Restauration sauvegarde ZIP effectuée ({result['restored_count']} fichier(s))."
    db_log("WARNING", "BACKUP", msg, active_mode)
    add_container_log("WARNING", "BACKUP", msg)
    return result


@router.post("/system/restart", responses={403: {"description": "Authentification installateur requise"}})
async def api_system_restart(request: Request):
    """Déclenche un redémarrage propre du backend (relancé automatiquement par Docker)."""
    import routers.system as _sys_facade

    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    db_log("COMMAND", "SYSTEM", "Redémarrage du backend demandé depuis l'interface.", active_mode)
    add_container_log("WARNING", "SYSTEM", "Redémarrage du backend demandé depuis l'interface.")
    spawn_background_task(_sys_facade._delayed_process_restart(0.5))
    return {"status": "ok", "message": "Redémarrage du backend déclenché. Reconnexion en cours..."}


@router.post(
    "/system/check-update",
    responses={
        403: {"description": "Authentification installateur requise"},
        502: {"description": "Watchtower injoignable ou en erreur"},
    },
)
async def api_system_check_update(request: Request):
    """Déclenche une vérification/mise à jour via l'API HTTP de Watchtower (ou Git hors Docker)."""
    import routers.system as _sys_facade

    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()
    env_watchtower_url = os.environ.get("WATCHTOWER_URL", "").strip()
    in_docker = await asyncio.to_thread(os.path.exists, "/.dockerenv")
    if not env_watchtower_url and not in_docker:
        try:
            res_local = await asyncio.to_thread(
                subprocess.run,
                ["git", "rev-parse", "HEAD"],
                cwd=_REPO_DIR,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            local_sha = (res_local.stdout or "").strip()

            res_remote = await asyncio.to_thread(
                subprocess.run,
                ["git", "ls-remote", "origin", "HEAD"],
                cwd=_REPO_DIR,
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
    watchtower_token = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {watchtower_token}"} if watchtower_token else {}

    add_container_log("INFO", "WATCHTOWER", f"Vérification de mise à jour demandée ({watchtower_url}).")
    try:
        async with _sys_facade.borrow_http_client(timeout=15.0) as client:
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
        raise HTTPException(status_code=502, detail=err_detail) from exc
