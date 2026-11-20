package app.auren.phone

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HandsTest {
    private val apps = listOf("Calculator", "Chrome", "Google Maps", "Maps Go", "WhatsApp", "YouTube Music", "YouTube")

    @Test
    fun `an app is found by its whole name first, then how it starts, then a word in it`() {
        assertEquals("YouTube", apps[matchApp("youtube", apps)!!])
        assertEquals("Maps Go", apps[matchApp("maps", apps)!!])
        assertEquals("Google Maps", apps[matchApp("google", apps)!!])
        assertEquals("YouTube Music", apps[matchApp("music", apps)!!])
        assertEquals("WhatsApp", apps[matchApp(" WhatsApp ", apps)!!])
        assertNull(matchApp("telegram", apps))
        assertNull(matchApp("  ", apps))
    }

    @Test
    fun `a missing app suggests ones that look alike`() {
        assertEquals(listOf("YouTube Music", "YouTube"), closeMatches("you tube", apps))
        assertTrue(closeMatches("telegram", apps).isEmpty())
    }

    @Test
    fun `screen text loses line breaks and icon-font glyphs`() {
        assertEquals("20° smoky", clean("\ue33d 20°\nsmoky \uDB80\uDD40"))
    }

    @Test
    fun `a control action parses and its answer names it`() {
        val m = Incoming.parse("""{"type":"control","id":"ab12","action":"tap","n":3,"expect":"Send"}""") as Incoming.Control
        assertEquals("ab12", m.id)
        assertEquals(3, m.body.getInt("n"))
        val out = JSONObject(Outgoing.controlResult("ab12", JSONObject().put("ok", true).put("screen", "Home")))
        assertEquals("control.result", out.getString("type"))
        assertEquals("ab12", out.getString("id"))
        assertEquals("""{"type":"control.ready","on":true}""", Outgoing.controlReady(true))
    }
}
