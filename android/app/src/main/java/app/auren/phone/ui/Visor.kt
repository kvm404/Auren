package app.auren.phone.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.translate
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

enum class Mood { IDLE, LISTENING, THINKING, WORKING, SPEAKING, ASKING, ERROR, OFF }

fun moodOf(state: String, awaiting: Boolean, connected: Boolean): Mood = when {
    !connected -> Mood.OFF
    awaiting -> Mood.ASKING
    else -> when (state) {
        "listening" -> Mood.LISTENING
        "thinking" -> Mood.THINKING
        "working" -> Mood.WORKING
        "speaking" -> Mood.SPEAKING
        "error" -> Mood.ERROR
        "off", "starting" -> Mood.OFF
        else -> Mood.IDLE
    }
}

/**
 * Auren's head, as on the desktop: a cobalt hover-bot, dark glass visor, gold LED eyes, an antenna with a
 * glowing tip. Listening sends rings out of the antenna (as loud as you are), thinking orbits two sparks,
 * working narrows the eyes to a scanning slit, speaking bounces a little equalizer, an error turns the eyes
 * into red x's, and off dims everything and lets him sink.
 */
@Composable
fun Visor(mood: Mood, level: Float, modifier: Modifier = Modifier) {
    val clock = rememberInfiniteTransition(label = "clock")
    val t by clock.animateFloat(0f, 1f, infiniteRepeatable(tween(2400, easing = LinearEasing)), label = "t")
    var blink by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(mood) {
        while (mood == Mood.IDLE || mood == Mood.ASKING || mood == Mood.THINKING) {
            delay(2600L + (0..2400).random())
            blink = 1f; delay(110); blink = 0f
        }
    }
    val off = mood == Mood.OFF
    val eyeW by animateFloatAsState(
        when (mood) { Mood.LISTENING -> 1.35f; Mood.WORKING -> 1.6f; Mood.OFF -> 1.5f; else -> 1.1f }, tween(160), label = "w")
    val eyeH by animateFloatAsState(
        when (mood) { Mood.LISTENING -> 2.1f; Mood.WORKING -> 0.6f; Mood.OFF -> 0.22f; else -> 1.75f }, tween(160), label = "h")
    val heard by animateFloatAsState(level, tween(90), label = "level")
    val sink by animateFloatAsState(if (off) 1f else 0f, tween(600), label = "sink")

    Canvas(modifier) {
        val u = minOf(size.width / 18f, size.height / 15f)
        val ox = (size.width - 16 * u) / 2
        val bob = (1 - sink) * sin(t * 2 * PI).toFloat() * 0.28f * u + sink * 0.9f * u
        translate(ox, 2.2f * u + bob) {
            thruster(u, mood, t, 1 - sink)
            antenna(u, mood, t, heard, off)
            head(u, off)
            eyes(u, mood, t, eyeW, eyeH, blink, off)
            if (mood == Mood.SPEAKING) equalizer(u, t)
            if (mood == Mood.ASKING) question(u, t)
        }
    }
}

private fun DrawScope.thruster(u: Float, mood: Mood, t: Float, on: Float) {
    if (on < 0.05f) return
    val c = Offset(8 * u, 12.6f * u)
    val pulse = 0.85f + 0.15f * sin(t * 4 * PI).toFloat()
    val tint = if (mood == Mood.LISTENING) Au.Holo else Au.Gold
    drawOval(
        Brush.radialGradient(listOf(tint.copy(alpha = 0.45f * on * pulse), Color.Transparent), c, 4.2f * u),
        topLeft = Offset(c.x - 4.2f * u, c.y - 1.2f * u), size = Size(8.4f * u, 2.4f * u),
    )
}

private fun DrawScope.antenna(u: Float, mood: Mood, t: Float, level: Float, off: Boolean) {
    val base = Offset(8 * u, 3.1f * u)
    val tip = Offset(8 * u, 0.7f * u)
    drawLine(Au.CobaltDark.dim(off), base, tip, strokeWidth = 0.32f * u, cap = StrokeCap.Round)
    val glow = if (off) Au.Faint else if (mood == Mood.ERROR) Au.Red else Au.Gold
    if (!off) drawCircle(glow.copy(alpha = 0.25f), 1.1f * u, tip)
    drawCircle(glow, 0.5f * u, tip)
    drawCircle(Au.Core.copy(alpha = if (off) 0.2f else 0.8f), 0.2f * u, tip)
    when (mood) {
        Mood.LISTENING -> for (i in 0 until 3) {                  // radio rings, louder when you are
            val p = (t * 1.5f + i / 3f) % 1f
            drawCircle(Au.Holo.copy(alpha = (1 - p) * (0.35f + 0.65f * level)), (0.9f + p * (2.2f + 2.2f * level)) * u, tip,
                style = Stroke(0.18f * u))
        }
        Mood.THINKING -> for (i in 0 until 2) {                   // two sparks orbit the tip
            val a = (t + i * 0.5f) * 2 * PI
            drawCircle(Au.Led, 0.2f * u, tip + Offset(cos(a).toFloat() * 1.6f * u, sin(a).toFloat() * 0.7f * u))
        }
        else -> Unit
    }
}

private fun DrawScope.head(u: Float, off: Boolean) {
    val topLeft = Offset(1 * u, 3 * u)
    val sz = Size(14 * u, 9.2f * u)
    drawRoundRect(
        Brush.verticalGradient(listOf(Au.CobaltLight.dim(off), Au.Cobalt.dim(off), Au.CobaltDark.dim(off)), topLeft.y, topLeft.y + sz.height),
        topLeft, sz, CornerRadius(3.6f * u),
    )
    drawRoundRect(Color(0xFF1E2F55).dim(off), topLeft, sz, CornerRadius(3.6f * u), style = Stroke(0.22f * u))
    // ear bolts
    drawCircle(Au.CobaltDark.dim(off), 0.75f * u, Offset(1.1f * u, 7.6f * u))
    drawCircle(Au.CobaltDark.dim(off), 0.75f * u, Offset(14.9f * u, 7.6f * u))
    // the visor
    val vTop = Offset(2.3f * u, 5f * u)
    val vSize = Size(11.4f * u, 5.3f * u)
    drawRoundRect(Au.Glass, vTop, vSize, CornerRadius(2.4f * u))
    drawRoundRect(Color.White.copy(alpha = 0.07f), vTop + Offset(0.6f * u, 0.35f * u), Size(vSize.width - 1.2f * u, 0.5f * u),
        CornerRadius(0.25f * u))
}

private fun DrawScope.eyes(u: Float, mood: Mood, t: Float, w: Float, h: Float, blink: Float, off: Boolean) {
    val led = when { mood == Mood.ERROR -> Au.Red; off -> Color(0xFF5A5F6A); else -> Au.Led }
    val look = when (mood) {
        Mood.THINKING -> Offset(0.5f * u, -0.45f * u)
        Mood.WORKING -> Offset(sin(t * 2 * PI).toFloat() * 1.1f * u, 0f)
        Mood.OFF -> Offset(0f, 0.4f * u)
        else -> Offset.Zero
    }
    for (cx in floatArrayOf(5.7f, 10.3f)) {
        val c = Offset(cx * u, 7.65f * u) + look
        if (mood == Mood.ERROR) {
            val r = 0.7f * u
            drawLine(led, c + Offset(-r, -r), c + Offset(r, r), 0.36f * u, StrokeCap.Round)
            drawLine(led, c + Offset(-r, r), c + Offset(r, -r), 0.36f * u, StrokeCap.Round)
            continue
        }
        if (mood == Mood.SPEAKING) {                               // ^ happy, as he talks
            val p = Path().apply {
                moveTo(c.x - 0.75f * u, c.y + 0.4f * u); lineTo(c.x, c.y - 0.4f * u); lineTo(c.x + 0.75f * u, c.y + 0.4f * u)
            }
            drawPath(p, led, style = Stroke(0.38f * u, cap = StrokeCap.Round))
            continue
        }
        val ew = w * u
        val eh = maxOf(0.2f * u, h * u * (1 - blink * 0.85f))
        val bright = if (off) 0.5f else 1f
        drawRoundRect(led.copy(alpha = 0.25f * bright), Offset(c.x - (ew + 0.9f * u) / 2, c.y - (eh + 0.9f * u) / 2),
            Size(ew + 0.9f * u, eh + 0.9f * u), CornerRadius((minOf(ew, eh) + 0.9f * u) / 2))
        drawRoundRect(led.copy(alpha = bright), Offset(c.x - ew / 2, c.y - eh / 2), Size(ew, eh), CornerRadius(minOf(ew, eh) / 2))
        if (!off) drawRoundRect(Au.Core.copy(alpha = 0.75f), Offset(c.x - ew * 0.225f, c.y - eh * 0.275f),
            Size(ew * 0.45f, eh * 0.55f), CornerRadius(minOf(ew * 0.45f, eh * 0.55f) / 2))
    }
}

private fun DrawScope.equalizer(u: Float, t: Float) {
    for (i in 0 until 3) {
        val hgt = (0.35f + 0.65f * ((sin((t * 6 + i * 0.37f) * 2 * PI).toFloat() + 1) / 2)) * 0.9f * u
        val x = (7.2f + i * 0.8f) * u
        drawLine(Au.Led, Offset(x, 9.55f * u - hgt / 2), Offset(x, 9.55f * u + hgt / 2), 0.36f * u, StrokeCap.Round)
    }
}

private fun DrawScope.question(u: Float, t: Float) {
    val c = Offset(15.6f * u, 2.6f * u + sin(t * 2 * PI).toFloat() * 0.2f * u)
    val p = Path().apply {
        moveTo(c.x - 0.6f * u, c.y - 0.5f * u)
        cubicTo(c.x - 0.6f * u, c.y - 1.4f * u, c.x + 0.7f * u, c.y - 1.4f * u, c.x + 0.6f * u, c.y - 0.5f * u)
        cubicTo(c.x + 0.55f * u, c.y, c.x, c.y, c.x, c.y + 0.5f * u)
    }
    drawPath(p, Au.Gold, style = Stroke(0.32f * u, cap = StrokeCap.Round))
    drawCircle(Au.Gold, 0.2f * u, Offset(c.x, c.y + 1.15f * u))
}

private fun Color.dim(off: Boolean) = if (!off) this else {
    val g = (red * 0.3f + green * 0.59f + blue * 0.11f) * 0.85f
    Color(g, g, g * 1.05f, alpha)
}
