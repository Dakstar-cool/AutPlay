package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits as Limits
import app.autplay.application.desktop.validDesktopEmbeddedText
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InterruptedIOException
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.channels.ClosedByInterruptException
import java.nio.channels.FileChannel
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.READ
import java.nio.file.attribute.BasicFileAttributes
import java.security.MessageDigest
import javax.imageio.IIOImage
import javax.imageio.ImageIO
import javax.imageio.stream.MemoryCacheImageInputStream
import javax.imageio.stream.MemoryCacheImageOutputStream

/** Bounded metadata inspection only. The host owns the managed capability, IO job and timeout.
 * No audio validation, full-file hash, source mutation or external picture lookup is performed.
 */
object DesktopEmbeddedMetadataReader {
    fun read(file: Path, expectedByteSize: Long): DesktopEmbeddedMetadata {
        var format = "UNKNOWN"
        try {
            active()
            val before = attributes(file, expectedByteSize)
            val result = FileChannel.open(file, READ, NOFOLLOW_LINKS).use { channel ->
                if (channel.size() != expectedByteSize) throw Unavailable()
                val input = Input(channel, expectedByteSize)
                val prefix = input.bytes(minOf(4L, expectedByteSize).toInt())
                val parsed = try { when {
                    prefix.size >= 3 && prefix.copyOfRange(0, 3).contentEquals(byteArrayOf(73, 68, 51)) -> {
                        if (prefix.size < 4) throw Invalid()
                        val version = prefix[3].toInt() and 255
                        format = when (version) { 3 -> "ID3V23"; 4 -> "ID3V24"; else -> "UNKNOWN" }
                        id3(input, version, format)
                    }
                    prefix.contentEquals(byteArrayOf(102, 76, 97, 67)) -> {
                        format = "FLAC"
                        flac(input)
                    }
                    // Recognize only a nonreserved MPEG Layer III header hint, excluding ADTS.
                    // This does not claim that its audio frames are playable or even complete.
                    untaggedMp3Header(prefix) -> DesktopEmbeddedMetadata("NO_TAGS")
                    else -> {
                        if (prefix.size == 4 && input.remaining >= 4 &&
                            input.bytes(4).contentEquals(byteArrayOf(102, 116, 121, 112))) {
                            format = DesktopMp4MetadataParser.FORMAT
                            DesktopMp4MetadataParser(
                                expectedByteSize, { input.position }, input::bytes, input::sparseSkipTo,
                                { encoded, imageFormat -> artwork(encoded, imageFormat) },
                            ).read(unsigned(prefix, false))
                        } else DesktopEmbeddedMetadata("UNSUPPORTED")
                    }
                } } catch (failure: Unsupported) {
                    DesktopEmbeddedMetadata("UNSUPPORTED", format)
                } catch (failure: Invalid) {
                    DesktopEmbeddedMetadata("INVALID", format)
                } catch (failure: DesktopMp4MetadataParser.Unsupported) {
                    DesktopEmbeddedMetadata("UNSUPPORTED", format)
                } catch (failure: DesktopMp4MetadataParser.Invalid) {
                    DesktopEmbeddedMetadata("INVALID", format)
                }
                active()
                val after = attributes(file, expectedByteSize)
                if (channel.size() != expectedByteSize || !sameFileSnapshot(before, after)) throw Unavailable()
                parsed
            }
            return result
        } catch (failure: InterruptedException) {
            Thread.currentThread().interrupt()
            throw failure
        } catch (failure: ClosedByInterruptException) {
            Thread.currentThread().interrupt()
            throw InterruptedException("desktop_metadata_cancelled")
        } catch (failure: InterruptedIOException) {
            Thread.currentThread().interrupt()
            throw InterruptedException("desktop_metadata_cancelled")
        } catch (failure: Exception) {
            active()
            return DesktopEmbeddedMetadata("UNAVAILABLE", format)
        }
    }

    private fun attributes(file: Path, size: Long): BasicFileAttributes {
        var ancestor: Path? = file.toAbsolutePath()
        while (ancestor != null) {
            if (Files.isSymbolicLink(ancestor)) throw Unavailable()
            ancestor = ancestor.parent
        }
        if (size <= 0 || Files.isSymbolicLink(file) || !Files.isReadable(file)) throw Unavailable()
        val attributes = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        if (!attributes.isRegularFile || attributes.size() != size) throw Unavailable()
        return attributes
    }

    /** Attribute guard only, not a hash or proof against writes that restore all attributes. */
    internal fun sameFileSnapshot(before: BasicFileAttributes, after: BasicFileAttributes): Boolean =
        before.isRegularFile && after.isRegularFile && before.size() == after.size() &&
            before.fileKey() == after.fileKey() && before.lastModifiedTime() == after.lastModifiedTime() &&
            before.creationTime() == after.creationTime()

    private fun untaggedMp3Header(prefix: ByteArray): Boolean {
        if (prefix.size < 4) return false
        val first = prefix[0].toInt() and 255
        val second = prefix[1].toInt() and 255
        val third = prefix[2].toInt() and 255
        val fourth = prefix[3].toInt() and 255
        return first == 255 && (second and 224) == 224 &&
            ((second ushr 3) and 3) != 1 && ((second ushr 1) and 3) == 1 &&
            ((third ushr 4) and 15) != 15 && ((third ushr 2) and 3) != 3 && (fourth and 3) != 2
    }

    private fun id3(input: Input, version: Int, format: String): DesktopEmbeddedMetadata {
        if (version != 3 && version != 4) throw Unsupported()
        val revision = input.byte()
        val flags = input.byte()
        if (revision == 255 || flags and (if (version == 3) 31 else 15) != 0) throw Invalid()
        val size = synchsafe(input.bytes(4))
        if (size + 10L > Limits.MAX_METADATA_BYTES || size > input.remaining) throw Invalid()
        if (revision != 0 || flags != 0) throw Unsupported()
        val end = input.position + size
        val values = Values(format)
        var frames = 0
        while (input.position < end) {
            active()
            val first = input.byte()
            if (first == 0) {
                while (input.position < end) {
                    if (input.bytes(minOf(8192L, end - input.position).toInt()).any { it != 0.toByte() }) throw Invalid()
                }
                break
            }
            if (++frames > Limits.MAX_PARTS || end - input.position < 9) throw Invalid()
            val nameBytes = byteArrayOf(first.toByte()) + input.bytes(3)
            if (nameBytes.any { it.toInt() !in 65..90 && it.toInt() !in 48..57 }) throw Invalid()
            val name = String(nameBytes, Charsets.US_ASCII)
            val length = if (version == 4) synchsafe(input.bytes(4)).toLong() else input.uint()
            val status = input.byte()
            val features = input.byte()
            if (status and (if (version == 3) 31 else 143) != 0 ||
                features and (if (version == 3) 31 else 176) != 0) throw Invalid()
            if (length <= 0 || length > end - input.position) throw Invalid()
            if (features != 0) throw Unsupported()
            val frameEnd = input.position + length
            when (name) {
                "TIT2", "TPE1", "TALB" -> {
                    val encoding = input.byte()
                    values.field(name, textFrame(input, frameEnd, encoding, version))
                }
                "APIC" -> optionalPicture { apic(input, frameEnd, version, values) }
            }
            input.skipTo(frameEnd)
        }
        if (frames == 0) throw Invalid()
        return values.result()
    }

    private fun apic(input: Input, end: Long, version: Int, values: Values): Unit {
        val encoding = input.byteBefore(end)
        checkEncoding(encoding, version)
        val mime = String(input.terminated(end, 1, 32), Charsets.ISO_8859_1)
        val type = input.byteBefore(end)
        val description = input.terminated(end, if (encoding == 1 || encoding == 2) 2 else 1, MAX_TEXT_BYTES)
        text(description, encoding, version, terminated = true)
        val length = end - input.position
        // Linked pictures and all other MIME types are deliberately never dereferenced.
        if (type != 3 || mime !in setOf("image/png", "image/jpeg", "png", "jpeg")) return
        if (length !in 1..Limits.MAX_IMAGE_BYTES.toLong()) return
        val encoded = input.bytes(length.toInt())
        if (values.artwork == null) values.artwork = artwork(encoded, if (mime.endsWith("png")) "png" else "jpeg")
    }

    private fun flac(input: Input): DesktopEmbeddedMetadata {
        val values = Values("FLAC")
        var blocks = 0
        var declared = 4L
        var comments = false
        while (true) {
            active()
            if (++blocks + values.extraParts > Limits.MAX_PARTS) throw Invalid()
            val header = input.byte()
            val type = header and 127
            val lengthBytes = input.bytes(3)
            val length = ((lengthBytes[0].toInt() and 255) shl 16) or
                ((lengthBytes[1].toInt() and 255) shl 8) or (lengthBytes[2].toInt() and 255)
            declared += 4L + length
            if (declared > Limits.MAX_METADATA_BYTES || length > input.remaining || type == 127) throw Invalid()
            if ((blocks == 1 && (type != 0 || length != 34)) || (blocks > 1 && type == 0)) throw Invalid()
            val end = input.position + length
            when (type) {
                4 -> {
                    if (comments) throw Invalid()
                    comments = true
                    vorbis(input, end, values, blocks)
                }
                6 -> optionalPicture { picture(input, end, values) }
                in 7..126 -> throw Unsupported()
            }
            input.skipTo(end)
            if (header and 128 != 0) return values.result()
        }
    }

    private fun vorbis(input: Input, end: Long, values: Values, blocks: Int): Unit {
        val vendorLength = input.uintBefore(end, little = true)
        if (vendorLength > MAX_TEXT_BYTES || vendorLength > end - input.position) throw Invalid()
        if (!validDesktopEmbeddedText(strict(input.bytes(vendorLength.toInt()), Charsets.UTF_8), true)) throw Invalid()
        val count = input.uintBefore(end, little = true)
        if (count > Limits.MAX_PARTS - blocks - values.extraParts) throw Invalid()
        repeat(count.toInt()) {
            active()
            val length = input.uintBefore(end, little = true)
            if (length > MAX_TEXT_BYTES + 64 || length > end - input.position) throw Invalid()
            val entry = strict(input.bytes(length.toInt()), Charsets.UTF_8)
            val separator = entry.indexOf('=')
            if (separator <= 0 || entry.substring(0, separator).any { it.code !in 32..126 || it == '=' }) throw Invalid()
            val name = entry.substring(0, separator).uppercase(java.util.Locale.ROOT)
            val value = entry.substring(separator + 1)
            if (!validDesktopEmbeddedText(value, true)) throw Invalid()
            when (name) {
                "TITLE" -> values.field("TIT2", value)
                "ARTIST" -> values.field("TPE1", value)
                "ALBUM" -> values.field("TALB", value)
            }
        }
        if (input.position != end) throw Invalid()
        values.extraParts += count.toInt()
    }

    private fun picture(input: Input, end: Long, values: Values): Unit {
        val type = input.uintBefore(end)
        val mimeLength = input.uintBefore(end)
        if (mimeLength > 32 || mimeLength > end - input.position) throw Invalid()
        val mime = strict(input.bytes(mimeLength.toInt()), Charsets.US_ASCII)
        val descriptionLength = input.uintBefore(end)
        if (descriptionLength > MAX_TEXT_BYTES || descriptionLength > end - input.position) throw Invalid()
        val description = strict(input.bytes(descriptionLength.toInt()), Charsets.UTF_8)
        if (!validDesktopEmbeddedText(description, true)) throw Invalid()
        val width = input.uintBefore(end)
        val height = input.uintBefore(end)
        input.uintBefore(end) // Color depth is informational; the image reader supplies actual dimensions.
        input.uintBefore(end) // Indexed color count.
        val length = input.uintBefore(end)
        if (length != end - input.position) throw Invalid()
        if (type != 3L || mime !in setOf("image/png", "image/jpeg")) return
        if (length !in 1..Limits.MAX_IMAGE_BYTES.toLong()) return
        if (!dimensions(width, height)) return
        val encoded = input.bytes(length.toInt())
        if (values.artwork == null) values.artwork = artwork(encoded, if (mime == "image/png") "png" else "jpeg", width, height)
    }

    private inline fun optionalPicture(read: () -> Unit): Unit {
        // The caller has validated the frame/block boundary and skips its remainder.
        // Invalid optional picture fields must not discard independent safe text.
        try { read() } catch (failure: Invalid) { } catch (failure: Unsupported) { }
    }

    private fun textFrame(input: Input, end: Long, encoding: Int, version: Int): String {
        checkEncoding(encoding, version)
        val length = end - input.position
        if (version != 3) {
            if (length > MAX_TEXT_BYTES) throw Invalid()
            return text(input.bytes(length.toInt()), encoding, version, terminated = false)
        }
        // ID3v2.3 section 4.2 ignores everything after the first aligned terminator.
        // Read only a bounded prefix; the frame owner skips any ignored tail.
        val unit = if (encoding == 1) 2 else 1
        val bytes = input.bytes(minOf(length, MAX_TEXT_BYTES + unit.toLong()).toInt())
        val terminator = (0..(bytes.size - unit) step unit).firstOrNull { index ->
            bytes[index] == 0.toByte() && (unit == 1 || bytes[index + 1] == 0.toByte())
        }
        if (terminator == null && length > MAX_TEXT_BYTES) throw Invalid()
        val value = if (terminator == null) bytes else bytes.copyOf(terminator)
        return text(value, encoding, version, terminated = false)
    }

    private fun text(bytes: ByteArray, encoding: Int, version: Int, terminated: Boolean): String {
        checkEncoding(encoding, version)
        val charset = when (encoding) {
            0 -> Charsets.ISO_8859_1
            1 -> {
                if (bytes.size < 2 || !((bytes[0].toInt() == -2 && bytes[1].toInt() == -1) ||
                    (bytes[0].toInt() == -1 && bytes[1].toInt() == -2))) {
                    // The empty UTF-16 picture description may consist of only its terminator.
                    if (terminated && bytes.isEmpty()) return ""
                    throw Invalid()
                }
                Charsets.UTF_16
            }
            2 -> Charsets.UTF_16BE
            else -> Charsets.UTF_8
        }
        var value = strict(bytes, charset)
        if (!terminated && value.endsWith('\u0000')) value = value.dropLast(1)
        if (!validDesktopEmbeddedText(value, true)) throw Invalid()
        return value
    }

    private fun checkEncoding(encoding: Int, version: Int): Unit {
        if (encoding !in 0..3) throw Invalid()
        if (version == 3 && encoding > 1) throw Unsupported()
    }

    private fun strict(bytes: ByteArray, charset: Charset): String = try {
        charset.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
    } catch (failure: java.nio.charset.CharacterCodingException) { throw Invalid() }

    private fun artwork(encoded: ByteArray, format: String, declaredWidth: Long? = null, declaredHeight: Long? = null): DesktopArtwork? {
        active()
        // Select a whitelisted JDK provider explicitly; never consult arbitrary registered codecs.
        val readers = ImageIO.getImageReadersByFormatName(format)
        var reader: javax.imageio.ImageReader? = null
        while (readers.hasNext()) {
            val candidate = readers.next()
            if (candidate.javaClass.name == (if (format == "png") "com.sun.imageio.plugins.png.PNGImageReader"
                    else "com.sun.imageio.plugins.jpeg.JPEGImageReader")) {
                reader = candidate
                break
            }
            candidate.dispose()
        }
        val selected = reader ?: return null
        var decoded: BufferedImage? = null
        var canonical: BufferedImage? = null
        try {
            MemoryCacheImageInputStream(InterruptibleImageInput(encoded)).use { stream ->
                selected.setInput(stream, true, true)
                val width = selected.getWidth(0)
                val height = selected.getHeight(0)
                if (!dimensions(width.toLong(), height.toLong())) return null
                if ((declaredWidth != null && declaredWidth != width.toLong()) ||
                    (declaredHeight != null && declaredHeight != height.toLong())) return null
                active()
                val image = selected.read(0) ?: return null
                decoded = image
                if (image.width != width || image.height != height) return null
                val scale = minOf(1.0, Limits.MAX_THUMBNAIL_DIMENSION.toDouble() / maxOf(width, height))
                val target = BufferedImage(maxOf(1, (width * scale).toInt()), maxOf(1, (height * scale).toInt()), BufferedImage.TYPE_INT_ARGB)
                canonical = target
                val graphics = target.createGraphics()
                try {
                    graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BILINEAR)
                    graphics.drawImage(image, 0, 0, target.width, target.height, null)
                } finally { graphics.dispose() }
                active()
                val output = BoundedOutput()
                val writers = ImageIO.getImageWritersByFormatName("png")
                var writer: javax.imageio.ImageWriter? = null
                while (writers.hasNext()) {
                    val candidate = writers.next()
                    if (candidate.javaClass.name == "com.sun.imageio.plugins.png.PNGImageWriter") { writer = candidate; break }
                    candidate.dispose()
                }
                val encoder = writer ?: return null
                try {
                    BoundedImageOutput(output).use { destination ->
                        encoder.output = destination
                        encoder.write(null, IIOImage(target, null, null), encoder.defaultWriteParam)
                        destination.flush()
                    }
                } finally { encoder.dispose() }
                active()
                val png = output.bytes.toByteArray()
                val hash = MessageDigest.getInstance("SHA-256").digest(png).joinToString("") { "%02x".format(it.toInt() and 255) }
                return DesktopArtwork(hash, png, target.width, target.height)
            }
        } catch (failure: Exception) {
            if (failure is InterruptedException || failure is InterruptedIOException || failure is ClosedByInterruptException) {
                Thread.currentThread().interrupt()
                throw InterruptedException("desktop_metadata_cancelled")
            }
            active()
            return null // Unsupported/corrupt artwork does not discard already safe text.
        } finally {
            canonical?.flush()
            decoded?.flush()
            selected.dispose()
        }
    }

    private fun dimensions(width: Long, height: Long): Boolean = width in 1..Limits.MAX_IMAGE_DIMENSION.toLong() &&
        height in 1..Limits.MAX_IMAGE_DIMENSION.toLong() && width * height <= Limits.MAX_IMAGE_PIXELS

    private class InterruptibleImageInput(bytes: ByteArray) : ByteArrayInputStream(bytes) {
        override fun read(): Int { active(); return super.read() }
        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            active()
            return super.read(buffer, offset, minOf(length, 8192))
        }
    }

    private class BoundedOutput : OutputStream() {
        val bytes: ByteArrayOutputStream = ByteArrayOutputStream()
        override fun write(value: Int): Unit {
            active()
            if (bytes.size() >= Limits.MAX_THUMBNAIL_BYTES) throw Invalid()
            bytes.write(value)
        }
        override fun write(buffer: ByteArray, offset: Int, length: Int): Unit {
            active()
            if (length > Limits.MAX_THUMBNAIL_BYTES - bytes.size()) throw Invalid()
            bytes.write(buffer, offset, length)
        }
    }

    private class BoundedImageOutput(output: OutputStream) : MemoryCacheImageOutputStream(output) {
        override fun write(value: Int): Unit {
            active()
            if (streamPosition >= Limits.MAX_THUMBNAIL_BYTES) throw Invalid()
            super.write(value)
        }
        override fun write(buffer: ByteArray, offset: Int, length: Int): Unit {
            active()
            if (length > Limits.MAX_THUMBNAIL_BYTES - streamPosition) throw Invalid()
            super.write(buffer, offset, length)
        }
    }

    private class Values(val format: String) {
        var title: String? = null
        var artist: String? = null
        var album: String? = null
        var artwork: DesktopArtwork? = null
        var extraParts: Int = 0
        fun field(name: String, value: String): Unit {
            if (value.isEmpty()) return
            when (name) {
                "TIT2" -> if (title == null) title = value
                "TPE1" -> if (artist == null) artist = value
                "TALB" -> if (album == null) album = value
            }
        }
        fun result(): DesktopEmbeddedMetadata = DesktopEmbeddedMetadata(
            if (title == null && artist == null && album == null && artwork == null) "NO_TAGS" else "AVAILABLE",
            format, title, artist, album, artwork,
        )
    }

    private class Input(val channel: FileChannel, val size: Long) {
        var position: Long = 0
        val remaining: Long get() = size - position
        private var readBytes: Long = 0
        fun bytes(count: Int): ByteArray {
            if (count < 0 || count > remaining || readBytes + count > Limits.MAX_METADATA_BYTES) throw Invalid()
            val bytes = ByteArray(count)
            val buffer = ByteBuffer.wrap(bytes)
            var stalls = 0
            while (buffer.hasRemaining()) {
                active()
                buffer.limit(minOf(buffer.capacity(), buffer.position() + 8192))
                val read = channel.read(buffer, position)
                if (read < 0) throw Invalid()
                if (read == 0) { if (++stalls > 3) throw Unavailable() } else stalls = 0
                position += maxOf(0, read)
                buffer.limit(buffer.capacity())
            }
            readBytes += count
            return bytes
        }
        fun byte(): Int = bytes(1)[0].toInt() and 255
        fun byteBefore(end: Long): Int { if (position >= end) throw Invalid(); return byte() }
        fun uint(): Long = unsigned(bytes(4), false)
        fun uintBefore(end: Long, little: Boolean = false): Long {
            if (end - position < 4) throw Invalid()
            return unsigned(bytes(4), little)
        }
        fun skipTo(end: Long): Unit {
            if (end < position || end > size) throw Invalid()
            // Skip unknown payloads without allocation or reading audio; still poll interruption.
            while (position < end) { active(); position += minOf(8192L, end - position) }
        }
        fun sparseSkipTo(end: Long): Unit {
            active()
            if (end < position || end > size) throw Invalid()
            // MP4 media/free boxes can span gigabytes. One validated offset per bounded atom
            // avoids both payload IO and work proportional to skipped audio/sparse padding.
            position = end
        }
        fun terminated(end: Long, unit: Int, maximum: Int): ByteArray {
            val output = ByteArrayOutputStream()
            while (true) {
                if (end - position < unit) throw Invalid()
                val first = byte()
                val second = if (unit == 2) byte() else 0
                if (first == 0 && second == 0) return output.toByteArray()
                if (output.size() + unit > maximum) throw Invalid()
                output.write(first)
                if (unit == 2) output.write(second)
            }
        }
    }

    private fun unsigned(bytes: ByteArray, little: Boolean): Long {
        var result = 0L
        for (index in if (little) bytes.indices.reversed() else bytes.indices) result = (result shl 8) or (bytes[index].toLong() and 255)
        return result
    }
    private fun synchsafe(bytes: ByteArray): Int {
        var result = 0
        for (byte in bytes) { if (byte < 0) throw Invalid(); result = (result shl 7) or byte.toInt() }
        return result
    }
    private fun active(): Unit { if (Thread.currentThread().isInterrupted) throw InterruptedException("desktop_metadata_cancelled") }
    private class Invalid : Exception()
    private class Unsupported : Exception()
    private class Unavailable : Exception()
    private const val MAX_TEXT_BYTES: Int = Limits.MAX_TEXT_UNITS * 4 + 4
}
