package uz.sarideo.phone

import android.content.Context
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.util.zip.ZipInputStream

/**
 * Unpacks the Python application out of the package and onto the filesystem.
 *
 * It would be less work to import `app` straight out of the package's assets,
 * and it would not work: `app/main.py` finds its own web UI with
 * `Path(__file__).parent / "static"` and hands that directory to Starlette,
 * which opens real files. So the payload is unpacked once, into private
 * storage, and re-unpacked only when the build that wrote it changes.
 */
object Payload {

    private const val TAG = "SarideoPayload"
    private const val ARCHIVE = "sarideo-python.zip"
    private const val STAMP = "sarideo-python.version"
    private const val FONTS = "fonts"

    /** Where Python's `sys.path` is pointed. */
    fun pythonRoot(context: Context): File = File(context.filesDir, "pyroot")

    fun fontsDir(context: Context): File = File(context.filesDir, FONTS)

    /**
     * Make both folders current. Cheap on every run but the first after an
     * install or an update, when it costs the time to write about 2 MB.
     */
    fun install(context: Context) {
        val stampFile = File(context.filesDir, STAMP)
        val wanted = context.assets.open(STAMP).use { it.readBytes().decodeToString().trim() }
        val have = if (stampFile.isFile) stampFile.readText().trim() else ""
        val root = pythonRoot(context)

        if (wanted.isNotEmpty() && wanted == have && root.isDirectory) return

        Log.i(TAG, "unpacking python payload ($wanted)")
        // Deleted rather than written over: a file that this build no longer
        // ships would otherwise stay behind and keep being imported.
        root.deleteRecursively()
        root.mkdirs()
        unzipAsset(context, ARCHIVE, root)

        val fonts = fontsDir(context)
        fonts.deleteRecursively()
        fonts.mkdirs()
        copyAssetFolder(context, FONTS, fonts)

        stampFile.writeText(wanted)
    }

    private fun unzipAsset(context: Context, name: String, target: File) {
        val canonicalTarget = target.canonicalPath
        ZipInputStream(context.assets.open(name).buffered()).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                val out = File(target, entry.name)
                // A zip is data, and an entry named `../..` is how data becomes
                // a write outside the folder it was supposed to land in.
                if (!out.canonicalPath.startsWith(canonicalTarget + File.separator)) {
                    throw SecurityException("payload entry escapes its folder: ${entry.name}")
                }
                if (entry.isDirectory) {
                    out.mkdirs()
                } else {
                    out.parentFile?.mkdirs()
                    FileOutputStream(out).use { zip.copyTo(it) }
                }
                zip.closeEntry()
            }
        }
    }

    private fun copyAssetFolder(context: Context, name: String, target: File) {
        val children = context.assets.list(name) ?: return
        if (children.isEmpty()) {
            context.assets.open(name).use { input ->
                FileOutputStream(target).use { input.copyTo(it) }
            }
            return
        }
        target.mkdirs()
        for (child in children) {
            copyAssetFolder(context, "$name/$child", File(target, child))
        }
    }
}
