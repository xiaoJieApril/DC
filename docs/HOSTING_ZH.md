# DC-Gra-vt-bot Hosting 中文總覽

這份是 hosting 入口文件。現在主線是 **Exabytes VPS**，本機/手機只作測試或備用。

## 推薦路線

| 場景 | 文件 / 指令 | 備註 |
| --- | --- | --- |
| Exabytes VPS 24/7 正式 host | `docs/DEPLOY_EXABYTES_ZH.md` | 目前推薦，電腦和手機可關機 |
| AWS Lightsail 備用 host | `docs/DEPLOY_AWS_LIGHTSAIL_ZH.md` | Exabytes 不可用時才看 |
| Windows 本機測試 | `scripts/run_local_host.ps1` | 電腦關機 bot 就離線 |
| 手機 Termux 備用 host | `scripts/start_phone_host.sh` | 不如 VPS 穩，只作臨時備用 |
| 公開本機 dashboard | `scripts/start_public_tunnel.ps1` | Quick Tunnel URL 可能會變 |

---

## 重要概念

- Exabytes VPS：最適合正式 24/7 跑 `bot.py` + `dashboard_api.py`。
- Exabytes cPanel / Website Hosting：只適合放 dashboard frontend，不適合跑整個 Discord bot。
- Cloudflare Tunnel：只負責把 dashboard 公開出去，不會自己 host bot。
- Windows/手機 host：裝置睡眠、斷網、關機，bot 就會離線。
- `.env` 裏有 Discord token 和 dashboard 密碼，不要 commit 或傳給不信任的人。

---

## A. Exabytes VPS 正式部署

完整步驟看：

```text
docs/DEPLOY_EXABYTES_ZH.md
```

常用部署指令：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\deploy\deploy_exabytes.ps1 -HostName "你的_VPS_IP" -User "ubuntu"
```

如果 VPS 使用 SSH key：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\deploy\deploy_exabytes.ps1 -HostName "你的_VPS_IP" -User "ubuntu" -KeyPath "C:\path\to\key.pem"
```

部署後在 VPS：

```bash
cd /opt/dc-gra-vt-bot
cp .env.example .env
nano .env
sudo systemctl enable --now dc-gra-vt-dashboard
sudo systemctl enable --now dc-gra-vt-bot
sudo systemctl status dc-gra-vt-dashboard
sudo systemctl status dc-gra-vt-bot
```

正式 24/7 host 由 systemd 自動保活。`.env` 建議設定 `BOT_CONTROL_MODE=systemd`，dashboard 會顯示 bot service 狀態，不會用 UI 重複啟動 bot。

---

## B. Windows 本機測試

進入專案資料夾：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\scripts\run_local_host.ps1
```

成功後：

```text
本機 dashboard: http://127.0.0.1:8000
同 Wi-Fi 手機: http://你的電腦IP:8000
```

健康檢查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing
```

---

## C. 公開本機 dashboard

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\scripts\start_public_tunnel.ps1
```

它會顯示：

```text
https://xxxx.trycloudflare.com
```

把這個網址給其他管理員。登入帳號密碼在 `.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`。

注意：Quick Tunnel URL 可能每次重開都不同。正式固定網址建議用 Exabytes VPS + domain / Cloudflare named tunnel。

---

## D. 手機 Termux 備用 host

Termux 是 Android terminal + Linux environment app。建議從官方網站安裝最新版：

https://termux.dev/en/

安裝套件：

```bash
pkg update
pkg upgrade
pkg install python git unzip openssh clang rust libffi openssl
termux-setup-storage
```

放入專案後：

```bash
cd DC
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-phone.txt
```

設定：

```bash
cp .env.example .env
nano .env
```

生成 `SESSION_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

啟動：

```bash
bash scripts/start_phone_host.sh
```

手機自己打開：

```text
http://127.0.0.1:8000
```

停止：

```bash
bash scripts/stop_phone_host.sh
```

手機 host 注意事項：

- 關閉 Termux battery optimization。
- 手機保持充電和網路穩定。
- Wi-Fi 要穩定。
- 如果 Android 自動殺 Termux，bot 會掉線。
- CustomTkinter GUI 不會在一般 Termux 裏跑。

---

## E. 備用 AWS Lightsail

如果 Exabytes VPS 暫時不可用，可以改看：

```text
docs/DEPLOY_AWS_LIGHTSAIL_ZH.md
```

---

## F. 文件整理狀態

本機/手機 hosting、公開 dashboard、舊雲端部署內容已合併或移除。現在主要看這兩份：

```text
README.md
docs/HOSTING_ZH.md
docs/DEPLOY_EXABYTES_ZH.md
```
