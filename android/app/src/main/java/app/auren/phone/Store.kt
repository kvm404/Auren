package app.auren.phone

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import java.io.File

/** The paired PC, the device token and the app's settings, in the app's private storage. */
data class Pc(val host: String, val port: Int, val token: String, val deviceId: String, val name: String) {
    val base get() = "http://$host:$port"
}

class Store(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("auren", Context.MODE_PRIVATE)
    private val inboxFile = File(context.filesDir, "inbox.json")

    var pc: Pc?
        get() {
            val host = prefs.getString("host", null) ?: return null
            val token = prefs.getString("token", null) ?: return null
            return Pc(host, prefs.getInt("port", 8735), token, prefs.getString("device", "").orEmpty(),
                prefs.getString("pc", "PC").orEmpty())
        }
        set(v) {
            prefs.edit().apply {
                if (v == null) {
                    listOf("host", "port", "token", "device", "pc", "inboxLast").forEach(::remove)
                } else {
                    putString("host", v.host); putInt("port", v.port); putString("token", v.token)
                    putString("device", v.deviceId); putString("pc", v.name)
                }
            }.apply()
        }

    var inboxLast: Long
        get() = prefs.getLong("inboxLast", 0)
        set(v) = prefs.edit().putLong("inboxLast", v).apply()

    var readAloud: Boolean
        get() = prefs.getBoolean("readAloud", false)
        set(v) = prefs.edit().putBoolean("readAloud", v).apply()

    var inboxSeen: Long
        get() = prefs.getLong("inboxSeen", 0)
        set(v) = prefs.edit().putLong("inboxSeen", v).apply()

    fun loadInbox(): List<InboxItem> = try {
        val a = JSONArray(inboxFile.readText())
        (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(InboxItem::from) }
    } catch (_: Exception) {
        emptyList()
    }

    fun saveInbox(items: List<InboxItem>) {
        val a = JSONArray()
        items.take(KEEP).forEach { a.put(it.toJson()) }
        val tmp = File(inboxFile.path + ".tmp")
        tmp.writeText(a.toString())
        tmp.renameTo(inboxFile)
    }

    companion object {
        const val KEEP = 200
    }
}
