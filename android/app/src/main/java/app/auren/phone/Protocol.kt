package app.auren.phone

import org.json.JSONObject
import java.net.URLDecoder

/**
 * Auren's phone protocol, API v1 (docs/phone.md in the Auren repo): what the PC sends, what the phone
 * sends, and the pairing link. Plain Kotlin and org.json only, so it is unit tested off the device.
 */
const val API = 1

/** Tailscale hands out 100.64.0.0/10. The app only ever talks to such an address. */
fun isTailnet(host: String): Boolean {
    val parts = host.split(".")
    if (parts.size != 4) return false
    val n = parts.map { it.toIntOrNull() ?: return false }
    if (n.any { it !in 0..255 }) return false
    return n[0] == 100 && n[1] in 64..127
}

/** What `auren phone pair` shows: auren://pair?v=1&host=100.x.y.z&port=8735&code=...&pc=name */
data class Pairing(val host: String, val port: Int, val code: String, val pc: String) {
    companion object {
        fun parse(text: String): Pairing? {
            val s = text.trim()
            if (!s.startsWith("auren://pair?")) return null
            val q = s.substringAfter('?').split('&').mapNotNull {
                val k = it.substringBefore('=', "")
                if (k.isEmpty()) null else k to URLDecoder.decode(it.substringAfter('='), "UTF-8")
            }.toMap()
            if ((q["v"]?.toIntOrNull() ?: return null) != API) return null
            val host = q["host"] ?: return null
            val port = q["port"]?.toIntOrNull() ?: return null
            val code = q["code"]?.takeIf { it.isNotBlank() } ?: return null
            if (!isTailnet(host) || port !in 1..65535) return null
            return Pairing(host, port, code, q["pc"].orEmpty().ifBlank { "PC" })
        }
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
    val transcript: String = "",
    val reply: String = "",
    val tool: String = "",
    val remind: Boolean = false,
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
            transcript = o.optString("transcript"),
            reply = o.optString("reply"),
            tool = o.optString("tool"),
            remind = o.optBoolean("remind"),
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
    data class Hello(val api: Int, val pc: String, val device: Device?, val inboxLast: Long, val state: AurenState) : Incoming
    data class State(val state: AurenState) : Incoming
    data class Reply(val id: String, val text: String, val at: Double) : Incoming
    data class Inbox(val item: InboxItem) : Incoming
    data class Ack(val id: String, val status: String, val duplicate: Boolean) : Incoming {
        val ok get() = status == "ok" || status.startsWith("approved") || status.startsWith("dropped") ||
            status.startsWith("heard") || status == "cancelled"
        val busy get() = status == "busy"
    }
    data class Busy(val id: String, val owner: String) : Incoming
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
                )
                "state" -> State(AurenState.from(o))
                "reply" -> Reply(o.optString("id"), o.optString("text"), o.optDouble("at", 0.0))
                "inbox" -> o.optJSONObject("item")?.let(InboxItem::from)?.let(::Inbox)
                "ack" -> Ack(o.optString("id"), o.optString("status"), o.optBoolean("duplicate"))
                "busy" -> Busy(o.optString("id"), o.optString("owner", "desk"))
                "revoked" -> Revoked
                "error" -> Error(o.optString("id"), o.optString("error"))
                "pong" -> Pong(o.optString("id"))
                else -> null
            }
        }
    }
}

/** Messages to the PC. Requests with an id are acked; resending one the PC already took is harmless. */
object Outgoing {
    fun ask(id: String, text: String) = obj("ask").put("id", id).put("text", text).toString()
    fun talkStart(id: String, handsFree: Boolean, fresh: Boolean = false) =
        obj("talk.start").put("id", id).put("handsFree", handsFree).put("fresh", fresh).toString()
    fun talkStop() = obj("talk.stop").toString()
    fun cancel() = obj("cancel").toString()
    fun answer(id: String, yes: Boolean) = obj("answer").put("id", id).put("yes", yes).toString()
    fun sync(after: Long) = obj("sync").put("after", after).toString()
    fun ping(id: String) = obj("ping").put("id", id).toString()

    /** POST /v1/share. `data` is base64, for an image. */
    fun share(id: String, kind: String, action: String, text: String, note: String, data: String? = null, mime: String? = null) =
        JSONObject().put("id", id).put("kind", kind).put("action", action).put("text", text).put("note", note).apply {
            if (data != null) { put("data", data); put("mime", mime ?: "image/png") }
        }.toString()

    private fun obj(type: String) = JSONObject().put("type", type)
}

private fun JSONObject.optNullString(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key).ifEmpty { null }
