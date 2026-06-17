# Public Dashboard With Cloudflare Quick Tunnel

This exposes the local dashboard to other admins without Oracle Cloud or router port forwarding.

## Current Flow

1. Keep the PC on.
2. Keep the bot/dashboard running.
3. Start Cloudflare Tunnel.
4. Give the `https://*.trycloudflare.com` URL to trusted admins.

## Start The Local Host

```powershell
.\run_local_host.ps1
```

Or keep using the already-running bot/dashboard processes.

## Start Public Tunnel

```powershell
.\start_public_tunnel.ps1
```

Copy the generated URL:

```text
https://something.trycloudflare.com
```

Admins can open that URL and log in with the dashboard username/password from `.env`.

## Important Limits

- This is a Cloudflare Quick Tunnel.
- It is free and does not need a domain.
- The URL can change every time you restart the tunnel.
- It has no uptime guarantee.
- If your PC sleeps or shuts down, the bot/dashboard goes offline.

For a fixed URL later, use a named Cloudflare Tunnel with your own domain.

## Security

Only give the URL and password to trusted admins. Anyone with both can control the bot.

Recommended next upgrade:

- multiple dashboard accounts
- Owner/Admin/Viewer permissions
- audit log for who sent/edited/deleted messages
