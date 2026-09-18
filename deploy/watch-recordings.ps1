# Open the local UX session player for files in data\ux-recordings.
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dest = Join-Path $repo "data\ux-recordings"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$env:WELLNAV_RECORDINGS_DIR = $dest
Set-Location $repo
Write-Host "Watching sessions in $dest"
python -m wellnav.replay
