package app.autplay.desktop.account

import app.autplay.application.portableaccount.*
import app.autplay.application.securestate.*
import app.autplay.ui.desktop.*
import org.junit.Assert.*
import org.junit.Test

class PortablePasswordLoginTest {
    @Test fun loginFormCommandSatisfiesPasswordPermitWithoutAllowingPasswordlessInvoke() {
        val permit = DesktopAccountPermit("login", DesktopAccountAction.LoginPortable, requiresPassword = true)
        val command = DesktopAccountCommand.LoginPortable("existing_account", DesktopAccountSecret("secret input"))
        try {
            assertTrue(permit.acceptsCommand(command))
            assertFalse(permit.acceptsCommand(DesktopAccountCommand.Invoke(DesktopAccountAction.LoginPortable)))
            assertFalse(permit.copy(action = DesktopAccountAction.RegisterPortable).acceptsCommand(command))
            assertFalse(command.toString().contains("secret input"))
        } finally { command.password.clear() }
        assertTrue(runCatching { command.password.consume { it.size } }.isFailure)
    }

    @Test fun downloadedRootKeepsIdentityWithoutInventingRecoveryMaterial() {
        val password = "existing account test password".toCharArray()
        PortableAccountCrypto.create("existing_account", password).use { created ->
            val passport = created.document.encode()
            val history = PortableAccountLineage(true, emptyList()).encode(created.document).toByteArray()
            try {
                PortableRootPayload(passport, byteArrayOf(), history, 100, true).use { root ->
                    assertFalse(root.hasRecoveryDocument)
                    assertArrayEquals(passport, root.passportCopy())
                    assertTrue(root.recoveryCopy().isEmpty())
                    root.markEnrollmentAttempted().use { copied ->
                        assertFalse(copied.hasRecoveryDocument)
                        assertTrue(copied.everEnrolled)
                        assertArrayEquals(passport, copied.passportCopy())
                    }
                }
                val neverEnrolled = PortableAccountLineage(false, emptyList()).encode(created.document).toByteArray()
                try {
                    assertTrue(runCatching { PortableRootPayload(passport, byteArrayOf(), neverEnrolled, 100, false) }.isFailure)
                } finally { neverEnrolled.fill(0) }
            } finally { passport.fill(0); history.fill(0) }
        }
        password.fill('\u0000')
    }

    @Test fun existingRecoveryArchiveRemainsExactAndMalformedArchiveIsRejected() {
        val password = "existing account test password".toCharArray()
        PortableAccountCrypto.create("existing_account", password).use { created ->
            val passport = created.document.encode()
            val recovery = PortableRecoveryFile.encode(created.document, created.recoveryCode)
            val history = PortableAccountLineage(true, emptyList()).encode(created.document).toByteArray()
            try {
                PortableRootPayload(passport, recovery, history, 100, true).use { root ->
                    assertTrue(root.hasRecoveryDocument)
                    assertArrayEquals(recovery, root.recoveryCopy())
                    root.markEnrollmentAttempted().use { assertArrayEquals(recovery, it.recoveryCopy()) }
                }
                assertTrue(runCatching { PortableRootPayload(passport, byteArrayOf(1), history, 100, true) }.isFailure)
            } finally { passport.fill(0); recovery.fill(0); history.fill(0) }
        }
        password.fill('\u0000')
    }

    @Test fun passwordRejectionHasAStableRedactedUserFacingError() {
        val failure = IllegalArgumentException("ACCOUNT_AUTHENTICATION_FAILED")
        assertEquals(AccountControlFailure.AUTHENTICATION_FAILED, safeFailure(failure))
        assertEquals(AccountControlFailure.AUTHENTICATION_FAILED,
            safeFailure(java.util.concurrent.CompletionException(failure)))
    }
}
