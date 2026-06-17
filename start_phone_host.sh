#!/data/data/com.termux/files/usr/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -f ".env" ]; then
  echo ".env not found. Copy .env.example to .env and fill it first."
  exit 1
fi

mkdir -p logs

echo "Starting DC-Gra-vt-bot on phone..."
python bot.py > logs/bot.out.log 2> logs/bot.err.log &
echo $! > logs/bot.pid

python -m uvicorn dashboard_api:app --host 0.0.0.0 --port 8000 > logs/dashboard.out.log 2> logs/dashboard.err.log &
echo $! > logs/dashboard.pid

echo "Started."
echo "Bot PID: $(cat logs/bot.pid)"
echo "Dashboard PID: $(cat logs/dashboard.pid)"
echo "Open on phone: http://127.0.0.1:8000"
echo "Open from same Wi-Fi: http://PHONE_LAN_IP:8000"
