# DC-Gra-vt-bot AWS Lightsail 備用部署教學

目前主線是 Exabytes VPS。這份只作備用：如果 Exabytes 暫時不能用，可以把同一套 dashboard-managed bot 放到 AWS Lightsail。

## 1. 建立 Lightsail Instance

推薦最簡單方案：

```text
Platform: Linux/Unix
Blueprint: OS Only
OS: Ubuntu 24.04 LTS 或 Ubuntu 22.04 LTS
Plan: public IPv4 $5/month
Instance name: dc-gra-vt-bot
```

便宜方案可以用 IPv6-only $3.50/month，但 SSH / dashboard 公開會比較麻煩。

不要開：

```text
Load Balancer
RDS
NAT Gateway
Container service
Extra instance
```

## 2. 安裝 runtime

用 Lightsail browser SSH 進去：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git unzip curl
```

Clone repo：

```bash
cd ~
git clone https://github.com/xiaoJieApril/DC.git
cd DC
```

如果 repo 是 private，GitHub password 要用 Personal Access Token。

安裝 Python packages：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. 設定 `.env`

```bash
cp .env.example .env
nano .env
```

填入：

```env
DISCORD_TOKEN=你的_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的_dashboard_密碼
SESSION_SECRET=一串很長的隨機字串
PUBLIC_FRONTEND_ORIGIN=http://127.0.0.1:8000
```

生成 `SESSION_SECRET`：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. 測試 dashboard

```bash
source .venv/bin/activate
python -m py_compile bot.py dashboard_api.py storage.py
python -m uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
```

看到 server started 後按 `Ctrl+C`。

## 5. 建立 dashboard systemd service

只需要讓 dashboard API 自動開機啟動。bot 由 dashboard 的 Overview 頁面按 `Start Bot` / `End Bot` 控制。

```bash
sudo nano /etc/systemd/system/dc-gra-vt-dashboard.service
```

貼上：

```ini
[Unit]
Description=DC-Gra-vt-bot Dashboard API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/DC
EnvironmentFile=/home/ubuntu/DC/.env
ExecStart=/home/ubuntu/DC/.venv/bin/uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=8
User=ubuntu

[Install]
WantedBy=multi-user.target
```

啟動：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-dashboard
```

## 6. 公開 dashboard

最簡單用 Cloudflare Quick Tunnel：

```bash
cd /tmp
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
cloudflared tunnel --url http://127.0.0.1:8000
```

它會顯示：

```text
https://xxxx.trycloudflare.com
```

打開 dashboard，登入後到 Overview：

```text
Start Bot = 啟動 Discord bot
End Bot = 停止 Discord bot
```

Quick Tunnel URL 可能會變。固定網址要用 Cloudflare named tunnel + domain。

## 7. 更新 code

```bash
cd ~/DC
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart dc-gra-vt-dashboard
```

Dashboard 重啟後，如需要 bot online，重新在 Overview 按 `Start Bot`。

## 8. 備份 SQLite

```bash
mkdir -p ~/backups
cp ~/DC/data/dc_gra_vt_bot.db ~/backups/dc_gra_vt_bot_$(date +%Y%m%d_%H%M%S).db
```

不要把 `.env` commit 到 GitHub。
