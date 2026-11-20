package app.auren.phone

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityNodeInfo.AccessibilityAction
import android.view.accessibility.AccessibilityWindowInfo
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.coroutines.resume

/**
 * Phone control: the PC's Auren uses this phone through Android's accessibility service, the way a person
 * does (docs/phone.md#phone-control). The user turns it on in Android's Accessibility settings; while it is
 * on, the app stays connected to the PC so the PC can reach it from the desk too.
 *
 * The PC sends one action at a time ([run]): read the screen, tap or type into a numbered item, scroll, a
 * system key, open an app. Each answer carries the screen after it: the visible text and a numbered list of
 * what can be tapped, typed into or scrolled. Numbers belong to the last read; an action names the label it
 * expects, so a screen that changed meanwhile refuses rather than taps something else.
 */
class HandsService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        _on.value = true
        Remote.keep()
        Remote.controlChanged()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onUnbind(intent: Intent?): Boolean {
        off()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        off()
        super.onDestroy()
    }

    private fun off() {
        if (instance !== this) return
        instance = null
        _on.value = false
        nodes.clear()
        Remote.controlChanged()
        Remote.unkeep()
    }

    // --- the actions (main thread) ------------------------------------------------------------------

    private val nodes = HashMap<Int, AccessibilityNodeInfo>()        // the last read's numbered items

    suspend fun run(o: JSONObject): JSONObject = try {
        when (o.optString("action")) {
            "screen" -> read()
            "tap" -> { tap(item(o), o.optBoolean("long")); settle(); read() }
            "type" -> { type(item(o, field = true), o.optString("text"), o.optBoolean("submit")); settle(); read() }
            "scroll" -> { scroll(o.optString("direction", "down"), if (o.has("n")) item(o) else null); settle(); read() }
            "key" -> { key(o.optString("key")); settle(); read() }
            "open" -> { val pkg = open(o.optString("app")); arrive(pkg); read() }
            "apps" -> JSONObject().put("ok", true).put("screen", apps(o.optString("query")).joinToString("\n"))
            else -> fail("this app doesn't know the action '${o.optString("action")}': update it")
        }
    } catch (e: Refused) {
        fail(e.message ?: "refused")
    }

    private class Refused(why: String) : Exception(why)

    private fun fail(why: String) = JSONObject().put("ok", false).put("error", why)

    /** The numbered node the action names, still showing the label it was read with. */
    private fun item(o: JSONObject, field: Boolean = false): AccessibilityNodeInfo {
        val n = o.optInt("n", -1)
        val node = nodes[n] ?: throw Refused("no item $n on the phone's screen: read it again")
        if (!node.refresh()) throw Refused("item $n is gone from the phone's screen: read it again")
        val expect = o.optString("expect")
        if (o.has("expect") && label(node) != expect) {
            throw Refused("item $n is now “${label(node)}”, not “$expect”: read the phone's screen again")
        }
        if (field && !node.isEditable) throw Refused("item $n isn't a text field")
        return node
    }

    private suspend fun tap(node: AccessibilityNodeInfo, long: Boolean) {
        val action = if (long) AccessibilityNodeInfo.ACTION_LONG_CLICK else AccessibilityNodeInfo.ACTION_CLICK
        var target: AccessibilityNodeInfo? = node
        while (target != null && !(if (long) target.isLongClickable else target.isClickable)) target = target.parent
        if (target?.performAction(action) == true) return
        val r = Rect().also(node::getBoundsInScreen)          // nothing takes the click: touch it where it shows
        if (!gesture(r.exactCenterX(), r.exactCenterY(), r.exactCenterX(), r.exactCenterY(), if (long) 700 else 60)) {
            throw Refused("the tap didn't go through")
        }
    }

    private fun type(node: AccessibilityNodeInfo, text: String, submit: Boolean) {
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) throw Refused("that field didn't take the text")
        if (!submit) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R ||
            !node.performAction(AccessibilityAction.ACTION_IME_ENTER.id)) {
            throw Refused("typed it, but couldn't press the keyboard's action key: tap the send or search button instead")
        }
    }

    private suspend fun scroll(direction: String, node: AccessibilityNodeInfo?) {
        val target = node ?: biggestScrollable()
        if (target != null) {
            val exact = when (direction) {
                "up" -> AccessibilityAction.ACTION_SCROLL_UP
                "down" -> AccessibilityAction.ACTION_SCROLL_DOWN
                "left" -> AccessibilityAction.ACTION_SCROLL_LEFT
                else -> AccessibilityAction.ACTION_SCROLL_RIGHT
            }
            val forward = direction == "down" || direction == "right"
            if (target.performAction(exact.id)) return
            if (target.performAction(if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)) return
        }
        // No list takes a scroll: swipe across the middle of the screen, the other way (content follows the finger).
        val w = resources.displayMetrics.widthPixels.toFloat()
        val h = resources.displayMetrics.heightPixels.toFloat()
        val ok = when (direction) {
            "down" -> gesture(w / 2, h * 0.7f, w / 2, h * 0.3f, 300)
            "up" -> gesture(w / 2, h * 0.3f, w / 2, h * 0.7f, 300)
            "right" -> gesture(w * 0.8f, h / 2, w * 0.2f, h / 2, 300)
            else -> gesture(w * 0.2f, h / 2, w * 0.8f, h / 2, 300)
        }
        if (!ok) throw Refused("nothing there scrolls")
    }

    private fun biggestScrollable(): AccessibilityNodeInfo? {
        var best: AccessibilityNodeInfo? = null
        var area = 0
        val r = Rect()
        fun walk(n: AccessibilityNodeInfo?) {
            n ?: return
            if (n.isScrollable && n.isVisibleToUser) {
                n.getBoundsInScreen(r)
                if (r.width() * r.height() > area) { area = r.width() * r.height(); best = n }
            }
            for (i in 0 until n.childCount) walk(n.getChild(i))
        }
        walk(rootInActiveWindow)
        return best
    }

    private fun key(name: String) {
        val action = when (name) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            "lock" -> GLOBAL_ACTION_LOCK_SCREEN
            else -> throw Refused("no key '$name'")
        }
        if (!performGlobalAction(action)) throw Refused("the phone didn't take the $name key")
    }

    private class App(val label: String, val pkg: String)

    private fun launchable(): List<App> {
        val pm = packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        @Suppress("DEPRECATION")
        val found = if (Build.VERSION.SDK_INT >= 33) pm.queryIntentActivities(main, PackageManager.ResolveInfoFlags.of(0))
            else pm.queryIntentActivities(main, 0)
        return found.map { App(it.loadLabel(pm).toString(), it.activityInfo.packageName) }
            .filter { it.pkg != packageName }.distinctBy { it.pkg }.sortedBy { it.label.lowercase(Locale.ROOT) }
    }

    private fun apps(query: String): List<String> {
        val q = query.lowercase(Locale.ROOT)
        return launchable().map { it.label }.filter { q.isBlank() || q in it.lowercase(Locale.ROOT) }
            .ifEmpty { listOf("(no app matches '$query')") }
    }

    /** Opens the app whose name best matches; its package, to wait for. */
    private fun open(name: String): String {
        val all = launchable()
        val app = matchApp(name, all.map { it.label })?.let { i -> all[i] }
            ?: throw Refused("no app called '$name' on the phone" +
                closeMatches(name, all.map { it.label }).let { if (it.isEmpty()) "" else ": close matches: ${it.joinToString()}" })
        val intent = packageManager.getLaunchIntentForPackage(app.pkg) ?: throw Refused("${app.label} can't be opened")
        startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED))
        return app.pkg
    }

    /** Wait for an opened app to come to the front (up to 5 s), and for its screen to fill in. */
    private suspend fun arrive(pkg: String) {
        withTimeoutOrNull(5_000) {
            while (rootInActiveWindow?.packageName?.toString() != pkg) delay(150)
        }
        settle(900)
    }

    private suspend fun settle(ms: Long = 700) = delay(ms)

    private suspend fun gesture(x1: Float, y1: Float, x2: Float, y2: Float, ms: Long): Boolean {
        val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
        val g = GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, ms)).build()
        return suspendCancellableCoroutine { c ->
            val sent = dispatchGesture(g, object : GestureResultCallback() {
                override fun onCompleted(d: GestureDescription?) { if (c.isActive) c.resume(true) }
                override fun onCancelled(d: GestureDescription?) { if (c.isActive) c.resume(false) }
            }, null)
            if (!sent && c.isActive) c.resume(false)
        }
    }

    // --- reading the screen -------------------------------------------------------------------------

    /** What shows now: the app in front (and the notification shade or a system dialog over it, if open). */
    private fun read(): JSONObject {
        nodes.clear()
        val out = Dump()
        val shown = windows.filter { w ->
            when (w.type) {
                AccessibilityWindowInfo.TYPE_APPLICATION -> true
                AccessibilityWindowInfo.TYPE_SYSTEM -> big(w)      // the shade, a system dialog; not the status bar
                else -> false
            }
        }.sortedByDescending { it.layer }
        val keyboard = windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        val roots = shown.mapNotNull { w -> w.root?.let { w to it } }.filter { it.second.packageName != packageName }
            .ifEmpty { listOfNotNull(rootInActiveWindow?.let { null to it }) }
        for ((w, root) in roots) {
            val pkg = root.packageName?.toString().orEmpty()
            val title = w?.title?.toString()?.takeIf { it.isNotBlank() }
            out.line("== ${appName(pkg)}${title?.takeIf { it != appName(pkg) }?.let { " — $it" } ?: ""} ($pkg)")
            walk(root, out, 0)
        }
        if (roots.isEmpty()) out.line("(the phone shows nothing readable: it may be locked or asleep)")
        if (keyboard) out.line("(the keyboard is open)")
        return JSONObject().put("ok", true).put("screen", out.text()).put("items", out.items)
    }

    private fun big(w: AccessibilityWindowInfo): Boolean {
        val r = Rect().also(w::getBoundsInScreen)
        return r.height() > resources.displayMetrics.heightPixels / 3
    }

    private fun appName(pkg: String): String = try {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (_: PackageManager.NameNotFoundException) { pkg }

    private inner class Dump {
        private val sb = StringBuilder()
        val items = JSONArray()
        var full = false
        fun line(s: String) {
            if (full) return
            if (sb.length + s.length > MAX_TEXT) { sb.append("… (more below: scroll)\n"); full = true; return }
            sb.append(s).append('\n')
        }
        fun text() = sb.toString().trimEnd()
    }

    private fun walk(n: AccessibilityNodeInfo, out: Dump, depth: Int) {
        if (!n.isVisibleToUser || depth > 40 || out.full) return
        val actionable = n.isClickable || n.isLongClickable || n.isEditable || n.isCheckable || n.isScrollable
        val own = label(n)
        if (actionable) {
            val number = nodes.size + 1
            nodes[number] = n
            // A row with no words of its own (a chat in a list) is named by what it holds.
            val name = own.ifBlank { inner(n).take(100) }
            val kind = kind(n)
            val state = buildList {
                if (n.isEditable) n.text?.toString()?.takeIf { it.isNotBlank() && it != own }?.let { add("holds “${it.take(80)}”") }
                if (n.isCheckable) add(if (n.isChecked) "on" else "off")
                if (n.isScrollable && !n.isClickable) add("scrolls")
                if (n.isFocused) add("focused")
                if (!n.isEnabled) add("disabled")
            }
            out.line("[$number] ${if (kind.isEmpty()) "" else "$kind "}“$name”${if (state.isEmpty()) "" else " (${state.joinToString()})"}")
            out.items.put(JSONObject().put("n", number).put("label", name).put("field", n.isEditable)
                .put("search", n.isEditable && searchy(n)))
            if (own.isBlank() && !n.isScrollable) return       // its words are its name now; don't list them twice
        } else if (own.isNotBlank()) {
            out.line(own.take(300))
        }
        for (i in 0 until n.childCount) n.getChild(i)?.let { walk(it, out, depth + 1) }
    }

    private fun label(n: AccessibilityNodeInfo): String {
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) n.hintText?.toString() else null
        val text = if (n.isEditable) null else n.text?.toString()
        return listOfNotNull(text, n.contentDescription?.toString(), if (n.isEditable) hint else null,
            if (n.isEditable && hint == null) n.viewIdResourceName?.substringAfterLast('/') else null)
            .map { it.replace('\n', ' ').trim() }.firstOrNull { it.isNotBlank() }.orEmpty()
    }

    private fun inner(n: AccessibilityNodeInfo): String {
        val parts = ArrayList<String>()
        fun walk(c: AccessibilityNodeInfo?, depth: Int) {
            c ?: return
            if (depth > 8 || parts.size > 6) return
            (c.text ?: c.contentDescription)?.toString()?.replace('\n', ' ')?.trim()?.takeIf { it.isNotBlank() }?.let(parts::add)
            for (i in 0 until c.childCount) walk(c.getChild(i), depth + 1)
        }
        for (i in 0 until n.childCount) walk(n.getChild(i), 0)
        return parts.joinToString(" · ")
    }

    private fun kind(n: AccessibilityNodeInfo): String {
        val cls = n.className?.toString().orEmpty().substringAfterLast('.')
        return when {
            n.isEditable -> "field"
            n.isCheckable && "Switch" in cls -> "switch"
            n.isCheckable -> "checkbox"
            "Button" in cls -> "button"
            n.isScrollable && !n.isClickable -> "list"
            else -> ""
        }
    }

    private fun searchy(n: AccessibilityNodeInfo): Boolean {
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) n.hintText else null
        return listOfNotNull(hint, n.contentDescription, n.viewIdResourceName).any { "search" in it.toString().lowercase(Locale.ROOT) }
    }

    companion object {
        const val MAX_TEXT = 8_000

        @Volatile var instance: HandsService? = null
            private set
        private val _on = MutableStateFlow(false)
        /** The service is on (the user turned it on in Accessibility settings). */
        val on: StateFlow<Boolean> = _on
    }
}

/** The index of the app whose name matches best: exactly, then starting with it, then containing it (ignoring case). */
fun matchApp(name: String, labels: List<String>): Int? {
    val q = name.trim().lowercase(Locale.ROOT)
    if (q.isEmpty()) return null
    val low = labels.map { it.lowercase(Locale.ROOT) }
    return low.indexOf(q).takeIf { it >= 0 }
        ?: low.indexOfFirst { it.startsWith(q) }.takeIf { it >= 0 }
        ?: low.indexOfFirst { q in it }.takeIf { it >= 0 }
        ?: low.indexOfFirst { it.replace(" ", "") == q.replace(" ", "") }.takeIf { it >= 0 }
}

/** Up to five app names sharing a word or its start with [name], for an error that helps. */
fun closeMatches(name: String, labels: List<String>): List<String> {
    val words = name.lowercase(Locale.ROOT).split(' ').filter { it.length >= 2 }
    return labels.filter { l ->
        val lw = l.lowercase(Locale.ROOT).split(' ')
        words.any { w -> lw.any { it.startsWith(w.take(3)) } }
    }.take(5)
}
