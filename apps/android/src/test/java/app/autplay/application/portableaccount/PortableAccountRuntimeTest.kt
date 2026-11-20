package app.autplay.application.portableaccount

import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.*
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.*
import org.junit.Test

class PortableAccountRuntimeTest {
    @Test fun legacyLogoutUsesDeviceSessionAndKeepsBindingUntilServerConfirms() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val h = Harness(server)
            h.bindLegacy()
            val bound = h.settings.settings.value
            val profile = requireNotNull(bound.activeServerProfileId)
            val session = requireNotNull(h.credentials.read(profile))
            assertFalse(h.runtime.confirmLifecycle(RuntimeLifecycleAction.LOGOUT_CURRENT, "", charArrayOf()))
            assertEquals(bound.m5Binding, h.settings.settings.value.m5Binding)
            assertArrayEquals(session, h.credentials.read(profile))
            h.lifecycleResult = PairingNetworkResult.Success(Unit)
            assertTrue(h.runtime.confirmLifecycle(RuntimeLifecycleAction.LOGOUT_CURRENT, "", charArrayOf()))
            assertNull(h.settings.settings.value.m5Binding)
            assertNull(h.settings.settings.value.activeServerProfileId)
            assertNull(h.credentials.read(profile))
            assertNull(h.store.read())
            assertEquals(2, h.lifecycleCalls.size)
            assertEquals(h.lifecycleCalls[0], h.lifecycleCalls[1])
            assertNull(h.lifecycleCalls[0].portableAuthorization)
            session.fill(0)
        }
    }

    @Test fun legacyLogoutCannotBypassPortableRootMarkerOrPendingEnrollment() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            for (guard in listOf("root", "marker", "pending")) {
                val h = Harness(server)
                h.bindLegacy()
                val bound = h.settings.settings.value
                when (guard) {
                    "marker" -> h.settings.update(bound.copy(portableAccountId = bound.activeUserId!!.value))
                    "pending" -> h.credentials.write(CredentialJournalSlots.portableEnrollment, "pending".toByteArray())
                    else -> PortableAccountCrypto.create("fixture_owner", "fixture7!".toCharArray()).use { created ->
                        h.credentials.write(CredentialJournalSlots.portableAccount,
                            app.autplay.data.security.SessionCredentialEnvelopeCodec.encode(
                                app.autplay.data.security.SessionCredentialEnvelope("portable-account", null, 0,
                                    portableAccountDocument = created.document.encode().toString(Charsets.UTF_8))))
                    }
                }
                h.lifecycleResult = PairingNetworkResult.Success(Unit)
                assertFalse(guard, h.runtime.confirmLifecycle(RuntimeLifecycleAction.LOGOUT_CURRENT, "", charArrayOf()))
                assertEquals(bound.m5Binding, h.settings.settings.value.m5Binding)
                assertTrue(h.lifecycleCalls.isEmpty())
            }
        }
    }

    @Test fun signOutRequiresPasswordRetainsIdentityAndSurvivesRestart() = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val h = Harness(server)
            val password = "fixture7!".toCharArray()
            try {
                assertTrue(h.runtime.create("local_owner", password))
                val before = requireNotNull(h.store.read())
                assertFalse(h.runtime.signOut("local_owner", "wrong!7".toCharArray()))
                assertFalse(h.settings.settings.value.accountSignedOut)
                assertTrue(h.runtime.signOut("local_owner", password))
                assertTrue(h.settings.settings.value.accountSignedOut)
                h.restart()
                assertEquals(before.fields, h.store.read()?.fields)
                assertFalse(h.runtime.signInLocal("other_owner", password))
                assertTrue(h.settings.settings.value.accountSignedOut)
                assertTrue(h.runtime.signInLocal("local_owner", password))
                assertFalse(h.settings.settings.value.accountSignedOut)
            } finally { password.fill('\u0000') }
        }
    }

    @Test fun uncertainEnrollmentReplaysExactlyAndAuthoritativeExpiryReleasesFirstBind(): Unit = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val h = Harness(server)
            val password = "portable runtime fixture phrase!".toCharArray()
            try {
                assertTrue(h.runtime.create("fixture_user", password))
                val document = requireNotNull(h.store.read())
                val requests = mutableListOf<String>()
                server.dispatcher = object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                        "/portable-accounts/login-challenge" -> errorResponse("account_authentication_failed")
                        "/portable-accounts/register-challenge" -> json(h.challenge(document, "REGISTER"))
                        "/portable-accounts/complete" -> {
                            requests += request.body.readUtf8()
                            errorResponse(if (requests.size == 1) "account_server_unavailable" else "account_challenge_expired")
                        }
                        else -> error("unexpected fixture request")
                    }
                }
                h.trust()
                assertFalse(h.runtime.connect("fixture_user", password, register = true))
                val exact = requireNotNull(h.credentials.read(CredentialJournalSlots.portableEnrollment))
                assertTrue(h.runtime.state.value.pendingEnrollment)
                h.restart()
                assertFalse(h.runtime.resume())
                assertEquals(2, requests.size)
                assertEquals(requests[0], requests[1])
                assertNull(h.credentials.read(CredentialJournalSlots.portableEnrollment))
                assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.PORTABLE_ACCOUNT))
                assertEquals(document.accountId, h.store.read()?.accountId)
                h.trust()
                assertTrue(h.pairing.state.value.pairing is PairingState.AwaitingTrust)
                exact.fill(0)
            } finally { password.fill('\u0000') }
        }
    }

    @Test fun offlineCodeResetAndPriorServerPropagationKeepUuidAndReplayTheRotationAfterCrash(): Unit = runBlocking {
        MockWebServer().use { first -> MockWebServer().use { second ->
            first.start(); second.start()
            val h = Harness(first)
            val firstPassword = "offline original fixture phrase!".toCharArray()
            val localPassword = "offline recovered fixture phrase!".toCharArray()
            val currentPassword = "online recovered fixture phrase!".toCharArray()
            try {
                assertTrue(h.runtime.create("fixture_user", firstPassword))
                val original = requireNotNull(h.store.read())
                val code = requireNotNull(h.store.recoveryCodeExport())
                try { assertTrue(h.runtime.recover("fixture_user", code, null, localPassword)) } finally { code.fill('\u0000') }
                val beforeServer = requireNotNull(h.store.read())
                assertEquals(original.accountId, beforeServer.accountId)
                assertNotEquals(original.keyThumbprint, beforeServer.keyThumbprint)
                assertTrue(h.store.canRotateOffline())
                var firstRoot: PortableAccountDocument? = null
                val firstRotations = mutableListOf<String>()
                first.dispatcher = object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                        "/portable-accounts/login-challenge" -> firstRoot?.let { json(h.challenge(it, "LOGIN")) }
                            ?: errorResponse("account_authentication_failed")
                        "/portable-accounts/register-challenge" -> json(h.challenge(beforeServer, "REGISTER"))
                        "/portable-accounts/rotation-challenge" -> json(h.challenge(requireNotNull(firstRoot), "ROTATE"))
                        "/portable-accounts/complete" -> {
                            val raw = request.body.readUtf8()
                            val body = Json.parseToJsonElement(raw).jsonObject["request"]!!.jsonObject
                            if (body["challenge"]!!.jsonObject.string("purpose") == "ROTATE") {
                                firstRotations += raw
                                firstRoot = PortableAccountDocument.parse(canonicalAccountRequest(body["replacement_account"]!!.jsonObject))
                                if (firstRotations.size == 1) h.credentials.failNextRootWrite = true
                                json(buildJsonObject { put("account_id", original.accountId); put("rotated", true)
                                    put("revision", 2); put("account", requireNotNull(firstRoot).fields) })
                            } else {
                                firstRoot = beforeServer
                                errorResponse("account_receipt_expired")
                            }
                        }
                        else -> error("unexpected fixture request")
                    }
                }
                h.trust()
                assertFalse(h.runtime.connect("fixture_user", localPassword, register = true))
                assertEquals(beforeServer.keyThumbprint, firstRoot?.keyThumbprint)
                assertFalse(h.store.canRotateOffline())
                assertFalse(h.runtime.state.value.pendingEnrollment)
                h.trust()
                val nextCode = requireNotNull(h.store.recoveryCodeExport())
                try { assertFalse(h.runtime.recover("fixture_user", nextCode, null, currentPassword)) }
                finally { nextCode.fill('\u0000') }
                assertTrue(h.runtime.state.value.pendingEnrollment)
                h.restart()
                assertTrue(h.runtime.resume())
                assertEquals(firstRotations[0], firstRotations[1])
                val current = requireNotNull(h.store.read())
                assertEquals(original.accountId, current.accountId)
                assertNotEquals(beforeServer.keyThumbprint, current.keyThumbprint)
                val other = Harness(second, h.credentials, h.settings)
                var otherRoot = beforeServer
                var propagationCount = 0
                second.dispatcher = object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                        "/portable-accounts/login-challenge" -> json(other.challenge(otherRoot, "LOGIN"))
                        "/portable-accounts/rotation-challenge" -> json(other.challenge(otherRoot, "ROTATE"))
                        "/portable-accounts/complete" -> {
                            val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject["request"]!!.jsonObject
                            if (body["challenge"]!!.jsonObject.string("purpose") == "ROTATE") {
                                assertEquals(beforeServer.fields, body["account"])
                                assertEquals(current.fields, body["replacement_account"])
                                propagationCount++; otherRoot = current
                                json(buildJsonObject { put("account_id", current.accountId); put("rotated", true)
                                    put("revision", 2); put("account", current.fields) })
                            } else errorResponse("account_challenge_expired")
                        }
                        else -> error("unexpected fixture request")
                    }
                }
                other.trust()
                assertFalse(other.runtime.connect("fixture_user", currentPassword, register = true))
                assertEquals(1, propagationCount)
                assertEquals(current.keyThumbprint, otherRoot.keyThumbprint)
                assertEquals(current.keyThumbprint, other.store.read()?.keyThumbprint)
                assertFalse(other.runtime.state.value.pendingEnrollment)
                PortableAccountCrypto.unlock(requireNotNull(other.store.read()), currentPassword).close()
            } finally { firstPassword.fill('\u0000'); localPassword.fill('\u0000'); currentPassword.fill('\u0000') }
        } }
    }

    @Test fun newPhoneFileRecoveryResumesCommittedServerRotationAfterLocalRootWriteFailure(): Unit = runBlocking {
        MockWebServer().use { server ->
            server.start()
            val h = Harness(server)
            val oldPassword = "old recovery fixture phrase!".toCharArray()
            val newPassword = "new recovery fixture phrase!".toCharArray()
            val created = try { PortableAccountCrypto.create("fixture_user", oldPassword) }
                finally { oldPassword.fill('\u0000') }
            created.use { old ->
                val recovery = PortableRecoveryFile.encode(old.document, old.recoveryCode)
                try {
                    val requests = mutableListOf<String>()
                    server.dispatcher = object : Dispatcher() {
                        override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                            "/portable-accounts/rotation-challenge" -> json(h.challenge(old.document, "ROTATE"))
                            "/portable-accounts/complete" -> {
                                val body = request.body.readUtf8()
                                requests += body
                                val replacement = Json.parseToJsonElement(body).jsonObject["request"]!!.jsonObject["replacement_account"]!!
                                json(buildJsonObject {
                                    put("account_id", old.document.accountId)
                                    put("rotated", true); put("revision", 2); put("account", replacement)
                                })
                            }
                            else -> error("unexpected fixture request")
                        }
                    }
                    h.trust()
                    h.credentials.failNextRootWrite = true
                    assertFalse(h.runtime.recover("fixture_user", charArrayOf(), recovery, newPassword))
                    assertNull(h.store.read())
                    assertTrue(h.runtime.state.value.pendingEnrollment)
                    val exact = h.credentials.read(CredentialJournalSlots.portableEnrollment)
                    h.restart()
                    assertTrue(h.runtime.resume())
                    assertEquals(2, requests.size)
                    assertEquals(requests[0], requests[1])
                    val root = requireNotNull(h.store.read())
                    assertEquals(old.document.accountId, root.accountId)
                    assertNotEquals(old.document.keyThumbprint, root.keyThumbprint)
                    PortableAccountCrypto.unlock(root, newPassword).close()
                    assertNotNull(h.store.recoveryExport())
                    assertNull(h.credentials.read(CredentialJournalSlots.portableEnrollment))
                    assertFalse(h.gate.isReservedBy(FirstBindCeremonyOwner.PORTABLE_ACCOUNT))
                    assertNull(h.settings.settings.value.activeServerProfileId)
                    exact?.fill(0)
                } finally { recovery.fill(0); newPassword.fill('\u0000') }
            }
        }
    }

    private class Harness(server: MockWebServer, val credentials: MemoryCredentials = MemoryCredentials(),
        val settings: MemorySettings = MemorySettings()) {
        val origin = server.url("/").toString().trimEnd('/')
        val keys = Keys()
        val store = PortableAccountStore(credentials, settings)
        var gate = FirstBindCeremonyGate()
        lateinit var pairing: ProfilePairingRuntime
        lateinit var runtime: PortableAccountRuntime
        private val serverId = java.util.UUID.randomUUID().toString()
        private var legacySnapshot: PairingFlowSnapshot? = null
        val lifecycleCalls = mutableListOf<LifecycleCommand>()
        var lifecycleResult: PairingNetworkResult<Unit> = PairingNetworkResult.Failure("server_unavailable")

        suspend fun bindLegacy() {
            fun uuid() = java.util.UUID.randomUUID().toString()
            val snapshot = PairingFlowSnapshot(uuid(), origin, origin, ServerProfileId(serverId), serverId, 1,
                hash(keys.spki), app.autplay.domain.UserId(uuid()), app.autplay.domain.DeviceId(uuid()),
                hash(keys.spki), null, uuid())
            legacySnapshot = snapshot
            assertTrue(gate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS))
            credentials.write(snapshot.serverProfileId, app.autplay.data.security.SessionCredentialEnvelopeCodec.encode(
                app.autplay.data.security.SessionCredentialEnvelope("pending-account-registration", null, 0,
                    refreshPending = true, publicAccessPendingRegistrationId = uuid(),
                    publicAccessPendingCanonicalRequest = "e30", publicAccessPendingSuccessorRefreshToken = "r".repeat(43))))
            assertTrue(pairing.completePublicAccountRegistration(snapshot, "fixture-key",
                EnrollmentSession(requireNotNull(snapshot.expectedDeviceId), uuid(), uuid(), 0,
                    "fixture-access".toByteArray(), ByteArray(32) { 1 }), keys.spki.copyOf()))
            assertTrue(pairing.state.value.pairing is PairingState.Connected)
        }

        suspend fun trust() { pairing.startDiscovery(origin).join(); pairing.confirmTrust() }
        fun restart() { gate = FirstBindCeremonyGate(); pairing = pairing(); runtime = runtime() }
        fun challenge(document: PortableAccountDocument, purpose: String): JsonObject = buildJsonObject {
            put("account", document.fields)
            put("challenge", buildJsonObject {
                put("account_id", document.accountId); put("purpose", purpose)
                put("server_instance_id", serverId); put("identity_epoch", 1)
                put("identity_thumbprint_sha256", hash(keys.spki)); put("api_origin", origin); put("stream_origin", origin)
                put("document_sha256", hash(document.encode())); put("expires_at", Instant.now().plusSeconds(120).toString())
            })
        }
        private fun runtime(): PortableAccountRuntime = PortableAccountRuntime(store, settings, credentials, keys,
            pairing, discovery, gate, { PortableAccountPort(it, credentials) }, "Fixture phone", "fixture-1")
        private fun pairing(): ProfilePairingRuntime = ProfilePairingRuntime(
            CoroutineScope(Dispatchers.Unconfined), settings, credentials, keys, discovery,
            M5BindingMaterializationCoordinator(settings, credentials, keys, object : M5LocalIntentMaterializer {
                override suspend fun pending(limit: Int): List<PendingLocalIntentSummary> = emptyList()
                override suspend fun materialize(binding: ClientEventBinding, localChangeId: LocalId, eventId: LocalId, materializedAtMs: Long): LocalId = error("unexpected materialization")
            }), "Fixture phone", {}, allowUnsafeDevelopmentHttp = true, firstBindGate = gate,
        )
        private val discovery = object : ProfilePairingPort {
            override suspend fun discovery(apiOrigin: String) = PairingNetworkResult.Success(DiscoveryDocument(
                TrustedServerIdentity(serverId, 1, hash(keys.spki)), "Fixture", origin, origin, setOf(1), Instant.now().plusSeconds(300), keys.spki.copyOf()))
            override suspend fun capabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot): PairingNetworkResult<CapabilityDocument> =
                legacySnapshot?.let {
                    PairingNetworkResult.Success(CapabilityDocument(CapabilityState(
                        TrustedServerIdentity(serverId, 1, hash(keys.spki)), requireNotNull(it.expectedUserId),
                        requireNotNull(it.expectedDeviceId), 1, 1, System.currentTimeMillis() + 300_000, emptySet()),
                        "fixture".toByteArray(), "1".repeat(64)))
                } ?: PairingNetworkResult.Failure("unused")
            override suspend fun exchange(request: EnrollmentExchangeCommand) = PairingNetworkResult.Failure("unused")
            override suspend fun createInvitation(profileId: ServerProfileId, operationId: String, expiresInSeconds: Int) = PairingNetworkResult.Failure("unused")
            override suspend fun cancelInvitation(profileId: ServerProfileId, invitationId: String, operationId: String) = PairingNetworkResult.Failure("unused")
            override suspend fun rotate(request: SessionRotationCommand) = PairingNetworkResult.Failure("unused")
            override suspend fun devices(profileId: ServerProfileId) = PairingNetworkResult.Failure("unused")
            override suspend fun sessions(profileId: ServerProfileId) = PairingNetworkResult.Failure("unused")
            override suspend fun lifecycle(profileId: ServerProfileId, command: LifecycleCommand): PairingNetworkResult<Unit> {
                lifecycleCalls += command
                return lifecycleResult
            }
        }
        init { pairing = pairing(); runtime = runtime() }
    }

    private class MemorySettings : NonSecretSettingsStore {
        override val settings = MutableStateFlow(NonSecretSettings())
        override suspend fun update(settings: NonSecretSettings) { this.settings.value = settings }
    }
    private class MemoryCredentials : CredentialStore {
        private val values = ConcurrentHashMap<ServerProfileId, ByteArray>()
        var failNextRootWrite = false
        override suspend fun read(profileId: ServerProfileId): ByteArray? = values[profileId]?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            if (profileId == CredentialJournalSlots.portableAccount && failNextRootWrite) {
                failNextRootWrite = false
                throw IOException("fixture local persistence interrupted")
            }
            values.put(profileId, material.copyOf())?.fill(0)
        }
        override suspend fun clear(profileId: ServerProfileId) { values.remove(profileId)?.fill(0) }
        override suspend fun hasPublicAccessPendingRegistration(): Boolean = false
    }
    private class Keys : M5DeviceKeyStore {
        val spki: ByteArray = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair().public.encoded
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) = Unit
        override fun publicKeySpki(alias: String): ByteArray = spki.copyOf()
        override fun publicKeyThumbprintSha256(alias: String): String = hash(spki)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray = error("unused device signing")
    }
    private companion object {
        fun json(value: JsonObject): MockResponse = MockResponse().setHeader("Content-Type", "application/json").setBody(value.toString())
        fun errorResponse(code: String): MockResponse = json(buildJsonObject {
            put("error", buildJsonObject { put("code", code) })
        }).setResponseCode(409)
    }
}
