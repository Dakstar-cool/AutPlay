package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits as Limits
import app.autplay.application.desktop.validDesktopEmbeddedText
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Classic iTunes item-list evidence only; no sample decoding, byte identity or DRM handling.
 * Apple QTFF: /documentation/quicktime-file-format/{atoms,value_atom,type_indicator,well-known_types}.
 * The caller supplies its existing byte budget, interruption checks and file snapshot guard.
 * Unknown payloads are skipped by bounded offsets, never searched for apparent atom signatures.
 */
internal class DesktopMp4MetadataParser(
    private val size: Long,
    private val position: () -> Long,
    private val bytes: (Int) -> ByteArray,
    private val skipTo: (Long) -> Unit,
    private val artwork: (ByteArray, String) -> DesktopArtwork?,
) {
    internal class Invalid : Exception()
    internal class Unsupported : Exception()
    private data class Box(val type: String, val end: Long)
    private var parts: Int = 0
    private var title: String? = null
    private var artist: String? = null
    private var album: String? = null
    private var cover: DesktopArtwork? = null
    private val fields: MutableSet<String> = mutableSetOf()

    /** First size/type have already been read, preserving the caller's format detection. */
    fun read(firstSize: Long): DesktopEmbeddedMetadata {
        val first = box(size, 0, firstSize, "ftyp")
        fileType(first)
        skipTo(first.end)
        var movie = false
        while (position() < size) {
            val child = box(size, 0)
            when (child.type) {
                "moov" -> {
                    if (movie) throw Invalid()
                    movie = true
                    container(child.end, "moov", 1)
                }
                "free", "skip", "wide", "mdat" -> Unit
                "ftyp" -> throw Invalid()
                else -> throw Unsupported()
            }
            skipTo(child.end)
        }
        if (!movie) throw Invalid()
        return DesktopEmbeddedMetadata(
            if (title == null && artist == null && album == null && cover == null) "NO_TAGS" else "AVAILABLE",
            FORMAT, title, artist, album, cover,
        )
    }

    private fun fileType(box: Box): Unit {
        val length = box.end - position()
        if (length < 8 || length % 4 != 0L || length > Limits.MAX_METADATA_BYTES) throw Invalid()
        val major = code()
        uint() // Minor brand version is not a metadata layout version.
        val brands = setOf("M4A ", "mp41", "mp42", "isom", "iso2")
        if (major !in brands) throw Unsupported()
        while (position() < box.end) {
            active()
            if (++parts > Limits.MAX_PARTS) throw Invalid()
            if (code() !in brands) throw Unsupported()
        }
    }

    /** Only known structural paths are descended. This fixes recursion depth independently of input. */
    private fun container(end: Long, parent: String, depth: Int): Unit {
        if (depth > MAX_DEPTH) throw Invalid()
        val seen = mutableSetOf<String>()
        while (position() < end) {
            val child = box(end, depth)
            // Fragmentation, protection, compressed/remote movies and alternate metadata scopes
            // are outside this reader. In particular, never follow data references or decompress.
            if (child.type in UNSUPPORTED_BOXES) throw Unsupported()
            when {
                parent == "moov" && child.type == "trak" -> container(child.end, "trak", depth + 1)
                parent == "moov" && child.type == "udta" -> {
                    unique(seen, child.type)
                    container(child.end, "udta", depth + 1)
                }
                parent == "udta" && child.type == "meta" -> {
                    unique(seen, child.type)
                    metadata(child.end, depth + 1)
                }
                parent == "udta" && child.type in setOf("\u00a9nam", "\u00a9ART", "\u00a9alb", "covr") ->
                    throw Unsupported() // Legacy user-data text has a different language/length layout.
                (parent == "trak" && child.type == "mdia") ||
                    (parent == "mdia" && child.type == "minf") ||
                    (parent == "minf" && child.type == "stbl") -> {
                    unique(seen, child.type)
                    container(child.end, child.type, depth + 1)
                }
                parent == "stbl" && child.type == "stsd" -> {
                    unique(seen, child.type)
                    sampleDescriptions(child.end, depth + 1)
                }
                child.type in STRUCTURAL_BOXES -> throw Unsupported()
                else -> Unit // Opaque leaf (including sample tables); never read its payload.
            }
            skipTo(child.end)
        }
    }

    private fun sampleDescriptions(end: Long, depth: Int): Unit {
        if (end - position() < 8) throw Invalid()
        if (uint() != 0L) throw Unsupported()
        val count = uint()
        if (count > Limits.MAX_PARTS - parts) throw Invalid()
        repeat(count.toInt()) {
            val entry = box(end, depth)
            if (entry.type !in setOf("mp4a", "alac")) throw Unsupported()
            // Version-0 sound sample entry: reserved(6), data-reference(2), version(2),
            // revision/vendor(6), channels/sample-size/compression/packet-size/rate(12).
            if (entry.end - position() < 28) throw Invalid()
            val header = bytes(28)
            if (header.take(6).any { it != 0.toByte() }) throw Invalid()
            if (header[8] != 0.toByte() || header[9] != 0.toByte()) throw Unsupported()
            while (position() < entry.end) {
                val extension = box(entry.end, depth + 1)
                if (extension.type !in setOf("esds", "alac", "btrt", "free")) throw Unsupported()
                skipTo(extension.end)
            }
        }
        if (position() != end) throw Invalid()
    }

    private fun metadata(end: Long, depth: Int): Unit {
        if (end - position() < 4) throw Invalid()
        // ISO-style FullBox only. Headerless QuickTime meta and mdta/keys are unsupported.
        if (uint() != 0L) throw Unsupported()
        val seen = mutableSetOf<String>()
        while (position() < end) {
            val child = box(end, depth)
            when (child.type) {
                "hdlr" -> {
                    unique(seen, "hdlr")
                    if (child.end - position() < 24) throw Invalid()
                    if (uint() != 0L || uint() != 0L || code() != "mdir") throw Unsupported()
                }
                "ilst" -> {
                    unique(seen, "ilst")
                    if ("hdlr" !in seen) throw Invalid()
                    if (child.end - position() > Limits.MAX_METADATA_BYTES) throw Invalid()
                    items(child.end, depth + 1)
                }
                "free" -> Unit
                else -> throw Unsupported()
            }
            skipTo(child.end)
        }
        if ("hdlr" !in seen || "ilst" !in seen) throw Invalid()
    }

    private fun items(end: Long, depth: Int): Unit {
        while (position() < end) {
            val item = box(end, depth)
            if (item.type in STRUCTURAL_BOXES || item.type in UNSUPPORTED_BOXES) throw Unsupported()
            val recognized = item.type in setOf("\u00a9nam", "\u00a9ART", "\u00a9alb", "covr")
            if (recognized && !fields.add(item.type)) throw Invalid()
            var data = false
            while (position() < item.end) {
                val value = box(item.end, depth + 1)
                if (value.type != "data") throw Unsupported()
                if (data) throw Invalid() // No locale/type/cover preference is guessed.
                data = true
                if (value.end - position() < 8) throw Invalid()
                val type = uint()
                val locale = uint()
                if (recognized) {
                    if (locale != 0L) throw Unsupported()
                    val length = value.end - position()
                    if (item.type == "covr") {
                        if (type != 13L && type != 14L) throw Unsupported()
                        if (length in 1..Limits.MAX_IMAGE_BYTES.toLong()) {
                            cover = artwork(bytes(length.toInt()), if (type == 14L) "png" else "jpeg")
                        }
                    } else {
                        if (type != 1L && type != 2L) throw Unsupported()
                        if (length > MAX_TEXT_BYTES) throw Invalid()
                        val text = text(bytes(length.toInt()), type).takeIf { it.isNotEmpty() }
                        when (item.type) {
                            "\u00a9nam" -> title = text
                            "\u00a9ART" -> artist = text
                            "\u00a9alb" -> album = text
                        }
                    }
                }
                skipTo(value.end)
            }
            if (!data) throw Invalid()
        }
    }

    private fun text(encoded: ByteArray, type: Long): String {
        val charset = if (type == 1L) Charsets.UTF_8 else Charsets.UTF_16BE
        val decoded = try {
            charset.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(encoded)).toString()
        } catch (failure: java.nio.charset.CharacterCodingException) { throw Invalid() }
        if (!validDesktopEmbeddedText(decoded, allowBlank = true)) throw Invalid()
        return decoded
    }

    private fun box(end: Long, depth: Int, firstSize: Long? = null, firstType: String? = null): Box {
        active()
        if (++parts > Limits.MAX_PARTS || depth > MAX_DEPTH) throw Invalid()
        val start = if (firstSize == null) position() else position() - 8
        if (end - start < 8) throw Invalid()
        val shortSize = firstSize ?: uint()
        val type = firstType ?: code()
        val header = if (shortSize == 1L) 16 else 8
        val length = when (shortSize) {
            0L -> { if (depth != 0) throw Invalid(); end - start }
            1L -> {
                if (end - position() < 8) throw Invalid()
                val high = uint()
                val low = uint()
                if (high > 0x7fffffffL) throw Invalid()
                (high shl 32) or low
            }
            else -> shortSize
        }
        if (length < header || length > end - start) throw Invalid()
        return Box(type, start + length)
    }

    private fun uint(): Long = bytes(4).fold(0L) { value, byte -> (value shl 8) or (byte.toLong() and 255) }
    private fun code(): String = String(bytes(4), Charsets.ISO_8859_1)
    private fun unique(seen: MutableSet<String>, value: String): Unit { if (!seen.add(value)) throw Invalid() }
    private fun active(): Unit {
        if (Thread.currentThread().isInterrupted) throw InterruptedException("desktop_metadata_cancelled")
    }

    companion object {
        const val FORMAT: String = "MP4"
        private const val MAX_DEPTH: Int = 8
        private const val MAX_TEXT_BYTES: Int = Limits.MAX_TEXT_UNITS * 4 + 4
        private val STRUCTURAL_BOXES: Set<String> = setOf("ftyp", "moov", "trak", "mdia", "minf", "stbl", "stsd", "udta", "meta", "ilst")
        private val UNSUPPORTED_BOXES: Set<String> = setOf(
            "moof", "mvex", "mfra", "sidx", "ssix", "pssh", "sinf", "schm", "schi", "tenc", "senc",
            "enca", "encv", "drms", "drmi", "cmov", "rmra", "uuid",
        )
    }
}
