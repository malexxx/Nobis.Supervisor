"""Résolution du mode TLS au démarrage Uvicorn et synthèse d'état pour l'interface Paramètres."""
import os
from typing import Any

from database import get_settings_dict
from tls.acme import _tls_operation_state
from tls.config import (
    DNS_PROVIDERS,
    VALID_HTTPS_MODES,
    find_acme_sh,
    get_active_runtime_tls_mode,
    get_configured_https_mode,
    get_default_https_mode,
    get_letsencrypt_paths,
    get_selfsigned_paths,
    parse_san_dns,
    parse_san_ips,
)
from tls.selfsigned import ensure_selfsigned_cert, inspect_cert


def resolve_runtime_tls() -> tuple[str, str | None, str | None]:
    """
    Détermine le mode TLS et les fichiers cert/key pour le lancement d'Uvicorn.
    Garantit un repli sécurisé (selfsigned -> http) pour ne jamais bloquer le démarrage.
    """
    if os.environ.get("FORCE_HTTP", "").strip().lower() in ("1", "true", "yes", "on"):
        os.environ["NOBIS_ACTIVE_TLS_MODE"] = "http"
        return "http", None, None

    settings = get_settings_dict()
    desired = (settings.get("https_mode") or "").strip().lower()
    if desired not in VALID_HTTPS_MODES:
        desired = get_default_https_mode()

    if desired == "http":
        os.environ["NOBIS_ACTIVE_TLS_MODE"] = "http"
        return "http", None, None

    if desired == "letsencrypt":
        le_cert, le_key = get_letsencrypt_paths()
        if os.path.isfile(le_cert) and os.path.isfile(le_key) and os.path.getsize(le_cert) > 0 and os.path.getsize(le_key) > 0:
            os.environ["NOBIS_ACTIVE_TLS_MODE"] = "letsencrypt"
            return "letsencrypt", le_cert, le_key
        desired = "selfsigned"

    if desired == "selfsigned":
        try:
            extra_ips = parse_san_ips(settings.get("https_san_ips", ""))
        except ValueError:
            extra_ips = []
        try:
            extra_dns = parse_san_dns(settings.get("https_san_dns", ""))
        except ValueError:
            extra_dns = []
        ok, _ = ensure_selfsigned_cert(extra_ips=extra_ips, extra_dns=extra_dns, force=False)
        if ok:
            cert_path, key_path = get_selfsigned_paths()
            os.environ["NOBIS_ACTIVE_TLS_MODE"] = "selfsigned"
            return "selfsigned", cert_path, key_path

    os.environ["NOBIS_ACTIVE_TLS_MODE"] = "http"
    return "http", None, None


def get_active_cert_path() -> str | None:
    """Retourne le chemin du certificat public actif (auto-signé ou Let's Encrypt), ou None en HTTP."""
    active = get_active_runtime_tls_mode()
    if active == "letsencrypt":
        p, _ = get_letsencrypt_paths()
        return p if os.path.isfile(p) else None
    if active == "selfsigned":
        p, _ = get_selfsigned_paths()
        return p if os.path.isfile(p) else None
    p, _ = get_selfsigned_paths()
    return p if os.path.isfile(p) else None


def get_tls_status() -> dict[str, Any]:
    """Retourne l'état complet TLS/HTTPS pour l'interface Paramètres (sans jamais exposer le token DNS)."""
    import tls_service as _tls_facade

    settings = get_settings_dict()
    configured_mode = get_configured_https_mode()
    active_mode = get_active_runtime_tls_mode()

    selfsigned_cert, _ = get_selfsigned_paths()
    le_cert, _ = get_letsencrypt_paths()

    ss_info = inspect_cert(selfsigned_cert)
    le_info = inspect_cert(le_cert)
    active_cert_info = le_info if active_mode == "letsencrypt" else (ss_info if active_mode == "selfsigned" else None)

    providers_list = [
        {
            "id": key,
            "label": cfg["label"],
            "token_placeholder": cfg["token_placeholder"],
            "help": cfg["help"],
        }
        for key, cfg in DNS_PROVIDERS.items()
    ]

    return {
        "active_mode": active_mode,
        "configured_mode": configured_mode,
        "force_http": os.environ.get("FORCE_HTTP", "").strip().lower() in ("1", "true", "yes", "on"),
        "openssl_available": bool(_tls_facade.find_openssl()),
        "acme_available": bool(find_acme_sh()),
        "https_domain": (settings.get("https_domain") or "").strip(),
        "https_dns_provider": (settings.get("https_dns_provider") or "duckdns").strip() or "duckdns",
        "https_email": (settings.get("https_email") or "").strip(),
        "has_dns_token": bool((settings.get("https_dns_token") or "").strip()),
        "https_san_ips": (settings.get("https_san_ips") or "").strip(),
        "https_san_dns": (settings.get("https_san_dns") or "").strip(),
        "active_cert": active_cert_info,
        "selfsigned_cert": ss_info,
        "letsencrypt_cert": le_info,
        "providers": providers_list,
        "operation": dict(_tls_operation_state),
    }
