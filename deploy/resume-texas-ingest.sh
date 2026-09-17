#!/bin/bash
set -euo pipefail
COUNTIES="439 441 445 447 449 451 453 455 457 459 461 463 465 467 469 471 473 475 477 479 481 483 485 487 489 491 493 495 497 499 501 503 505 507"
docker rm -f wellnav-ingest >/dev/null 2>&1 || true
docker run -d --name wellnav-ingest --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-texas --workers 2 --delay 0.2 --counties $COUNTIES
sleep 3
docker ps --filter name=wellnav-ingest
echo '--- log ---'
docker logs --tail 40 wellnav-ingest
