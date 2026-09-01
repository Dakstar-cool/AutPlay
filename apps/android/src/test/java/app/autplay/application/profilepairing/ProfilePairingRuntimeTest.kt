package app.autplay.application.profilepairing

import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.M5TrustEvidence
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.time.Instant
import java.util.Base64
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfilePairingRuntimeTest {
    @Test
    fun publicReservationPreventsOrdinaryM5DiscoveryFromStarting() = runBlocking {
        val fixture = Fixture()
        assertTrue(fixture.firstBindGate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS))

        fixture.runtime.startDiscovery(API_ORIGIN).join()

        assertEquals(0, fixture.port.discoveryCalls)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.NotConnected)
    }

    @Test
    fun processDeathPublicPendingPreventsOrdinaryM5AndPreservesExactReplayEnvelope() = runBlocking {
        val credentials = FakeCredentials()
        credentials.write(PROFILE, publicPendingEnvelope())
        val before = requireNotNull(credentials.read(PROFILE))
        val restarted = Fixture(credentials = credentials)

        restarted.runtime.startDiscovery(API_ORIGIN).join()

        assertEquals(0, restarted.port.discoveryCalls)
        assertTrue(restarted.runtime.state.value.pairing is PairingState.Blocked)
        val after = requireNotNull(credentials.read(PROFILE))
        try {
            assertArrayEquals(before, after)
        } finally {
            before.fill(0)
            after.fill(0)
        }
    }
    @Test
    fun explicitTrustRegistersDiscoveryOriginForAdmission() = runBlocking {
        val fixture = Fixture()

        fixture.runtime.startDiscovery(API_ORIGIN).join()

        assertTrue(fixture.registeredOrigins.isEmpty())
        fixture.runtime.confirmTrust()
        val snapshot = fixture.runtime.state.value.pairing as PairingState.AwaitingTrust
        assertEquals(
            listOf(snapshot.snapshot.serverProfileId to API_ORIGIN),
            fixture.registeredOrigins,
        )
    }

    @Test
    fun exchangeIsDeniedBeforeExplicitTrust() = runBlocking {
        val fixture = Fixture()
        fixture.runtime.startDiscovery(API_ORIGIN).join()
        fixture.runtime.exchangeInvitation(invitation()).join()
        assertEquals(0, fixture.port.exchangeCalls)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.AwaitingTrust)
    }

    @Test
    fun delayedDiscoveryCannotOverwriteCancelledState() = runBlocking {
        val gate = CompletableDeferred<Unit>()
        val fixture = Fixture(FakePort(discoveryGate = gate))
        val discovery = fixture.runtime.startDiscovery(API_ORIGIN)
        fixture.runtime.cancel()
        gate.complete(Unit)
        discovery.join()
        assertEquals(PairingState.Cancelled, fixture.runtime.state.value.pairing)
    }

    @Test
    fun expiredInvitationNeverReachesTransport() = runBlocking {
        val fixture = Fixture()
        fixture.runtime.startDiscovery(API_ORIGIN).join()
        fixture.runtime.confirmTrust()
        fixture.runtime.exchangeInvitation(invitation(expiresAt = "2000-01-01T00:00:00Z")).join()
        assertEquals(0, fixture.port.exchangeCalls)
        assertEquals(
            PairingState.Blocked(PairingFailure.AUTH_ATTENTION_REQUIRED),
            fixture.runtime.state.value.pairing,
        )
    }

    @Test
    fun accountDeviceAndLocalChoiceAreConfirmedBeforeExchange() = runBlocking {
        val fixture = Fixture()
        fixture.runtime.startDiscovery(API_ORIGIN).join()
        fixture.runtime.confirmTrust()

        fixture.runtime.exchangeInvitation(invitation()).join()

        assertEquals(0, fixture.port.exchangeCalls)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.AwaitingConfirmation)
        assertEquals("Owner", fixture.runtime.state.value.accountLabel)
        assertEquals("Test device", fixture.runtime.state.value.deviceLabel)

        fixture.runtime.chooseLocalData(review = false).join()

        assertEquals(1, fixture.port.exchangeCalls)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals("KEEP_LOCAL", fixture.settings.value.m5LocalDataDecision)
    }

    @Test
    fun coldAdmissionRecoveryReestablishesTrustAndPersistsOrdinaryM5Binding() = runBlocking {
        val fixture = Fixture(FakePort(wipeSeedInput = true))
        val checkpoint = AdmissionCheckpoint(
            requestId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            requestSha256 = "c".repeat(64),
            serverProfileId = PROFILE,
            serverInstanceId = SERVER_INSTANCE_ID,
            identityEpoch = 1,
            identityThumbprintSha256 = IDENTITY_THUMBPRINT,
            deviceKeyThumbprintSha256 = DEVICE_THUMBPRINT,
            generationId = GENERATION_ID,
            apiOrigin = API_ORIGIN,
            streamOrigin = STREAM_ORIGIN,
        )

        assertTrue(fixture.runtime.recoverAdmissionTrust(checkpoint))
        assertTrue(fixture.port.identitySeeded)
        assertTrue(fixture.runtime.state.value.trustConfirmed)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.AwaitingTrust)

        val stored = fixture.runtime.completeAdmissionEnrollment(
            checkpoint,
            AdmissionAccount(USER, "Owner"),
            BINDING_COMMIT_ID,
            EnrollmentSession(
                DEVICE,
                SESSION_ID,
                SESSION_FAMILY_ID,
                0,
                "access".toByteArray(StandardCharsets.US_ASCII),
                REFRESH_TOKEN.copyOf(),
            ),
        )

        assertTrue(stored)
        assertEquals(PROFILE, fixture.settings.value.activeServerProfileId)
        assertEquals(USER, fixture.settings.value.activeUserId)
        assertEquals(DEVICE, fixture.settings.value.deviceId)
        assertEquals(BINDING_COMMIT_ID, fixture.settings.value.m5Binding?.bindingCommitId)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals(
            Base64.getEncoder().encodeToString(IDENTITY_SPKI),
            fixture.settings.value.m5TrustEvidence?.identityPublicKeySpkiB64,
        )
    }

    @Test
    fun cancellingDelayedColdAdmissionTrustRecoveryCannotRestorePairing() = runBlocking {
        val gate = CompletableDeferred<Unit>()
        val fixture = Fixture(FakePort(discoveryGate = gate))
        val checkpoint = admissionCheckpoint()

        val recovery = async(start = CoroutineStart.UNDISPATCHED) {
            fixture.runtime.recoverAdmissionTrust(checkpoint)
        }
        fixture.runtime.cancel()
        gate.complete(Unit)

        assertFalse(recovery.await())
        assertEquals(PairingState.Cancelled, fixture.runtime.state.value.pairing)
    }

    @Test
    fun profileCancellationClearsAdmissionCheckpointAndDeletesItsExactKey() = runBlocking {
        val checkpoint = admissionCheckpoint()
        val fixture = Fixture(
            initialSettings = NonSecretSettings(
                m5AdmissionCheckpoint = AdmissionCheckpointCodec.encode(checkpoint),
            ),
        )

        fixture.runtime.cancel()

        assertNull(fixture.settings.value.m5AdmissionCheckpoint)
        assertTrue(KEY_ALIAS in fixture.keys.deletedAliases)
    }

    @Test
    fun committedBindingWinsOverCrashStaleAdmissionCheckpointOnRestart() = runBlocking {
        val checkpoint = admissionCheckpoint()
        val encoded = AdmissionCheckpointCodec.encode(checkpoint)
        val binding = M5BindingCheckpoint(
            BINDING_COMMIT_ID,
            SERVER_INSTANCE_ID,
            1,
            IDENTITY_THUMBPRINT,
            KEY_ALIAS,
            SESSION_ID,
            SESSION_FAMILY_ID,
            0,
        )
        val fixture = Fixture(
            initialSettings = NonSecretSettings(
                activeServerProfileId = PROFILE,
                activeUserId = USER,
                deviceId = DEVICE,
                serverBaseUrl = API_ORIGIN,
                streamBaseUrl = STREAM_ORIGIN,
                m5Binding = binding,
                m5TrustEvidence = M5TrustEvidence(
                    identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(IDENTITY_SPKI),
                    serverLabelHint = "Test server",
                ),
                m5LocalDataDecision = "KEEP_LOCAL",
                m5AdmissionCheckpoint = encoded,
            ),
        )
        fixture.credentials.write(
            PROFILE,
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = "access",
                    refreshToken = REFRESH_TOKEN.toString(StandardCharsets.US_ASCII),
                    generation = 0,
                    bindingCommitId = BINDING_COMMIT_ID,
                    sessionId = SESSION_ID,
                    sessionFamilyId = SESSION_FAMILY_ID,
                    sessionGeneration = 0,
                ),
            ),
        )
        val bootstrap = AdmissionRecoveryBootstrap(encoded, preferExistingBinding = true)

        assertTrue(
            bootstrap.restoreExistingBindingIfPresent(fixture.runtime) {
                fixture.settings.update(fixture.settings.value.copy(m5AdmissionCheckpoint = null))
            },
        )

        assertNull(fixture.settings.value.m5AdmissionCheckpoint)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals(BINDING_COMMIT_ID, fixture.settings.value.m5Binding?.bindingCommitId)
    }

    @Test
    fun cancelWhilePendingSecretWriteCannotPublishOrReplayBinding() = runBlocking {
        val gatedCredentials = GatedCredentials()
        val fixture = Fixture(credentials = gatedCredentials)
        fixture.runtime.startDiscovery(API_ORIGIN).join()
        fixture.runtime.confirmTrust()
        fixture.runtime.exchangeInvitation(invitation()).join()

        val enrollment = async { fixture.runtime.chooseLocalData(review = false).join() }
        gatedCredentials.writeStarted.await()
        fixture.runtime.cancel()
        gatedCredentials.releaseWrite.complete(Unit)
        enrollment.await()

        assertEquals(PairingState.Cancelled, fixture.runtime.state.value.pairing)
        assertEquals(0, fixture.port.exchangeCalls)
        assertNull(fixture.settings.value.m5PendingExchangeCheckpoint)
        assertTrue(fixture.settings.value.m5CancelledPairingGenerationId != null)
        assertNull(gatedCredentials.read(fixture.settings.value.activeServerProfileId ?: PROFILE))

        fixture.runtime.recoverAndRefresh()
        assertEquals(0, fixture.port.exchangeCalls)
        assertNull(fixture.settings.value.m5Binding)
    }

    @Test
    fun cancelAfterBindingCommitRollsBackMatchingActiveBinding() = runBlocking {
        val releaseCapabilities = CompletableDeferred<Unit>()
        val port = FakePort(capabilitiesGate = releaseCapabilities)
        val fixture = Fixture(port)
        fixture.runtime.startDiscovery(API_ORIGIN).join()
        fixture.runtime.confirmTrust()
        fixture.runtime.exchangeInvitation(invitation()).join()

        val enrollment = async { fixture.runtime.chooseLocalData(review = false).join() }
        port.capabilitiesStarted.await()
        val activeProfile = requireNotNull(fixture.settings.value.activeServerProfileId)
        val activeAlias = requireNotNull(fixture.settings.value.m5Binding).deviceKeyAlias

        fixture.runtime.cancel()
        releaseCapabilities.complete(Unit)
        enrollment.await()

        assertEquals(PairingState.Cancelled, fixture.runtime.state.value.pairing)
        assertNull(fixture.settings.value.activeServerProfileId)
        assertNull(fixture.settings.value.activeUserId)
        assertNull(fixture.settings.value.deviceId)
        assertNull(fixture.settings.value.m5Binding)
        assertNull(fixture.settings.value.m5TrustEvidence)
        assertNull(fixture.settings.value.m5LocalDataDecision)
        assertNull(fixture.credentials.read(activeProfile))
        assertTrue(activeAlias in fixture.keys.deletedAliases)
    }

    @Test
    fun matchingPendingExchangeReplaysExactlyAndPromotesBinding() = runBlocking {
        val pending = pendingFixture()
        val fixture = Fixture(initialSettings = pending.settings)
        fixture.credentials.write(PROFILE, SessionCredentialEnvelopeCodec.encode(pending.envelope))
        fixture.runtime.recoverAndRefresh()
        val replay = requireNotNull(fixture.port.lastExchange)
        assertEquals(EXCHANGE_ID, replay.snapshot.operationId)
        assertEquals(BINDING_COMMIT_ID, replay.snapshot.bindingCommitId)
        assertEquals("MDEyMzQ1Njc4OWFiY2RlZg", replay.clientNonceB64Url)
        assertArrayEquals(REFRESH_TOKEN, replay.nextRefreshToken)
        assertEquals(INVITATION_ID, replay.invitationId)
        assertTrue(fixture.port.identitySeeded)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        val stored = fixture.settings.value
        assertEquals(PROFILE, stored.activeServerProfileId)
        assertEquals(DEVICE, stored.deviceId)
        assertNull(stored.m5PendingExchangeCheckpoint)
        assertEquals(BINDING_COMMIT_ID, stored.m5Binding?.bindingCommitId)
        assertEquals("KEEP_LOCAL", stored.m5LocalDataDecision)
        assertEquals(1L, stored.m5TrustEvidence?.capabilityRevisionHighWater)
    }

    @Test
    fun mismatchedPendingExchangeClearsBothStoresFailClosed() = runBlocking {
        val pending = pendingFixture()
        val fixture = Fixture(initialSettings = pending.settings)
        val mismatched = pending.envelope.copy(m5PendingExchangeId = OTHER_OPERATION_ID)
        fixture.credentials.write(PROFILE, SessionCredentialEnvelopeCodec.encode(mismatched))
        fixture.runtime.recoverAndRefresh()
        assertNull(fixture.credentials.read(PROFILE))
        assertNull(fixture.settings.value.m5PendingExchangeCheckpoint)
        assertNull(fixture.settings.value.m5TrustEvidence)
        assertNull(fixture.settings.value.m5Binding)
        assertEquals(
            PairingState.Blocked(PairingFailure.AUTH_ATTENTION_REQUIRED),
            fixture.runtime.state.value.pairing,
        )
        assertEquals(0, fixture.port.exchangeCalls)
    }

    @Test
    fun successorCredentialPromotesStaleRotationCheckpointAfterProcessDeath() = runBlocking {
        val parent = M5BindingCheckpoint(
            BINDING_COMMIT_ID,
            SERVER_INSTANCE_ID,
            1,
            IDENTITY_THUMBPRINT,
            KEY_ALIAS,
            SESSION_ID,
            SESSION_FAMILY_ID,
            0,
        )
        val fixture = Fixture(
            initialSettings = NonSecretSettings(
                activeServerProfileId = PROFILE,
                activeUserId = USER,
                deviceId = DEVICE,
                serverBaseUrl = API_ORIGIN,
                streamBaseUrl = STREAM_ORIGIN,
                m5Binding = parent,
                m5TrustEvidence = M5TrustEvidence(
                    identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(IDENTITY_SPKI),
                    serverLabelHint = "Test server",
                ),
                m5LocalDataDecision = "KEEP_LOCAL",
            ),
        )
        fixture.credentials.write(
            PROFILE,
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = "successor-access",
                    refreshToken = REFRESH_TOKEN.toString(StandardCharsets.US_ASCII),
                    generation = 1,
                    bindingCommitId = BINDING_COMMIT_ID,
                    sessionId = SUCCESSOR_SESSION_ID,
                    sessionFamilyId = SESSION_FAMILY_ID,
                    sessionGeneration = 1,
                ),
            ),
        )

        fixture.runtime.recoverAndRefresh()

        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals(SUCCESSOR_SESSION_ID, fixture.settings.value.m5Binding?.sessionId)
        assertEquals(1L, fixture.settings.value.m5Binding?.sessionGeneration)
    }

    @Test
    fun coldRecoveryRotatesRejectedAccessTokenAndRetriesCapabilitiesOnce() = runBlocking {
        val binding = M5BindingCheckpoint(
            BINDING_COMMIT_ID,
            SERVER_INSTANCE_ID,
            1,
            IDENTITY_THUMBPRINT,
            KEY_ALIAS,
            SESSION_ID,
            SESSION_FAMILY_ID,
            0,
        )
        val port = FakePort(
            initialCapabilitiesFailure = "authentication_required",
            rotationSession = EnrollmentSession(
                DEVICE,
                SUCCESSOR_SESSION_ID,
                SESSION_FAMILY_ID,
                1,
                "rotated-access-token".toByteArray(StandardCharsets.US_ASCII),
                REFRESH_TOKEN.copyOf(),
            ),
        )
        val fixture = Fixture(
            port = port,
            initialSettings = NonSecretSettings(
                activeServerProfileId = PROFILE,
                activeUserId = USER,
                deviceId = DEVICE,
                serverBaseUrl = API_ORIGIN,
                streamBaseUrl = STREAM_ORIGIN,
                m5Binding = binding,
                m5TrustEvidence = M5TrustEvidence(
                    identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(IDENTITY_SPKI),
                    serverLabelHint = "Test server",
                    capabilitySignedPayloadB64 = Base64.getEncoder().encodeToString("old".toByteArray()),
                    capabilityPayloadSha256 = "0".repeat(64),
                    capabilityRevisionHighWater = 1,
                ),
                m5LocalDataDecision = "KEEP_LOCAL",
            ),
        )
        fixture.credentials.write(
            PROFILE,
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = "expired-access-token",
                    refreshToken = REFRESH_TOKEN.toString(StandardCharsets.US_ASCII),
                    generation = 0,
                    bindingCommitId = BINDING_COMMIT_ID,
                    sessionId = SESSION_ID,
                    sessionFamilyId = SESSION_FAMILY_ID,
                    sessionGeneration = 0,
                ),
            ),
        )

        fixture.runtime.recoverAndRefresh()

        assertEquals(1, port.rotateCalls)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals(SUCCESSOR_SESSION_ID, fixture.settings.value.m5Binding?.sessionId)
        assertEquals(1L, fixture.settings.value.m5Binding?.sessionGeneration)
        assertEquals(1L, fixture.settings.value.m5TrustEvidence?.capabilityRevisionHighWater)
    }

    @Test
    fun pendingReviewSelectionIsMaterializedAfterProcessDeathThenMarkerClears() = runBlocking {
        val materializer = RecordingMaterializer()
        val binding = M5BindingCheckpoint(
            BINDING_COMMIT_ID,
            SERVER_INSTANCE_ID,
            1,
            IDENTITY_THUMBPRINT,
            KEY_ALIAS,
            SESSION_ID,
            SESSION_FAMILY_ID,
            0,
        )
        val fixture = Fixture(
            initialSettings = NonSecretSettings(
                activeServerProfileId = PROFILE,
                activeUserId = USER,
                deviceId = DEVICE,
                serverBaseUrl = API_ORIGIN,
                streamBaseUrl = STREAM_ORIGIN,
                m5Binding = binding,
                m5TrustEvidence = M5TrustEvidence(
                    identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(IDENTITY_SPKI),
                    serverLabelHint = "Test server",
                ),
                m5LocalDataDecision = "REVIEW_SELECTED",
            ),
            materializer = materializer,
        )
        fixture.credentials.write(
            PROFILE,
            SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = "access",
                    refreshToken = REFRESH_TOKEN.toString(StandardCharsets.US_ASCII),
                    generation = 0,
                    bindingCommitId = BINDING_COMMIT_ID,
                    sessionId = SESSION_ID,
                    sessionFamilyId = SESSION_FAMILY_ID,
                    sessionGeneration = 0,
                    m5PendingMaterializationRequest = materializationMarker(),
                ),
            ),
        )

        fixture.runtime.recoverAndRefresh()

        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        assertEquals(listOf(LocalId(LOCAL_CHANGE_ID)), materializer.calls)
        val stored = requireNotNull(fixture.credentials.read(PROFILE))
        val decoded = try {
            SessionCredentialEnvelopeCodec.decode(stored)
        } finally {
            stored.fill(0)
        }
        assertNull(decoded.m5PendingMaterializationRequest)
    }

    @Test
    fun publicRegistrationEvidenceClearsOnlyAfterDurableBinding() = runBlocking {
        val fixture = Fixture()
        assertTrue(fixture.firstBindGate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS))
        fixture.credentials.write(PROFILE, publicPendingEnvelope())

        assertTrue(
            fixture.runtime.completePublicAccountRegistration(
                publicSnapshot(),
                KEY_ALIAS,
                publicSession(),
                IDENTITY_SPKI.copyOf(),
            ),
        )

        assertEquals(BINDING_COMMIT_ID, fixture.settings.value.m5Binding?.bindingCommitId)
        assertTrue(fixture.runtime.state.value.pairing is PairingState.Connected)
        val material = requireNotNull(fixture.credentials.read(PROFILE))
        val stored = try { SessionCredentialEnvelopeCodec.decode(material) } finally { material.fill(0) }
        assertFalse(stored.refreshPending)
        assertNull(stored.publicAccessPendingRegistrationId)
        assertNull(stored.publicAccessPendingCanonicalRequest)
        assertNull(stored.publicAccessPendingSuccessorRefreshToken)
    }

    @Test
    fun failedPostBindingCleanupKeepsReplayEvidenceAndDurableBinding() = runBlocking {
        val credentials = FailOnThirdWriteCredentials()
        val fixture = Fixture(credentials = credentials)
        assertTrue(fixture.firstBindGate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS))
        fixture.credentials.write(PROFILE, publicPendingEnvelope())

        assertFalse(
            fixture.runtime.completePublicAccountRegistration(
                publicSnapshot(),
                KEY_ALIAS,
                publicSession(),
                IDENTITY_SPKI.copyOf(),
            ),
        )

        assertEquals(BINDING_COMMIT_ID, fixture.settings.value.m5Binding?.bindingCommitId)
        val material = requireNotNull(fixture.credentials.read(PROFILE))
        val stored = try { SessionCredentialEnvelopeCodec.decode(material) } finally { material.fill(0) }
        assertTrue(stored.refreshPending)
        assertEquals(INVITATION_ID, stored.publicAccessPendingRegistrationId)
        assertTrue(KEY_ALIAS !in fixture.keys.deletedAliases)

        val restarted = Fixture(
            initialSettings = fixture.settings.value,
            credentials = credentials,
        )
        restarted.runtime.recoverAndRefresh()

        assertTrue(restarted.runtime.state.value.pairing is PairingState.Connected)
        val cleanedMaterial = requireNotNull(credentials.read(PROFILE))
        val cleaned = try {
            SessionCredentialEnvelopeCodec.decode(cleanedMaterial)
        } finally {
            cleanedMaterial.fill(0)
        }
        assertFalse(cleaned.refreshPending)
        assertNull(cleaned.publicAccessPendingRegistrationId)
        assertNull(cleaned.publicAccessPendingCanonicalRequest)
        assertNull(cleaned.publicAccessPendingSuccessorRefreshToken)
    }

    private fun publicPendingEnvelope() = SessionCredentialEnvelopeCodec.encode(
        SessionCredentialEnvelope(
            accessToken = "pending-account-registration",
            refreshToken = null,
            generation = 0,
            refreshPending = true,
            publicAccessPendingRegistrationId = INVITATION_ID,
            publicAccessPendingCanonicalRequest = "e30",
            publicAccessPendingSuccessorRefreshToken = "r".repeat(43),
        ),
    )

    private fun publicSnapshot() = PairingFlowSnapshot(
        GENERATION_ID,
        API_ORIGIN,
        STREAM_ORIGIN,
        PROFILE,
        SERVER_INSTANCE_ID,
        1,
        IDENTITY_THUMBPRINT,
        USER,
        DEVICE,
        DEVICE_THUMBPRINT,
        null,
        BINDING_COMMIT_ID,
    )

    private fun publicSession() = EnrollmentSession(
        DEVICE,
        SESSION_ID,
        SESSION_FAMILY_ID,
        0,
        "access".toByteArray(),
        REFRESH_TOKEN.copyOf(),
    )

    private class Fixture(
        val port: FakePort = FakePort(),
        initialSettings: NonSecretSettings = NonSecretSettings(),
        val credentials: CredentialStore = FakeCredentials(),
        materializer: M5LocalIntentMaterializer = EmptyMaterializer,
    ) {
        val settings = FakeSettings(initialSettings)
        val keys = FakeKeys()
        val registeredOrigins = mutableListOf<Pair<ServerProfileId, String>>()
        val firstBindGate = FirstBindCeremonyGate()
        val runtime = ProfilePairingRuntime(
            scope = CoroutineScope(Dispatchers.Unconfined),
            settings = settings,
            credentials = credentials,
            deviceKeys = keys,
            port = port,
            materialization = M5BindingMaterializationCoordinator(settings, credentials, keys, materializer),
            deviceName = "Test device",
            reportSafeError = {},
            registerOrigin = { profile, origin -> registeredOrigins += profile to origin },
            firstBindGate = firstBindGate,
        )
    }

    private object EmptyMaterializer : M5LocalIntentMaterializer {
        override suspend fun pending(limit: Int) = emptyList<PendingLocalIntentSummary>()
        override suspend fun materialize(
            binding: ClientEventBinding,
            localChangeId: LocalId,
            eventId: LocalId,
            materializedAtMs: Long,
        ) = eventId
    }

    private class RecordingMaterializer : M5LocalIntentMaterializer {
        val calls = mutableListOf<LocalId>()
        override suspend fun pending(limit: Int) = emptyList<PendingLocalIntentSummary>()
        override suspend fun materialize(
            binding: ClientEventBinding,
            localChangeId: LocalId,
            eventId: LocalId,
            materializedAtMs: Long,
        ): LocalId {
            calls += localChangeId
            return eventId
        }
    }

    private class FakeSettings(initial: NonSecretSettings) : NonSecretSettingsStore {
        private val state = MutableStateFlow(initial)
        override val settings: Flow<NonSecretSettings> = state
        val value: NonSecretSettings get() = state.value
        override suspend fun update(settings: NonSecretSettings) { state.value = settings }
    }

    private class FakeCredentials : CredentialStore {
        private val values = mutableMapOf<ServerProfileId, ByteArray>()
        override suspend fun read(profileId: ServerProfileId) = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            values[profileId] = material.copyOf()
        }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = values.values.any {
            SessionCredentialEnvelopeCodec.decode(it).publicAccessPendingRegistrationId != null
        }
    }

    private class GatedCredentials : CredentialStore {
        private val values = mutableMapOf<ServerProfileId, ByteArray>()
        val writeStarted = CompletableDeferred<Unit>()
        val releaseWrite = CompletableDeferred<Unit>()

        override suspend fun read(profileId: ServerProfileId) = values[profileId]?.copyOf()

        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            writeStarted.complete(Unit)
            releaseWrite.await()
            values[profileId] = material.copyOf()
        }

        override suspend fun clear(profileId: ServerProfileId) {
            values.remove(profileId)?.fill(0)
        }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = values.values.any {
            SessionCredentialEnvelopeCodec.decode(it).publicAccessPendingRegistrationId != null
        }
    }

    private class FailOnThirdWriteCredentials : CredentialStore {
        private var value: ByteArray? = null
        private var writes = 0
        override suspend fun read(profileId: ServerProfileId) = value?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            writes += 1
            if (writes == 3) error("simulated process death after binding")
            value?.fill(0)
            value = material.copyOf()
        }
        override suspend fun clear(profileId: ServerProfileId) {
            value?.fill(0)
            value = null
        }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = value?.let {
            SessionCredentialEnvelopeCodec.decode(it).publicAccessPendingRegistrationId != null
        } ?: false
    }

    private class FakeKeys : M5DeviceKeyStore {
        val deletedAliases = mutableSetOf<String>()
        override fun publicKeySpki(alias: String) = IDENTITY_SPKI.copyOf()
        override fun publicKeyThumbprintSha256(alias: String) = DEVICE_THUMBPRINT
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = ByteArray(64)
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) { deletedAliases += alias }
    }

    private class FakePort(
        private val discoveryGate: CompletableDeferred<Unit>? = null,
        private val capabilitiesGate: CompletableDeferred<Unit>? = null,
        private val wipeSeedInput: Boolean = false,
        initialCapabilitiesFailure: String? = null,
        private val rotationSession: EnrollmentSession? = null,
    ) : ProfilePairingPort {
        var exchangeCalls = 0
        var discoveryCalls = 0
        var rotateCalls = 0
        var lastExchange: EnrollmentExchangeCommand? = null
        var identitySeeded = false
        private var capabilitiesFailure = initialCapabilitiesFailure
        val capabilitiesStarted = CompletableDeferred<Unit>()

        override fun seedTrustedIdentity(identity: TrustedServerIdentity, publicKeySpki: ByteArray) {
            identitySeeded = identity == IDENTITY && publicKeySpki.contentEquals(IDENTITY_SPKI)
            if (wipeSeedInput) publicKeySpki.fill(0)
        }
        override suspend fun discovery(apiOrigin: String): PairingNetworkResult<DiscoveryDocument> {
            discoveryCalls += 1
            discoveryGate?.await()
            return PairingNetworkResult.Success(
                DiscoveryDocument(
                    IDENTITY,
                    "Test server",
                    API_ORIGIN,
                    STREAM_ORIGIN,
                    setOf(1),
                    Instant.now().plusSeconds(300),
                    IDENTITY_SPKI.copyOf(),
                ),
            )
        }
        override suspend fun capabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot): PairingNetworkResult<CapabilityDocument> {
            capabilitiesStarted.complete(Unit)
            capabilitiesGate?.await()
            capabilitiesFailure?.let { code ->
                capabilitiesFailure = null
                return PairingNetworkResult.Failure(code)
            }
            return PairingNetworkResult.Success(
                CapabilityDocument(
                    CapabilityState(
                        IDENTITY,
                        USER,
                        DEVICE,
                        1,
                        1,
                        System.currentTimeMillis() + 300_000,
                        setOf("createEnrollmentInvitation"),
                    ),
                    "signed".toByteArray(StandardCharsets.US_ASCII),
                    "1".repeat(64),
                ),
            )
        }
        override suspend fun exchange(request: EnrollmentExchangeCommand): PairingNetworkResult<EnrollmentSession> {
            exchangeCalls += 1
            lastExchange = request.copy(
                invitationSecret = request.invitationSecret.copyOf(),
                nextRefreshToken = request.nextRefreshToken.copyOf(),
            )
            return PairingNetworkResult.Success(
                EnrollmentSession(
                    DEVICE,
                    SESSION_ID,
                    SESSION_FAMILY_ID,
                    0,
                    "access".toByteArray(StandardCharsets.US_ASCII),
                    request.nextRefreshToken.copyOf(),
                ),
            )
        }
        override suspend fun createInvitation(profileId: ServerProfileId, operationId: String, expiresInSeconds: Int) = PairingNetworkResult.Failure("server_unavailable")
        override suspend fun cancelInvitation(profileId: ServerProfileId, invitationId: String, operationId: String) = PairingNetworkResult.Failure("server_unavailable")
        override suspend fun rotate(request: SessionRotationCommand): PairingNetworkResult<EnrollmentSession> {
            rotateCalls += 1
            return rotationSession?.let { session ->
                PairingNetworkResult.Success(
                    session.copy(
                        accessToken = session.accessToken.copyOf(),
                        refreshToken = request.nextRefreshToken.copyOf(),
                    ),
                )
            } ?: PairingNetworkResult.Failure("server_unavailable")
        }
        override suspend fun devices(profileId: ServerProfileId) = PairingNetworkResult.Success(emptyList<DeviceSummary>())
        override suspend fun sessions(profileId: ServerProfileId) = PairingNetworkResult.Success(emptyList<SessionSummary>())
        override suspend fun lifecycle(profileId: ServerProfileId, command: LifecycleCommand) = PairingNetworkResult.Failure("server_unavailable")
    }

    private data class PendingFixture(
        val settings: NonSecretSettings,
        val envelope: SessionCredentialEnvelope,
    )

    private fun pendingFixture(): PendingFixture {
        val request = """{"invitation_id":"$INVITATION_ID","invitation_secret":"${Base64.getUrlEncoder().withoutPadding().encodeToString(INVITATION_SECRET)}","binding_commit_id":"$BINDING_COMMIT_ID","exchange_id":"$EXCHANGE_ID","generation_id":"$GENERATION_ID","client_nonce_b64url":"MDEyMzQ1Njc4OWFiY2RlZg","device_name":"Test device","next_refresh_token_sha256":"${"2".repeat(64)}","local_data_decision":"KEEP_LOCAL","selected_local_change_ids":[]}"""
        val checkpoint = listOf(
            "v1", PROFILE.value, GENERATION_ID, API_ORIGIN, STREAM_ORIGIN, SERVER_INSTANCE_ID, "1",
            IDENTITY_THUMBPRINT, USER.value, KEY_ALIAS, BINDING_COMMIT_ID, EXCHANGE_ID,
        ).joinToString("|")
        return PendingFixture(
            NonSecretSettings(
                m5TrustEvidence = M5TrustEvidence(
                    identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(IDENTITY_SPKI),
                    serverLabelHint = "Test server",
                ),
                m5PendingExchangeCheckpoint = checkpoint,
            ),
            SessionCredentialEnvelope(
                accessToken = "pending",
                refreshToken = null,
                generation = 0,
                refreshPending = true,
                m5PendingExchangeId = EXCHANGE_ID,
                m5PendingExchangeRequest = request,
                m5PendingExchangeSuccessorRefreshToken = REFRESH_TOKEN.toString(StandardCharsets.US_ASCII),
            ),
        )
    }

    private fun invitation(expiresAt: String = "2099-01-01T00:00:00Z") = """
        {"contract_version":"v1","schema_version":"1","invitation_id":"$INVITATION_ID",
        "invitation_secret":"${Base64.getUrlEncoder().withoutPadding().encodeToString(INVITATION_SECRET)}",
        "server_instance_id":"$SERVER_INSTANCE_ID","identity_epoch":"1",
        "identity_thumbprint_sha256":"$IDENTITY_THUMBPRINT","api_origin":"$API_ORIGIN",
        "stream_origin":"$STREAM_ORIGIN","user_id":"${USER.value}","account_display_name":"Owner",
        "expires_at":"$expiresAt","secret_handling":"DISPLAY_ONCE_NO_CLIPBOARD_NO_LOG_NO_EXPORT"}
    """.trimIndent()

    private fun admissionCheckpoint() = AdmissionCheckpoint(
        requestId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        requestSha256 = "c".repeat(64),
        serverProfileId = PROFILE,
        serverInstanceId = SERVER_INSTANCE_ID,
        identityEpoch = 1,
        identityThumbprintSha256 = IDENTITY_THUMBPRINT,
        deviceKeyThumbprintSha256 = DEVICE_THUMBPRINT,
        generationId = GENERATION_ID,
        apiOrigin = API_ORIGIN,
        streamOrigin = STREAM_ORIGIN,
    )

    private fun materializationMarker() = buildJsonObject {
        put("marker_version", 1)
        put("generation_id", GENERATION_ID)
        put("api_origin", API_ORIGIN)
        put("stream_origin", STREAM_ORIGIN)
        put("server_profile_id", PROFILE.value)
        put("server_instance_id", SERVER_INSTANCE_ID)
        put("identity_epoch", 1)
        put("identity_thumbprint_sha256", IDENTITY_THUMBPRINT)
        put("user_id", USER.value)
        put("device_id", DEVICE.value)
        put("device_key_thumbprint_sha256", DEVICE_THUMBPRINT)
        put("device_name", "Test device")
        put("exchange_id", EXCHANGE_ID)
        put("binding_commit_id", BINDING_COMMIT_ID)
        put("session_id", SESSION_ID)
        put("session_family_id", SESSION_FAMILY_ID)
        put("session_generation", 0)
        put("local_data_decision", "REVIEW_SELECTED")
        put("selected_local_change_ids", buildJsonArray {
            add(kotlinx.serialization.json.JsonPrimitive(LOCAL_CHANGE_ID))
        })
    }.toString()

    private companion object {
        const val SERVER_INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
        const val GENERATION_ID = "22222222-2222-4222-8222-222222222222"
        const val BINDING_COMMIT_ID = "33333333-3333-4333-8333-333333333333"
        const val EXCHANGE_ID = "44444444-4444-4444-8444-444444444444"
        const val OTHER_OPERATION_ID = "55555555-5555-4555-8555-555555555555"
        const val INVITATION_ID = "66666666-6666-4666-8666-666666666666"
        const val SESSION_ID = "77777777-7777-4777-8777-777777777777"
        const val SESSION_FAMILY_ID = "88888888-8888-4888-8888-888888888888"
        const val SUCCESSOR_SESSION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        const val API_ORIGIN = "https://server.example"
        const val STREAM_ORIGIN = "https://stream.example"
        const val IDENTITY_THUMBPRINT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        const val DEVICE_THUMBPRINT = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        const val KEY_ALIAS = "autplay.m5.99999999-9999-4999-8999-999999999999"
        const val LOCAL_CHANGE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        val PROFILE = ServerProfileId("99999999-9999-4999-8999-999999999999")
        val USER = UserId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        val DEVICE = DeviceId("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        val IDENTITY = TrustedServerIdentity(SERVER_INSTANCE_ID, 1, IDENTITY_THUMBPRINT)
        val IDENTITY_SPKI = ByteArray(64) { 7 }
        val INVITATION_SECRET = ByteArray(32) { 3 }
        val REFRESH_TOKEN = "r".repeat(43).toByteArray(StandardCharsets.US_ASCII)
    }
}
