# Well Navigation Android / Play Store wrapper

Same idea as the iOS app: a `WebView` pointed at `https://wellnav.simba.services`, not a general-purpose browser. There is no address bar. Navigation stays on that host, plus `mailto:` / `tel:` / Google Maps.

Stripe Checkout, the Customer Portal, and any URL with `?external_browser=1` open in **Chrome** (or the default browser). The WebView never loads `*.stripe.com`. The wrapper identifies itself as `WellNavigation/1.0 (Android; store)` so the website hides purchase forms.

Offline maps are handled by the **website**, not by Google Maps SDK:

1. Sign in once while online.
2. Pin one or more wells or spots and tap **Save for offline** on the Map tab (routes from your location, plus USGS tiles along them).
3. After that, losing signal still shows the pins, the saved routes, and the USGS tiles.
4. Esri imagery/streets/topo stay online-only (their terms do not allow us to cache them).
5. Turn-by-turn still opens the Google Maps app.

## Open in Android Studio

1. Android Studio (Koala / 2024.1 or newer) → Open → this `android/` folder.
2. Let Gradle sync. Application id is `services.simba.wellnav`.
3. Set your Play signing key in Gradle or Play App Signing.
4. Build a signed AAB from this folder: `gradlew.bat bundleRelease`.
   Output: `android/app/build/outputs/bundle/release/app-release.aab`.
   Upload keystore (do not commit): `C:\Simba\secrets\wellnav-upload.jks`.

## Suggested listing copy

**Short description:** Well and Pipeline Search

**Full description:** paste from `android/store/listing.txt` (Play Console 4000-character limit).

## Play Console

- Privacy policy: `https://wellnav.simba.services/privacy`
- Terms: `https://wellnav.simba.services/terms`
- **Billing:** no Play Billing / no in-app products. Organization seats are a workplace subscription sold on the website (Google Play Payments policy enterprise / workplace exception). `@simba.services` accounts are complimentary.
- Account deletion: Account → Delete account (same as iOS). Do not add Google Sign-In unless you also add it on the web.
- Data safety: account email and user ID (app functionality, linked, not sold); other user content (saved wells); IP address in server logs. No advertising ID. Optional approximate and precise location (app functionality, not linked, not sold), used when the user saves offline routes to pinned places and when they open directions to a disposal site to estimate drive time. The GPS fix is not stored on the server.
- High-res icon: `android/store/icon-512.png` (also `static/app-icon.png` at 1024). Feature graphic: `android/store/feature-graphic.png`.
- Screenshots: `android/store/screenshots/` — exact 9:16 24-bit PNG: phone 1080×1920, 7-inch 1440×2560, 10-inch 1800×3200. Recapture with `python android/store/capture-screenshots.py`. Paths are in `android/store/listing.txt`.
- Content rating: IARC questionnaire — business utility, no user-generated social, no sharing location.

## Review notes (paste into Play Console)

Well Navigation is workplace software sold to oil and gas organizations for their employees. The Play app does not sell digital goods and does not use Google Play Billing.

Organization admins buy seats on https://wellnav.simba.services in the system browser. Stripe Checkout for MP Solutions is blocked inside the WebView. This is enterprise / workplace software, not a consumer subscription.

The app is not a general-purpose browser (no address bar; host allow-list). Offline routes, pins, and USGS tiles remain on device after the user taps Save for offline.

Account deletion: Account → Delete account.

## What this project cannot do from this repo alone

A Play Console upload requires a release keystore and your Google Play developer account. This repo ships the Android Studio project and launcher icons.
