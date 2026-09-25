import CoreLocation
import SwiftUI
import WebKit

struct WebContainer: UIViewRepresentable {
    let startURL: URL

    func makeCoordinator() -> Coordinator {
        Coordinator(startURL: startURL)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.defaultWebpagePreferences.preferredContentMode = .mobile
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.allowsInlineMediaPlayback = true
        config.applicationNameForUserAgent = "WellNavigation/1.0 (iOS; store)"
        // Runs before page scripts, including a cached shell, so a stuck
        // service worker cannot keep swallowing /operators and /search.
        config.userContentController.addUserScript(
            WKUserScript(
                source: Self.storeBootScript,
                injectionTime: .atDocumentStart,
                forMainFrameOnly: true
            )
        )
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = true
        view.allowsLinkPreview = false
        view.isOpaque = false
        view.backgroundColor = UIColor(red: 0.07, green: 0.09, blue: 0.06, alpha: 1)
        view.scrollView.backgroundColor = view.backgroundColor
        view.scrollView.contentInsetAdjustmentBehavior = .never
        let request = URLRequest(
            url: startURL,
            cachePolicy: .reloadRevalidatingCacheData,
            timeoutInterval: 30
        )
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        let defaults = UserDefaults.standard
        if defaults.string(forKey: "wn.uiCache") != version {
            let types: Set<String> = [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache]
            WKWebsiteDataStore.default().removeData(ofTypes: types, modifiedSince: Date(timeIntervalSince1970: 0)) {
                defaults.set(version, forKey: "wn.uiCache")
                view.load(request)
            }
        } else {
            view.load(request)
        }
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    // Keep in sync with templates/partials/store_boot.js.
    // Do not gate wait reports or map UX on __WN_STORE.
    private static let storeBootScript = """
    (function () {
      var ua = navigator.userAgent || "";
      var store = ua.indexOf("WellNavigation/") !== -1 && ua.toLowerCase().indexOf("store") !== -1;
      if (!store) return;
      window.__WN_STORE = true;
      function armCredentials() {
        if (window.htmx && window.htmx.config) window.htmx.config.withCredentials = true;
      }
      armCredentials();
      document.addEventListener("DOMContentLoaded", armCredentials);
      if (!("serviceWorker" in navigator)) return;
      try {
        navigator.serviceWorker.register = function () {
          return Promise.resolve(null);
        };
      } catch (err) {}
      var controlled = !!navigator.serviceWorker.controller;
      var tries = 0;
      try {
        tries = parseInt(sessionStorage.getItem("wn-store-sw-reset") || "0", 10) || 0;
      } catch (err) {
        tries = 2;
      }
      function dropWorkers() {
        return navigator.serviceWorker.getRegistrations().then(function (regs) {
          return Promise.all((regs || []).map(function (reg) { return reg.unregister(); })).then(function () {
            if (!window.caches || !caches.keys) return;
            return caches.keys().then(function (keys) {
              return Promise.all(keys.map(function (key) { return caches.delete(key); }));
            });
          });
        });
      }
      if (!controlled) {
        dropWorkers().catch(function () {});
        return;
      }
      if (tries >= 2) return;
      try {
        sessionStorage.setItem("wn-store-sw-reset", String(tries + 1));
      } catch (err) {}
      dropWorkers().then(function () { window.location.reload(); }).catch(function () {});
    })();
    """

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, CLLocationManagerDelegate {
        let startURL: URL
        /// Retained for the WebView lifetime. WKWebView's Geolocation API uses the
        /// host app's Core Location When In Use authorization (Info.plist usage
        /// string). Holding a manager keeps the framework active when the page
        /// calls navigator.geolocation to estimate drive time for disposal directions.
        private let locationManager = CLLocationManager()

        init(startURL: URL) {
            self.startURL = startURL
            super.init()
            locationManager.delegate = self
            // Prompt When In Use so WKWebView navigator.geolocation can resolve
            // drive-time estimates when opening disposal directions. WebKit shares this status.
            if locationManager.authorizationStatus == .notDetermined {
                locationManager.requestWhenInUseAuthorization()
            }
        }

        func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
            // WebKit observes the same authorization state for Geolocation.
            if manager.authorizationStatus == .notDetermined {
                manager.requestWhenInUseAuthorization()
            }
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }
            if shouldOpenExternally(url) {
                UIApplication.shared.open(browserURL(url))
                decisionHandler(.cancel)
                return
            }
            if isAllowed(url) {
                decisionHandler(.allow)
                return
            }
            // https links off the app host open in Safari (permit PDFs, maps).
            let topLevel = navigationAction.targetFrame == nil || navigationAction.targetFrame?.isMainFrame == true
            if topLevel, url.scheme?.lowercased() == "https" {
                UIApplication.shared.open(url)
            }
            decisionHandler(.cancel)
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url, shouldOpenExternally(url) {
                UIApplication.shared.open(browserURL(url))
            } else if let url = navigationAction.request.url, isAllowed(url) {
                webView.load(URLRequest(url: url))
            } else if let url = navigationAction.request.url, url.scheme?.lowercased() == "https" {
                UIApplication.shared.open(url)
            }
            return nil
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            presentRetry(on: webView, message: error.localizedDescription)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            presentRetry(on: webView, message: error.localizedDescription)
        }

        private func isAllowed(_ url: URL) -> Bool {
            guard let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "about" else {
                return url.scheme == "about"
            }
            if url.scheme == "about" { return true }
            guard let host = url.host?.lowercased() else { return false }
            if isStripeHost(host) { return false }
            return AppConfig.allowedHosts.contains(host)
        }

        private func shouldOpenExternally(_ url: URL) -> Bool {
            let scheme = url.scheme?.lowercased() ?? ""
            if ["mailto", "tel", "sms"].contains(scheme) { return true }
            let host = url.host?.lowercased() ?? ""
            if isStripeHost(host) { return true }
            if wantsSystemBrowser(url) { return true }
            if host == "maps.apple.com" || host.contains("maps.google.") { return true }
            if host.hasSuffix("google.com") && url.path.contains("/maps") { return true }
            return false
        }

        private func isStripeHost(_ host: String) -> Bool {
            host == "stripe.com" || host.hasSuffix(".stripe.com")
        }

        private func wantsSystemBrowser(_ url: URL) -> Bool {
            URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?
                .contains(where: { $0.name == "external_browser" && $0.value != "0" }) == true
        }

        private func browserURL(_ url: URL) -> URL {
            guard var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
                return url
            }
            parts.queryItems = parts.queryItems?.filter { $0.name != "external_browser" }
            if parts.queryItems?.isEmpty == true {
                parts.queryItems = nil
            }
            return parts.url ?? url
        }

        private static func escape(_ text: String) -> String {
            text
                .replacingOccurrences(of: "&", with: "&amp;")
                .replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;")
        }

        private func presentRetry(on webView: WKWebView, message: String) {
            let html = """
            <!doctype html><html><head>
            <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
            <style>
              body { margin:0; min-height:100dvh; display:flex; align-items:center; justify-content:center;
                     background:#12160f; color:#e8ecd9; font: 17px -apple-system, sans-serif; padding: 28px; text-align:center; }
              p { color:#9aa386; }
              a { color:#d4a017; }
            </style></head>
            <body>
              <div>
                <div style="font-size:40px;color:#d4a017">◆</div>
                <h1>Can't reach Well Navigation</h1>
                <p>\(Self.escape(message))</p>
                <p>If you already opened the app once, saved USGS map tiles and pinned wells stay on this device. Search still needs a connection.</p>
                <p><a href="\(startURL.absoluteString)">Try again</a></p>
              </div>
            </body></html>
            """
            webView.loadHTMLString(html, baseURL: startURL)
        }
    }
}
