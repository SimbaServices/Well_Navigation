# Well Navigation iOS wrapper

Thin SwiftUI + `WKWebView` shell. It is **not** a general-purpose browser: there is no URL bar, and navigation is limited to `https://wellnav.simba.services` plus `mailto:` / Maps.

Stripe Checkout, the Customer Portal, and any URL with `?external_browser=1` open in **Safari**. The WebView never loads `*.stripe.com`. The wrapper identifies itself as `WellNavigation/1.0 (iOS; store)` so the website hides purchase forms.

Start URL is `WELLNAVStartURL` in `WellNavigation/Info.plist`. ATS does not allow arbitrary HTTP.

## Offline maps (field use)

The wrapper does not tear down the WebView when cellular drops. Offline mapping lives in the website:

1. Open the Map tab while you still have signal.
2. Pin the wells you need.
3. Tap **Save this view**. That stores USGS topo/imagery tiles plus the last pipeline and waste-site overlay for that area on the device.
4. Esri layers stay online-only — their terms do not allow us to cache them. When you go offline the map switches to the saved USGS tiles.
5. Apple Maps / Google Maps links still open the system apps.

Search, sign-in, and new well lookups still need a network.

## Before you archive

1. Point DNS for `wellnav.simba.services` at the host and run `deploy/setup-https.sh` so the start URL actually serves HTTPS.
2. Open `ios/WellNavigation.xcodeproj` on a Mac with Xcode 15+.
3. Signing & Capabilities → your Team. Bundle ID is `services.simba.wellnav` (change it if that ID is taken).
4. Create the App Store Connect app with **no IAP** and **no Sign in with Apple** (email/password only). Privacy: `https://wellnav.simba.services/privacy`. Terms: `https://wellnav.simba.services/terms`.
5. Describe account deletion: Account → Delete account → password + type DELETE.

## Suggested listing copy

**Subtitle:** Well and Pipeline Search

**Description:**
Well Navigation is workplace software for oil and gas teams. Sign in with your work email to search and map wells, pipelines, and waste-disposal sites in Texas, New Mexico, Oklahoma, and Louisiana.

Organization admins buy seats on the website. This app does not sell subscriptions and does not collect payment. Use it in the field: pin wells, save a USGS map view for offline use, and open Apple Maps or Google Maps for turn-by-turn.

**Keywords:** well,pipeline,oil,gas,RRC,Texas,New Mexico,Oklahoma,Louisiana

## App Store Connect listing

- **Category:** Business.
- **Age rating:** 4+ (professional records; no user-generated social, no location permission).
- **Price:** Free. The app does not sell digital goods. Organization seats are a workplace subscription on the website.
- **In-app purchases:** none.
- **Privacy nutrition:** email, user ID, other user content (saved wells), IP address for server logs. No tracking. No advertising.
- **Export compliance:** HTTPS only (`ITSAppUsesNonExemptEncryption` is false).

## Review notes (paste into App Store Connect)

Well Navigation is workplace software for oil and gas organizations (Apple Guideline 3.1.3(c) enterprise / 3.1.3(b) multiplatform). Employees sign in with their work email to search wells, pipelines, and waste-disposal sites.

The iOS app does not sell subscriptions and does not collect payment. Organization admins buy seats on the website (https://wellnav.simba.services) in Safari. Stripe Checkout for MP Solutions never loads in the WKWebView. There are no In-App Purchases.

Account deletion is in-app: Account → Delete account.

The wrapper is not a general-purpose browser (no address bar; host allow-list). Field value beyond the website: offline USGS map tiles and pinned wells after the user taps Save this view, native splash, and an offline banner.

Demo login (complimentary, no payment): appreview@simba.services — password is in the operator file C:\Simba\wellnav-app-review-login.txt. Paste both into App Store Connect → App Review Information.

## Archive and upload

1. Destination → **Any iOS Device (arm64)**.
2. Product → Archive.
3. Distribute App → App Store Connect → Upload.
4. Use a **Distribution** certificate and an **App Store** provisioning profile (Xcode Automatic signing will create these if the Team is enrolled).
5. Add the PNGs in `ios/store/screenshots/` (iPhone 6.9" 1290×2796 and iPad 13" 2048×2732) and Submit for Review. Recapture with `python ios/store/capture-screenshots.py` then `python ios/store/capture-account.py` if the UI changes.

First review is often 24–72 hours. Guideline **3.1.1** (payment in a WebView), **4.2** (minimum native chrome), and **5.1.1** (account deletion) are the usual bounce reasons; payment stays in Safari, and the other two are implemented here.

## What this project cannot do from Windows

Certificate creation, archive, upload, and review submission require your Apple Developer account and a Mac. This repo only ships the Xcode project.
