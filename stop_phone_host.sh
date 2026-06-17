#!/data/data/com.termux/files/usr/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

stop_pid_file() {
  local file="$1"
  local name="$2"
  if [ -f "$file" ]; then
    local pid
    pid="$(cat "$file")"
    if kill "$pid" 2>/dev/null; then
      echo "Stopped $name PID $pid"
    else
      echo "$name PID $pid was not running"
    fi
    rm -f "$file"
  else
    echo "No $name pid file found"
  fi
}

stop_pid_file logs/bot.pid "bot"
stop_pid_file logs/dashboard.pid "dashboard"
