package app.auren.phone

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    private val fp = "8838ec0185cb0e1e4792b74215c1b6a529f0d16580b5ef11b7a79a9b59b82a02"

    @Test
    fun `a pairing link from auren phone pair parses`() {
        // What Python's urlencode makes: spaces as +, the rest percent-encoded.
        val p = Pairing.parse("auren://pair?v=1&hosts=100.92.209.55%2C192.168.0.105&port=8735&code=Ab-_c9%3D&fp=$fp&pc=raja+laptop")
        assertEquals(Pairing(listOf("100.92.209.55", "192.168.0.105"), 8735, "Ab-_c9=", fp, "raja laptop"), p)
    }

    @Test
    fun `a pairing link needs a pin and a tailnet or private address`() {
        assertNull(Pairing.parse("auren://pair?v=1&hosts=100.92.209.55&port=8735&code=x"))                // no pin
        assertNull(Pairing.parse("auren://pair?v=1&hosts=100.92.209.55&port=8735&code=x&fp=abc"))         // not a sha256
        assertNull(Pairing.parse("auren://pair?v=1&hosts=8.8.8.8&port=8735&code=x&fp=$fp"))               // public
        assertNull(Pairing.parse("auren://pair?v=2&hosts=100.92.209.55&port=8735&code=x&fp=$fp"))
        assertNull(Pairing.parse("auren://pair?v=1&hosts=100.92.209.55&port=8735&code=&fp=$fp"))
        assertNull(Pairing.parse("https://example.com/?hosts=100.92.209.55"))
        // A malformed percent-escape is refused, not thrown.
        assertNull(Pairing.parse("auren://pair?v=1&hosts=100.92.209.55&port=8735&code=%&fp=$fp"))
        assertNull(Pairing.parse("auren://pair?v=1&hosts=100.92.209.55&port=8735&code=%zz&fp=$fp"))
        // Only the usable addresses are kept.
        assertEquals(listOf("10.0.0.2"), Pairing.parse("auren://pair?v=1&hosts=8.8.8.8,10.0.0.2,127.0.0.1&port=1&code=x&fp=$fp")?.hosts)
    }

    @Test
    fun `tailnet is 100 64 0 0 slash 10 and the lan is rfc 1918`() {
        assertTrue(isTailnet("100.64.0.1"))
        assertTrue(isTailnet("100.127.255.254"))
        assertFalse(isTailnet("100.128.0.1"))
        assertFalse(isTailnet("100.63.255.255"))
        assertFalse(isTailnet("10.0.0.1"))
        assertFalse(isTailnet("100.64.0"))
        assertFalse(isTailnet("100.64.0.300"))
        assertTrue(isPrivateLan("10.1.2.3") && isPrivateLan("172.16.0.1") && isPrivateLan("172.31.9.9") && isPrivateLan("192.168.0.5"))
        assertFalse(isPrivateLan("172.32.0.1") || isPrivateLan("192.169.0.1") || isPrivateLan("8.8.8.8") || isPrivateLan("127.0.0.1"))
        assertFalse(isPrivateLan("192.168.01.5"))
        assertEquals("Tailscale", pathOf("100.92.209.55"))
        assertEquals("Wi-Fi", pathOf("192.168.0.105"))
    }

    @Test
    fun `the pc's address on this wi-fi goes first, then tailscale, then the rest`() {
        val hosts = listOf("100.92.209.55", "10.9.0.4", "192.168.0.105", "192.168.0.100")
        val home = listOf("192.168.0.42" to 24)
        assertEquals(listOf("192.168.0.105", "192.168.0.100", "100.92.209.55", "10.9.0.4"), tryOrder(hosts, home, null))
        // What worked last goes first in its group, not ahead of the direct path.
        assertEquals(listOf("192.168.0.100", "192.168.0.105", "100.92.209.55", "10.9.0.4"), tryOrder(hosts, home, "192.168.0.100"))
        assertEquals(listOf("192.168.0.105", "192.168.0.100", "100.92.209.55", "10.9.0.4"), tryOrder(hosts, home, "100.92.209.55"))
        // Away (mobile data, or another Wi-Fi): Tailscale first.
        assertEquals("100.92.209.55", tryOrder(hosts, emptyList(), "192.168.0.105").first())
        assertEquals("100.92.209.55", tryOrder(hosts, listOf("192.168.1.7" to 24), null).first())
        assertTrue(onSubnet("10.9.0.4", "10.1.1.1", 8) && !onSubnet("10.9.0.4", "10.1.1.1", 16))
        assertFalse(onSubnet("192.168.0.5", "192.168.0.1", 0))
    }

    @Test
    fun `a hello updates the addresses but never drops the tailscale route it didn't mention`() {
        val saved = listOf("100.92.209.55", "192.168.0.105")
        // The PC moved on the LAN (DHCP): the new address replaces the old one.
        assertEquals(listOf("100.92.209.55", "192.168.0.120"), mergeHosts(saved, listOf("100.92.209.55", "192.168.0.120"), "100.92.209.55"))
        // A hello over Wi-Fi while the PC's Tailscale is down keeps the saved tailnet address.
        assertEquals(listOf("192.168.0.105", "100.92.209.55"), mergeHosts(saved, listOf("192.168.0.105"), "192.168.0.105"))
        // A new tailnet address replaces the old one; no list keeps what's saved.
        assertEquals(listOf("100.64.1.2", "192.168.0.105"), mergeHosts(saved, listOf("100.64.1.2"), "192.168.0.105"))
        assertEquals(saved, mergeHosts(saved, emptyList(), "192.168.0.105"))
    }

    @Test
    fun `hello carries the device, the inbox cursor and the state`() {
        val m = Incoming.parse(
            """{"type":"hello","api":1,"pc":"omarchy","device":{"id":"a1b2c3","name":"Pixel"},"inboxLast":7,
               "addrs":[{"ip":"100.92.209.55","via":"tailscale"},{"ip":"192.168.0.111","via":"lan"},{"ip":"1.2.3.4"}],
               "state":{"state":"thinking","detail":"…","origin":"phone","transcript":"what time is it"}}"""
        ) as Incoming.Hello
        assertEquals(listOf("100.92.209.55", "192.168.0.111"), m.addrs)       // a changed LAN IP is learned here
        assertEquals(Device("a1b2c3", "Pixel"), m.device)
        assertEquals(7L, m.inboxLast)
        assertEquals("thinking", m.state.state)
        assertEquals("phone", m.state.origin)
        assertTrue(m.state.active)
    }

    @Test
    fun `state with a null origin and an open question`() {
        val m = Incoming.parse(
            """{"type":"state","state":"idle","origin":null,"awaiting":true,"question":"Close Calculator?","until":1.5e9}"""
        ) as Incoming.State
        assertNull(m.state.origin)
        assertTrue(m.state.awaiting)
        assertEquals("Close Calculator?", m.state.question)
        assertEquals("", m.state.ask)
        assertEquals("ab12", (Incoming.parse("""{"type":"state","awaiting":true,"ask":"ab12"}""") as Incoming.State).state.ask)
        assertFalse(m.state.active)
    }

    @Test
    fun `acks, busy, replies and the rest`() {
        val ack = Incoming.parse("""{"type":"ack","id":"r1","status":"ok","duplicate":true}""") as Incoming.Ack
        assertTrue(ack.ok && ack.duplicate)
        val busy = Incoming.parse("""{"type":"ack","id":"r2","status":"busy"}""") as Incoming.Ack
        assertTrue(busy.busy && !busy.ok)
        assertTrue((Incoming.parse("""{"type":"ack","id":"","status":"approved 2"}""") as Incoming.Ack).ok)
        assertTrue(!(Incoming.parse("""{"type":"ack","id":"","status":"error: nope"}""") as Incoming.Ack).ok)
        assertEquals(Incoming.Busy("r2", "desk"), Incoming.parse("""{"type":"busy","id":"r2","owner":"desk"}"""))
        assertEquals(Incoming.Reply("r1", "It's noon.", 12.0), Incoming.parse("""{"type":"reply","id":"r1","device":"x","text":"It's noon.","at":12}"""))
        assertEquals(Incoming.Revoked, Incoming.parse("""{"type":"revoked"}"""))
        assertEquals(Incoming.Lost("r9"), Incoming.parse("""{"type":"lost","id":"r9"}"""))
        assertEquals(Incoming.Ended("r8"), Incoming.parse("""{"type":"ended","id":"r8"}"""))
        assertNull(Incoming.parse("""{"type":"something-new"}"""))
        assertNull(Incoming.parse("not json"))
    }

    @Test
    fun `inbox items of each kind, and broken ones are dropped`() {
        val file = Incoming.parse(
            """{"type":"inbox","item":{"id":3,"kind":"file","title":"Report","at":5,"file":{"name":"r.pdf","size":6,"mime":"application/pdf"}}}"""
        ) as Incoming.Inbox
        assertEquals(InboxFile("r.pdf", 6, "application/pdf"), file.item.file)
        val link = InboxItem.from(JSONObject("""{"id":4,"kind":"link","title":"","at":1,"url":"https://x.org"}"""))!!
        assertEquals("https://x.org", link.url)
        assertEquals(link, InboxItem.from(link.toJson()))           // the phone's own cache round-trips
        assertEquals(file.item, InboxItem.from(file.item.toJson()))
        assertNull(InboxItem.from(JSONObject("""{"id":5,"kind":"image","title":"","at":1}""")))     // no file
        assertNull(InboxItem.from(JSONObject("""{"id":0,"kind":"text","text":"x"}""")))
        assertNull(Incoming.parse("""{"type":"inbox","item":{"id":6,"kind":"video"}}"""))
    }

    @Test
    fun `outgoing messages`() {
        val ask = JSONObject(Outgoing.ask("r1", "hi \"there\""))
        assertEquals("ask", ask.getString("type"))
        assertEquals("hi \"there\"", ask.getString("text"))
        val talk = JSONObject(Outgoing.talkStart("r2", handsFree = true))
        assertEquals("talk.start", talk.getString("type"))
        assertTrue(talk.getBoolean("handsFree"))
        assertEquals(9L, JSONObject(Outgoing.sync(9)).getLong("after"))
        val answer = JSONObject(Outgoing.answer("a1", yes = true, ask = "ab12"))
        assertEquals("ab12", answer.getString("ask"))                  // a yes names the question it was tapped on
        assertTrue(answer.getBoolean("yes"))
        assertEquals("r1", JSONObject(Outgoing.resume(listOf("r1", "r2"))).getJSONArray("ids").getString(0))
        val share = JSONObject(Outgoing.share("s1", "image", "ask", "", "what is this", "AAAA", "image/jpeg"))
        assertEquals("image/jpeg", share.getString("mime"))
        assertFalse(JSONObject(Outgoing.share("s2", "link", "open", "https://x.org", "")).has("data"))
    }

    @Test
    fun `the voice is in the state, and voice set names the new one`() {
        assertEquals("Hugh", AurenState.from(JSONObject("""{"state":"idle","voiceName":"Hugh"}""")).voiceName)
        assertEquals("", AurenState.from(JSONObject("""{"state":"idle"}""")).voiceName)     // an older PC
        val set = JSONObject(Outgoing.voiceSet("v1", "Trevor"))
        assertEquals("voice.set", set.getString("type"))
        assertEquals("v1", set.getString("id"))
        assertEquals("Trevor", set.getString("name"))
    }
}
