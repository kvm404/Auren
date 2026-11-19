package app.auren.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.security.MessageDigest

class VoicesTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test
    fun `four voices, Everly first, each pinned`() {
        assertEquals(listOf("Everly", "Juniper", "Hugh", "Trevor"), Voices.ALL.map { it.name })
        assertEquals("Everly", Voices.DEFAULT.name)
        val hex = Regex("[0-9a-f]{64}")
        assertTrue(Voices.FILES.all { hex.matches(it.sha256) && it.size > 0 && it.url.startsWith("https://huggingface.co/") })
        assertEquals(Voices.FILES.size, Voices.FILES.map { it.path }.toSet().size)
        // The library lists a voice by its file's name, which must be the name the PC's state uses.
        assertEquals(Voices.ALL.map { "voices/${it.name}.safetensors" }, Voices.FILES.drop(3).map { it.path })
        assertTrue(Voices.FILES.all { "/resolve/${Voices.MODEL_COMMIT}/" in it.url || "/resolve/${Voices.VOICES_COMMIT}/" in it.url })
    }

    @Test
    fun `the reply is read in the pc's voice, else the default`() {
        assertEquals("Hugh", Voices.forState("Hugh").name)
        assertEquals("Juniper", Voices.forState(" juniper ").name)
        assertEquals("Everly", Voices.forState("").name)              // an older PC names none
        assertEquals("Everly", Voices.forState("Harper").name)        // one this app doesn't have
        assertNull(Voices.byName(null))
    }

    @Test
    fun `the picker shows a pick on its way, then the pc's`() {
        assertEquals("Trevor", Voices.shown("Everly", "Trevor").name)
        assertEquals("Everly", Voices.shown("Everly", null).name)
        assertEquals("Hugh", Voices.shown("Hugh", "nonsense").name)
    }

    // --- the download ---------------------------------------------------------------------------------

    private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }
    private val a = ByteArray(300_000) { (it * 7).toByte() }
    private val b = "voice b".toByteArray()
    private val files = listOf(
        ModelFile("https://h/a", "model.bin", a.size.toLong(), sha(a)),
        ModelFile("https://h/b", "voices/B.safetensors", b.size.toLong(), sha(b)),
    )
    private val bodies = mapOf("https://h/a" to a, "https://h/b" to b)

    /** A server: records each request, honours ranges unless [ranges] is false, can corrupt a body. */
    private class Server(val bodies: Map<String, ByteArray>, val ranges: Boolean = true) : Fetch {
        val asked = mutableListOf<Pair<String, Long>>()
        var corrupt = false
        override fun open(url: String, offset: Long): Fetched {
            asked += url to offset
            val body = bodies.getValue(url).copyOf().also { if (corrupt) it[it.size - 1] = (it.last() + 1).toByte() }
            return if (ranges && offset > 0) Fetched(ByteArrayInputStream(body, offset.toInt(), body.size), true)
            else Fetched(ByteArrayInputStream(body), false)
        }
    }

    @Test
    fun `installs every file, checks it, and is ready after`() {
        val dir = tmp.newFolder()
        val server = Server(bodies)
        val i = Installer(dir, files, server)
        assertFalse(i.ready())
        var last = 0L
        i.install({ last = it })
        assertTrue(i.ready())
        assertEquals(i.total, last)
        assertTrue(a.contentEquals(File(dir, "model.bin").readBytes()))
        assertTrue(b.contentEquals(File(dir, "voices/B.safetensors").readBytes()))
        assertEquals(listOf("https://h/a" to 0L, "https://h/b" to 0L), server.asked)
    }

    @Test
    fun `a file that doesn't match its checksum is thrown away, and nothing counts as ready`() {
        val dir = tmp.newFolder()
        val server = Server(bodies).apply { corrupt = true }
        val e = assertThrows(IOException::class.java) { Installer(dir, files, server).install() }
        assertTrue(e.message!!.contains("checksum"))
        assertFalse(File(dir, "model.bin").exists())
        assertFalse(File(dir, "model.bin.part").exists())
        assertFalse(Installer(dir, files, server).ready())
    }

    @Test
    fun `a cut download resumes where it stopped`() {
        val dir = tmp.newFolder()
        File(dir, "model.bin.part").writeBytes(a.copyOf(120_000))
        val server = Server(bodies)
        Installer(dir, files, server).install()
        assertEquals("https://h/a" to 120_000L, server.asked.first())
        assertTrue(a.contentEquals(File(dir, "model.bin").readBytes()))
    }

    @Test
    fun `a server that ignores the range sends it all again, and that works too`() {
        val dir = tmp.newFolder()
        File(dir, "model.bin.part").writeBytes(a.copyOf(120_000))
        val server = Server(bodies, ranges = false)
        val i = Installer(dir, files, server)
        i.install()
        assertTrue(i.ready())
        assertTrue(a.contentEquals(File(dir, "model.bin").readBytes()))
    }

    @Test
    fun `files already right are kept, and a voice swapped out is removed`() {
        val dir = tmp.newFolder()
        Installer(dir, files, Server(bodies)).install()
        val c = "voice c".toByteArray()
        val swapped = files.take(1) + ModelFile("https://h/c", "voices/C.safetensors", c.size.toLong(), sha(c))
        val server = Server(bodies + ("https://h/c" to c))
        val i = Installer(dir, swapped, server)
        assertFalse(i.ready())                                  // the manifest names the old list
        i.install()
        assertTrue(i.ready())
        assertEquals(listOf("https://h/c" to 0L), server.asked) // the model wasn't fetched again
        assertFalse(File(dir, "voices/B.safetensors").exists())
        assertTrue(File(dir, "voices/C.safetensors").exists())
    }

    @Test
    fun `a file changed on disk after install isn't ready`() {
        val dir = tmp.newFolder()
        val i = Installer(dir, files, Server(bodies))
        i.install()
        File(dir, "voices/B.safetensors").writeBytes("short".toByteArray())
        assertFalse(i.ready())
        assertFalse(verified(File(dir, "voices/B.safetensors"), files[1]))
    }
}
