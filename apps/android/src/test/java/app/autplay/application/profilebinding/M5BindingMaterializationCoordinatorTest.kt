package app.autplay.application.profilebinding

import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class M5BindingMaterializationCoordinatorTest {
    @Test
    fun selectedIntentCreatesOnlyNewEventsAfterCompleteCrossStoreRevalidation() = runBlocking {
        val fixture = Fixture()
        val selected = listOf(LocalId(uuid(21)), LocalId(uuid(22)))

        val results = fixture.coordinator.apply(fixture.consent(selected), nowMs = 50)

        assertEquals(2, results.size)
        assertEquals(selected, fixture.materializer.calls.map { it.second })
        assertEquals(listOf(PROFILE, PROFILE), fixture.materializer.calls.map { it.first.serverProfileId })
        assertEquals(emptySet<LocalId>(), results.toSet().intersect(selected.toSet()))
    }

    @Test
    fun bindingChangeBetweenItemsStopsBeforeTheNextRoomTransaction() = runBlocking {
        val fixture = Fixture()
        fixture.materializer.afterFirst = {
            fixture.settings.value = fixture.settings.value.copy(deviceId = DeviceId(uuid(99)))
        }

        val error = try {
            fixture.coordinator.apply(
                fixture.consent(listOf(LocalId(uuid(31)), LocalId(uuid(32)))),
                nowMs = 60,
            )
            null
        } catch (failure: M5MaterializationException) {
            failure
        }

        assertEquals(M5MaterializationErrorCode.MATERIALIZATION_BINDING_CHANGED, error?.code)
        assertEquals(1, fixture.materializer.calls.size)
    }

    @Test
    fun mismatchedSecretCommitMarkerCreatesNoEvent() = runBlocking {
        val fixture = Fixture(secretCommitId = uuid(88))

        val error = try {
            fixture.coordinator.apply(fixture.consent(listOf(LocalId(uuid(41)))), nowMs = 70)
            null
        } catch (failure: M5MaterializationException) {
            failure
        }

        assertEquals(M5MaterializationErrorCode.MATERIALIZATION_CREDENTIAL_MISSING, error?.code)
        assertEquals(0, fixture.materializer.calls.size)
    }

    @Test
    fun keepLocalChoiceNeverReadsOrWritesMaterializationState() = runBlocking {
        val fixture = Fixture()
        val consent = M5MaterializationConsent(
            fixture.snapshot,
            SESSION,
            LocalDataBindingDecision.KEEP_LOCAL,
            emptyList(),
        )

        assertEquals(emptyList<LocalId>(), fixture.coordinator.apply(consent, nowMs = 80))
        assertEquals(0, fixture.materializer.calls.size)
    }

    private class Fixture(secretCommitId: String = COMMIT) {
        val snapshot = PairingFlowSnapshot(
            generationId = uuid(1),
            apiOrigin = "https://api.example",
            streamOrigin = "https://stream.example",
            serverProfileId = PROFILE,
            expectedServerInstanceId = INSTANCE,
            expectedIdentityEpoch = 1,
            expectedIdentityThumbprintSha256 = HASH,
            expectedUserId = USER,
            expectedDeviceId = DEVICE,
            deviceKeyThumbprintSha256 = KEY_HASH,
            operationId = uuid(2),
            bindingCommitId = COMMIT,
        )
        val settings = MutableStateFlow(
            NonSecretSettings(
                activeServerProfileId = PROFILE,
                activeUserId = USER,
                deviceId = DEVICE,
                serverBaseUrl = snapshot.apiOrigin,
                streamBaseUrl = snapshot.streamOrigin,
                m5Binding = M5BindingCheckpoint(
                    COMMIT,
                    INSTANCE,
                    1,
                    HASH,
                    KEY_ALIAS,
                    SESSION,
                    FAMILY,
                    0,
                ),
            ),
        )
        private val credentials = FakeCredentialStore(
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    "access",
                    "refresh",
                    0,
                    bindingCommitId = secretCommitId,
                    sessionId = SESSION,
                    sessionFamilyId = FAMILY,
                    sessionGeneration = 0,
                ),
            ),
        )
        val materializer = FakeMaterializer()
        val coordinator = M5BindingMaterializationCoordinator(
            FakeSettingsStore(settings),
            credentials,
            FakeKeys,
            materializer,
        )

        fun consent(ids: List<LocalId>) = M5MaterializationConsent(
            snapshot,
            SESSION,
            LocalDataBindingDecision.REVIEW_SELECTED,
            ids,
        )
    }

    private class FakeSettingsStore(private val state: MutableStateFlow<NonSecretSettings>) :
        NonSecretSettingsStore {
        override val settings: Flow<NonSecretSettings> = state
        override suspend fun update(settings: NonSecretSettings) { state.value = settings }
    }

    private class FakeCredentialStore(private val value: ByteArray) : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray = value.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }

    private object FakeKeys : M5DeviceKeyStore {
        override fun publicKeySpki(alias: String) = byteArrayOf(1)
        override fun publicKeyThumbprintSha256(alias: String) = KEY_HASH
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) =
            ByteArray(64)
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) = Unit
    }

    private class FakeMaterializer : M5LocalIntentMaterializer {
        val calls = mutableListOf<Triple<ClientEventBinding, LocalId, LocalId>>()
        var afterFirst: (() -> Unit)? = null
        override suspend fun pending(limit: Int) = emptyList<PendingLocalIntentSummary>()
        override suspend fun materialize(
            binding: ClientEventBinding,
            localChangeId: LocalId,
            eventId: LocalId,
            materializedAtMs: Long,
        ): LocalId {
            calls += Triple(binding, localChangeId, eventId)
            if (calls.size == 1) afterFirst?.invoke()
            return eventId
        }
    }

    private companion object {
        fun uuid(seed: Int) = "00000000-0000-4000-8000-${seed.toString().padStart(12, '0')}"
        val PROFILE = ServerProfileId(uuid(3))
        val USER = UserId(uuid(4))
        val DEVICE = DeviceId(uuid(5))
        val INSTANCE = uuid(6)
        val COMMIT = uuid(7)
        val SESSION = uuid(8)
        val FAMILY = uuid(9)
        const val KEY_ALIAS = "autplay.m5.device.test"
        const val HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        const val KEY_HASH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
}
