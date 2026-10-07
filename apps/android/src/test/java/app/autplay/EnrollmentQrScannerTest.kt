package app.autplay

import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import org.junit.Assert.assertEquals
import org.junit.Test
import app.autplay.application.profilepairing.OriginNormalizer

class EnrollmentQrScannerTest {
    @Test
    fun decodesServerAddressGeneratedByAdminWebWithItsPortPreserved() {
        val rows = requireNotNull(javaClass.getResourceAsStream("/server-connection-qr.txt"))
            .bufferedReader().use { it.readLines() }.filterNot { it.startsWith("#") }
        val scale = 8
        val width = rows.size * scale
        val luma = ByteArray(width * width) { index ->
            if (rows[(index / width) / scale][(index % width) / scale] == '1') 0 else 0xff.toByte()
        }
        val origin = decodeQrLuminance(luma, width, width)

        assertEquals("https://mobile.test:8443", origin)
        assertEquals(origin, OriginNormalizer.normalize(origin))
    }

    @Test
    fun decodesDeviceInvitationWithoutTakingPicture() {
        val invitation = "{\"invitation_id\":\"11111111-1111-4111-8111-111111111111\",\"invitation_secret\":\"test-only\"}"
        val matrix = QRCodeWriter().encode(invitation, BarcodeFormat.QR_CODE, 384, 384)
        val luma = ByteArray(matrix.width * matrix.height) { index ->
            if (matrix[index % matrix.width, index / matrix.width]) 0 else 0xff.toByte()
        }

        assertEquals(invitation, decodeQrLuminance(luma, matrix.width, matrix.height))
    }
}
