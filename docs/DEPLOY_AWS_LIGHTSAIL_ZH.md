# DC-Gra-vt-bot AWS Lightsail 24/7 部署教學

目標：讓 Discord bot 和 dashboard 完全在 AWS Lightsail 上 24/7 運行，不使用 domain name。dashboard 固定入口使用 Lightsail Static IP，例如：

```text
http://你的_Lightsail_Static_IP
```

注意：沒有 domain name 時，不能申請正常可信任的 HTTPS certificate。這個方案是固定 IP + HTTP。若之後要 HTTPS，才需要 domain/subdomain + Certbot。

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

這個 Static IP 才是固定 IP。之後 dashboard 就用這個 IP 開。

不要開：

```text
Load Balancer
RDS
NAT Gateway
Container service
Extra instance
```

## 2. 固定 Dashboard URL

不使用 domain name 時，不需要 DNS provider。你的固定 dashboard URL 會是：

```text
http://Lightsail_Static_IP
```

例如 Static IP 是 `1.2.3.4`：

```text
http://1.2.3.4
```

這個 URL 不會亂換，前提是你已經把 Static IP attach 到 Lightsail instance。

## 3. 安裝 runtime

用 Lightsail browser SSH 進去：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git unzip curl nginx
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
PUBLIC_FRONTEND_ORIGIN=http://你的_Lightsail_Static_IP
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
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -f /home/ubuntu/DC/.env
ExecStartPre=/usr/bin/test -x /home/ubuntu/DC/.venv/bin/uvicorn
ExecStart=/home/ubuntu/DC/.venv/bin/uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
Restart=on-failure
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
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/test -f /home/ubuntu/DC/.env
ExecStartPre=/usr/bin/test -x /home/ubuntu/DC/.venv/bin/python
ExecStart=/home/ubuntu/DC/.venv/bin/python -u bot.py
Restart=on-failure
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

如果你是從 repo 更新部署，也可以用腳本自動安裝/覆蓋兩個 service，避免 `/etc/systemd/system/` 還留著舊路徑：

```bash
cd ~/DC
bash scripts/install_lightsail_services.sh
```

## 8. 設定 Nginx 固定 IP 入口

```bash
sudo nano /etc/nginx/sites-available/dc-gra-vt-dashboard
```

貼上：

```nginx
server {
    listen 80;
    server_name _;

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

之後 dashboard 就是：

```text
http://你的_Lightsail_Static_IP
```

如果之後買 domain，再改成 domain + Certbot HTTPS。

## 9. Lightsail firewall

Lightsail instance 的 Networking / Firewall 開：

```text
TCP 22   SSH
TCP 80   HTTP
```

不使用 domain/HTTPS 時不需要開 `443`。也不需要公開 `8000`，因為 Nginx 會在 VPS 內部轉發到 `127.0.0.1:8000`。

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
curl -I http://你的_Lightsail_Static_IP
```

資源檢查：

```bash
free -h
df -h
```

如果 service 一直重啟，先看 `journalctl`。如果是記憶體太低，可以考慮升級 Lightsail plan。

也可以直接跑 repo 內的診斷腳本，會一次列出 service 狀態、最後 log、`.env`、venv、port、Nginx、記憶體和硬碟狀態：

```bash
cd ~/DC
bash scripts/diagnose_lightsail.sh
```

常見關掉原因：

- service 裏的 `WorkingDirectory` / `EnvironmentFile` 指到錯路徑，例如 repo 在 `/home/ubuntu/DC`，但 service 還在找 `/opt/dc-gra-vt-bot`
- `.env` 沒有 `DISCORD_TOKEN` 或 token 已失效
- Discord Developer Portal 沒開需要的 privileged intents，例如 Members Intent / Message Content Intent
- `.venv` 沒建好或 dependencies 沒裝完整
- 低記憶體造成 process 被 kill

### Browser SSH 顯示 `UPSTREAM_ERROR [515]`

這通常代表 Lightsail 的瀏覽器 SSH proxy 連不上 instance 內的 SSH service，或 instance 當下資源卡住。它不一定是 bot code 的錯，但 bot/dashboard restart loop 或記憶體不足可能會讓小機器連 SSH 都變得不穩。

Reboot 後一進 SSH，先暫停 bot/dashboard，避免它又把 instance 打滿：

```bash
sudo systemctl stop dc-gra-vt-bot dc-gra-vt-dashboard
```

然後立刻抓原因：

```bash
cd ~/DC
bash scripts/diagnose_lightsail.sh
```

重點看這幾類：

```bash
sudo systemctl status ssh --no-pager
sudo journalctl -u ssh -n 120 --no-pager
sudo journalctl -k -n 200 --no-pager | grep -Ei 'oom|killed|out of memory|segfault|blocked'
free -h
df -h
sudo systemctl status dc-gra-vt-bot dc-gra-vt-dashboard --no-pager
```

如果看到 OOM / killed process，先加 swap：

```bash
sudo fallocate -l 1G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

如果確認服務設定已修好，再重啟：

```bash
cd ~/DC
bash scripts/install_lightsail_services.sh
```

## 12. Reboot 測試

```bash
sudo reboot
```

等 1-2 分鐘後重新 SSH：

```bash
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
curl -I http://你的_Lightsail_Static_IP
```

成功標準：

```text
bot 自動 online
dashboard URL 不變，仍然是 http://Lightsail_Static_IP
dashboard 可登入
兩個 service 都是 active (running)
```

## 13. 備份 JSON 資料

```bash
mkdir -p ~/backups
cp ~/DC/config.json ~/backups/config_$(date +%Y%m%d_%H%M%S).json
```

不要把 `.env` commit 到 GitHub。

## 14. Optional: 之後升級成 domain + HTTPS

如果之後你買 domain/subdomain，可以把 DNS A record 指去 Lightsail Static IP，然後把 Nginx `server_name _;` 改成你的 domain，例如：

```nginx
server_name dashboard.gra-vt.my;
```

再安裝 certbot：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d dashboard.gra-vt.my
```

## 15. Quick Tunnel 只作臨時測試

不要把 `trycloudflare.com` 當正式 dashboard URL。Quick Tunnel 每次重開可能換網址，而且手動 SSH session 斷掉後 tunnel 也可能停止。
# Discord 429 / Cloudflare 1015 防護與排查

Dashboard 會快取 Discord 的伺服器、頻道、角色與 emoji 資料。Discord 回傳 429 或 Cloudflare Error 1015 時，後端會停止新的 Discord 請求，讀取頁面改用最近一次成功資料，所有寫入操作則暫時停用。請勿反覆 Refresh 或重啟服務，否則可能延長 IP 封鎖。

可選環境設定（以下也是預設值）：

```dotenv
DISCORD_CACHE_TTL_SECONDS=300
DISCORD_CACHE_STALE_SECONDS=86400
DISCORD_CLOUDFLARE_COOLDOWN_SECONDS=900
DISCORD_CLOUDFLARE_MAX_COOLDOWN_SECONDS=3600
```

查看發生時段與服務是否重啟：

```bash
sudo journalctl -u dc-gra-vt-dashboard --since "30 minutes ago"
sudo journalctl -u dc-gra-vt-bot --since "30 minutes ago"
sudo systemctl show dc-gra-vt-dashboard -p NRestarts
sudo systemctl show dc-gra-vt-bot -p NRestarts
```

`/api/health` 的 `discord` 欄位會顯示 circuit 狀態、剩餘冷卻秒數、最後成功時間與限流次數。正式部署保持單一 Uvicorn worker；增加 workers 前，必須先把 circuit 狀態改為跨程序共享。
