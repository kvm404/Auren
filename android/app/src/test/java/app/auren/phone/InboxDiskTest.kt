package app.auren.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.concurrent.CountDownLatch
import kotlin.concurrent.thread

class InboxDiskTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun item(id: Long) = InboxItem(id, "text", "t$id", id.toDouble(), text = "x$id")

    @Test
    fun `the items and the cursor are saved together`() {
        val disk = InboxDisk(tmp.root.resolve("inbox.json"))
        assertEquals(InboxDisk.Saved(0, emptyList()), disk.load())          // nothing yet
        disk.save(7, listOf(item(7), item(3)))
        assertEquals(InboxDisk.Saved(7, listOf(item(7), item(3))), InboxDisk(tmp.root.resolve("inbox.json")).load())
        disk.save(0, emptyList())                                             // a new pairing starts clean
        assertEquals(InboxDisk.Saved(0, emptyList()), disk.load())
    }

    @Test
    fun `a broken file reads as empty`() {
        tmp.root.resolve("inbox.json").writeText("{not json")
        assertEquals(InboxDisk.Saved(0, emptyList()), InboxDisk(tmp.root.resolve("inbox.json")).load())
    }

    @Test
    fun `saves at once from many threads leave one whole file and no temp files`() {
        val disk = InboxDisk(tmp.root.resolve("inbox.json"))
        val go = CountDownLatch(1)
        val threads = (1L..16L).map { n ->
            thread {
                go.await()
                repeat(20) { disk.save(n, (1..n).map { item(it) }) }
            }
        }
        go.countDown()
        threads.forEach { it.join() }
        val saved = disk.load()
        assertTrue(saved.last in 1L..16L)
        assertEquals(saved.last.toInt(), saved.items.size)                    // the items match the cursor they came with
        assertEquals(listOf("inbox.json"), tmp.root.list()!!.toList())
    }
}
