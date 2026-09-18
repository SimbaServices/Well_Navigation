# Well Navigation iOS wrapper

Thin SwiftUI + `WKWebView` shell. It is **not** a general-purpose browser: there is no URL bar, and navigation is limited to `https://wellnav.simba.services` plus `mailto:` / Maps.

Stripe Checkout, the Customer Portal, and any URL with `?external_browser=1` open in **Safari**. The WebView never loads `*.stripe.com`. The wrapper identifies itself as `WellNavigation/1.0 (iOS; store)` so the website hides purchase forms, analytics, and internal UX recordings.

Start URL is `WELLNAVStartURL` in `WellNavigation/Info.plist`. ATS does not allow arbitrary HTTP.

## Offline maps (field use)

The wrapper does not tear down the WebView when cellular drops. Offline mapping lives in the website:

1. Open the Map tab while you still have signal.
2. Pin the wells you need.
3. Tap **Save this view**. That stores USGS topo/imagery tiles plus the last pipeline and waste-site overlay for that area on the device.
4. Esri layers stay online-only — their terms do not allow us to cache them. When you go offline the map switches to the saved USGS tiles.
5. Apple Maps / Google Maps links still open the system apps.

Search, sign-in, and new well lookups still need a network.

## App Store Connect checklist

Paste copy from `ios/store/listing.txt`. Age-rating answers: `ios/store/age-rating.txt`. Review notes: `ios/store/review-notes.txt`. What's New: `ios/store/whats-new.txt`.

1. Apple Developer Program team is enrolled. Bundle ID `services.simba.wellnav` is registered (change it in Xcode if Apple says it is taken).
2. App Store Connect → New App: iOS, name **Well Navigation**, SKU `wellnav-ios`, primary language English (U.S.).
3. **No IAP. No Sign in with Apple.** Login is work email and password only.
4. Privacy URL `https://wellnav.simba.services/privacy`. Terms `https://wellnav.simba.services/terms`. Support URL `https://wellnav.simba.services`.
5. Age rating 4+ from `ios/store/age-rating.txt`. Category Business. Price Free. Copyright `2026 Simba Services`.
6. Privacy nutrition: email, user ID, other user content (saved wells), IP address. No tracking. No advertising.
7. Export compliance: HTTPS only (`ITSAppUsesNonExemptEncryption` is false).
8. Screenshots: the eight PNGs in `ios/store/screenshots/` (iPhone 6.9" 1290×2796 and iPad 13" 2048×2732).
9. App Review Information: demo `appreview@simba.services` plus the password in `C:\Simba\wellnav-app-review-login.txt`. Account deletion: Account → Delete account → password + type DELETE.

## Before you archive

1. Confirm `https://wellnav.simba.services` serves the live site over HTTPS.
2. Open `ios/WellNavigation.xcodeproj` on a Mac with **Xcode 26 or later** (required for App Store uploads since 28 April 2026; iOS 26 SDK). Deployment target stays 16.0.
3. Signing & Capabilities → your Team. Automatic signing is already on. Bundle ID is `services.simba.wellnav`.
4. Destination → **Any iOS Device (arm64)** → Product → Archive, or run `zsh store/archive-and-upload.sh` from `ios/`.
5. Distribute App → App Store Connect → Upload. Use a Distribution certificate and an App Store profile (Xcode Automatic signing creates these when the Team is enrolled).
6. Select the build, attach screenshots, paste listing copy, then Submit for Review.

First review is often 24–72 hours. Guideline **3.1.1** (payment in a WebView), **4.2** (minimum native chrome), and **5.1.1** (account deletion) are the usual bounce reasons; payment stays in Safari, and the other two are implemented here.

## Suggested listing copy

**Subtitle:** Well and Pipeline Search

**Description / keywords / promotional text:** `ios/store/listing.txt`

**Keywords:** well,pipeline,oil,gas,RRC,Texas,New Mexico,Oklahoma,Louisiana,API,disposal

## What this project cannot do from Windows

Certificate creation, archive, upload, and review submission require your Apple Developer account and a Mac. This repo ships the Xcode project, 1024×1024 RGB icon, listing copy, and store-sized screenshots.
