# DC-Gra-vt-bot AWS Lightsail 24/7 固定網址部署教學

目標：讓 Discord bot 和 dashboard 在 AWS Lightsail 上 24/7 運行，並用固定 domain/subdomain 開 dashboard，不再使用會更換的 `trycloudflare.com` URL。

## 1. 建立 Lightsail Instance

推薦：

```text
Platform: Linux/Unix
Blueprint: OS Only
OS: Ubuntu 24.04 LTS 或 Ubuntu 22.04 LTS
Plan: public IPv4 $5/month
Instance name: dc-gra-vt-bot
```

建立後，到 Lightsail：

```text
Networking -> Create static IP -> Attach to dc-gra-vt-bot
```

這個 Static IP 才是固定 IP。之後 DNS 要指向這個 IP。

不要開：

```text
Load Balancer
RDS
NAT Gateway
Container service
Extra instance
```

## 2. Domain / DNS

建立 subdomain，例如：

```text
dashboard.gra-vt.my
```

在你的 DNS provider 裏新增：

```text
Type: A
Name: dashboard
Value: Lightsail Static IP
Proxy: DNS only / 灰雲，如果用 Cloudflare
```

等 DNS 生效後可以檢查：

```bash
dig dashboard.gra-vt.my
```

回傳應該是 Lightsail Static IP。

## 3. 安裝 runtime

用 Lightsail browser SSH 進去：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git unzip curl nginx certbot python3-certbot-nginx
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

## 4. 設定 `.env`

```bash
cd ~/DC
cp .env.example .env
nano .env
```

填入：

```env
DISCORD_TOKEN=你的_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的_dashboard_密碼
SESSION_SECRET=一串很長的隨機字串
PUBLIC_FRONTEND_ORIGIN=https://dashboard.gra-vt.my
BOT_CONTROL_MODE=systemd
```

生成 `SESSION_SECRET`：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

`BOT_CONTROL_MODE=systemd` 很重要：正式 24/7 host 由 systemd 管理 bot，dashboard 的 Start/End Bot 按鈕會禁用，避免開出第二個 bot。

## 5. 本機測試

```bash
cd ~/DC
source .venv/bin/activate
python -m py_compile bot.py dashboard_api.py storage.py
python -m uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
```

另開一個 SSH tab 測：

```bash
curl http://127.0.0.1:8000/api/health
```

成功後按 `Ctrl+C` 停止 uvicorn。

## 6. 建立 dashboard systemd service

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

## 7. 建立 bot systemd service

```bash
sudo nano /etc/systemd/system/dc-gra-vt-bot.service
```

貼上：

```ini
[Unit]
Description=DC-Gra-vt-bot Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/DC
EnvironmentFile=/home/ubuntu/DC/.env
ExecStart=/home/ubuntu/DC/.venv/bin/python bot.py
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
sudo systemctl enable --now dc-gra-vt-bot
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
```

## 8. 設定 Nginx 固定網址

```bash
sudo nano /etc/nginx/sites-available/dc-gra-vt-dashboard
```

貼上，記得把 domain 改成你的：

```nginx
server {
    listen 80;
    server_name dashboard.gra-vt.my;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

啟用：

```bash
sudo ln -s /etc/nginx/sites-available/dc-gra-vt-dashboard /etc/nginx/sites-enabled/dc-gra-vt-dashboard
sudo nginx -t
sudo systemctl reload nginx
```

申請 HTTPS：

```bash
sudo certbot --nginx -d dashboard.gra-vt.my
```

Certbot 會自動設定 HTTPS 和憑證續期。

## 9. Lightsail firewall

Lightsail instance 的 Networking / Firewall 開：

```text
TCP 22   SSH
TCP 80   HTTP
TCP 443  HTTPS
```

不需要公開 `8000`，因為 Nginx 會在 VPS 內部轉發到 `127.0.0.1:8000`。

## 10. 更新 code

```bash
cd ~/DC
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart dc-gra-vt-dashboard
sudo systemctl restart dc-gra-vt-bot
```

## 11. 檢查和排錯

狀態：

```bash
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
sudo systemctl status nginx
```

Log：

```bash
sudo journalctl -u dc-gra-vt-dashboard -n 100 --no-pager
sudo journalctl -u dc-gra-vt-bot -n 100 --no-pager
sudo tail -n 100 /var/log/nginx/error.log
```

健康檢查：

```bash
curl http://127.0.0.1:8000/api/health
curl -I https://dashboard.gra-vt.my
```

資源檢查：

```bash
free -h
df -h
```

如果 service 一直重啟，先看 `journalctl`。如果是記憶體太低，可以考慮升級 Lightsail plan。

## 12. Reboot 測試

```bash
sudo reboot
```

等 1-2 分鐘後重新 SSH：

```bash
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
curl -I https://dashboard.gra-vt.my
```

成功標準：

```text
bot 自動 online
dashboard URL 不變
dashboard 可登入
兩個 service 都是 active (running)
```

## 13. 備份 SQLite

```bash
mkdir -p ~/backups
cp ~/DC/data/dc_gra_vt_bot.db ~/backups/dc_gra_vt_bot_$(date +%Y%m%d_%H%M%S).db
```

不要把 `.env` commit 到 GitHub。

## 14. Quick Tunnel 只作臨時測試

不要把 `trycloudflare.com` 當正式 dashboard URL。Quick Tunnel 每次重開可能換網址，而且手動 SSH session 斷掉後 tunnel 也可能停止。
