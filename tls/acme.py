"""Émission et renouvellement automatique Let's Encrypt par challenge ACME DNS-01 (acme.sh)."""
import asyncio
from datetime import datetime
import logging
import os
from typing import Any

from database import add_container_log, db_log, get_active_mode, get_settings_dict
from services.lifecycle import delayed_process_restart, spawn_background_task
from tls.config import (
    DNS_PROVIDERS,
    find_acme_sh,
    get_acme_home,
    get_letsencrypt_paths,
    validate_domain,
    validate_email,
)
from tls.selfsigned import inspect_cert

logger = logging.getLogger("nobis.tls.acme")

_acme_lock = asyncio.Lock()
_tls_operation_state: dict[str, Any] = {
    "in_progress": False,
    "action": None,
    "last_result": None,
    "last_error": None,
    "last_run_at": None,
}


async def _run_acme_subprocess(cmd: list[str], env: dict[str, str], timeout_s: float) -> tuple[int, str]:
    """Exécute un sous-processus acme.sh avec arrêt propre garanti (kill + wait) en cas de timeout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception as exc:
            logger.debug("Attente fin processus acme.sh ignorée : %s", exc)
        raise
    combined = (stdout_b.decode("utf-8", errors="replace") + "\n" + stderr_b.decode("utf-8", errors="replace")).strip()
    return int(proc.returncode or 0), combined


def _build_acme_provider_env(provider_key: str, token: str) -> dict[str, str]:
    """Prépare les variables d'environnement secrètes pour acme.sh (jamais passées en CLI)."""
    if provider_key not in DNS_PROVIDERS:
        raise ValueError(f"Fournisseur DNS non supporté : '{provider_key}'.")
    tok = (token or "").strip()
    if not tok:
        raise ValueError("Token API DNS requis.")

    cfg = DNS_PROVIDERS[provider_key]
    env = dict(os.environ)
    if cfg["token_format"] == "ovh_triple":
        parts = [p.strip() for p in tok.split(":")]
        if len(parts) != 3 or not all(parts):
            raise ValueError("Format OVH invalide : utilisez 'ApplicationKey:ApplicationSecret:ConsumerKey'.")
        env["OVH_END_POINT"] = "ovh-eu"
        env["OVH_AK"] = parts[0]
        env["OVH_AS"] = parts[1]
        env["OVH_CK"] = parts[2]
    else:
        env[cfg["env_var"]] = tok
    return env


def _sanitize_acme_output(raw: str, secret_token: str) -> str:
    """Masque toute occurrence potentielle du token secret dans les logs d'erreur ACME."""
    text = (raw or "").strip()
    if secret_token:
        for piece in secret_token.split(":"):
            p = piece.strip()
            if len(p) >= 6:
                text = text.replace(p, "***")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    tail = "\n".join(lines[-8:])
    return tail[:600]


async def issue_letsencrypt_cert(
    domain: str,
    provider_key: str,
    token: str,
    email: str = "",
    force: bool = False,
) -> tuple[bool, str]:
    """Émet ou renouvelle un certificat Let's Encrypt par challenge DNS-01 via acme.sh."""
    acme_bin = find_acme_sh()
    if not acme_bin:
        return (
            False,
            "Client ACME (acme.sh) introuvable dans cet environnement. Cette fonction est disponible dans le conteneur Docker.",
        )

    clean_domain = validate_domain(domain)
    clean_email = validate_email(email)
    if provider_key not in DNS_PROVIDERS:
        raise ValueError(f"Fournisseur DNS non supporté : '{provider_key}'.")
    provider_cfg = DNS_PROVIDERS[provider_key]
    sub_env = _build_acme_provider_env(provider_key, token)

    acme_home = get_acme_home()
    le_cert, le_key = get_letsencrypt_paths()

    async with _acme_lock:
        _tls_operation_state.update(
            {
                "in_progress": True,
                "action": "letsencrypt_issue",
                "last_error": None,
                "last_run_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        try:
            if clean_email:
                await _run_acme_subprocess(
                    [
                        acme_bin,
                        "--register-account",
                        "--server",
                        "letsencrypt",
                        "-m",
                        clean_email,
                        "--home",
                        acme_home,
                    ],
                    env=sub_env,
                    timeout_s=60,
                )

            issue_cmd = [
                acme_bin,
                "--issue",
                "--server",
                "letsencrypt",
                "--dns",
                provider_cfg["acme_hook"],
                "-d",
                clean_domain,
                "--keylength",
                "ec-256",
                "--home",
                acme_home,
            ]
            if force:
                issue_cmd.append("--force")

            rc, combined = await _run_acme_subprocess(issue_cmd, env=sub_env, timeout_s=180)
            if rc not in (0, 2):
                safe_err = _sanitize_acme_output(combined, token)
                _tls_operation_state.update({"last_result": "error", "last_error": safe_err})
                return False, f"Échec validation DNS-01 Let's Encrypt : {safe_err}"

            install_cmd = [
                acme_bin,
                "--install-cert",
                "-d",
                clean_domain,
                "--ecc",
                "--fullchain-file",
                le_cert,
                "--key-file",
                le_key,
                "--home",
                acme_home,
            ]
            inst_rc, inst_combined = await _run_acme_subprocess(install_cmd, env=sub_env, timeout_s=30)
            cert_exists = await asyncio.to_thread(os.path.isfile, le_cert)
            key_exists = await asyncio.to_thread(os.path.isfile, le_key)
            if inst_rc != 0 or not cert_exists or not key_exists:
                safe_err = _sanitize_acme_output(inst_combined, token)
                _tls_operation_state.update({"last_result": "error", "last_error": safe_err})
                return False, f"Certificat émis mais échec d'installation : {safe_err}"

            try:
                await asyncio.to_thread(os.chmod, le_key, 0o600)
            except OSError:
                pass

            msg = f"Certificat Let's Encrypt prêt pour {clean_domain}."
            _tls_operation_state.update({"last_result": "ok", "last_error": None})
            return True, msg

        except TimeoutError:
            err = "Délai dépassé lors de la validation DNS-01 Let's Encrypt (timeout 180s)."
            _tls_operation_state.update({"last_result": "error", "last_error": err})
            return False, err
        except Exception as e:
            err = f"Erreur interne ACME : {e}"
            _tls_operation_state.update({"last_result": "error", "last_error": err})
            return False, err
        finally:
            _tls_operation_state["in_progress"] = False


async def check_tls_renewal_job() -> None:
    """Tâche APScheduler quotidienne : renouvelle automatiquement Let's Encrypt à J-30."""
    try:
        settings = get_settings_dict()
        if (settings.get("https_mode") or "").strip().lower() != "letsencrypt":
            return
        le_cert, _ = get_letsencrypt_paths()
        cert_info = await asyncio.to_thread(inspect_cert, le_cert)
        days = cert_info.get("days_remaining")
        if days is not None and days > 30:
            return

        domain = (settings.get("https_domain") or "").strip()
        provider = (settings.get("https_dns_provider") or "").strip()
        token = (settings.get("https_dns_token") or "").strip()
        email = (settings.get("https_email") or "").strip()
        if not domain or not provider or not token:
            return

        active_mode = get_active_mode()
        add_container_log("INFO", "TLS", f"Renouvellement automatique Let's Encrypt déclenché pour {domain} (J-{days}).")
        ok, msg = await issue_letsencrypt_cert(domain=domain, provider_key=provider, token=token, email=email, force=False)
        if ok:
            db_log("INFO", "TLS", f"Renouvellement Let's Encrypt réussi : {msg}", active_mode)
            add_container_log("INFO", "TLS", "Renouvellement réussi, redémarrage pour recharger le certificat.")
            spawn_background_task(delayed_process_restart(1.0))
        else:
            db_log("ERROR", "TLS", f"Échec renouvellement automatique Let's Encrypt : {msg}", active_mode)
            add_container_log("ERROR", "TLS", f"Échec renouvellement automatique : {msg}")
    except Exception as e:
        add_container_log("ERROR", "TLS", f"Erreur tâche renouvellement TLS : {e}")
