param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

if (-not $PythonPath.Trim()) {
    $DefaultPython = "C:\Users\lolha\AppData\Local\Programs\Python\Python313\python.exe"
    if (Test-Path $DefaultPython) {
        $PythonPath = $DefaultPython
    } else {
        $PythonPath = "python"
    }
}

if (-not (Test-Path ".env")) {
    Write-Host ".env not found. Copy .env.example to .env and fill it first." -ForegroundColor Red
    exit 1
}

Write-Host "Starting DC-Gra-vt-bot local host..." -ForegroundColor Green
Write-Host "Dashboard URL on this PC: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Dashboard URL on phone: use http://YOUR_PC_LAN_IP:$Port" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop both processes." -ForegroundColor Yellow

$botJob = Start-Job -Name "dc-gra-vt-bot" -ScriptBlock {
    param($Root, $PythonPath)
    Set-Location $Root
    & $PythonPath bot.py
} -ArgumentList $Root, $PythonPath

$apiJob = Start-Job -Name "dc-gra-vt-dashboard" -ScriptBlock {
    param($Root, $PythonPath, $HostAddress, $Port)
    Set-Location $Root
    & $PythonPath -m uvicorn dashboard_api:app --host $HostAddress --port $Port
} -ArgumentList $Root, $PythonPath, $HostAddress, $Port

try {
    while ($true) {
        Receive-Job $botJob -Keep
        Receive-Job $apiJob -Keep
        Start-Sleep -Seconds 2

        if ($botJob.State -ne "Running") {
            Write-Host "Bot process stopped: $($botJob.State)" -ForegroundColor Red
            Receive-Job $botJob -Keep
            break
        }
        if ($apiJob.State -ne "Running") {
            Write-Host "Dashboard API stopped: $($apiJob.State)" -ForegroundColor Red
            Receive-Job $apiJob -Keep
            break
        }
    }
}
finally {
    Stop-Job $botJob, $apiJob -ErrorAction SilentlyContinue
    Remove-Job $botJob, $apiJob -Force -ErrorAction SilentlyContinue
}
