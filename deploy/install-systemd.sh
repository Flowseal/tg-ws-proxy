#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/opt/tg-ws-proxy-core}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/tg-ws-vps}"
APP_USER="${APP_USER:-tgwsproxy}"
ENV_FILE="${ENV_FILE:-/etc/tg-ws-proxy.env}"
SERVICE_FILE="/etc/systemd/system/tg-ws-proxy.service"
CORE_REPO="${CORE_REPO:-https://github.com/Flowseal/tg-ws-proxy.git}"
CORE_BRANCH="${CORE_BRANCH:-main}"
PUBLIC_HOST="${TG_WS_PROXY_PUBLIC_HOST:-${PUBLIC_HOST:-}}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo sh deploy/install-systemd.sh" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Install it first, for example: apt install python3 python3-venv" >&2
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is required. Install it first, for example: apt install python3-venv" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required. Install it first, for example: apt install git" >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

run_as_app_user() {
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$APP_USER" -- "$@"
  else
    su -s /bin/sh "$APP_USER" -c "$(printf '%s ' "$@")"
  fi
}

SRC_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

mkdir -p "$DEPLOY_DIR"
if [ "$SRC_DIR" != "$DEPLOY_DIR" ]; then
  cp -R "$SRC_DIR"/. "$DEPLOY_DIR"/
fi

git config --global --add safe.directory "$APP_DIR" >/dev/null 2>&1 || true
git config --global --add safe.directory "$DEPLOY_DIR" >/dev/null 2>&1 || true

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --prune origin
  git -C "$APP_DIR" checkout "$CORE_BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$CORE_BRANCH"
else
  rm -rf "$APP_DIR"
  git clone --branch "$CORE_BRANCH" "$CORE_REPO" "$APP_DIR"
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chown -R root:root "$DEPLOY_DIR"

if [ ! -d "$APP_DIR/.venv" ]; then
  run_as_app_user python3 -m venv "$APP_DIR/.venv"
fi

run_as_app_user "$APP_DIR/.venv/bin/pip" install --upgrade pip
run_as_app_user "$APP_DIR/.venv/bin/pip" install cryptography==46.0.5

if [ ! -f "$ENV_FILE" ]; then
  cp "$DEPLOY_DIR/deploy/systemd/tg-ws-proxy.env.example" "$ENV_FILE"
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 16)"
  else
    SECRET="$(python3 -c 'import os; print(os.urandom(16).hex())')"
  fi
  sed -i "s/change_me_to_32_hex_chars/$SECRET/" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

SECRET="$(sed -n 's/^TG_WS_PROXY_SECRET=//p' "$ENV_FILE" | tail -n 1)"
if ! printf '%s' "$SECRET" | grep -Eq '^[0-9a-fA-F]{32}$'; then
  if command -v openssl >/dev/null 2>&1; then
    SECRET="$(openssl rand -hex 16)"
  else
    SECRET="$(python3 -c 'import os; print(os.urandom(16).hex())')"
  fi
  if grep -q '^TG_WS_PROXY_SECRET=' "$ENV_FILE"; then
    sed -i "s/^TG_WS_PROXY_SECRET=.*/TG_WS_PROXY_SECRET=$SECRET/" "$ENV_FILE"
  else
    printf '\nTG_WS_PROXY_SECRET=%s\n' "$SECRET" >> "$ENV_FILE"
  fi
fi

PORT="$(sed -n 's/^TG_WS_PROXY_PORT=//p' "$ENV_FILE" | tail -n 1)"
PORT="${PORT:-1443}"

chmod +x "$DEPLOY_DIR/deploy/systemd/run-headless.sh"
chmod +x "$DEPLOY_DIR/deploy/systemd/update-headless.sh"
cp "$DEPLOY_DIR/deploy/systemd/tg-ws-proxy.service" "$SERVICE_FILE"
cp "$DEPLOY_DIR/deploy/systemd/tg-ws-proxy-update.service" /etc/systemd/system/tg-ws-proxy-update.service
cp "$DEPLOY_DIR/deploy/systemd/tg-ws-proxy-update.timer" /etc/systemd/system/tg-ws-proxy-update.timer

systemctl daemon-reload
systemctl enable --now tg-ws-proxy
systemctl enable --now tg-ws-proxy-update.timer

if [ -z "$PUBLIC_HOST" ]; then
  if command -v curl >/dev/null 2>&1; then
    PUBLIC_HOST="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  fi
fi

if [ -z "$PUBLIC_HOST" ]; then
  PUBLIC_HOST="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi

echo "tg-ws-proxy installed and started."
echo "Core directory: $APP_DIR"
echo "Deploy directory: $DEPLOY_DIR"
echo "Show logs with: journalctl -u tg-ws-proxy -n 80 --no-pager"
echo "Auto-update timer: systemctl list-timers tg-ws-proxy-update.timer"
if [ -n "$PUBLIC_HOST" ]; then
  echo
  echo "Telegram proxy link:"
  echo "tg://proxy?server=$PUBLIC_HOST&port=$PORT&secret=dd$SECRET"
else
  echo
  echo "Could not detect public host automatically."
  echo "Run again with: sudo TG_WS_PROXY_PUBLIC_HOST=YOUR_VPS_IP sh deploy/install-systemd.sh"
fi
