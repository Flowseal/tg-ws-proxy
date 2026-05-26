#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/opt/tg-ws-proxy-core}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/tg-ws-vps}"
SERVICE_NAME="${SERVICE_NAME:-tg-ws-proxy.service}"
PIP="${APP_DIR}/.venv/bin/pip"

git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true
git config --global --add safe.directory "$DEPLOY_DIR" >/dev/null 2>&1 || true

if [ -d "$DEPLOY_DIR/.git" ]; then
  git -C "$DEPLOY_DIR" fetch --prune
  git -C "$DEPLOY_DIR" pull --ff-only
fi

cd "$APP_DIR"

if [ ! -d .git ]; then
  echo "Skip update: $APP_DIR is not a git checkout"
  exit 0
fi

OLD_HEAD="$(git rev-parse HEAD)"

git fetch --prune
git pull --ff-only origin main

NEW_HEAD="$(git rev-parse HEAD)"

if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
  echo "tg-ws-proxy is already up to date at $NEW_HEAD"
  exit 0
fi

if [ -x "$PIP" ]; then
  "$PIP" install cryptography==46.0.5
fi

systemctl restart "$SERVICE_NAME"

echo "tg-ws-proxy updated: $OLD_HEAD -> $NEW_HEAD"
