package app.autplay.application.selfpairing

import app.autplay.application.accountrecovery.AccountRecoveryPendingStore
import app.autplay.application.accountrecovery.AccountRecoverySlot
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.*
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class SelfPairingRecipientRuntimeTest {
    @Test fun committedSettingsFailureRetainsCredentialsAndRestartFinishesWithoutReplay() = runBlocking {
        assertUncertainBindingFinishesLocally(this, IOException("settings committed before failure"))
    }

    @Test fun committedSettingsCancellationPropagatesAndRestartFinishesWithoutReplay() = runBlocking {
        assertUncertainBindingFinishesLocally(this, CancellationException("settings committed before cancellation"))
    }

    @Test fun inspectRejectsEveryReservedServerBeforeDiscoveryOrJournalOverwrite() = runBlocking {
        for (slot in CredentialJournalSlots.all) {
            val h = Harness(serverId = slot.value)
            h.seedSourceJournals()
            val saved = h.store.values.mapValues { it.value.copyOf() }
            val writes = h.store.writes
            val runtime = h.runtime()
            runtime.inspectQr(h.qr())
            assertTrue(runtime.state.value is SelfPairingRecipientState.Blocked)
            assertEquals(0, h.discoveryCalls)
            assertEquals(0, h.calls)
            assertEquals(0, h.keys.created)
            assertEquals(writes, h.store.writes)
            assertEquals(0, h.store.clears)
            assertEquals(saved.keys, h.store.values.keys)
            saved.forEach { (profile, material) -> assertArrayEquals(material, h.store.values[profile]) }
            assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.SELF_DEVICE_PAIRING))
        }
    }

    @Test fun resumeRejectsEveryReservedServerBeforeTransportAndPreservesExactJournal() = runBlocking {
        for (slot in CredentialJournalSlots.all) {
            val h = Harness(serverId = slot.value)
            h.seedSourceJournals()
            h.seedLegacyClaim()
            val saved = h.store.values.mapValues { it.value.copyOf() }
            val writes = h.store.writes
            val runtime = h.restart()
            runtime.resume()
            assertTrue((runtime.state.value as SelfPairingRecipientState.Blocked).pending)
            assertEquals(0, h.discoveryCalls)
            assertEquals(0, h.calls)
            assertEquals(writes, h.store.writes)
            assertEquals(0, h.store.clears)
            assertEquals(saved.keys, h.store.values.keys)
            saved.forEach { (profile, material) -> assertArrayEquals(material, h.store.values[profile]) }
        }
    }

    private suspend fun assertUncertainBindingFinishesLocally(scope: CoroutineScope, failure: Exception) {
        val h = Harness(bindingScope = scope)
        val runtime = h.ready()
        h.settings.failureAfterCommit = failure
        val outcome = runCatching { runtime.confirmAccount(USER) }
        if (failure is CancellationException) {
            assertSame(failure, outcome.exceptionOrNull())
        } else {
            assertTrue(outcome.isSuccess)
            assertTrue((runtime.state.value as SelfPairingRecipientState.Blocked).pending)
        }
        val journal = h.store.values.getValue(SelfPairingRole.RECIPIENT.slot).copyOf()
        assertArrayEquals(requireNotNull(h.journalBeforeCommit), journal)
        val pending = requireNotNull(SelfPairingPendingStore(h.store).read(SelfPairingRole.RECIPIENT))
        assertEquals("EXCHANGE_PENDING", pending.text("stage"))
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
        assertEquals(pending.text("next_refresh_token"), envelope.refreshToken)
        assertEquals(1, h.exchanges.size)
        val calls = h.calls
        val discoveries = h.discoveryCalls
        val mutations = h.settings.mutations
        h.clock = h.clock.plusSeconds(172800)
        h.offline = true
        val resumed = h.restart()
        resumed.resume()
        assertEquals(SelfPairingRecipientState.Connected, resumed.state.value)
        assertEquals(calls, h.calls)
        assertEquals(discoveries, h.discoveryCalls)
        assertEquals(mutations, h.settings.mutations)
        assertArrayEquals(material, h.store.values[ServerProfileId(SERVER)])
        assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
        assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.SELF_DEVICE_PAIRING))
    }

    @Test fun cancelledOrExpiredUncommittedExchangeReconcilesBeforeDiscarding() = runBlocking {
        for (terminal in listOf("CANCELLED", "EXPIRED")) {
            val h = Harness()
            val runtime = h.ready()
            h.loseExchange = true
            runtime.confirmAccount(USER)
            h.clock = h.clock.plusSeconds(1800)
            h.terminal = terminal
            val resumed = h.restart()
            resumed.resume()
            assertEquals(SelfPairingRecipientState.Finished(terminal), resumed.state.value)
            assertFalse(h.bound)
            val pending = SessionCredentialEnvelopeCodec.decode(h.store.values.getValue(SelfPairingRole.RECIPIENT.slot)).selfDevicePairingPending!!
            assertFalse(pending.contains("next_refresh_token"))
            resumed.dismiss()
            assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
        }
    }
    @Test fun bothExplicitConfirmationsAndEncryptedJournalPrecedeBinding() = runBlocking {
        val h = Harness()
        val runtime = h.runtime()
        runtime.inspectQr(h.qr())
        assertTrue(runtime.state.value is SelfPairingRecipientState.AwaitingTrust)
        assertEquals(0, h.calls)
        runtime.confirmTrust()
        assertTrue(runtime.state.value is SelfPairingRecipientState.WaitingForApproval)
        assertTrue(h.store.hasSelfDevicePairingPendingRecipient())
        assertFalse(h.bound)
        h.approved = true
        runtime.poll()
        assertTrue(runtime.state.value is SelfPairingRecipientState.AwaitingAccountConfirmation)
        assertEquals(0, h.exchanges.size)
        runtime.confirmAccount(USER)
        assertEquals(SelfPairingRecipientState.Connected, runtime.state.value)
        assertTrue(h.bound)
        assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
        assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.SELF_DEVICE_PAIRING))
    }

    @Test fun lostClaimResponseUsesExactKeySecretAndRequestAfterRestart() = runBlocking {
        val h = Harness()
        h.loseClaim = true
        h.runtime().also { it.inspectQr(h.qr()); it.confirmTrust() }
        assertEquals(1, h.claims.size)
        val runtime = h.restart()
        runtime.resume()
        assertEquals(2, h.claims.size)
        assertEquals(h.claims.first(), h.claims.last())
        assertEquals(1, h.keys.created)
        assertTrue(runtime.state.value is SelfPairingRecipientState.WaitingForApproval)
    }

    @Test fun lostExchangeReplaysAfterQrDeadlineWithSameRefreshAndBinding() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.loseExchange = true
        runtime.confirmAccount(USER)
        assertTrue(runtime.state.value is SelfPairingRecipientState.Blocked)
        assertFalse(h.bound)
        val pendingBefore = h.store.values.getValue(SelfPairingRole.RECIPIENT.slot).copyOf()
        h.clock = h.clock.plusSeconds(1800)
        val resumed = h.restart()
        resumed.resume()
        assertEquals(SelfPairingRecipientState.Connected, resumed.state.value)
        assertEquals(h.exchanges.first(), h.exchanges.last())
        assertEquals(1, h.keys.created)
        assertTrue(SessionCredentialEnvelopeCodec.decode(pendingBefore).selfDevicePairingPending!!.contains("EXCHANGE_PENDING"))
        pendingBefore.fill(0)
    }

    @Test fun committedBindingWithFailedJournalEraseFinishesLocallyAfterRestart() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.store.failClear = true
        runtime.confirmAccount(USER)
        assertTrue(h.bound)
        assertTrue(h.store.hasSelfDevicePairingPendingRecipient())
        val calls = h.calls
        h.clock = h.clock.plusSeconds(1800)
        val resumed = h.restart()
        resumed.resume()
        assertEquals(calls, h.calls)
        assertEquals(SelfPairingRecipientState.Connected, resumed.state.value)
        assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
    }

    @Test fun foreignBindingOrMissingKeyCannotConsumePendingExchange() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        h.loseExchange = true
        runtime.confirmAccount(USER)
        val calls = h.calls
        h.activeForeign = true
        h.restart().resume()
        assertEquals(calls, h.calls)
        assertTrue(h.store.hasSelfDevicePairingPendingRecipient())
        h.activeForeign = false
        h.keys.lost = true
        h.restart().resume()
        assertEquals(calls, h.calls)
    }

    @Test fun anUnexchangedExpiredJournalCanBeDismissedWithoutNetworkOrBinding() = runBlocking {
        val h = Harness()
        h.loseClaim = true
        h.runtime().also { it.inspectQr(h.qr()); it.confirmTrust() }
        val calls = h.calls
        h.clock = h.clock.plusSeconds(1800)
        val resumed = h.restart()
        resumed.resume()
        assertEquals(SelfPairingRecipientState.Finished("EXPIRED"), resumed.state.value)
        resumed.dismiss()
        assertEquals(calls, h.calls)
        assertFalse(h.bound)
        assertFalse(h.store.hasSelfDevicePairingPendingRecipient())
    }

    private class Harness(private val bindingScope: CoroutineScope? = null, serverId: String = SERVER) {
        val store = MemoryStore()
        val keys = Keys()
        val settings = CommitThenThrowSettings()
        var gate = FirstBindCeremonyGate()
        var clock = Instant.now()
        val expiry = clock.plusSeconds(900)
        val identity = SelfPairingIdentity(serverId, 1, SelfPairingProof.hash(keys.spki), "https://api.test.invalid", "https://stream.test.invalid")
        var calls = 0
        var discoveryCalls = 0
        var offline = false
        var approved = false
        var loseClaim = false
        var loseExchange = false
        var bound = false
        var activeForeign = false
        var terminal: String? = null
        var committedIntent: SelfPairingBindingIntent? = null
        var journalBeforeCommit: ByteArray? = null
        val claims = mutableListOf<String>()
        val exchanges = mutableListOf<String>()
        var claimed: JsonObject? = null
        fun qr(): ByteArray = SelfPairingQr(CEREMONY, identity, expiry, SelfPairingProof.secret()).use { it.encode() }
        fun runtime() = SelfPairingRecipientRuntime(store, keys, transport, discovery, binding(),
            ActiveProfileGate { bound || activeForeign || settings.settings.value.activeServerProfileId != null }, gate, "New phone", "fixture-1", { clock })
        fun restart(): SelfPairingRecipientRuntime { gate = FirstBindCeremonyGate(); return runtime() }
        suspend fun ready(): SelfPairingRecipientRuntime = runtime().also {
            it.inspectQr(qr()); it.confirmTrust(); approved = true; it.poll()
            assertTrue(it.state.value is SelfPairingRecipientState.AwaitingAccountConfirmation)
        }
        suspend fun seedSourceJournals() {
            val saved = JsonObject(mapOf("stage" to JsonPrimitive("SAVED")))
            SelfPairingPendingStore(store).write(SelfPairingRole.SOURCE, saved)
            AccountRecoveryPendingStore(store).write(AccountRecoverySlot.SOURCE, saved)
        }
        suspend fun seedLegacyClaim() {
            val alias = "autplay.self.pairing.fixture"
            keys.ensure(alias)
            SelfPairingQr.parse(qr(), clock).use { qr ->
                val secret = SelfPairingProof.secret()
                try {
                    val claim = SelfPairingProof.claim(qr, secret, "New phone", "fixture-1", keys, alias)
                    try {
                        SelfPairingPendingStore(store).write(SelfPairingRole.RECIPIENT, JsonObject(identity.fields() + mapOf(
                            "schema_version" to JsonPrimitive(1), "stage" to JsonPrimitive("CLAIM_PENDING"),
                            "ceremony_id" to JsonPrimitive(CEREMONY), "generation_id" to JsonPrimitive(UUID.randomUUID().toString()),
                            "key_alias" to JsonPrimitive(alias), "identity_spki_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(keys.spki)),
                            "expires_at" to JsonPrimitive(expiry.toString()),
                            "claim_request_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(claim)),
                            "poll_secret" to JsonPrimitive(secret.toString(Charsets.US_ASCII)),
                            "rendezvous_secret" to JsonPrimitive(qr.rendezvousSecret.toString(Charsets.US_ASCII)),
                        )))
                    } finally { claim.fill(0) }
                } finally { secret.fill(0) }
            }
        }
        private fun binding(): SelfPairingBindingCommitter {
            val scope = bindingScope ?: return committer
            val materializer = object : M5LocalIntentMaterializer {
                override suspend fun pending(limit: Int): List<PendingLocalIntentSummary> = error("unexpected local review")
                override suspend fun materialize(binding: ClientEventBinding, localChangeId: LocalId, eventId: LocalId, materializedAtMs: Long): LocalId = error("unexpected materialization")
            }
            val pairing = ProfilePairingRuntime(scope, settings, store, keys, discovery,
                M5BindingMaterializationCoordinator(settings, store, keys, materializer),
                "New phone", {}, firstBindGate = gate)
            return ProfilePairingSelfBindingCommitter(pairing, discovery, settings, store, keys)
        }
        private val discovery = object : UnsupportedDiscoveryPort() {
            override suspend fun discovery(apiOrigin: String): PairingNetworkResult<DiscoveryDocument> {
                discoveryCalls++
                check(!offline)
                return PairingNetworkResult.Success(
                    DiscoveryDocument(TrustedServerIdentity(identity.serverInstanceId, 1, identity.thumbprint), "Test server", identity.apiOrigin, identity.streamOrigin, setOf(1), clock.plusSeconds(60), keys.spki.copyOf()),
                )
            }
        }
        private val committer = object : SelfPairingBindingCommitter {
            override suspend fun isDurable(intent: SelfPairingBindingIntent): Boolean = bound && !activeForeign && committedIntent == intent
            override suspend fun commit(intent: SelfPairingBindingIntent, result: SelfPairingExchangeResult, refresh: ByteArray, identitySpki: ByteArray): Boolean {
                assertTrue(store.hasSelfDevicePairingPendingRecipient())
                assertEquals(SelfPairingProof.hash(refresh), SelfPairingJson.parse(exchanges.last().toByteArray()).text("next_refresh_token_sha256"))
                committedIntent = intent; bound = true; return true
            }
        }
        private val transport = object : SelfPairingTransport {
            override suspend fun source(identity: SelfPairingIdentity, profile: ServerProfileId, kind: String, ceremonyId: String, request: ByteArray?): JsonObject = error("unexpected source")
            override suspend fun recipient(identity: SelfPairingIdentity, kind: String, ceremonyId: String, secret: ByteArray, request: ByteArray): JsonObject {
                assertTrue(store.hasSelfDevicePairingPendingRecipient())
                calls++
                check(!offline)
                val value = SelfPairingJson.parse(request)
                return when (kind) {
                    "claim" -> {
                        claims += request.toString(Charsets.UTF_8); claimed = value
                        if (loseClaim) { loseClaim = false; throw IOException("lost") }
                        status()
                    }
                    "poll" -> status()
                    else -> {
                        exchanges += request.toString(Charsets.UTF_8)
                        journalBeforeCommit = store.values.getValue(SelfPairingRole.RECIPIENT.slot).copyOf()
                        if (terminal != null) throw SelfPairingRemoteFailure("self_pairing_unavailable")
                        if (loseExchange) { loseExchange = false; throw IOException("lost") }
                        JsonObject(mapOf(
                            "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
                            "exchange_id" to value.getValue("exchange_id"), "binding_commit_id" to value.getValue("binding_commit_id"),
                            "server_instance_id" to JsonPrimitive(SERVER), "user_id" to JsonPrimitive(USER),
                            "device_id" to JsonPrimitive(DEVICE), "session_id" to JsonPrimitive(SESSION), "refresh_generation" to JsonPrimitive(0),
                            "refresh_absolute_expires_at" to JsonPrimitive(clock.plusSeconds(86400).toString()),
                            "receipt_expires_at" to JsonPrimitive(clock.plusSeconds(86700).toString()),
                            "access_token" to JsonPrimitive("fixture-access-token"), "access_expires_at" to JsonPrimitive(clock.plusSeconds(300).toString()),
                            "replayed" to JsonPrimitive(exchanges.size > 1),
                        ))
                    }
                }
            }
            private fun status(): JsonObject {
                val claim = requireNotNull(claimed)
                return JsonObject(mapOf(
                    "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
                    "ceremony_id" to JsonPrimitive(CEREMONY), "state" to JsonPrimitive(terminal ?: if (approved) "APPROVED" else "CLAIMED"),
                    "revision" to JsonPrimitive(if (approved) 3 else 2), "expires_at" to JsonPrimitive(expiry.toString()), "retry_after_seconds" to JsonPrimitive(2),
                    "claim_id" to claim.getValue("claim_id"), "claim_request_sha256" to claim.getValue("request_sha256"),
                    "device_name" to claim.getValue("device_name"), "device_key_thumbprint_sha256" to claim.getValue("device_key_thumbprint_sha256"),
                    "comparison_code" to JsonPrimitive(SelfPairingProof.comparisonCode(identity, CEREMONY, claim.text("request_sha256"), claim.text("device_key_thumbprint_sha256"))),
                ) + if (approved && terminal == null) mapOf("account_id" to JsonPrimitive(USER), "account_label" to JsonPrimitive("Existing user"), "approval_operation_id" to JsonPrimitive(APPROVAL)) else emptyMap())
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
        var failClear = false
        var writes = 0
        var clears = 0
        override suspend fun read(profileId: ServerProfileId): ByteArray? = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { writes++; values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) {
            clears++
            if (failClear) { failClear = false; throw IOException("clear failed") }
            values.remove(profileId)?.fill(0)
        }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = values.values.any { SessionCredentialEnvelopeCodec.decode(it).publicAccessPendingRegistrationId != null }
    }
    private class Keys : M5DeviceKeyStore {
        val spki = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair().public.encoded
        var created = 0
        var lost = false
        override fun ensure(alias: String) { created++ }
        override fun publicKeySpki(alias: String): ByteArray { check(!lost); return spki.copyOf() }
        override fun publicKeyThumbprintSha256(alias: String): String { check(!lost); return SelfPairingProof.hash(spki) }
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray = ByteArray(64) { 1 }
        override fun delete(alias: String) = Unit
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
        const val CEREMONY = "10000000-0000-4000-8000-000000000002"
        const val USER = "10000000-0000-4000-8000-000000000003"
        const val DEVICE = "10000000-0000-4000-8000-000000000004"
        const val SESSION = "10000000-0000-4000-8000-000000000005"
        const val APPROVAL = "10000000-0000-4000-8000-000000000006"
    }
}
