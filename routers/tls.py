"""
Sous-routeur pour la gestion HTTPS / TLS autonome (certificat auto-signé & Let's Encrypt DNS-01).
Permet de consulter l'état du certificat actif, télécharger le certificat public (.crt),
basculer entre HTTP / Auto-signé / Let's Encrypt et déclencher l'émission ou le renouvellement ACME.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from database import add_container_log, db_log, get_active_mode, get_settings_dict
from routers.installer import require_installer_auth_if_configured
from routers.system import _delayed_process_restart
from schemas import TlsLetsEncryptPayload, TlsModePayload
from tls_service import (
    DNS_PROVIDERS,
    ensure_selfsigned_cert,
    get_active_cert_path,
    get_tls_status,
    issue_letsencrypt_cert,
    parse_san_dns,
    parse_san_ips,
    save_tls_settings,
    validate_domain,
    validate_email,
)

router = APIRouter(prefix="/api/tls", tags=["TLS & HTTPS"])


@router.get("/status")
async def api_tls_status():
    """Retourne l'état complet du mode HTTPS, du certificat actif et des fournisseurs DNS supportés."""
    return get_tls_status()


@router.get(
    "/certificate",
    responses={
        200: {"content": {"application/x-x509-ca-cert": {}}},
        404: {"description": "Aucun certificat public disponible"},
    },
)
async def api_tls_download_certificate():
    """Télécharge le certificat public actif (.crt) pour installation éventuelle sur mobile/PC."""
    cert_path = get_active_cert_path()
    if not cert_path:
        raise HTTPException(status_code=404, detail="Aucun certificat TLS public disponible pour le moment.")
    return FileResponse(
        cert_path,
        media_type="application/x-x509-ca-cert",
        filename="nobis-supervisor.crt",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.post(
    "/mode",
    responses={
        400: {"description": "Paramètres SAN invalides ou échec génération certificat"},
        403: {"description": "Authentification installateur requise"},
    },
)
async def api_tls_set_mode(payload: TlsModePayload, request: Request):
    """Bascule entre le mode 'http' et 'selfsigned' (avec régénération optionnelle des SAN)."""
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()

    updates = {"https_mode": payload.mode}
    extra_ips = None
    extra_dns = None

    try:
        if payload.san_ips is not None:
            parsed_ips = parse_san_ips(payload.san_ips)
            updates["https_san_ips"] = ", ".join(parsed_ips)
            extra_ips = parsed_ips
        if payload.san_dns is not None:
            parsed_dns = parse_san_dns(payload.san_dns)
            updates["https_san_dns"] = ", ".join(parsed_dns)
            extra_dns = parsed_dns
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if payload.mode == "selfsigned":
        settings = get_settings_dict()
        if extra_ips is None:
            try:
                extra_ips = parse_san_ips(settings.get("https_san_ips", ""))
            except ValueError:
                extra_ips = []
        if extra_dns is None:
            try:
                extra_dns = parse_san_dns(settings.get("https_san_dns", ""))
            except ValueError:
                extra_dns = []

        ok, msg = ensure_selfsigned_cert(extra_ips=extra_ips, extra_dns=extra_dns, force=payload.regenerate)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    else:
        msg = "Mode HTTP (sans TLS) enregistré."

    save_tls_settings(updates)
    db_log("COMMAND", "TLS", f"Changement mode HTTPS -> {payload.mode} ({msg})", active_mode)
    add_container_log("INFO", "TLS", f"Changement mode HTTPS -> {payload.mode} ({msg})")

    if payload.restart:
        asyncio.create_task(_delayed_process_restart(0.8))

    return {
        "status": "ok",
        "mode": payload.mode,
        "restarting": payload.restart,
        "message": msg,
        "tls": get_tls_status(),
    }


@router.post(
    "/letsencrypt/issue",
    responses={
        400: {"description": "Paramètres invalides ou token DNS manquant"},
        403: {"description": "Authentification installateur requise"},
        502: {"description": "Échec validation ACME DNS-01 Let's Encrypt"},
    },
)
async def api_tls_letsencrypt_issue(payload: TlsLetsEncryptPayload, request: Request):
    """
    Valide un domaine via ACME DNS-01 (Let's Encrypt) et bascule en HTTPS officiel uniquement si la validation réussit.
    En cas d'échec, le mode précédent (HTTP ou Auto-signé) reste actif sans perte d'accès.
    """
    require_installer_auth_if_configured(request)
    active_mode = get_active_mode()

    provider_key = payload.dns_provider.strip().lower()
    if provider_key not in DNS_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Fournisseur DNS non supporté : '{provider_key}'.")

    try:
        clean_domain = validate_domain(payload.domain)
        clean_email = validate_email(payload.email or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing_settings = get_settings_dict()
    raw_token = (payload.dns_token or "").strip()
    if not raw_token or raw_token == "********":
        effective_token = (existing_settings.get("https_dns_token") or "").strip()
    else:
        effective_token = raw_token

    if not effective_token:
        raise HTTPException(status_code=400, detail="Veuillez renseigner un token API DNS valide.")

    # Persiste d'abord les paramètres (sans changer https_mode tant que le cert n'est pas validé)
    to_save = {
        "https_domain": clean_domain,
        "https_dns_provider": provider_key,
        "https_email": clean_email,
    }
    if raw_token and raw_token != "********":
        to_save["https_dns_token"] = effective_token
    save_tls_settings(to_save)

    add_container_log("INFO", "TLS", f"Demande certificat Let's Encrypt (DNS-01 / {provider_key}) pour {clean_domain}...")
    try:
        ok, msg = await issue_letsencrypt_cert(
            domain=clean_domain,
            provider_key=provider_key,
            token=effective_token,
            email=clean_email,
            force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not ok:
        db_log("ERROR", "TLS", f"Échec Let's Encrypt ({clean_domain}): {msg}", active_mode)
        add_container_log("ERROR", "TLS", f"Échec Let's Encrypt ({clean_domain}): {msg}")
        raise HTTPException(status_code=502, detail=msg)

    # Succès : on active officiellement le mode letsencrypt puis on redémarre
    save_tls_settings({"https_mode": "letsencrypt"})
    db_log("COMMAND", "TLS", f"Activation HTTPS Let's Encrypt réussie pour {clean_domain}.", active_mode)
    add_container_log("INFO", "TLS", f"Activation HTTPS Let's Encrypt réussie pour {clean_domain}.")

    if payload.restart:
        asyncio.create_task(_delayed_process_restart(1.0))

    return {
        "status": "ok",
        "mode": "letsencrypt",
        "domain": clean_domain,
        "restarting": payload.restart,
        "message": msg,
        "tls": get_tls_status(),
    }


@router.post(
    "/letsencrypt/renew",
    responses={
        400: {"description": "Configuration Let's Encrypt incomplète"},
        403: {"description": "Authentification installateur requise"},
        502: {"description": "Échec du renouvellement ACME"},
    },
)
async def api_tls_letsencrypt_renew(request: Request):
    """Déclenche manuellement le renouvellement du certificat Let's Encrypt configuré."""
    require_installer_auth_if_configured(request)
    settings = get_settings_dict()
    domain = (settings.get("https_domain") or "").strip()
    provider = (settings.get("https_dns_provider") or "").strip().lower()
    token = (settings.get("https_dns_token") or "").strip()
    email = (settings.get("https_email") or "").strip()

    if not domain or not provider or not token:
        raise HTTPException(status_code=400, detail="Configuration Let's Encrypt incomplète (domaine, fournisseur ou token manquant).")

    ok, msg = await issue_letsencrypt_cert(domain=domain, provider_key=provider, token=token, email=email, force=True)
    if not ok:
        raise HTTPException(status_code=502, detail=msg)

    active_mode = get_active_mode()
    db_log("INFO", "TLS", f"Renouvellement manuel Let's Encrypt réussi pour {domain}.", active_mode)
    asyncio.create_task(_delayed_process_restart(1.0))
    return {
        "status": "ok",
        "domain": domain,
        "restarting": True,
        "message": msg,
        "tls": get_tls_status(),
    }
