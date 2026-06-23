# DC-Gra-vt-bot

DC-Gra-vt-bot 是一個 Discord server 管理 bot，包含 web dashboard、message/embed 發送、reaction role、dropdown role、button role panel，以及 SQLite 儲存。

## Project Structure

```text
.
├─ bot.py                         # Discord bot runtime
├─ dashboard_api.py               # FastAPI dashboard API + static frontend host
├─ storage.py                     # SQLite/config storage layer
├─ gui.py                         # 舊 CustomTkinter GUI，本機備用
├─ frontend/                      # Dashboard HTML/CSS/JS
├─ deploy/                        # VPS systemd services + deploy helper
├─ docs/                          # 中文 hosting/deployment 文件
├─ scripts/                       # 本機/手機啟動腳本
├─ data/                          # SQLite data, ignored by git
├─ logs/                          # Runtime logs, ignored by git
├─ requirements.txt               # Full local/server dependencies
├─ requirements-phone.txt         # Termux/phone lightweight dependencies
└─ .env.example                   # Environment template
```

## Quick Setup

建立 `.env`：

```bash
cp .env.example .env
```

填入：

```env
DISCORD_TOKEN=你的_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的_dashboard_密碼
SESSION_SECRET=一串很長的隨機字串
PUBLIC_FRONTEND_ORIGIN=http://你的_Lightsail_Static_IP
BOT_CONTROL_MODE=systemd
SYSTEMD_BOT_SERVICE=dc-gra-vt-bot
```

生成 `SESSION_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

安裝 dependencies：

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## Local Run

Windows 本機測試：

```powershell
.\scripts\run_local_host.ps1
```

打開：

```text
http://127.0.0.1:8000
```

手機 Termux 備用：

```bash
bash scripts/start_phone_host.sh
```

## 24/7 VPS Deployment

正式 24/7 建議使用 systemd 跑 dashboard 和 bot：

```bash
sudo systemctl enable --now dc-gra-vt-dashboard
sudo systemctl enable --now dc-gra-vt-bot
```

不用 domain 時，固定 dashboard URL 使用：

```text
Lightsail Static IP -> Nginx -> http://Static_IP
```

之後如果要 HTTPS，再升級成 domain/subdomain + Certbot。

詳細文件：

- [Hosting 總覽](docs/HOSTING_ZH.md)
- [AWS Lightsail 24/7 無 domain 部署](docs/DEPLOY_AWS_LIGHTSAIL_ZH.md)
- [Exabytes VPS 部署](docs/DEPLOY_EXABYTES_ZH.md)

## Useful Commands

檢查服務：

```bash
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
```

看 logs：

```bash
sudo journalctl -u dc-gra-vt-dashboard -f
sudo journalctl -u dc-gra-vt-bot -f
```

健康檢查：

```bash
curl http://127.0.0.1:8000/api/health
```

## Notes

- 不要 commit `.env`、`data/`、`logs/`。
- 正式 VPS 請設定 `BOT_CONTROL_MODE=systemd`，避免 dashboard UI 重複啟動 bot。
- `trycloudflare.com` Quick Tunnel 只適合臨時測試。沒有 domain 時，正式入口請用 Lightsail Static IP。
