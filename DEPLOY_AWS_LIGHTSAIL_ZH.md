# DC-Gra-vt-bot AWS Lightsail 部署教學（便宜版）

目標：用 AWS Lightsail 讓 Discord bot + dashboard 24/7 運行。  
推薦方案：**Lightsail Ubuntu + Cloudflare Tunnel**。

## 0. 成本建議

最便宜建議：

```text
AWS Lightsail
OS: Ubuntu
Plan: Linux/Unix IPv6-only $3.50/month
Dashboard expose: Cloudflare Tunnel
```

如果你不想碰 IPv6 / SSH 比較麻煩，可以選：

```text
Linux/Unix public IPv4 $5/month
```

不要開：

```text
Load Balancer
RDS / Database
NAT Gateway
Extra static IP unless needed
Container service
```

只需要一台 Lightsail instance。

---

## 1. 建立 Lightsail Instance

進入：

```text
AWS Console -> Lightsail -> Create instance
```

選：

```text
Platform: Linux/Unix
Blueprint: OS Only
OS: Ubuntu 24.04 LTS 或 Ubuntu 22.04 LTS
Region: Singapore / Tokyo / 你附近的 region
Plan:
  - 最便宜：IPv6-only $3.50/month
  - 較簡單：public IPv4 $5/month
Instance name: dc-gra-vt-bot
```

建立後等 instance 狀態變成 `Running`。

---

## 2. SSH 進 Lightsail

Lightsail 頁面有 browser SSH：

```text
Lightsail -> Instances -> dc-gra-vt-bot -> Connect using SSH
```

進去後先更新系統：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git unzip curl
```

---

## 3. Clone 你的 GitHub Repo

如果 repo 是 public：

```bash
cd ~
git clone https://github.com/xiaoJieApril/DC.git
cd DC
```

如果 repo 是 private，用 GitHub Personal Access Token：

```bash
cd ~
git clone https://github.com/xiaoJieApril/DC.git
```

GitHub 問：

```text
Username: xiaoJieApril
Password: 貼 Personal Access Token，不是 GitHub password
```

---

## 4. 建立 Python Environment

```bash
cd ~/DC
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-phone.txt
```

如果 `requirements-phone.txt` 不存在，先用：

```bash
pip install py-cord python-dotenv requests fastapi uvicorn itsdangerous
```

---

## 5. 設定 `.env`

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
PUBLIC_FRONTEND_ORIGIN=http://127.0.0.1:8000
```

生成 `SESSION_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

保存 nano：

```text
Ctrl + O
Enter
Ctrl + X
```

---

## 6. 測試 Bot 和 Dashboard

測試 Python 檔案：

```bash
cd ~/DC
source .venv/bin/activate
python -m py_compile bot.py dashboard_api.py storage.py
```

測試 dashboard：

```bash
python -m uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
```

另開一個 SSH tab 或先按 `Ctrl+C` 停掉。

---

## 7. 建立 systemd Service

建立 bot service：

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

建立 dashboard service：

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
sudo systemctl enable --now dc-gra-vt-bot
sudo systemctl enable --now dc-gra-vt-dashboard
```

檢查：

```bash
sudo systemctl status dc-gra-vt-bot
sudo systemctl status dc-gra-vt-dashboard
```

看 log：

```bash
sudo journalctl -u dc-gra-vt-bot -f
sudo journalctl -u dc-gra-vt-dashboard -f
```

---

## 8. 安裝 Cloudflare Tunnel

### Ubuntu x86_64 / amd64

```bash
cd /tmp
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
```

### Ubuntu ARM64

如果你的 Lightsail 是 ARM：

```bash
cd /tmp
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared-linux-arm64.deb
```

檢查：

```bash
cloudflared --version
```

---

## 9. 先用 Quick Tunnel 測試

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

它會顯示：

```text
https://xxxx.trycloudflare.com
```

打開這個網址，應該會看到 dashboard login。

測試成功後按 `Ctrl+C` 停止。

---

## 10. 讓 Cloudflare Tunnel 自動開機啟動

建立 quick tunnel service：

```bash
sudo nano /etc/systemd/system/dc-gra-vt-tunnel.service
```

貼上：

```ini
[Unit]
Description=DC-Gra-vt-bot Cloudflare Quick Tunnel
After=network-online.target dc-gra-vt-dashboard.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8000
Restart=always
RestartSec=8
User=ubuntu

[Install]
WantedBy=multi-user.target
```

有些安裝會把 cloudflared 放在 `/usr/bin/cloudflared`。檢查：

```bash
which cloudflared
```

如果顯示 `/usr/bin/cloudflared`，把 service 裡的：

```ini
ExecStart=/usr/local/bin/cloudflared ...
```

改成：

```ini
ExecStart=/usr/bin/cloudflared tunnel --url http://127.0.0.1:8000
```

啟動：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dc-gra-vt-tunnel
sudo journalctl -u dc-gra-vt-tunnel -f
```

log 裡會看到：

```text
https://xxxx.trycloudflare.com
```

這就是 dashboard 網址。

注意：Quick Tunnel 網址可能會變。如果你要固定網址，需要 Cloudflare named tunnel + domain。

---

## 11. 更新 Bot Code

```bash
cd ~/DC
git pull
source .venv/bin/activate
pip install -r requirements-phone.txt
sudo systemctl restart dc-gra-vt-bot
sudo systemctl restart dc-gra-vt-dashboard
sudo systemctl restart dc-gra-vt-tunnel
```

---

## 12. 備份 SQLite Database

你的資料在：

```text
~/DC/data/dc_gra_vt_bot.db
```

手動備份：

```bash
mkdir -p ~/backups
cp ~/DC/data/dc_gra_vt_bot.db ~/backups/dc_gra_vt_bot_$(date +%Y%m%d_%H%M%S).db
```

不要把 `.env` commit 到 GitHub。

---

## 13. 停止服務

```bash
sudo systemctl stop dc-gra-vt-bot
sudo systemctl stop dc-gra-vt-dashboard
sudo systemctl stop dc-gra-vt-tunnel
```

如果不想再被 AWS 收費，要在 Lightsail Console 刪除 instance。

---

## 14. Billing 安全

一定要做：

```text
AWS Billing -> Budgets -> Create budget
Monthly budget: 5 USD 或 10 USD
Email alert: 開啟
```

避免做：

```text
NAT Gateway
Load Balancer
RDS
Extra storage
多開 instance
```

---

## 推薦結論

如果你要最便宜：

```text
Lightsail IPv6-only $3.50/month + Cloudflare Tunnel
```

如果你要最容易：

```text
Lightsail public IPv4 $5/month + Cloudflare Tunnel
```

這個 bot 不需要 EC2 / RDS / Load Balancer。
