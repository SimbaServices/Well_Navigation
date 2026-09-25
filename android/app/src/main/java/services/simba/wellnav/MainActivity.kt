package services.simba.wellnav

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Message
import android.view.View
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
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
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var banner: TextView
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var pendingGeolocationOrigin: String? = null
    private var pendingGeolocationCallback: GeolocationPermissions.Callback? = null

    private val locationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
            val granted =
                results[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
                    results[Manifest.permission.ACCESS_COARSE_LOCATION] == true
            val origin = pendingGeolocationOrigin
            val callback = pendingGeolocationCallback
            pendingGeolocationOrigin = null
            pendingGeolocationCallback = null
            if (origin != null && callback != null) {
                callback.invoke(origin, granted, false)
            }
        }

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
        settings.setGeolocationEnabled(true)
        settings.setSupportMultipleWindows(true)
        settings.javaScriptCanOpenWindowsAutomatically = true
        settings.userAgentString = settings.userAgentString + " WellNavigation/1.0 (Android; store)"

        val prefs = getSharedPreferences("wellnav", MODE_PRIVATE)
        if (prefs.getInt("ui_cache", 0) < BuildConfig.VERSION_CODE) {
            webView.clearCache(true)
            prefs.edit().putInt("ui_cache", BuildConfig.VERSION_CODE).apply()
        }

        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(
                webView,
                STORE_BOOT_JS,
                setOf("https://wellnav.simba.services"),
            )
        }

        webView.webChromeClient =
            object : WebChromeClient() {
                override fun onGeolocationPermissionsShowPrompt(
                    origin: String?,
                    callback: GeolocationPermissions.Callback?,
                ) {
                    if (origin == null || callback == null) return
                    if (hasLocationPermission()) {
                        callback.invoke(origin, true, false)
                        return
                    }
                    pendingGeolocationOrigin = origin
                    pendingGeolocationCallback = callback
                    locationPermissionLauncher.launch(
                        arrayOf(
                            Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION,
                        ),
                    )
                }

                override fun onCreateWindow(
                    view: WebView,
                    isDialog: Boolean,
                    isUserGesture: Boolean,
                    resultMsg: Message,
                ): Boolean {
                    val clicked = view.hitTestResult.extra
                    val direct = clicked?.let { raw -> runCatching { Uri.parse(raw) }.getOrNull() }
                    if (direct != null && !direct.scheme.isNullOrEmpty()) {
                        if (!handleUrl(direct)) view.loadUrl(direct.toString())
                        return false
                    }
                    val transport = resultMsg.obj as? WebView.WebViewTransport ?: return false
                    val popup = WebView(this@MainActivity)
                    popup.webViewClient =
                        object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(
                                popupView: WebView,
                                request: WebResourceRequest,
                            ): Boolean {
                                val target = request.url
                                if (!handleUrl(target)) webView.loadUrl(target.toString())
                                popupView.post { popupView.destroy() }
                                return true
                            }
                        }
                    transport.webView = popup
                    resultMsg.sendToTarget()
                    return true
                }
            }
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                return handleUrl(request.url)
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                // WebView keeps new cookies in memory until flush. Without this,
                // closing the app drops the sign-in session.
                CookieManager.getInstance().flush()
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

    override fun onPause() {
        if (::webView.isInitialized) {
            CookieManager.getInstance().flush()
            webView.onPause()
        }
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        if (::webView.isInitialized) webView.onResume()
    }

    override fun onDestroy() {
        if (::webView.isInitialized) CookieManager.getInstance().flush()
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
        if (isMapHost(host, url) || isStripeHost(host) || wantsSystemBrowser(url)) {
            openExternal(stripExternalFlag(url))
            return true
        }
        if (scheme == "https" && host in ALLOWED_HOSTS) {
            return false
        }
        // https links off the app host open in the system browser (permit PDFs, maps).
        if (scheme == "https" && host.isNotEmpty()) {
            openExternal(url)
        }
        return true
    }

    private fun isMapHost(host: String, url: Uri): Boolean {
        if (host == "maps.apple.com" || host.contains("maps.google.")) return true
        return host.endsWith("google.com") && url.path?.contains("/maps") == true
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

    private fun hasLocationPermission(): Boolean {
        val fine =
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
        val coarse =
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION)
        return fine == PackageManager.PERMISSION_GRANTED ||
            coarse == PackageManager.PERMISSION_GRANTED
    }

    companion object {
        private const val START_URL = "https://wellnav.simba.services/"
        private val ALLOWED_HOSTS = setOf("wellnav.simba.services")

        // Keep in sync with templates/partials/store_boot.js.
        // Do not gate wait reports or map UX on __WN_STORE.
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
