package app.auren.phone

import android.app.Application
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit

/** A release of Auren on GitHub with the phone app attached: the APK, its size, and its `sha256sum` file. */
data class Release(val version: String, val code: Long, val apk: String, val size: Long, val sums: String, val page: String) {
    val file get() = "auren-phone-$version.apk"
}

/** Reading GitHub's answer about the latest release. Plain Kotlin, so it's unit tested off the device. */
object Releases {
    /** GitHub's latest release: never a draft or a prerelease (a release candidate). */
    const val LATEST = "https://api.github.com/repos/kvm404/Auren/releases/latest"

    /** The versionCode build.gradle.kts gives X.Y.Z (0.1.7 is 107); null for anything else. */
    fun code(version: String): Long? {
        val (a, b, c) = Regex("""(\d{1,4})\.(\d{1,2})\.(\d{1,2})""").matchEntire(version)?.destructured ?: return null
        return a.toLong() * 10000 + b.toLong() * 100 + c.toLong()
    }

    /**
     * The release in [json] (one of GitHub's release objects), or null when it has no phone app to install:
     * not a vX.Y.Z release, or its APK or checksum isn't attached yet (the APK is built after the release is
     * published, so for a few minutes it has neither).
     */
    fun parse(json: String): Release? {
        val o = JSONObject(json)
        if (o.optBoolean("draft") || o.optBoolean("prerelease")) return null
        val tag = o.optString("tag_name")
        if (!tag.startsWith("v")) return null
        val version = tag.removePrefix("v")
        val code = code(version) ?: return null
        val assets = o.optJSONArray("assets") ?: return null
        fun asset(name: String) = (0 until assets.length()).mapNotNull { assets.optJSONObject(it) }.firstOrNull {
            it.optString("name") == name && it.optString("state", "uploaded") == "uploaded" &&
                it.optString("browser_download_url").startsWith("https://")
        }
        val apk = asset("auren-phone-$version.apk") ?: return null
        val sums = asset("auren-phone-$version.apk.sha256") ?: return null
        return Release(version, code, apk.getString("browser_download_url"), apk.optLong("size"),
            sums.getString("browser_download_url"), o.optString("html_url"))
    }

    /** The SHA-256 a `sha256sum` file gives for [name] ("<hex>  <name>" lines), lowercase; null if it names none. */
    fun sha(text: String, name: String): String? = text.lineSequence()
        .mapNotNull { Regex("""([0-9a-fA-F]{64}) [ *]?(\S+)""").matchEntire(it.trim()) }
        .firstOrNull { it.groupValues[2] == name }?.groupValues?.get(1)?.lowercase()
}

sealed interface UpdateStatus {
    /** The release this is about, if any: what Update installs. */
    val release: Release? get() = null

    data object Unknown : UpdateStatus
    data object Checking : UpdateStatus
    data object Current : UpdateStatus
    data class Available(override val release: Release) : UpdateStatus
    data class Downloading(override val release: Release, val done: Long) : UpdateStatus
    /** Handed to Android, which asks the user and then installs it, closing this app. */
    data class Installing(override val release: Release) : UpdateStatus
    /** [release] null: the check failed. Else getting or installing it did. */
    data class Failed(val why: String, override val release: Release?) : UpdateStatus
}

/**
 * The app updating itself from Auren's GitHub releases. Opening the app checks the latest release now and
 * then; Update downloads its APK into the cache, checks it against the release's SHA-256, that it is this
 * app, newer, and signed with this app's key, and hands it to Android's package installer, which asks the user
 * and installs it over this one (the pairing stays). The release workflow (android-release.yml) attaches the
 * APK and its checksum to every release.
 */
object Updates {
    private lateinit var app: Application
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
    private val _status = MutableStateFlow<UpdateStatus>(UpdateStatus.Unknown)
    val status: StateFlow<UpdateStatus> = _status
    /** Android's "install this update?" screen, waiting for the app's activity to open it (see [confirmOpened]). */
    private val _confirm = MutableStateFlow<Intent?>(null)
    val confirm: StateFlow<Intent?> = _confirm
    private var job: Job? = null
    private var checkedAt = 0L               // elapsedRealtime of the last check that got an answer; 0: none yet
    @Volatile private var call: Call? = null  // the request in flight, so Cancel stops a download at once
    /** The install session the installer's answers count for: an abandoned one's answer is ignored. */
    @Volatile private var session = -1
    private val dir get() = File(app.cacheDir, "update")

    private val self: PackageInfo get() = app.packageManager.getPackageInfo(app.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
    val version: String get() = self.versionName.orEmpty()

    fun init(application: Application) {
        if (::app.isInitialized) return
        app = application
        scope.launch(Dispatchers.IO) { clean() }
    }

    /** The app came to the front: check, unless this run already did lately. */
    fun checkIfDue() {
        if (checkedAt != 0L && SystemClock.elapsedRealtime() - checkedAt < EVERY_MS) return
        // Never in the middle of one; a release found earlier is looked up again (it may be gone, or a newer one out).
        val s = _status.value
        if (s is UpdateStatus.Checking || s is UpdateStatus.Downloading || s is UpdateStatus.Installing) return
        check()
    }

    fun check() {
        if (job?.isActive == true) return
        job = scope.launch {
            _status.value = UpdateStatus.Checking
            _status.value = try {
                val r = withContext(Dispatchers.IO) { latest() }
                checkedAt = SystemClock.elapsedRealtime()
                if (r != null && r.code > self.longVersionCode) UpdateStatus.Available(r) else UpdateStatus.Current
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                UpdateStatus.Failed(why(e), null)
            }
        }
    }

    /** Whether Android lets this app install apps; if not, the user allows it in Settings first. */
    fun canInstall() = app.packageManager.canRequestPackageInstalls()

    /** Download [r], check it, and hand it to Android. Again while installing: starts the install over. */
    fun install(r: Release) {
        if (job?.isActive == true) return
        job = scope.launch {
            try {
                _confirm.value = null
                _status.value = UpdateStatus.Downloading(r, 0)
                val apk = withContext(Dispatchers.IO) {
                    // Only while still downloading: a Cancel in between stays.
                    download(r) { n -> _status.update { if (it is UpdateStatus.Downloading) UpdateStatus.Downloading(r, n) else it } }
                }
                withContext(Dispatchers.IO) { vet(apk) }
                _status.value = UpdateStatus.Installing(r)
                withContext(Dispatchers.IO) { commit(apk) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _status.value = UpdateStatus.Failed(why(e), r)
            }
        }
    }

    /** Stops a download; the update stays on offer. */
    fun cancel() {
        val s = _status.value as? UpdateStatus.Downloading ?: return
        _status.value = UpdateStatus.Available(s.release)
        job?.cancel()
        call?.cancel()
    }

    /** The activity opened (or couldn't open) [confirm]. */
    fun confirmOpened(ok: Boolean) {
        _confirm.value = null
        if (!ok) _status.value = UpdateStatus.Failed("Android couldn't show the install screen", _status.value.release)
    }

    /** The package installer's answer about the session [commit] started (UpdateReceiver). */
    fun result(i: Intent) {
        if (i.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != session) return
        val r = _status.value.release
        when (val code = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val ask = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) i.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    else @Suppress("DEPRECATION") i.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (ask != null) _confirm.value = ask.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                else _status.value = UpdateStatus.Failed("Android didn't ask to install it", r)
            }
            // An update of this app usually ends it before this comes; if it does come, the new one is in.
            PackageInstaller.STATUS_SUCCESS -> {
                _status.value = UpdateStatus.Current
                scope.launch(Dispatchers.IO) { clean() }
            }
            PackageInstaller.STATUS_FAILURE_ABORTED -> _status.value = r?.let { UpdateStatus.Available(it) } ?: UpdateStatus.Unknown
            else -> _status.value = UpdateStatus.Failed(installError(code, i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)), r)
        }
    }

    // --- off the main thread ---------------------------------------------------------------------

    /** [deadline]: the whole request, in seconds; the APK has none (Cancel stops it, and a stall still times out). */
    private fun get(url: String, api: Boolean = false, deadline: Long = 30): Response {
        val req = Request.Builder().url(url).header("User-Agent", "Auren-phone")
            .apply { if (api) header("Accept", "application/vnd.github+json") }.build()
        val c = http.newCall(req)
        if (deadline > 0) c.timeout().timeout(deadline, TimeUnit.SECONDS)
        call = c
        val res = c.execute()
        if (!res.isSuccessful) {
            res.close()
            throw HttpError(res.code)
        }
        return res
    }

    private fun latest(): Release? = try {
        get(Releases.LATEST, api = true).use { Releases.parse(it.body!!.string()) }
    } catch (e: HttpError) {
        if (e.code == 404) null else throw e     // no release yet
    }

    /** The APK in the cache, checked against the release's SHA-256: a copy already there is used again. */
    private fun download(r: Release, progress: (Long) -> Unit): File {
        val sums = get(r.sums).use {
            val src = it.body!!.source()
            src.request(4097)                      // a checksum file is one line: never read a big one whole
            if (src.buffer.size > 4096) throw IOException("the release's checksum file is too big")
            src.buffer.readUtf8()
        }
        val want = Releases.sha(sums, r.file) ?: throw IOException("the release's checksum doesn't name ${r.file}")
        dir.mkdirs()
        val out = File(dir, r.file)
        if (out.isFile && sha256(out) == want) return out
        out.delete()
        val part = File(dir, r.file + ".part")
        get(r.apk, deadline = 0).use { res ->
            res.body!!.byteStream().use { input ->
                part.outputStream().use { output ->
                    val buf = ByteArray(1 shl 16)
                    var have = 0L
                    var shown = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        have += n
                        if (r.size > 0 && have > r.size) throw IOException("the download is bigger than the release says")
                        output.write(buf, 0, n)
                        if (have - shown >= 256 * 1024) {
                            shown = have
                            progress(have)
                        }
                    }
                }
            }
        }
        if (sha256(part) != want) {
            part.delete()
            throw IOException("the download didn't match its checksum")
        }
        if (!part.renameTo(out)) throw IOException("couldn't save the download")
        return out
    }

    /**
     * That [apk] is this app, newer, and signed by a key in this one's signing history (or one that took its
     * place). Android checks the key itself too; this says why in words, before asking the user.
     */
    private fun vet(apk: File) {
        val pm = app.packageManager
        // Android 10 reads an archive's certificates only when GET_SIGNATURES asks too.
        @Suppress("DEPRECATION")
        val flags = PackageManager.GET_SIGNING_CERTIFICATES or
            (if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) PackageManager.GET_SIGNATURES else 0)
        val got = pm.getPackageArchiveInfo(apk.path, flags)
            ?: throw IOException("the download isn't an app Android can read")
        val me = self
        if (got.packageName != me.packageName) throw IOException("the download is a different app")
        if (got.longVersionCode <= me.longVersionCode) throw IOException("the download isn't newer than this app")
        val theirs = got.signingInfo?.let { if (it.hasMultipleSigners()) it.apkContentsSigners else it.signingCertificateHistory }
        val mine = me.signingInfo?.let { if (it.hasMultipleSigners()) it.apkContentsSigners else it.signingCertificateHistory }
        // Unreadable on this Android: leave it to the installer, which refuses another key anyway.
        if (theirs.isNullOrEmpty() || mine.isNullOrEmpty()) return
        if (theirs.none { it in mine }) throw IOException(OTHER_KEY)
    }

    /** One install session for [apk], replacing any this app left open; Android answers UpdateReceiver. */
    private fun commit(apk: File) {
        val installer = app.packageManager.packageInstaller
        installer.mySessions.forEach { runCatching { installer.abandonSession(it.sessionId) } }
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(app.packageName)
            setSize(apk.length())
        }
        val id = installer.createSession(params)
        session = id
        installer.openSession(id).use { s ->
            try {
                s.openWrite("auren.apk", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out) }
                    s.fsync(out)
                }
                val answer = Intent(app, UpdateReceiver::class.java)
                // Mutable: the installer adds its status to the intent.
                val pending = PendingIntent.getBroadcast(app, id, answer, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
                s.commit(pending.intentSender)
            } catch (e: Exception) {
                s.abandon()
                throw e
            }
        }
    }

    /** Drops downloads that are no newer than this app (the update is in) and any half-done one. */
    private fun clean() {
        val now = self.longVersionCode
        dir.listFiles()?.forEach { f ->
            val v = Regex("""auren-phone-(.+)\.apk""").matchEntire(f.name)?.groupValues?.get(1)?.let(Releases::code)
            if (v == null || v <= now) f.delete()
        }
    }

    private class HttpError(val code: Int) : IOException("HTTP $code")

    private fun why(e: Exception): String = when (e) {
        is UnknownHostException -> "no internet"
        is SocketTimeoutException -> "GitHub didn't answer"
        is HttpError -> when (e.code) {
            403, 429 -> "GitHub is limiting checks from this network; try again in an hour"
            else -> "GitHub answered ${e.code}"
        }
        else -> e.message ?: e.javaClass.simpleName
    }

    private fun installError(code: Int, message: String?): String = when (code) {
        PackageInstaller.STATUS_FAILURE_CONFLICT -> OTHER_KEY
        PackageInstaller.STATUS_FAILURE_STORAGE -> "the phone is out of space"
        PackageInstaller.STATUS_FAILURE_BLOCKED -> "the phone blocked the install"
        PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "this phone can't run that version"
        else -> "Android couldn't install it" + if (message.isNullOrBlank()) "" else " ($message)"
    }

    private const val OTHER_KEY = "this copy of Auren has another signing key (a build of your own?). " +
        "Uninstall it and install the release from GitHub once; it updates itself after that"
    private const val EVERY_MS = 6 * 60 * 60 * 1000L
}

/** Where the package installer answers [Updates]. Not exported: only the PendingIntent it was given reaches it. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Updates.result(intent)
}
