#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data
    chown -R nobis:nobis /app/data
    exec gosu nobis:nobis "$@"
fi

exec "$@"
