#!/bin/sh
set -e

# Rétro-compatibilité Synology / Watchtower : si l'ancien conteneur a conservé
# l'ancien CMD ("uvicorn main:app ..."), redirige automatiquement vers server.py
# afin que la résolution HTTPS/TLS autonome soit toujours active.
if [ "$1" = "uvicorn" ] && [ "$2" = "main:app" ]; then
    shift 2
    set -- python server.py "$@"
fi

if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data
    chown -R nobis:nobis /app/data
    exec gosu nobis:nobis "$@"
fi

exec "$@"
