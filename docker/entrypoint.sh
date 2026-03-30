#!/bin/sh
set -eu
args="--host ${TG_WS_PROXY_HOST} --port ${TG_WS_PROXY_PORT}"
for dc in ${TG_WS_PROXY_DC_IPS}; do
  args="$args --dc-ip $dc"
done
exec python -u proxy/tg_ws_proxy.py $args "$@"
