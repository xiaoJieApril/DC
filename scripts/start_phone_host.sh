#!/data/data/com.termux/files/usr/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f ".env" ]; then
  echo ".env not found. Copy .env.example to .env and fill it first."
  exit 1
fi

mkdir -p logs

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python"
fi

if [ -f "logs/dashboard.pid" ] && kill "$(cat logs/dashboard.pid)" 2>/dev/null; then
  echo "Dashboard is already running. PID: $(cat logs/dashboard.pid)"
  echo "Open on phone: http://127.0.0.1:8000"
  exit 0
fi

echo "Starting DC-Gra-vt-bot dashboard on phone..."
"$PYTHON" -m uvicorn dashboard_api:app --host 0.0.0.0 --port 8000 > logs/dashboard.out.log 2> logs/dashboard.err.log &
echo $! > logs/dashboard.pid

sleep 3
if ! kill "$(cat logs/dashboard.pid)" 2>/dev/null; then
  echo "Dashboard failed to start. Last error:"
  tail -n 80 logs/dashboard.err.log
  rm -f logs/dashboard.pid
  exit 1
fi

echo "Started."
echo "Dashboard PID: $(cat logs/dashboard.pid)"
echo "Open on phone: http://127.0.0.1:8000"
echo "Open from same Wi-Fi: http://PHONE_LAN_IP:8000"
echo "Then login to dashboard -> Overview -> Start Bot."
echo "If it crashes, run: tail -n 80 logs/dashboard.err.log"
