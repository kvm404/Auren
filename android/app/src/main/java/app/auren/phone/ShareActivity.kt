package app.auren.phone

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.auren.phone.ui.Au
import app.auren.phone.ui.AurenTheme
import app.auren.phone.ui.Caps
import app.auren.phone.ui.GoldButton
import app.auren.phone.ui.QuietButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Share text, a link or an image from any app: ask Auren about it, put it on the PC's clipboard, or open it there. */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val shared = read(intent)
        setContent { AurenTheme { Sheet(shared, onDone = { finish() }, openApp = ::openApp) } }
    }

    override fun onStart() {
        super.onStart()
        Remote.start()              // so an "ask" reply has a socket to land on
    }

    override fun onStop() {
        super.onStop()
        Remote.stop()
    }

    private fun openApp() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP))
    }

    private fun read(i: Intent?): Shared? {
        if (i?.action != Intent.ACTION_SEND) return null
        val type = i.type.orEmpty()
        if (type.startsWith("image/")) {
            val uri = if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
            else @Suppress("DEPRECATION") i.getParcelableExtra(Intent.EXTRA_STREAM)
            return uri?.let { Shared("image", "", it, type) }
        }
        val text = i.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
        if (text.isEmpty()) return null
        val link = Regex("^https?://\\S+$").matches(text)
        return Shared(if (link) "link" else "text", text, null, null)
    }
}

data class Shared(val kind: String, val text: String, val image: Uri?, val mime: String?)

private const val MAX_IMAGE = 14 * 1024 * 1024        // base64 of it stays under the PC's 20 MB

@Composable
private fun Sheet(shared: Shared?, onDone: () -> Unit, openApp: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var note by remember { mutableStateOf("") }
    var working by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val paired = Remote.store.pc != null
    val ids = remember { mutableMapOf<String, String>() }     // one id per action: trying again can't run it twice

    fun go(action: String) {
        if (shared == null || working != null) return
        working = action
        error = null
        scope.launch {
            val data = shared.image?.let { uri ->
                withContext(Dispatchers.IO) {
                    runCatching {
                        ctx.contentResolver.openInputStream(uri)?.use { s ->
                            val out = java.io.ByteArrayOutputStream()
                            val buf = ByteArray(64 * 1024)
                            while (out.size() <= MAX_IMAGE) {
                                val n = s.read(buf)
                                if (n < 0) break
                                out.write(buf, 0, n)
                            }
                            if (out.size() > MAX_IMAGE) null else Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
                        }
                    }.getOrNull()
                }
            }
            if (shared.image != null && data == null) {
                working = null
                error = "Couldn't read that image, or it's over 14 MB"
                return@launch
            }
            val status = Remote.share(ids.getOrPut(action, Remote::newId), shared.kind, action, shared.text, note, data, shared.mime)
            working = null
            when {
                status == "ok" || status == "in progress" -> {
                    Toast.makeText(ctx, when (action) {
                        "ask" -> "Asked Auren"
                        "clipboard" -> "On the PC's clipboard"
                        else -> "Opened on the PC"
                    }, Toast.LENGTH_SHORT).show()
                    if (action == "ask") openApp()
                    onDone()
                }
                status == "busy" -> error = "Auren is busy. Try again when the current turn ends."
                else -> error = status.removePrefix("error: ").replaceFirstChar { it.uppercase() }
            }
        }
    }

    Box(
        Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.55f))
            .clickable(remember { MutableInteractionSource() }, null) { onDone() },
        contentAlignment = Alignment.BottomCenter,
    ) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp)).background(Au.Night)
                .border(1.dp, Au.Edge, RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp))
                .clickable(remember { MutableInteractionSource() }, null) {}
                .navigationBarsPadding().imePadding().padding(20.dp),
        ) {
            Caps("Send to Auren", Au.Gold, size = 12)
            when {
                !paired -> {
                    Text("This phone isn't paired with a PC yet.", color = Au.Text, fontSize = 15.sp, modifier = Modifier.padding(top = 10.dp))
                    GoldButton("Open Auren", Modifier.padding(top = 14.dp)) { openApp(); onDone() }
                    return@Column
                }
                shared == null -> {
                    Text("Auren can take text, a link or an image.", color = Au.Text, fontSize = 15.sp, modifier = Modifier.padding(top = 10.dp))
                    QuietButton("Close", Modifier.padding(top = 14.dp), onDone)
                    return@Column
                }
            }
            val s = shared!!
            Text(
                if (s.kind == "image") "An image" else s.text,
                color = if (s.kind == "link") Au.Holo else Au.Text, fontSize = 14.sp, maxLines = 4, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 10.dp).fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Au.Glass).padding(12.dp),
            )
            OutlinedTextField(
                value = note, onValueChange = { note = it },
                placeholder = { Text("Add a note for Auren (optional)", color = Au.Faint) },
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp), shape = RoundedCornerShape(14.dp), maxLines = 3,
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Au.Gold.copy(alpha = 0.7f), unfocusedBorderColor = Au.Edge,
                    focusedContainerColor = Au.Glass, unfocusedContainerColor = Au.Glass, cursorColor = Au.Gold,
                ),
            )
            error?.let { Text(it, color = Au.Red, fontSize = 13.sp, modifier = Modifier.padding(top = 10.dp)) }
            Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                GoldButton("Ask Auren", Modifier.weight(1f), enabled = working == null) { go("ask") }
                QuietButton("PC clipboard", Modifier.weight(1f)) { go("clipboard") }
                QuietButton("Open on PC", Modifier.weight(1f)) { go("open") }
            }
            if (working != null) {
                Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(color = Au.Gold, strokeWidth = 2.dp, modifier = Modifier.padding(end = 10.dp))
                    Text("Sending…", color = Au.Dim, fontSize = 13.sp)
                }
            }
        }
    }
}
