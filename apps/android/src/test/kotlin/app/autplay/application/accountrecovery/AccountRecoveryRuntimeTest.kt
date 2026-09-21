package app.autplay.application.accountrecovery

import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.*
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.application.selfpairing.*
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AccountRecoveryRuntimeTest {
    @Test fun deletionCancellationUsesOwnJournalAndExactDeletionRevision() = runBlocking {
        val h = Harness(purpose = AccountRestorationPurpose.DELETE_CANCEL)
        val runtime = h.ready()
        val preview = (runtime.state.value as AccountRecoveryRecipientState.ConfirmAccount).preview
        assertEquals(OTHER, preview.deletion?.requestId)
        assertNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.RECIPIENT))
        h.loseCommit = true
        runtime.confirmAccount(USER)
        val pending = requireNotNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.DELETION_RECIPIENT))
        val original = pending.text("request_b64")
        val request = SelfPairingJson.parse(Base64.getDecoder().decode(original))
        assertEquals(OTHER, request.text("deletion_request_id"))
        assertEquals(1L, request.integer("expected_revision"))
        assertTrue(h.gate.isReservedBy(FirstBindCeremonyOwner.ACCOUNT_DELETE_CANCEL))
        runtime.cancelBeforeCommit()
        assertEquals(original, requireNotNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.DELETION_RECIPIENT)).text("request_b64"))
        h.restart().resume()
        assertTrue(h.bound)
        assertFalse(h.store.hasAccountDeletionPendingRecipient())
        assertEquals(h.proofs[1].third, h.proofs[2].third)
    }

    @Test fun durableDeletionCancellationFinishesOfflineAfterReceiptExpiry() = runBlocking {
        val h = Harness(this, AccountRestorationPurpose.DELETE_CANCEL)
        val runtime = h.ready()
        h.settings.failureAfterCommit = IOException("settings committed before failure")
        runtime.confirmAccount(USER)
        assertTrue((runtime.state.value as AccountRecoveryRecipientState.Blocked).commitPending)
        val calls = h.proofs.size
        h.clock = h.clock.plusSeconds(172800); h.offline = true
        val resumed = h.restart()
        resumed.resume()
        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        assertEquals(calls, h.proofs.size)
        assertFalse(h.store.hasAccountRecoveryPendingRecipient())
        assertEquals(2L, requireNotNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.SOURCE)).integer("code_generation"))
    }

    @Test fun cancellationCannotOverlapAnotherRecipientAfterRestart() = runBlocking {
        val h = Harness()
        h.ready()
        h.gate = FirstBindCeremonyGate()
        val other = h.recipient(AccountRestorationPurpose.DELETE_CANCEL)
        val calls = h.discoveryCalls
        other.importDocument(h.document())
        assertTrue(other.state.value is AccountRecoveryRecipientState.Blocked)
        assertEquals(calls, h.discoveryCalls)
        assertNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.DELETION_RECIPIENT))
    }

    @Test fun unresolvedDeletionBlocksBothRestorationPurposesWithoutContact() = runBlocking {
        for (purpose in AccountRestorationPurpose.entries) {
            val h = Harness(purpose = purpose)
            AccountRecoveryPendingStore(h.store).write(AccountRecoverySlot.DELETION_SOURCE, json("schema_version" to 1, "stage" to "REQUEST_PENDING"))
            val runtime = h.recipient()
            runtime.importDocument(h.document())
            assertTrue(runtime.state.value is AccountRecoveryRecipientState.Blocked)
            assertEquals(0, h.discoveryCalls)
            assertTrue(h.proofs.isEmpty())
        }
    }

    @Test fun committedSettingsFailureRetainsCredentialsAndRestartFinishesWithoutReplay() = runBlocking {
        assertUncertainBindingFinishesLocally(this, IOException("settings committed before failure"))
    }

    @Test fun committedSettingsCancellationPropagatesAndRestartFinishesWithoutReplay() = runBlocking {
        assertUncertainBindingFinishesLocally(this, CancellationException("settings committed before cancellation"))
    }

    private suspend fun assertUncertainBindingFinishesLocally(scope: CoroutineScope, failure: Exception) {
        val h = Harness(scope)
        val runtime = h.ready()
        h.settings.failureAfterCommit = failure
        val outcome = runCatching { runtime.confirmAccount(USER) }
        if (failure is CancellationException) {
            assertSame(failure, outcome.exceptionOrNull())
        } else {
            assertTrue(outcome.isSuccess)
            assertTrue((runtime.state.value as AccountRecoveryRecipientState.Blocked).commitPending)
        }
        val journal = h.store.values.getValue(AccountRecoverySlot.RECIPIENT.profile).copyOf()
        assertArrayEquals(requireNotNull(h.journalBeforeCommit), journal)
        val pending = requireNotNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.RECIPIENT))
        assertEquals("COMMIT_PENDING", pending.text("stage"))
        assertEquals(SERVER, h.settings.settings.value.activeServerProfileId?.value)
        assertEquals(USER, h.settings.settings.value.activeUserId?.value)
        assertEquals(DEVICE, h.settings.settings.value.deviceId?.value)
        val checkpoint = requireNotNull(h.settings.settings.value.m5Binding)
        val material = h.store.values.getValue(ServerProfileId(SERVER)).copyOf()
        val envelope = SessionCredentialEnvelopeCodec.decode(material)
        assertEquals(checkpoint.bindingCommitId, envelope.bindingCommitId)
        assertEquals(checkpoint.sessionId, envelope.sessionId)
        assertEquals(checkpoint.sessionFamilyId, envelope.sessionFamilyId)
        assertEquals(checkpoint.sessionGeneration, envelope.sessionGeneration)
        assertEquals(pending.text("next_refresh"), envelope.refreshToken)
        assertEquals(1, h.proofs.count { it.first == "recover" })
        val calls = h.proofs.size
        val discoveries = h.discoveryCalls
        val mutations = h.settings.mutations
        h.clock = h.clock.plusSeconds(172800)
        h.offline = true
        val resumed = h.restart()
        resumed.resume()
        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        assertEquals(calls, h.proofs.size)
        assertEquals(discoveries, h.discoveryCalls)
        assertEquals(mutations, h.settings.mutations)
        assertArrayEquals(material, h.store.values[ServerProfileId(SERVER)])
        assertFalse(h.store.hasAccountRecoveryPendingRecipient())
        val saved = requireNotNull(AccountRecoveryPendingStore(h.store).read(AccountRecoverySlot.SOURCE))
        assertEquals("SAVED", saved.text("stage"))
        assertEquals(pending.text("next_document_b64"), saved.text("document_b64"))
        assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.ACCOUNT_RECOVERY))
    }

    @Test fun importIsLocalAndEachDisclosureRequiresItsOwnConfirmation() = runBlocking {
        val h = Harness()
        val runtime = h.recipient()
        runtime.importDocument(h.document())
        assertTrue(runtime.state.value is AccountRecoveryRecipientState.ConfirmServer)
        assertEquals(0, h.discoveryCalls)
        assertEquals(0, h.proofs.size)
        runtime.confirmServer()
        assertTrue(runtime.state.value is AccountRecoveryRecipientState.ConfirmAccount)
        assertEquals(listOf("preview"), h.proofs.map { it.first })
        assertFalse(h.bound)
        runtime.confirmAccount(USER)
        assertEquals(AccountRecoveryRecipientState.Connected, runtime.state.value)
        assertTrue(h.bound)
        assertFalse(h.store.hasAccountRecoveryPendingRecipient())
        val source = h.source()
        source.loadLocal()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 2), source.state.value)
        AccountRecoveryDocument.parse(source.export()).use {
            assertEquals(USER, it.accountId)
            assertEquals(h.verifier, AccountRecoveryProof.verifier(it.identity, it.accountId, it.code))
        }
    }

    @Test fun lostCommitUsesExactRequestCodeAndRefreshAfterRestart() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.loseCommit = true
        runtime.confirmAccount(USER)
        val journal = h.store.values.getValue(AccountRecoverySlot.RECIPIENT.profile).copyOf()
        runtime.cancelBeforeCommit()
        assertArrayEquals(journal, h.store.values[AccountRecoverySlot.RECIPIENT.profile])
        assertTrue((runtime.state.value as AccountRecoveryRecipientState.Blocked).commitPending)
        h.clock = h.clock.plusSeconds(1800)
        val resumed = h.restart()
        resumed.resume()
        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        val commits = h.proofs.filter { it.first == "recover" }
        assertEquals(commits.first(), commits.last())
        assertEquals(1, h.keys.created)
        assertEquals(2L, h.generation)
    }

    @Test fun expiredLostReplyUsesBoundRefreshProofWithoutCreatingAnotherBranch() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.loseCommit = true
        runtime.confirmAccount(USER)
        assertTrue((runtime.state.value as AccountRecoveryRecipientState.Blocked).commitPending)
        assertFalse(h.bound)
        h.clock = h.clock.plusSeconds(25 * 3600)
        h.expireReceipts = true
        h.outcomeAvailable = true

        val resumed = h.restart()
        resumed.resume()

        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        assertTrue(h.bound)
        assertEquals(1, h.outcomeCalls)
        assertEquals(2L, h.generation)
        assertEquals(1, h.keys.created)
    }

    @Test fun expiredLostCancellationReplyUsesDeletionBoundRefreshProof() = runBlocking {
        val h = Harness(purpose = AccountRestorationPurpose.DELETE_CANCEL)
        val runtime = h.ready()
        h.loseCommit = true
        runtime.confirmAccount(USER)
        assertTrue((runtime.state.value as AccountRecoveryRecipientState.Blocked).commitPending)
        assertFalse(h.bound)
        h.clock = h.clock.plusSeconds(25 * 3600)
        h.expireReceipts = true
        h.outcomeAvailable = true

        val resumed = h.restart()
        resumed.resume()

        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        assertTrue(h.bound)
        assertEquals(1, h.outcomeCalls)
        assertEquals(2L, h.generation)
        assertEquals(1, h.keys.created)
        assertFalse(h.store.hasAccountDeletionPendingRecipient())
    }

    @Test fun failedJournalEraseBlocksRotationThenFinishesLocallyWithoutLosingNewCode() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.store.failRecipientClear = true
        runtime.confirmAccount(USER)
        assertTrue(h.bound)
        val saved = h.store.values.getValue(AccountRecoverySlot.SOURCE.profile).copyOf()
        val source = h.source()
        source.configure(replacePending = true)
        assertEquals(0, h.configurations.size)
        assertArrayEquals(saved, h.store.values[AccountRecoverySlot.SOURCE.profile])
        val calls = h.proofs.size
        h.offline = true
        val resumed = h.restart()
        resumed.resume()
        assertEquals(AccountRecoveryRecipientState.Connected, resumed.state.value)
        assertEquals(calls, h.proofs.size)
        assertFalse(h.store.hasAccountRecoveryPendingRecipient())
        h.offline = false
        source.configure()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 3), source.state.value)
    }

    @Test fun durableRetryPreservesANewerAlreadySavedCode() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.store.failRecipientClear = true
        runtime.confirmAccount(USER)
        val store = AccountRecoveryPendingStore(h.store)
        val old = requireNotNull(store.read(AccountRecoverySlot.SOURCE))
        val newer = JsonObject(old + mapOf("code_generation" to JsonPrimitive(3), "document_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(h.document()))))
        store.write(AccountRecoverySlot.SOURCE, newer)
        h.restart().resume()
        assertEquals(newer, store.read(AccountRecoverySlot.SOURCE))
    }

    @Test fun foreignBindingAndLostKeyLeaveUncertainCommitUntouched() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.loseCommit = true
        runtime.confirmAccount(USER)
        val calls = h.proofs.size
        h.foreign = true
        h.restart().resume()
        h.foreign = false
        h.keys.lost = true
        h.restart().resume()
        assertEquals(calls, h.proofs.size)
        assertTrue(h.store.hasAccountRecoveryPendingRecipient())
    }

    @Test fun previewCancellationReleasesFirstBindAndManualDiscoveryDoesNotSendCode() = runBlocking {
        val h = Harness()
        val runtime = h.recipient()
        runtime.inspectManual(h.identity.apiOrigin, USER, AccountRecoveryProof.ALPHABET)
        assertEquals(1, h.discoveryCalls)
        assertEquals(0, h.proofs.size)
        runtime.confirmServer()
        runtime.cancelBeforeCommit()
        assertEquals(AccountRecoveryRecipientState.Idle, runtime.state.value)
        assertFalse(h.store.hasAccountRecoveryPendingRecipient())
        assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.ACCOUNT_RECOVERY))
    }

    @Test fun lostConfigureReplaysExactlyAndSavedFileExportsOfflineAfterRestart() = runBlocking {
        val h = Harness()
        h.loseConfigure = true
        h.source().configure()
        val resumed = h.source()
        resumed.load()
        assertEquals(h.configurations.first(), h.configurations.last())
        assertEquals(2L, h.generation)
        val calls = h.sourceCalls
        h.offline = true
        val offline = h.source()
        offline.loadLocal()
        assertEquals(AccountRecoverySourceState.Ready(true, true, 2), offline.state.value)
        AccountRecoveryDocument.parse(offline.export()).use { assertEquals(h.verifier, AccountRecoveryProof.verifier(it.identity, it.accountId, it.code)) }
        assertEquals(calls, h.sourceCalls)
        h.context = h.context.copy(userId = OTHER)
        offline.loadLocal()
        assertEquals(AccountRecoverySourceState.Idle, offline.state.value)
        assertTrue(runCatching { offline.export() }.isFailure)
    }

    @Test fun neverDeliveredExpiredConfigureRequiresExplicitFreshOperation() = runBlocking {
        val h = Harness()
        h.dropConfigure = true
        h.source().configure()
        h.dropConfigure = false
        h.clock = h.clock.plusSeconds(180)
        val resumed = h.source()
        resumed.load()
        assertTrue(resumed.state.value is AccountRecoverySourceState.Blocked)
        val calls = h.configurations.size
        resumed.configure()
        assertEquals(calls, h.configurations.size)
        resumed.configure(replacePending = true)
        assertEquals(AccountRecoverySourceState.Ready(true, true, 2), resumed.state.value)
        assertNotEquals(h.configurations.first(), h.configurations.last())
    }

    private class Harness(private val bindingScope: CoroutineScope? = null,
        private val purpose: AccountRestorationPurpose = AccountRestorationPurpose.RECOVERY) {
        private val slot get() = if (purpose == AccountRestorationPurpose.RECOVERY) AccountRecoverySlot.RECIPIENT else AccountRecoverySlot.DELETION_RECIPIENT
        private val owner get() = if (purpose == AccountRestorationPurpose.RECOVERY) FirstBindCeremonyOwner.ACCOUNT_RECOVERY else FirstBindCeremonyOwner.ACCOUNT_DELETE_CANCEL
        val store = MemoryStore()
        val keys = RecoveryFixtureKeys()
        val settings = CommitThenThrowSettings()
        var gate = FirstBindCeremonyGate()
        var clock = Instant.parse("2026-09-18T12:00:00Z")
        val identity = SelfPairingIdentity(SERVER, 1, keys.publicKeyThumbprintSha256("server"), "https://api.test.invalid", "https://stream.test.invalid")
        var context = SelfPairingSourceBinding(ServerProfileId(SERVER), USER, DEVICE, SESSION, identity)
        var generation = 1L
        var verifier = AccountRecoveryProof.verifier(identity, USER, AccountRecoveryProof.ALPHABET.toByteArray())
        var bound = false
        var foreign = false
        var offline = false
        var loseCommit = false
        var loseConfigure = false
        var dropConfigure = false
        var expireReceipts = false
        var outcomeAvailable = false
        var outcomeCalls = 0
        var discoveryCalls = 0
        var sourceCalls = 0
        var committedIntent: AccountRecoveryBindingIntent? = null
        var journalBeforeCommit: ByteArray? = null
        val proofs = mutableListOf<Triple<String, String, String>>()
        val configurations = mutableListOf<String>()
        val receipts = mutableMapOf<String, JsonObject>()
        fun source() = AccountRecoverySourceRuntime(store, SelfPairingSourceContext { context }, transport, { clock })
        fun recipient(selectedPurpose: AccountRestorationPurpose = purpose) = AccountRecoveryRecipientRuntime(store, keys, transport, discovery, binding(),
            ActiveProfileGate { bound || foreign || settings.settings.value.activeServerProfileId != null }, gate, "Recovery phone", "test-1", { clock }, purpose = selectedPurpose)
        fun restart(): AccountRecoveryRecipientRuntime { gate = FirstBindCeremonyGate(); return recipient() }
        fun document(): ByteArray = AccountRecoveryDocument(identity, USER, "Recovery account", AccountRecoveryProof.ALPHABET.toByteArray()).use { it.encode() }
        suspend fun ready(): AccountRecoveryRecipientRuntime = recipient().also {
            it.importDocument(document()); it.confirmServer()
            assertTrue(it.state.value is AccountRecoveryRecipientState.ConfirmAccount)
        }
        private fun binding(): AccountRecoveryBindingCommitter {
            val scope = bindingScope ?: return committer
            val materializer = object : M5LocalIntentMaterializer {
                override suspend fun pending(limit: Int): List<PendingLocalIntentSummary> = error("unexpected local review")
                override suspend fun materialize(binding: ClientEventBinding, localChangeId: LocalId, eventId: LocalId, materializedAtMs: Long): LocalId = error("unexpected materialization")
            }
            val pairing = ProfilePairingRuntime(scope, settings, store, keys, discovery,
                M5BindingMaterializationCoordinator(settings, store, keys, materializer),
                "Recovery phone", {}, firstBindGate = gate)
            return ProfilePairingRecoveryBindingCommitter(pairing, discovery, settings, store, keys, owner)
        }
        private val discovery = object : UnsupportedDiscoveryPort() {
            override suspend fun discovery(apiOrigin: String): PairingNetworkResult<DiscoveryDocument> {
                discoveryCalls++
                return PairingNetworkResult.Success(DiscoveryDocument(TrustedServerIdentity(SERVER, 1, identity.thumbprint),
                    "Test server", identity.apiOrigin, identity.streamOrigin, setOf(1), clock.plusSeconds(60), keys.publicKeySpki("server")))
            }
        }
        private val committer = object : AccountRecoveryBindingCommitter {
            override suspend fun isDurable(intent: AccountRecoveryBindingIntent) = bound && !foreign && committedIntent == intent
            override suspend fun commit(intent: AccountRecoveryBindingIntent, result: AccountRecoveryResult, refresh: ByteArray, identitySpki: ByteArray): Boolean {
                assertTrue(store.hasAccountRecoveryPendingRecipient())
                val request = SelfPairingJson.parse(proofs.last().third.toByteArray())
                assertEquals(request.text("next_refresh_token_sha256"), SelfPairingProof.hash(refresh))
                assertEquals(identity.thumbprint, SelfPairingProof.hash(identitySpki))
                committedIntent = intent; bound = true; return true
            }
        }
        private val transport = object : AccountRecoveryTransport {
            override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, request: ByteArray?): JsonObject {
                sourceCalls++
                check(!offline)
                if (request == null) return json("contract_version" to "v1", "schema_version" to 1,
                    "account_id" to context.userId, "account_label" to "Recovery account", "configured" to (generation > 0), "code_generation" to generation)
                assertEquals("CONFIGURE_PENDING", requireNotNull(AccountRecoveryPendingStore(store).read(AccountRecoverySlot.SOURCE)).text("stage"))
                configurations += request.toString(Charsets.UTF_8)
                if (dropConfigure) throw IOException("not delivered")
                val parsed = SelfPairingJson.parse(request)
                val result = receipts[parsed.text("operation_id")] ?: run {
                    require(!parsed.instant("requested_at").isBefore(clock.minusSeconds(120)))
                    assertEquals(generation, parsed.integer("expected_code_generation"))
                    generation++; verifier = parsed.text("next_code_verifier_sha256")
                    json("contract_version" to "v1", "schema_version" to 1, "operation_id" to parsed.text("operation_id"),
                        "account_id" to context.userId, "code_generation" to generation, "configured" to true, "replayed" to false)
                        .also { receipts[parsed.text("operation_id")] = it }
                }
                if (loseConfigure) { loseConfigure = false; throw IOException("lost reply") }
                return result
            }
            override suspend fun recipient(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject {
                check(!offline)
                val journal = requireNotNull(AccountRecoveryPendingStore(store).read(slot))
                assertEquals(if (kind == "preview") "PREVIEW_PENDING" else "COMMIT_PENDING", journal.text("stage"))
                proofs += Triple(kind, code.toString(Charsets.US_ASCII), request.toString(Charsets.UTF_8))
                val parsed = SelfPairingJson.parse(request)
                if (kind == "preview" && purpose == AccountRestorationPurpose.DELETE_CANCEL) return json(
                    "contract_version" to "v1", "schema_version" to 1, "deletion_request_id" to OTHER,
                    "account_id" to USER, "state" to "PENDING", "revision" to 1,
                    "requested_at" to clock.toString(), "cancel_before" to clock.plusSeconds(30 * 86400).toString(),
                    "replayed" to false, "account_label" to "Recovery account", "code_generation" to generation, "confirmation_required" to true)
                if (kind == "preview") return json("contract_version" to "v1", "schema_version" to 1,
                    "operation_id" to parsed.text("operation_id"), "server_instance_id" to SERVER, "identity_epoch" to 1,
                    "account_id" to USER, "account_label" to "Recovery account", "role" to "USER", "code_generation" to generation, "confirmation_required" to true)
                journalBeforeCommit = store.values.getValue(slot.profile).copyOf()
                if (expireReceipts && receipts.containsKey(parsed.text("operation_id"))) {
                    throw AccountRecoveryRemoteFailure("account_recovery_unavailable")
                }
                if (!receipts.containsKey(parsed.text("operation_id"))) {
                    assertEquals(generation, parsed.integer("expected_code_generation"))
                    generation++; verifier = parsed.text("next_code_verifier_sha256")
                    receipts[parsed.text("operation_id")] = parsed
                }
                if (loseCommit) { loseCommit = false; throw IOException("lost reply") }
                return json("contract_version" to "v1", "schema_version" to 1, "operation_id" to parsed.text("operation_id"),
                    "binding_commit_id" to parsed.text("binding_commit_id"), "server_instance_id" to SERVER, "user_id" to USER,
                    "account_label" to "Recovery account", "device_id" to DEVICE, "session_id" to SESSION, "refresh_generation" to 0,
                    "refresh_absolute_expires_at" to clock.plusSeconds(86400).toString(), "receipt_expires_at" to clock.plusSeconds(86400).toString(),
                    "code_generation" to generation, "access_token" to "fixture-access-token", "access_expires_at" to clock.plusSeconds(300).toString(), "replayed" to (proofs.size > 2))
            }
            override suspend fun outcome(identity: SelfPairingIdentity, refresh: ByteArray, request: ByteArray): JsonObject {
                check(!offline && outcomeAvailable)
                outcomeCalls++
                val parsed = SelfPairingJson.parse(request)
                require(receipts.containsKey(parsed.text("operation_id")))
                assertEquals(parsed.text("next_refresh_token_sha256"), SelfPairingProof.hash(refresh))
                return json("contract_version" to "v1", "schema_version" to 1, "operation_id" to parsed.text("operation_id"),
                    "binding_commit_id" to parsed.text("binding_commit_id"), "server_instance_id" to SERVER, "user_id" to USER,
                    "account_label" to "Recovery account", "device_id" to DEVICE, "session_id" to SESSION, "refresh_generation" to 0,
                    "refresh_absolute_expires_at" to clock.plusSeconds(86400).toString(),
                    "receipt_expires_at" to parsed.instant("requested_at").plusSeconds(86400).toString(),
                    "code_generation" to generation, "access_token" to "fixture-access-token", "access_expires_at" to clock.plusSeconds(300).toString(),
                    "replayed" to true, "outcome_recovered" to true)
            }
        }
    }

    private class CommitThenThrowSettings : NonSecretSettingsStore {
        override val settings = MutableStateFlow(NonSecretSettings())
        var failureAfterCommit: Exception? = null
        var mutations = 0
        override suspend fun update(settings: NonSecretSettings) { this.settings.value = settings }
        override suspend fun mutate(transform: (NonSecretSettings) -> NonSecretSettings) {
            mutations++
            settings.value = transform(settings.value)
            if (settings.value.m5Binding != null) failureAfterCommit?.let {
                failureAfterCommit = null
                throw it
            }
        }
    }

    private class MemoryStore : CredentialStore {
        val values = mutableMapOf<ServerProfileId, ByteArray>()
        var failRecipientClear = false
        override suspend fun read(profileId: ServerProfileId) = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) {
            if (profileId == AccountRecoverySlot.RECIPIENT.profile && failRecipientClear) { failRecipientClear = false; throw IOException("clear failed") }
            values.remove(profileId)?.fill(0)
        }
        override suspend fun hasPublicAccessPendingRegistration() = values.values.any { SessionCredentialEnvelopeCodec.decode(it).publicAccessPendingRegistrationId != null }
    }
    private open class UnsupportedDiscoveryPort : ProfilePairingPort {
        override suspend fun discovery(apiOrigin: String): PairingNetworkResult<DiscoveryDocument> = error("unexpected")
        override suspend fun capabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot): PairingNetworkResult<CapabilityDocument> = error("unexpected")
        override suspend fun exchange(request: EnrollmentExchangeCommand): PairingNetworkResult<EnrollmentSession> = error("unexpected")
        override suspend fun createInvitation(profileId: ServerProfileId, operationId: String, expiresInSeconds: Int): PairingNetworkResult<ManagedInvitation> = error("unexpected")
        override suspend fun cancelInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): PairingNetworkResult<Unit> = error("unexpected")
        override suspend fun rotate(request: SessionRotationCommand): PairingNetworkResult<EnrollmentSession> = error("unexpected")
        override suspend fun devices(profileId: ServerProfileId): PairingNetworkResult<List<DeviceSummary>> = error("unexpected")
        override suspend fun sessions(profileId: ServerProfileId): PairingNetworkResult<List<SessionSummary>> = error("unexpected")
        override suspend fun lifecycle(profileId: ServerProfileId, command: LifecycleCommand): PairingNetworkResult<Unit> = error("unexpected")
    }
    private companion object {
        const val SERVER = "10000000-0000-4000-8000-000000000001"
        const val USER = "10000000-0000-4000-8000-000000000003"
        const val DEVICE = "10000000-0000-4000-8000-000000000004"
        const val SESSION = "10000000-0000-4000-8000-000000000005"
        const val OTHER = "10000000-0000-4000-8000-000000000009"
        fun json(vararg fields: Pair<String, Any>): JsonObject = JsonObject(fields.associate { (key, value) -> key to when (value) {
            is Number -> JsonPrimitive(value); is Boolean -> JsonPrimitive(value); else -> JsonPrimitive(value.toString())
        } })
    }
}
