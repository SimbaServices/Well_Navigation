#!/bin/zsh
# Run on a Mac with Xcode 26+ after signing in to the Apple Developer Team.
# Usage: cd ios && zsh store/archive-and-upload.sh
#
# Optional, for CI (all three, or none). When set, signing uses an App Store
# Connect API key instead of the login keychain. The .p8 is never printed.
#   APP_STORE_CONNECT_KEY_ID
#   APP_STORE_CONNECT_ISSUER_ID
#   APP_STORE_CONNECT_API_KEY   full .p8 PEM, including BEGIN PRIVATE KEY
# SKIP_APP_STORE_UPLOAD=1 exports a local IPA (debug). Unset uploads via
# store/ExportOptions.plist (destination: upload).
set -euo pipefail
set +x
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

key_file=""
tmp_export_plist=""
export_plist="$ROOT/store/ExportOptions.plist"

cleanup() {
  if [[ -n "$key_file" ]]; then
    rm -f "$key_file"
  fi
  if [[ -n "$tmp_export_plist" ]]; then
    rm -f "$tmp_export_plist"
  fi
}
trap cleanup EXIT

key_id="${APP_STORE_CONNECT_KEY_ID:-}"
issuer_id="${APP_STORE_CONNECT_ISSUER_ID:-}"
api_key="${APP_STORE_CONNECT_API_KEY:-}"
auth_args=()

if [[ -n "$key_id" || -n "$issuer_id" || -n "$api_key" ]]; then
  if [[ -z "$key_id" || -z "$issuer_id" || -z "$api_key" ]]; then
    echo "Set all of APP_STORE_CONNECT_KEY_ID, APP_STORE_CONNECT_ISSUER_ID, and APP_STORE_CONNECT_API_KEY, or leave all three unset."
    exit 1
  fi
  key_file="/tmp/AuthKey.p8"
  rm -f "$key_file"
  old_umask="$(umask)"
  umask 077
  printf '%s\n' "$api_key" > "$key_file"
  chmod 600 "$key_file"
  umask "$old_umask"
  unset api_key
  unset APP_STORE_CONNECT_API_KEY
  if ! grep -q "PRIVATE KEY" "$key_file"; then
    echo "APP_STORE_CONNECT_API_KEY does not look like a .p8 (missing PRIVATE KEY header)."
    exit 1
  fi
  echo "Using App Store Connect API key for signing."
  auth_args=(
    -authenticationKeyPath "$key_file"
    -authenticationKeyID "$key_id"
    -authenticationKeyIssuerID "$issuer_id"
  )
fi

if [[ "${SKIP_APP_STORE_UPLOAD:-}" == "1" ]]; then
  tmp_export_plist="$(mktemp /tmp/ExportOptions.XXXXXX)"
  cp "$ROOT/store/ExportOptions.plist" "$tmp_export_plist"
  /usr/libexec/PlistBuddy -c "Set :destination export" "$tmp_export_plist"
  export_plist="$tmp_export_plist"
  echo "App Store Connect upload skipped. IPA export only."
fi

xcodebuild -version
xcodebuild \
  -project WellNavigation.xcodeproj \
  -scheme WellNavigation \
  -destination "generic/platform=iOS" \
  -archivePath "$ROOT/build/WellNavigation.xcarchive" \
  -allowProvisioningUpdates \
  "${auth_args[@]}" \
  archive
xcodebuild \
  -exportArchive \
  -archivePath "$ROOT/build/WellNavigation.xcarchive" \
  -exportOptionsPlist "$export_plist" \
  -exportPath "$ROOT/build/export" \
  -allowProvisioningUpdates \
  "${auth_args[@]}"

if [[ -n "$key_file" && "${SKIP_APP_STORE_UPLOAD:-}" != "1" ]]; then
  echo "Export used store/ExportOptions.plist (destination: upload) for App Store Connect."
else
  echo "IPA at $ROOT/build/export. Upload with Organizer or:"
  echo "  xcrun altool --upload-app -f $ROOT/build/export/*.ipa -t ios --apiKey KEY --apiIssuer ISSUER"
fi
