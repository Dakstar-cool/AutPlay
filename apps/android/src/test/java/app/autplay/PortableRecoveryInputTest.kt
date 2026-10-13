package app.autplay

import app.autplay.application.portableaccount.PortableRecoveryFile
import java.io.InputStream
import java.io.IOException
import org.junit.Assert.*
import org.junit.Test

class PortableRecoveryInputTest {
    @Test fun shortReadsRespectTheExactLimitAndOversizeSentinelWithoutRetainingSecretBuffers() {
        val accepted = ChunkedInput(PortableRecoveryFile.MAX_BYTES)
        val bytes = readBoundedPortableRecoveryBytes(accepted)
        try {
            assertEquals(PortableRecoveryFile.MAX_BYTES, bytes.size)
            assertTrue(bytes.all { it == 42.toByte() })
            assertTrue(requireNotNull(accepted.destination).all { it == 0.toByte() })
        } finally { bytes.fill(0) }
        val rejected = ChunkedInput(PortableRecoveryFile.MAX_BYTES + 100)
        try {
            readBoundedPortableRecoveryBytes(rejected)
            fail("Oversized recovery input must be rejected")
        } catch (_: IllegalArgumentException) {
            assertEquals(PortableRecoveryFile.MAX_BYTES + 1, rejected.consumed)
            assertTrue(requireNotNull(rejected.destination).all { it == 0.toByte() })
        }
        val zeroReads = object : InputStream() {
            var position = 0
            override fun read(bytes: ByteArray, offset: Int, length: Int): Int = 0
            override fun read(): Int = if (position++ < 2) 42 else -1
        }
        val progressed = readBoundedPortableRecoveryBytes(zeroReads)
        try { assertArrayEquals(byteArrayOf(42, 42), progressed) } finally { progressed.fill(0) }
        var closedBuffer: ByteArray? = null
        val closed = object : InputStream() {
            override fun read(): Int = throw IOException("Fixture provider closed")
            override fun read(bytes: ByteArray, offset: Int, length: Int): Int {
                closedBuffer = bytes
                bytes[offset] = 42
                throw IOException("Fixture provider closed")
            }
        }
        try { readBoundedPortableRecoveryBytes(closed); fail("Closed provider must fail") }
        catch (_: IOException) { assertTrue(requireNotNull(closedBuffer).all { it == 0.toByte() }) }
    }

    private class ChunkedInput(private val size: Int) : InputStream() {
        var consumed = 0
        var destination: ByteArray? = null
        override fun read(): Int = error("Bulk read required")
        override fun read(bytes: ByteArray, offset: Int, length: Int): Int {
            destination = bytes
            if (consumed == size) return -1
            val count = minOf(113, length, size - consumed)
            bytes.fill(42, offset, offset + count)
            consumed += count
            return count
        }
    }
}
