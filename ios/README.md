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

Paste copy from `ios/store/listing.txt`. Age-rating answers: `ios/store/age-rating.txt`. Review notes: `ios/store/review-notes.txt`. What's New: `ios/store/whats-new.txt`. Physical-device recording shot list: `ios/store/recording-script.txt`.

1. Apple Developer Program team is enrolled. Bundle ID `services.simba.wellnav` is registered (change it in Xcode if Apple says it is taken).
2. App Store Connect → New App: iOS, name **Well Navigation**, SKU `wellnav-ios`, primary language English (U.S.).
3. **No IAP. No Sign in with Apple.** Login is work email and password only. Public App Store distribution: any oil and gas professional can register with a work email (not an internal employee-only app).
4. Privacy URL `https://wellnav.simba.services/privacy`. Terms `https://wellnav.simba.services/terms`. Support URL `https://wellnav.simba.services`.
5. Age rating 4+ from `ios/store/age-rating.txt`. Category Business. Price Free. Copyright `2026 Simba Services`.
6. Privacy nutrition: email, user ID, other user content (saved wells), IP address. No tracking. No advertising.
7. Export compliance: HTTPS only (`ITSAppUsesNonExemptEncryption` is false).
8. Screenshots: real in-app shots from `ios/store/screenshots/` (search, results, map, account — not splash-only). Upload paths in `ios/store/listing.txt` (`upload-iphone/` or `upload-iphone-1242/`, plus `ipad-13-*`).
9. App Review Information: paste `ios/store/review-notes.txt` into **both** the Resolution Center reply and the Notes field. Demo user `appreview@simba.services`; password from `ios/store/review-notes.local.txt` (gitignored) or `C:\Simba\wellnav-app-review-login.txt` — never commit it. Sign-in: open app → Sign in → work email and password. Account deletion: Account → Delete account → password → type DELETE. Do **not** delete `appreview@simba.services`.
10. Guideline 2.1 recording: follow `ios/store/recording-script.txt` on a **physical** iPhone on the latest iOS (Simulator rejected). Start recording before the icon tap. Sign in as `appreview@simba.services` for search, map, and Save this view. Register and delete a **disposable** account only. Do not buy seats in the recording. No video file in the repo.
11. Before Submit for Review: TestFlight smoke test on a physical iPhone (and iPad if that destination is enabled) on the latest iOS — sign in as the demo account, search, Save this view, Account (no IAP), then register and delete a disposable account.

## Before you archive

1. Confirm `https://wellnav.simba.services` serves the live site over HTTPS.
2. Open `ios/WellNavigation.xcodeproj` on a Mac with **Xcode 26 or later** (required for App Store uploads since 28 April 2026; iOS 26 SDK). Deployment target stays 16.0.
3. Signing & Capabilities → your Team. Automatic signing is already on. Bundle ID is `services.simba.wellnav`.
4. Destination → **Any iOS Device (arm64)** → Product → Archive, or run `zsh store/archive-and-upload.sh` from `ios/`. Without a Mac, use **CI signing** below.
5. Distribute App → App Store Connect → Upload. Use a Distribution certificate and an App Store profile (Xcode Automatic signing creates these when the Team is enrolled). The CI workflow does this upload for you.
6. Select the build, attach screenshots, paste listing copy, then Submit for Review.

First review is often 24–72 hours. Guideline **3.1.1** (payment in a WebView), **4.2** (minimum native chrome), and **5.1.1** (account deletion) are the usual bounce reasons; payment stays in Safari, and the other two are implemented here.

## CI signing

`.github/workflows/ios-sign.yml` runs on GitHub’s hosted `xcode-27` preview runner (macOS 27, Xcode 27). It does not download a macOS image. The job archives with `zsh store/archive-and-upload.sh` and uploads to App Store Connect. Run it with `gh workflow run ios-sign.yml`. It also runs on push to `ios/**` on `main`.

Repository secrets (Settings → Secrets and variables → Actions). Do not commit the `.p8`.

- `APP_STORE_CONNECT_KEY_ID`
- `APP_STORE_CONNECT_ISSUER_ID`
- `APP_STORE_CONNECT_API_KEY` — full `.p8` text, including `BEGIN PRIVATE KEY`

Create the key in App Store Connect → Users and Access → Integrations → App Store Connect API. Role: Admin or App Manager. Download the `.p8` once. Copy the Key ID and the Issuer ID.

One-time, before the first run: accept the Apple Developer team agreements, and create the Well Navigation app with bundle ID `services.simba.wellnav`.

## Suggested listing copy

**Subtitle:** Well and Pipeline Search

**Description / keywords / promotional text:** `ios/store/listing.txt`

**Keywords:** well,pipeline,oil,gas,RRC,Texas,New Mexico,Oklahoma,Louisiana,API,disposal

## What this project cannot do from Windows

Certificate creation, archive, and upload do not run on Windows. Use **CI signing** (GitHub Actions on a Mac runner) or a Mac with Xcode 26+. Review submission stays in App Store Connect. This repo ships the Xcode project, 1024×1024 RGB icon, listing copy, and store-sized screenshots.
