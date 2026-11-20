package app.auren.phone

/**
 * Hands-free as a live conversation: after Auren's reply to a turn, the phone opens the microphone for the
 * next one by itself. Plain Kotlin, so the decision is unit tested off the device; Remote holds the state.
 */
data class Between(
    val on: Boolean,                 // conversation mode is on
    val connected: Boolean,
    val talkOpen: Boolean,           // a listen is open on this phone: never two at once
    val replyPending: Boolean,       // a turn of this phone's is still being heard or answered
    val speaking: Boolean,           // this phone is reading a reply aloud: the mic would hear it
    val speechDue: Boolean,          // a reply was handed to the speaker, which hasn't started or finished it
    val state: AurenState,
)

/**
 * Whether the next hands-free listen should start now (after a short pause, checked again). Only when the
 * last turn is over everywhere: its reply came, the phone has finished reading it, and the PC is idle with
 * nobody's turn open (not busy at the desk). A yes/no question waiting (state.awaiting) doesn't stop it:
 * the next listen is how the user answers by voice.
 */
fun listenNext(b: Between): Boolean =
    b.on && b.connected && !b.talkOpen && !b.replyPending && !b.speaking && !b.speechDue &&
        b.state.state == "idle" && b.state.origin == null && !b.state.active && !b.state.busyAtDesk

/** The details the PC gives a listen that ended without words: hands-free went quiet, or a tap too short. */
val QUIET_ENDS = setOf("heard nothing", "key tap ignored")

/** The PC ended a listen having heard nothing (hands-free gives up after 12 s of silence). */
fun heardNothing(before: AurenState, now: AurenState): Boolean =
    before.state == "listening" && now.state == "idle" && now.detail in QUIET_ENDS

/** How long conversation mode waits, once the turn is over, before it opens the microphone again. */
const val LISTEN_PAUSE_MS = 400L
