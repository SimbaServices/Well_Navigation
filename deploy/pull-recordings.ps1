# Copy Simba UX session recordings from the live host to this laptop,
# then remove the pulled files from the server.
$ErrorActionPreference = "Stop"
$dest = Join-Path $PSScriptRoot "..\data\ux-recordings"
$remoteDir = "/home/wellnav/Well_Navigation/ux-recordings"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Write-Host "Listing recordings on wellnav..."
ssh -o BatchMode=yes wellnav "mkdir -p $remoteDir; ls -lh $remoteDir"

$remote = ssh -o BatchMode=yes wellnav "ls $remoteDir"
if (-not $remote) {
    Write-Host "Nothing to pull."
    exit 0
}

$names = @(
    $remote -split "[\r\n]+" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^[A-Za-z0-9._-]{1,80}$' }
)
if ($names.Count -eq 0) {
    Write-Host "Nothing to pull."
    exit 0
}

Write-Host "Copying $($names.Count) file(s) to $dest"
scp -r "wellnav:${remoteDir}/." $dest
if ($LASTEXITCODE -ne 0) {
    throw "Copy failed; leaving files on the server."
}

$missing = @()
foreach ($name in $names) {
    $local = Join-Path $dest $name
    if (-not (Test-Path -LiteralPath $local)) {
        $missing += $name
    }
}
if ($missing.Count -gt 0) {
    Write-Host "Copy incomplete; leaving files on the server:"
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

$safe = @($names | Where-Object { $_ -match '^[a-f0-9]{16,32}\.(json|jsonl|webm)$' })
if ($safe.Count -ne $names.Count) {
    Write-Host "Skipped unexpected remote names; those stay on the server."
}

if ($safe.Count -gt 0) {
    Write-Host "Removing $($safe.Count) pulled file(s) from wellnav..."
    $quoted = ($safe | ForEach-Object { "'$_'" }) -join " "
    ssh -o BatchMode=yes wellnav "cd $remoteDir && rm -f -- $quoted"
    if ($LASTEXITCODE -ne 0) {
        throw "Copied locally, but the server delete failed. Check $remoteDir."
    }
}

Write-Host "Remaining on wellnav:"
ssh -o BatchMode=yes wellnav "ls -lh $remoteDir"
Get-ChildItem $dest | Sort-Object LastWriteTime -Descending | Select-Object Name, Length, LastWriteTime
Write-Host "Done."
