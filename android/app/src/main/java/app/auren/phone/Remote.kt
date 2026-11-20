package app.auren.phone

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.ConnectionPool
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.Inet4Address
import java.util.Locale
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

/** Where the link to the PC stands, and what the user can do about it. */
enum class LinkKind { UNPAIRED, CONNECTING, CONNECTED, UNREACHABLE, SERVER_OFF, REVOKED }

/** [via] is the live path while connected: "Wi-Fi" or "Tailscale". */
data class Link(val kind: LinkKind, val why: String = "", val via: String = "")

enum class Who { YOU, AUREN, NOTE }

/** One line of this session's conversation with Auren (phone turns only; the desk has its own). */
data class Line(val id: String, val who: Who, val text: String, val waiting: Boolean = false)

data class Talk(val id: String, val handsFree: Boolean, val heard: Boolean = false)

/**
 * The one connection to the PC, for every screen, the share sheet and the tile. It keeps a WebSocket open
 * while any screen is showing, over the PC's address on this Wi-Fi when there is one and over Tailscale
 * otherwise, always TLS pinned to the PC's certificate. It reconnects with backoff, resends asks the PC
 * hasn't acked (their ids make a resend that already ran harmless) and, after a reconnect, asks for the
 * replies it missed. A Yes/No is never resent: it answers only the question it was tapped on.
 */
object Remote {
    private lateinit var app: Application
    lateinit var store: Store
        private set
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val plain: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()
    private val clients = HashMap<Pair<String, Network?>, OkHttpClient>()

    /**
     * A client that trusts the one certificate [fp] names, and nothing else. With [net] its sockets go out over
     * that network (the Wi-Fi the PC's LAN address is on), not the default one, which may be mobile data when
     * the Wi-Fi has no internet. Each network gets its own connection pool: OkHttp's pool doesn't tell them apart.
     */
    @Synchronized
    private fun client(fp: String, net: Network? = null): OkHttpClient {
        clients[fp to net]?.let { return it }
        if (clients.size >= 4) clients.clear()          // an old pairing or a Wi-Fi that is gone
        val trust = PinnedTrust(fp)
        val b = plain.newBuilder().sslSocketFactory(trust.socketFactory(), trust).hostnameVerifier(PinnedTrust.ANY_HOST)
        if (net != null) b.socketFactory(net.socketFactory).connectionPool(ConnectionPool())
        return b.build().also { clients[fp to net] = it }
    }

    private val _link = MutableStateFlow(Link(LinkKind.UNPAIRED))
    val link: StateFlow<Link> = _link
    private val _state = MutableStateFlow(AurenState())
    val state: StateFlow<AurenState> = _state
    private val _lines = MutableStateFlow<List<Line>>(emptyList())
    val lines: StateFlow<List<Line>> = _lines
    private val _inbox = MutableStateFlow<List<InboxItem>>(emptyList())
    val inbox: StateFlow<List<InboxItem>> = _inbox
    private val _unread = MutableStateFlow(0)
    val unread: StateFlow<Int> = _unread
    private val _talk = MutableStateFlow<Talk?>(null)
    val talk: StateFlow<Talk?> = _talk
    private val _level = MutableStateFlow(0f)
    val level: StateFlow<Float> = _level
    private val _notices = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val notices: SharedFlow<String> = _notices
    private val _readAloud = MutableStateFlow(false)
    val readAloud: StateFlow<Boolean> = _readAloud
    /** A voice picked here that the PC hasn't confirmed yet (its state still names the old one). */
    private val _pendingVoice = MutableStateFlow<String?>(null)
    val pendingVoice: StateFlow<String?> = _pendingVoice
    private var voiceSetId: String? = null
    /** Brain settings picked here that the PC hasn't confirmed yet: brain, effort or codex-effort to the value. */
    private val _pendingSettings = MutableStateFlow<Map<String, String>>(emptyMap())
    val pendingSettings: StateFlow<Map<String, String>> = _pendingSettings
    private val settingIds = HashMap<String, String>()     // request id -> the setting it changes
    private var forgetId: String? = null
    /** Hands-free as a live conversation: on until the user ends it, or a listen hears nothing (see [listenNext]). */
    private val _conversation = MutableStateFlow(false)
    val conversation: StateFlow<Boolean> = _conversation
    /** A reply handed to the speaker that it hasn't finished (or started) reading: the next listen waits for it. */
    private val _speechDue = MutableStateFlow(false)
    private var speechWatch: Job? = null
    private var lastListen: String? = null          // the newest listen's id: its turn ending without a reply ends the mode
    private var conversationOffAt = 0L
    /** Turns a fresh conversation dropped while they waited: their replies, when they come, aren't shown. */
    private val dropped = LinkedHashSet<String>()

    private var ws: WebSocket? = null
    private var connected = false
    @Volatile private var host: String? = null      // the address the open connection uses
    @Volatile private var gen = 0                   // bumped on pair/unpair: the old PC's connection is dropped
    private var loop: Job? = null
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private class Snapshot(val owner: String, val last: Long, val items: List<InboxItem>)
    private val saves = Channel<Snapshot>(Channel.CONFLATED)
    @Volatile private var inboxLast = 0L             // the newest inbox id the PC has sent; saved with the items
    private val users = Users(scope, GRACE_MS, onLast = ::leftScreen, onIdle = ::idle)
    private val pending = LinkedHashMap<String, Pair<String, Long>>()     // id -> (message, when queued)
    private var mic: Mic? = null
    private val _voiceTurn = MutableStateFlow<String?>(null)
    private var voiceTurn: String?                  // the last listen's id, until its words come back
        get() = _voiceTurn.value
        set(v) { _voiceTurn.value = v }
    /** Reads replies aloud on this phone: Phonon once downloaded, Android's own voice until then. */
    lateinit var speaker: Speaker
        private set
    /** Start a hands-free conversation once connected (the Quick Settings tile). */
    var talkWhenConnected = false

    fun init(application: Application) {
        if (::app.isInitialized) return
        app = application
        store = Store(application)
        _readAloud.value = store.readAloud
        speaker = Speaker(application)
        if (store.readAloud) speaker.prepare()
        val saved = store.inbox.load(store.pc?.owner.orEmpty())
        inboxLast = saved.last
        _inbox.value = saved.items.sortedByDescending { it.id }
        _unread.value = _inbox.value.count { it.id > store.inboxSeen }
        _link.value = Link(if (store.pc == null) LinkKind.UNPAIRED else LinkKind.CONNECTING)
        val cm = application.getSystemService(ConnectivityManager::class.java)
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { wake.trySend(Unit) }
            override fun onLost(network: Network) { wake.trySend(Unit) }
        })
        // One writer, always the newest state: an older save can't land after a newer one. Each save is taken
        // whole on the main thread, so items, cursor and pairing always belong together.
        scope.launch(Dispatchers.IO) { for (x in saves) store.inbox.save(x.owner, x.last, x.items) }
        // Conversation mode: whenever what it waits on changes, decide again; once the turn is over, pause, look
        // again, and open the next listen. A new change during the pause starts the decision over.
        scope.launch {
            combine(listOf<Flow<Any?>>(_conversation, _link, _talk, _state, _lines, _voiceTurn, speaker.speaking, _speechDue)) {
                nextListen()
            }.distinctUntilChanged().collectLatest { ready ->
                if (!ready) return@collectLatest
                delay(LISTEN_PAUSE_MS)
                // talkStart says why it couldn't (the mic is busy, the link went): no retry, so no loop.
                if (nextListen() && !talkStart(handsFree = true)) endConversation(null)
            }
        }
    }

    // --- lifecycle: connected while something shows ----------------------------------------------

    fun start() {
        users.acquire()
        if (loop?.isActive != true) loop = scope.launch { run() }
        else wake.trySend(Unit)
    }

    fun stop() {
        users.release()
    }

    /**
     * The last screen closed: the microphone stops now, and the PC stops listening. A tile tap still waiting
     * for the link is dropped too, so a later hello can't start recording nobody asked for then.
     */
    private fun leftScreen() {
        talkWhenConnected = false
        endConversation(null)
        if (_talk.value == null) return
        stopMic()
        send(Outgoing.cancel())
    }

    /** Nobody came back: close the connection. */
    private fun idle() {
        loop?.cancel()
        loop = null
        close()
        if (_link.value.kind == LinkKind.CONNECTED) _link.value = Link(LinkKind.CONNECTING)
    }

    fun retry() {
        wake.trySend(Unit)
    }

    /** A short message for whichever screen is showing. */
    fun say(text: String) {
        _notices.tryEmit(text)
    }

    private suspend fun run() {
        var backoff = 1_000L
        while (scope.isActive) {
            val pc = store.pc
            if (pc == null) {
                _link.value = Link(LinkKind.UNPAIRED)
                wake.receive()
                continue
            }
            if (_link.value.kind == LinkKind.REVOKED) {
                wake.receive()
                continue
            }
            if (_link.value.kind != LinkKind.CONNECTED) _link.value = Link(LinkKind.CONNECTING)
            val g = gen
            val lans = withContext(Dispatchers.IO) { wifi() }
            val order = tryOrder(pc.hosts, lans.map { it.ip to it.prefix }, pc.last)
            var outcome = Link(LinkKind.UNREACHABLE)
            var greeted = false
            for (h in order) {                  // Wi-Fi first when the PC is on it, then Tailscale
                if (g != gen) break             // paired again while looking: never open a socket to the old PC
                val (o, hello) = connectOnce(pc, h, g, netOf(h, lans))
                if (o.kind != LinkKind.UNREACHABLE || outcome.kind == LinkKind.UNREACHABLE) outcome = o
                greeted = hello
                if (hello || o.kind == LinkKind.REVOKED || g != gen) break     // it was up: start again from the top
            }
            if (g != gen) {                     // paired again, or unpaired: start over with what's stored now
                backoff = 1_000L
                continue
            }
            _link.value = outcome
            if (outcome.kind == LinkKind.REVOKED) continue
            if (greeted) backoff = 1_000L
            withTimeoutOrNull(backoff) { wake.receive() }
            backoff = minOf(backoff * 2, 15_000L)
        }
    }

    private sealed interface Event {
        data class Text(val text: String) : Event
        data class Closed(val code: Int, val reason: String) : Event
        data class Failed(val t: Throwable, val code: Int?) : Event
    }

    /** One WebSocket, until it closes. Returns why it ended, and whether the PC said hello first. */
    private suspend fun connectOnce(pc: Pc, to: String, g: Int, net: Network?): Pair<Link, Boolean> {
        val events = Channel<Event>(Channel.UNLIMITED)
        val req = Request.Builder().url("${pc.ws(to)}/v1/ws").header("Authorization", "Bearer ${pc.token}").build()
        val socket = client(pc.fp, net).newWebSocket(req, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) { events.trySend(Event.Text(text)) }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                events.trySend(Event.Closed(code, reason))
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { events.trySend(Event.Closed(code, reason)) }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                events.trySend(Event.Failed(t, response?.code))
            }
        })
        ws = socket
        var greeted = false
        try {
            for (e in events) {
                if (g != gen) return Link(LinkKind.CONNECTING) to greeted       // a message for the PC before
                when (e) {
                    is Event.Text -> {
                        val m = Incoming.parse(e.text) ?: continue
                        if (m is Incoming.Hello) {
                            greeted = true
                            greet(m, pc, to)
                            continue
                        }
                        if (m is Incoming.Revoked) return revoked() to greeted
                        handle(m)
                    }
                    is Event.Closed -> return (if (e.code == 4401) revoked() else lost("the PC closed the link")) to greeted
                    is Event.Failed -> {
                        if (e.code == 401) return revoked() to greeted
                        return lost(describe(e.t)) to greeted
                    }
                }
            }
        } finally {
            if (ws === socket) {
                connected = false
                ws = null
                host = null
                stopMic()
                endConversation("lost the PC")
            }
            socket.cancel()
        }
        return Link(LinkKind.UNREACHABLE) to greeted
    }

    private fun lost(why: String) = Link(if (why.startsWith(REFUSED)) LinkKind.SERVER_OFF else LinkKind.UNREACHABLE, why)

    private fun revoked(): Link {
        endConversation(null)                   // the screen says this phone was unpaired
        stopMic()
        return Link(LinkKind.REVOKED)
    }

    private fun describe(t: Throwable): String {
        val msg = (t.message ?: t.javaClass.simpleName)
        if (t is SSLException) return "$PIN: $msg"
        return if ("ECONNREFUSED" in msg || "refused" in msg.lowercase(Locale.ROOT)) "$REFUSED: $msg" else msg
    }

    private fun close() {
        ws?.close(1000, null)
        ws = null
        host = null
        connected = false
    }

    // --- what the PC says ---------------------------------------------------------------------------

    /** The PC said hello on [to]: remember the address that worked and the ones it listens on now. */
    private fun greet(m: Incoming.Hello, pc: Pc, to: String) {
        connected = true
        host = to
        _link.value = Link(LinkKind.CONNECTED, via = pathOf(to))
        val hosts = mergeHosts(pc.hosts, m.addrs, to)     // a new LAN IP, say
        if (hosts != pc.hosts || pc.last != to) store.pc = pc.copy(hosts = hosts, last = to)
        _pendingVoice.value = null          // a pick sent on the old link: the PC's state says what took
        voiceSetId = null
        clearBrainRequests()
        setState(m.state)
        if (m.inboxLast < inboxLast) {      // the PC's inbox was reset: its new items reuse the old ids
            inboxLast = 0
            store.inboxSeen = 0
            clearInbox()
        }
        send(Outgoing.sync(inboxLast))
        // An old ask isn't sent again, but the PC may have taken it before the link dropped: it is resumed
        // below like the rest, so its reply (or "lost") still comes back and its line stops waiting.
        val now = System.currentTimeMillis()
        val stale = pending.filterValues { now - it.second > STALE_MS }.keys.toList()
        stale.forEach { pending.remove(it) }
        pending.values.forEach { send(it.first) }
        // What the PC took but hadn't answered when the link dropped: its reply, or "lost", comes back.
        val waiting = _lines.value.filter { it.waiting && it.id !in pending }.map { it.id } + stale + listOfNotNull(voiceTurn)
        if (waiting.isNotEmpty()) send(Outgoing.resume(waiting.distinct()))
        if (talkWhenConnected) {
            talkWhenConnected = false
            startConversation()
        }
    }

    private fun handle(m: Incoming) {
        when (m) {
            is Incoming.Hello -> Unit
            is Incoming.State -> setState(m.state)
            is Incoming.Lost -> {
                pending.remove(m.id)
                if (_lines.value.any { it.id == m.id && it.waiting } || voiceTurn == m.id) {
                    addLine(Line("lost-${m.id}", Who.NOTE, "The reply to that was lost. Ask again if you still need it."))
                }
                if (voiceTurn == m.id) voiceTurn = null
                done(m.id)
            }
            is Incoming.Ended -> {
                pending.remove(m.id)
                // The newest listen's turn ended with no reply: it heard nothing (no words came back), or it was
                // stopped or failed after them. Either way the conversation stops rather than listen again.
                if (_conversation.value && m.id == lastListen && m.id !in dropped) {
                    endConversation(if (_lines.value.none { it.id == m.id }) "you went quiet" else "that turn ended without a reply")
                }
                if (voiceTurn == m.id) voiceTurn = null
                done(m.id)
            }
            is Incoming.Reply -> {
                if (voiceTurn == m.id) voiceTurn = null
                if (m.id in dropped) return         // asked before a fresh start: not shown, not read
                done(m.id)
                val text = m.text.ifBlank { "Done." }
                addLine(Line("r-${m.id}-${m.at}", Who.AUREN, text))
                if (_readAloud.value) {
                    speaker.speak(text, Voices.forState(_state.value.voiceName).name)
                    expectSpeech()
                }
            }
            is Incoming.Inbox -> addInbox(m.item)
            is Incoming.Ack -> {
                pending.remove(m.id)
                if (m.id == voiceSetId) {
                    voiceSetId = null
                    if (!m.ok) {
                        _pendingVoice.value = null
                        _notices.tryEmit("Couldn't change the voice: " + m.status.removePrefix("error: "))
                    }
                    return
                }
                if (m.id == forgetId) {
                    forgetId = null
                    if (m.ok) freshStart()
                    else _notices.tryEmit("Couldn't start fresh: " + m.status.removePrefix("error: "))
                    return
                }
                val setting = settingIds.remove(m.id)
                if (setting != null) {
                    val want = _pendingSettings.value[setting]
                    // A later pick of the same setting may still be on its way: only this one's pending goes.
                    val newer = setting in settingIds.values
                    if (!m.ok) {
                        if (!newer) _pendingSettings.value = _pendingSettings.value - setting
                        _notices.tryEmit(
                            (if (setting == "brain") "Couldn't change who answers: " else "Couldn't change the effort: ") +
                                m.status.removePrefix("error: "))
                    } else if (!newer && want == _state.value.setting(setting)) {
                        _pendingSettings.value = _pendingSettings.value - setting     // no change, so no new state
                    }
                    return
                }
                val talk = _talk.value
                val listen = m.id.isNotEmpty() && m.id == lastListen
                when {
                    m.busy -> {                     // the Busy message before it said who has the turn
                        if (talk?.id == m.id) stopMic()
                        done(m.id)
                        if (listen) endConversation(null)
                    }
                    // In conversation mode, "Hands-free off: you went quiet" says it (the turn's end comes first).
                    m.status == "heard nothing" || m.status == "too short" ->
                        if (!_conversation.value && System.currentTimeMillis() - conversationOffAt > 3_000) {
                            _notices.tryEmit("Didn't catch that")
                        }
                    !m.ok && m.status.isNotEmpty() && m.status !in QUIET -> {
                        if (talk?.id == m.id) stopMic()
                        done(m.id)
                        val why = m.status.removePrefix("error: ")
                        if (listen && _conversation.value) endConversation(why)
                        else _notices.tryEmit(why.replaceFirstChar { it.uppercase() })
                    }
                }
            }
            is Incoming.Busy -> {
                val why = if (m.owner == "desk") "Auren is busy at the desk" else "Auren is busy"
                if (_conversation.value && m.id.isNotEmpty() && m.id == lastListen) endConversation(why)
                else _notices.tryEmit(why)
            }
            is Incoming.Error -> {
                // A PC too old to know forget or setting answers with an error: nothing stays pending.
                if (m.id == forgetId) forgetId = null
                settingIds.remove(m.id)?.let { name ->
                    if (name !in settingIds.values) _pendingSettings.value = _pendingSettings.value - name
                }
                if (_conversation.value && m.id.isNotEmpty() && m.id == lastListen) endConversation(m.error)
                else _notices.tryEmit(m.error)
            }
            is Incoming.Revoked, is Incoming.Pong -> Unit
        }
    }

    private fun setState(s: AurenState) {
        val before = _state.value
        _state.value = s
        if (Voices.byName(s.voiceName) == Voices.byName(_pendingVoice.value)) _pendingVoice.value = null
        val picked = _pendingSettings.value
        if (picked.isNotEmpty()) _pendingSettings.value = picked.filterNot { (name, v) -> s.setting(name) == v }
        // The words the PC heard become this turn's line, once it starts thinking about them.
        val voice = voiceTurn
        if (voice != null && s.origin == "phone" && s.transcript.isNotBlank() && before.state == "listening" &&
            s.state in setOf("thinking", "working")) {
            voiceTurn = null
            addLine(Line(voice, Who.YOU, s.transcript, waiting = true))
        }
        if (_conversation.value) {
            // Backups for the turn's end message: the PC's own word that the listen heard nothing, or failed.
            if (voiceTurn != null && heardNothing(before, s)) endConversation("you went quiet")
            else if (s.state == "error" && before.state != "error" &&
                (_talk.value != null || voiceTurn != null || _lines.value.any { it.waiting })) {
                endConversation(s.detail.ifBlank { "Auren hit an error" }.take(80))
            }
        }
        val talk = _talk.value ?: return
        if (s.state == "listening" && s.origin == "phone") {
            if (!talk.heard) _talk.value = talk.copy(heard = true)
        } else if (talk.heard) {
            stopMic()                           // it sent by itself (hands-free), or the turn was stopped
        }
    }

    // --- what the phone asks ------------------------------------------------------------------------

    fun newId() = UUID.randomUUID().toString().substring(0, 12)

    private fun send(text: String): Boolean = connected && ws?.send(text) == true

    fun ask(text: String) {
        val t = text.trim()
        if (t.isEmpty()) return
        val id = newId()
        val msg = Outgoing.ask(id, t)
        speaker.stop()
        pending[id] = msg to System.currentTimeMillis()
        addLine(Line(id, Who.YOU, t, waiting = true))
        if (!send(msg)) _notices.tryEmit("Sends when the PC is back")
    }

    /**
     * Yes or No to the question [ask] names: the one on screen when it was tapped, so the PC refuses it if
     * another question took its place. Not kept for later: after a reconnect, the PC asks again.
     */
    fun answer(yes: Boolean, ask: String) {
        if (ask.isEmpty()) return
        if (!send(Outgoing.answer(newId(), yes, ask))) _notices.tryEmit("Not connected to the PC")
    }

    fun cancel() {
        endConversation(null)
        stopMic()
        speaker.stop()
        if (!send(Outgoing.cancel())) _notices.tryEmit("Not connected to the PC")
    }

    /** Start listening on the PC with this phone's microphone. The caller has RECORD_AUDIO. */
    fun talkStart(handsFree: Boolean): Boolean {
        if (_talk.value != null) return false
        if (!connected) {
            _notices.tryEmit("Not connected to the PC")
            return false
        }
        speaker.stop()                          // don't talk over the user, or into the microphone
        val id = newId()
        val socket = ws ?: return false
        val m = Mic(
            onChunk = { socket.send(it.toByteString()) },
            onLevel = { _level.value = it },
        )
        // talk.start goes first: the PC handles frames in order, so none arrive before it's listening.
        if (!send(Outgoing.talkStart(id, handsFree))) return false
        if (!m.start()) {
            send(Outgoing.cancel())
            _notices.tryEmit("The microphone is busy")
            return false
        }
        mic = m
        voiceTurn = id
        lastListen = id
        _talk.value = Talk(id, handsFree)
        return true
    }

    /**
     * Hands-free (Home, the tile): a live conversation. It listens now, and after each reply (once this phone
     * has read it aloud, and the PC is idle) it listens again by itself, until [stopConversation], Stop, a
     * listen that hears nothing, a busy PC, an error or the link going. The caller has RECORD_AUDIO.
     */
    fun startConversation(): Boolean {
        if (_conversation.value) return false
        if (!talkStart(handsFree = true)) return false
        _conversation.value = true
        ConversationService.start(app)          // it keeps going with the screen off
        return true
    }

    /** Hands-free tapped again: the conversation ends, and a listen still open is dropped, not sent. */
    fun stopConversation() {
        endConversation(null)
        if (_talk.value == null) return
        stopMic()
        send(Outgoing.cancel())
    }

    /** Stop with nothing running on the PC: just stop reading aloud, and end the conversation. */
    fun hush() {
        endConversation(null)
        speaker.stop()
    }

    /** Conversation mode goes off; [why] (when it went off by itself) is shown as "Hands-free off: why". */
    private fun endConversation(why: String?) {
        if (!_conversation.value) return
        _conversation.value = false
        conversationOffAt = System.currentTimeMillis()
        if (why != null) _notices.tryEmit("Hands-free off: $why")
    }

    private fun nextListen() = listenNext(Between(
        on = _conversation.value,
        connected = connected,
        talkOpen = _talk.value != null,
        replyPending = voiceTurn != null || _lines.value.any { it.waiting },
        speaking = speaker.speaking.value,
        speechDue = _speechDue.value,
        state = _state.value,
    ))

    /**
     * The speaker was just given a reply: until it has read it, the next listen waits. Android's own voice
     * starts a moment later, so this waits for the start too, but not forever (a voice that never starts).
     */
    private fun expectSpeech() {
        speechWatch?.cancel()
        _speechDue.value = true
        speechWatch = scope.launch {
            if (withTimeoutOrNull(SPEECH_START_MS) { speaker.speaking.first { it } } != null) speaker.speaking.first { !it }
            _speechDue.value = false
        }
    }

    private fun clearSpeech() {
        speechWatch?.cancel()
        speechWatch = null
        _speechDue.value = false
    }

    /** Release: send what was said. */
    fun talkStop() {
        if (_talk.value == null) return
        stopMic()
        send(Outgoing.talkStop())
    }

    private fun stopMic() {
        mic?.stop()
        mic = null
        _talk.value = null
        _level.value = 0f
    }

    // --- the conversation and the inbox ---------------------------------------------------------------

    private fun addLine(line: Line) {
        _lines.value = (_lines.value.filterNot { it.id == line.id } + line).takeLast(60)
    }

    /**
     * The PC started a fresh conversation: so does the chat here, empty like a new one. A turn still waiting is
     * dropped (the PC's forget waits for it, and a fresh start means the user is done with it): its reply isn't
     * shown or read when it comes, and an ask not acked yet isn't sent again. Reading aloud stops. A listen
     * still open stays: its words are the new conversation's first turn.
     */
    private fun freshStart() {
        val talking = _talk.value != null
        val drop = _lines.value.filter { it.waiting }.map { it.id } + listOfNotNull(voiceTurn.takeIf { !talking })
        drop.forEach {
            pending.remove(it)
            dropped.add(it)
        }
        while (dropped.size > 32) dropped.remove(dropped.first())
        if (!talking) voiceTurn = null
        _lines.value = emptyList()
        clearSpeech()
        speaker.stop()
    }

    private fun done(id: String) {
        _lines.value = _lines.value.map { if (it.id == id && it.waiting) it.copy(waiting = false) else it }
    }

    private fun addInbox(item: InboxItem) {
        if (_inbox.value.any { it.id == item.id }) return
        val all = (_inbox.value + item).sortedByDescending { it.id }.take(Store.KEEP)
        _inbox.value = all
        if (item.id > inboxLast) inboxLast = item.id
        if (item.id > store.inboxSeen) _unread.value = all.count { it.id > store.inboxSeen }
        saveInbox()
    }

    private fun saveInbox() {
        saves.trySend(Snapshot(store.pc?.owner.orEmpty(), inboxLast, _inbox.value))
    }

    fun inboxSeen() {
        store.inboxSeen = maxOf(store.inboxSeen, _inbox.value.firstOrNull()?.id ?: 0)
        _unread.value = 0
    }

    /** Clear the list on this phone. The cursor stays: the PC doesn't send these again. */
    fun clearInbox() {
        _inbox.value = emptyList()
        _unread.value = 0
        saveInbox()
        scope.launch(Dispatchers.IO) { File(app.cacheDir, "inbox").deleteRecursively() }
    }

    fun setReadAloud(on: Boolean) {
        store.readAloud = on
        _readAloud.value = on
        if (on) speaker.prepare() else speaker.stop()
    }

    /** The voice Auren speaks in, here and on the PC: one setting, kept on the PC. */
    fun setVoice(name: String) {
        val v = Voices.byName(name) ?: return
        if (v == Voices.byName(_state.value.voiceName) && _pendingVoice.value == null) return
        val id = newId()
        if (!send(Outgoing.voiceSet(id, v.name))) {
            _notices.tryEmit("Not connected to the PC")
            return
        }
        voiceSetId = id
        _pendingVoice.value = v.name
    }

    /** Start a fresh conversation on the PC, like the bar panel's Fresh. Needs the link: it isn't queued. */
    fun forget() {
        val id = newId()
        if (!send(Outgoing.forget(id))) {
            _notices.tryEmit("Not connected to the PC")
            return
        }
        forgetId = id
    }

    /**
     * Who answers ("brain": auto, claude, codex) and how hard it thinks ("effort" for Claude, "codex-effort"):
     * kept on the PC, like the voice. Shown as pending until a state carries [value].
     */
    fun setSetting(name: String, value: String) {
        if (value == _state.value.setting(name) && _pendingSettings.value[name] == null) return
        val id = newId()
        if (!send(Outgoing.setting(id, name, value))) {
            _notices.tryEmit("Not connected to the PC")
            return
        }
        settingIds[id] = name
        _pendingSettings.value = _pendingSettings.value + (name to value)
    }

    /** Requests sent on a link that is gone: the PC's next state says what took. */
    private fun clearBrainRequests() {
        _pendingSettings.value = emptyMap()
        settingIds.clear()
        forgetId = null
    }

    // --- pairing, sharing and files (HTTPS, pinned) -----------------------------------------------------

    /** Trade the pairing code for a token. Returns null when paired, or what went wrong. */
    suspend fun pair(p: Pairing): String? = withContext(Dispatchers.IO) {
        val body = JSONObject().put("code", p.code).put("name", deviceName()).toString()
        var refused = false
        var wrongPc: String? = null
        val lans = wifi()
        for (h in tryOrder(p.hosts, lans.map { it.ip to it.prefix }, null)) {
            val req = Request.Builder().url("https://$h:${p.port}/v1/pair").post(body.toRequestBody(JSON)).build()
            try {
                client(p.fp, netOf(h, lans)).newCall(req).execute().use { r ->
                    val o = runCatching { JSONObject(r.body?.string().orEmpty()) }.getOrElse { JSONObject() }
                    if (!r.isSuccessful) return@withContext o.optString("error").ifBlank { "The PC said ${r.code}" }
                    val dev = o.optJSONObject("device") ?: JSONObject()
                    val addrs = o.optJSONArray("addrs")?.let { a ->
                        Pairing.hosts((0 until a.length()).mapNotNull { a.optJSONObject(it)?.optString("ip") })
                    }.orEmpty()
                    val pc = Pc((addrs + h).distinct(), p.port, o.getString("token"), dev.optString("id"),
                        o.optString("pc", p.pc), p.fp, last = h)
                    withContext(Dispatchers.Main) {
                        reset()
                        store.pc = pc
                        _link.value = Link(LinkKind.CONNECTING)
                        wake.trySend(Unit)
                    }
                    return@withContext null
                }
            } catch (e: IOException) {
                val why = describe(e)
                if (why.startsWith(REFUSED)) refused = true
                if (why.startsWith(PIN)) wrongPc = h
            }
        }
        when {
            wrongPc != null -> "Something at $wrongPc answered, but it isn't ${p.pc}: its certificate doesn't match the code."
            refused -> "Auren isn't serving on ${p.pc}. Is the daemon running? Try auren doctor."
            else -> "Can't reach ${p.pc}. Is this phone on the same Wi-Fi, or on Tailscale?"
        }
    }

    /** Forget the PC on this phone. (`auren phone revoke` on the PC is what stops the token working.) */
    fun unpair() {
        store.pc = null
        reset()
        _link.value = Link(LinkKind.UNPAIRED)
        wake.trySend(Unit)
    }

    /**
     * Everything that belonged to the PC this phone was paired with: its connection, unsent asks, the
     * conversation, its state, its inbox and the inbox cursor. A new pairing starts clean.
     */
    private fun reset() {
        gen++
        ws?.cancel()
        ws = null
        host = null
        connected = false
        endConversation(null)
        stopMic()
        speaker.stop()
        clearSpeech()
        dropped.clear()
        lastListen = null
        _pendingVoice.value = null
        voiceSetId = null
        clearBrainRequests()
        pending.clear()
        voiceTurn = null
        talkWhenConnected = false
        _lines.value = emptyList()
        _state.value = AurenState()
        inboxLast = 0
        store.inboxSeen = 0
        clearInbox()
    }

    /** The addresses to try for a request, each with the network to use: the live connection's first, then the usual order. */
    private fun routes(pc: Pc): List<Pair<String, Network?>> {
        val lans = wifi()
        return (listOfNotNull(host) + tryOrder(pc.hosts, lans.map { it.ip to it.prefix }, pc.last)).distinct()
            .map { it to netOf(it, lans) }
    }

    /**
     * POST /v1/share. Returns the PC's status ("ok", "in progress", "busy", "error: ...") or why it couldn't
     * send. [id] stays the same when the user tries again, so a share that ran but whose answer got lost
     * doesn't run twice. An ask is a waiting line from the start, so a reconnect resumes its reply. It goes
     * only when the PC turned the ask down: when the request may have reached the PC (it failed on the way),
     * it stays waiting, and the next hello resumes it (the reply, or "lost").
     */
    suspend fun share(id: String, kind: String, action: String, text: String, note: String, data: String? = null,
                      mime: String? = null): String = withContext(Dispatchers.Main) {
        val asking = action == "ask"
        if (asking && _lines.value.none { it.id == id }) addLine(Line(id, Who.YOU, listOf(note,
            if (kind == "image") "(an image)" else text).filter { it.isNotBlank() }.joinToString("\n"), waiting = true))
        val p = post(id, kind, action, text, note, data, mime)
        if (asking && p.answered && p.status != "ok" && p.status != "in progress") {
            _lines.value = _lines.value.filterNot { it.id == id }
        }
        p.status
    }

    /** What a share POST came to: [answered] when the PC (or this phone, unpaired) said so, not a failed send. */
    private class Posted(val status: String, val answered: Boolean = true)

    private suspend fun post(id: String, kind: String, action: String, text: String, note: String, data: String?,
                             mime: String?): Posted =
        withContext(Dispatchers.IO) {
            val pc = store.pc ?: return@withContext Posted("error: not paired")
            val body = Outgoing.share(id, kind, action, text, note, data, mime)
            var last = ""
            for ((h, net) in routes(pc)) {      // the same id on every try: the PC runs it at most once
                val req = Request.Builder().url("${pc.base(h)}/v1/share").header("Authorization", "Bearer ${pc.token}")
                    .post(body.toRequestBody(JSON)).build()
                try {
                    client(pc.fp, net).newCall(req).execute().use { r ->
                        if (r.code == 401) return@withContext Posted("error: this phone isn't paired any more")
                        val raw = r.body?.string().orEmpty()     // cut off here: unknown, like a failed send
                        val o = runCatching { JSONObject(raw) }.getOrElse { JSONObject() }
                        return@withContext Posted(o.optString("status").ifBlank { o.optString("error", "error: ${r.code}") })
                    }
                } catch (e: IOException) {
                    last = describe(e)
                }
            }
            Posted(if (last.startsWith(REFUSED)) "error: Auren isn't serving on the PC" else "error: can't reach the PC",
                answered = false)
        }

    /**
     * An inbox item's file, downloaded once into the cache. Files are kept per pairing (item ids restart with
     * another PC), and one still coming when the phone is paired again is thrown away, not cached.
     */
    suspend fun download(item: InboxItem): File? = withContext(Dispatchers.IO) {
        val g = gen
        val pc = store.pc ?: return@withContext null
        val f = item.file ?: return@withContext null
        val dir = File(app.cacheDir, "inbox/${pc.owner.replace(Regex("[^\\w.-]"), "_")}").apply { mkdirs() }
        val out = File(dir, "${item.id}-${f.name.replace(Regex("[^\\w.-]"), "_")}")
        if (out.exists() && out.length() == f.size) return@withContext out
        for ((h, net) in routes(pc)) {
            val req = Request.Builder().url("${pc.base(h)}/v1/inbox/${item.id}/file")
                .header("Authorization", "Bearer ${pc.token}").build()
            val tmp = File.createTempFile(out.name, ".part", dir)
            try {
                client(pc.fp, net).newCall(req).execute().use { r ->
                    if (!r.isSuccessful) return@withContext null
                    r.body?.byteStream()?.use { input -> tmp.outputStream().use { input.copyTo(it) } } ?: return@withContext null
                    if (g != gen) return@withContext null
                    tmp.renameTo(out)
                    return@withContext out.takeIf { it.exists() }
                }
            } catch (_: IOException) {
            } finally {
                tmp.delete()                    // gone already once renamed; otherwise no half file stays behind
            }
        }
        null
    }

    // --- the network --------------------------------------------------------------------------------

    /** One of this phone's Wi-Fi (or Ethernet) IPv4 addresses, its prefix length and the network it is on. */
    private class Lan(val ip: String, val prefix: Int, val net: Network)

    /** The network to reach [host] over: the Wi-Fi it is on, for a LAN address; the default one otherwise. */
    private fun netOf(host: String, lans: List<Lan>): Network? =
        if (isPrivateLan(host)) lans.firstOrNull { onSubnet(host, it.ip, it.prefix) }?.net else null

    /** This phone's own Wi-Fi (or Ethernet) addresses: which LANs it is on. */
    private fun wifi(): List<Lan> = try {
        val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        @Suppress("DEPRECATION")
        cm.allNetworks.flatMap { n ->
            val caps = cm.getNetworkCapabilities(n)
            val local = caps != null && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
            if (!local) emptyList()
            else cm.getLinkProperties(n)?.linkAddresses.orEmpty()
                .filter { it.address is Inet4Address }
                .map { Lan(it.address.hostAddress.orEmpty(), it.prefixLength, n) }
        }
    } catch (_: Exception) {
        emptyList()
    }

    private fun deviceName(): String {
        val m = Build.MODEL.orEmpty()
        return if (m.startsWith(Build.MANUFACTURER.orEmpty(), ignoreCase = true)) m
        else "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} $m"
    }

    private val JSON = "application/json".toMediaType()
    private const val REFUSED = "refused"
    private const val PIN = "tls"
    private const val GRACE_MS = 4_000L                // a quick app switch keeps the link
    private const val SPEECH_START_MS = 8_000L        // Android's voice starting up; Phonon starts at once
    private const val STALE_MS = 2 * 60_000L          // an ask older than this isn't resent: it would surprise
    private val QUIET = setOf("not listening", "cancelled", "nothing is waiting for an answer", "in progress")
}
