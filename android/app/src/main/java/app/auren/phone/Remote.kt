package app.auren.phone

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.speech.tts.TextToSpeech
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
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
import java.net.NetworkInterface
import java.util.Locale
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Where the link to the PC stands, and what the user can do about it. */
enum class LinkKind { UNPAIRED, NO_TAILSCALE, CONNECTING, CONNECTED, UNREACHABLE, SERVER_OFF, REVOKED }

data class Link(val kind: LinkKind, val why: String = "")

enum class Who { YOU, AUREN, NOTE }

/** One line of this session's conversation with Auren (phone turns only; the desk has its own). */
data class Line(val id: String, val who: Who, val text: String, val waiting: Boolean = false)

data class Talk(val id: String, val handsFree: Boolean, val heard: Boolean = false)

/**
 * The one connection to the PC, for every screen, the share sheet and the tile. It keeps a WebSocket open
 * while any screen is showing (or the microphone is on), reconnects with backoff, and resends asks and
 * answers the PC hasn't acked: their ids make a resend that already ran harmless.
 */
object Remote {
    private lateinit var app: Application
    lateinit var store: Store
        private set
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

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
    private var users = 0
    private var loop: Job? = null
    private val wake = Channel<Unit>(Channel.CONFLATED)
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
        _inbox.value = store.loadInbox().sortedByDescending { it.id }
        _unread.value = _inbox.value.count { it.id > store.inboxSeen }
        _link.value = Link(if (store.pc == null) LinkKind.UNPAIRED else LinkKind.CONNECTING)
        val cm = application.getSystemService(ConnectivityManager::class.java)
        cm.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { wake.trySend(Unit) }
            override fun onLost(network: Network) { wake.trySend(Unit) }
        })
    }

    // --- lifecycle: connected while something shows ----------------------------------------------

    fun start() {
        users++
        if (loop?.isActive != true) loop = scope.launch { run() }
        else wake.trySend(Unit)
    }

    fun stop() {
        users = maxOf(0, users - 1)
        if (users == 0) scope.launch {
            delay(4_000)                        // a rotation or a quick app switch keeps the link
            if (users == 0 && _talk.value == null) loop?.cancel()?.also { close() }
        }
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
            val tailnet = withContext(Dispatchers.IO) { tailnetUp() }
            val (outcome, greeted) = connectOnce(pc)
            if (outcome.kind == LinkKind.REVOKED) {
                _link.value = outcome
                continue
            }
            _link.value = if (!tailnet) Link(LinkKind.NO_TAILSCALE) else outcome
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
    private suspend fun connectOnce(pc: Pc): Pair<Link, Boolean> {
        val events = Channel<Event>(Channel.UNLIMITED)
        val req = Request.Builder().url("${pc.base}/v1/ws").header("Authorization", "Bearer ${pc.token}").build()
        val socket = http.newWebSocket(req, object : WebSocketListener() {
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
                when (e) {
                    is Event.Text -> {
                        val m = Incoming.parse(e.text) ?: continue
                        if (m is Incoming.Hello) greeted = true
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
            connected = false
            ws = null
            socket.cancel()
            stopMic()
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
        return if ("ECONNREFUSED" in msg || "refused" in msg.lowercase(Locale.ROOT)) "$REFUSED: $msg" else msg
    }

    private fun close() {
        ws?.close(1000, null)
        ws = null
        connected = false
    }

    // --- what the PC says ---------------------------------------------------------------------------

    private fun handle(m: Incoming) {
        when (m) {
            is Incoming.Hello -> {
                connected = true
                _link.value = Link(LinkKind.CONNECTED)
                setState(m.state)
                if (m.inboxLast < store.inboxLast) store.inboxLast = 0       // the PC's inbox was reset
                send(Outgoing.sync(store.inboxLast))
                val now = System.currentTimeMillis()
                pending.filterValues { now - it.second > STALE_MS }.keys.forEach {
                    pending.remove(it)
                    done(it)
                }
                pending.values.forEach { send(it.first) }
                if (talkWhenConnected) {
                    talkWhenConnected = false
                    talkStart(handsFree = true)
                }
            }
            is Incoming.State -> setState(m.state)
            is Incoming.Reply -> {
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

    private fun newId() = UUID.randomUUID().toString().substring(0, 12)

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

    fun answer(yes: Boolean) {
        val id = newId()
        val msg = Outgoing.answer(id, yes)
        pending[id] = msg to System.currentTimeMillis()
        if (!send(msg)) _notices.tryEmit("Sends when the PC is back")
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
        if (item.id > store.inboxLast) store.inboxLast = item.id
        if (item.id > store.inboxSeen) _unread.value = all.count { it.id > store.inboxSeen }
        scope.launch(Dispatchers.IO) { store.saveInbox(all) }
    }

    fun inboxSeen() {
        store.inboxSeen = maxOf(store.inboxSeen, _inbox.value.firstOrNull()?.id ?: 0)
        _unread.value = 0
    }

    fun clearInbox() {
        _inbox.value = emptyList()
        _unread.value = 0
        scope.launch(Dispatchers.IO) {
            store.saveInbox(emptyList())
            File(app.cacheDir, "inbox").deleteRecursively()
        }
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

    // --- pairing, sharing and files (plain HTTP) -------------------------------------------------------

    /** Trade the pairing code for a token. Returns null when paired, or what went wrong. */
    suspend fun pair(p: Pairing): String? = withContext(Dispatchers.IO) {
        val body = JSONObject().put("code", p.code).put("name", deviceName()).toString()
        val req = Request.Builder().url("http://${p.host}:${p.port}/v1/pair")
            .post(body.toRequestBody(JSON)).build()
        try {
            http.newCall(req).execute().use { r ->
                val o = runCatching { JSONObject(r.body?.string().orEmpty()) }.getOrElse { JSONObject() }
                if (!r.isSuccessful) return@withContext o.optString("error").ifBlank { "The PC said ${r.code}" }
                val dev = o.optJSONObject("device") ?: JSONObject()
                store.pc = Pc(p.host, p.port, o.getString("token"), dev.optString("id"), o.optString("pc", p.pc))
                store.inboxLast = 0
                withContext(Dispatchers.Main) {
                    clearInbox()
                    _lines.value = emptyList()
                    _link.value = Link(LinkKind.CONNECTING)
                    wake.trySend(Unit)
                }
                null
            }
        } catch (e: IOException) {
            val why = describe(e)
            when {
                why.startsWith(REFUSED) -> "Auren isn't serving on ${p.pc}. Is the daemon running? Try auren doctor."
                !tailnetUp() -> "Tailscale is off on this phone. Turn it on and try again."
                else -> "Can't reach ${p.pc} at ${p.host}. Is it awake and on Tailscale?"
            }
        }
    }

    /** Forget the PC on this phone. (`auren phone revoke` on the PC is what stops the token working.) */
    fun unpair() {
        store.pc = null
        close()
        stopMic()
        pending.clear()
        _lines.value = emptyList()
        _state.value = AurenState()
        clearInbox()
        _link.value = Link(LinkKind.UNPAIRED)
        wake.trySend(Unit)
    }

    /** POST /v1/share. Returns the PC's status ("ok", "busy", "error: ...") or why it couldn't send. */
    suspend fun share(kind: String, action: String, text: String, note: String, data: String? = null, mime: String? = null): String =
        withContext(Dispatchers.IO) {
            val pc = store.pc ?: return@withContext "error: not paired"
            val body = Outgoing.share(newId(), kind, action, text, note, data, mime)
            val req = Request.Builder().url("${pc.base}/v1/share").header("Authorization", "Bearer ${pc.token}")
                .post(body.toRequestBody(JSON)).build()
            var last = ""
            repeat(2) {                        // one resend, same id: the PC runs it at most once
                try {
                    http.newCall(req).execute().use { r ->
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
        val req = Request.Builder().url("${pc.base}/v1/inbox/${item.id}/file")
            .header("Authorization", "Bearer ${pc.token}").build()
        try {
            http.newCall(req).execute().use { r ->
                if (!r.isSuccessful) return@withContext null
                val tmp = File(dir, out.name + ".part")
                r.body?.byteStream()?.use { input -> tmp.outputStream().use { input.copyTo(it) } } ?: return@withContext null
                tmp.renameTo(out)
                out
            }
        } catch (_: IOException) {
            null
        }
    }

    // --- the network --------------------------------------------------------------------------------

    /** Whether this phone has a Tailscale address now (the Tailscale app's VPN is up). */
    fun tailnetUp(): Boolean {
        try {
            val cm = app.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            @Suppress("DEPRECATION")
            for (n in cm.allNetworks) {
                val props = cm.getLinkProperties(n) ?: continue
                if (props.linkAddresses.any { a -> a.address is Inet4Address && isTailnet(a.address.hostAddress.orEmpty()) }) return true
            }
        } catch (_: Exception) {
        }
        return try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty().any { ni ->
                ni.isUp && ni.inetAddresses.toList().any { it is Inet4Address && isTailnet(it.hostAddress.orEmpty()) }
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun deviceName(): String {
        val m = Build.MODEL.orEmpty()
        return if (m.startsWith(Build.MANUFACTURER.orEmpty(), ignoreCase = true)) m
        else "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} $m"
    }

    private val JSON = "application/json".toMediaType()
    private const val REFUSED = "refused"
    private const val STALE_MS = 2 * 60_000L          // an ask older than this isn't resent: it would surprise
    private val QUIET = setOf("not listening", "cancelled", "nothing is waiting for an answer")
}
