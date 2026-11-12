"""
Sous-routeur pour la sécurité et l'authentification du menu installateur.
Protège l'accès aux registres avancés via mot de passe Agua Cloud, sessions à jetons éphémères,
comparaison à temps constant (anti-timing attacks) et limitation anti force-brute.
"""
import hmac
import secrets
import time
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from database import db_log, get_active_mode, get_settings_dict
from schemas import InstallerVerifyPayload

router = APIRouter(prefix="/api/installer", tags=["Installer"])

_installer_sessions: Dict[str, float] = {}  # token -> expires_at
_failed_auth_attempts: Dict[str, List[float]] = {}  # ip -> timestamps


def _clean_expired_installer_sessions():
    """Purge de la mémoire les jetons de session installateur expirés."""
    now_ts = time.time()
    for tok in list(_installer_sessions.keys()):
        if _installer_sessions[tok] <= now_ts:
            _installer_sessions.pop(tok, None)


def is_installer_authenticated(request: Request) -> bool:
    """Vérifie si la requête HTTP contient un en-tête X-Installer-Token valide et non expiré."""
    _clean_expired_installer_sessions()
    tok = request.headers.get("X-Installer-Token", "").strip()
    return bool(tok and tok in _installer_sessions and _installer_sessions[tok] > time.time())


def clear_installer_sessions():
    """Réinitialise toutes les sessions installateur actives (ex: changement de mot de passe)."""
    _installer_sessions.clear()


@router.post("/verify", responses={400: {"description": "Mot de passe non configuré"}, 401: {"description": "Mot de passe Agua Cloud incorrect"}, 429: {"description": "Trop de tentatives échouées"}})
async def api_installer_verify(payload: InstallerVerifyPayload, request: Request):
    """Vérifie le mot de passe Agua Cloud pour déverrouiller l'accès au menu installateur et registres."""
    client_ip = request.client.host if request.client else "unknown"
    now_ts = time.time()

    attempts = [t for t in _failed_auth_attempts.get(client_ip, []) if now_ts - t < 60.0]
    if len(attempts) >= 5:
        db_log("WARNING", "SECURITY", f"Blocage anti force-brute accès installateur déclenché pour l'IP {client_ip}.", get_active_mode())
        raise HTTPException(
            status_code=429,
            detail="Trop de tentatives erronées. Veuillez patienter 60 secondes avant de réessayer.",
        )

    settings = get_settings_dict()
    stored_pwd = (settings.get("agua_password") or "").strip()
    active_mode = get_active_mode()

    if not stored_pwd:
        db_log("WARNING", "SECURITY", "Tentative d'accès installateur rejetée : aucun mot de passe Agua Cloud configuré.", active_mode)
        raise HTTPException(
            status_code=401,
            detail="Aucun mot de passe Agua Cloud n'est configuré dans les réglages. Veuillez d'abord le renseigner.",
        )

    submitted_bytes = payload.password.strip().encode("utf-8")
    stored_bytes = stored_pwd.encode("utf-8")
    if not hmac.compare_digest(submitted_bytes, stored_bytes):
        attempts.append(now_ts)
        _failed_auth_attempts[client_ip] = attempts
        db_log("WARNING", "SECURITY", f"Échec d'authentification installateur depuis {client_ip} : mot de passe incorrect.", active_mode)
        raise HTTPException(status_code=401, detail="Mot de passe Agua Cloud incorrect.")

    _failed_auth_attempts.pop(client_ip, None)
    token = secrets.token_hex(24)
    expires_at = now_ts + 7200.0
    _installer_sessions[token] = expires_at

    db_log("INFO", "SECURITY", f"Accès menu installateur déverrouillé avec succès (IP {client_ip}).", active_mode)
    return {
        "status": "ok",
        "success": True,
        "authenticated": True,
        "token": token,
        "expires_in_s": 7200,
    }


@router.get("/status")
async def api_installer_status(request: Request):
    """Retourne l'état d'authentification actuel du menu installateur."""
    settings = get_settings_dict()
    has_pwd = bool((settings.get("agua_password") or "").strip())
    authenticated = is_installer_authenticated(request)
    return {
        "authenticated": authenticated,
        "has_password_configured": has_pwd,
    }


@router.post("/lock")
async def api_installer_lock(request: Request):
    """Verrouille immédiatement la session du menu installateur."""
    tok = request.headers.get("X-Installer-Token", "").strip()
    if tok:
        _installer_sessions.pop(tok, None)
    active_mode = get_active_mode()
    db_log("INFO", "SECURITY", "Verrouillage manuel du menu installateur.", active_mode)
    return {"status": "ok", "authenticated": False}
