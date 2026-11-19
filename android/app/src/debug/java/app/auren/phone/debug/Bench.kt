package app.auren.phone.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.auren.phone.Remote
import app.auren.phone.Speaker
import app.auren.phone.VoiceStatus
import app.auren.phone.Voices
import android.util.Log

/**
 * Debug builds only. Measures the phone's own voice without touching the phone:
 *
 *   adb shell am broadcast -n app.auren.phone/.debug.Bench --es text "A sentence to time."
 *     downloads and loads Phonon if needed, then logs, for each voice, the time to the first audio and the
 *     real-time factor (logcat -s AurenBench AurenVoice). Nothing is played. --ei runs N (default 2),
 *     --ei threads N (only before the first load in this process).
 *   adb shell am broadcast -n app.auren.phone/.debug.Bench --es say "Hello." --es voice Hugh
 *     reads it aloud with Phonon, as a reply would be, at a fifth of the phone's volume (--ef gain to change);
 *     only once Phonon is ready. --ez stop true: stops it, as the Stop button does.
 */
class Bench : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val speaker = Remote.speaker
        if (intent.getBooleanExtra("stop", false)) return speaker.stop()
        speaker.prepare(intent.getIntExtra("threads", Speaker.THREADS))
        intent.getStringExtra("say")?.let {
            if (speaker.status.value != VoiceStatus.Ready) {
                Log.i("AurenBench", "not ready: ${speaker.status.value}")
                return
            }
            speaker.gain = intent.getFloatExtra("gain", 0.2f)
            speaker.speak(it, Voices.forState(intent.getStringExtra("voice")).name)
            return
        }
        val text = intent.getStringExtra("text") ?: "Here's what I found: the build passed, and the phone is ready."
        speaker.bench(text, intent.getIntExtra("runs", 2))
    }
}
