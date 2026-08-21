package app.autplay.ui.profilepairing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class InvitationQrCodeTest {
    @Test
    fun encoderIsDeterministicAndBounded() {
        val envelope = "{\"invitation_id\":\"11111111-1111-4111-8111-111111111111\",\"invitation_secret\":\"shown-once\"}"

        val first = InvitationQrEncoder.encode(envelope)
        val second = InvitationQrEncoder.encode(envelope)

        assertEquals(first.side, second.side)
        assertTrue(first.side <= InvitationQrEncoder.MAX_MODULES)
        assertArrayEquals(first.copyModules(), second.copyModules())
    }

    @Test(expected = IllegalArgumentException::class)
    fun encoderRejectsOversizedEnvelopeBeforeQrWork() {
        InvitationQrEncoder.encode("x".repeat(InvitationQrEncoder.MAX_ENVELOPE_CHARS + 1))
    }
}
