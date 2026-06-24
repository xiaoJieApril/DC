#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUN_USER="${RUN_USER:-$(stat -c '%U' "$APP_DIR")}"
DASHBOARD_SERVICE="${DASHBOARD_SERVICE:-dc-gra-vt-dashboard}"
BOT_SERVICE="${BOT_SERVICE:-dc-gra-vt-bot}"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "Missing $APP_DIR/.env. Copy .env.example to .env and fill it first." >&2
  exit 1
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "Missing $APP_DIR/.venv/bin/python. Create the venv and install requirements first." >&2
  exit 1
fi

if [ ! -x "$APP_DIR/.venv/bin/uvicorn" ]; then
  echo "Missing $APP_DIR/.venv/bin/uvicorn. Run: source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/$DASHBOARD_SERVICE.service" <<SERVICE
[Unit]
Description=DC-Gra-vt-bot Dashboard API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -f $APP_DIR/.env
ExecStartPre=/usr/bin/test -x $APP_DIR/.venv/bin/uvicorn
ExecStart=$APP_DIR/.venv/bin/uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=8
User=$RUN_USER

[Install]
WantedBy=multi-user.target
SERVICE

cat > "$TMP_DIR/$BOT_SERVICE.service" <<SERVICE
[Unit]
Description=DC-Gra-vt-bot Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -f $APP_DIR/.env
ExecStartPre=/usr/bin/test -x $APP_DIR/.venv/bin/python
ExecStart=$APP_DIR/.venv/bin/python -u bot.py
Restart=on-failure
RestartSec=8
User=$RUN_USER

[Install]
WantedBy=multi-user.target
SERVICE

echo "Installing services with APP_DIR=$APP_DIR and User=$RUN_USER"
sudo cp "$TMP_DIR/$DASHBOARD_SERVICE.service" "/etc/systemd/system/$DASHBOARD_SERVICE.service"
sudo cp "$TMP_DIR/$BOT_SERVICE.service" "/etc/systemd/system/$BOT_SERVICE.service"
sudo systemctl daemon-reload
sudo systemctl enable "$DASHBOARD_SERVICE" "$BOT_SERVICE"
sudo systemctl restart "$DASHBOARD_SERVICE" "$BOT_SERVICE" || true
sudo systemctl --no-pager --full status "$DASHBOARD_SERVICE" "$BOT_SERVICE" || true

if ! systemctl is-active --quiet "$DASHBOARD_SERVICE" || ! systemctl is-active --quiet "$BOT_SERVICE"; then
  echo
  echo "One or both services are not active. Recent logs:"
  sudo journalctl -u "$DASHBOARD_SERVICE" -n 80 --no-pager || true
  sudo journalctl -u "$BOT_SERVICE" -n 120 --no-pager || true
  exit 1
fi

echo "Both services are active."
