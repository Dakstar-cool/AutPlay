package app.autplay.data.desktop

import java.io.IOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.READ
import java.nio.file.attribute.BasicFileAttributes
import kotlinx.coroutines.CancellationException

/** Informational PCM duration for explicit imports; never a decoding or playback authority. */
object DesktopPcmWaveDurationProbe {
    private const val MAX_READ_BYTES: Int = 256 * 1024
    private const val MAX_CHUNKS: Int = 1_024
    private const val MAX_DURATION_SECONDS: Long = 7L * 24 * 60 * 60

    /**
     * Synchronous IO on a private, SHA-verified temp copy. The caller supplies the snapshot from
     * verification and owns containment, runInterruptible(IO) and the existing batch timeout.
     * Ordinary optional-metadata failures return null; cancellation propagates.
     */
    fun durationMs(file: Path, verifiedSnapshot: BasicFileAttributes): Long? {
        checkInterrupted()
        return try {
            val before = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
            if (!sameSnapshot(verifiedSnapshot, before) || before.size() < 12) return null
            FileChannel.open(file, READ, NOFOLLOW_LINKS).use { channel ->
                if (channel.size() != before.size()) return null
                val duration = WaveReader(channel, before.size()).durationMs()
                checkInterrupted()
                val after = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (!sameSnapshot(before, after) || channel.size() != before.size()) return null
                duration
            }
        } catch (_: IOException) {
            checkInterrupted()
            null
        } catch (_: SecurityException) {
            checkInterrupted()
            null
        } catch (_: UnsupportedOperationException) {
            checkInterrupted()
            null
        }
    }

    private fun sameSnapshot(before: BasicFileAttributes, after: BasicFileAttributes): Boolean =
        before.isRegularFile && !before.isSymbolicLink && after.isRegularFile && !after.isSymbolicLink &&
            before.size() == after.size() && before.fileKey() == after.fileKey() &&
            before.lastModifiedTime() == after.lastModifiedTime() && before.creationTime() == after.creationTime()

    private fun checkInterrupted(): Unit {
        if (Thread.currentThread().isInterrupted) throw CancellationException("desktop_probe_cancelled")
    }

    private data class PcmFormat(val sampleRate: Long, val blockAlign: Int)

    private class WaveReader(private val channel: FileChannel, private val fileSize: Long) {
        private var readBytes: Int = 0

        // Reads only container/metadata bytes and padding, never the data payload. Long positions
        // allow visiting trailing chunks without counting audio bytes against the read budget.
        private fun read(position: Long, count: Int): ByteArray? {
            checkInterrupted()
            if (position < 0 || count < 0 || position > fileSize || count.toLong() > fileSize - position ||
                count > MAX_READ_BYTES - readBytes) return null
            val bytes = ByteArray(count)
            val buffer = ByteBuffer.wrap(bytes)
            while (buffer.hasRemaining()) {
                checkInterrupted()
                val received = channel.read(buffer, position + buffer.position())
                if (received <= 0) return null
                readBytes += received
            }
            return bytes
        }

        fun durationMs(): Long? {
            val header = read(0, 12) ?: return null
            // RIFX/RF64 remain legacy suffix-recognition cases, outside this duration subset.
            if (!matches(header, 0, "RIFF") || !matches(header, 8, "WAVE")) return null
            val end = u32(header, 4) + 8
            if (end != fileSize || end < 12) return null
            var offset = 12L
            var chunks = 0
            var format: PcmFormat? = null
            var dataBytes: Long? = null
            while (offset < end) {
                checkInterrupted()
                if (chunks >= MAX_CHUNKS || end - offset < 8) return null
                chunks++
                val chunk = read(offset, 8) ?: return null
                val length = u32(chunk, 4)
                val payload = offset + 8
                if (length > end - payload) return null
                val payloadEnd = payload + length
                val padding = (length and 1L).toInt()
                if (padding.toLong() > end - payloadEnd) return null
                when {
                    matches(chunk, 0, "data") -> {
                        val pcm = format ?: return null
                        if (dataBytes != null || length == 0L || length % pcm.blockAlign != 0L) return null
                        dataBytes = length
                    }
                    matches(chunk, 0, "fmt ") -> {
                        if (format != null || (length != 16L && length != 18L)) return null
                        val fields = read(payload, length.toInt()) ?: return null
                        format = pcmFormat(fields) ?: return null
                    }
                    // No nested wave lists, silence runs, playlists or alternate sample-count
                    // authority. These require interpretation beyond one contiguous PCM stream.
                    matches(chunk, 0, "LIST") || matches(chunk, 0, "RIFF") ||
                        matches(chunk, 0, "slnt") || matches(chunk, 0, "plst") ||
                        matches(chunk, 0, "fact") -> return null
                    else -> {
                        if (length > (MAX_READ_BYTES - readBytes).toLong()) return null
                        if (read(payload, length.toInt()) == null) return null
                    }
                }
                if (padding != 0 && read(payloadEnd, 1)?.get(0)?.toInt() != 0) return null
                offset = payloadEnd + padding
            }
            val pcm = format ?: return null
            val frames = (dataBytes ?: return null) / pcm.blockAlign
            if (frames > MAX_DURATION_SECONDS * pcm.sampleRate) return null
            // Floor exact frame time to whole milliseconds. Divide first so arithmetic stays
            // bounded even at unsigned 32-bit sample rates. Sub-millisecond clips remain unknown.
            val duration = (frames / pcm.sampleRate) * 1_000 + (frames % pcm.sampleRate) * 1_000 / pcm.sampleRate
            return duration.takeIf { it > 0 }
        }

        private fun pcmFormat(fields: ByteArray): PcmFormat? {
            if (u16(fields, 0) != 1 || (fields.size == 18 && u16(fields, 16) != 0)) return null
            val channels = u16(fields, 2)
            val sampleRate = u32(fields, 4)
            val byteRate = u32(fields, 8)
            val blockAlign = u16(fields, 12)
            val bits = u16(fields, 14)
            if (channels !in 1..2 || sampleRate == 0L || (bits != 8 && bits != 16)) return null
            if (blockAlign != channels * (bits / 8) || byteRate != sampleRate * blockAlign) return null
            return PcmFormat(sampleRate, blockAlign)
        }

        private fun matches(bytes: ByteArray, offset: Int, value: String): Boolean =
            value.indices.all { u8(bytes, offset + it) == value[it].code }
        private fun u8(bytes: ByteArray, offset: Int): Int = bytes[offset].toInt() and 255
        private fun u16(bytes: ByteArray, offset: Int): Int = u8(bytes, offset) or (u8(bytes, offset + 1) shl 8)
        private fun u32(bytes: ByteArray, offset: Int): Long {
            var value = 0L
            for (index in 3 downTo 0) value = (value shl 8) or u8(bytes, offset + index).toLong()
            return value
        }
    }
}
