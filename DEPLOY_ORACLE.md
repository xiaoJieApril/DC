# DC-Gra-vt-bot Oracle Cloud Free VM Deployment

This guide runs the Discord bot and dashboard API on an Oracle Cloud Always Free VM. The dashboard frontend can be uploaded to Cloudflare Pages as `dc-gra-vt-bot`.

## 1. Create The VM

Use an Ubuntu VM on Oracle Cloud Free Tier. Keep the VM public SSH key safe, then SSH into the VM.

Open inbound ports:

- `22` for SSH
- `8000` temporarily for dashboard API testing
- `80` and `443` later if you add Caddy/Nginx/Cloudflare Tunnel

## 2. Install Runtime

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
sudo mkdir -p /opt/dc-gra-vt-bot
sudo chown ubuntu:ubuntu /opt/dc-gra-vt-bot
```

Copy or clone this project into:

```bash
/opt/dc-gra-vt-bot
```

Then install dependencies:

```bash
cd /opt/dc-gra-vt-bot
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 3. Create `.env`

Create `/opt/dc-gra-vt-bot/.env`:

```env
DISCORD_TOKEN=your_rotated_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_this_password
SESSION_SECRET=change_this_to_a_long_random_string
PUBLIC_FRONTEND_ORIGIN=https://dc-gra-vt-bot.pages.dev
```

Rotate the Discord token in the Discord Developer Portal if it was ever exposed locally.

## 4. Test Manually

```bash
cd /opt/dc-gra-vt-bot
. .venv/bin/activate
python -m py_compile bot.py gui.py storage.py dashboard_api.py
python bot.py
```

In a second SSH session:

```bash
cd /opt/dc-gra-vt-bot
. .venv/bin/activate
uvicorn dashboard_api:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://YOUR_VM_PUBLIC_IP:8000/api/health
```

## 5. Enable 24/7 Services

```bash
sudo cp deploy/dc-gra-vt-bot.service /etc/systemd/system/dc-gra-vt-bot.service
sudo cp deploy/dc-gra-vt-dashboard.service /etc/systemd/system/dc-gra-vt-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now dc-gra-vt-bot
sudo systemctl enable --now dc-gra-vt-dashboard
```

Check logs:

```bash
sudo journalctl -u dc-gra-vt-bot -f
sudo journalctl -u dc-gra-vt-dashboard -f
```

Restart after updates:

```bash
sudo systemctl restart dc-gra-vt-bot
sudo systemctl restart dc-gra-vt-dashboard
```

## 6. Cloudflare Pages Frontend

Upload the `frontend/` folder to Cloudflare Pages. Use:

```text
Project name: dc-gra-vt-bot
Build command: none
Output directory: frontend
```

Before uploading, edit `frontend/config.js`:

```js
window.DASHBOARD_API_BASE = "https://your-api-domain-or-tunnel-url";
```

If you test directly against the VM first, use:

```js
window.DASHBOARD_API_BASE = "http://YOUR_VM_PUBLIC_IP:8000";
```

For production, prefer HTTPS through Caddy, Nginx, or Cloudflare Tunnel.

## 7. Old Phone Role

The old phone does not need to stay powered on for the bot to run. Use it only for:

- opening the dashboard
- SSH/Termius emergency access
- checking Discord bot status
- receiving uptime monitor alerts

The bot keeps running as long as the Oracle VM is online.
