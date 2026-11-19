package app.auren.phone

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * The paired PC: every address it may be reached on, the pin of its certificate, and the address that
 * worked last. The app speaks only HTTPS/WSS to it, trusting that certificate alone.
 */
data class Pc(
    val hosts: List<String>, val port: Int, val token: String, val deviceId: String, val name: String,
    val fp: String, val last: String? = null,
) {
    /** This pairing: the PC's certificate and the device id it gave this phone. */
    val owner get() = "$fp/$deviceId"
    fun base(host: String) = "https://$host:$port"
    fun ws(host: String) = "wss://$host:$port"
}

class Store(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("auren", Context.MODE_PRIVATE)
    val inbox = InboxDisk(File(context.filesDir, "inbox.json"))

    var pc: Pc?
        get() {
            val hosts = Pairing.hosts(prefs.getString("hosts", null)?.split(',').orEmpty())
            val token = prefs.getString("token", null) ?: return null
            val fp = prefs.getString("fp", null) ?: return null
            if (hosts.isEmpty()) return null
            return Pc(hosts, prefs.getInt("port", 8735), token, prefs.getString("device", "").orEmpty(),
                prefs.getString("pc", "PC").orEmpty(), fp, prefs.getString("last", null))
        }
        set(v) {
            prefs.edit().apply {
                if (v == null) {
                    listOf("hosts", "port", "token", "device", "pc", "fp", "last").forEach(::remove)
                } else {
                    putString("hosts", v.hosts.joinToString(",")); putInt("port", v.port); putString("token", v.token)
                    putString("device", v.deviceId); putString("pc", v.name); putString("fp", v.fp)
                    if (v.last == null) remove("last") else putString("last", v.last)
                }
            }.apply()
        }

    var readAloud: Boolean
        get() = prefs.getBoolean("readAloud", false)
        set(v) = prefs.edit().putBoolean("readAloud", v).apply()

    var inboxSeen: Long
        get() = prefs.getLong("inboxSeen", 0)
        set(v) = prefs.edit().putLong("inboxSeen", v).apply()

    companion object {
        const val KEEP = 200
    }
}

/**
 * The inbox on disk: its items, the cursor (the newest id the PC has sent) and the pairing they came from
 * ([Pc.owner]) in one file, so they can't disagree after a crash, and a new pairing never opens with the old
 * PC's inbox even if the app died before clearing it. Each save writes a temp file of its own and renames it
 * over the old one; saves are serialized, so the last one to start is the one that stays.
 */
class InboxDisk(private val file: File) {
    data class Saved(val last: Long, val items: List<InboxItem>)

    /** What was saved for [owner]; empty when the file belongs to another pairing, or there is none. */
    @Synchronized
    fun load(owner: String): Saved = try {
        val o = JSONObject(file.readText())
        if (o.optString("owner") != owner) throw IllegalStateException("another pairing's inbox")
        val a = o.optJSONArray("items") ?: JSONArray()
        Saved(o.optLong("last", 0), (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(InboxItem::from) })
    } catch (_: Exception) {
        Saved(0, emptyList())
    }

    @Synchronized
    fun save(owner: String, last: Long, items: List<InboxItem>) {
        val a = JSONArray()
        items.take(Store.KEEP).forEach { a.put(it.toJson()) }
        val text = JSONObject().put("owner", owner).put("last", last).put("items", a).toString()
        file.parentFile?.mkdirs()
        val tmp = File.createTempFile(file.name, ".tmp", file.parentFile)
        try {
            tmp.writeText(text)
            Files.move(tmp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            tmp.delete()
        }
    }
}
