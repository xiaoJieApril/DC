# DC-Gra-vt-bot

DC-Gra-vt-bot 是一個 Discord server 管理 bot，包含 web dashboard、message/embed 發送、channel/member/role mention、reaction role、dropdown role、button role panel、新成員歡迎、Moderation Rules、Discord 訊息證據與案件封存，以及 JSON 檔案儲存。

## Project Structure

```text
.
├─ bot.py                         # Discord bot runtime
├─ dashboard_api.py               # FastAPI dashboard API + static frontend host
├─ storage.py                     # JSON config storage layer
├─ gui.py                         # 舊 CustomTkinter GUI，本機備用
├─ frontend/                      # Dashboard HTML/CSS/JS
├─ deploy/                        # VPS systemd services + deploy helper
├─ docs/                          # 中文 hosting/deployment 文件
├─ scripts/                       # 本機/手機啟動腳本
├─ config.json                    # Saved messages and reaction role data
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

Lightsail 一次性診斷：

```bash
bash scripts/diagnose_lightsail.sh
```

如果 Lightsail browser SSH 顯示 `UPSTREAM_ERROR [515]`，reboot 後先停服務再診斷：

```bash
sudo systemctl stop dc-gra-vt-bot dc-gra-vt-dashboard
cd ~/DC
bash scripts/diagnose_lightsail.sh
```

套用/更新 Lightsail systemd service：

```bash
bash scripts/install_lightsail_services.sh
```

健康檢查：

```bash
curl http://127.0.0.1:8000/api/health
```

## JSON Storage

Saved messages、reaction roles、Welcome Automation 設定和待發跟進工作會存到專案根目錄的 `config.json`。Orihost/free container 上請確認 `config.json` 有保留在 Files 裡；如果重新 clone repo 或清空檔案，資料也會跟著消失。

New Member Rules 會在指定頻道發布單選語言訊息。每個啟用語言需指定自己的 Fans Role；成員選擇語言後會私下看到該語言規則，未有對應 Role 時顯示 Agree 按鈕，已有 Role 時只顯示規則。Common Member Role 為可選，設定後會在同意規則時一併授予。

Welcome Automation 支援 `{member}`、`{server}`、`{rules_channel}`。若啟用延遲跟進，需先在 New Member Rules 設定 Rules Channel 和至少一個 Fans Role；Bot 重啟後會繼續處理尚未到期的跟進工作，並會略過已取得任一語言 Fans Role 的成員。

Dashboard 在 Discord 限流或暫時離線時，會使用持久化的 guild/channel/role 快取顯示 selector。Welcome Automation 等純本地設定仍可保存；Send Message、Publish Panel 等真正需要寫入 Discord 的操作會等冷卻結束後才開放，避免反覆觸發 429／503。

Dashboard 會在送出前檢查必填欄位、Discord IDs、Rule 條件及 Message Link，並阻止相同操作重複送出。若 Discord 暫時不可用且沒有提供 retry 時間，前端會套用 60 秒保護期；這能避免多數可預防的 400／503，但 Discord 權限錯誤、外部服務中斷等真實失敗仍會明確顯示。

Bot 與 Dashboard 會透過 `data/request_limits.sqlite3` 共用短期 Discord 請求安全預算；Dashboard 另外依 login、本地讀寫及 Discord 讀寫分層限流。預設為平衡模式，所有數值都可在 `.env.example` 所列的 `DISCORD_*`、`DASHBOARD_*_LIMIT_*` 變數調整。`/api/health` 的 `discord` 與 `rate_limits` 欄位可用來確認 bucket 冷卻、無效請求、合併及拒絕次數。

Moderation Rules 可設定規則編號、原因、嚴重度及預設處置。Dashboard 可貼上 Discord Message Link 自動取得作者與證據；Discord 管理員也可右鍵訊息使用 **Apps → Create Moderation Case**，選 Rule 並確認後建立案件。Resolved／Rejected／Accepted 案件會顯示於 Archive，並可 Reopen。

伺服器 `.env` 建議使用：

```env
STORAGE_BACKEND=json
```

## Notes

- 不要 commit `.env`、`data/`、`logs/`。
- 正式 VPS 請設定 `BOT_CONTROL_MODE=systemd`，避免 dashboard UI 重複啟動 bot。
- `trycloudflare.com` Quick Tunnel 只適合臨時測試。沒有 domain 時，正式入口請用 Lightsail Static IP。
- Discord Developer Portal 需要開啟 Server Members Intent，New Member Rules、Welcome Automation 和 member role 發放才會穩定運作。
