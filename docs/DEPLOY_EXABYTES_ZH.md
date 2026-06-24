# DC-Gra-vt-bot Exabytes 部署教學

目標：判斷朋友目前的 Exabytes 能不能跑整個 bot，並在可行時把 Discord bot + dashboard 搬到 Exabytes。

## 0. 先判斷 Exabytes 類型

### 可以跑整個 bot 的情況

你朋友的 Exabytes 必須是 VPS / Cloud Server，而且要有：

```text
SSH login
root / sudo access
Ubuntu 或 Debian
可以安裝 Python package
可以長時間跑 systemd service
```

登入後可以跑這些指令：

```bash
whoami
sudo systemctl status
python3 --version
```

如果這些都可以，就是 VPS 路線，可以把 bot 和 dashboard 都放上去。

正式 24/7 部署方式是：

```text
systemd 長期跑 dashboard_api.py
systemd 長期跑 bot.py
dashboard 顯示 bot service 狀態
```

### 只適合放 dashboard frontend 的情況

如果朋友只有：

```text
cPanel
File Manager
phpMyAdmin
Email Accounts
WordPress installer
```

那通常是 shared website hosting。這種不適合跑 Discord bot，因為 bot 需要 24/7 背景 Python process 和 Discord gateway websocket。這種情況可以只放 dashboard frontend，bot backend 仍然放 Exabytes VPS、AWS Lightsail、手機、電腦或另一台 VPS。

---

## 1. Exabytes VPS 推薦架構

```text
Exabytes VPS Ubuntu
  -> /opt/dc-gra-vt-bot
  -> bot.py
  -> dashboard_api.py
  -> config.json
  -> systemd auto restart dashboard
  -> systemd auto restart bot

Domain / subdomain
  -> Cloudflare Tunnel 或 Nginx reverse proxy
  -> dashboard API
```

建議 subdomain：

```text
bot.gra-vt.my
dashboard.gra-vt.my
dc-gra-vt-bot.gra-vt.my
```

如果不想影響 Gra-vt 主網站，建議用 subdomain，不要動主 domain。

---

## 2. 在 VPS 建立環境

SSH 進 Exabytes VPS：

```bash
ssh ubuntu@你的_VPS_IP
```

如果 username 不是 `ubuntu`，改成 Exabytes 給你的 username。

更新系統：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git unzip curl
```

建立專案資料夾：

```bash
sudo mkdir -p /opt/dc-gra-vt-bot
sudo chown $USER:$USER /opt/dc-gra-vt-bot
```

---

## 3. 上傳專案

### 方法 A：從 GitHub clone

如果 repo 是 public：

```bash
cd /opt
git clone https://github.com/xiaoJieApril/DC.git dc-gra-vt-bot
cd /opt/dc-gra-vt-bot
```

如果 repo 是 private，要用 GitHub Personal Access Token，不是 GitHub password。

### 方法 B：從你的 Windows 電腦直接部署

在 Windows PowerShell 進入專案資料夾：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\deploy\deploy_exabytes.ps1 -HostName "你的_VPS_IP" -User "ubuntu"
```

如果需要 SSH key：

```powershell
.\deploy\deploy_exabytes.ps1 -HostName "你的_VPS_IP" -User "ubuntu" -KeyPath "C:\path\to\key.pem"
```

---

## 4. 安裝 Python dependencies

```bash
cd /opt/dc-gra-vt-bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

如果是很小的 VPS，也可以改裝手機/伺服器精簡版：

```bash
pip install -r requirements-phone.txt
```

---

## 5. 設定 `.env`

```bash
cd /opt/dc-gra-vt-bot
cp .env.example .env
nano .env
```

填入：

```env
DISCORD_TOKEN=你的_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的_dashboard_密碼
SESSION_SECRET=一串很長的隨機字串
PUBLIC_FRONTEND_ORIGIN=https://你的_dashboard_domain
BOT_CONTROL_MODE=systemd
SYSTEMD_BOT_SERVICE=dc-gra-vt-bot
```

生成 `SESSION_SECRET`：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

不要把 `.env` 上傳到 GitHub，也不要發給不信任的人。

---

## 6. 本機測試 VPS 上的 bot/API

```bash
cd /opt/dc-gra-vt-bot
source .venv/bin/activate
python -m py_compile bot.py dashboard_api.py storage.py
```

測試 dashboard API：

```bash
python -m uvicorn dashboard_api:app --host 127.0.0.1 --port 8000
```

看到 server started 後，按 `Ctrl+C` 停止。

可選：手動測試 bot：

```bash
python bot.py
```

Discord 看到 bot online 後，按 `Ctrl+C` 停止。

---

## 7. 安裝 systemd services

正式 24/7 host 要讓 dashboard 和 bot 都自動開機啟動。`.env` 建議設定 `BOT_CONTROL_MODE=systemd`，避免 dashboard UI 開出第二個 bot instance。

```bash
sudo cp /opt/dc-gra-vt-bot/deploy/dc-gra-vt-bot.service /etc/systemd/system/dc-gra-vt-bot.service
sudo cp /opt/dc-gra-vt-bot/deploy/dc-gra-vt-dashboard.service /etc/systemd/system/dc-gra-vt-dashboard.service
sudo systemctl daemon-reload
```

如果 VPS username 不是 `ubuntu`，要修改 service 裏的 `User=`：

```bash
sudo nano /etc/systemd/system/dc-gra-vt-dashboard.service
sudo nano /etc/systemd/system/dc-gra-vt-bot.service
```

把：

```ini
User=ubuntu
```

改成你的 Linux username。

啟動 dashboard：

```bash
sudo systemctl enable --now dc-gra-vt-dashboard
sudo systemctl enable --now dc-gra-vt-bot
```

檢查狀態：

```bash
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
```

看 log：

```bash
sudo journalctl -u dc-gra-vt-dashboard -f
sudo journalctl -u dc-gra-vt-bot -f
```

然後打開 dashboard，進入 Overview，確認 bot service 是 running。

```text
BOT_CONTROL_MODE=systemd 時，Start Bot / End Bot 會禁用
正式開關 bot 請用 systemctl restart/stop/start dc-gra-vt-bot
```

Bot log 會在 dashboard 內顯示，也會寫到：

```text
/opt/dc-gra-vt-bot/logs/dashboard_bot.log
```

---

## 8. 公開 dashboard

### 推薦：Cloudflare Tunnel，不影響主網站

這個方法最適合朋友已經有 Gra-vt 主網站的情況，因為只需要新增 subdomain，不需要改主站 server。

安裝 cloudflared：

```bash
cd /tmp
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
cloudflared --version
```

先用 quick tunnel 測試：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

測試成功後，可以再用 Cloudflare named tunnel 綁定固定 subdomain。

### 可選：Nginx reverse proxy

如果朋友想用 Exabytes VPS IP 直接提供 dashboard：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

建立 Nginx site：

```bash
sudo nano /etc/nginx/sites-available/dc-gra-vt-bot
```

貼上：

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
sudo ln -s /etc/nginx/sites-available/dc-gra-vt-bot /etc/nginx/sites-enabled/dc-gra-vt-bot
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d dashboard.gra-vt.my
```

DNS 要把 `dashboard.gra-vt.my` 的 A record 指去 Exabytes VPS public IP。

---

## 9. cPanel shared hosting 路線

如果朋友不是 VPS，只能做這個：

1. 保持 bot backend 在 AWS Lightsail / 手機 / 電腦 / 其他 VPS。
2. 把 `frontend` 放到 Exabytes cPanel 的 subdomain，例如 `bot.gra-vt.my`。
3. 修改 `frontend/config.js`：

```js
window.DASHBOARD_API_BASE = "https://你的_backend_api_domain";
```

4. 用 cPanel File Manager 上傳 `frontend/index.html`、`frontend/app.js`、`frontend/styles.css`、`frontend/config.js`。

這樣網站 dashboard 可以在 Exabytes 顯示，但真正發訊息、改 reaction role 的 API 還是由外部 VPS/手機/電腦提供。

---

## 10. 更新 code

如果是 GitHub clone：

```bash
cd /opt/dc-gra-vt-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart dc-gra-vt-dashboard
```

如果是 Windows deploy script：

```powershell
.\deploy\deploy_exabytes.ps1 -HostName "你的_VPS_IP" -User "ubuntu"
```

然後在 VPS：

```bash
sudo systemctl restart dc-gra-vt-dashboard
sudo systemctl restart dc-gra-vt-bot
```

Dashboard 重啟後，bot 會由 `dc-gra-vt-bot.service` 繼續保活。

---

## 11. 備份資料

JSON 資料在：

```text
/opt/dc-gra-vt-bot/config.json
```

手動備份：

```bash
mkdir -p ~/dc-gra-vt-backups
cp /opt/dc-gra-vt-bot/config.json ~/dc-gra-vt-backups/config_$(date +%Y%m%d_%H%M%S).json
```

---

## 12. 最終檢查

```bash
sudo systemctl is-active dc-gra-vt-dashboard
curl http://127.0.0.1:8000/api/health
```

Dashboard 可以登入後測：

```text
Load guild/channel/role/emoji
Send message
Create reaction role
Create multi-select dropdown role
Restart VPS
Confirm dashboard auto restore
Confirm bot auto restore
```

---

## 推薦結論

如果朋友有 Exabytes VPS：可以把整個 bot 搬過去。  
如果朋友只有 Exabytes cPanel：不要把 bot 放上去，只放 dashboard frontend，bot backend 另外找 VPS。
