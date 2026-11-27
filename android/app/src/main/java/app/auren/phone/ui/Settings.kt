package app.auren.phone.ui

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.auren.phone.LinkKind
import app.auren.phone.codexName
import app.auren.phone.pathOf
import app.auren.phone.Release
import app.auren.phone.Remote
import app.auren.phone.UpdateStatus
import app.auren.phone.Updates
import app.auren.phone.VoiceStatus
import app.auren.phone.Voices
import androidx.compose.ui.semantics.Role

@Composable
fun SettingsScreen(onBack: () -> Unit, onUnpaired: () -> Unit) {
    val link by Remote.link.collectAsStateWithLifecycle()
    val readAloud by Remote.readAloud.collectAsStateWithLifecycle()
    var confirm by remember { mutableStateOf(false) }
    val pc = Remote.store.pc

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Caps("Settings", Au.Gold, size = 13)
            Box(Modifier.weight(1f))
            QuietButton("Back", onClick = onBack)
        }

        Section("PC")
        Card {
            Text(pc?.name ?: "Not paired", color = Au.Text, fontSize = 16.sp)
            if (pc != null) {
                Text(pc.hosts.joinToString("  ") { "$it (${pathOf(it)})" } + "  port ${pc.port}", color = Au.Dim, fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                    modifier = Modifier.padding(top = 2.dp))
                Text(
                    when (link.kind) {
                        LinkKind.CONNECTED -> "Connected over ${link.via}"
                        LinkKind.CONNECTING -> "Connecting…"
                        LinkKind.SERVER_OFF -> "The PC is up; Auren isn't serving"
                        LinkKind.UNREACHABLE -> "Can't reach the PC"
                        LinkKind.REVOKED -> "This phone's key was revoked"
                        LinkKind.UNPAIRED -> "Not paired"
                    } + if (link.why.isNotBlank() && link.kind != LinkKind.CONNECTED) " (${link.why})" else "",
                    color = Au.Faint, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp),
                )
            }
        }

        Section("Replies")
        Card {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Read replies aloud", color = Au.Text, fontSize = 15.sp)
                    Text("With this phone's voice. The PC stays quiet for phone requests either way.", color = Au.Dim,
                        fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 2.dp))
                }
                Switch(readAloud, { Remote.setReadAloud(it) }, colors = SwitchDefaults.colors(
                    checkedThumbColor = Au.Ink, checkedTrackColor = Au.Gold, uncheckedTrackColor = Au.Raised,
                    uncheckedBorderColor = Au.Edge, uncheckedThumbColor = Au.Dim))
            }
        }

        Section("Voice")
        VoiceCard(readAloud)

        Section("Brain")
        BrainCard(link.kind == LinkKind.CONNECTED)

        Section("Quick settings")
        Card {
            Text("Add the Auren tile", color = Au.Text, fontSize = 15.sp)
            Text("Pull down quick settings, tap the pencil, and drag Auren in. The tile starts a hands-free conversation.",
                color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 2.dp))
        }

        Section("Safety")
        Card {
            Text("Requests from this phone run at the PC's safety level. When Auren needs a yes, you can answer here or at the desk.",
                color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp)
        }

        Section("Updates")
        UpdateCard()

        if (pc != null) {
            Section("Unpair")
            Card {
                Text("Forget this PC on the phone. To stop the key working on the PC as well, run auren phone revoke there.",
                    color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp)
                OutlinedButton(
                    onClick = { if (confirm) { Remote.unpair(); onUnpaired() } else confirm = true },
                    modifier = Modifier.padding(top = 10.dp),
                    border = androidx.compose.foundation.BorderStroke(1.dp, Au.Red.copy(alpha = 0.6f)),
                ) { Text(if (confirm) "Tap again to unpair" else "Unpair", color = Au.Red) }
            }
        }
        Box(Modifier.height(24.dp))
    }
}

/**
 * This app's version and its update from GitHub: check, download, install. Installing needs Android's
 * "install unknown apps" for Auren, asked for the first time Update is tapped.
 */
@Composable
private fun UpdateCard() {
    val status by Updates.status.collectAsStateWithLifecycle()
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val version = remember { Updates.version }
    // Android's settings answer nothing: whether it's allowed now is all that counts.
    val allow = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        if (!Updates.canInstall()) return@rememberLauncherForActivityResult
        val r = Updates.status.value.release
        if (r != null) Updates.install(r) else Updates.check()
    }
    val update: (Release) -> Unit = { r ->
        if (Updates.canInstall()) Updates.install(r)
        else runCatching {
            allow.launch(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}")))
        }.onFailure { Remote.say("Allow Auren to install apps in Android's settings") }
    }
    fun mb(n: Long) = "%.0f".format(n / 1e6)
    val note = Modifier.padding(top = 4.dp)

    Card {
        Text("Auren remote $version", color = Au.Text, fontSize = 15.sp)
        when (val s = status) {
            UpdateStatus.Unknown, UpdateStatus.Current, UpdateStatus.Checking -> {
                Row(note, verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        when (s) {
                            UpdateStatus.Checking -> "Checking GitHub for a newer version…"
                            UpdateStatus.Current -> "Up to date."
                            else -> "Updates come from Auren's releases on GitHub."
                        },
                        color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.weight(1f),
                    )
                    QuietButton(if (s == UpdateStatus.Current) "Check again" else "Check", Modifier.padding(start = 8.dp),
                        enabled = s != UpdateStatus.Checking) { Updates.check() }
                }
            }
            is UpdateStatus.Available -> {
                Text("Auren ${s.release.version} is out.", color = Au.Gold, fontSize = 13.sp, modifier = note)
                Text("Update downloads it from GitHub" + (if (s.release.size > 0) " (${mb(s.release.size)} MB)" else "") +
                    " and installs it over this one; the pairing stays. Auren closes while it installs.",
                    color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp, modifier = note)
                Row(Modifier.padding(top = 10.dp)) {
                    GoldButton("Update") { update(s.release) }
                    if (s.release.page.isNotEmpty()) QuietButton("What's new", Modifier.padding(start = 8.dp)) {
                        runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(s.release.page))) }
                    }
                }
            }
            is UpdateStatus.Downloading -> {
                LinearProgressIndicator(
                    progress = { if (s.release.size > 0) s.done.toFloat() / s.release.size else 0f },
                    modifier = Modifier.fillMaxWidth().padding(top = 10.dp), color = Au.Gold, trackColor = Au.Raised,
                    drawStopIndicator = {},
                )
                Text("Downloading Auren ${s.release.version}" +
                    (if (s.release.size > 0) ", ${mb(s.done)} of ${mb(s.release.size)} MB" else "") + "…",
                    color = Au.Faint, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                QuietButton("Cancel", Modifier.padding(top = 10.dp)) { Updates.cancel() }
            }
            is UpdateStatus.Installing -> {
                Text("Installing Auren ${s.release.version}. Android asks you to confirm, then Auren closes: open it again after.",
                    color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp, modifier = note)
                QuietButton("Try again", Modifier.padding(top = 10.dp)) { update(s.release) }
            }
            is UpdateStatus.Failed -> Row(note, verticalAlignment = Alignment.CenterVertically) {
                Text((if (s.release == null) "Couldn't check for updates: " else "Couldn't update: ") + s.why + ".",
                    color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.weight(1f))
                QuietButton("Retry", Modifier.padding(start = 8.dp)) {
                    val r = s.release
                    if (r != null) update(r) else Updates.check()
                }
            }
        }
    }
}

/** The four voices, one setting shared with the PC, and how the phone's copy of the voice is doing. */
@Composable
private fun VoiceCard(readAloud: Boolean) {
    val st by Remote.state.collectAsStateWithLifecycle()
    val pending by Remote.pendingVoice.collectAsStateWithLifecycle()
    val status by Remote.speaker.status.collectAsStateWithLifecycle()
    val chosen = Voices.shown(st.voiceName, pending)
    Card {
        Text("Auren speaks in this voice at the desk and on this phone.", color = Au.Dim, fontSize = 13.sp,
            lineHeight = 18.sp, modifier = Modifier.padding(bottom = 8.dp))
        Column(Modifier.selectableGroup()) {
            Voices.ALL.forEach { v ->
                val on = v == chosen
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(if (on) Au.Raised else Au.Glass)
                        .selectable(selected = on, role = Role.RadioButton) { Remote.setVoice(v.name) }
                        .padding(horizontal = 10.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(if (on) Au.Gold else Au.Edge))
                    Column(Modifier.weight(1f).padding(start = 12.dp)) {
                        Text(v.name, color = if (on) Au.Text else Au.Dim, fontSize = 15.sp)
                        Text(v.blurb, color = Au.Faint, fontSize = 12.sp)
                    }
                    if (on && pending != null) Caps("Switching", Au.Faint, size = 10)
                }
            }
        }
        VoiceStatusLine(status, readAloud)
    }
}

private class Pick(val value: String, val label: String, val tip: String)

// The bar panel's wording (omarchy-plugin/Panel.qml), so the desk and the phone describe the same choices alike.
private val CLAUDE_EFFORTS = listOf(
    Pick("low", "Low", "Fastest; fine for most requests (the default)"),
    Pick("medium", "Medium", "A bit more care, a bit slower"),
    Pick("high", "High", "Harder requests; noticeably slower"),
    Pick("xhigh", "XHigh", "Much slower; uses more of the Claude allowance"),
    Pick("max", "Max", "Slowest and most expensive"),
)
private val CODEX_EFFORTS = listOf(
    Pick("minimal", "Minimal", "Fastest; barely thinks"),
    Pick("low", "Low", "Quick; fine for most requests (the default)"),
    Pick("medium", "Medium", "A bit more care, a bit slower"),
    Pick("high", "High", "Harder requests; noticeably slower"),
    Pick("xhigh", "XHigh", "Much slower; uses more of the ChatGPT allowance"),
)

/**
 * Who answers and how hard it thinks, like the bar panel's Brain page. The values are the PC's: a pick shows
 * as switching until the PC's state carries it.
 */
@Composable
private fun BrainCard(connected: Boolean) {
    val st by Remote.state.collectAsStateWithLifecycle()
    val pending by Remote.pendingSettings.collectAsStateWithLifecycle()
    Card {
        if (st.mode.isEmpty()) {
            // An older PC doesn't send these (or this phone hasn't heard from the PC yet).
            Text(if (connected) "Update Auren on the PC to choose who answers from this phone."
                else "Connect to the PC to choose who answers.",
                color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp)
        } else {
            val name = codexName(st.codexModel)
            val modes = listOf(
                Pick("auto", "Auto", "Claude Haiku; Codex when Claude's limit is hit"),
                Pick("claude", "Claude", "Claude Haiku only"),
                Pick("codex", "Codex", (if (name.isNotEmpty()) "$name via Codex" else "Codex") + " (saves Claude's allowance)"),
            )
            // Codex's own levels while it answers, as on the panel; which one is the PC's call, not a pending pick.
            val codex = st.codexAnswers
            val effortName = if (codex) "codex-effort" else "effort"
            val effort = if (codex) st.codexEffort else st.effort
            val now = when {
                st.mode == "codex" -> if (name.isNotEmpty()) "Codex ($name)" else "Codex"
                st.mode == "claude" -> "Claude Haiku only"
                st.brain == "codex" -> "Codex for now (Claude's limit)"
                else -> "Claude Haiku"
            } + if (effort.isNotEmpty()) ", $effort effort" else ""

            val mode = pending["brain"] ?: st.mode
            Title("Who answers", pending["brain"] != null)
            Segments(modes, mode, connected) { Remote.setSetting("brain", it) }
            Text(modes.firstOrNull { it.value == mode }?.tip.orEmpty(), color = Au.Dim, fontSize = 12.sp, lineHeight = 16.sp,
                maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 6.dp))
            Text("Now: $now", color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.padding(top = 2.dp))

            Title(if (codex) "Codex effort" else "Claude effort", pending[effortName] != null, Modifier.padding(top = 16.dp))
            Dropdown(if (codex) CODEX_EFFORTS else CLAUDE_EFFORTS, pending[effortName] ?: effort, connected) {
                Remote.setSetting(effortName, it)
            }
            Text(
                when {
                    st.mode == "codex" -> "Codex only: Claude isn't used. Higher thinks harder but answers slower."
                    codex -> "Codex answers until Claude's limit resets. Higher thinks harder but answers slower."
                    else -> "Higher thinks harder but answers slower. Changing it starts a fresh conversation."
                },
                color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.padding(top = 6.dp),
            )
        }
        // Also on Home's top bar; here it sits by the settings that start fresh too.
        Row(Modifier.padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Start a fresh conversation on the PC.", color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp,
                modifier = Modifier.weight(1f))
            QuietButton("Fresh", Modifier.padding(start = 8.dp), enabled = connected) { Remote.forget() }
        }
    }
}

/** A Brain setting's name, with "Switching…" beside it while a pick waits for the PC's state to carry it. */
@Composable
private fun Title(text: String, switching: Boolean, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(bottom = 8.dp).height(16.dp), verticalAlignment = Alignment.CenterVertically) {
        Caps(text, Au.Faint, Modifier.weight(1f), size = 10)
        if (switching) {
            CircularProgressIndicator(Modifier.size(10.dp), color = Au.Gold, strokeWidth = 1.5.dp, trackColor = Au.Edge)
            Caps("Switching…", Au.Dim, Modifier.padding(start = 6.dp), size = 9)
        }
    }
}

/** One of a few, side by side (Material's segmented buttons). Disabled while not connected: the values live on the PC. */
@Composable
private fun Segments(options: List<Pick>, chosen: String, enabled: Boolean, onPick: (String) -> Unit) {
    val colors = SegmentedButtonDefaults.colors(
        activeContainerColor = Au.Gold.copy(alpha = 0.16f), activeContentColor = Au.Gold, activeBorderColor = Au.Gold.copy(alpha = 0.7f),
        inactiveContainerColor = Au.Glass, inactiveContentColor = Au.Dim, inactiveBorderColor = Au.Edge,
        disabledActiveContainerColor = Au.Raised, disabledActiveContentColor = Au.Dim, disabledActiveBorderColor = Au.Edge,
        disabledInactiveContainerColor = Au.Glass, disabledInactiveContentColor = Au.Faint, disabledInactiveBorderColor = Au.Edge,
    )
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        options.forEachIndexed { i, p ->
            SegmentedButton(
                selected = p.value == chosen,
                onClick = { onPick(p.value) },
                shape = SegmentedButtonDefaults.itemShape(index = i, count = options.size),
                enabled = enabled,
                colors = colors,
                icon = {},                      // no check mark: the fill says which, and the labels keep their room
                label = { Text(p.label, fontSize = 14.sp, maxLines = 1) },
            )
        }
    }
}

/**
 * One of a longer list, as a menu under a read-only field showing the current one (Material's exposed
 * dropdown); each level's tip is under its name in the menu. Disabled while not connected.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Dropdown(options: List<Pick>, chosen: String, enabled: Boolean, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val shown = open && enabled
    ExposedDropdownMenuBox(expanded = shown, onExpandedChange = { if (enabled) open = it }) {
        OutlinedTextField(
            value = options.firstOrNull { it.value == chosen }?.label ?: chosen.ifEmpty { "—" },
            onValueChange = {},
            readOnly = true,
            enabled = enabled,
            singleLine = true,
            textStyle = TextStyle(fontSize = 15.sp),
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = shown) },
            shape = RoundedCornerShape(12.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedTextColor = Au.Text, unfocusedTextColor = Au.Text, disabledTextColor = Au.Dim,
                focusedContainerColor = Au.Glass, unfocusedContainerColor = Au.Glass, disabledContainerColor = Au.Glass,
                focusedBorderColor = Au.Gold.copy(alpha = 0.7f), unfocusedBorderColor = Au.Edge, disabledBorderColor = Au.Edge,
                focusedTrailingIconColor = Au.Gold, unfocusedTrailingIconColor = Au.Dim, disabledTrailingIconColor = Au.Faint,
                cursorColor = Au.Gold,
            ),
            modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable, enabled).fillMaxWidth(),
        )
        ExposedDropdownMenu(
            expanded = shown,
            onDismissRequest = { open = false },
            containerColor = Au.Raised,
            border = BorderStroke(1.dp, Au.Edge),
        ) {
            options.forEach { p ->
                val on = p.value == chosen
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(p.label, color = if (on) Au.Gold else Au.Text, fontSize = 15.sp)
                            Text(p.tip, color = Au.Dim, fontSize = 12.sp, lineHeight = 16.sp)
                        }
                    },
                    onClick = {
                        open = false
                        onPick(p.value)
                    },
                    trailingIcon = { if (on) Icon(Icons.Filled.Check, null, tint = Au.Gold) },
                    contentPadding = ExposedDropdownMenuDefaults.ItemContentPadding,
                )
            }
        }
    }
}

@Composable
private fun VoiceStatusLine(status: VoiceStatus, readAloud: Boolean) {
    val small = Modifier.padding(top = 10.dp)
    fun mb(n: Long) = "%.0f".format(n / 1e6)
    when (status) {
        is VoiceStatus.Ready -> Text("Ready on this phone.", color = Au.Faint, fontSize = 12.sp, modifier = small)
        is VoiceStatus.Loading -> Text("Loading the voice…", color = Au.Faint, fontSize = 12.sp, modifier = small)
        is VoiceStatus.Downloading -> Column(small) {
            LinearProgressIndicator(
                progress = { if (status.total > 0) status.done.toFloat() / status.total else 0f },
                modifier = Modifier.fillMaxWidth(), color = Au.Gold, trackColor = Au.Raised, drawStopIndicator = {},
            )
            Text("Downloading the voice, ${mb(status.done)} of ${mb(status.total)} MB. Until then, replies use the phone's own voice.",
                color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.padding(top = 6.dp))
        }
        is VoiceStatus.Missing -> Row(small, verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (readAloud) "Getting the voice ready…"
                else "To read replies in it, this phone downloads the voice once (${mb(Voices.FILES.sumOf { it.size })} MB).",
                color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.weight(1f),
            )
            if (!readAloud) QuietButton("Download", Modifier.padding(start = 8.dp)) { Remote.speaker.prepare() }
        }
        is VoiceStatus.Failed -> Row(small, verticalAlignment = Alignment.CenterVertically) {
            Text("Couldn't get the voice (${status.why}). Until then, replies use the phone's own voice.",
                color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = Modifier.weight(1f))
            QuietButton("Retry", Modifier.padding(start = 8.dp)) { Remote.speaker.prepare() }
        }
        is VoiceStatus.Unsupported -> Text("On this phone, replies use the phone's own voice: ${status.why}.",
            color = Au.Faint, fontSize = 12.sp, lineHeight = 16.sp, modifier = small)
    }
}

@Composable
private fun Card(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Au.Glass)
            .border(1.dp, Au.Edge, RoundedCornerShape(14.dp)).padding(14.dp),
    ) { content() }
}
