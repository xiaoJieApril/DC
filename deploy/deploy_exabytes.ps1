param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,

    [string]$User = "ubuntu",
    [string]$KeyPath = "",
    [string]$RemoteDir = "/opt/dc-gra-vt-bot"
)

$ErrorActionPreference = "Stop"

function Run-Local($Command) {
    Write-Host "`n> $Command" -ForegroundColor Cyan
    Invoke-Expression $Command
}

function SshTarget {
    if ($KeyPath.Trim()) {
        return "ssh -i `"$KeyPath`" $User@$HostName"
    }
    return "ssh $User@$HostName"
}

function ScpBase {
    if ($KeyPath.Trim()) {
        return "scp -i `"$KeyPath`""
    }
    return "scp"
}

$ssh = SshTarget
$scp = ScpBase
$archive = "dc-gra-vt-bot-exabytes-deploy.zip"

Write-Host "Preparing Exabytes deployment archive..." -ForegroundColor Green
if (Test-Path $archive) {
    Remove-Item $archive -Force
}

$items = @(
    "bot.py",
    "dashboard_api.py",
    "storage.py",
    "requirements.txt",
    "requirements-phone.txt",
    ".env.example",
    "README.md",
    "docs",
    "scripts",
    "frontend",
    "deploy"
)

Compress-Archive -Path $items -DestinationPath $archive -Force

Run-Local "$ssh `"sudo mkdir -p $RemoteDir && sudo chown $User:$User $RemoteDir`""
Run-Local "$scp `"$archive`" $User@$HostName`:/tmp/$archive"
Run-Local "$ssh `"sudo apt update && sudo apt install -y python3 python3-venv python3-pip unzip curl`""
Run-Local "$ssh `"find $RemoteDir -maxdepth 1 ! -name '.env' ! -name 'data' ! -name '.venv' ! -path $RemoteDir -exec rm -rf {} + && unzip -o /tmp/$archive -d $RemoteDir`""
Run-Local "$ssh `"cd $RemoteDir && python3 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt`""
Run-Local "$ssh `"sudo cp $RemoteDir/deploy/dc-gra-vt-dashboard.service /etc/systemd/system/dc-gra-vt-dashboard.service && sudo cp $RemoteDir/deploy/dc-gra-vt-bot.service /etc/systemd/system/dc-gra-vt-bot.service && sudo systemctl daemon-reload`""

Write-Host "`nUpload complete." -ForegroundColor Green
Write-Host "Next SSH into the Exabytes VPS and configure .env before starting services:" -ForegroundColor Yellow
Write-Host "  $ssh"
Write-Host "  cd $RemoteDir"
Write-Host "  cp .env.example .env   # only if .env does not exist yet"
Write-Host "  nano .env"
Write-Host "  # For 24/7 production set BOT_CONTROL_MODE=systemd in .env"
Write-Host "  sudo systemctl enable --now dc-gra-vt-dashboard"
Write-Host "  sudo systemctl enable --now dc-gra-vt-bot"
Write-Host "  sudo journalctl -u dc-gra-vt-dashboard -f"
