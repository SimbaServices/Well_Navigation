# Point this machine's Docker CLI at the live Hetzner engine over SSH.
# After this, `docker compose -f docker-compose.yml` talks to the public
# wellnav container and the well_navigation_wellnav-data volume.
$ErrorActionPreference = "Stop"

docker context inspect wellnav 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    docker context create wellnav --docker "host=ssh://wellnav"
}

docker context use wellnav
docker info --format "Server: {{.Name}}  {{.ServerVersion}}"
Write-Host "Docker CLI is peering at the live host. Use:"
Write-Host "  docker compose -f docker-compose.yml ps"
Write-Host "  docker compose -f docker-compose.yml up -d --build --force-recreate --no-deps web"
