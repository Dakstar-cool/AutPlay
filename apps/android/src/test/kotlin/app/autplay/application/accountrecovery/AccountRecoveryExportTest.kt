package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.application.selfpairing.SelfPairingJson
import app.autplay.application.selfpairing.SelfPairingProof
import app.autplay.application.selfpairing.SettingsSelfPairingSourceContext
import app.autplay.application.selfpairing.integer
import app.autplay.application.selfpairing.text
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.AccountRecoverySetupCheckpoint
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.io.IOException
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class AccountRecoveryExportTest {
    @Test fun explicitSaveAcknowledgesExactDocumentAndSurvivesOfflineRestart() = runBlocking {
        val h = Harness()
        val source = h.ready()
        val raw = source.export()
        assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        val ticket = source.prepareExport()
        assertEquals(SelfPairingProof.hash(raw), ticket.documentSha256)
        assertFalse(ticket.toString().contains(h.identity.apiOrigin))
        assertFalse(ticket.toString().contains(AccountRecoveryProof.ALPHABET))
        assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        var owned: ByteArray? = null
        source.saveExport(ticket) { bytes -> owned = bytes; assertArrayEquals(raw, bytes) }
        assertTrue(requireNotNull(owned).all { it == 0.toByte() })
        val saved = requireNotNull(h.settings.settings.value.accountRecoverySetup)
        assertEquals(1L, saved.savedCodeGeneration)
        assertEquals(ticket.documentSha256, saved.savedDocumentSha256)
        assertEquals(AccountRecoverySourceState.Ready(true, true, 1, true), source.state.value)
        val restarted = h.source()
        restarted.loadLocal()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 1, true), restarted.state.value)
        assertEquals(0, h.networkCalls)
        raw.fill(0)
    }

    @Test fun failedOrCancelledWriterRetainsRequiredSetupAndErasesBorrowedBytes() = runBlocking {
        for (failure in listOf(IOException("destination failed"), CancellationException("destination cancelled"))) {
            val h = Harness()
            val source = h.ready()
            val before = h.settings.settings.value
            val journal = h.credentials.values.getValue(AccountRecoverySlot.SOURCE.profile).copyOf()
            var owned: ByteArray? = null
            val outcome = runCatching { source.saveExport(source.prepareExport()) { bytes -> owned = bytes; throw failure } }
            assertSame(failure, outcome.exceptionOrNull())
            assertTrue(requireNotNull(owned).all { it == 0.toByte() })
            assertEquals(before, h.settings.settings.value)
            assertArrayEquals(journal, h.credentials.values[AccountRecoverySlot.SOURCE.profile])
            assertEquals(AccountRecoverySourceState.Ready(true, true, 1), source.state.value)
        }
    }

    @Test fun cancellationWithoutWriterExceptionCannotAcknowledgeSetup() = runBlocking {
        val h = Harness()
        val source = h.ready()
        val ticket = source.prepareExport()
        var owned: ByteArray? = null
        val saving = launch {
            source.saveExport(ticket) { bytes -> owned = bytes; currentCoroutineContext().cancel() }
        }
        saving.join()
        assertTrue(saving.isCancelled)
        assertTrue(requireNotNull(owned).all { it == 0.toByte() })
        assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
    }

    @Test fun rotationDuringWriterReleasesLocksAndRejectsBothAcknowledgementAndStaleTicket() = runBlocking {
        val h = Harness()
        val source = h.ready()
        val oldTicket = source.prepareExport()
        h.offline = false
        val outcome = withTimeout(5000) {
            runCatching { source.saveExport(oldTicket) { source.configure() } }
        }
        assertTrue(outcome.exceptionOrNull() is IllegalArgumentException)
        assertEquals(AccountRecoverySourceState.Ready(true, true, 2), source.state.value)
        assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        var writerCalled = false
        assertTrue(runCatching { source.saveExport(oldTicket) { writerCalled = true } }.isFailure)
        assertFalse(writerCalled)
        h.offline = true
        source.saveExport(source.prepareExport()) {}
        assertEquals(AccountRecoverySourceState.Ready(true, true, 2, true), source.state.value)
    }

    @Test fun laterRotationOrSameGenerationReplacementInvalidatesOldSaveEvidence() = runBlocking {
        for (rotate in listOf(true, false)) {
            val h = Harness()
            val source = h.ready()
            source.saveExport(source.prepareExport()) {}
            assertTrue((source.state.value as AccountRecoverySourceState.Ready).exportConfirmed)
            if (rotate) {
                h.offline = false
                source.configure()
            } else {
                AccountRecoveryJournalGate.serialized { h.writeSavedDocument(AccountRecoveryProof.ALPHABET.reversed()) }
            }
            val restarted = h.source()
            restarted.loadLocal()
            assertFalse((restarted.state.value as AccountRecoverySourceState.Ready).exportConfirmed)
            assertEquals(1L, h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        }
    }

    @Test fun accountOrBindingSwitchDuringWriterCannotConfirmTheReplacementBinding() = runBlocking {
        for (switchAccount in listOf(true, false)) {
            val h = Harness()
            val source = h.ready()
            val ticket = source.prepareExport()
            var owned: ByteArray? = null
            val outcome = withTimeout(5000) {
                runCatching { source.saveExport(ticket) { bytes ->
                    owned = bytes
                    BindingAuthorityWriteGate.serialized { h.replaceBinding(switchAccount) }
                } }
            }
            assertTrue(outcome.isFailure)
            assertTrue(requireNotNull(owned).all { it == 0.toByte() })
            assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
            assertEquals(NEW_COMMIT, h.settings.settings.value.accountRecoverySetup?.bindingCommitId)
        }
    }

    @Test fun accountOrBindingSwitchWhilePickerIsOpenRejectsBeforeAnyDestinationWrite() = runBlocking {
        for (switchAccount in listOf(true, false)) {
            val h = Harness()
            val source = h.ready()
            val ticket = source.prepareExport()
            BindingAuthorityWriteGate.serialized { h.replaceBinding(switchAccount) }
            var written = false
            assertTrue(runCatching { source.saveExport(ticket) { written = true } }.isFailure)
            assertFalse(written)
            assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        }
    }

    @Test fun existingAccountExportDoesNotCreateMandatorySetupCheckpoint() = runBlocking {
        val h = Harness(required = false)
        val source = h.ready()
        source.saveExport(source.prepareExport()) {}
        assertNull(h.settings.settings.value.accountRecoverySetup)
        val restarted = h.source()
        restarted.loadLocal()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 1), restarted.state.value)
        assertEquals(0, h.networkCalls)
    }

    @Test fun acknowledgementCommitThenThrowCanBeRecognizedLocallyAfterRestart() = runBlocking {
        val h = Harness()
        val source = h.ready()
        val ticket = source.prepareExport()
        val failure = IOException("settings response lost")
        h.settings.failureAfterCommit = failure
        var owned: ByteArray? = null
        assertSame(failure, runCatching { source.saveExport(ticket) { owned = it } }.exceptionOrNull())
        assertTrue(requireNotNull(owned).all { it == 0.toByte() })
        val restarted = h.source()
        restarted.loadLocal()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 1, true), restarted.state.value)
        assertEquals(0, h.networkCalls)
    }

    @Test fun pendingRecoveryOrMissingCredentialPreventsAcknowledgingAnOldDocument() = runBlocking {
        for (pendingRecovery in listOf(true, false)) {
            val h = Harness()
            val source = h.ready()
            val ticket = source.prepareExport()
            val outcome = runCatching { source.saveExport(ticket) {
                if (pendingRecovery) {
                    AccountRecoveryJournalGate.serialized {
                        AccountRecoveryPendingStore(h.credentials).write(AccountRecoverySlot.RECIPIENT,
                            JsonObject(mapOf("stage" to JsonPrimitive("COMMIT_PENDING"))))
                    }
                } else {
                    BindingAuthorityWriteGate.serialized { h.credentials.clear(ServerProfileId(SERVER)) }
                }
            } }
            assertTrue(outcome.isFailure)
            assertNull(h.settings.settings.value.accountRecoverySetup?.savedCodeGeneration)
        }
    }

    private class Harness(required: Boolean = true) {
        val credentials = MemoryCredentials()
        val identity = SelfPairingIdentity(SERVER, 1, "a".repeat(64), "https://api.example.test", "https://stream.example.test")
        val settings = MemorySettings(NonSecretSettings(
            activeServerProfileId = ServerProfileId(SERVER), activeUserId = UserId(ACCOUNT), deviceId = DeviceId(DEVICE),
            serverBaseUrl = identity.apiOrigin, streamBaseUrl = identity.streamOrigin,
            m5Binding = M5BindingCheckpoint(COMMIT, SERVER, 1, identity.thumbprint, "fixture-key", SESSION, SESSION, 0),
            accountRecoverySetup = if (required) AccountRecoverySetupCheckpoint(SERVER, ACCOUNT, COMMIT) else null,
        ))
        var generation = 1L
        var networkCalls = 0
        var offline = true
        fun source() = AccountRecoverySourceRuntime(credentials, SettingsSelfPairingSourceContext(settings, credentials),
            transport, { Instant.parse("2026-09-18T12:00:00Z") }, settings = settings)
        suspend fun ready(): AccountRecoverySourceRuntime {
            writeCredential(COMMIT)
            writeSavedDocument()
            return source().also { it.loadLocal(); assertEquals(AccountRecoverySourceState.Ready(true, true, 1), it.state.value) }
        }
        suspend fun writeSavedDocument(code: String = AccountRecoveryProof.ALPHABET) {
            AccountRecoveryDocument(identity, ACCOUNT, "Fixture account", code.toByteArray()).use { document ->
                val bytes = document.encode()
                try {
                    AccountRecoveryPendingStore(credentials).write(AccountRecoverySlot.SOURCE, JsonObject(identity.fields() + mapOf(
                        "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("SAVED"), "account_id" to JsonPrimitive(ACCOUNT),
                        "code_generation" to JsonPrimitive(generation), "document_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(bytes)),
                    )))
                } finally { bytes.fill(0) }
            }
        }
        private suspend fun writeCredential(commit: String) {
            val bytes = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("fixture-access", "fixture-refresh", 0,
                bindingCommitId = commit, sessionId = SESSION, sessionFamilyId = SESSION, sessionGeneration = 0))
            try { credentials.write(ServerProfileId(SERVER), bytes) } finally { bytes.fill(0) }
        }
        suspend fun replaceBinding(switchAccount: Boolean) {
            val account = if (switchAccount) OTHER_ACCOUNT else ACCOUNT
            writeCredential(NEW_COMMIT)
            settings.mutate { it.copy(activeUserId = UserId(account),
                m5Binding = requireNotNull(it.m5Binding).copy(bindingCommitId = NEW_COMMIT),
                accountRecoverySetup = AccountRecoverySetupCheckpoint(SERVER, account, NEW_COMMIT)) }
        }
        private val transport = object : AccountRecoveryTransport {
            override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, request: ByteArray?): JsonObject {
                networkCalls++
                check(!offline) { "unexpected network" }
                if (request == null) return json("contract_version" to "v1", "schema_version" to 1,
                    "account_id" to ACCOUNT, "account_label" to "Fixture account", "configured" to true, "code_generation" to generation)
                val parsed = SelfPairingJson.parse(request)
                assertEquals(generation, parsed.integer("expected_code_generation"))
                generation++
                return json("contract_version" to "v1", "schema_version" to 1, "operation_id" to parsed.text("operation_id"),
                    "account_id" to ACCOUNT, "code_generation" to generation, "configured" to true, "replayed" to false)
            }
            override suspend fun recipient(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject = error("unexpected recipient")
        }
    }

    private class MemorySettings(initial: NonSecretSettings) : NonSecretSettingsStore {
        override val settings = MutableStateFlow(initial)
        var failureAfterCommit: Exception? = null
        override suspend fun update(settings: NonSecretSettings) { this.settings.value = settings }
        override suspend fun mutate(transform: (NonSecretSettings) -> NonSecretSettings) {
            settings.value = transform(settings.value)
            failureAfterCommit?.let { failureAfterCommit = null; throw it }
        }
    }
    private class MemoryCredentials : CredentialStore {
        val values = mutableMapOf<ServerProfileId, ByteArray>()
        override suspend fun read(profileId: ServerProfileId) = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
    }
    private companion object {
        const val SERVER = "10000000-0000-4000-8000-000000000001"
        const val ACCOUNT = "10000000-0000-4000-8000-000000000002"
        const val DEVICE = "10000000-0000-4000-8000-000000000003"
        const val SESSION = "10000000-0000-4000-8000-000000000004"
        const val COMMIT = "10000000-0000-4000-8000-000000000005"
        const val NEW_COMMIT = "10000000-0000-4000-8000-000000000006"
        const val OTHER_ACCOUNT = "10000000-0000-4000-8000-000000000007"
        fun json(vararg fields: Pair<String, Any>): JsonObject = JsonObject(fields.associate { (name, value) -> name to when (value) {
            is Number -> JsonPrimitive(value); is Boolean -> JsonPrimitive(value); else -> JsonPrimitive(value.toString())
        } })
    }
}
