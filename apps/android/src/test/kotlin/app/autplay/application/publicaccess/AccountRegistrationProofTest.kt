package app.autplay.application.publicaccess

import app.autplay.data.security.M5DeviceKeyStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.SecureRandom

class AccountRegistrationProofTest {
    @Test fun canonicalProofIsReplayStableForSamePendingRequest() {
        val key = FakeKeys()
        val invite = AccountInvitationParser.parseQr(document())
        val first = AccountRegistrationProof.create(invite, "Pixel", "0.3.0", key)
        val replay = AccountRegistrationProof.sign(first.registration, key)
        assertEquals(first.requestSha256, replay.requestSha256)
        assertTrue(first.canonicalJson.contentEquals(replay.canonicalJson))
        assertTrue(first.registration.keyAlias.startsWith("autplay.public.registration."))
        assertTrue(!first.registration.keyAlias.contains("m5"))
        first.close(); replay.close()
    }

    @Test fun processDeathReplayRetainsRegistrationIdAndKeyInsteadOfCreatingAnotherEnrollment() {
        val key = FakeKeys()
        val first = AccountRegistrationProof.create(AccountInvitationParser.parseQr(document()), "Pixel", "0.3.0", key)
        val resumed = AccountRegistrationProof.resume(first.canonicalJson.copyOf(), first.registration.successorRefreshToken.copyOf(), key)
        assertEquals(first.registration.registrationId, resumed.registration.registrationId)
        assertEquals(first.registration.keyAlias, resumed.registration.keyAlias)
        assertEquals(first.requestSha256, resumed.requestSha256)
        first.close(); resumed.close()
    }

    private class FakeKeys : M5DeviceKeyStore {
        override fun ensure(alias: String) = Unit
        override fun publicKeySpki(alias: String) = ByteArray(91) { 7 }
        override fun publicKeyThumbprintSha256(alias: String) = "b".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = ByteArray(64) { 1 }
        override fun delete(alias: String) = Unit
    }
    private fun document() = """{"contract_version":"v1","schema_version":1,"invitation_id":"10000000-0000-4000-8000-000000000001","server_instance_id":"10000000-0000-4000-8000-000000000002","identity_epoch":1,"identity_thumbprint_sha256":"${"a".repeat(64)}","api_origin":"https://api.example","stream_origin":"https://stream.example","account_display_name":"Friend","account_role":"USER","issued_at":"2026-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","invitation_secret":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY","secret_handling":"DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"}"""
}
