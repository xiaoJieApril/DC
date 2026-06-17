# DC-Gra-vt-bot 本機 / 手機 Host 中文操作手冊

## 重要概念

- 電腦 host：電腦開著，bot/dashboard 才在線；手機可以關機。
- 手機 host：手機開著，bot/dashboard 才在線；電腦可以關機。
- Cloudflare Tunnel：只負責把 dashboard 公開出去，不會自己 host bot。
- 如果 host 裝置睡眠、斷網、關機，bot 就會離線。

Termux 是 Android terminal + Linux environment app，官方網站說它不需要 root，會自動安裝 minimal base system，其他 package 透過 APT 安裝。建議從 Termux 官方網站連到 F-Droid 或 GitHub 安裝最新版，不要用過時來源。

官方網站：https://termux.dev/en/

---

## A. 電腦重新開機後，如何恢復 bot + dashboard

### 1. 打開 PowerShell

進入專案資料夾：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
```

### 2. 啟動本機 bot + dashboard

```powershell
.\run_local_host.ps1
```

成功後：

```text
本機 dashboard: http://127.0.0.1:8000
同 Wi-Fi 手機: http://你的電腦IP:8000
```

如果你之前是用背景 process 啟動，也可以檢查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing
```

看到：

```json
{"ok":true,"storage":"sqlite"}
```

代表 dashboard API 正常。

### 3. 開公開 dashboard 給其他管理員

另開一個 PowerShell，進入同一個資料夾：

```powershell
cd "C:\Users\lolha\Desktop\All Document\app by myself\DC"
.\start_public_tunnel.ps1
```

它會顯示：

```text
https://xxxx.trycloudflare.com
```

把這個網址給其他管理員。

登入資料在 `.env`：

```env
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
```

### 4. 停止電腦 host

如果你是用 `run_local_host.ps1` 啟動，按：

```text
Ctrl + C
```

如果是背景 process，可以查：

```powershell
Get-CimInstance Win32_Process -Filter "name = 'python.exe' or name = 'cloudflared.exe'" | Select-Object ProcessId,CommandLine
```

停止指定 PID：

```powershell
Stop-Process -Id PID
```

---

## B. 手機作為主要 host（Android + Termux）

### 1. 安裝 Termux

建議從官方網站進入 F-Droid 或 GitHub 安裝最新版：

```text
https://termux.dev/en/
```

不要用太舊的 Termux 版本，否則 Python package 可能裝不到。

### 2. 安裝手機需要的套件

打開 Termux：

```bash
pkg update
pkg upgrade
pkg install python git unzip openssh clang rust libffi openssl
termux-setup-storage
```

`termux-setup-storage` 會要求手機授權檔案存取。

### 3. 把專案放到手機

方法 1：用 USB / Google Drive / Discord 傳 zip 到手機，然後在 Termux 解壓。

假設 zip 在 Downloads：

```bash
cd ~
cp /sdcard/Download/DC.zip .
unzip DC.zip
cd DC
```

方法 2：如果你之後放到 GitHub，可以用：

```bash
git clone 你的repo網址 DC
cd DC
```

### 4. 建立手機 Python 環境

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-phone.txt
```

手機不需要 `customtkinter`，所以用 `requirements-phone.txt`。

### 5. 設定 `.env`

```bash
cp .env.example .env
nano .env
```

填入：

```env
DISCORD_TOKEN=你的bot token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的dashboard密碼
SESSION_SECRET=一串很長的隨機字串
PUBLIC_FRONTEND_ORIGIN=http://127.0.0.1:8000
```

手機生成 `SESSION_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 6. 啟動手機 host

```bash
bash start_phone_host.sh
```

手機自己打開：

```text
http://127.0.0.1:8000
```

同 Wi-Fi 其他裝置打開，要先找手機 IP：

```bash
ip addr
```

找 `wlan0` 的 IPv4，例如：

```text
192.168.0.25
```

其他裝置打開：

```text
http://192.168.0.25:8000
```

### 7. 手機公開 dashboard 給外部管理員

手機上也可以用 Cloudflare Tunnel，但比較耗電。先安裝 cloudflared：

```bash
pkg install cloudflared
```

如果 Termux repo 沒有 cloudflared，就代表這台手機環境不適合用手機公開 dashboard，建議仍用電腦跑 tunnel。

有 cloudflared 的話：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

它會給你：

```text
https://xxxx.trycloudflare.com
```

給管理員用這個網址登入。

### 8. 停止手機 host

```bash
bash stop_phone_host.sh
```

如果要看 log：

```bash
tail -n 50 logs/bot.err.log
tail -n 50 logs/dashboard.err.log
```

---

## C. 手機 host 的注意事項

### 必做

- 手機不要睡眠或被系統殺背景。
- 關閉 Termux 的 battery optimization。
- 手機保持充電。
- Wi-Fi 要穩定。
- 不要把 `.env` 傳給不信任的人。

### Android 設定建議

```text
Settings -> Apps -> Termux -> Battery -> Unrestricted / 不限制
Settings -> Apps -> Termux -> Allow background activity
```

不同 Android 品牌名字會不一樣。

### 限制

- 手機 host 不如電腦 / cloud 穩。
- CustomTkinter GUI 不會在一般 Termux 裡跑。
- 如果 Android 自動殺 Termux，bot 會掉線。
- Cloudflare Quick Tunnel 的網址每次重開可能會變。

---

## D. 推薦日常使用方式

目前最穩的免費做法：

```text
平時：電腦 host + Cloudflare Tunnel 公開 dashboard
備用：手機 host，只有電腦不能開時才用
未來：Oracle Cloud / 其他 VPS 成功後，再搬到 cloud 24/7
```

如果只是讓管理員在外面控制 dashboard，不一定要手機 host；電腦 host + Cloudflare Tunnel 已經可以做到。
