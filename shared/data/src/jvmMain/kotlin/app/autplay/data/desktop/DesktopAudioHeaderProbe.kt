package app.autplay.data.desktop

import java.io.IOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.READ
import java.nio.file.attribute.BasicFileAttributes

/**
 * WAV-only suffix recovery for legacy copies. Reads at most 256 KiB and 1,024 RIFF chunks.
 * Strong fmt/data evidence identifies the container, not a supported codec or decoded stream.
 * Other formats/oversized headers require explicit reimport with a recognized source suffix.
 * References: Microsoft RIFF services and FFmpeg's RF64/WAV parser (libavformat/wavdec.c).
 */
internal object DesktopAudioHeaderProbe {
    private const val MAX_HEADER_BYTES: Int = 256 * 1024
    private const val MAX_CHUNKS: Int = 1_024

    /** Synchronous IO: caller owns runInterruptible(IO), timeout, containment and SHA verification. */
    fun detectExtension(file: Path): String? {
        checkInterrupted()
        return try {
            val before = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
            if (!before.isRegularFile || before.isSymbolicLink || !Files.isReadable(file) || before.size() < 12) return null
            FileChannel.open(file, READ, NOFOLLOW_LINKS).use { channel ->
                if (channel.size() != before.size()) return null
                val bytes = ByteArray(minOf(before.size(), MAX_HEADER_BYTES.toLong()).toInt())
                var offset = 0
                while (offset < bytes.size) {
                    checkInterrupted()
                    val count = channel.read(ByteBuffer.wrap(bytes, offset, minOf(16 * 1024, bytes.size - offset)))
                    if (count <= 0) return null
                    offset += count
                }
                checkInterrupted()
                val after = Files.readAttributes(file, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (!after.isRegularFile || after.isSymbolicLink || after.size() != before.size() ||
                    channel.size() != before.size() || after.fileKey() != before.fileKey() ||
                    after.lastModifiedTime() != before.lastModifiedTime()) return null
                if (WaveHeader(bytes, before.size()).isWave()) "wav" else null
            }
        } catch (_: IOException) {
            checkInterrupted()
            null
        } catch (_: SecurityException) {
            null
        } catch (_: UnsupportedOperationException) {
            null
        }
    }

    private fun checkInterrupted(): Unit {
        if (Thread.currentThread().isInterrupted) throw InterruptedException("desktop_probe_cancelled")
    }

    private class WaveHeader(private val bytes: ByteArray, private val fileSize: Long) {
        private fun has(offset: Int, count: Int): Boolean = offset >= 0 && count >= 0 && offset <= bytes.size - count
        private fun u8(offset: Int): Int = bytes[offset].toInt() and 255
        private fun matches(offset: Int, value: String): Boolean =
            has(offset, value.length) && value.indices.all { u8(offset + it) == value[it].code }
        private fun u16(offset: Int, little: Boolean): Int =
            if (little) u8(offset) or (u8(offset + 1) shl 8) else (u8(offset) shl 8) or u8(offset + 1)
        private fun u32(offset: Int, little: Boolean): Long {
            var value = 0L
            for (index in 0..3) value = (value shl 8) or u8(offset + if (little) 3 - index else index).toLong()
            return value
        }
        private fun size64(offset: Int): Long? {
            if (!has(offset, 8) || u8(offset + 7) >= 128) return null
            var value = 0L
            for (index in 7 downTo 0) value = (value shl 8) or u8(offset + index).toLong()
            return value
        }
        private fun withinFile(start: Long, length: Long, end: Long): Boolean =
            start >= 0 && length >= 0 && start <= end && length <= end - start

        fun isWave(): Boolean {
            val rf64 = matches(0, "RF64")
            val little = !matches(0, "RIFX")
            if ((!rf64 && !matches(0, "RIFF") && !matches(0, "RIFX")) || !matches(8, "WAVE")) return false
            var end = u32(4, little) + 8
            var data64: Long? = null
            if (rf64) {
                if (u32(4, true) != 0xffffffffL || !matches(12, "ds64") || !has(20, 28)) return false
                val chunkSize = u32(16, true)
                if (chunkSize < 28 || !withinFile(20, chunkSize, fileSize) || u32(44, true) > (chunkSize - 28) / 12) return false
                val riffSize = size64(20) ?: return false
                if (riffSize > fileSize - 8) return false
                end = riffSize + 8
                data64 = size64(28) ?: return false
            }
            if (end < 12 || end > fileSize) return false
            var offset = 12
            var format = false
            repeat(MAX_CHUNKS) {
                checkInterrupted()
                if (!has(offset, 8) || !withinFile(offset.toLong(), 8, end)) return false
                val payload = offset + 8
                var length = u32(offset + 4, little)
                if (length == 0xffffffffL) {
                    if (!rf64 || !matches(offset, "data")) return false
                    length = data64 ?: return false
                }
                if (!withinFile(payload.toLong(), length, end)) return false
                if (matches(offset, "fmt ")) {
                    if (length < 16 || !has(payload, 16) || u16(payload, little) == 0 ||
                        u16(payload + 2, little) == 0 || u32(payload + 4, little) == 0L || u16(payload + 12, little) == 0) return false
                    format = true
                }
                if (matches(offset, "data")) return format && length > 0
                val next = payload.toLong() + length + (length and 1)
                if (next > bytes.size || next > end) return false
                offset = next.toInt()
            }
            return false
        }
    }
}
