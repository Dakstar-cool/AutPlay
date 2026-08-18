package app.autplay.application.sync

import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import org.junit.Assert.assertEquals
import org.junit.Test

class P04ClientEventHasherTest {
    @Test
    fun matchesP04Rfc8785GoldenVector() {
        val input = P04ClientEventHashInput(
            eventId = LocalId("11111111-1111-4111-8111-111111111111"),
            idempotencyKey = "21111111-1111-4111-8111-111111111111",
            binding = ClientEventBinding(
                userId = UserId("31111111-1111-4111-8111-111111111111"),
                deviceId = DeviceId("41111111-1111-4111-8111-111111111111"),
                serverProfileId = ServerProfileId("51111111-1111-4111-8111-111111111111"),
            ),
            deviceSequence = 2,
            eventType = "USER_TRACK_REF_CREATED",
            aggregateType = "USER_TRACK_REF",
            aggregateLocalId = LocalId("61111111-1111-4111-8111-111111111111"),
            aggregateServerId = null,
            baseServerRowVersion = null,
            occurredAt = "2026-08-15T00:00:00Z",
            payloadJson = "{\"rating\":5}",
        )

        assertEquals(
            "1da6337721df4f004be28716a86211fd99f39d3c72486aad0ff3198d6f8d1770",
            P04ClientEventHasher.sha256(input).toHex(),
        )
    }

    private fun ByteArray.toHex(): String = joinToString(separator = "") { byte ->
        "%02x".format(byte.toInt() and 0xff)
    }
}
