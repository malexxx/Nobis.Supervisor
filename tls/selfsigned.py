"""Génération OpenSSL de certificats TLS auto-signés (ECDSA P-256) et inspection X.509."""
from datetime import datetime, UTC
import logging
import os
import socket
import subprocess
from typing import Any

from tls.config import (
    _HOSTNAME_RE,
    _detect_local_ips,
    get_selfsigned_paths,
    parse_san_dns,
    parse_san_ips,
)

logger = logging.getLogger("nobis.tls.selfsigned")


def _resolve_openssl() -> str | None:
    import tls_service as _tls_facade

    return _tls_facade.find_openssl()


def _append_unique(target: list[str], values: list[str]) -> None:
    for item in values:
        if item not in target:
            target.append(item)


def _collect_selfsigned_sans(
    extra_ips: list[str] | None,
    extra_dns: list[str] | None,
) -> tuple[list[str], list[str]]:
    dns_list: list[str] = ["localhost"]
    try:
        hn = socket.gethostname().strip().lower()
        if hn and _HOSTNAME_RE.match(hn):
            _append_unique(dns_list, [hn] + ([f"{hn}.local"] if "." not in hn else []))
    except Exception as exc:
        logger.debug("Résolution hostname SAN ignorée : %s", exc)

    env_dns = os.environ.get("SSL_SAN_DNS", "")
    if env_dns:
        _append_unique(dns_list, parse_san_dns(env_dns))
    _append_unique(dns_list, list(extra_dns or []))

    ip_list = _detect_local_ips()
    env_ips = os.environ.get("SSL_SAN_IPS", "")
    if env_ips:
        _append_unique(ip_list, parse_san_ips(env_ips))
    _append_unique(ip_list, list(extra_ips or []))
    return dns_list, ip_list


def _build_selfsigned_openssl_cmd(
    openssl_bin: str,
    tmp_key: str,
    tmp_cert: str,
    cn: str,
    san_value: str,
) -> list[str]:
    return [
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


def ensure_selfsigned_cert(
    extra_ips: list[str] | None = None,
    extra_dns: list[str] | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    """Génère (si absent ou force=True) un certificat TLS auto-signé persistant (825 jours, SAN complets)."""
    cert_path, key_path = get_selfsigned_paths()
    if (
        not force
        and os.path.isfile(cert_path)
        and os.path.isfile(key_path)
        and os.path.getsize(cert_path) > 0
        and os.path.getsize(key_path) > 0
    ):
        return True, "Certificat auto-signé existant prêt."

    openssl_bin = _resolve_openssl()
    if not openssl_bin:
        return False, "Binaire 'openssl' introuvable sur le système."

    dns_list, ip_list = _collect_selfsigned_sans(extra_ips, extra_dns)
    san_value = ",".join([f"DNS:{d}" for d in dns_list] + [f"IP:{ip}" for ip in ip_list])
    cn = dns_list[1] if len(dns_list) > 1 else "nobis.supervisor.local"

    tmp_cert = cert_path + ".tmp"
    tmp_key = key_path + ".tmp"
    cmd = _build_selfsigned_openssl_cmd(openssl_bin, tmp_key, tmp_cert, cn, san_value)

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


def _parse_not_after(raw_after: str, info: dict[str, Any]) -> None:
    info["not_after"] = raw_after
    try:
        dt = datetime.strptime(raw_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        delta = dt - datetime.now(UTC)
        info["days_remaining"] = int(delta.total_seconds() // 86400)
        info["is_expired"] = delta.total_seconds() <= 0
    except Exception as exc:
        logger.debug("Parsing date notAfter X.509 ignoré (%s) : %s", raw_after, exc)


def _parse_x509_output(out: str, info: dict[str, Any]) -> None:
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("subject="):
            info["subject"] = s.split("=", 1)[1].strip()
        elif s.startswith("issuer="):
            info["issuer"] = s.split("=", 1)[1].strip()
        elif s.startswith("notBefore="):
            info["not_before"] = s.split("=", 1)[1].strip()
        elif s.startswith("notAfter="):
            _parse_not_after(s.split("=", 1)[1].strip(), info)
        elif "DNS:" in s or "IP Address:" in s:
            for token in s.split(","):
                t = token.strip()
                if t.startswith(("DNS:", "IP Address:")):
                    info["sans"].append(t.replace("IP Address:", "IP:"))


def inspect_cert(cert_path: str) -> dict[str, Any]:
    """Inspecte un certificat PEM via OpenSSL et retourne ses métadonnées."""
    info: dict[str, Any] = {
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
    openssl_bin = _resolve_openssl()
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
        if proc.returncode == 0:
            _parse_x509_output(proc.stdout or "", info)
    except Exception as exc:
        logger.debug("Inspection X.509 échouée pour %s : %s", cert_path, exc)
    return info
