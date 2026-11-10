package app.autplay.desktop.account

import app.autplay.application.securestate.SecureJson
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.Result
import com.google.zxing.ResultMetadataType
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.awt.image.BufferedImage
import java.awt.image.DataBufferInt
import java.io.ByteArrayInputStream
import java.nio.CharBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import javax.imageio.ImageIO
import javax.imageio.ImageReader
import javax.imageio.stream.MemoryCacheImageInputStream

/** Process-only selected-file decoder. The caller owns the input and returned secret bytes. */
internal object DesktopPairingInput {
    const val MAX_FILE_BYTES: Int = 4_194_304
    const val MAX_TEXT_BYTES: Int = 16_384
    private const val MAX_SIDE: Int = 4096
    private const val MAX_PIXELS: Long = 4_194_304

    /** Runs on the host's retained worker; retirement does not imply that a decoder has exited. */
    fun decodeFile(bytes: ByteArray, requireCurrent: () -> Unit): ByteArray {
        requireCurrent()
        controlRequire(bytes.size in 2..MAX_FILE_BYTES)
        var decoded: ByteArray? = null
        try {
            val value = if (png(bytes) || jpeg(bytes)) image(bytes, requireCurrent) else {
                controlRequire(bytes.size <= MAX_TEXT_BYTES)
                bytes.copyOf()
            }
            decoded = value
            controlRequire(value.size in 2..MAX_TEXT_BYTES)
            // No URI, browser action, image metadata or arbitrary text can become a command.
            SecureJson.parse(value, MAX_TEXT_BYTES, canonical = false)
            requireCurrent()
            return value.also { decoded = null }
        } catch (failure: AccountControlException) { throw failure }
        catch (_: Exception) { throw AccountControlException(AccountControlFailure.INVALID_RESPONSE) }
        finally { decoded?.fill(0) }
    }

    private fun image(bytes: ByteArray, requireCurrent: () -> Unit): ByteArray {
        // This constructor cannot create the default ImageIO disk cache for a secret QR image.
        MemoryCacheImageInputStream(ByteArrayInputStream(bytes)).use { input ->
            val readers = ImageIO.getImageReaders(input)
            var reader: ImageReader? = null
            try {
                while (readers.hasNext()) {
                    val candidate = readers.next()
                    val expected = if (png(bytes)) "com.sun.imageio.plugins.png.PNGImageReader"
                        else "com.sun.imageio.plugins.jpeg.JPEGImageReader"
                    if (candidate.javaClass.name == expected && candidate.javaClass.module.name == "java.desktop") {
                        reader = candidate; break
                    }
                    candidate.dispose()
                }
                val selected = reader ?: throw AccountControlException(AccountControlFailure.UNSUPPORTED)
                selected.setInput(input, true, true)
                val width = selected.getWidth(0); val height = selected.getHeight(0)
                controlRequire(width in 1..MAX_SIDE && height in 1..MAX_SIDE && width.toLong() * height <= MAX_PIXELS)
                requireCurrent()
                val alpha = png(bytes)
                val destination = BufferedImage(width, height, if (alpha) BufferedImage.TYPE_INT_ARGB else BufferedImage.TYPE_INT_RGB)
                val pixels = (destination.raster.dataBuffer as DataBufferInt).data
                try {
                    val parameters = selected.defaultReadParam.apply { this.destination = destination }
                    val image = selected.read(0, parameters)
                    controlRequire(image === destination && image.width == width && image.height == height)
                    requireCurrent()
                    if (alpha) pixels.indices.forEach { index ->
                        if (index % 16_384 == 0) requireCurrent()
                        val pixel = pixels[index]; val opacity = pixel ushr 24
                        val white = 255 * (255 - opacity)
                        val red = (((pixel ushr 16) and 255) * opacity + white) / 255
                        val green = (((pixel ushr 8) and 255) * opacity + white) / 255
                        val blue = ((pixel and 255) * opacity + white) / 255
                        pixels[index] = (red shl 16) or (green shl 8) or blue
                    }
                    val source = RGBLuminanceSource(width, height, pixels)
                    try {
                        val bitmap = BinaryBitmap(HybridBinarizer(source))
                        val matrix = bitmap.blackMatrix
                        val qr = QRCodeReader()
                        try {
                            requireCurrent()
                            val result = qr.decode(bitmap, mapOf(DecodeHintType.CHARACTER_SET to "UTF-8"))
                            try {
                                requireCurrent()
                                controlRequire(result.barcodeFormat == BarcodeFormat.QR_CODE &&
                                    result.resultMetadata?.containsKey(ResultMetadataType.STRUCTURED_APPEND_SEQUENCE) != true &&
                                    result.resultMetadata?.containsKey(ResultMetadataType.STRUCTURED_APPEND_PARITY) != true)
                                return encode(result)
                            } finally { retire(result) }
                        } finally { qr.reset(); matrix.clear() }
                    } finally { source.matrix.fill(0) }
                } finally { pixels.fill(0); destination.flush() }
            } finally { reader?.dispose() }
        }
    }

    private fun encode(result: Result): ByteArray {
        controlRequire(result.text.length in 2..MAX_TEXT_BYTES)
        val encoder = StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        val encoded = encoder.encode(CharBuffer.wrap(result.text))
        try {
            controlRequire(encoded.remaining() in 2..MAX_TEXT_BYTES)
            return ByteArray(encoded.remaining()).also { encoded.get(it) }
        } finally { if (encoded.hasArray()) encoded.array().fill(0) }
    }

    private fun retire(result: Result): Unit {
        result.rawBytes?.fill(0)
        (result.resultMetadata?.get(ResultMetadataType.BYTE_SEGMENTS) as? List<*>)
            ?.forEach { (it as? ByteArray)?.fill(0) }
    }

    private fun png(bytes: ByteArray): Boolean = bytes.size >= 8 && bytes[0] == 0x89.toByte() &&
        bytes[1] == 0x50.toByte() && bytes[2] == 0x4e.toByte() && bytes[3] == 0x47.toByte() &&
        bytes[4] == 13.toByte() && bytes[5] == 10.toByte() && bytes[6] == 26.toByte() && bytes[7] == 10.toByte()

    private fun jpeg(bytes: ByteArray): Boolean = bytes.size >= 3 && bytes[0] == 0xff.toByte() &&
        bytes[1] == 0xd8.toByte() && bytes[2] == 0xff.toByte()
}
