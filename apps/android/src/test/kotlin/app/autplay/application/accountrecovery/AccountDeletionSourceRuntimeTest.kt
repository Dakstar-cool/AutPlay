package app.autplay.application.accountrecovery

import app.autplay.application.profilepairing.BindingRecovery
import app.autplay.application.profilepairing.BindingRecoveryResult
import app.autplay.application.selfpairing.*
import app.autplay.data.security.*
import app.autplay.data.settings.*
import app.autplay.domain.*
import java.io.IOException
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AccountDeletionSourceRuntimeTest {
    @Test fun acceptedDeletionDetachesBeforePostAndRetainsHistoricalProofWithoutBearer() = runBlocking {
        val h = Harness()
        val runtime = h.ready()
        runtime.request(USER, AccountRecoveryProof.ALPHABET)
        assertTrue(runtime.state.value is AccountDeletionSourceState.Recorded)
        assertEquals(listOf("receipt", "request"), h.calls)
        assertNull(h.settings.settings.value.m5Binding)
        assertNull(h.store.values[h.profile])
        val pending = requireNotNull(h.pending())
        assertEquals("RECORDED", pending.text("stage"))
        assertFalse(pending.containsKey("access"))
        assertTrue(pending.containsKey("code") && pending.containsKey("request_b64"))
        assertFalse(h.store.hasUnresolvedAccountDeletion())
        assertEquals(1, h.purges)
    }

    @Test fun lostReplyResolvesThroughHistoricalReceiptWithoutOrdinarySession() = runBlocking {
        val h = Harness(); h.loseReply = true
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        assertTrue((runtime.state.value as AccountDeletionSourceState.Blocked).pending)
        val pending = requireNotNull(h.pending())
        assertTrue(h.store.hasUnresolvedAccountDeletion())
        h.clock = h.clock.plusSeconds(172800)
        h.runtime().resume()
        assertEquals(listOf("receipt", "request", "receipt"), h.calls)
        assertEquals(pending.text("request_b64"), requireNotNull(h.pending()).text("request_b64"))
        assertEquals(1, h.requests.size)
    }

    @Test fun missingReceiptAndExpiredBearerNeverPermitDiscardOrReplacement() = runBlocking {
        val h = Harness(); h.accept = false
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        val original = requireNotNull(h.pending())
        h.clock = h.clock.plusSeconds(172800)
        val resumed = h.runtime(); resumed.resume()
        assertTrue((resumed.state.value as AccountDeletionSourceState.Blocked).pending)
        assertEquals(original, h.pending())
        assertEquals(h.requests[0], h.resolutions[0])
        assertTrue(h.store.hasAccountRecoveryPendingRecipient())
        resumed.request(USER, AccountRecoveryProof.ALPHABET)
        assertEquals(1, h.requests.size)
    }

    @Test fun deathAfterJournalBeforeDetachVetoesAuthorityAndRecoveryCannotReviveIt() = runBlocking {
        val h = Harness(); h.dieBeforeDetach = true
        val runtime = h.ready()
        assertTrue(runCatching { runtime.request(USER, AccountRecoveryProof.ALPHABET) }.exceptionOrNull() is CancellationException)
        assertNotNull(h.settings.settings.value.m5Binding)
        assertNotNull(h.store.values[h.profile])
        assertTrue(h.calls.isEmpty())
        assertNull(h.store.readAccessToken(h.profile))
        val provider = RefreshingSessionCredentials(h.identity.apiOrigin, h.store,
            m5Rotation = M5SessionRotationClient(SettingsM5RotationContextResolver(h.settings), h.keys))
        assertTrue(runCatching { provider.access(h.profile) }.exceptionOrNull() is SessionRequiredException)
        val recovery = BindingRecovery(h.settings, h.store)
        assertEquals(BindingRecoveryResult.ClearedPartialBinding, recovery.recover(h.profile))
        assertNull(h.settings.settings.value.m5Binding)
        h.runtime().resume()
        assertEquals(listOf("receipt", "request"), h.calls)
    }

    @Test fun unreadableJournalVetoesOrdinaryAuthorityAndKeepsUnknownIntent() = runBlocking {
        val h = Harness()
        h.store.values[AccountRecoverySlot.DELETION_SOURCE.profile] = "broken".toByteArray()
        assertTrue(h.store.accountDeletionVetoes(h.profile, COMMIT))
        assertNull(h.store.readAccessToken(h.profile))
        val provider = RefreshingSessionCredentials(h.identity.apiOrigin, h.store,
            m5Rotation = M5SessionRotationClient(SettingsM5RotationContextResolver(h.settings), h.keys))
        assertTrue(runCatching { provider.access(h.profile) }.exceptionOrNull() is SessionRequiredException)
        val runtime = h.runtime(); runtime.resume()
        assertTrue((runtime.state.value as AccountDeletionSourceState.Blocked).pending)
        assertTrue(h.calls.isEmpty())
    }

    @Test fun historicalCancellationOnAnotherPhoneClearsJournalWithoutDetachingNewBinding() = runBlocking {
        val h = Harness()
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        val newer = h.initial.copy(m5Binding = h.initial.m5Binding!!.copy(bindingCommitId = OTHER, deviceKeyAlias = "new-key"))
        h.settings.settings.value = newer
        h.store.values[h.profile] = SessionCredentialEnvelopeCodec.encode(h.envelope.copy(bindingCommitId = OTHER))
        assertFalse(h.store.accountDeletionVetoes(h.profile, OTHER))
        h.receipt = JsonObject(requireNotNull(h.receipt) + mapOf("state" to JsonPrimitive("CANCELLED"), "revision" to JsonPrimitive(2)))
        val resumed = h.runtime(); resumed.resume()
        assertEquals("CANCELLED", (resumed.state.value as AccountDeletionSourceState.Recorded).receipt.state)
        assertNull(h.pending())
        assertEquals(newer, h.settings.settings.value)
        assertNotNull(h.store.values[h.profile])
        assertEquals(1, h.requests.size)
        resumed.load()
        assertTrue(resumed.state.value is AccountDeletionSourceState.Ready)
    }

    @Test fun currentCredentialIsCapturedAfterConcurrentRotationDuringStatus() = runBlocking {
        val h = Harness()
        val runtime = h.ready(); h.rotateDuringStatus = true; h.accept = false
        runtime.request(USER, AccountRecoveryProof.ALPHABET)
        assertEquals("rotated-bearer", requireNotNull(h.pending()).text("access"))
        assertEquals("rotated-bearer", h.sentAccess)
    }

    @Test fun lastOwnerCannotEnterDeletionOrDetach() = runBlocking {
        val h = Harness(); h.allowed = false
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        assertNull(h.pending())
        assertEquals(h.initial, h.settings.settings.value)
        assertTrue(h.calls.isEmpty())
    }

    @Test fun delayedHistoricalReceiptCannotResurrectJournalClearedByCancellation() = runBlocking {
        val h = Harness()
        h.ready().request(USER, AccountRecoveryProof.ALPHABET)
        h.beforeReceiptReturn = {
            AccountRecoveryJournalGate.serialized { h.store.clear(AccountRecoverySlot.DELETION_SOURCE.profile) }
        }
        val runtime = h.runtime(); runtime.resume()
        assertNull(h.pending())
        assertEquals(AccountDeletionSourceState.Idle, runtime.state.value)
        assertEquals(1, h.requests.size)
    }

    @Test fun journalReadCancellationPropagatesWithoutBecomingAuthenticationFailure() = runBlocking {
        val h = Harness()
        val failure = CancellationException("cancel journal read")
        h.store.readFailure = failure
        assertSame(failure, runCatching { h.store.readAccessToken(h.profile) }.exceptionOrNull())
        assertSame(failure, runCatching { h.store.accountDeletionVetoes(h.profile, COMMIT) }.exceptionOrNull())
    }

    @Test fun authoritativeNegativeClearsOnlyExactJournalAndLeavesPhoneDisconnected() = runBlocking {
        val h = Harness(); h.accept = false
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        val original = requireNotNull(h.pending())
        h.clock = h.clock.plusSeconds(121)
        h.resolution = h.negative(original)
        runtime.resume()
        assertTrue(runtime.state.value is AccountDeletionSourceState.NotAccepted)
        assertNull(h.pending())
        assertNull(h.settings.settings.value.m5Binding)
        assertNull(h.store.values[h.profile])
        assertFalse(h.store.hasUnresolvedAccountDeletion())
        assertEquals(h.requests.single(), h.resolutions.single())
    }

    @Test fun initializingServerCannotJournalOrDetachDeletion() = runBlocking {
        val h = Harness(); h.allowed = false; h.denialReason = "deletion_initializing"
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        assertNull(h.pending())
        assertEquals(h.initial, h.settings.settings.value)
        assertNotNull(h.store.values[h.profile])
        assertTrue(h.calls.isEmpty())
    }

    @Test fun wrongNegativeProofAndUndurableClearKeepUnknownJournal() = runBlocking {
        val h = Harness(); h.accept = false
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        val original = requireNotNull(h.pending())
        h.clock = h.clock.plusSeconds(121)
        h.resolution = JsonObject(h.negative(original) + ("request_sha256" to JsonPrimitive("b".repeat(64))))
        runtime.resume()
        assertEquals(original, h.pending())
        assertTrue((runtime.state.value as AccountDeletionSourceState.Blocked).pending)
        h.resolution = h.negative(original); h.store.ignoreDeletionClear = true
        runtime.resume()
        assertEquals(original, h.pending())
        assertTrue((runtime.state.value as AccountDeletionSourceState.Blocked).pending)
    }

    @Test fun delayedNegativeCannotClearReplacedJournal() = runBlocking {
        val h = Harness(); h.accept = false
        val runtime = h.ready(); runtime.request(USER, AccountRecoveryProof.ALPHABET)
        val original = requireNotNull(h.pending())
        h.clock = h.clock.plusSeconds(121); h.resolution = h.negative(original)
        val replacement = JsonObject(original + ("binding_commit_id" to JsonPrimitive(OTHER)))
        h.beforeReceiptReturn = { AccountRecoveryPendingStore(h.store).write(AccountRecoverySlot.DELETION_SOURCE, replacement) }
        runtime.resume()
        assertEquals(replacement, h.pending())
        assertEquals(AccountDeletionSourceState.Idle, runtime.state.value)
    }

    private class Harness {
        val keys = RecoveryFixtureKeys()
        val profile = ServerProfileId(SERVER)
        val identity = SelfPairingIdentity(SERVER, 1, keys.publicKeyThumbprintSha256("server"), "https://api.test.invalid", "https://stream.test.invalid")
        val initial = NonSecretSettings(profile, UserId(USER), DeviceId(DEVICE), identity.apiOrigin, identity.streamOrigin,
            m5Binding = M5BindingCheckpoint(COMMIT, SERVER, 1, identity.thumbprint, "old-key", SESSION, SESSION, 0))
        val envelope = SessionCredentialEnvelope("original-bearer", "original-refresh", 0,
            bindingCommitId = COMMIT, sessionId = SESSION, sessionFamilyId = SESSION, sessionGeneration = 0)
        val settings = MemorySettings(initial)
        val store = MemoryStore().also { it.values[profile] = SessionCredentialEnvelopeCodec.encode(envelope) }
        var clock = Instant.parse("2026-09-18T12:00:00Z")
        var allowed = true; var accept = true; var loseReply = false; var dieBeforeDetach = false
        var denialReason = "last_owner_required"
        var rotateDuringStatus = false; var purges = 0; var sentAccess: String? = null
        var receipt: JsonObject? = null
        var resolution: JsonObject? = null
        var beforeReceiptReturn: (suspend () -> Unit)? = null
        val calls = mutableListOf<String>(); val requests = mutableListOf<String>(); val resolutions = mutableListOf<String>()
        suspend fun pending() = AccountRecoveryPendingStore(store).read(AccountRecoverySlot.DELETION_SOURCE)
        fun negative(pending: JsonObject): JsonObject {
            val raw = java.util.Base64.getDecoder().decode(pending.text("request_b64"))
            val request = try { SelfPairingJson.parse(raw) } finally { raw.fill(0) }
            return buildJsonObject {
                put("contract_version", "v1"); put("schema_version", 1); put("account_id", USER)
                put("deletion_request_id", request.text("operation_id")); put("request_sha256", request.text("request_sha256"))
                put("state", "NOT_ACCEPTED"); put("resolved_at", clock.toString()); put("replayed", false)
            }
        }
        suspend fun ready() = runtime().also { it.load(); assertTrue(it.state.value is AccountDeletionSourceState.Ready) }
        fun runtime() = AccountDeletionSourceRuntime(store, SettingsSelfPairingSourceContext(settings, store),
            SettingsAccountDeletionBindingPort(settings, store, {
                if (dieBeforeDetach) { dieBeforeDetach = false; throw CancellationException("death before detach") }
                purges++
            }), keys, transport, "Deletion phone", "test-1", { clock })
        private val transport = object : AccountDeletionTransport {
            override suspend fun status(identity: SelfPairingIdentity, profile: ServerProfileId): JsonObject {
                if (rotateDuringStatus) {
                    rotateDuringStatus = false
                    store.values[profile] = SessionCredentialEnvelopeCodec.encode(envelope.copy(accessToken = "rotated-bearer", generation = 1,
                        sessionGeneration = 1))
                    settings.settings.value = initial.copy(m5Binding = initial.m5Binding!!.copy(sessionGeneration = 1))
                }
                return buildJsonObject {
                    put("contract_version", "v1"); put("schema_version", 1); put("account_id", USER)
                    put("authority_generation", 1); put("code_generation", 1); put("can_request", allowed)
                    put("reason", if (allowed) JsonNull else JsonPrimitive(denialReason))
                }
            }
            override suspend fun receipt(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject {
                calls += "receipt"
                val result = receipt ?: throw AccountRecoveryRemoteFailure("account_deletion_unavailable")
                beforeReceiptReturn?.invoke()
                return result
            }
            override suspend fun resolve(identity: SelfPairingIdentity, code: ByteArray, request: ByteArray): JsonObject {
                calls += "resolve"; resolutions += request.toString(Charsets.UTF_8)
                val result = resolution ?: throw AccountRecoveryRemoteFailure("account_deletion_unavailable")
                beforeReceiptReturn?.invoke()
                return result
            }
            override suspend fun request(identity: SelfPairingIdentity, access: ByteArray, code: ByteArray, request: ByteArray): JsonObject {
                calls += "request"; requests += request.toString(Charsets.UTF_8); sentAccess = access.toString(Charsets.US_ASCII)
                assertNull(settings.settings.value.m5Binding); assertNull(store.values[profile])
                assertEquals(AccountRecoveryProof.ALPHABET, code.toString(Charsets.US_ASCII))
                if (!accept) throw AccountRecoveryRemoteFailure("authentication_required")
                val body = SelfPairingJson.parse(request)
                receipt = buildJsonObject {
                    put("contract_version", "v1"); put("schema_version", 1); put("account_id", USER)
                    put("deletion_request_id", body.text("operation_id")); put("state", "PENDING"); put("revision", 1)
                    put("requested_at", clock.toString()); put("cancel_before", clock.plusSeconds(30 * 86400).toString()); put("replayed", false)
                }
                if (loseReply) { loseReply = false; throw IOException("lost response") }
                return requireNotNull(receipt)
            }
            override suspend fun cancel(identity: SelfPairingIdentity, kind: String, code: ByteArray, request: ByteArray): JsonObject = error("unexpected")
        }
    }
    private class MemorySettings(initial: NonSecretSettings) : NonSecretSettingsStore {
        override val settings = MutableStateFlow(initial)
        override suspend fun update(settings: NonSecretSettings) { this.settings.value = settings }
        override suspend fun mutate(transform: (NonSecretSettings) -> NonSecretSettings) { settings.value = transform(settings.value) }
    }
    private class MemoryStore : CredentialStore {
        val values = mutableMapOf<ServerProfileId, ByteArray>()
        var readFailure: Exception? = null
        var ignoreDeletionClear = false
        override suspend fun read(profileId: ServerProfileId): ByteArray? {
            if (profileId == AccountRecoverySlot.DELETION_SOURCE.profile) readFailure?.let { throw it }
            return values[profileId]?.copyOf()
        }
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { values[profileId] = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) {
            if (ignoreDeletionClear && profileId == AccountRecoverySlot.DELETION_SOURCE.profile) return
            values.remove(profileId)?.fill(0)
        }
        override suspend fun hasPublicAccessPendingRegistration() = false
    }
    private companion object {
        const val SERVER = "10000000-0000-4000-8000-000000000001"
        const val USER = "10000000-0000-4000-8000-000000000003"
        const val DEVICE = "10000000-0000-4000-8000-000000000004"
        const val SESSION = "10000000-0000-4000-8000-000000000005"
        const val COMMIT = "10000000-0000-4000-8000-000000000006"
        const val OTHER = "10000000-0000-4000-8000-000000000009"
    }
}
