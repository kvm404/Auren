package app.auren.phone

import org.json.JSONObject
import java.net.URLDecoder

/**
 * Auren's phone protocol, API v1 (docs/phone.md in the Auren repo): what the PC sends, what the phone
 * sends, and the pairing link. Plain Kotlin and org.json only, so it is unit tested off the device.
 */
const val API = 1

private fun octets(host: String): List<Int>? {
    val parts = host.split(".")
    if (parts.size != 4) return null
    val n = parts.map { p -> p.toIntOrNull()?.takeIf { it in 0..255 && p == it.toString() } ?: return null }
    return n
}

/** Tailscale hands out 100.64.0.0/10. */
fun isTailnet(host: String): Boolean {
    val n = octets(host) ?: return false
    return n[0] == 100 && n[1] in 64..127
}

/** A private LAN address (RFC 1918): what the PC listens on for the phone's Wi-Fi. */
fun isPrivateLan(host: String): Boolean {
    val n = octets(host) ?: return false
    return n[0] == 10 || (n[0] == 172 && n[1] in 16..31) || (n[0] == 192 && n[1] == 168)
}

/** The app only ever talks to an address like these, and only over TLS pinned to the PC's certificate. */
fun isPcAddress(host: String) = isTailnet(host) || isPrivateLan(host)

/** How the phone reaches an address, as the app shows it. */
fun pathOf(host: String) = if (isTailnet(host)) "Tailscale" else "Wi-Fi"

/** Whether [host] is on the network [net]/[prefix] (the phone's own Wi-Fi address and its prefix length). */
fun onSubnet(host: String, net: String, prefix: Int): Boolean {
    val a = octets(host) ?: return false
    val b = octets(net) ?: return false
    if (prefix !in 1..32) return false
    val x = a.fold(0L) { acc, o -> acc * 256 + o }
    val y = b.fold(0L) { acc, o -> acc * 256 + o }
    val mask = (0xFFFFFFFFL shl (32 - prefix)) and 0xFFFFFFFFL
    return (x and mask) == (y and mask)
}

/**
 * The order to try the PC's addresses in: a LAN address on the phone's current Wi-Fi first (the short, direct
 * path), then Tailscale, then any other LAN address. Within each, the one that worked last goes first.
 * [wifi] is the phone's own Wi-Fi addresses with their prefix lengths.
 */
fun tryOrder(hosts: List<String>, wifi: List<Pair<String, Int>>, last: String?): List<String> {
    val local = hosts.filter { h -> isPrivateLan(h) && wifi.any { (ip, n) -> onSubnet(h, ip, n) } }
    val tail = hosts.filter(::isTailnet)
    val other = hosts.filter { isPrivateLan(it) && it !in local }
    fun lastFirst(l: List<String>) = l.sortedByDescending { it == last }
    return (lastFirst(local) + lastFirst(tail) + lastFirst(other)).distinct()
}

/**
 * The PC's addresses after a hello on [to] that listed [said]: what the PC listens on now, plus the address that
 * worked. A hello that lists no Tailscale address (the PC's Tailscale is down for a moment) keeps the saved one,
 * so a LAN hello can't erase the only way back from away.
 */
fun mergeHosts(saved: List<String>, said: List<String>, to: String): List<String> {
    if (said.isEmpty()) return saved
    val tail = if (said.any(::isTailnet)) emptyList() else saved.filter(::isTailnet)
    return (said + to + tail).filter(::isPcAddress).distinct()
}

private val FINGERPRINT = Regex("[0-9a-f]{64}")

/**
 * What `auren phone pair` shows:
 * auren://pair?v=1&hosts=100.x.y.z,192.168.0.5&port=8735&code=...&fp=<sha256 of the PC's certificate>&pc=name
 */
data class Pairing(val hosts: List<String>, val port: Int, val code: String, val fp: String, val pc: String) {
    companion object {
        fun parse(text: String): Pairing? {
            val s = text.trim()
            if (!s.startsWith("auren://pair?")) return null
            val q = s.substringAfter('?').split('&').mapNotNull {
                val k = it.substringBefore('=', "")
                if (k.isEmpty()) null else k to URLDecoder.decode(it.substringAfter('='), "UTF-8")
            }.toMap()
            if ((q["v"]?.toIntOrNull() ?: return null) != API) return null
            val hosts = hosts(q["hosts"].orEmpty().split(','))
            val port = q["port"]?.toIntOrNull() ?: return null
            val code = q["code"]?.takeIf { it.isNotBlank() } ?: return null
            val fp = q["fp"]?.lowercase()?.replace(":", "")?.takeIf { FINGERPRINT.matches(it) } ?: return null
            if (hosts.isEmpty() || port !in 1..65535) return null
            return Pairing(hosts, port, code, fp, q["pc"].orEmpty().ifBlank { "PC" })
        }

        /** The addresses the app will use: Tailscale or private LAN ones, nothing else. */
        fun hosts(raw: List<String>) = raw.map(String::trim).filter(::isPcAddress).distinct()
    }
}

/** The PC's state, as the bar plugin and the mascot see it (state.json), plus who owns the turn. */
data class AurenState(
    val state: String = "off",
    val detail: String = "",
    val since: Double = 0.0,
    val brain: String = "",
    val safety: String = "",
    val voice: Boolean = false,
    val origin: String? = null,          // "desk", "phone" or null: nobody has the turn
    val awaiting: Boolean = false,
    val question: String = "",
    val until: Double = 0.0,
    val ask: String = "",                // which question is open: a Yes/No names it, so it can't approve another
    val transcript: String = "",
    val reply: String = "",
    val tool: String = "",
    val remind: Boolean = false,
    val voiceName: String = "",          // the voice Auren speaks in, on the PC and on this phone (Voices.ALL)
) {
    val active get() = state in ACTIVE
    val busyAtDesk get() = origin == "desk"

    companion object {
        val ACTIVE = setOf("listening", "thinking", "working", "speaking")

        fun from(o: JSONObject) = AurenState(
            state = o.optString("state", "off"),
            detail = o.optString("detail"),
            since = o.optDouble("since", 0.0),
            brain = o.optString("brain"),
            safety = o.optString("safety"),
            voice = o.optBoolean("voice"),
            origin = o.optNullString("origin"),
            awaiting = o.optBoolean("awaiting"),
            question = o.optString("question"),
            until = o.optDouble("until", 0.0),
            ask = o.optString("ask"),
            transcript = o.optString("transcript"),
            reply = o.optString("reply"),
            tool = o.optString("tool"),
            remind = o.optBoolean("remind"),
            voiceName = o.optString("voiceName"),
        )
    }
}

data class InboxFile(val name: String, val size: Long, val mime: String)

data class InboxItem(
    val id: Long,
    val kind: String,                    // text, link, image, file
    val title: String,
    val at: Double,
    val text: String = "",
    val url: String = "",
    val file: InboxFile? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id); put("kind", kind); put("title", title); put("at", at)
        if (text.isNotEmpty()) put("text", text)
        if (url.isNotEmpty()) put("url", url)
        file?.let { put("file", JSONObject().put("name", it.name).put("size", it.size).put("mime", it.mime)) }
    }

    companion object {
        fun from(o: JSONObject): InboxItem? {
            val id = o.optLong("id", 0)
            val kind = o.optString("kind")
            if (id <= 0 || kind !in setOf("text", "link", "image", "file")) return null
            val f = o.optJSONObject("file")?.let {
                InboxFile(it.optString("name", "file"), it.optLong("size"), it.optString("mime", "application/octet-stream"))
            }
            if ((kind == "image" || kind == "file") && f == null) return null
            return InboxItem(id, kind, o.optString("title"), o.optDouble("at", 0.0), o.optString("text"), o.optString("url"), f)
        }
    }
}

data class Device(val id: String, val name: String)

/** A message from the PC. */
sealed interface Incoming {
    data class Hello(
        val api: Int, val pc: String, val device: Device?, val inboxLast: Long, val state: AurenState,
        val addrs: List<String> = emptyList(),
    ) : Incoming
    data class State(val state: AurenState) : Incoming
    data class Reply(val id: String, val text: String, val at: Double) : Incoming
    data class Inbox(val item: InboxItem) : Incoming
    data class Ack(val id: String, val status: String, val duplicate: Boolean) : Incoming {
        val ok get() = status == "ok" || status.startsWith("approved") || status.startsWith("dropped") ||
            status.startsWith("heard") || status == "cancelled"
        val busy get() = status == "busy"
    }
    data class Busy(val id: String, val owner: String) : Incoming
    /** The PC has no reply for this request and never will (it restarted, or the reply expired). */
    data class Lost(val id: String) : Incoming
    /** The request's turn ended without a reply (nothing was heard, or it was stopped). */
    data class Ended(val id: String) : Incoming
    data object Revoked : Incoming
    data class Error(val id: String, val error: String) : Incoming
    data class Pong(val id: String) : Incoming

    companion object {
        /** null for anything this version doesn't know (a newer PC may send more). */
        fun parse(text: String): Incoming? {
            val o = try { JSONObject(text) } catch (_: Exception) { return null }
            return when (o.optString("type")) {
                "hello" -> Hello(
                    o.optInt("api"), o.optString("pc"),
                    o.optJSONObject("device")?.let { Device(it.optString("id"), it.optString("name")) },
                    o.optLong("inboxLast"), AurenState.from(o.optJSONObject("state") ?: JSONObject()),
                    o.optJSONArray("addrs")?.let { a ->
                        Pairing.hosts((0 until a.length()).mapNotNull { a.optJSONObject(it)?.optString("ip") })
                    }.orEmpty(),
                )
                "state" -> State(AurenState.from(o))
                "reply" -> Reply(o.optString("id"), o.optString("text"), o.optDouble("at", 0.0))
                "inbox" -> o.optJSONObject("item")?.let(InboxItem::from)?.let(::Inbox)
                "ack" -> Ack(o.optString("id"), o.optString("status"), o.optBoolean("duplicate"))
                "busy" -> Busy(o.optString("id"), o.optString("owner", "desk"))
                "lost" -> Lost(o.optString("id"))
                "ended" -> Ended(o.optString("id"))
                "revoked" -> Revoked
                "error" -> Error(o.optString("id"), o.optString("error"))
                "pong" -> Pong(o.optString("id"))
                else -> null
            }
        }
    }
}

/**
 * Messages to the PC. Requests with an id are acked; resending one the PC already took is harmless. An answer
 * is never resent: it is only for the question it names, and only while connected.
 */
object Outgoing {
    fun ask(id: String, text: String) = obj("ask").put("id", id).put("text", text).toString()
    fun talkStart(id: String, handsFree: Boolean, fresh: Boolean = false) =
        obj("talk.start").put("id", id).put("handsFree", handsFree).put("fresh", fresh).toString()
    fun talkStop() = obj("talk.stop").toString()
    fun cancel() = obj("cancel").toString()
    fun answer(id: String, yes: Boolean, ask: String) = obj("answer").put("id", id).put("yes", yes).put("ask", ask).toString()
    /** After a reconnect: the requests still waiting for a reply. Each gets it, or "lost". */
    fun resume(ids: List<String>) = obj("resume").put("ids", org.json.JSONArray(ids)).toString()
    fun sync(after: Long) = obj("sync").put("after", after).toString()
    fun ping(id: String) = obj("ping").put("id", id).toString()
    /** Speak in [name] from now on, on the PC and on this phone. Acked; the next state carries it. */
    fun voiceSet(id: String, name: String) = obj("voice.set").put("id", id).put("name", name).toString()

    /** POST /v1/share. `data` is base64, for an image. */
    fun share(id: String, kind: String, action: String, text: String, note: String, data: String? = null, mime: String? = null) =
        JSONObject().put("id", id).put("kind", kind).put("action", action).put("text", text).put("note", note).apply {
            if (data != null) { put("data", data); put("mime", mime ?: "image/png") }
        }.toString()

    private fun obj(type: String) = JSONObject().put("type", type)
}

private fun JSONObject.optNullString(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key).ifEmpty { null }
