"""
Point d'entrée serveur Uvicorn pour Nobis.Supervisor (conteneur Docker & lancement autonome).
Initialise les bases SQLite, résout le mode TLS actif (auto-signé par défaut en Docker,
Let's Encrypt si configuré, ou HTTP), puis démarre Uvicorn sur le port configuré.
"""
import argparse
import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import uvicorn

from database import init_all_databases
from services.lifecycle import install_windows_proactor_guard
from tls_service import resolve_runtime_tls

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("nobis.server")


def parse_args(argv=None) -> argparse.Namespace:
    """Analyse les arguments CLI et variables d'environnement du serveur Uvicorn."""
    parser = argparse.ArgumentParser(description="Lanceur Nobis.Supervisor (HTTP/HTTPS autonome)")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--proxy-headers", action="store_true", default=True)
    parser.add_argument(
        "--forwarded-allow-ips",
        default=os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1"),
    )
    parser.add_argument("--timeout-graceful-shutdown", type=int, default=5)
    args, _ = parser.parse_known_args(argv)
    return args


def main() -> None:
    """Initialise les bases, résout la configuration TLS active et démarre Uvicorn."""
    install_windows_proactor_guard()
    args = parse_args()
    os.environ["NOBIS_RUNTIME_PORT"] = str(args.port)
    init_all_databases()
    tls_mode, certfile, keyfile = resolve_runtime_tls()

    kwargs = {
        "host": args.host,
        "port": args.port,
        "workers": args.workers,
        "proxy_headers": args.proxy_headers,
        "forwarded_allow_ips": args.forwarded_allow_ips,
        "timeout_graceful_shutdown": args.timeout_graceful_shutdown,
    }

    if tls_mode in ("selfsigned", "letsencrypt") and certfile and keyfile:
        kwargs["ssl_certfile"] = certfile
        kwargs["ssl_keyfile"] = keyfile
        logger.info("Démarrage Nobis.Supervisor en HTTPS (%s) sur https://%s:%d", tls_mode, args.host, args.port)
    else:
        logger.info("Démarrage Nobis.Supervisor en HTTP sur http://%s:%d", args.host, args.port)

    uvicorn.run("main:app", **kwargs)


if __name__ == "__main__":
    main()
