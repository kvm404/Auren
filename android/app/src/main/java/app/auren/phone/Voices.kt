package app.auren.phone

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest

/**
 * The phone's own voice: Gradium's Phonon 31m model, read on the phone (Speaker.kt), in one of the four voices
 * the PC also speaks in. One shared setting: the PC's state names the voice (`voiceName`), and the picker
 * changes it on the PC with `voice.set`. Plain Kotlin, so the catalog, the choice and the download checks are
 * unit tested off the device.
 */
data class PhoneVoice(val name: String, val id: String, val sha256: String, val blurb: String, val size: Long = 256_136)

/** One file of the model folder: where it comes from, where it goes in the folder, and what it must be. */
data class ModelFile(val url: String, val path: String, val size: Long, val sha256: String)

object Voices {
    /** The commit xn-ptts v0.7.1 loads by default (its DEFAULT_MODEL), so the model matches the library. */
    const val MODEL_COMMIT = "92741c5b07d085ff840e26fea8ce43110b87852f"
    const val VOICES_COMMIT = "0ee17205f254b5abc43b266c8a0f215565c352f7"

    /**
     * The four voices, the PC's default first: name, catalog id (voices.jsonl in gradium/phonon-voices), the
     * file's SHA-256 and a line for the picker. Swapping one is one line here (and the same name in the
     * daemon's VOICES, src/auren/voice.py); the phone downloads the new file and drops the old one.
     */
    val ALL = listOf(
        PhoneVoice("Everly", "vjFyp2BcCyTlUmYb", "958ea94da14f46dd48c7f64adac11ed2ad2aa45f7ccd165dfebab5272664402e", "US · rounded, soft, relaxed"),
        PhoneVoice("Juniper", "NIWIOv5vTi5eRMj8", "444378eb06b7c492b86e8fc7d13e4db1a3c0cf3e68891a960bf6f0a41a13ef35", "US · cheerful, resonant"),
        PhoneVoice("Hugh", "YyKcxPG7iIgS3jsK", "3169d74b47a54d1eb15e4c243f56393abe9fe90c7e1c308ecf96ee67618b152a", "UK · soft, close, lively"),
        PhoneVoice("Trevor", "0zLApsZfVLLNaEGl", "dc1341c8937af5534e6bbcc5c0fc5e7c10312229b59061228074f93683fe2261", "US · buoyant, warm low core"),
    )
    val DEFAULT get() = ALL.first()

    fun byName(name: String?): PhoneVoice? {
        val n = name?.trim().orEmpty()
        return ALL.firstOrNull { it.name.equals(n, ignoreCase = true) }
    }

    /** The voice to read a reply in: the PC's, or the default while the PC names none of these (an older PC). */
    fun forState(voiceName: String?): PhoneVoice = byName(voiceName) ?: DEFAULT

    /** What the picker shows as chosen: a pick still on its way to the PC, else the PC's. */
    fun shown(voiceName: String?, pending: String?): PhoneVoice = byName(pending) ?: forState(voiceName)

    private fun hf(repo: String, commit: String, path: String) = "https://huggingface.co/$repo/resolve/$commit/$path"

    /**
     * The model folder as libptts_ffi loads it. q8 weights: the library loads q8_0 GGUF or f32 safetensors
     * only (q4k would save 9.5 MB but needs a patched library). Each voice is saved under its name, which is
     * the name the library lists it by.
     */
    val FILES: List<ModelFile> = listOf(
        ModelFile(hf("gradium/phonon-31m-en", MODEL_COMMIT, "config.json"), "config.json", 4_634,
            "b5d0f88353638370d94035b7d73d7d6577c0554d78f8d7ecfd40490aae39ae85"),
        ModelFile(hf("gradium/phonon-31m-en", MODEL_COMMIT, "tokenizer.json"), "tokenizer.json", 277_967,
            "691a00068fa7b989f2f3e5ae5daa2d1a5bb319f2d5360deafcfb19a8bb6e7936"),
        ModelFile(hf("gradium/phonon-31m-en", MODEL_COMMIT, "model.q8.gguf"), "model.q8.gguf", 64_512_352,
            "a7d9aad2cd6dc66473f24a08bc6d2939030ef4d751ef82349361fbef3272dbae"),
    ) + ALL.map {
        ModelFile(hf("gradium/phonon-voices", VOICES_COMMIT, "voices/${it.id}.safetensors"), "voices/${it.name}.safetensors",
            it.size, it.sha256)
    }
}

/** An HTTP GET of [url] from byte [offset] on. [fromOffset]: the body starts there (206); false, it starts at 0. */
class Fetched(val body: InputStream, val fromOffset: Boolean)

fun interface Fetch {
    fun open(url: String, offset: Long): Fetched
}

fun sha256(f: File): String {
    val md = MessageDigest.getInstance("SHA-256")
    f.inputStream().use { input ->
        val buf = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            md.update(buf, 0, n)
        }
    }
    return md.digest().joinToString("") { "%02x".format(it) }
}

/** Whether [f] is exactly the file [m] pins: its size, then its SHA-256. */
fun verified(f: File, m: ModelFile): Boolean = f.isFile && f.length() == m.size && sha256(f) == m.sha256

/**
 * Puts [files] in [dir] and checks every one, once. A file already there and right is kept; a download stops
 * part way into `<file>.part` and the next try asks for the rest (a server that ignores the range sends it
 * again from the start). Nothing counts until it matches its size and SHA-256, and anything in [dir] that
 * isn't listed (a voice swapped out) is removed. [ready] is cheap: a manifest written last names what was
 * checked, so the app doesn't hash 65 MB every start.
 */
class Installer(private val dir: File, private val files: List<ModelFile>, private val fetch: Fetch) {
    private val manifest = File(dir, "verified.txt")
    private fun expected() = files.joinToString("\n") { "${it.path} ${it.sha256}" }
    val total = files.sumOf { it.size }

    fun ready(): Boolean = try {
        manifest.readText() == expected() && files.all { File(dir, it.path).length() == it.size }
    } catch (_: IOException) {
        false
    }

    /** Blocks; run it off the main thread. [progress] gets bytes in place out of [total]. Throws on failure. */
    fun install(progress: (Long) -> Unit = {}, cancelled: () -> Boolean = { false }) {
        dir.mkdirs()
        manifest.delete()
        var done = 0L
        for (m in files) {
            val out = File(dir, m.path)
            out.parentFile?.mkdirs()
            if (verified(out, m)) {
                done += m.size
                progress(done)
                continue
            }
            out.delete()
            val part = File(out.path + ".part")
            if (part.length() > m.size) part.delete()
            if (part.length() < m.size) download(m, part) { progress(done + it) }
            if (cancelled()) throw IOException("cancelled")
            if (!verified(part, m)) {
                part.delete()
                throw IOException("${m.path} didn't match its checksum")
            }
            if (!part.renameTo(out)) throw IOException("couldn't move ${m.path} into place")
            done += m.size
            progress(done)
        }
        val keep = files.map { File(dir, it.path).canonicalPath }.toSet() + manifest.canonicalPath
        dir.walkBottomUp().forEach { f ->
            if (f == dir) return@forEach
            if (f.isFile && f.canonicalPath !in keep) f.delete()
            if (f.isDirectory && f.list().isNullOrEmpty()) f.delete()
        }
        manifest.writeText(expected())
    }

    private fun download(m: ModelFile, part: File, progress: (Long) -> Unit) {
        var have = part.length()
        val got = fetch.open(m.url, have)
        if (!got.fromOffset) have = 0
        got.body.use { input ->
            FileOutputStream(part, have > 0).use { output ->
                val buf = ByteArray(1 shl 16)
                var last = 0L
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    have += n
                    if (have > m.size) throw IOException("${m.path} is bigger than it should be")
                    output.write(buf, 0, n)
                    if (have - last >= 256 * 1024) {
                        last = have
                        progress(have)
                    }
                }
            }
        }
        progress(have)
    }
}
