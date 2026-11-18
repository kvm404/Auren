package app.auren.phone

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.auren.phone.ui.Au
import app.auren.phone.ui.AurenTheme
import app.auren.phone.ui.HomeScreen
import app.auren.phone.ui.InboxScreen
import app.auren.phone.ui.PairScreen
import app.auren.phone.ui.SettingsScreen

enum class Screen { HOME, PAIR, INBOX, SETTINGS }

class MainActivity : ComponentActivity() {
    /** A pairing link that opened the app (auren://pair?...): it fills the pair screen, never pairs by itself. */
    private val pairLink = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        take(intent)
        setContent { AurenTheme { App(pairLink.value) { pairLink.value = null } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        take(intent)
    }

    override fun onStart() {
        super.onStart()
        Remote.start()
    }

    override fun onStop() {
        super.onStop()
        Remote.stop()
    }

    private fun take(i: Intent?) {
        when (i?.action) {
            Intent.ACTION_VIEW -> i.dataString?.takeIf { it.startsWith("auren://pair") }?.let { pairLink.value = it }
            ACTION_TALK -> if (Remote.store.pc != null && micAllowed()) {
                if (Remote.link.value.kind == LinkKind.CONNECTED) Remote.talkStart(handsFree = true)
                else Remote.talkWhenConnected = true
            }
        }
    }

    private fun micAllowed() = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    companion object {
        const val ACTION_TALK = "app.auren.phone.TALK"
    }
}

@Composable
private fun App(pairLink: String?, pairLinkUsed: () -> Unit) {
    val link by Remote.link.collectAsStateWithLifecycle()
    var screen by rememberSaveable { mutableStateOf(if (Remote.store.pc == null) Screen.PAIR else Screen.HOME) }
    if (pairLink != null && screen != Screen.PAIR) screen = Screen.PAIR
    if (link.kind == LinkKind.UNPAIRED && screen != Screen.PAIR) screen = Screen.PAIR
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { Remote.notices.collect { snack.showSnackbar(it) } }

    val ctx = androidx.compose.ui.platform.LocalContext.current
    // Hold-to-talk can't wait for a permission dialog: after the user allows it, the next press talks.
    val askMicPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (!ok) Remote.say("Auren needs the microphone to hear you")
    }
    val askMic: (() -> Unit) -> Unit = { action ->
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) action()
        else askMicPermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    BackHandler(screen == Screen.INBOX || screen == Screen.SETTINGS || (screen == Screen.PAIR && Remote.store.pc != null)) {
        screen = Screen.HOME
    }
    Scaffold(
        containerColor = Au.Night,
        snackbarHost = {
            SnackbarHost(snack) { Snackbar(containerColor = Au.Raised, contentColor = Au.Text) { Text(it.visuals.message) } }
        },
    ) { pad ->
        val m = Modifier.fillMaxSize().padding(pad)
        androidx.compose.foundation.layout.Box(m) {
            when (screen) {
                Screen.PAIR -> PairScreen(
                    prefill = pairLink,
                    onPaired = { pairLinkUsed(); screen = Screen.HOME },
                    onBack = if (Remote.store.pc != null) ({ pairLinkUsed(); screen = Screen.HOME }) else null,
                )
                Screen.HOME -> HomeScreen(
                    onInbox = { screen = Screen.INBOX },
                    onSettings = { screen = Screen.SETTINGS },
                    onPairAgain = { Remote.unpair(); screen = Screen.PAIR },
                    askMic = askMic,
                )
                Screen.INBOX -> InboxScreen(onBack = { screen = Screen.HOME })
                Screen.SETTINGS -> SettingsScreen(onBack = { screen = Screen.HOME }, onUnpaired = { screen = Screen.PAIR })
            }
        }
    }
}
