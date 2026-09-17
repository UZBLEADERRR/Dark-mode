package uz.sarideo.phone

import android.Manifest
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Log
import android.view.View
import android.webkit.MimeTypeMap
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLDecoder
import kotlin.concurrent.thread

/**
 * The window onto the server running inside this same process.
 *
 * There is no remote anything here: the page, the API it calls and the ffmpeg
 * that answers it are all on this phone, and the WebView is pointed at the
 * loopback address the service prints once Python has bound it.
 */
class MainActivity : AppCompatActivity() {

    private val main = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var splash: View
    private lateinit var splashText: TextView

    private var pendingFileChooser: ValueCallback<Array<Uri>>? = null

    private val pickFiles = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = pendingFileChooser ?: return@registerForActivityResult
        pendingFileChooser = null
        callback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
    }

    private val askNotifications = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Declining costs the progress notification, not the render. */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        web = findViewById(R.id.web)
        splash = findViewById(R.id.splash)
        splashText = findViewById(R.id.splash_text)

        requestNotificationPermission()
        ContextCompat.startForegroundService(this, Intent(this, SarideoService::class.java))

        configureWebView()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        waitForServer(attempt = 0)
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
        if (granted != PackageManager.PERMISSION_GRANTED) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    /**
     * Poll the loopback socket until it answers, then show the page.
     *
     * The first run is the slow one — CPython starts, the payload is unpacked
     * and FastAPI imports a hundred routes — so the wait is generous and the
     * screen says what is happening rather than showing a blank WebView.
     */
    private fun waitForServer(attempt: Int) {
        thread(isDaemon = true) {
            val up = SarideoService.isListening()
            main.post {
                val error = SarideoService.failure
                when {
                    up -> showPage()
                    error != null -> splashText.text = getString(R.string.splash_failed, error)
                    // 300 attempts at 400 ms is two minutes, which no cold start
                    // has any business exceeding.
                    attempt > 300 -> splashText.text = getString(R.string.splash_slow)
                    else -> {
                        if (attempt == 12) splashText.text = getString(R.string.splash_first_run)
                        main.postDelayed({ waitForServer(attempt + 1) }, 400)
                    }
                }
            }
        }
    }

    private fun showPage() {
        if (web.url != null) return
        web.loadUrl(SarideoService.serverUrl())
        splash.visibility = View.GONE
        web.visibility = View.VISIBLE
    }

    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(false)
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            // Nothing here loads a `file://` page; the UI is served over the
            // loopback socket like any other page, and leaving these off keeps
            // a scripted page from reading the app's own storage.
            allowFileAccess = false
            allowContentAccess = false
            useWideViewPort = true
            loadWithOverviewMode = true
        }

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest): Boolean {
                val url = request.url
                // The app's own pages stay in the WebView. A link out — a
                // YouTube video, a provider's key page — belongs in the browser.
                if (url.host == "127.0.0.1") return false
                startActivity(Intent(Intent.ACTION_VIEW, url))
                return true
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?,
            ): Boolean {
                // Hero photos, your own narration, your own music — every upload
                // the app offers arrives through here.
                val chooser = params?.createIntent() ?: return false
                pendingFileChooser?.onReceiveValue(null)
                pendingFileChooser = callback
                return try {
                    pickFiles.launch(chooser)
                    true
                } catch (t: Throwable) {
                    // No app on the phone answers this kind of pick. Returning
                    // false lets the WebView fall back rather than leaving the
                    // page waiting on a callback that never arrives.
                    pendingFileChooser = null
                    callback?.onReceiveValue(null)
                    false
                }
            }
        }

        web.setDownloadListener { url, _, disposition, mimeType, _ ->
            saveDownload(url, disposition, mimeType)
        }
    }

    /**
     * Put a finished file where the phone's own apps can open it.
     *
     * Android's DownloadManager is a separate process and will not fetch from
     * this app's loopback socket, so the file is fetched here and written into
     * the shared Downloads collection — which is what makes a rendered MP4
     * appear in the gallery and the file manager rather than staying locked
     * inside the app.
     */
    private fun saveDownload(url: String, disposition: String?, mimeType: String?) {
        val name = fileNameFor(url, disposition, mimeType)
        Toast.makeText(this, getString(R.string.saving, name), Toast.LENGTH_SHORT).show()
        thread(isDaemon = true) {
            val message = try {
                val where = streamToDownloads(url, name, mimeType)
                getString(R.string.saved, where)
            } catch (t: Throwable) {
                Log.e("Sarideo", "download failed", t)
                getString(R.string.save_failed, t.message ?: "")
            }
            main.post { Toast.makeText(this, message, Toast.LENGTH_LONG).show() }
        }
    }

    /**
     * Copy the response straight into the destination, a buffer at a time.
     *
     * A finished video is hundreds of megabytes and reading one into a byte
     * array would end the process before it ended the download.
     */
    private fun streamToDownloads(url: String, name: String, mimeType: String?): String {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 120_000
        }
        try {
            connection.inputStream.use { input ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    val values = ContentValues().apply {
                        put(MediaStore.Downloads.DISPLAY_NAME, name)
                        if (!mimeType.isNullOrBlank()) put(MediaStore.Downloads.MIME_TYPE, mimeType)
                        put(MediaStore.Downloads.IS_PENDING, 1)
                    }
                    val resolver = contentResolver
                    val target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                        ?: throw IllegalStateException("Downloads")
                    resolver.openOutputStream(target)!!.use { input.copyTo(it, 256 * 1024) }
                    values.clear()
                    values.put(MediaStore.Downloads.IS_PENDING, 0)
                    resolver.update(target, values, null, null)
                    return "Downloads/$name"
                }
                // Before Android 10 the shared Downloads folder needs a storage
                // permission this app does not otherwise want, so the file goes
                // to the app's own external folder, which a file manager can
                // still reach.
                val folder = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
                    ?: throw IllegalStateException("Downloads")
                folder.mkdirs()
                val file = File(folder, name)
                file.outputStream().use { input.copyTo(it, 256 * 1024) }
                return file.absolutePath
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun fileNameFor(url: String, disposition: String?, mimeType: String?): String {
        // `filename*=UTF-8''...` and plain `filename="..."`, in that order —
        // the video's name is the topic, so it is very often not ASCII.
        val extended = Regex("""filename\*=UTF-8''([^;]+)""", RegexOption.IGNORE_CASE)
            .find(disposition ?: "")?.groupValues?.get(1)
        val plain = Regex("""filename="?([^";]+)"?""", RegexOption.IGNORE_CASE)
            .find(disposition ?: "")?.groupValues?.get(1)
        val raw = extended?.let { runCatching { URLDecoder.decode(it, "UTF-8") }.getOrNull() }
            ?: plain
            ?: Uri.parse(url).lastPathSegment
            ?: "sarideo"
        val cleaned = raw.substringAfterLast('/').replace(Regex("""[\\/:*?"<>|]"""), "_").trim()
        if (cleaned.isEmpty()) return "sarideo"
        if (cleaned.contains('.')) return cleaned
        val suffix = MimeTypeMap.getSingleton().getExtensionFromMimeType(mimeType ?: "")
        return if (suffix.isNullOrBlank()) cleaned else "$cleaned.$suffix"
    }
}
