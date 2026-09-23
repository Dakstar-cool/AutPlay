package app.autplay.application.face

import java.io.File
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class FaceLeaseClockV2Test {
    private val fixture = Json.parseToJsonElement(
        File("../../tests/fixtures/face/v2-projection-lease.json").readText()).jsonObject
    private val envelope = fixture.getValue("envelope_json").jsonPrimitive.content.toByteArray()
    private val spki = Base64.getUrlDecoder().decode(
        fixture.getValue("pinned_spki_b64url").jsonPrimitive.content)
    private val now = fixture.getValue("trusted_now_ms").jsonPrimitive.content.toLong()
    private val projection = FaceProjectionV2Codec.verify(envelope,
        "50000000-0000-4000-8000-000000000005",
        "60000000-0000-4000-8000-000000000006",
        "70000000-0000-4000-8000-000000000007", 2, "fixture-key", spki, now)

    @Test fun rollbackAndProcessRestoreCannotExtendTheLease() {
        val accepted = FaceLeaseClockV2.acceptOnline(projection, now, 10_000, 4)
        assertNotNull(accepted)
        val later = FaceLeaseClockV2.advance(projection, accepted!!.advancedAnchor,
            now - 20_000, 20_000, 4)
        assertNotNull(later)
        assertEquals(now + 9_000, later!!.effectiveNowMs)
        val restored = FaceLeaseClockV2.advance(projection, later.advancedAnchor,
            now - 100_000, 21_000, 4)
        assertNotNull(restored)
        assertEquals(now + 10_000, restored!!.effectiveNowMs)
        assertNull(FaceLeaseClockV2.advance(projection, restored.advancedAnchor,
            now - 100_000, 10_000 + 86_401_000, 4))
    }

    @Test fun rebootDiscontinuityAndIdentityChangeRequireOnlineAcceptance() {
        val accepted = FaceLeaseClockV2.acceptOnline(projection, now, 10_000, 4)!!
        val anchor = accepted.advancedAnchor
        assertNull(FaceLeaseClockV2.advance(projection, anchor, now, 11_000, 5))
        assertNull(FaceLeaseClockV2.advance(projection, anchor, now, 9_999, 4))
        assertNull(FaceLeaseClockV2.advance(projection.copy(serverIdentityEpoch = 3),
            anchor, now, 11_000, 4))
        assertNull(FaceLeaseClockV2.advance(projection.copy(projectionId =
            "90000000-0000-4000-8000-000000000009"), anchor, now, 11_000, 4))
        assertNull(FaceLeaseClockV2.advance(projection, null, now, 11_000, 4))
    }

    @Test fun onlineRenewalCarriesTheProfileMaximumAcrossBoots() {
        val accepted = FaceLeaseClockV2.acceptOnline(projection, now + 30_000, 10_000, 4)!!
        val rollback = FaceLeaseClockV2.acceptOnline(projection, now - 30_000, 100, 5,
            accepted.advancedAnchor)!!
        assertEquals(now + 30_000, rollback.effectiveNowMs)
        assertNull(FaceLeaseClockV2.acceptOnline(projection, projection.authorizedUntilMs,
            100, 5, rollback.advancedAnchor))
    }
}
