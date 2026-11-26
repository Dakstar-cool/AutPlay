package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.securestate.SecureMediaGuardCodec
import app.autplay.desktop.presentation.desktopRuntimeConnectionView
import app.autplay.desktop.presentation.signInActions
import app.autplay.ui.desktop.DesktopAccountAction
import app.autplay.ui.desktop.DesktopAccountCondition
import app.autplay.ui.desktop.DesktopAccountIssue
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import org.junit.Assert.*
import org.junit.Test

class AccountTransportPolicyTest {
    @Test fun releaseServerOffersPasswordOrInvitationWithoutTrustedReenrollment() {
        val families = setOf(D1.Family.ENROLLMENT, D1.Family.SESSION, D1.Family.PORTABLE)
        assertEquals(setOf(DesktopAccountAction.LoginPortable, DesktopAccountAction.SubmitInvitation),
            signInActions(families, hasLocalRoot = false, hasTrustedProfile = true))
        assertEquals(setOf(DesktopAccountAction.SubmitInvitation),
            signInActions(families - D1.Family.PORTABLE, hasLocalRoot = true, hasTrustedProfile = true))
        assertTrue(signInActions(emptySet(), hasLocalRoot = true, hasTrustedProfile = true).isEmpty())
        val extra = setOf(D1.Family.TRUSTED, D1.Family.ADMISSION, D1.Family.PORTABLE)
        assertEquals(setOf(DesktopAccountAction.LoginPortable, DesktopAccountAction.RegisterPortable,
            DesktopAccountAction.EnrollTrusted, DesktopAccountAction.RequestAdmission),
            signInActions(extra, hasLocalRoot = true, hasTrustedProfile = true))
        assertFalse(DesktopAccountAction.EnrollTrusted in signInActions(extra, hasLocalRoot = true, hasTrustedProfile = false))
    }

    @Test fun confirmedServerIsReadyAndDiscoveryFailuresKeepTheirCause() {
        val ready = DesktopAccountViewState(phase = DesktopAccountPhase.READY)
        assertEquals(DesktopAccountCondition.Ready, desktopRuntimeConnectionView(ready).condition)
        for ((reason, issue) in mapOf(D1.Failure.EXPIRED to DesktopAccountIssue.Expired,
            D1.Failure.INVALID_SIGNATURE to DesktopAccountIssue.IdentityChanged,
            D1.Failure.UNSUPPORTED to DesktopAccountIssue.ServerConnectionUnsupported,
            D1.Failure.INVALID_DOCUMENT to DesktopAccountIssue.InvalidServerResponse)) {
            val failure = assertThrows(AccountControlException::class.java) { verified(D1.Result.Rejected(reason)) }
            assertEquals(issue, desktopRuntimeConnectionView(ready.copy(failure = failure.code)).issue)
        }
    }

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
