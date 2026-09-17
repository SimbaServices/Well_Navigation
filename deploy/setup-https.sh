#!/usr/bin/env bash
# Point wellnav.simba.services at this host, then run as root on the box.
set -euo pipefail
DOMAIN="${WELLNAV_DOMAIN:-wellnav.simba.services}"
EMAIL="${WELLNAV_CERT_EMAIL:-wellnav@simba.services}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p /var/www/certbot
cp "$ROOT/deploy/nginx/wellnav.conf" /etc/nginx/sites-available/wellnav
ln -sfn /etc/nginx/sites-available/wellnav /etc/nginx/sites-enabled/wellnav
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

if ! command -v certbot >/dev/null; then
  apt-get update
  apt-get install -y certbot python3-certbot-nginx
fi

certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

# Session cookies stay Secure only when WELLNAV_HTTPS=1 in the container env.
if [ -f /home/wellnav/Well_Navigation/.env ]; then
  grep -q '^WELLNAV_HTTPS=' /home/wellnav/Well_Navigation/.env \
    && sed -i 's/^WELLNAV_HTTPS=.*/WELLNAV_HTTPS=1/' /home/wellnav/Well_Navigation/.env \
    || echo 'WELLNAV_HTTPS=1' >> /home/wellnav/Well_Navigation/.env
  grep -q '^WELLNAV_PUBLIC_URL=' /home/wellnav/Well_Navigation/.env \
    && sed -i "s|^WELLNAV_PUBLIC_URL=.*|WELLNAV_PUBLIC_URL=https://$DOMAIN|" /home/wellnav/Well_Navigation/.env \
    || echo "WELLNAV_PUBLIC_URL=https://$DOMAIN" >> /home/wellnav/Well_Navigation/.env
fi
echo "Set WELLNAV_HTTPS=1 and WELLNAV_PUBLIC_URL=https://$DOMAIN, then:"
echo "  docker compose -f docker-compose.yml up -d"

echo "HTTPS ready for https://$DOMAIN"
