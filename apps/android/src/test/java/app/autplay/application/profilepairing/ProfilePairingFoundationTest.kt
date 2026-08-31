package app.autplay.application.profilepairing

import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest
import java.security.SecureRandom

class ProfilePairingFoundationTest {
    @Test fun normalizesOriginAndRejectsNonRootPath() {
        assertEquals("https://xn--bcher-kva.example", OriginNormalizer.normalize("HTTPS://bücher.example:443/"))
        assertFails { OriginNormalizer.normalize("https://example.test/api") }
        assertFails { OriginNormalizer.normalize("http://example.test") }
        assertEquals("http://192.168.1.10:8080", OriginNormalizer.normalize("http://192.168.1.10:8080", true))
        assertFails { OriginNormalizer.normalize("http://public.example", true) }
        assertFails { OriginNormalizer.normalize("http://192.168.999.1", true) }
        assertFails { OriginNormalizer.normalize("http://10.example", true) }
    }
    @Test fun delayedCapabilitiesCannotChangeNewerFlow() {
        val snapshot = snapshot()
        val current = PairingState.ExchangingInvitation(snapshot.copy(generationId = "10000000-0000-4000-8000-000000000002"))
        assertEquals(PairingState.Blocked(PairingFailure.STALE_FLOW_GENERATION), PairingReducer.applyCapabilities(current, snapshot, capabilities(), null, 0))
    }
    @Test fun capabilityIdentityAndRevisionFailClosed() {
        val snapshot = snapshot(); val current = PairingState.ExchangingInvitation(snapshot)
        assertEquals(PairingState.Blocked(PairingFailure.CAPABILITY_ROLLBACK_DETECTED), PairingReducer.applyCapabilities(current, snapshot, capabilities(), 2, 0))
        assertEquals(PairingState.Blocked(PairingFailure.SERVER_IDENTITY_CHANGED), PairingReducer.applyCapabilities(current, snapshot, capabilities().copy(identity = TrustedServerIdentity("10000000-0000-4000-8000-000000000099", 1, "a".repeat(64))), null, 0))
    }
    @Test fun refreshTokenIsExactBase64UrlAsciiBearer() {
        val deterministic = object : SecureRandom() { override fun nextBytes(bytes: ByteArray) { bytes.indices.forEach { bytes[it] = it.toByte() } } }
        val token = newM5RefreshToken(deterministic)
        assertEquals(43, token.size)
        assertTrue(token.all { it.toInt().toChar() in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" })
        // The transport and persisted credential use these identical ASCII bytes, not raw entropy.
        assertEquals(32, MessageDigest.getInstance("SHA-256").digest(token).size)
    }
    private fun snapshot() = PairingFlowSnapshot("10000000-0000-4000-8000-000000000001", "https://api.example", "https://stream.example", ServerProfileId("10000000-0000-4000-8000-000000000003"), "10000000-0000-4000-8000-000000000004", 1, "a".repeat(64), UserId("10000000-0000-4000-8000-000000000005"), DeviceId("10000000-0000-4000-8000-000000000006"), null, null, null)
    private fun capabilities() = CapabilityState(TrustedServerIdentity("10000000-0000-4000-8000-000000000004", 1, "a".repeat(64)), UserId("10000000-0000-4000-8000-000000000005"), DeviceId("10000000-0000-4000-8000-000000000006"), 1, 1, 1, emptySet())
    private fun assertFails(block: () -> Unit) { try { block(); throw AssertionError("expected failure") } catch (_: IllegalArgumentException) {} }
}
