package app.auren.phone

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Who is showing Auren: the screens and the share sheet. When the last one goes, [onLast] runs at once (the
 * microphone never runs without a screen), and [onIdle] once nobody has come back for [graceMs] (the
 * connection closes; a quick app switch keeps it). Coming back cancels the wait, so a stale wait can't
 * close a connection someone is using. Main thread only.
 */
class Users(
    private val scope: CoroutineScope,
    private val graceMs: Long,
    private val onLast: () -> Unit,
    private val onIdle: () -> Unit,
) {
    var count = 0
        private set
    private var grace: Job? = null

    fun acquire() {
        count++
        grace?.cancel()
        grace = null
    }

    fun release() {
        if (count == 0) return
        count--
        if (count > 0) return
        onLast()
        grace?.cancel()
        grace = scope.launch {
            delay(graceMs)
            grace = null
            onIdle()
        }
    }
}
