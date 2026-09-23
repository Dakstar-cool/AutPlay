package app.autplay.application.face

import java.io.File
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class FaceProjectionV2CodecTest {
    private val fixture = Json.parseToJsonElement(
        File("../../tests/fixtures/face/v2-projection-lease.json").readText()).jsonObject
    private val envelope = fixture.getValue("envelope_json").jsonPrimitive.content.toByteArray()
    private val spki = Base64.getUrlDecoder().decode(
        fixture.getValue("pinned_spki_b64url").jsonPrimitive.content)
    private val now = fixture.getValue("trusted_now_ms").jsonPrimitive.content.toLong()
    private val profile = "50000000-0000-4000-8000-000000000005"
    private val user = "60000000-0000-4000-8000-000000000006"
    private val server = "70000000-0000-4000-8000-000000000007"

    private fun verify(data: ByteArray = envelope, userId: String = user, time: Long = now) =
        FaceProjectionV2Codec.verify(data, profile, userId, server, 2, "fixture-key", spki, time)

    @Test fun exactPythonSignedVectorAndMinimumLease() {
        val result = verify()
        assertEquals("80000000-0000-4000-8000-000000000008", result.projectionId)
        assertEquals(86_400_000L, result.authorizedUntilMs - result.issuedAtMs)
        assertEquals(5L, result.activationEpoch)
        assertEquals("cc".repeat(32), result.resultSha256)
    }

    @Test fun tamperWrongOwnerDuplicateAndExpiryFailClosed() {
        val text = envelope.decodeToString()
        val tampered = text.replace("\"offline_lease_ms\":86400000",
            "\"offline_lease_ms\":172800000").toByteArray()
        val duplicate = text.replace("\"schema_version\":2",
            "\"schema_version\":2,\"schema_version\":2").toByteArray()
        assertThrows(FaceProjectionV2Exception::class.java) { verify(tampered) }
        assertThrows(FaceProjectionV2Exception::class.java) { verify(duplicate) }
        assertThrows(FaceProjectionV2Exception::class.java) { verify(userId = server) }
        assertThrows(FaceProjectionV2Exception::class.java) { verify(time = now + 86_400_000L) }
    }

    @Test fun pinnedIdentityAndSignedLineageCannotBeSubstituted() {
        val text = envelope.decodeToString()
        val changedLineage = text.replace("\"result_sha256\":\"${"c".repeat(64)}\"",
            "\"result_sha256\":\"${"d".repeat(64)}\"").toByteArray()
        assertThrows(FaceProjectionV2Exception::class.java) { verify(changedLineage) }
        assertThrows(FaceProjectionV2Exception::class.java) {
            FaceProjectionV2Codec.verify(envelope, profile, user, server, 3,
                "fixture-key", spki, now)
        }
        assertThrows(FaceProjectionV2Exception::class.java) {
            FaceProjectionV2Codec.verify(envelope, profile, user, server, 2,
                "fixture-key", ByteArray(spki.size), now)
        }
        assertThrows(FaceProjectionV2Exception::class.java) { verify(time = now - 301_001L) }
    }
}
