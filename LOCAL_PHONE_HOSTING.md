# Local PC + Phone Hosting

Use this when Oracle Cloud is not available yet.

## What This Setup Means

- Main host: your Windows PC
- Phone role: open dashboard, manage bot, optional backup host
- If the PC is off, the bot is offline
- If the phone is off but PC is on, the bot still runs

## 1. Prepare `.env`

Copy `.env.example` to `.env` if needed, then fill:

```env
DISCORD_TOKEN=your_discord_bot_token
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_dashboard_password
SESSION_SECRET=a_long_random_secret
PUBLIC_FRONTEND_ORIGIN=http://localhost:8000
```

Generate `SESSION_SECRET`:

```powershell
& 'C:\Users\lolha\AppData\Local\Programs\Python\Python313\python.exe' -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 2. Start On Windows PC

From this project folder:

```powershell
.\run_local_host.ps1
```

Open on the PC:

```text
http://127.0.0.1:8000
```

## 3. Open Dashboard From Phone

Phone and PC must be on the same Wi-Fi.

Find your PC LAN IP:

```powershell
ipconfig
```

Look for `IPv4 Address`, for example:

```text
192.168.1.23
```

Open this on your phone browser:

```text
http://192.168.1.23:8000
```

If the phone cannot connect, allow Python/port `8000` through Windows Firewall.

## 4. Keep PC Awake

For the bot to stay online, the PC must not sleep.

Windows:

```text
Settings -> System -> Power -> Screen and sleep
```

Set sleep to `Never` while hosting.

## 5. Optional: Android Phone As Backup Host

Use this only if you want the phone itself to run the bot when the PC is not available.

Install Termux, then:

```bash
pkg update
pkg install python git
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Notes:

- CustomTkinter GUI will not run on normal Android Termux.
- The FastAPI dashboard can run in Termux, but phone battery/network stability is weaker than a PC or cloud VM.
- Android may stop background apps unless battery optimization is disabled for Termux.

## 6. Stop Local Host

In the PowerShell window running `run_local_host.ps1`, press:

```text
Ctrl+C
```
