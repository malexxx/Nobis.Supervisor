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
from tls_service import resolve_runtime_tls

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("nobis.server")


def _silence_windows_proactor_reset() -> None:
    """Ignore le bruit bénin WinError 10054 lors de la fermeture de sockets TLS sous Windows (ProactorEventLoop)."""
    if sys.platform != "win32":
        return
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport

        orig = _ProactorBasePipeTransport._call_connection_lost

        def _safe_call_connection_lost(self, exc):
            try:
                orig(self, exc)
            except ConnectionResetError:
                pass

        _ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost
    except Exception:
        pass


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lanceur Nobis.Supervisor (HTTP/HTTPS autonome)")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--proxy-headers", action="store_true", default=True)
    parser.add_argument("--forwarded-allow-ips", default="*")
    parser.add_argument("--timeout-graceful-shutdown", type=int, default=5)
    args, _ = parser.parse_known_args(argv)
    return args


def main() -> None:
    _silence_windows_proactor_reset()
    args = parse_args()
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
