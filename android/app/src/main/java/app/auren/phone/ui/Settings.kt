package app.auren.phone.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.auren.phone.LinkKind
import app.auren.phone.pathOf
import app.auren.phone.Remote

@Composable
fun SettingsScreen(onBack: () -> Unit, onUnpaired: () -> Unit) {
    val link by Remote.link.collectAsStateWithLifecycle()
    val readAloud by Remote.readAloud.collectAsStateWithLifecycle()
    var confirm by remember { mutableStateOf(false) }
    val pc = Remote.store.pc
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val version = remember { runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: "" }

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

        Section("Quick settings")
        Card {
            Text("Add the Auren tile", color = Au.Text, fontSize = 15.sp)
            Text("Pull down quick settings, tap the pencil, and drag Auren in. The tile starts a hands-free turn.",
                color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 2.dp))
        }

        Section("Safety")
        Card {
            Text("Requests from this phone run at the PC's safety level. When Auren needs a yes, you can answer here or at the desk.",
                color = Au.Dim, fontSize = 13.sp, lineHeight = 18.sp)
        }

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
        Text("Auren remote $version", color = Au.Faint, fontSize = 11.sp,
            modifier = Modifier.padding(vertical = 24.dp))
    }
}

@Composable
private fun Card(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Au.Glass)
            .border(1.dp, Au.Edge, RoundedCornerShape(14.dp)).padding(14.dp),
    ) { content() }
}
