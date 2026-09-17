package services.simba.wellnav

import android.annotation.SuppressLint
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private lateinit var banner: TextView
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.web)
        banner = findViewById(R.id.offline_banner)

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
    }
}
