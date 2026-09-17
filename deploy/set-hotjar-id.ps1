param(
    [Parameter(Mandatory = $true)]
    [string]$SiteId
)
$ErrorActionPreference = "Stop"
if ($SiteId -notmatch '^(\d{5,12}|[a-fA-F0-9]{8,20})$') {
    throw "ID must be a numeric Hotjar site ID or a Clarity tag."
}
$local = Join-Path $PSScriptRoot "set-hotjar-id.py"
scp $local wellnav:/home/wellnav/Well_Navigation/deploy/set-hotjar-id.py
ssh -o BatchMode=yes wellnav "python3 /home/wellnav/Well_Navigation/deploy/set-hotjar-id.py $SiteId && cd /home/wellnav/Well_Navigation && docker compose -f docker-compose.yml up -d"
Write-Host "Hotjar site ID saved and container recreated."
