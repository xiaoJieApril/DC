param(
    [string]$LocalUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $Root "tools"
$Cloudflared = Join-Path $ToolsDir "cloudflared.exe"
$DownloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

if (-not (Test-Path $Cloudflared)) {
    Write-Host "Downloading cloudflared..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $DownloadUrl -OutFile $Cloudflared
}

Write-Host "Starting public Cloudflare Quick Tunnel for $LocalUrl" -ForegroundColor Green
Write-Host "Copy the https://*.trycloudflare.com URL shown below and give it to admins." -ForegroundColor Yellow
Write-Host "Keep this window open while you want the dashboard public." -ForegroundColor Yellow

& $Cloudflared tunnel --url $LocalUrl
