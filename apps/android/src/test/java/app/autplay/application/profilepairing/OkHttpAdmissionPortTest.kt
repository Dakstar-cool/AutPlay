package app.autplay.application.profilepairing

import app.autplay.domain.ServerProfileId
import java.util.UUID
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpAdmissionPortTest {
    @Test fun recoveryAndPollKeepBearerHeaderOnlyAndEnforceNoStore() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(noStore("""{"review_locator":"review-rotated-value-1","poll_bearer":"poll-rotated-value-001"}"""))
            server.enqueue(noStore("""{"request_id":"$REQUEST_ID","state":"APPROVED","expires_at":"2026-08-25T12:15:00Z","approved_account_id":"$USER_ID","approved_account_label":"Owner"}"""))
            val checkpoint = checkpoint()
            val port = OkHttpAdmissionPort({ server.url("/").toString().trimEnd('/') })
            val recoveryWire = """{"request_id":"$REQUEST_ID","request_sha256":"${"a".repeat(64)}","expected_server_instance_id":"$SERVER_ID","expected_identity_epoch":1,"expected_identity_thumbprint_sha256":"${"b".repeat(64)}","device_key_thumbprint_sha256":"${"c".repeat(64)}","recovery_nonce_b64url":"${"N".repeat(43)}","proof_b64url":"${"P".repeat(86)}"}"""

            val recovered = port.recover(AdmissionRequest(checkpoint, recoveryWire))
            assertTrue(recovered is PairingNetworkResult.Success)
            val recoveryRequest = server.takeRequest()
            assertEquals("/api/v1/social/admission-requests/$REQUEST_ID/recover", recoveryRequest.path)
            assertEquals(null, recoveryRequest.getHeader("X-AutPlay-Admission-Poll"))
            assertTrue(recoveryRequest.body.readUtf8().contains("recovery_nonce_b64url"))
            assertEquals("no-store", recoveryRequest.getHeader("Cache-Control"))

            val rawBearer = "poll-rotated-value-001"
            val bearer = rawBearer.encodeToByteArray()
            val poll = port.poll(
                AdmissionRequest(checkpoint, """{"request_id":"$REQUEST_ID","device_key_thumbprint_sha256":"${"c".repeat(64)}","client_nonce_b64url":"${"Q".repeat(43)}","proof_b64url":"${"R".repeat(86)}"}"""),
                bearer,
            )
            assertTrue(poll is PairingNetworkResult.Success)
            val pollRequest = server.takeRequest()
            assertEquals("/api/v1/social/admission-requests/$REQUEST_ID/poll", pollRequest.path)
            assertEquals(rawBearer, pollRequest.getHeader("X-AutPlay-Admission-Poll"))
            assertFalse(requireNotNull(pollRequest.path).contains(rawBearer))
            assertTrue(bearer.all { it == 0.toByte() })
        } finally {
            server.close()
        }
    }

    @Test fun responseWithoutNoStoreFailsClosedBeforeUsingSecrets() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            server.enqueue(MockResponse().setResponseCode(202).setBody("""{"review_locator":"leaked-locator-value","poll_bearer":"leaked-poll-value-01"}"""))
            val port = OkHttpAdmissionPort({ server.url("/").toString().trimEnd('/') })
            assertEquals(
                PairingNetworkResult.Failure("admission_request_unavailable"),
                port.request(AdmissionRequest(checkpoint(), "{}")),
            )
        } finally {
            server.close()
        }
    }

    private fun checkpoint() = AdmissionCheckpoint(
        requestId = REQUEST_ID,
        requestSha256 = "a".repeat(64),
        serverProfileId = ServerProfileId(PROFILE_ID),
        serverInstanceId = SERVER_ID,
        identityEpoch = 1,
        identityThumbprintSha256 = "b".repeat(64),
        deviceKeyThumbprintSha256 = "c".repeat(64),
        generationId = UUID.randomUUID().toString(),
        apiOrigin = "https://api.test.invalid",
        streamOrigin = "https://stream.test.invalid",
    )

    private fun noStore(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Cache-Control", "no-store")
        .setBody(body)

    private companion object {
        const val REQUEST_ID = "11111111-1111-4111-8111-111111111111"
        const val PROFILE_ID = "22222222-2222-4222-8222-222222222222"
        const val SERVER_ID = "33333333-3333-4333-8333-333333333333"
        const val USER_ID = "44444444-4444-4444-8444-444444444444"
    }
}
