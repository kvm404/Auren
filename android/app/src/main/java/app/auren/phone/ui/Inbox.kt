package app.auren.phone.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.text.format.DateUtils
import android.text.format.Formatter
import android.widget.Toast
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.auren.phone.InboxItem
import app.auren.phone.Remote
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** What Auren sent to the phone ("send this to my phone"): text, links, images and files. Newest first. */
@Composable
fun InboxScreen(onBack: () -> Unit) {
    val items by Remote.inbox.collectAsStateWithLifecycle()
    LaunchedEffect(items.firstOrNull()?.id) { Remote.inboxSeen() }
    var confirmClear by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Caps("Inbox", Au.Gold, size = 13)
            Box(Modifier.weight(1f))
            if (items.isNotEmpty()) {
                TextButton(onClick = { if (confirmClear) { Remote.clearInbox(); confirmClear = false } else confirmClear = true }) {
                    Caps(if (confirmClear) "Clear all?" else "Clear", if (confirmClear) Au.Red else Au.Dim, size = 10)
                }
            }
            QuietButton("Back", onClick = onBack)
        }
        if (items.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("Nothing yet.\nAsk Auren to send something to your phone.", color = Au.Faint, fontSize = 14.sp,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
            return
        }
        Text("Clearing removes these from this phone. The PC keeps its own copy.", color = Au.Faint, fontSize = 12.sp)
        LazyColumn(Modifier.fillMaxSize().padding(top = 10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(items, key = { it.id }) { InboxCard(it) }
            item { Box(Modifier.padding(bottom = 24.dp)) }
        }
    }
}

@Composable
private fun InboxCard(item: InboxItem) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }

    fun withFile(then: (File) -> Unit) {
        if (busy) return
        busy = true
        scope.launch {
            val f = Remote.download(item)
            busy = false
            if (f == null) Toast.makeText(ctx, "Couldn't fetch it from the PC", Toast.LENGTH_SHORT).show() else then(f)
        }
    }

    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Au.Glass)
            .border(1.dp, Au.Edge, RoundedCornerShape(14.dp)).padding(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Caps(item.kind, Au.Gold, size = 9)
            Box(Modifier.weight(1f))
            Text(DateUtils.getRelativeTimeSpanString((item.at * 1000).toLong()).toString(), color = Au.Faint, fontSize = 11.sp)
        }
        if (item.title.isNotBlank()) {
            Text(item.title, color = Au.Text, fontSize = 15.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 6.dp))
        }
        when (item.kind) {
            "text" -> Text(item.text, color = Au.Dim, fontSize = 14.sp, lineHeight = 20.sp, maxLines = 12,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
            "link" -> Text(item.url, color = Au.Holo, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 4.dp).clickable { open(ctx, Uri.parse(item.url)) })
            "image" -> Thumbnail(item, Modifier.padding(top = 8.dp).clickable { withFile { openFile(ctx, it, item) } })
            "file" -> item.file?.let {
                Text("${it.name} · ${Formatter.formatShortFileSize(ctx, it.size)}", color = Au.Dim, fontSize = 13.sp,
                    modifier = Modifier.padding(top = 4.dp))
            }
        }
        Row(Modifier.padding(top = 6.dp)) {
            when (item.kind) {
                "text" -> Action("Copy") { copy(ctx, item.text) }
                "link" -> { Action("Open") { open(ctx, Uri.parse(item.url)) }; Action("Copy") { copy(ctx, item.url) } }
                else -> {
                    Action(if (busy) "Fetching…" else "Open") { withFile { openFile(ctx, it, item) } }
                    Action("Share") { withFile { shareFile(ctx, it, item) } }
                }
            }
        }
    }
}

@Composable
private fun Action(label: String, onClick: () -> Unit) {
    TextButton(onClick = onClick) { Caps(label, Au.Text, size = 10) }
}

@Composable
private fun Thumbnail(item: InboxItem, modifier: Modifier) {
    val bmp by produceState<ImageBitmap?>(null, item.id) {
        val f = Remote.download(item) ?: return@produceState
        value = withContext(Dispatchers.IO) {
            runCatching {
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeFile(f.path, bounds)
                var sample = 1
                while (bounds.outWidth / sample > 1200) sample *= 2
                BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
            }.getOrNull()
        }
    }
    val b = bmp
    if (b == null) {
        Box(modifier.fillMaxWidth().heightIn(min = 120.dp).clip(RoundedCornerShape(10.dp)).background(Au.Raised))
    } else {
        Image(b, item.title, modifier.fillMaxWidth().heightIn(max = 320.dp).clip(RoundedCornerShape(10.dp)),
            contentScale = ContentScale.Fit)
    }
}

private fun copy(ctx: Context, text: String) {
    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Auren", text))
    Toast.makeText(ctx, "Copied", Toast.LENGTH_SHORT).show()
}

private fun open(ctx: Context, uri: Uri) {
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        .onFailure { Toast.makeText(ctx, "Nothing on this phone opens that", Toast.LENGTH_SHORT).show() }
}

private fun uriOf(ctx: Context, f: File): Uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)

private fun openFile(ctx: Context, f: File, item: InboxItem) {
    val i = Intent(Intent.ACTION_VIEW).setDataAndType(uriOf(ctx, f), item.file?.mime ?: "*/*")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    runCatching { ctx.startActivity(Intent.createChooser(i, item.title.ifBlank { "Open" })) }
        .onFailure { Toast.makeText(ctx, "Nothing on this phone opens that", Toast.LENGTH_SHORT).show() }
}

private fun shareFile(ctx: Context, f: File, item: InboxItem) {
    val i = Intent(Intent.ACTION_SEND).setType(item.file?.mime ?: "*/*").putExtra(Intent.EXTRA_STREAM, uriOf(ctx, f))
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    ctx.startActivity(Intent.createChooser(i, "Share"))
}
