"""Configuration TLS, catalogue fournisseurs DNS-01, chemins certificats et validateurs anti-injection."""
import ipaddress
import logging
import os
import re
import shutil
import socket
from typing import Any

from config import DATA_DIR
from database import get_settings_dict, get_sys_db

logger = logging.getLogger("nobis.tls.config")

VALID_HTTPS_MODES = ("http", "selfsigned", "letsencrypt")

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

DNS_PROVIDERS: dict[str, dict[str, Any]] = {
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


def get_certs_dir() -> str:
    """Retourne le répertoire persistant des certificats TLS."""
    custom = os.environ.get("CERTS_DIR", "").strip()
    base = custom if custom else os.path.join(DATA_DIR, "certs")
    os.makedirs(base, exist_ok=True)
    return base


def get_selfsigned_paths() -> tuple[str, str]:
    """Retourne les chemins (cert.pem, key.pem) du certificat TLS auto-signé."""
    d = os.path.join(get_certs_dir(), "selfsigned")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")


def get_letsencrypt_paths() -> tuple[str, str]:
    """Retourne les chemins (fullchain.pem, privkey.pem) du certificat Let's Encrypt."""
    d = os.path.join(get_certs_dir(), "letsencrypt")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "fullchain.pem"), os.path.join(d, "privkey.pem")


def get_acme_home() -> str:
    """Retourne le répertoire de travail persistant pour acme.sh."""
    d = os.path.join(get_certs_dir(), "acme_home")
    os.makedirs(d, exist_ok=True)
    return d


def find_openssl() -> str | None:
    """Localise le binaire système openssl dans le PATH."""
    return shutil.which("openssl")


def find_acme_sh() -> str | None:
    """Localise le script acme.sh dans les emplacements standards ou via ACME_SH_PATH."""
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
    except Exception as exc:
        logger.debug("Lecture https_mode en base ignorée : %s", exc)
    return get_default_https_mode()


def get_active_runtime_tls_mode() -> str:
    """Retourne le mode TLS réellement actif sur le processus Uvicorn courant."""
    m = os.environ.get("NOBIS_ACTIVE_TLS_MODE", "").strip().lower()
    if m in VALID_HTTPS_MODES:
        return m
    return "http"


def save_tls_settings(updates: dict[str, str]) -> None:
    """Persiste un ensemble de clés TLS dans la table système settings (avec chiffrement des secrets)."""
    from database.secrets import encrypt_setting_value

    conn = get_sys_db()
    try:
        cur = conn.cursor()
        for k, v in updates.items():
            cur.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (k, encrypt_setting_value(str(k), str(v))),
            )
        conn.commit()
    finally:
        conn.close()


def validate_domain(domain: str) -> str:
    """Valide et normalise un nom de domaine FQDN (anti-injection CLI acme.sh)."""
    d = (domain or "").strip().lower().rstrip(".")
    if not d or not _DOMAIN_RE.match(d):
        raise ValueError("Nom de domaine invalide (ex: mon-poele.duckdns.org).")
    return d


def validate_email(email: str) -> str:
    """Valide une adresse email optionnelle pour l'enregistrement de compte ACME."""
    e = (email or "").strip()
    if not e:
        return ""
    if not _EMAIL_RE.match(e):
        raise ValueError("Adresse email invalide.")
    return e


def parse_san_ips(raw: str) -> list[str]:
    """Extrait et valide une liste d'adresses IPv4/IPv6 pour les extensions SAN."""
    ips: list[str] = []
    for part in re.split(r"[,\r\n]+", raw or ""):
        token = part.strip()
        if not token:
            continue
        try:
            ip = str(ipaddress.ip_address(token))
            if ip not in ips:
                ips.append(ip)
        except ValueError as err:
            raise ValueError(f"Adresse IP SAN invalide : '{token}'") from err
    return ips


def parse_san_dns(raw: str) -> list[str]:
    """Extrait et valide une liste de noms d'hôtes DNS pour les extensions SAN."""
    names: list[str] = []
    for part in re.split(r"[,\r\n]+", raw or ""):
        token = part.strip().lower().rstrip(".")
        if not token:
            continue
        if not _HOSTNAME_RE.match(token):
            raise ValueError(f"Nom d'hôte SAN invalide : '{token}'")
        if token not in names:
            names.append(token)
    return names


def _detect_local_ips() -> list[str]:
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
    except Exception as exc:
        logger.debug("Détection IPs locales ignorée : %s", exc)
    return ips
