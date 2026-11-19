package app.auren.phone

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.system.Os
import android.util.Log
import app.auren.phone.phonon.PhononTTS
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Where the phone's own voice stands. Until [Ready], replies are read with Android's TextToSpeech. */
sealed interface VoiceStatus {
    /** This phone can't run it (not arm64 with dotprod and fp16, or the app was built without the library). */
    data class Unsupported(val why: String) : VoiceStatus
    data object Missing : VoiceStatus
    data class Downloading(val done: Long, val total: Long) : VoiceStatus
    data object Loading : VoiceStatus
    data object Ready : VoiceStatus
    data class Failed(val why: String) : VoiceStatus
}

/**
 * Reads replies aloud on the phone. With Phonon: the model and the four voices are downloaded once into the
 * app's files (Voices.FILES, each checked against its pinned SHA-256), loaded once, and every reply is
 * synthesized on one background thread and streamed to an AudioTrack, which starts on the first chunk.
 * [stop] (Stop, a new turn) silences it at once. Until Phonon is ready, Android's TextToSpeech reads them.
 */
class Speaker(private val app: Context) {
    private val _status = MutableStateFlow<VoiceStatus>(VoiceStatus.Missing)
    val status: StateFlow<VoiceStatus> = _status
    private val _speaking = MutableStateFlow(false)
    val speaking: StateFlow<Boolean> = _speaking

    /** Every call into Phonon, the download and the load run here, one at a time, as the library needs. */
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "phonon").apply { priority = Thread.MAX_PRIORITY } }
    /** Bumped by every speak and stop: an utterance whose number is no longer current stops at its next chunk. */
    private val turn = AtomicInteger()
    @Volatile private var busy = false               // a download or load is queued or running
    @Volatile private var tts: PhononTTS? = null
    @Volatile private var track: AudioTrack? = null
    private var voice: String? = null                // the voice Phonon is set to (worker thread only)
    private val dir = File(app.filesDir, "phonon")
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
    private val installer = Installer(dir, Voices.FILES, ::fetch)
    private val audio = app.getSystemService(AudioManager::class.java)
    private val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).setAudioAttributes(attrs).build()
    private var android: TextToSpeech? = null
    /** Debug builds' adb check plays quietly, whatever the phone's volume is. 1 otherwise. */
    @Volatile var gain = 1f
    private var androidReady = false

    init {
        unsupported()?.let { _status.value = VoiceStatus.Unsupported(it) }
    }

    /** Why this phone can't run Phonon, or null. Checked before the library is touched: on a CPU without the
     *  instructions it was built for, it would crash rather than fail. */
    private fun unsupported(): String? {
        if ("arm64-v8a" !in Build.SUPPORTED_ABIS) return "needs a 64-bit ARM phone"
        val features = try {
            File("/proc/cpuinfo").readLines().firstOrNull { it.startsWith("Features") }.orEmpty()
        } catch (_: IOException) {
            ""
        }
        if (!features.split(' ', '\t').containsAll(listOf("asimddp", "asimdhp"))) return "this phone's CPU is too old"
        return try {
            System.loadLibrary("ptts_ffi")
            null
        } catch (_: Throwable) {
            "this build of the app doesn't include it"
        }
    }

    /**
     * Download (the first time) and load Phonon in the background, unless it is ready or on its way. Called
     * when reading aloud is turned on, and again by a reply while it isn't ready (a failed download resumes).
     * [threads] is RAYON_NUM_THREADS, read once per process at the first load.
     */
    fun prepare(threads: Int = THREADS) {
        val s = _status.value
        if (s is VoiceStatus.Unsupported || s is VoiceStatus.Ready || busy) return
        busy = true
        worker.execute {
            try {
                if (!installer.ready()) {
                    val total = installer.total
                    _status.value = VoiceStatus.Downloading(0, total)
                    val t0 = SystemClock.elapsedRealtime()
                    installer.install({ _status.value = VoiceStatus.Downloading(it, total) })
                    Log.i(TAG, "downloaded and checked $total bytes in ${SystemClock.elapsedRealtime() - t0} ms")
                }
                _status.value = VoiceStatus.Loading
                Os.setenv("RAYON_NUM_THREADS", threads.toString(), true)
                val t0 = SystemClock.elapsedRealtime()
                val loaded = PhononTTS(dir.path, "en")
                val missing = Voices.ALL.map { it.name } - loaded.voices.toSet()
                if (missing.isNotEmpty()) {
                    loaded.close()
                    throw IllegalStateException("the model has no ${missing.joinToString()}")
                }
                tts = loaded
                Log.i(TAG, "loaded with $threads threads in ${SystemClock.elapsedRealtime() - t0} ms")
                _status.value = VoiceStatus.Ready
            } catch (t: Throwable) {
                Log.w(TAG, "Phonon isn't ready", t)
                _status.value = VoiceStatus.Failed(t.message ?: t.javaClass.simpleName)
            } finally {
                busy = false
            }
        }
    }

    private fun fetch(url: String, offset: Long): Fetched {
        val req = Request.Builder().url(url).apply { if (offset > 0) header("Range", "bytes=$offset-") }.build()
        val r = http.newCall(req).execute()
        if (!r.isSuccessful) {
            r.close()
            throw IOException("the download got HTTP ${r.code}")
        }
        val body = r.body ?: throw IOException("the download was empty")
        return Fetched(body.byteStream(), r.code == 206)
    }

    /** Read [text] in [voice] (one of Voices.ALL), cutting off whatever was being read. */
    fun speak(text: String, voice: String) {
        val my = turn.incrementAndGet()
        silence()
        if (text.isBlank()) return
        val phonon = tts
        if (phonon == null || _status.value != VoiceStatus.Ready) {
            prepare()
            fallback(text, my)
            return
        }
        _speaking.value = true
        val asked = SystemClock.elapsedRealtime()
        worker.execute {
            if (turn.get() != my) return@execute
            try {
                play(phonon, text, voice, my) { Log.i(TAG, "$voice: first audio ${SystemClock.elapsedRealtime() - asked} ms after the reply") }
            } catch (t: Throwable) {
                Log.w(TAG, "Phonon couldn't read that", t)
                if (turn.get() == my) fallback(text, my)
            } finally {
                if (turn.get() == my) {
                    _speaking.value = false
                    audio.abandonAudioFocusRequest(focus)
                }
            }
        }
    }

    /** Synthesize into the AudioTrack, starting it on the first chunk, then let it play out. Worker thread. */
    private fun play(phonon: PhononTTS, text: String, voiceName: String, my: Int, onFirst: () -> Unit) {
        if (voiceName != voice) {
            phonon.setVoice(voiceName)
            voice = voiceName
        }
        val t = track ?: newTrack().also { track = it }
        t.pause()
        t.flush()                          // a chunk a stopped utterance wrote after its stop
        t.setVolume(gain)
        val base = t.playbackHeadPosition.toLong() and 0xFFFFFFFFL
        var written = 0L
        phonon.speak(text) { pcm ->
            if (turn.get() != my) return@speak false
            if (written == 0L) {
                audio.requestAudioFocus(focus)
                t.play()
                onFirst()
            }
            var off = 0
            while (off < pcm.size) {
                if (turn.get() != my) return@speak false
                val n = t.write(pcm, off, pcm.size - off, AudioTrack.WRITE_NON_BLOCKING)
                if (n < 0) throw IllegalStateException("AudioTrack.write gave $n")
                off += n
                if (n == 0) Thread.sleep(10)          // the buffer is full: it drains in real time
            }
            written += pcm.size
            true
        }
        // Wait for the tail to play out, so "speaking" ends when the sound does.
        val deadline = SystemClock.elapsedRealtime() + written * 1000 / RATE + 2_000
        while (turn.get() == my && written > 0 && SystemClock.elapsedRealtime() < deadline &&
            (t.playbackHeadPosition.toLong() and 0xFFFFFFFFL) - base < written) {
            Thread.sleep(20)
        }
        if (turn.get() == my) t.stop()
        Log.i(TAG, "$voiceName: ${if (turn.get() == my) "read" else "stopped after"} %.1f s of audio".format(written.toDouble() / RATE))
    }

    private fun newTrack(): AudioTrack {
        val min = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_FLOAT)
        return AudioTrack.Builder().setAudioAttributes(attrs)
            .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setSampleRate(RATE)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(min * 2, RATE * 4 / 2))     // half a second of floats
            .build()
    }

    /** Stop reading now: Stop, a new turn, reading aloud turned off. */
    fun stop() {
        turn.incrementAndGet()
        silence()
    }

    private fun silence() {
        track?.let { t ->
            try {
                t.pause()
                t.flush()
            } catch (_: IllegalStateException) {
            }
        }
        android?.stop()
        _speaking.value = false
        audio.abandonAudioFocusRequest(focus)
    }

    /** Android's own voice, while Phonon isn't ready or failed on this text. */
    private fun fallback(text: String, my: Int) {
        val id = "auren-$my"
        val t = android
        if (t == null) {
            android = TextToSpeech(app) { status ->
                androidReady = status == TextToSpeech.SUCCESS
                if (androidReady && turn.get() == my) android?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
            }.apply {
                setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String) { _speaking.value = true }
                    override fun onDone(utteranceId: String) { if (utteranceId == "auren-${turn.get()}") _speaking.value = false }
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String) { if (utteranceId == "auren-${turn.get()}") _speaking.value = false }
                })
            }
        } else if (androidReady) {
            t.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
        }
    }

    /**
     * Debug builds' measurement (adb, see docs/phone.md): synthesize [text] in each voice without playing it,
     * and log the time to the first audio and the real-time factor. The first run warms the model up.
     */
    fun bench(text: String, runs: Int = 2) {
        worker.execute {
            val phonon = tts ?: run {
                Log.i(BENCH, "not ready: ${_status.value}")
                return@execute
            }
            phonon.setVoice(Voices.DEFAULT.name)
            phonon.speak("Warming up.") { true }
            for (v in Voices.ALL) {
                phonon.setVoice(v.name)
                voice = v.name
                repeat(runs) { i ->
                    val t0 = SystemClock.elapsedRealtimeNanos()
                    var first = -1.0
                    var samples = 0L
                    val r = phonon.speak(text) { pcm ->
                        if (first < 0) first = (SystemClock.elapsedRealtimeNanos() - t0) / 1e6
                        samples += pcm.size
                        true
                    }
                    val total = (SystemClock.elapsedRealtimeNanos() - t0) / 1e6
                    val secs = samples.toDouble() / RATE
                    Log.i(BENCH, "%s run %d: first audio %.0f ms (library %.0f ms), %.0f ms for %.2f s of audio, RTF %.3f"
                        .format(v.name, i + 1, first, r.ttfaMs, total, secs, total / 1000 / secs))
                }
            }
            Log.i(BENCH, "done")
        }
    }

    companion object {
        private const val TAG = "AurenVoice"
        private const val BENCH = "AurenBench"
        const val RATE = 24_000
        /** Phonon's own Android engine uses 2: one thread per core is too many on big.LITTLE phones. */
        const val THREADS = 2
    }
}
