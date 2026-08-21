package app.autplay.ui.profilepairing

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.unit.dp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter

/** A bounded local QR encoding of a shown-once enrollment envelope. */
internal data class InvitationQrMatrix(
    val side: Int,
    private val darkModules: BooleanArray,
) {
    init {
        require(side in 1..InvitationQrEncoder.MAX_MODULES)
        require(darkModules.size == side * side)
    }

    fun isDark(x: Int, y: Int): Boolean = darkModules[y * side + x]

    internal fun copyModules(): BooleanArray = darkModules.copyOf()
}

/** Pure, deterministic ZXing adapter with a strict envelope-size boundary. */
internal object InvitationQrEncoder {
    const val MAX_ENVELOPE_CHARS = 4096
    const val MAX_MODULES = 177 // QR Model 2 Version 40 maximum side length.

    fun encode(envelope: String): InvitationQrMatrix {
        require(envelope.isNotBlank()) { "INVITATION_ENVELOPE_EMPTY" }
        require(envelope.length <= MAX_ENVELOPE_CHARS) { "INVITATION_ENVELOPE_TOO_LARGE" }
        val bits = QRCodeWriter().encode(envelope, BarcodeFormat.QR_CODE, 0, 0)
        require(bits.width == bits.height && bits.width in 1..MAX_MODULES) { "INVITATION_QR_BOUNDS" }
        return InvitationQrMatrix(
            side = bits.width,
            darkModules = BooleanArray(bits.width * bits.height) { index ->
                bits[index % bits.width, index / bits.width]
            },
        )
    }
}

/**
 * Renders the full volatile envelope only as pixels. Its only semantic label is generic, so the
 * envelope cannot leak through accessibility text, clipboard affordances, logs, or exports.
 */
@Composable
internal fun InvitationQrCode(envelope: String, modifier: Modifier = Modifier) {
    val matrix = remember(envelope) { InvitationQrEncoder.encode(envelope) }
    Canvas(
        modifier = modifier
            .size(224.dp)
            .clearAndSetSemantics { contentDescription = QR_CONTENT_DESCRIPTION },
    ) {
        val module = size.minDimension / matrix.side
        for (y in 0 until matrix.side) {
            for (x in 0 until matrix.side) {
                if (matrix.isDark(x, y)) {
                    drawRect(
                        color = Color.Black,
                        topLeft = androidx.compose.ui.geometry.Offset(x * module, y * module),
                        size = androidx.compose.ui.geometry.Size(module, module),
                    )
                }
            }
        }
    }
}

internal const val QR_CONTENT_DESCRIPTION = "Enrollment invitation QR code"
