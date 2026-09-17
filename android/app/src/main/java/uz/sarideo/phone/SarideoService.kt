package uz.sarideo.phone

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.net.InetSocketAddress
import java.net.Socket
import kotlin.concurrent.thread

/**
 * Holds the Python server for as long as the app is installed and running.
 *
 * A foreground service, and not because a notification is nice to have: a
 * ninety-scene render is an hour of solid CPU, and Android stops a background
 * process long before that. The notification is the price of being allowed to
 * keep working while the screen is off, and the wake lock is what keeps the
 * processor from idling between ffmpeg calls.
 */
class SarideoService : Service() {

    companion object {
        private const val TAG = "SarideoService"
        private const val CHANNEL = "sarideo-render"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "uz.sarideo.phone.STOP"

        /** Set once the listener answers; read by the activity to load the page. */
        @Volatile var port: Int = 0
            private set

        @Volatile var failure: String? = null
            private set

        fun serverUrl(): String = "http://127.0.0.1:$port/"

        /** True once the socket accepts a connection, not merely once it is bound. */
        fun isListening(): Boolean {
            val open = port
            if (open == 0) return false
            return try {
                Socket().use {
                    it.connect(InetSocketAddress("127.0.0.1", open), 400)
                    true
                }
            } catch (_: Exception) {
                false
            }
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startInForeground()
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            // The server runs on a thread of its own inside this process and
            // uvicorn has no way in to be asked to stop from outside it, so the
            // process is what ends. Nothing is lost that was not already
            // written: renders land on disk scene by scene.
            android.os.Process.killProcess(android.os.Process.myPid())
            return START_NOT_STICKY
        }
        if (!started) {
            started = true
            thread(name = "sarideo-python", isDaemon = false) { runPython() }
        }
        // START_STICKY: if Android does reclaim us under memory pressure, come
        // back rather than leaving the page pointing at nothing.
        return START_STICKY
    }

    override fun onDestroy() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }

    private fun runPython() {
        try {
            Payload.install(this)

            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
            }
            val module = Python.getInstance().getModule("sarideo_android")

            var chosen = module["DEFAULT_PORT"]!!.toInt()
            // The fixed port keeps the address stable across reloads; a phone
            // where something else already holds it gets the next one rather
            // than a crash.
            var attempts = 0
            while (attempts < 20 &&
                !module.callAttr("port_is_free", chosen).toBoolean()
            ) {
                chosen += 1
                attempts += 1
            }
            port = chosen

            Log.i(TAG, "starting server on 127.0.0.1:$chosen")
            module.callAttr(
                "serve",
                filesDir.absolutePath,
                applicationInfo.nativeLibraryDir,
                chosen,
            )
        } catch (t: Throwable) {
            Log.e(TAG, "python server stopped", t)
            failure = t.message ?: t.javaClass.simpleName
            port = 0
        }
    }

    private fun startInForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL,
                    getString(R.string.channel_name),
                    // Low: this notification exists so the work is allowed to
                    // continue, not so it can interrupt anyone.
                    NotificationManager.IMPORTANCE_LOW,
                ).apply { setShowBadge(false) }
            )
        }

        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification: Notification = Notification.Builder(this, CHANNEL)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(
                Notification.Action.Builder(
                    null as android.graphics.drawable.Icon?,
                    getString(R.string.notification_stop),
                    PendingIntent.getService(
                        this,
                        1,
                        Intent(this, SarideoService::class.java).setAction(ACTION_STOP),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                    ),
                ).build()
            )
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun acquireWakeLock() {
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sarideo:render").apply {
            setReferenceCounted(false)
            // A partial wake lock keeps the processor running with the screen
            // off. It does not keep the screen on and costs nothing while the
            // app is idle, because ffmpeg is what draws the battery, not this.
            acquire()
        }
    }
}
