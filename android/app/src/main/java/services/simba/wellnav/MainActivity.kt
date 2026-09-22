package services.simba.wellnav

import android.annotation.SuppressLint
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.ServiceWorkerClient
import android.webkit.ServiceWorkerController
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var banner: TextView
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Install before any WebView loads. Without this client, service-worker
        // fetch() inside the Play WebView fails and search calls never return.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            ServiceWorkerController.getInstance().setServiceWorkerClient(
                object : ServiceWorkerClient() {
                    override fun shouldInterceptRequest(request: WebResourceRequest): WebResourceResponse? {
                        return super.shouldInterceptRequest(request)
                    }
                },
            )
        }
        CookieManager.getInstance().setAcceptCookie(true)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.web)
        banner = findViewById(R.id.offline_banner)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.mediaPlaybackRequiresUserGesture = false
        settings.builtInZoomControls = true
        settings.displayZoomControls = false
        settings.allowFileAccess = false
        settings.allowContentAccess = false
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        settings.userAgentString = settings.userAgentString + " WellNavigation/1.0 (Android; store)"

        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(
                webView,
                STORE_BOOT_JS,
                setOf("https://wellnav.simba.services"),
            )
        }

        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                return handleUrl(request.url)
            }
        }

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (webView.canGoBack()) webView.goBack() else finish()
                }
            },
        )

        webView.loadUrl(START_URL)
        watchNetwork()
    }

    override fun onDestroy() {
        networkCallback?.let { callback ->
            getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(callback)
        }
        super.onDestroy()
    }

    private fun handleUrl(url: Uri): Boolean {
        val scheme = url.scheme?.lowercase().orEmpty()
        val host = url.host?.lowercase().orEmpty()
        if (scheme in setOf("mailto", "tel", "sms")) {
            openExternal(url)
            return true
        }
        if (host.contains("maps.google.") || (host.endsWith("google.com") && url.path?.contains("/maps") == true)) {
            openExternal(url)
            return true
        }
        if (isStripeHost(host) || wantsSystemBrowser(url)) {
            openExternal(stripExternalFlag(url))
            return true
        }
        if (scheme == "https" && host in ALLOWED_HOSTS) {
            return false
        }
        return true
    }

    private fun openExternal(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (_: Exception) {
        }
    }

    private fun isStripeHost(host: String): Boolean {
        return host == "stripe.com" || host.endsWith(".stripe.com")
    }

    private fun wantsSystemBrowser(url: Uri): Boolean {
        val flag = url.getQueryParameter("external_browser")
        return !flag.isNullOrEmpty() && flag != "0"
    }

    private fun stripExternalFlag(url: Uri): Uri {
        if (!wantsSystemBrowser(url)) return url
        val builder = url.buildUpon().clearQuery()
        url.queryParameterNames.forEach { name ->
            if (name != "external_browser") {
                url.getQueryParameters(name).forEach { value ->
                    builder.appendQueryParameter(name, value)
                }
            }
        }
        return builder.build()
    }

    private fun watchNetwork() {
        val manager = getSystemService(ConnectivityManager::class.java)
        val callback =
            object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    runOnUiThread { banner.visibility = View.GONE }
                }

                override fun onLost(network: Network) {
                    runOnUiThread { banner.visibility = View.VISIBLE }
                }
            }
        networkCallback = callback
        manager.registerNetworkCallback(
            NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(),
            callback,
        )
        banner.visibility = if (isOnline(manager)) View.GONE else View.VISIBLE
    }

    private fun isOnline(manager: ConnectivityManager): Boolean {
        val network = manager.activeNetwork ?: return false
        val caps = manager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    companion object {
        private const val START_URL = "https://wellnav.simba.services/"
        private val ALLOWED_HOSTS = setOf("wellnav.simba.services")

        // Keep in sync with templates/partials/store_boot.js.
        private const val STORE_BOOT_JS = """
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
    }
}
