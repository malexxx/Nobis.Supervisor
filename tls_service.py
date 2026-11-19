"""
Gestionnaire TLS / HTTPS autonome pour Nobis.Supervisor.
Supporte 3 modes persistés en base SQLite :
- 'selfsigned' : certificat auto-signé persistant dans /app/data/certs/selfsigned/ (mode par défaut en Docker)
- 'letsencrypt' : certificat officiel Let's Encrypt via challenge ACME DNS-01 (acme.sh)
- 'http' : HTTP simple sans TLS (utile derrière un reverse proxy externe ou en développement local)
"""
import asyncio
from datetime import datetime, timezone
import ipaddress
import os
import re
import shutil
import socket
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from config import DATA_DIR
from database import add_container_log, db_log, get_active_mode, get_settings_dict, get_sys_db

VALID_HTTPS_MODES = ("http", "selfsigned", "letsencrypt")

# Validation stricte anti-injection pour domaines, emails et entrées SAN
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

# Catalogue des fournisseurs DNS-01 supportés via acme.sh
DNS_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "duckdns": {
        "label": "DuckDNS (Gratuit)",
        "acme_hook": "dns_duckdns",
        "token_format": "single",
        "env_var": "DuckDNS_Token",
        "token_placeholder": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        "help": "Token disponible sur duckdns.org après connexion (domaine type mon-poele.duckdns.org).",
    },
    "cloudflare": {
        "label": "Cloudflare",
        "acme_hook": "dns_cf",
        "token_format": "single",
        "env_var": "CF_Token",
        "token_placeholder": "Token API Cloudflare (Zone:DNS:Edit)",
        "help": "Créez un API Token dans Cloudflare avec la permission Zone -> DNS -> Edit sur votre domaine.",
    },
    "infomaniak": {
        "label": "Infomaniak",
        "acme_hook": "dns_infomaniak",
        "token_format": "single",
        "env_var": "INFOMANIAK_API_TOKEN",
        "token_placeholder": "Token API Infomaniak (scope domain)",
        "help": "Token API généré depuis le Manager Infomaniak avec le scope 'domain'.",
    },
    "gandi": {
        "label": "Gandi LiveDNS",
        "acme_hook": "dns_gandi_livedns",
        "token_format": "single",
        "env_var": "GANDI_LIVEDNS_TOKEN",
        "token_placeholder": "Personal Access Token Gandi LiveDNS",
        "help": "Personal Access Token (PAT) Gandi avec permission de gestion des enregistrements DNS.",
    },
    "desec": {
        "label": "deSEC.io (Gratuit)",
        "acme_hook": "dns_desec",
        "token_format": "single",
        "env_var": "DEDYN_TOKEN",
        "token_placeholder": "Token API deSEC.io",
        "help": "Token API généré sur desec.io (compatible sous-domaines gratuits .dedyn.io).",
    },
    "hetzner": {
        "label": "Hetzner DNS",
        "acme_hook": "dns_hetzner",
        "token_format": "single",
        "env_var": "HETZNER_Token",
        "token_placeholder": "Token API Hetzner DNS Console",
        "help": "Token API depuis dns.hetzner.com.",
    },
    "ovh": {
        "label": "OVH (Europe)",
        "acme_hook": "dns_ovh",
        "token_format": "ovh_triple",
        "env_var": "OVH_AK:OVH_AS:OVH_CK",
        "token_placeholder": "ApplicationKey:ApplicationSecret:ConsumerKey",
        "help": "Renseignez les 3 clés OVH séparées par ':' (AK:AS:CK) créées sur eu.api.ovh.com/createToken.",
    },
}

_acme_lock = asyncio.Lock()
_tls_operation_state: Dict[str, Any] = {
    "in_progress": False,
    "action": None,
    "last_result": None,
    "last_error": None,
    "last_run_at": None,
}


def get_certs_dir() -> str:
    """Retourne le répertoire persistant des certificats TLS."""
    custom = os.environ.get("CERTS_DIR", "").strip()
    base = custom if custom else os.path.join(DATA_DIR, "certs")
    os.makedirs(base, exist_ok=True)
    return base


def get_selfsigned_paths() -> Tuple[str, str]:
    d = os.path.join(get_certs_dir(), "selfsigned")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")


def get_letsencrypt_paths() -> Tuple[str, str]:
    d = os.path.join(get_certs_dir(), "letsencrypt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "fullchain.pem"), os.path.join(d, "privkey.pem")


def get_acme_home() -> str:
    d = os.path.join(get_certs_dir(), "acme_home")
    os.makedirs(d, exist_ok=True)
    return d


def find_openssl() -> Optional[str]:
    return shutil.which("openssl")


def find_acme_sh() -> Optional[str]:
    candidates = [
        os.environ.get("ACME_SH_PATH", "").strip(),
        "/usr/local/share/acme.sh/acme.sh",
        os.path.expanduser("~/.acme.sh/acme.sh"),
        shutil.which("acme.sh") or "",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def get_default_https_mode() -> str:
    """Détermine le mode HTTPS par défaut selon l'environnement d'exécution."""
    if os.environ.get("FORCE_HTTP", "").strip().lower() in ("1", "true", "yes", "on"):
        return "http"
    env_default = os.environ.get("HTTPS_DEFAULT_MODE", "").strip().lower()
    if env_default in VALID_HTTPS_MODES:
        return env_default
    if os.path.exists("/.dockerenv"):
        return "selfsigned"
    return "http"


def get_configured_https_mode() -> str:
    """Lit le mode HTTPS configuré en base (ou le mode par défaut)."""
    if os.environ.get("FORCE_HTTP", "").strip().lower() in ("1", "true", "yes", "on"):
        return "http"
    try:
        s = get_settings_dict()
        m = (s.get("https_mode") or "").strip().lower()
        if m in VALID_HTTPS_MODES:
            return m
    except Exception:
        pass
    return get_default_https_mode()


def get_active_runtime_tls_mode() -> str:
    """Retourne le mode TLS réellement actif sur le processus Uvicorn courant."""
    m = os.environ.get("NOBIS_ACTIVE_TLS_MODE", "").strip().lower()
    if m in VALID_HTTPS_MODES:
        return m
    return "http"


def save_tls_settings(updates: Dict[str, str]) -> None:
    """Persiste un ensemble de clés TLS dans la table système settings."""
    conn = get_sys_db()
    try:
        cur = conn.cursor()
        for k, v in updates.items():
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()
    finally:
        conn.close()


def validate_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip(".")
    if not d or not _DOMAIN_RE.match(d):
        raise ValueError("Nom de domaine invalide (ex: mon-poele.duckdns.org).")
    return d


def validate_email(email: str) -> str:
    e = (email or "").strip()
    if not e:
        return ""
    if not _EMAIL_RE.match(e):
        raise ValueError("Adresse email invalide.")
    return e


def parse_san_ips(raw: str) -> List[str]:
    ips: List[str] = []
    for part in re.split(r"[,\r\n]+", raw or ""):
        token = part.strip()
        if not token:
            continue
        try:
            ip = str(ipaddress.ip_address(token))
            if ip not in ips:
                ips.append(ip)
        except ValueError:
            raise ValueError(f"Adresse IP SAN invalide : '{token}'")
    return ips


def parse_san_dns(raw: str) -> List[str]:
    names: List[str] = []
    for part in re.split(r"[,\r\n]+", raw or ""):
        token = part.strip().lower().rstrip(".")
        if not token:
            continue
        if not _HOSTNAME_RE.match(token):
            raise ValueError(f"Nom d'hôte SAN invalide : '{token}'")
        if token not in names:
            names.append(token)
    return names


def _detect_local_ips() -> List[str]:
    ips = ["127.0.0.1", "::1"]
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0].split("%")[0]
            try:
                norm = str(ipaddress.ip_address(addr))
                if norm not in ips:
                    ips.append(norm)
            except ValueError:
                pass
    except Exception:
        pass
    return ips


def ensure_selfsigned_cert(
    extra_ips: Optional[List[str]] = None,
    extra_dns: Optional[List[str]] = None,
    force: bool = False,
) -> Tuple[bool, str]:
    """Génère (si absent ou force=True) un certificat TLS auto-signé persistant (825 jours, SAN complets)."""
    cert_path, key_path = get_selfsigned_paths()
    if not force and os.path.isfile(cert_path) and os.path.isfile(key_path):
        if os.path.getsize(cert_path) > 0 and os.path.getsize(key_path) > 0:
            return True, "Certificat auto-signé existant prêt."

    openssl_bin = find_openssl()
    if not openssl_bin:
        return False, "Binaire 'openssl' introuvable sur le système."

    dns_list: List[str] = ["localhost"]
    try:
        hn = socket.gethostname().strip().lower()
        if hn and _HOSTNAME_RE.match(hn) and hn not in dns_list:
            dns_list.append(hn)
            if "." not in hn and f"{hn}.local" not in dns_list:
                dns_list.append(f"{hn}.local")
    except Exception:
        pass

    env_dns = os.environ.get("SSL_SAN_DNS", "")
    if env_dns:
        for d in parse_san_dns(env_dns):
            if d not in dns_list:
                dns_list.append(d)
    for d in (extra_dns or []):
        if d not in dns_list:
            dns_list.append(d)

    ip_list = _detect_local_ips()
    env_ips = os.environ.get("SSL_SAN_IPS", "")
    if env_ips:
        for ip in parse_san_ips(env_ips):
            if ip not in ip_list:
                ip_list.append(ip)
    for ip in (extra_ips or []):
        if ip not in ip_list:
            ip_list.append(ip)

    san_entries = [f"DNS:{d}" for d in dns_list] + [f"IP:{ip}" for ip in ip_list]
    san_value = ",".join(san_entries)
    cn = dns_list[1] if len(dns_list) > 1 else "nobis.supervisor.local"

    tmp_cert = cert_path + ".tmp"
    tmp_key = key_path + ".tmp"

    cmd = [
        openssl_bin,
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-sha256",
        "-days",
        "825",
        "-keyout",
        tmp_key,
        "-out",
        tmp_cert,
        "-subj",
        f"/CN={cn}/O=Nobis.Supervisor",
        "-addext",
        f"subjectAltName={san_value}",
        "-addext",
        "basicConstraints=CA:FALSE",
        "-addext",
        "keyUsage=digitalSignature,keyEncipherment",
        "-addext",
        "extendedKeyUsage=serverAuth",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "Erreur openssl inconnue").strip()
            return False, f"Échec génération certificat auto-signé : {err[:300]}"
        os.replace(tmp_cert, cert_path)
        os.replace(tmp_key, key_path)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return True, f"Certificat auto-signé généré ({len(dns_list)} DNS, {len(ip_list)} IP)."
    except Exception as e:
        return False, f"Erreur lors de la génération auto-signée : {e}"
    finally:
        for p in (tmp_cert, tmp_key):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def inspect_cert(cert_path: str) -> Dict[str, Any]:
    """Inspecte un certificat PEM via OpenSSL et retourne ses métadonnées."""
    info: Dict[str, Any] = {
        "exists": False,
        "subject": "",
        "issuer": "",
        "not_before": None,
        "not_after": None,
        "days_remaining": None,
        "sans": [],
        "is_expired": False,
    }
    if not cert_path or not os.path.isfile(cert_path) or os.path.getsize(cert_path) == 0:
        return info

    info["exists"] = True
    openssl_bin = find_openssl()
    if not openssl_bin:
        return info

    try:
        proc = subprocess.run(
            [
                openssl_bin,
                "x509",
                "-in",
                cert_path,
                "-noout",
                "-subject",
                "-issuer",
                "-dates",
                "-ext",
                "subjectAltName",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return info

        out = proc.stdout or ""
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("subject="):
                info["subject"] = s.split("=", 1)[1].strip()
            elif s.startswith("issuer="):
                info["issuer"] = s.split("=", 1)[1].strip()
            elif s.startswith("notBefore="):
                info["not_before"] = s.split("=", 1)[1].strip()
            elif s.startswith("notAfter="):
                raw_after = s.split("=", 1)[1].strip()
                info["not_after"] = raw_after
                try:
                    dt = datetime.strptime(raw_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                    delta = dt - datetime.now(timezone.utc)
                    days = int(delta.total_seconds() // 86400)
                    info["days_remaining"] = days
                    info["is_expired"] = delta.total_seconds() <= 0
                except Exception:
                    pass
            elif "DNS:" in s or "IP Address:" in s:
                for token in s.split(","):
                    t = token.strip()
                    if t.startswith("DNS:") or t.startswith("IP Address:"):
                        info["sans"].append(t.replace("IP Address:", "IP:"))
    except Exception:
        pass
    return info


def _build_acme_provider_env(provider_key: str, token: str) -> Dict[str, str]:
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
) -> Tuple[bool, str]:
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
            # 1. Enregistrement du compte Let's Encrypt si email fourni (idempotent)
            if clean_email:
                reg_proc = await asyncio.create_subprocess_exec(
                    acme_bin,
                    "--register-account",
                    "--server",
                    "letsencrypt",
                    "-m",
                    clean_email,
                    "--home",
                    acme_home,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=sub_env,
                )
                await asyncio.wait_for(reg_proc.communicate(), timeout=60)

            # 2. Émission du certificat via challenge DNS-01
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

            proc = await asyncio.create_subprocess_exec(
                *issue_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sub_env,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=180)
            combined = (stdout_b.decode("utf-8", errors="replace") + "\n" + stderr_b.decode("utf-8", errors="replace")).strip()

            # acme.sh retourne 0 (succès) ou 2 (certificat déjà valide / pas encore besoin de renouveler)
            if proc.returncode not in (0, 2):
                safe_err = _sanitize_acme_output(combined, token)
                _tls_operation_state.update({"last_result": "error", "last_error": safe_err})
                return False, f"Échec validation DNS-01 Let's Encrypt : {safe_err}"

            # 3. Installation des fichiers PEM dans /app/data/certs/letsencrypt/
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
            inst_proc = await asyncio.create_subprocess_exec(
                *install_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=sub_env,
            )
            inst_out, inst_err = await asyncio.wait_for(inst_proc.communicate(), timeout=30)
            if inst_proc.returncode != 0 or not os.path.isfile(le_cert) or not os.path.isfile(le_key):
                safe_err = _sanitize_acme_output(
                    inst_out.decode("utf-8", errors="replace") + "\n" + inst_err.decode("utf-8", errors="replace"),
                    token,
                )
                _tls_operation_state.update({"last_result": "error", "last_error": safe_err})
                return False, f"Certificat émis mais échec d'installation : {safe_err}"

            try:
                os.chmod(le_key, 0o600)
            except OSError:
                pass

            msg = f"Certificat Let's Encrypt prêt pour {clean_domain}."
            _tls_operation_state.update({"last_result": "ok", "last_error": None})
            return True, msg

        except asyncio.TimeoutError:
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
        cert_info = inspect_cert(le_cert)
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
            from routers.system import _delayed_process_restart
            asyncio.create_task(_delayed_process_restart(1.0))
        else:
            db_log("ERROR", "TLS", f"Échec renouvellement automatique Let's Encrypt : {msg}", active_mode)
            add_container_log("ERROR", "TLS", f"Échec renouvellement automatique : {msg}")
    except Exception as e:
        add_container_log("ERROR", "TLS", f"Erreur tâche renouvellement TLS : {e}")


def resolve_runtime_tls() -> Tuple[str, Optional[str], Optional[str]]:
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
        # Repli sécurisé vers auto-signé si le cert LE manque
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


def get_active_cert_path() -> Optional[str]:
    """Retourne le chemin du certificat public actif (auto-signé ou Let's Encrypt), ou None en HTTP."""
    active = get_active_runtime_tls_mode()
    if active == "letsencrypt":
        p, _ = get_letsencrypt_paths()
        return p if os.path.isfile(p) else None
    if active == "selfsigned":
        p, _ = get_selfsigned_paths()
        return p if os.path.isfile(p) else None
    # Même si runtime est HTTP (ex. tests ou dev local), retourner le self-signed s'il existe
    p, _ = get_selfsigned_paths()
    return p if os.path.isfile(p) else None


def get_tls_status() -> Dict[str, Any]:
    """Retourne l'état complet TLS/HTTPS pour l'interface Paramètres (sans jamais exposer le token DNS)."""
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
        "openssl_available": bool(find_openssl()),
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
