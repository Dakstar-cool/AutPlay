package app.autplay.application.portableaccount

import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class PortableAccountStoreTest {
    @Test fun offlineRotationKeepsHistoryAfterCodeAndFileAcknowledgmentAndEnrollmentDisablesOfflineReset() = runBlocking {
        val credentials = MemoryCredentials()
        val settings = MemorySettings()
        val store = PortableAccountStore(credentials, settings)
        val password = "offline rotation fixture phrase".toCharArray()
        try {
            val previous = store.create("owner", password)
            assertTrue(store.canRotateOffline())
            val oldBytes = requireNotNull(store.recoveryExport())
            try {
                PortableRecoveryFile.parse(oldBytes).use { oldFile ->
                    PortableAccountCrypto.create("owner", password, previous.accountId).use { replacement ->
                        store.offlineRotate(previous, replacement, oldFile)
                        assertEquals(previous.accountId, store.read()?.accountId)
                        assertEquals(0L, store.ownership()?.standaloneSinceMs)
                        assertTrue(store.canRotateOffline())
                        store.lineage()!!.use { assertEquals(1, it.predecessors.size); assertFalse(it.everEnrolled) }
                        store.findPredecessor(previous)!!.use { assertTrue(it.code.contentEquals(oldFile.code)) }
                        val code = requireNotNull(store.recoveryCodeExport())
                        try { store.acknowledgeRecoveryCode(code) } finally { code.fill('\u0000') }
                        val archive = requireNotNull(store.recoveryExport())
                        try {
                            store.acknowledgeRecoveryExport(archive)
                            assertNull(store.recoveryExport())
                            store.findPredecessor(previous)!!.close()
                            store.markServerEnrollmentAttempted(replacement.document)
                            val restarted = PortableAccountStore(credentials, settings)
                            assertFalse(restarted.canRotateOffline())
                            PortableRecoveryFile.parse(archive).use { exported ->
                                PortableAccountCrypto.create("owner", password, previous.accountId).use { third ->
                                    assertTrue(runCatching { restarted.offlineRotate(replacement.document, third, exported) }.isFailure)
                                }
                            }
                            assertEquals(replacement.document.keyThumbprint, restarted.read()?.keyThumbprint)
                            restarted.lineage()!!.use { assertTrue(it.everEnrolled); assertEquals(1, it.predecessors.size) }
                        } finally { archive.fill(0) }
                    }
                }
            } finally { oldBytes.fill(0) }
        } finally { password.fill('\u0000'); credentials.clear(CredentialJournalSlots.portableAccount) }
    }

    @Test fun rootlessOfflineFileRestoreIsConservativeAndPropagationCannotOverwriteCurrentRoot() = runBlocking {
        val credentials = MemoryCredentials()
        val settings = MemorySettings()
        val store = PortableAccountStore(credentials, settings)
        val password = "rootless recovery fixture phrase".toCharArray()
        try {
            PortableAccountCrypto.create("owner", password).use { original ->
                PortableRecoveryFile.create(original.document, original.recoveryCode).use { file ->
                    PortableAccountCrypto.create("owner", password, original.document.accountId).use { replacement ->
                        store.offlineRotate(original.document, replacement, file)
                        assertFalse(store.canRotateOffline())
                        val ownership = requireNotNull(store.ownership())
                        assertTrue(ownership.standaloneSinceMs > 0)
                        val saved = requireNotNull(store.recoveryExport())
                        try {
                            store.commitRotation(original.document, replacement.document, null)
                            assertEquals(ownership, store.ownership())
                            val export = store.recoveryExport()
                            try { assertTrue(export != null && saved.contentEquals(export)) } finally { export?.fill(0) }
                            assertTrue(runCatching { store.commitRotation(replacement.document, original.document, null) }.isFailure)
                            assertEquals(replacement.document.keyThumbprint, store.read()?.keyThumbprint)
                            store.lineage()!!.use { assertTrue(it.everEnrolled); assertEquals(1, it.predecessors.size) }
                        } finally { saved.fill(0) }
                    }
                }
            }
        } finally { password.fill('\u0000'); credentials.clear(CredentialJournalSlots.portableAccount) }
    }

    @Test fun encryptedRootRecoversLostMarkersAndPersistsOneConservativeOwnershipCutoff() = runBlocking {
        val credentials = MemoryCredentials()
        val settings = MemorySettings()
        val password = "local account fixture phrase".toCharArray()
        try {
            PortableAccountCrypto.create("owner", password).use { created ->
                val encoded = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("portable-account", null, 0,
                    portableAccountDocument = created.document.encode().toString(Charsets.UTF_8)))
                try { credentials.write(CredentialJournalSlots.portableAccount, encoded) } finally { encoded.fill(0) }
                val store = PortableAccountStore(credentials, settings)
                val ownership = requireNotNull(store.ownership())
                assertEquals(created.document.accountId, ownership.accountId)
                assertTrue(ownership.standaloneSinceMs > 0)
                val persisted = requireNotNull(credentials.read(CredentialJournalSlots.portableAccount))
                try {
                    assertEquals(ownership.standaloneSinceMs, SessionCredentialEnvelopeCodec.decode(persisted).portableAccountStandaloneSinceMs)
                } finally { persisted.fill(0) }
                assertNull(settings.settings.value.portableAccountId)
                val restarted = PortableAccountStore(credentials, settings)
                assertFalse(restarted.canRotateOffline())
                restarted.recoverIdentity()
                assertEquals(ownership, restarted.ownership())
                assertEquals(created.document.accountId, settings.settings.value.portableAccountId)
                assertEquals(created.document.keyThumbprint, settings.settings.value.portableAccountKeyThumbprint)
                assertTrue(restarted.verify("owner", password))
            }
        } finally { password.fill('\u0000'); credentials.clear(CredentialJournalSlots.portableAccount) }
    }

    private class MemorySettings : NonSecretSettingsStore {
        override val settings = MutableStateFlow(NonSecretSettings())
        override suspend fun update(settings: NonSecretSettings) { this.settings.value = settings }
    }

    private class MemoryCredentials : CredentialStore {
        private val values = mutableMapOf<ServerProfileId, ByteArray>()
        override suspend fun read(profileId: ServerProfileId): ByteArray? = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            values.put(profileId, material.copyOf())?.fill(0)
        }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = false
    }
}
