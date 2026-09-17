#!/bin/bash
set -e
ROOT=/home/wellnav/Well_Navigation
ENV="$ROOT/.env"
touch "$ENV"
if grep -q '^WELLNAV_SEAT_PRICE_CENTS=' "$ENV"; then
  sed -i 's/^WELLNAV_SEAT_PRICE_CENTS=.*/WELLNAV_SEAT_PRICE_CENTS=1500/' "$ENV"
else
  printf '\nWELLNAV_SEAT_PRICE_CENTS=1500\n' >> "$ENV"
fi
if grep -q '^WELLNAV_STRIPE_PRICE_LOOKUP_KEY=' "$ENV"; then
  sed -i 's/^WELLNAV_STRIPE_PRICE_LOOKUP_KEY=.*/WELLNAV_STRIPE_PRICE_LOOKUP_KEY=wellnav_seat_monthly/' "$ENV"
else
  printf 'WELLNAV_STRIPE_PRICE_LOOKUP_KEY=wellnav_seat_monthly\n' >> "$ENV"
fi
cd "$ROOT"
python deploy/ensure-stripe-price.py | tee /tmp/wellnav-stripe-price.txt
PRICE_ID=$(awk -F= '/^price_id=/{print $2}' /tmp/wellnav-stripe-price.txt)
if [ -n "$PRICE_ID" ]; then
  if grep -q '^WELLNAV_STRIPE_PRICE_ID=' "$ENV"; then
    sed -i "s/^WELLNAV_STRIPE_PRICE_ID=.*/WELLNAV_STRIPE_PRICE_ID=$PRICE_ID/" "$ENV"
  else
    printf 'WELLNAV_STRIPE_PRICE_ID=%s\n' "$PRICE_ID" >> "$ENV"
  fi
fi
grep -E '^WELLNAV_SEAT_PRICE_CENTS=|^WELLNAV_STRIPE_PRICE_LOOKUP_KEY=|^WELLNAV_STRIPE_PRICE_ID=' "$ENV"
