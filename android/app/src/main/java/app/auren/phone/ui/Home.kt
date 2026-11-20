package app.auren.phone.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.auren.phone.AurenState
import app.auren.phone.Line
import app.auren.phone.Link
import app.auren.phone.LinkKind
import app.auren.phone.Remote
import app.auren.phone.Who

@Composable
fun HomeScreen(onInbox: () -> Unit, onSettings: () -> Unit, onPairAgain: () -> Unit, askMic: (() -> Unit) -> Unit) {
    val link by Remote.link.collectAsStateWithLifecycle()
    val st by Remote.state.collectAsStateWithLifecycle()
    val lines by Remote.lines.collectAsStateWithLifecycle()
    val talk by Remote.talk.collectAsStateWithLifecycle()
    val level by Remote.level.collectAsStateWithLifecycle()
    val unread by Remote.unread.collectAsStateWithLifecycle()
    val connected = link.kind == LinkKind.CONNECTED
    val mood = moodOf(st.state, st.awaiting, connected)

    Column(Modifier.fillMaxSize().imePadding().padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Caps("Auren", Au.Gold, size = 13)
            Spacer(Modifier.width(12.dp))
            Box(Modifier.weight(1f)) { LinkPill(link) }      // shrinks on a narrow phone, so the three buttons fit
            // The bar panel's Fresh: one tap away, since a new topic is the usual reason. The PC forgets the
            // conversation; the lines here stay, with a note where the fresh one starts.
            IconButton(onClick = { Remote.forget() }, enabled = connected) {
                Icon(Icons.Filled.Refresh, "Fresh conversation", tint = if (connected) Au.Dim else Au.Faint)
            }
            IconButton(onClick = onInbox) {
                BadgedBox(badge = { if (unread > 0) Badge(containerColor = Au.Gold, contentColor = Au.Ink) { Text("$unread") } }) {
                    Icon(Icons.Filled.Email, "Inbox", tint = Au.Dim)
                }
            }
            IconButton(onClick = onSettings) { Icon(Icons.Filled.Settings, "Settings", tint = Au.Dim) }
        }

        AnimatedVisibility(!connected && link.kind != LinkKind.CONNECTING, enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut()) {
            LinkCard(link, onPairAgain)
        }

        Box(Modifier.fillMaxWidth().height(if (lines.isEmpty()) 220.dp else 150.dp), contentAlignment = Alignment.Center) {
            Visor(mood, level, Modifier.fillMaxSize().semantics { contentDescription = "Auren is ${st.state}" })
        }
        StateLine(st, connected, talk != null)

        AnimatedVisibility(st.awaiting && connected, enter = expandVertically() + fadeIn(), exit = shrinkVertically() + fadeOut()) {
            ApprovalCard(st.question, st.ask)
        }

        Conversation(lines, Modifier.weight(1f))

        Dock(connected, st, talk, level, askMic)
    }
}

@Composable
private fun LinkPill(link: Link) {
    val pc = Remote.store.pc?.name ?: "PC"
    val (dot, label) = when (link.kind) {
        LinkKind.CONNECTED -> Au.Gold to "$pc · ${link.via.lowercase()}"
        LinkKind.CONNECTING -> Au.Holo to "connecting"
        LinkKind.UNREACHABLE -> Au.Red to "$pc unreachable"
        LinkKind.SERVER_OFF -> Au.Red to "auren off"
        LinkKind.REVOKED -> Au.Red to "unpaired"
        LinkKind.UNPAIRED -> Au.Faint to "not paired"
    }
    Row(
        Modifier.clip(RoundedCornerShape(50)).background(Au.Glass).border(1.dp, Au.Edge, RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(dot))
        Spacer(Modifier.width(7.dp))
        Caps(label, Au.Text, Modifier.widthIn(max = 150.dp), size = 10)
    }
}

/** What's wrong with the link, and the one thing to do about it. */
@Composable
private fun LinkCard(link: Link, onPairAgain: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val pc = Remote.store.pc?.name ?: "your PC"
    val (title, body) = when (link.kind) {
        LinkKind.UNREACHABLE -> "Can't reach $pc" to
            "Is it awake? At home, join its Wi-Fi. Away, turn on Tailscale on this phone and the PC."
        LinkKind.SERVER_OFF -> "$pc is up, Auren isn't" to "Start Auren on the PC. If it is running, auren doctor says why the phone server is off."
        LinkKind.REVOKED -> "This phone was unpaired" to "Its key was revoked on the PC. Pair again with auren phone pair."
        else -> "" to ""
    }
    Column(
        Modifier.fillMaxWidth().padding(top = 10.dp).clip(RoundedCornerShape(14.dp)).background(Au.Glass)
            .border(1.dp, Au.Red.copy(alpha = 0.45f), RoundedCornerShape(14.dp)).padding(14.dp),
    ) {
        Text(title, color = Au.Text, fontSize = 15.sp)
        Text(body, color = Au.Dim, fontSize = 13.sp, modifier = Modifier.padding(top = 3.dp))
        Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            when (link.kind) {
                LinkKind.UNREACHABLE -> GoldButton("Open Tailscale") {
                    val i = ctx.packageManager.getLaunchIntentForPackage("com.tailscale.ipn")
                        ?: android.content.Intent(android.content.Intent.ACTION_VIEW,
                            android.net.Uri.parse("https://play.google.com/store/apps/details?id=com.tailscale.ipn"))
                    ctx.startActivity(i)
                }
                LinkKind.REVOKED -> GoldButton("Pair again", onClick = onPairAgain)
                else -> Unit
            }
            if (link.kind != LinkKind.REVOKED) QuietButton("Retry") { Remote.retry() }
        }
    }
}

@Composable
private fun StateLine(st: AurenState, connected: Boolean, talking: Boolean) {
    val word = when {
        !connected -> "offline"
        st.awaiting -> "asking"
        talking && st.state != "listening" -> "starting"
        else -> st.state
    }
    val accent by animateColorAsState(
        when {
            !connected -> Au.Faint
            word == "listening" -> Au.Holo
            word == "error" -> Au.Red
            word == "idle" -> Au.Dim
            else -> Au.Gold
        }, label = "accent",
    )
    val detail = when {
        !connected -> ""
        st.state == "listening" && st.origin == "phone" -> st.detail.ifBlank { "Listening…" }
        st.state in setOf("thinking", "working") -> st.tool.ifBlank { st.detail }
        st.state == "idle" && !st.awaiting -> ""
        st.awaiting -> ""
        else -> st.detail
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Caps(word, accent, size = 12)
            if (connected && st.active && st.origin == "desk") {
                Spacer(Modifier.width(8.dp))
                Caps("· at the desk", Au.Faint, size = 10)
            }
        }
        if (detail.isNotBlank()) {
            Text(detail, color = Au.Dim, fontSize = 14.sp, textAlign = TextAlign.Center, maxLines = 3,
                overflow = TextOverflow.Ellipsis, fontStyle = if (st.state == "listening") FontStyle.Italic else FontStyle.Normal,
                modifier = Modifier.padding(top = 6.dp, start = 12.dp, end = 12.dp))
        }
    }
}

@Composable
/** [ask] names the question this card shows: a tap answers that one, never a newer one that came in meanwhile. */
private fun ApprovalCard(question: String, ask: String) {
    Column(
        Modifier.fillMaxWidth().padding(top = 14.dp).clip(RoundedCornerShape(16.dp)).background(Au.Glass)
            .border(1.dp, Au.Gold.copy(alpha = 0.7f), RoundedCornerShape(16.dp)).padding(16.dp),
    ) {
        Caps("Auren asks", Au.Gold)
        Text(question.ifBlank { "Go ahead?" }, color = Au.Text, fontSize = 15.sp, lineHeight = 21.sp,
            modifier = Modifier.padding(top = 8.dp))
        Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onClick = { Remote.answer(false, ask) }, modifier = Modifier.weight(1f).height(46.dp),
                border = BorderStroke(1.dp, Au.Edge)) { Text("No", color = Au.Text) }
            Button(onClick = { Remote.answer(true, ask) }, modifier = Modifier.weight(1f).height(46.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Au.Gold, contentColor = Au.Ink)) { Text("Yes") }
        }
    }
}

@Composable
private fun Conversation(lines: List<Line>, modifier: Modifier) {
    val list = rememberLazyListState()
    LaunchedEffect(lines.size) { if (lines.isNotEmpty()) list.animateScrollToItem(lines.size - 1) }
    if (lines.isEmpty()) {
        Box(modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text("Hold the button and talk,\nor type below.", color = Au.Faint, fontSize = 14.sp, textAlign = TextAlign.Center)
        }
        return
    }
    LazyColumn(modifier.fillMaxWidth().padding(top = 12.dp), state = list, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items(lines, key = { it.id }) { line ->
            when (line.who) {
                Who.YOU -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(line.text, color = if (line.waiting) Au.Dim else Au.Text, fontSize = 15.sp,
                        modifier = Modifier.widthIn(max = 300.dp).clip(RoundedCornerShape(16.dp, 16.dp, 4.dp, 16.dp))
                            .background(Au.Raised).padding(horizontal = 14.dp, vertical = 10.dp))
                }
                Who.AUREN -> Row(Modifier.fillMaxWidth().padding(end = 32.dp)) {
                    Box(Modifier.width(2.dp).heightIn(min = 22.dp).background(Au.Gold.copy(alpha = 0.8f)))
                    Text(line.text, color = Au.Text, fontSize = 15.sp, lineHeight = 22.sp, modifier = Modifier.padding(start = 12.dp))
                }
                Who.NOTE -> Text(line.text, color = Au.Faint, fontSize = 12.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            }
        }
    }
}

@Composable
private fun Dock(connected: Boolean, st: AurenState, talk: app.auren.phone.Talk?, level: Float, askMic: (() -> Unit) -> Unit) {
    var text by rememberSaveable { mutableStateOf("") }
    val busy = connected && st.active && st.origin == "desk"
    val stoppable = connected && (talk != null || (st.active && st.origin != "desk") || st.state in setOf("thinking", "working"))
    val speaking by Remote.speaker.speaking.collectAsStateWithLifecycle()     // a reply being read on this phone
    Column(Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = text, onValueChange = { text = it },
                placeholder = { Text("Ask Auren…", color = Au.Faint) },
                modifier = Modifier.weight(1f), shape = RoundedCornerShape(24.dp), maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (text.isNotBlank()) { Remote.ask(text); text = "" } }),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Au.Gold.copy(alpha = 0.7f), unfocusedBorderColor = Au.Edge,
                    focusedContainerColor = Au.Glass, unfocusedContainerColor = Au.Glass, cursorColor = Au.Gold,
                ),
            )
            IconButton(onClick = { Remote.ask(text); text = "" }, enabled = text.isNotBlank()) {
                Icon(Icons.AutoMirrored.Filled.Send, "Send", tint = if (text.isNotBlank()) Au.Gold else Au.Faint)
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceEvenly) {
            SideButton("Hands-free", enabled = connected && talk == null && !busy && !st.active, active = talk?.handsFree == true) {
                askMic { Remote.talkStart(handsFree = true) }
            }
            TalkPad(enabled = connected && !busy && (talk != null || !st.active), talk = talk, level = level, askMic = askMic)
            SideButton("Stop", enabled = stoppable || speaking, danger = true) {
                if (stoppable) Remote.cancel() else Remote.speaker.stop()
            }
        }
    }
}

/** Hold to talk, let go to send. During a hands-free turn a tap sends it. */
@Composable
private fun TalkPad(enabled: Boolean, talk: app.auren.phone.Talk?, level: Float, askMic: (() -> Unit) -> Unit) {
    val on = talk != null
    val enabledNow by rememberUpdatedState(enabled)
    val talkNow by rememberUpdatedState(talk)
    val ring by animateFloatAsState(if (on) 1f + level * 0.5f else 1f, tween(90), label = "ring")
    val fill by animateColorAsState(if (on) Au.Gold else Au.Glass, label = "fill")
    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(112.dp)) {
        Canvas(Modifier.fillMaxSize()) {
            val r = size.minDimension / 2 * 0.72f
            if (on) drawCircle(Au.Holo.copy(alpha = 0.18f + 0.3f * level), r * ring * 1.18f)
            drawCircle(fill, r)
            drawCircle(if (enabled || on) Au.Gold else Au.Faint, r, style = Stroke(2.dp.toPx()))
        }
        Box(
            Modifier.size(84.dp).clip(CircleShape)
                .semantics { contentDescription = if (on) "Release to send" else "Hold to talk" }
                .pointerInput(Unit) {
                    detectTapGestures(onPress = {
                        val t = talkNow
                        if (t != null) {                         // hands-free (or stuck): a tap sends
                            if (t.handsFree) Remote.talkStop()
                            return@detectTapGestures
                        }
                        if (!enabledNow) return@detectTapGestures
                        var mine: String? = null
                        askMic { if (Remote.talkStart(handsFree = false)) mine = Remote.talk.value?.id }
                        // On release, and when the screen goes away mid-hold (that cancels this), so the mic
                        // never stays on. Only this press's talk: not one that started after it ended.
                        try {
                            tryAwaitRelease()
                        } finally {
                            if (mine != null && Remote.talk.value?.id == mine) Remote.talkStop()
                        }
                    })
                },
            contentAlignment = Alignment.Center,
        ) {
            MicGlyph(if (on) Au.Ink else if (enabled) Au.Gold else Au.Faint)
        }
        Caps(if (on) (if (talk?.handsFree == true) "tap to send" else "release") else "hold", Au.Faint,
            Modifier.align(Alignment.BottomCenter), size = 9)
    }
}

@Composable
private fun MicGlyph(color: Color) {
    Canvas(Modifier.size(30.dp)) {
        val w = size.width
        val s = Stroke(w * 0.08f, cap = androidx.compose.ui.graphics.StrokeCap.Round)
        drawRoundRect(color, androidx.compose.ui.geometry.Offset(w * 0.36f, w * 0.06f), androidx.compose.ui.geometry.Size(w * 0.28f, w * 0.5f),
            androidx.compose.ui.geometry.CornerRadius(w * 0.14f))
        drawArc(color, 0f, 180f, false, androidx.compose.ui.geometry.Offset(w * 0.22f, w * 0.26f),
            androidx.compose.ui.geometry.Size(w * 0.56f, w * 0.48f), style = s)
        drawLine(color, androidx.compose.ui.geometry.Offset(w * 0.5f, w * 0.74f), androidx.compose.ui.geometry.Offset(w * 0.5f, w * 0.92f), w * 0.08f)
    }
}

@Composable
private fun SideButton(label: String, enabled: Boolean, active: Boolean = false, danger: Boolean = false, onClick: () -> Unit) {
    val tint = when { !enabled -> Au.Faint; danger -> Au.Red; active -> Au.Holo; else -> Au.Text }
    OutlinedButton(onClick = onClick, enabled = enabled, shape = RoundedCornerShape(50),
        border = BorderStroke(1.dp, if (enabled) tint.copy(alpha = 0.6f) else Au.Edge),
        modifier = Modifier.width(104.dp).height(42.dp)) {
        Caps(label, tint, size = 10)
    }
}

@Composable
fun GoldButton(label: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, modifier = modifier, shape = RoundedCornerShape(50),
        colors = ButtonDefaults.buttonColors(containerColor = Au.Gold, contentColor = Au.Ink)) { Text(label) }
}

@Composable
fun QuietButton(label: String, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, modifier = modifier, enabled = enabled, shape = RoundedCornerShape(50),
        border = BorderStroke(1.dp, Au.Edge)) {
        Text(label, color = if (enabled) Au.Text else Au.Faint)
    }
}
