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
$archive = "dc-gra-vt-bot-deploy.zip"

Write-Host "Preparing deployment archive..." -ForegroundColor Green
if (Test-Path $archive) {
    Remove-Item $archive -Force
}

$items = @(
    "bot.py",
    "dashboard_api.py",
    "storage.py",
    "requirements.txt",
    ".env.example",
    "DEPLOY_ORACLE.md",
    "frontend",
    "deploy"
)

Compress-Archive -Path $items -DestinationPath $archive -Force

Run-Local "$ssh `"sudo mkdir -p $RemoteDir && sudo chown $User:$User $RemoteDir`""
Run-Local "$scp `"$archive`" $User@$HostName`:/tmp/$archive"
Run-Local "$ssh `"sudo apt update && sudo apt install -y python3 python3-venv python3-pip unzip`""
Run-Local "$ssh `"rm -rf $RemoteDir/*.py $RemoteDir/frontend $RemoteDir/deploy $RemoteDir/requirements.txt $RemoteDir/DEPLOY_ORACLE.md $RemoteDir/.env.example && unzip -o /tmp/$archive -d $RemoteDir`""
Run-Local "$ssh `"cd $RemoteDir && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`""
Run-Local "$ssh `"sudo cp $RemoteDir/deploy/dc-gra-vt-bot.service /etc/systemd/system/dc-gra-vt-bot.service && sudo cp $RemoteDir/deploy/dc-gra-vt-dashboard.service /etc/systemd/system/dc-gra-vt-dashboard.service && sudo systemctl daemon-reload`""

Write-Host "`nUpload complete." -ForegroundColor Green
Write-Host "Next SSH into the VM and create $RemoteDir/.env from .env.example before starting services:" -ForegroundColor Yellow
Write-Host "  $ssh"
Write-Host "  cd $RemoteDir"
Write-Host "  cp .env.example .env"
Write-Host "  nano .env"
Write-Host "  sudo systemctl enable --now dc-gra-vt-bot"
Write-Host "  sudo systemctl enable --now dc-gra-vt-dashboard"
Write-Host "  sudo journalctl -u dc-gra-vt-bot -f"
