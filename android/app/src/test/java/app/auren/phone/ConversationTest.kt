package app.auren.phone

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationTest {
    private val idle = AurenState(state = "idle")
    /** The moment after a reply: every wait is over. */
    private val over = Between(on = true, connected = true, talkOpen = false, replyPending = false, speaking = false,
        speechDue = false, state = idle)

    @Test
    fun `the next listen starts once the turn is over everywhere`() {
        assertTrue(listenNext(over))
    }

    @Test
    fun `it never starts with conversation mode off or the link down`() {
        assertFalse(listenNext(over.copy(on = false)))
        assertFalse(listenNext(over.copy(connected = false)))
    }

    @Test
    fun `never two listens at once, nor before the reply`() {
        assertFalse(listenNext(over.copy(talkOpen = true)))
        assertFalse(listenNext(over.copy(replyPending = true)))
    }

    @Test
    fun `it waits while the phone reads the reply aloud, and for a reading about to start`() {
        assertFalse(listenNext(over.copy(speaking = true)))
        assertFalse(listenNext(over.copy(speechDue = true)))
    }

    @Test
    fun `it waits while the pc is busy, here or at the desk`() {
        for (s in listOf("listening", "thinking", "working", "speaking")) {
            assertFalse(s, listenNext(over.copy(state = AurenState(state = s, origin = "phone"))))
            assertFalse(s, listenNext(over.copy(state = AurenState(state = s, origin = "desk"))))
        }
        // Idle, but a turn is still open (the reply came before the turn closed): not yet.
        assertFalse(listenNext(over.copy(state = idle.copy(origin = "phone"))))
        assertFalse(listenNext(over.copy(state = idle.copy(origin = "desk"))))
        // Not running at all, or starting up.
        assertFalse(listenNext(over.copy(state = AurenState(state = "off"))))
        assertFalse(listenNext(over.copy(state = AurenState(state = "error", detail = "boom"))))
    }

    @Test
    fun `a yes or no question keeps the conversation going`() {
        assertTrue(listenNext(over.copy(state = idle.copy(awaiting = true, question = "Delete it?", ask = "a1"))))
    }

    @Test
    fun `a listen that ends with nothing heard is told apart from a sent one`() {
        val listening = AurenState(state = "listening", origin = "phone")
        assertTrue(heardNothing(listening, AurenState(state = "idle", detail = "heard nothing")))
        assertTrue(heardNothing(listening, AurenState(state = "idle", detail = "key tap ignored")))
        assertFalse(heardNothing(listening, AurenState(state = "thinking", transcript = "what time is it")))
        assertFalse(heardNothing(listening, AurenState(state = "idle", detail = "cancelled")))
        // An idle that didn't come straight from listening isn't this listen's end.
        assertFalse(heardNothing(AurenState(state = "thinking"), AurenState(state = "idle", detail = "heard nothing")))
    }

    @Test
    fun `a turn started at the desk ends the conversation`() {
        val desk = AurenState(state = "listening", origin = "desk")
        assertTrue(deskTookOver(idle, desk))
        assertFalse(deskTookOver(desk, desk.copy(state = "thinking")))            // once, when it starts
        assertFalse(deskTookOver(idle, AurenState(state = "listening", origin = "phone")))
    }
}
