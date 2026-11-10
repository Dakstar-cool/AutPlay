package app.autplay.desktop.acceptance

import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import app.autplay.desktop.account.DesktopPairingInput
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import java.awt.image.BufferedImage
import java.awt.image.DataBufferInt
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO
import javax.imageio.stream.MemoryCacheImageOutputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail

/** Future decoder assertions inside the existing import composite; no native or account authority. */
internal object DesktopPairingInputAssertions {
    fun run(): Unit {
        val text = "{\"fixture\":\"pairing-input\",\"label\":\"\u041f\u0440\u0438\u0432\u0435\u0442\"}\n"
        val original = text.toByteArray(Charsets.UTF_8)
        try {
            val plain = DesktopPairingInput.decodeFile(original) { }
            try { assertArrayEquals(original, plain) } finally { plain.fill(0) }
            val picture = qrPng(text)
            try {
                val decoded = DesktopPairingInput.decodeFile(picture) { }
                try { assertArrayEquals(original, decoded) } finally { decoded.fill(0) }
            } finally { picture.fill(0) }
            var visits = 0
            failure(AccountControlFailure.CONTEXT_CHANGED) {
                DesktopPairingInput.decodeFile(original) {
                    if (++visits == 2) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                }.fill(0)
            }
            assertEquals(2, visits)
        } finally { original.fill(0) }

        val address = qrPng("https://example.invalid/untrusted-qr")
        try { failure(AccountControlFailure.INVALID_RESPONSE) { DesktopPairingInput.decodeFile(address) { }.fill(0) } }
        finally { address.fill(0) }
        val tooLarge = ByteArray(DesktopPairingInput.MAX_FILE_BYTES + 1)
        try { failure(AccountControlFailure.INVALID_RESPONSE) { DesktopPairingInput.decodeFile(tooLarge) { }.fill(0) } }
        finally { tooLarge.fill(0) }
        val tooWide = BufferedImage(4097, 1, BufferedImage.TYPE_INT_RGB)
        val widePng = try { png(tooWide) } finally { tooWide.flush() }
        try { failure(AccountControlFailure.INVALID_RESPONSE) { DesktopPairingInput.decodeFile(widePng) { }.fill(0) } }
        finally { widePng.fill(0) }
    }

    private fun qrPng(text: String): ByteArray {
        val side = 384
        val bits = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, side, side,
            mapOf(com.google.zxing.EncodeHintType.CHARACTER_SET to "UTF-8"))
        val image = BufferedImage(side, side, BufferedImage.TYPE_INT_ARGB)
        val pixels = (image.raster.dataBuffer as DataBufferInt).data
        try {
            for (y in 0 until side) for (x in 0 until side) {
                // Transparent white quiet-zone pixels exercise alpha composition in the reader.
                pixels[y * side + x] = if (bits[x, y]) 0xff000000.toInt() else 0x00ffffff
            }
            return png(image)
        } finally { pixels.fill(0); image.flush(); bits.clear() }
    }

    private fun png(image: BufferedImage): ByteArray = ByteArrayOutputStream().use { output ->
        MemoryCacheImageOutputStream(output).use { cache -> assertTrue(ImageIO.write(image, "png", cache)); cache.flush() }
        output.toByteArray()
    }

    private fun failure(expected: AccountControlFailure, block: () -> Unit): Unit {
        try { block(); fail("Expected pairing input refusal") }
        catch (failure: AccountControlException) { assertEquals(expected, failure.code) }
    }
}
