#!/usr/bin/env bash
set -u

APP_DIR="${APP_DIR:-/home/ubuntu/DC}"
DASHBOARD_SERVICE="${DASHBOARD_SERVICE:-dc-gra-vt-dashboard}"
BOT_SERVICE="${BOT_SERVICE:-dc-gra-vt-bot}"

section() {
  printf '\n== %s ==\n' "$1"
}

run() {
  printf '$ %s\n' "$*"
  "$@" 2>&1 || true
}

section "Host"
run hostnamectl
run date
run uptime
run free -h
run swapon --show
run df -h "$APP_DIR"
run df -ih "$APP_DIR"

section "SSH health"
run systemctl status ssh --no-pager
run journalctl -u ssh -n 120 --no-pager
run tail -n 120 /var/log/auth.log

section "App files"
run ls -ld "$APP_DIR" "$APP_DIR/.venv" "$APP_DIR/.env" "$APP_DIR/data" "$APP_DIR/logs"
run test -x "$APP_DIR/.venv/bin/python"
run test -x "$APP_DIR/.venv/bin/uvicorn"

section "Python import check"
if [ -x "$APP_DIR/.venv/bin/python" ]; then
  (
    cd "$APP_DIR" &&
    "$APP_DIR/.venv/bin/python" -m py_compile bot.py dashboard_api.py storage.py
  ) 2>&1 || true
fi

section "Environment check"
if [ -f "$APP_DIR/.env" ]; then
  grep -E '^(BOT_CONTROL_MODE|SYSTEMD_BOT_SERVICE|PUBLIC_FRONTEND_ORIGIN)=' "$APP_DIR/.env" || true
  if grep -q '^DISCORD_TOKEN=.' "$APP_DIR/.env"; then
    echo "DISCORD_TOKEN is present"
  else
    echo "DISCORD_TOKEN is missing or empty"
  fi
  if grep -q '^SESSION_SECRET=.' "$APP_DIR/.env"; then
    echo "SESSION_SECRET is present"
  else
    echo "SESSION_SECRET is missing or empty"
  fi
else
  echo "$APP_DIR/.env is missing"
fi

section "Systemd status"
run systemctl status "$DASHBOARD_SERVICE" --no-pager
run systemctl status "$BOT_SERVICE" --no-pager
run systemctl show "$DASHBOARD_SERVICE" -p User -p WorkingDirectory -p ExecStart -p Restart -p NRestarts -p Result -p MainPID --no-pager
run systemctl show "$BOT_SERVICE" -p User -p WorkingDirectory -p ExecStart -p Restart -p NRestarts -p Result -p MainPID --no-pager

section "Recent dashboard logs"
run journalctl -u "$DASHBOARD_SERVICE" -n 120 --no-pager

section "Recent bot logs"
run journalctl -u "$BOT_SERVICE" -n 160 --no-pager

section "Kernel and system warnings"
run journalctl -k -n 200 --no-pager
run journalctl -p warning..alert -n 200 --no-pager
run dmesg -T

section "Port and health"
run ss -ltnp
run curl -fsS http://127.0.0.1:8000/api/health

section "Nginx"
run systemctl status nginx --no-pager
run nginx -t
run tail -n 80 /var/log/nginx/error.log
