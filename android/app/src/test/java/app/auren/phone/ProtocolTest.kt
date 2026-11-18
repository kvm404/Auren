package app.auren.phone

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun `a pairing link from auren phone pair parses`() {
        // What Python's urlencode makes: spaces as +, the rest percent-encoded.
        val p = Pairing.parse("auren://pair?v=1&host=100.92.209.55&port=8735&code=Ab-_c9%3D&pc=raja+laptop")
        assertEquals(Pairing("100.92.209.55", 8735, "Ab-_c9=", "raja laptop"), p)
    }

    @Test
    fun `a pairing link to anything but a tailnet address is refused`() {
        assertNull(Pairing.parse("auren://pair?v=1&host=192.168.1.4&port=8735&code=x"))
        assertNull(Pairing.parse("auren://pair?v=2&host=100.92.209.55&port=8735&code=x"))
        assertNull(Pairing.parse("auren://pair?v=1&host=100.92.209.55&port=8735&code="))
        assertNull(Pairing.parse("https://example.com/?host=100.92.209.55"))
    }

    @Test
    fun `tailnet is 100 64 0 0 slash 10`() {
        assertTrue(isTailnet("100.64.0.1"))
        assertTrue(isTailnet("100.127.255.254"))
        assertFalse(isTailnet("100.128.0.1"))
        assertFalse(isTailnet("100.63.255.255"))
        assertFalse(isTailnet("10.0.0.1"))
        assertFalse(isTailnet("100.64.0"))
        assertFalse(isTailnet("100.64.0.300"))
    }

    @Test
    fun `hello carries the device, the inbox cursor and the state`() {
        val m = Incoming.parse(
            """{"type":"hello","api":1,"pc":"omarchy","device":{"id":"a1b2c3","name":"Pixel"},"inboxLast":7,
               "state":{"state":"thinking","detail":"…","origin":"phone","transcript":"what time is it"}}"""
        ) as Incoming.Hello
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
        val share = JSONObject(Outgoing.share("s1", "image", "ask", "", "what is this", "AAAA", "image/jpeg"))
        assertEquals("image/jpeg", share.getString("mime"))
        assertFalse(JSONObject(Outgoing.share("s2", "link", "open", "https://x.org", "")).has("data"))
    }
}
