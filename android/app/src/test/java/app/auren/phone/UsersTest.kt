package app.auren.phone

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import org.junit.Assert.assertEquals
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class UsersTest {
    private val scope = TestScope()
    private val events = mutableListOf<String>()
    private val users = Users(scope, 4_000, onLast = { events += "mic off" }, onIdle = { events += "closed" })

    @Test
    fun `the mic stops at once and the link closes after the grace`() {
        users.acquire()
        users.release()
        assertEquals(listOf("mic off"), events)
        scope.advanceTimeBy(3_999); scope.runCurrent()
        assertEquals(listOf("mic off"), events)
        scope.advanceTimeBy(1); scope.runCurrent()
        assertEquals(listOf("mic off", "closed"), events)
    }

    @Test
    fun `coming back in time keeps the link`() {
        users.acquire()
        users.release()
        scope.advanceTimeBy(3_000); scope.runCurrent()
        users.acquire()
        scope.advanceTimeBy(60_000); scope.runCurrent()
        assertEquals(listOf("mic off"), events)
    }

    @Test
    fun `an old wait can't close a link that's in use again`() {
        // The bug this replaces: each stop started its own 4 s check, so a quick stop-start-stop closed early.
        users.acquire()
        users.release()                                       // t=0: first wait
        scope.advanceTimeBy(3_000); scope.runCurrent()
        users.acquire()
        users.release()                                       // t=3 s: a new wait, the old one gone
        scope.advanceTimeBy(1_500); scope.runCurrent()        // t=4.5 s: the first wait would have fired
        assertEquals(listOf("mic off", "mic off"), events)
        scope.advanceTimeBy(2_500); scope.runCurrent()        // t=7 s
        assertEquals(listOf("mic off", "mic off", "closed"), events)
    }

    @Test
    fun `with two screens only the last one leaving counts, and extra releases are ignored`() {
        users.acquire()
        users.acquire()
        users.release()
        scope.advanceTimeBy(10_000); scope.runCurrent()
        assertEquals(emptyList<String>(), events)
        users.release()
        users.release()
        assertEquals(0, users.count)
        scope.advanceTimeBy(4_000); scope.runCurrent()
        assertEquals(listOf("mic off", "closed"), events)
    }
}
