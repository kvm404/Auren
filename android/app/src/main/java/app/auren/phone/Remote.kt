package app.auren.phone

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.speech.tts.TextToSpeech
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
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
    private var pinned: Pair<String, OkHttpClient>? = null

    /** A client that trusts the one certificate [fp] names, and nothing else. */
    @Synchronized
    private fun client(fp: String): OkHttpClient {
        pinned?.let { (f, c) -> if (f == fp) return c }
        val trust = PinnedTrust(fp)
        return plain.newBuilder().sslSocketFactory(trust.socketFactory(), trust).hostnameVerifier(PinnedTrust.ANY_HOST)
            .build().also { pinned = fp to it }
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

    private var ws: WebSocket? = null
    private var connected = false
    @Volatile private var host: String? = null      // the address the open connection uses
    private var gen = 0                             // bumped on pair/unpair: the old PC's connection is dropped
    private var loop: Job? = null
    private val wake = Channel<Unit>(Channel.CONFLATED)
    private val saves = Channel<Unit>(Channel.CONFLATED)
    @Volatile private var inboxLast = 0L             // the newest inbox id the PC has sent; saved with the items
    private val users = Users(scope, GRACE_MS, onLast = ::leftScreen, onIdle = ::idle)
    private val pending = LinkedHashMap<String, Pair<String, Long>>()     // id -> (message, when queued)
    private var mic: Mic? = null
    private var voiceTurn: String? = null          // the last listen's id, until its words come back
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    /** Start a hands-free turn once connected (the Quick Settings tile). */
    var talkWhenConnected = false

    fun init(application: Application) {
        if (::app.isInitialized) return
        app = application
        store = Store(application)
        _readAloud.value = store.readAloud
        val saved = store.inbox.load()
        inboxLast = saved.last
        _inbox.value = saved.items.sortedByDescending { it.id }
        _unread.value = _inbox.value.count { it.id > store.inboxSeen }
        _link.value = Link(if (store.pc == null) LinkKind.UNPAIRED else LinkKind.CONNECTING)
        val cm = application.getSystemService(ConnectivityManager::class.java)
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { wake.trySend(Unit) }
            override fun onLost(network: Network) { wake.trySend(Unit) }
        })
        // One writer, always the newest state: an older save can't land after a newer one.
        scope.launch(Dispatchers.IO) { for (x in saves) store.inbox.save(inboxLast, _inbox.value) }
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
            val order = tryOrder(pc.hosts, withContext(Dispatchers.IO) { wifi() }, pc.last)
            var outcome = Link(LinkKind.UNREACHABLE)
            var greeted = false
            for (h in order) {                  // Wi-Fi first when the PC is on it, then Tailscale
                val (o, hello) = connectOnce(pc, h, g)
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
    private suspend fun connectOnce(pc: Pc, to: String, g: Int): Pair<Link, Boolean> {
        val events = Channel<Event>(Channel.UNLIMITED)
        val req = Request.Builder().url("${pc.ws(to)}/v1/ws").header("Authorization", "Bearer ${pc.token}").build()
        val socket = client(pc.fp).newWebSocket(req, object : WebSocketListener() {
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
            }
            socket.cancel()
        }
        return Link(LinkKind.UNREACHABLE) to greeted
    }

    private fun lost(why: String) = Link(if (why.startsWith(REFUSED)) LinkKind.SERVER_OFF else LinkKind.UNREACHABLE, why)

    private fun revoked(): Link {
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
        val hosts = if (m.addrs.isEmpty()) pc.hosts else (m.addrs + to).distinct()     // a new LAN IP, say
        if (hosts != pc.hosts || pc.last != to) store.pc = pc.copy(hosts = hosts, last = to)
        setState(m.state)
        if (m.inboxLast < inboxLast) inboxLast = 0                // the PC's inbox was reset
        send(Outgoing.sync(inboxLast))
        val now = System.currentTimeMillis()
        pending.filterValues { now - it.second > STALE_MS }.keys.forEach {
            pending.remove(it)
            done(it)
        }
        pending.values.forEach { send(it.first) }
        // What the PC took but hadn't answered when the link dropped: its reply, or "lost", comes back.
        val waiting = _lines.value.filter { it.waiting && it.id !in pending }.map { it.id } + listOfNotNull(voiceTurn)
        if (waiting.isNotEmpty()) send(Outgoing.resume(waiting.distinct()))
        if (talkWhenConnected) {
            talkWhenConnected = false
            talkStart(handsFree = true)
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
                if (voiceTurn == m.id) voiceTurn = null
                done(m.id)
            }
            is Incoming.Reply -> {
                if (voiceTurn == m.id) voiceTurn = null
                done(m.id)
                val text = m.text.ifBlank { "Done." }
                addLine(Line("r-${m.id}-${m.at}", Who.AUREN, text))
                if (_readAloud.value) speak(text)
            }
            is Incoming.Inbox -> addInbox(m.item)
            is Incoming.Ack -> {
                pending.remove(m.id)
                val talk = _talk.value
                when {
                    m.busy -> {                     // the Busy message before it said who has the turn
                        if (talk?.id == m.id) stopMic()
                        done(m.id)
                    }
                    m.status == "heard nothing" || m.status == "too short" -> _notices.tryEmit("Didn't catch that")
                    !m.ok && m.status.isNotEmpty() && m.status !in QUIET -> {
                        if (talk?.id == m.id) stopMic()
                        done(m.id)
                        _notices.tryEmit(m.status.removePrefix("error: ").replaceFirstChar { it.uppercase() })
                    }
                }
            }
            is Incoming.Busy -> _notices.tryEmit(if (m.owner == "desk") "Auren is busy at the desk" else "Auren is busy")
            is Incoming.Error -> _notices.tryEmit(m.error)
            is Incoming.Revoked, is Incoming.Pong -> Unit
        }
    }

    private fun setState(s: AurenState) {
        val before = _state.value
        _state.value = s
        // The words the PC heard become this turn's line, once it starts thinking about them.
        val voice = voiceTurn
        if (voice != null && s.origin == "phone" && s.transcript.isNotBlank() && before.state == "listening" &&
            s.state in setOf("thinking", "working")) {
            voiceTurn = null
            addLine(Line(voice, Who.YOU, s.transcript, waiting = true))
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
        stopMic()
        if (!send(Outgoing.cancel())) _notices.tryEmit("Not connected to the PC")
    }

    /** Start listening on the PC with this phone's microphone. The caller has RECORD_AUDIO. */
    fun talkStart(handsFree: Boolean): Boolean {
        if (_talk.value != null) return false
        if (!connected) {
            _notices.tryEmit("Not connected to the PC")
            return false
        }
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
        _talk.value = Talk(id, handsFree)
        return true
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

    private fun done(id: String) {
        _lines.value = _lines.value.map { if (it.id == id && it.waiting) it.copy(waiting = false) else it }
    }

    private fun addInbox(item: InboxItem) {
        if (_inbox.value.any { it.id == item.id }) return
        val all = (_inbox.value + item).sortedByDescending { it.id }.take(Store.KEEP)
        _inbox.value = all
        if (item.id > inboxLast) inboxLast = item.id
        if (item.id > store.inboxSeen) _unread.value = all.count { it.id > store.inboxSeen }
        saves.trySend(Unit)
    }

    fun inboxSeen() {
        store.inboxSeen = maxOf(store.inboxSeen, _inbox.value.firstOrNull()?.id ?: 0)
        _unread.value = 0
    }

    /** Clear the list on this phone. The cursor stays: the PC doesn't send these again. */
    fun clearInbox() {
        _inbox.value = emptyList()
        _unread.value = 0
        saves.trySend(Unit)
        scope.launch(Dispatchers.IO) { File(app.cacheDir, "inbox").deleteRecursively() }
    }

    fun setReadAloud(on: Boolean) {
        store.readAloud = on
        _readAloud.value = on
        if (!on) tts?.stop()
    }

    private fun speak(text: String) {
        val t = tts
        if (t == null) {
            tts = TextToSpeech(app) { status ->
                ttsReady = status == TextToSpeech.SUCCESS
                if (ttsReady) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "auren")
            }
        } else if (ttsReady) {
            t.speak(text, TextToSpeech.QUEUE_FLUSH, null, "auren")
        }
    }

    fun shutUp() {
        tts?.stop()
    }

    // --- pairing, sharing and files (HTTPS, pinned) -----------------------------------------------------

    /** Trade the pairing code for a token. Returns null when paired, or what went wrong. */
    suspend fun pair(p: Pairing): String? = withContext(Dispatchers.IO) {
        val body = JSONObject().put("code", p.code).put("name", deviceName()).toString()
        var refused = false
        var wrongPc: String? = null
        for (h in tryOrder(p.hosts, wifi(), null)) {
            val req = Request.Builder().url("https://$h:${p.port}/v1/pair").post(body.toRequestBody(JSON)).build()
            try {
                client(p.fp).newCall(req).execute().use { r ->
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
        stopMic()
        pending.clear()
        voiceTurn = null
        talkWhenConnected = false
        _lines.value = emptyList()
        _state.value = AurenState()
        inboxLast = 0
        store.inboxSeen = 0
        clearInbox()
    }

    /** The addresses to try for a request: the live connection's first, then the usual order. */
    private fun routes(pc: Pc): List<String> = (listOfNotNull(host) + tryOrder(pc.hosts, wifi(), pc.last)).distinct()

    /**
     * POST /v1/share. Returns the PC's status ("ok", "in progress", "busy", "error: ...") or why it couldn't
     * send. [id] stays the same when the user tries again, so a share that ran but whose answer got lost
     * doesn't run twice. An ask is a waiting line from the start, so a reconnect resumes its reply.
     */
    suspend fun share(id: String, kind: String, action: String, text: String, note: String, data: String? = null,
                      mime: String? = null): String = withContext(Dispatchers.Main) {
        val asking = action == "ask" && _lines.value.none { it.id == id }
        if (asking) addLine(Line(id, Who.YOU, listOf(note, if (kind == "image") "(an image)" else text)
            .filter { it.isNotBlank() }.joinToString("\n"), waiting = true))
        val status = post(id, kind, action, text, note, data, mime)
        if (asking && status != "ok" && status != "in progress") _lines.value = _lines.value.filterNot { it.id == id }
        status
    }

    private suspend fun post(id: String, kind: String, action: String, text: String, note: String, data: String?,
                             mime: String?): String =
        withContext(Dispatchers.IO) {
            val pc = store.pc ?: return@withContext "error: not paired"
            val body = Outgoing.share(id, kind, action, text, note, data, mime)
            var last = ""
            for (h in routes(pc)) {             // the same id on every try: the PC runs it at most once
                val req = Request.Builder().url("${pc.base(h)}/v1/share").header("Authorization", "Bearer ${pc.token}")
                    .post(body.toRequestBody(JSON)).build()
                try {
                    client(pc.fp).newCall(req).execute().use { r ->
                        if (r.code == 401) return@withContext "error: this phone isn't paired any more"
                        val o = runCatching { JSONObject(r.body?.string().orEmpty()) }.getOrElse { JSONObject() }
                        return@withContext o.optString("status").ifBlank { o.optString("error", "error: ${r.code}") }
                    }
                } catch (e: IOException) {
                    last = describe(e)
                }
            }
            if (last.startsWith(REFUSED)) "error: Auren isn't serving on the PC" else "error: can't reach the PC"
        }

    /** An inbox item's file, downloaded once into the cache. */
    suspend fun download(item: InboxItem): File? = withContext(Dispatchers.IO) {
        val pc = store.pc ?: return@withContext null
        val f = item.file ?: return@withContext null
        val dir = File(app.cacheDir, "inbox").apply { mkdirs() }
        val out = File(dir, "${item.id}-${f.name.replace(Regex("[^\\w.-]"), "_")}")
        if (out.exists() && out.length() == f.size) return@withContext out
        for (h in routes(pc)) {
            val req = Request.Builder().url("${pc.base(h)}/v1/inbox/${item.id}/file")
                .header("Authorization", "Bearer ${pc.token}").build()
            try {
                client(pc.fp).newCall(req).execute().use { r ->
                    if (!r.isSuccessful) return@withContext null
                    val tmp = File.createTempFile(out.name, ".part", dir)
                    r.body?.byteStream()?.use { input -> tmp.outputStream().use { input.copyTo(it) } } ?: return@withContext null
                    if (!tmp.renameTo(out)) tmp.delete()
                    return@withContext out.takeIf { it.exists() }
                }
            } catch (_: IOException) {
            }
        }
        null
    }

    // --- the network --------------------------------------------------------------------------------

    /** This phone's own Wi-Fi (or Ethernet) IPv4 addresses with their prefix lengths: which LAN it is on. */
    private fun wifi(): List<Pair<String, Int>> = try {
        val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        @Suppress("DEPRECATION")
        cm.allNetworks.flatMap { n ->
            val caps = cm.getNetworkCapabilities(n)
            val local = caps != null && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
            if (!local) emptyList()
            else cm.getLinkProperties(n)?.linkAddresses.orEmpty()
                .filter { it.address is Inet4Address }
                .map { it.address.hostAddress.orEmpty() to it.prefixLength }
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
    private const val STALE_MS = 2 * 60_000L          // an ask older than this isn't resent: it would surprise
    private val QUIET = setOf("not listening", "cancelled", "nothing is waiting for an answer", "in progress")
}
