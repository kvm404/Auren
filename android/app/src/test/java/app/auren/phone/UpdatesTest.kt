package app.auren.phone

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class UpdatesTest {
    private val base = "https://github.com/kvm404/Auren/releases/download"

    private fun asset(name: String, url: String = "$base/v0.1.10/$name", size: Long = 30_000_000, state: String = "uploaded") =
        JSONObject().put("name", name).put("browser_download_url", url).put("size", size).put("state", state)

    private fun release(tag: String = "v0.1.10", assets: Array<JSONObject> = arrayOf(
        asset("auren-phone-0.1.10.apk"), asset("auren-phone-0.1.10.apk.sha256", size = 89), asset("auren-0.1.10.tar.gz"),
    ), prerelease: Boolean = false, draft: Boolean = false) = JSONObject()
        .put("tag_name", tag).put("html_url", "https://github.com/kvm404/Auren/releases/tag/$tag")
        .put("prerelease", prerelease).put("draft", draft).put("assets", JSONArray(assets.toList())).toString()

    @Test
    fun `version codes match build gradle's`() {
        assertEquals(107L, Releases.code("0.1.7"))
        assertEquals(110L, Releases.code("0.1.10"))
        assertEquals(10203L, Releases.code("1.2.3"))
        assertNull(Releases.code("0.1.10-rc.1"))
        assertNull(Releases.code("0.1"))
        assertNull(Releases.code("0.100.0"))           // the formula would overlap the next major
    }

    @Test
    fun `a release with the phone app attached`() {
        val r = Releases.parse(release())!!
        assertEquals("0.1.10", r.version)
        assertEquals(110L, r.code)
        assertEquals("$base/v0.1.10/auren-phone-0.1.10.apk", r.apk)
        assertEquals("$base/v0.1.10/auren-phone-0.1.10.apk.sha256", r.sums)
        assertEquals(30_000_000L, r.size)
        assertEquals("auren-phone-0.1.10.apk", r.file)
        assertEquals("https://github.com/kvm404/Auren/releases/tag/v0.1.10", r.page)
    }

    @Test
    fun `no update until the apk and its checksum are both attached`() {
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-0.1.10.tar.gz")))))
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-phone-0.1.10.apk")))))
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-phone-0.1.10.apk.sha256")))))
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-phone-0.1.10.apk", state = "starter"),
            asset("auren-phone-0.1.10.apk.sha256")))))
        // Another version's APK isn't this release's.
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-phone-0.1.9.apk"), asset("auren-phone-0.1.9.apk.sha256")))))
    }

    @Test
    fun `never a prerelease, a draft, an odd tag or a plain http link`() {
        assertNull(Releases.parse(release(prerelease = true)))
        assertNull(Releases.parse(release(draft = true)))
        assertNull(Releases.parse(release(tag = "0.1.10")))
        assertNull(Releases.parse(release(tag = "v0.1.10-rc.1")))
        assertNull(Releases.parse(release(assets = arrayOf(asset("auren-phone-0.1.10.apk", url = "http://example.com/a.apk"),
            asset("auren-phone-0.1.10.apk.sha256")))))
    }

    @Test
    fun `the checksum line for the apk`() {
        val hex = "ab".repeat(32)
        assertEquals(hex, Releases.sha("$hex  auren-phone-0.1.10.apk\n", "auren-phone-0.1.10.apk"))
        assertEquals(hex, Releases.sha("${hex.uppercase()} *auren-phone-0.1.10.apk", "auren-phone-0.1.10.apk"))
        assertNull(Releases.sha("$hex  auren-phone-0.1.9.apk\n", "auren-phone-0.1.10.apk"))
        assertNull(Releases.sha("abc  auren-phone-0.1.10.apk\n", "auren-phone-0.1.10.apk"))
        assertNull(Releases.sha("", "auren-phone-0.1.10.apk"))
    }
}
