package app.autplay.application.profilepairing

import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.security.MessageDigest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AdmissionRuntimeTest {
    @Test fun sasMatchesTheSharedRfc8785Vector() {
        val checkpoint = AdmissionCheckpoint(
            "77777777-7777-4777-8777-777777777777", "b".repeat(64),
            ServerProfileId("11111111-1111-4111-8111-111111111111"),
            "44444444-4444-4444-8444-444444444444", 1, "a".repeat(64), "c".repeat(64),
            "88888888-8888-4888-8888-888888888888", "https://example.test", "https://stream.example.test",
        )
        assertEquals("485545140262", AdmissionProof.sasDecimal12(checkpoint))
    }

    @Test fun pollBearerIsNotIncludedInCheckpointAndApprovalNeedsConfirmation() = runBlocking {
        val port = FakePort(); val persisted = MutableStateFlow<AdmissionCheckpoint?>(null)
        val runtime = AdmissionRuntime(CoroutineScope(Dispatchers.Unconfined), FakeKeys, port, { persisted.value = it })
        runtime.request(snapshot()).join()
        val comparing = runtime.state.value as AdmissionState.AwaitingComparison
        assertFalse(AdmissionCheckpointCodec.encode(requireNotNull(persisted.value)).contains("poll-secret"))
        runtime.confirmComparison(); runtime.poll().join()
        assertTrue(runtime.state.value is AdmissionState.Approved)
        assertEquals(0, port.exchangeCalls)
        runtime.confirmAccount().join()
        assertEquals(1, port.exchangeCalls); assertEquals(AdmissionState.Connected, runtime.state.value)
        assertEquals(null, persisted.value)
        assertTrue(comparing.sas.matches(Regex("\\d{12}")))
    }

    @Test fun stalePollCannotOverwriteCancelledState() = runBlocking {
        val port = FakePort(); val runtime = AdmissionRuntime(CoroutineScope(Dispatchers.Unconfined), FakeKeys, port, {})
        runtime.request(snapshot()).join(); runtime.confirmComparison(); runtime.cancel().join(); runtime.poll().join()
        assertEquals(AdmissionState.Cancelled, runtime.state.value)
    }

    @Test fun trustedReenrollmentUsesSameKeyAndTwoStepExchange() = runBlocking {
        val checkpoint = AdmissionCheckpoint("77777777-7777-4777-8777-777777777777", "a".repeat(64), ServerProfileId("22222222-2222-4222-8222-222222222222"), "33333333-3333-4333-8333-333333333333", 1, "a".repeat(64), "b".repeat(64), "88888888-8888-4888-8888-888888888888", "https://example.test", "https://stream.example.test")
        val runtime = TrustedReenrollmentRuntime(CoroutineScope(Dispatchers.Unconfined), FakeKeys, FakePort(), { _, _, _, _ -> true })
        runtime.reenroll(checkpoint, AdmissionAccount(UserId("44444444-4444-4444-8444-444444444444"), "Account")).join()
        assertEquals(TrustedReenrollmentState.Connected, runtime.state.value)
    }

    @Test fun processRecoveryUsesExactM5AliasAndDoesNotPersistNewBearer() = runBlocking {
        val port = FakePort(); var persisted: AdmissionCheckpoint? = null
        val runtime = AdmissionRuntime(CoroutineScope(Dispatchers.Unconfined), FakeKeys, port, { persisted = it })
        runtime.request(snapshot()).join(); val checkpoint = requireNotNull(persisted)
        runtime.cancel().join(); runtime.recover(checkpoint).join()
        assertTrue(runtime.state.value is AdmissionState.AwaitingComparison)
        assertEquals("autplay.m5.${checkpoint.serverProfileId.value}", FakeKeys.lastAlias)
        assertTrue(requireNotNull(port.lastRecoveryWire).contains("\"recovery_nonce_b64url\""))
        assertFalse(requireNotNull(port.lastRecoveryWire).contains("\"client_nonce_b64url\""))
    }

    @Test fun liveCheckpointEmissionIsNotAddedToColdRecoveryBootstrap() {
        val bootstrap = AdmissionRecoveryBootstrap(null)
        val emittedByLiveRequest = AdmissionCheckpointCodec.encode(
            AdmissionCheckpoint(
                "77777777-7777-4777-8777-777777777777",
                "a".repeat(64),
                ServerProfileId("22222222-2222-4222-8222-222222222222"),
                "33333333-3333-4333-8333-333333333333",
                1,
                "a".repeat(64),
                "b".repeat(64),
                "88888888-8888-4888-8888-888888888888",
                "https://example.test",
                "https://stream.example.test",
            ),
        )

        assertTrue(emittedByLiveRequest.isNotEmpty())
        assertNull(bootstrap.checkpoint)
    }

    @Test fun stalePollAndExchangeFailuresCannotOverwriteANewerGeneration() = runBlocking {
        val port = FakePort(); val runtime = AdmissionRuntime(CoroutineScope(Dispatchers.Unconfined), FakeKeys, port, {})
        runtime.request(snapshot()).join(); runtime.confirmComparison()
        port.pollGate = CompletableDeferred(); port.pollFailure = "server_identity_changed"
        val stalePoll = runtime.poll()
        runtime.cancel().join(); runtime.request(snapshot()).join()
        val afterPollRestart = runtime.state.value as AdmissionState.AwaitingComparison
        port.pollGate?.complete(Unit); stalePoll.join()
        assertEquals(afterPollRestart, runtime.state.value)

        port.pollGate = null; port.pollFailure = null
        runtime.confirmComparison(); runtime.poll().join()
        port.exchangeGate = CompletableDeferred(); port.exchangeFailure = "server_identity_changed"
        val staleExchange = runtime.confirmAccount()
        runtime.cancel().join(); runtime.request(snapshot()).join()
        val afterExchangeRestart = runtime.state.value as AdmissionState.AwaitingComparison
        port.exchangeGate?.complete(Unit); staleExchange.join()
        assertEquals(afterExchangeRestart, runtime.state.value)
    }

    private fun snapshot() = PairingFlowSnapshot("11111111-1111-4111-8111-111111111111", "https://example.test", "https://example.test", ServerProfileId("22222222-2222-4222-8222-222222222222"), "33333333-3333-4333-8333-333333333333", 1, "a".repeat(64), null, null, null, null, null)
    private object FakeKeys : M5DeviceKeyStore {
        private val key = java.security.KeyPairGenerator.getInstance("EC").apply { initialize(java.security.spec.ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        var lastAlias = ""; override fun ensure(alias: String) { lastAlias = alias }; override fun delete(alias: String) = Unit
        override fun publicKeySpki(alias: String) = key.public.encoded
        override fun publicKeyThumbprintSha256(alias: String) = "b".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray { lastAlias = alias; return ByteArray(64) }
    }
    private class FakePort : AdmissionPort {
        var exchangeCalls = 0
        var lastRecoveryWire: String? = null
        var pollGate: CompletableDeferred<Unit>? = null
        var pollFailure: String? = null
        var exchangeGate: CompletableDeferred<Unit>? = null
        var exchangeFailure: String? = null
        override suspend fun request(request: AdmissionRequest) = PairingNetworkResult.Success(AdmissionCreated("review", "poll-secret".encodeToByteArray(), "000000000001"))
        override suspend fun recover(request: AdmissionRequest): PairingNetworkResult<AdmissionRecovery> { lastRecoveryWire = request.wireJson; return PairingNetworkResult.Success(AdmissionRecovery("review2", "new-poll-secret".encodeToByteArray(), "000000000002")) }
        override suspend fun poll(request: AdmissionRequest, pollBearer: ByteArray): PairingNetworkResult<AdmissionPoll> { pollGate?.await(); return pollFailure?.let { PairingNetworkResult.Failure(it) } ?: PairingNetworkResult.Success(AdmissionPoll.Approved(AdmissionAccount(UserId("44444444-4444-4444-8444-444444444444"), "Account"))) }
        override suspend fun exchange(command: AdmissionExchangeCommand): PairingNetworkResult<EnrollmentSession> { exchangeCalls++; exchangeGate?.await(); return exchangeFailure?.let { PairingNetworkResult.Failure(it) } ?: PairingNetworkResult.Success(EnrollmentSession(app.autplay.domain.DeviceId("55555555-5555-4555-8555-555555555555"), "66666666-6666-4666-8666-666666666666", "66666666-6666-4666-8666-666666666666", 0, ByteArray(32), ByteArray(32))) }
        override suspend fun trustedReenrollmentChallenge(request: AdmissionRequest) = PairingNetworkResult.Success(TrustedReenrollmentChallenge("77777777-7777-4777-8777-777777777777", "abcdefghijklmnopqrstuv".encodeToByteArray()))
        override suspend fun trustedReenrollmentExchange(command: TrustedReenrollmentCommand) = PairingNetworkResult.Success(EnrollmentSession(app.autplay.domain.DeviceId("55555555-5555-4555-8555-555555555555"), "66666666-6666-4666-8666-666666666666", "66666666-6666-4666-8666-666666666666", 0, ByteArray(32), ByteArray(32)))
    }
}
