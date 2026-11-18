package app.auren.phone

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlin.math.sqrt

/**
 * The phone's microphone as the PC's ears want it: 16 kHz, mono, 16-bit little-endian PCM, in 100 ms
 * chunks. The caller checks RECORD_AUDIO first.
 */
class Mic(private val onChunk: (ByteArray) -> Unit, private val onLevel: (Float) -> Unit) {
    @Volatile private var running = false

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) return false
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, CHUNK * 4))
        } catch (_: Exception) {
            return false
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return false
        }
        try {
            rec.startRecording()
        } catch (_: IllegalStateException) {
            rec.release()
            return false
        }
        running = true
        Thread({
            val buf = ByteArray(CHUNK)
            try {
                while (running) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n < 0) break
                    if (n > 0) {
                        onChunk(buf.copyOf(n))
                        onLevel(level(buf, n))
                    }
                }
            } finally {
                runCatching { rec.stop() }
                rec.release()
                onLevel(0f)
            }
        }, "auren-mic").start()
        return true
    }

    fun stop() {
        running = false
    }

    companion object {
        const val RATE = 16_000
        const val CHUNK = 3_200          // 100 ms of s16 mono at 16 kHz

        /** 0..1, loud speech near 1 (the square root lifts quiet speech, like the mascot's level). */
        fun level(buf: ByteArray, n: Int): Float {
            var sum = 0.0
            var count = 0
            var i = 0
            while (i + 1 < n) {
                val s = (buf[i].toInt() and 0xff) or (buf[i + 1].toInt() shl 8)
                sum += s.toDouble() * s
                count++
                i += 2
            }
            if (count == 0) return 0f
            return sqrt(minOf(1.0, sqrt(sum / count) / 6000.0)).toFloat()
        }
    }
}
