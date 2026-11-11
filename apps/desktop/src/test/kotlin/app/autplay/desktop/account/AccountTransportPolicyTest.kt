package app.autplay.desktop.account

import app.autplay.application.securestate.SecureMediaGuardCodec
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import org.junit.Assert.*
import org.junit.Test

class AccountTransportPolicyTest {
    @Test fun historicalConfirmationMatchesWithoutChangingProtectedEvidence() {
        val transport = pinnedTransport()
        val stored = transport.protectedPolicy(7).copy(confirmedAtMs = 1)
        val before = SecureMediaGuardCodec.transport(stored)
        assertTrue(transport.matches(stored))
        assertArrayEquals(before, SecureMediaGuardCodec.transport(stored))
        assertEquals(7L, stored.revision)
        assertEquals(1L, stored.confirmedAtMs)
    }

    @Test fun changedPinsModesAndUnknownPoliciesRemainDenied() {
        val transport = pinnedTransport()
        val stored = transport.protectedPolicy(7).copy(confirmedAtMs = 1)
        assertFalse(transport.matches(stored.copy(apiPin = "0".repeat(64))))
        assertFalse(transport.matches(stored.copy(streamPin = "0".repeat(64))))
        assertFalse(transport.matches(stored.copy(apiMode = "PLATFORM_PKI", apiPin = null)))
        assertFalse(transport.matches(stored.copy(streamMode = "PLATFORM_PKI", streamPin = null)))
        assertFalse(transport.matches(stored.copy(apiMode = "UNKNOWN")))
    }

    private fun pinnedTransport(): ConfirmedAccountTransport {
        fun key(): AccountTlsTrust = AccountTlsTrust.ExactSpki(KeyPairGenerator.getInstance("EC").run {
            initialize(ECGenParameterSpec("secp256r1")); generateKeyPair().public.encoded
        })
        return ConfirmedAccountTransport.confirm("https://api.test.invalid", "https://stream.test.invalid",
            key(), key(), true, true)
    }
}
