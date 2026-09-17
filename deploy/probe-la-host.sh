#!/bin/bash
set -euo pipefail
echo "=== curl DOTD ==="
curl -sS -o /tmp/la.json -w "%{http_code} %{size_download}\n" \
  -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "https://maps.dotd.la.gov/ltrcserver/rest/services/LTRC_18_3GT/SONRIS/MapServer/0/query?where=1%3D1&returnCountOnly=true&f=pjson" || true
head -c 200 /tmp/la.json; echo
echo "=== curl Lincoln ==="
curl -sS -o /tmp/la2.json -w "%{http_code} %{size_download}\n" \
  -A "Mozilla/5.0" \
  "https://maps.lincolnparish.org/arcgis/rest/services/LP_Jackson_27_Pro/MapServer/20/query?where=1%3D1&returnCountOnly=true&f=pjson" || true
head -c 300 /tmp/la2.json; echo
