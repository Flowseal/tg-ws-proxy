#!/bin/sh
set -eu

HOST="${TG_WS_PROXY_HOST:-0.0.0.0}"
PORT="${TG_WS_PROXY_PORT:-1443}"
SECRET="${TG_WS_PROXY_SECRET:?TG_WS_PROXY_SECRET must be set}"
DC_IPS="${TG_WS_PROXY_DC_IPS:-2:149.154.167.220 4:149.154.167.220}"
APP_DIR="${APP_DIR:-/opt/tg-ws-proxy-core}"

set -- --host "$HOST" --port "$PORT" --secret "$SECRET"

for dc in $DC_IPS; do
  set -- "$@" --dc-ip "$dc"
done

cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/python" -u proxy/tg_ws_proxy.py "$@"
