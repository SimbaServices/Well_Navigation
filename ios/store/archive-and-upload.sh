#!/bin/zsh
# Run on a Mac with Xcode 16+ after signing in to the Apple Developer Team.
# Usage: cd ios && zsh store/archive-and-upload.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
xcodebuild -version
xcodebuild \
  -project WellNavigation.xcodeproj \
  -scheme WellNavigation \
  -destination "generic/platform=iOS" \
  -archivePath "$ROOT/build/WellNavigation.xcarchive" \
  -allowProvisioningUpdates \
  archive
xcodebuild \
  -exportArchive \
  -archivePath "$ROOT/build/WellNavigation.xcarchive" \
  -exportOptionsPlist "$ROOT/store/ExportOptions.plist" \
  -exportPath "$ROOT/build/export" \
  -allowProvisioningUpdates
echo "IPA at $ROOT/build/export. Upload with Organizer or:"
echo "  xcrun altool --upload-app -f $ROOT/build/export/*.ipa -t ios --apiKey KEY --apiIssuer ISSUER"
