package app.auren.phone.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Auren's colours come from the mascot (mascot/mascots/auren/Auren.qml): a cobalt hover-bot with a dark
 * glass visor and gold LED eyes; holo cyan when he listens, red when something broke. The bar panel's
 * spaced capitals (LAST EXCHANGE) are the labels here.
 */
object Au {
    val Night = Color(0xFF070B16)          // the screen: deeper than the visor glass
    val Glass = Color(0xFF0C1222)          // the visor, cards
    val Raised = Color(0xFF121B31)
    val Edge = Color(0xFF1E2F55)
    val Cobalt = Color(0xFF4E73B8)
    val CobaltLight = Color(0xFF7097DA)
    val CobaltDark = Color(0xFF2F4B82)
    val Gold = Color(0xFFF5B83D)
    val Led = Color(0xFFFFD15C)
    val Core = Color(0xFFFFF6D6)
    val Holo = Color(0xFF7FDBFF)
    val Red = Color(0xFFFF5D5D)
    val Ink = Color(0xFF1D1310)            // text on gold
    val Text = Color(0xFFE6EAF4)
    val Dim = Color(0xFF8792AD)
    val Faint = Color(0xFF4A5470)
}

val Mono = FontFamily.Monospace

@Composable
fun AurenTheme(content: @Composable () -> Unit) {
    val scheme = darkColorScheme(
        primary = Au.Gold, onPrimary = Au.Ink,
        secondary = Au.CobaltLight, onSecondary = Au.Night,
        tertiary = Au.Holo,
        background = Au.Night, onBackground = Au.Text,
        surface = Au.Glass, onSurface = Au.Text,
        surfaceVariant = Au.Raised, onSurfaceVariant = Au.Dim,
        surfaceContainer = Au.Glass, surfaceContainerHigh = Au.Raised, surfaceContainerHighest = Au.Raised,
        outline = Au.Edge, outlineVariant = Au.Edge,
        error = Au.Red, onError = Au.Night,
    )
    MaterialTheme(colorScheme = scheme, content = content)
}

/** The panel's spaced capitals: section names, states, small labels. */
@Composable
fun Caps(text: String, color: Color = Au.Dim, modifier: Modifier = Modifier, size: Int = 11) {
    Text(
        text.uppercase(),
        modifier = modifier,
        color = color,
        style = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = size.sp, letterSpacing = (size * 0.16).sp),
        maxLines = 1,
    )
}

@Composable
fun Section(text: String) = Caps(text, Au.Faint, Modifier.padding(top = 22.dp, bottom = 8.dp))
