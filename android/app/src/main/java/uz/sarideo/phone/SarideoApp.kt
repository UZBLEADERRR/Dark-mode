package uz.sarideo.phone

import android.app.Application
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Starts CPython once, for the whole process.
 *
 * Chaquopy refuses a second `start`, and the service is `START_STICKY` — so the
 * one place guaranteed to run exactly once is here.
 */
class SarideoApp : Application() {
    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
    }
}
