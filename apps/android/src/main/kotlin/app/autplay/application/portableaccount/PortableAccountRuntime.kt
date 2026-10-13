package app.autplay.application.portableaccount

import app.autplay.application.profilepairing.*
import app.autplay.application.accountrecovery.hasAccountRecoveryPendingRecipient
import app.autplay.application.selfpairing.hasSelfDevicePairingPendingRecipient
import app.autplay.application.social.normalizeSocialPublicId
import app.autplay.data.security.*
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*

data class PortableAccountStatus(val publicId: String? = null, val recoveryUnsaved: Boolean = false, val serverPublicId: String? = null,
    val serverCopyReady: Boolean = false, val pendingEnrollment: Boolean = false, val busy: Boolean = false, val error: String? = null,
    val offlineRecoveryAllowed: Boolean = false)

/** Passwords exist only for the duration of a command. Exact network replay is Keystore-encrypted. */
class PortableAccountRuntime(
    val store: PortableAccountStore,
    private val settings: NonSecretSettingsStore,
    private val credentials: CredentialStore,
    private val keys: M5DeviceKeyStore,
    private val pairing: ProfilePairingRuntime,
    private val pairingPort: ProfilePairingPort,
    private val gate: FirstBindCeremonyGate,
    private val port: (String) -> PortableAccountPort,
    private val deviceName: String,
    private val appVersion: String,
    private val beforeDisconnect: suspend (ServerProfileId, PortableAccountOwnership) -> Unit = { _, _ -> },
) {
    private val mutableState = MutableStateFlow(PortableAccountStatus())
    private val commands = Mutex()
    val state = mutableState.asStateFlow()

    suspend fun refresh() = commands.withLock { refreshState() }

    private suspend fun refreshState() {
        var document = store.recoverIdentity()
        val current = settings.settings.first()
        val pending = credentials.hasPortableAccountPendingEnrollment()
        if (!pending && current.activeServerProfileId != null && current.m5Binding != null && current.serverBaseUrl != null) {
            try {
                val response = port(current.serverBaseUrl).request("/portable-accounts/current", null, current.activeServerProfileId)
                val remote = response["account"] as? JsonObject
                if (remote != null) {
                    val passport = PortableAccountDocument.parse(canonicalAccountRequest(remote))
                    store.adoptFromAuthenticatedBinding(passport, current.activeServerProfileId, current.m5Binding.bindingCommitId)
                    document = store.read()
                    mutableState.value = mutableState.value.copy(serverCopyReady = true)
                } else mutableState.value = mutableState.value.copy(serverCopyReady = false)
                mutableState.value = mutableState.value.copy(serverPublicId = (response["public_id"] as? JsonPrimitive)?.takeIf { it.isString }?.content)
            } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch (_: Exception) { mutableState.value = mutableState.value.copy(serverCopyReady = false) }
        } else mutableState.value = mutableState.value.copy(serverCopyReady = false, serverPublicId = null)
        mutableState.value = mutableState.value.copy(publicId = document?.publicId,
            recoveryUnsaved = store.recoveryExport()?.let { it.fill(0); true } ?: false,
            pendingEnrollment = credentials.hasPortableAccountPendingEnrollment(), offlineRecoveryAllowed = store.canRotateOffline())
    }

    suspend fun create(login: String, password: CharArray): Boolean = command {
        require(!credentials.hasPortableAccountPendingEnrollment()) { "ACCOUNT_CONNECTION_OUTCOME_PENDING" }
        require(gate.reserve(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)) { "FIRST_BIND_CEREMONY_BUSY" }
        try {
        val current = settings.settings.first()
        val normalized = requireNotNull(normalizeSocialPublicId(login))
        if (current.activeServerProfileId != null) {
            val transport = port(requireNotNull(current.serverBaseUrl))
            val existing = transport.request("/portable-accounts/current", null, current.activeServerProfileId)
            val confirmedLogin = (existing["public_id"] as? JsonPrimitive)?.takeIf { it.isString }?.content
            require(confirmedLogin == null || confirmedLogin == normalized) { "ACCOUNT_IDENTITY_CONFLICT" }
            val remote = existing["account"]
            if (remote is JsonObject) {
                val document = PortableAccountDocument.parse(canonicalAccountRequest(remote))
                require(document.publicId == normalized && document.accountId == current.activeUserId?.value) { "ACCOUNT_IDENTITY_CONFLICT" }
                store.adopt(document, password)
            } else {
                val document = store.create(normalized, password)
                val challenge = transport.request("/portable-accounts/claim-challenge", accountOnly(document), current.activeServerProfileId)
                verifyChallenge(challenge, document, current.m5Binding!!.serverInstanceId, current.m5Binding.identityEpoch,
                    current.m5Binding.identityThumbprintSha256, requireNotNull(current.serverBaseUrl), requireNotNull(current.streamBaseUrl), "CLAIM")
                PortableAccountCrypto.unlock(document, password).use { unlocked ->
                    store.markServerEnrollmentAttempted(document)
                    transport.request("/portable-accounts/complete", signed(unlocked, "claim", challenge), current.activeServerProfileId)
                }
            }
            mutableState.value = mutableState.value.copy(serverCopyReady = true)
        } else store.create(normalized, password)
        refreshState()
        } finally { gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT) }
    }

    suspend fun connect(login: String, password: CharArray, register: Boolean): Boolean = command {
        require(!credentials.hasPortableAccountPendingEnrollment()) { "ACCOUNT_CONNECTION_OUTCOME_PENDING" }
        val current = settings.settings.first()
        require(current.activeServerProfileId == null && current.m5Binding == null)
        require(current.m5PendingExchangeCheckpoint == null && current.m5AdmissionCheckpoint == null &&
            !credentials.hasPublicAccessPendingRegistration() && !credentials.hasSelfDevicePairingPendingRecipient() &&
            !credentials.hasAccountRecoveryPendingRecipient()) { "FIRST_BIND_CEREMONY_BUSY" }
        val runtime = pairing.state.value
        val awaiting = runtime.pairing as? PairingState.AwaitingTrust ?: error("ACCOUNT_SERVER_TRUST_REQUIRED")
        require(runtime.trustConfirmed) { "ACCOUNT_SERVER_TRUST_REQUIRED" }
        require(gate.transfer(FirstBindCeremonyOwner.M5, FirstBindCeremonyOwner.PORTABLE_ACCOUNT)) { "FIRST_BIND_CEREMONY_BUSY" }
        try {
        val snapshot = awaiting.snapshot
        val transport = port(snapshot.apiOrigin)
        var purpose = "LOGIN"
        var response = try {
            transport.request("/portable-accounts/login-challenge", buildJsonObject { put("public_id", requireNotNull(normalizeSocialPublicId(login))) })
        } catch (failure: PortableAccountServerException) {
            if (!register || failure.code != "account_authentication_failed") throw failure
            val local = requireNotNull(store.read()) { "ACCOUNT_SETUP_REQUIRED" }
            require(local.publicId == normalizeSocialPublicId(login)) { "ACCOUNT_AUTHENTICATION_FAILED" }
            purpose = "REGISTER"
            transport.request("/portable-accounts/register-challenge", accountOnly(local))
        }
        val remote = PortableAccountDocument.parse(canonicalAccountRequest(response["account"]!!.jsonObject))
        val local = store.read()
        if (local != null && remote.keyThumbprint != local.keyThumbprint) {
            require(purpose == "LOGIN" && remote.accountId == local.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            verifyChallenge(response, remote, snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch,
                snapshot.expectedIdentityThumbprintSha256, snapshot.apiOrigin, snapshot.streamOrigin, "LOGIN")
            // The current password authorizes using a retained predecessor only for this trusted server.
            PortableAccountCrypto.unlock(local, password).use { successor ->
                requireNotNull(store.findPredecessor(remote)) { "ACCOUNT_ROOT_TRANSITION_UNAVAILABLE" }.use { predecessor ->
                    val challenge = transport.request("/portable-accounts/rotation-challenge", accountOnly(remote))
                    verifyChallenge(challenge, remote, snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch,
                        snapshot.expectedIdentityThumbprintSha256, snapshot.apiOrigin, snapshot.streamOrigin, "ROTATE")
                    PortableAccountCrypto.recover(predecessor.document, predecessor.code).use { previous ->
                        persistAndRotate(previous, successor, challenge, null, current)
                    }
                }
            }
            require(gate.transfer(FirstBindCeremonyOwner.M5, FirstBindCeremonyOwner.PORTABLE_ACCOUNT)) { "FIRST_BIND_CEREMONY_BUSY" }
            response = transport.request("/portable-accounts/login-challenge", buildJsonObject { put("public_id", local.publicId) })
        }
        val document = PortableAccountDocument.parse(canonicalAccountRequest(response["account"]!!.jsonObject))
        require(document.publicId == normalizeSocialPublicId(login)) { "ACCOUNT_AUTHENTICATION_FAILED" }
        verifyChallenge(response, document, snapshot.expectedServerInstanceId, snapshot.expectedIdentityEpoch,
            snapshot.expectedIdentityThumbprintSha256, snapshot.apiOrigin, snapshot.streamOrigin, purpose)
        PortableAccountCrypto.unlock(document, password).use { unlocked ->
            store.adopt(document, password)
            val alias = "autplay-portable-device-${document.accountId}"
            keys.ensure(alias)
            val refresh = ByteArray(32).also(SecureRandom()::nextBytes).let { raw ->
                try { Base64.getUrlEncoder().withoutPadding().encode(raw) } finally { raw.fill(0) }
            }
            try {
                val commit = UUID.randomUUID().toString()
                val body = buildJsonObject {
                    response.forEach { (name, value) -> put(name, value) }
                    put("device_public_key_spki_b64", Base64.getEncoder().encodeToString(keys.publicKeySpki(alias)))
                    put("device_name", deviceName.take(120)); put("app_version", appVersion.take(32))
                    put("next_refresh_token_sha256", hash(refresh)); put("binding_commit_id", commit)
                }
                val bound = snapshot.copy(bindingCommitId = commit, expectedUserId = UserId(document.accountId),
                    deviceKeyThumbprintSha256 = keys.publicKeyThumbprintSha256(alias))
                val spki = requireNotNull(runtime.identityPublicKeySpki)
                val envelope = buildJsonObject {
                    put("request", signed(unlocked, purpose.lowercase(java.util.Locale.ROOT), body))
                    put("refresh", refresh.toString(Charsets.US_ASCII)); put("alias", alias)
                    put("profile_id", snapshot.serverProfileId.value); put("generation_id", snapshot.generationId)
                    put("identity_spki", Base64.getEncoder().encodeToString(spki))
                }
                store.markServerEnrollmentAttempted(document)
                BindingAuthorityWriteGate.serialized {
                    require(settings.settings.first().activeServerProfileId == null)
                    require(!credentials.hasPortableAccountPendingEnrollment())
                    savePending(envelope)
                    require(pairing.preparePortableEnrollment(bound, spki, recovering = false))
                }
                finish(envelope)
            } finally { refresh.fill(0) }
        }
        refreshState()
        } finally {
            withContext(NonCancellable) {
                if (!credentials.hasPortableAccountPendingEnrollment() && settings.settings.first().activeServerProfileId == null)
                    gate.transfer(FirstBindCeremonyOwner.PORTABLE_ACCOUNT, FirstBindCeremonyOwner.M5)
            }
        }
    }

    suspend fun resume(): Boolean = command {
        val pending = readPending() ?: return@command
        store.recoverIdentity()
        if ((pending["kind"] as? JsonPrimitive)?.content == "rotation") {
            finishRotation(pending)
            refreshState()
            return@command
        }
        val request = pending["request"]!!.jsonObject["request"]!!.jsonObject
        val challenge = request["challenge"]!!.jsonObject
        val document = PortableAccountDocument.parse(canonicalAccountRequest(request["account"]!!.jsonObject))
        val current = settings.settings.first()
        if (current.m5Binding?.bindingCommitId == request.string("binding_commit_id") && current.activeUserId?.value == document.accountId) {
            credentials.clear(CredentialJournalSlots.portableEnrollment)
            gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)
            refreshState()
            return@command
        }
        require(current.activeServerProfileId == null && current.portableAccountId == document.accountId) { "ACCOUNT_CONTEXT_CHANGED" }
        val discovery = when (val result = pairingPort.discovery(challenge.string("api_origin"))) {
            is PairingNetworkResult.Success -> result.value
            is PairingNetworkResult.Failure -> error("ACCOUNT_SERVER_UNAVAILABLE")
        }
        val spki = Base64.getDecoder().decode(pending.string("identity_spki"))
        require(MessageDigest.isEqual(spki, discovery.identityPublicKeySpki) && discovery.apiOrigin == challenge.string("api_origin") &&
            discovery.streamOrigin == challenge.string("stream_origin") && discovery.identity.serverInstanceId == challenge.string("server_instance_id") &&
            discovery.identity.identityEpoch == challenge.number("identity_epoch")) { "ACCOUNT_SERVER_IDENTITY_CHANGED" }
        require(pairing.preparePortableEnrollment(snapshot(pending, request, document), spki, recovering = true))
        finish(pending)
        refreshState()
    }

    suspend fun confirmLifecycle(action: RuntimeLifecycleAction, login: String, password: CharArray): Boolean = command {
        require(!credentials.hasPortableAccountPendingEnrollment()) { "ACCOUNT_CONNECTION_OUTCOME_PENDING" }
        require(store.verify(login, password)) { "ACCOUNT_AUTHENTICATION_FAILED" }
        val document = requireNotNull(store.read())
        val current = settings.settings.first()
        val binding = requireNotNull(current.m5Binding)
        beforeDisconnect(requireNotNull(current.activeServerProfileId), requireNotNull(store.ownership()))
        val operation = pairing.state.value.lastUnknownLifecycle?.takeIf {
            it.action.name == (if (action == RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE) "REVOKE_DEVICE" else action.name)
        }?.operationId ?: UUID.randomUUID().toString()
        PortableAccountCrypto.unlock(document, password).use { unlocked ->
            val request = if (action == RuntimeLifecycleAction.DISCONNECT_LOCAL) buildJsonObject {
                put("account", document.fields)
                put("challenge", buildJsonObject {
                    put("account_id", document.accountId); put("action", action.name); put("operation_id", operation)
                    put("binding_commit_id", binding.bindingCommitId); put("server_profile_id", requireNotNull(current.activeServerProfileId).value)
                    put("session_family_id", binding.sessionFamilyId)
                    put("expires_at", Instant.now().plusSeconds(120).toString()); put("nonce", UUID.randomUUID().toString())
                })
            } else {
                val transport = port(requireNotNull(current.serverBaseUrl))
                val body = buildJsonObject {
                    put("action", if (action == RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE) "REVOKE_DEVICE" else action.name)
                    put("operation_id", operation)
                    put("target_device_id", if (action == RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE) JsonPrimitive(current.deviceId!!.value) else JsonNull)
                }
                val challenge = transport.request("/portable-accounts/reauth-challenge", body, current.activeServerProfileId)
                verifyChallenge(challenge, document, binding.serverInstanceId, binding.identityEpoch, binding.identityThumbprintSha256,
                    requireNotNull(current.serverBaseUrl), requireNotNull(current.streamBaseUrl), "REAUTH")
                challenge
            }
            require(pairing.performConfirmedLifecycle(action, operation, signed(unlocked, "reauth", request).toString())) { "ACCOUNT_EXIT_UNAVAILABLE" }
        }
        mutableState.value = mutableState.value.copy(serverCopyReady = false)
        refreshState()
    }

    /** File/code recovery rotates the selected server's root; it never merely rewraps the old key. */
    suspend fun recover(login: String, recoveryCode: CharArray, recoveryFile: ByteArray?, password: CharArray): Boolean = command {
        require(PortableAccountCrypto.validPassword(password)) { "ACCOUNT_PASSWORD_INVALID" }
        require(!credentials.hasPortableAccountPendingEnrollment()) { "ACCOUNT_CONNECTION_OUTCOME_PENDING" }
        val current = settings.settings.first()
        val trusted = (pairing.state.value.pairing as? PairingState.AwaitingTrust)?.snapshot
        val binding = current.m5Binding
        if (binding == null && (store.canRotateOffline() || trusted == null || !pairing.state.value.trustConfirmed)) {
            recoverOffline(login, recoveryCode, recoveryFile, password)
            refreshState()
            return@command
        }
        require(binding != null || trusted != null && pairing.state.value.trustConfirmed) { "ACCOUNT_SERVER_TRUST_REQUIRED" }
        val origin = if (binding != null) requireNotNull(current.serverBaseUrl) else requireNotNull(trusted).apiOrigin
        val stream = if (binding != null) requireNotNull(current.streamBaseUrl) else requireNotNull(trusted).streamOrigin
        val serverId = binding?.serverInstanceId ?: requireNotNull(trusted).expectedServerInstanceId
        val epoch = binding?.identityEpoch ?: requireNotNull(trusted).expectedIdentityEpoch
        val thumb = binding?.identityThumbprintSha256 ?: requireNotNull(trusted).expectedIdentityThumbprintSha256
        val transport = port(origin)
        val file = recoveryFile?.let(PortableRecoveryFile::parse)
        try {
            val response = if (file != null) transport.request("/portable-accounts/rotation-challenge", accountOnly(file.document))
                else transport.request("/portable-accounts/login-challenge", buildJsonObject { put("public_id", requireNotNull(normalizeSocialPublicId(login))) })
            val old = PortableAccountDocument.parse(canonicalAccountRequest(response["account"]!!.jsonObject))
            verifyChallenge(response, old, serverId, epoch, thumb, origin, stream, if (file != null) "ROTATE" else "LOGIN")
            require(file == null || file.document.fields == old.fields) { "ACCOUNT_RECOVERY_INVALID" }
            val local = store.read()
            require(local == null || local.accountId == old.accountId && local.keyThumbprint == old.keyThumbprint) { "ACCOUNT_IDENTITY_CONFLICT" }
            require(current.activeUserId == null || current.activeUserId.value == old.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            val code = file?.code?.copyOf() ?: PortableRecoveryFile.decodeCode(recoveryCode)
            try {
                PortableAccountCrypto.recover(old, code).use { unlocked ->
                    val challenge = if (file != null) response else transport.request("/portable-accounts/rotation-challenge", accountOnly(old))
                    verifyChallenge(challenge, old, serverId, epoch, thumb, origin, stream, "ROTATE")
                    PortableAccountCrypto.rotate(unlocked, password).use { replacement ->
                        val source = file ?: recoveryFileOf(old, code)
                        try {
                            val recovery = store.buildRotatedRecovery(source, replacement)
                            try {
                                PortableAccountCrypto.unlock(replacement.document, password).use { next ->
                                    persistAndRotate(unlocked, next, challenge, recovery, current)
                                }
                            } finally { recovery.fill(0) }
                        } finally { if (file == null) source.close() }
                    }
                }
            } finally { code.fill(0) }
        } finally { file?.close() }
        refreshState()
    }

    private suspend fun recoverOffline(login: String, code: CharArray, bytes: ByteArray?, password: CharArray) {
        val file = bytes?.let(PortableRecoveryFile::parse)
        try {
            val old = file?.document ?: store.read() ?: error("ACCOUNT_SERVER_TRUST_REQUIRED")
            require(file != null || old.publicId == normalizeSocialPublicId(login)) { "ACCOUNT_AUTHENTICATION_FAILED" }
            val material = file?.code?.copyOf() ?: PortableRecoveryFile.decodeCode(code)
            try {
                PortableAccountCrypto.recover(old, material).use { unlocked ->
                    PortableAccountCrypto.rotate(unlocked, password).use { next ->
                        val source = file ?: recoveryFileOf(old, material)
                        val ownsDiscovery = gate.isReservedBy(FirstBindCeremonyOwner.M5)
                        try {
                            require(gate.reserve(FirstBindCeremonyOwner.PORTABLE_ACCOUNT) ||
                                gate.transfer(FirstBindCeremonyOwner.M5, FirstBindCeremonyOwner.PORTABLE_ACCOUNT)) { "FIRST_BIND_CEREMONY_BUSY" }
                            try { store.offlineRotate(old, next, source) }
                            finally {
                                if (ownsDiscovery) gate.transfer(FirstBindCeremonyOwner.PORTABLE_ACCOUNT, FirstBindCeremonyOwner.M5)
                                else gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)
                            }
                        } finally { if (file == null) source.close() }
                    }
                }
            } finally { material.fill(0) }
        } finally { file?.close() }
    }

    private fun recoveryFileOf(document: PortableAccountDocument, code: ByteArray): PortableRecoveryFile {
        val bytes = PortableRecoveryFile.encode(document, code)
        return try { PortableRecoveryFile.parse(bytes) } finally { bytes.fill(0) }
    }

    private suspend fun persistAndRotate(previous: UnlockedPortableAccount, replacement: UnlockedPortableAccount,
        challenge: JsonObject, recovery: ByteArray?, current: app.autplay.data.settings.NonSecretSettings) {
        val request = JsonObject(challenge + ("replacement_account" to replacement.document.fields))
        val authorization = JsonObject(signed(previous, "rotate", request) +
            ("replacement_proof" to signed(replacement, "rotate", request).getValue("proof")))
        val ownership = store.ownership()
        store.read()?.let { store.markServerEnrollmentAttempted(it) }
        val pending = buildJsonObject {
            put("kind", "rotation"); put("request", authorization)
            recovery?.let { put("recovery", it.toString(Charsets.UTF_8)) }
            put("ownership_since_ms", ownership?.standaloneSinceMs ?: System.currentTimeMillis())
            current.activeServerProfileId?.let { put("previous_profile", it.value) }
            current.m5Binding?.let { put("previous_commit", it.bindingCommitId) }
        }
        BindingAuthorityWriteGate.serialized {
            val latest = settings.settings.first()
            require(latest.activeServerProfileId == current.activeServerProfileId && latest.m5Binding?.bindingCommitId == current.m5Binding?.bindingCommitId)
            require(!credentials.hasPortableAccountPendingEnrollment())
            require(pairing.preparePortableRotation(previous.document.accountId)) { "FIRST_BIND_CEREMONY_BUSY" }
            try { savePending(pending) }
            catch (failure: Exception) {
                withContext(NonCancellable) {
                    if (!credentials.hasPortableAccountPendingEnrollment()) {
                        if (current.m5Binding != null) gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)
                        else gate.transfer(FirstBindCeremonyOwner.PORTABLE_ACCOUNT, FirstBindCeremonyOwner.M5)
                    }
                }
                throw failure
            }
        }
        finishRotation(pending)
    }

    private suspend fun finishRotation(pending: JsonObject) {
        val authorization = pending["request"]!!.jsonObject
        val request = authorization["request"]!!.jsonObject
        val challenge = request["challenge"]!!.jsonObject
        val old = PortableAccountDocument.parse(canonicalAccountRequest(request["account"]!!.jsonObject))
        val replacement = PortableAccountDocument.parse(canonicalAccountRequest(request["replacement_account"]!!.jsonObject))
        val origin = challenge.string("api_origin")
        val discovery = when (val result = pairingPort.discovery(origin)) {
            is PairingNetworkResult.Success -> result.value
            is PairingNetworkResult.Failure -> error("ACCOUNT_SERVER_UNAVAILABLE")
        }
        require(discovery.apiOrigin == origin && discovery.streamOrigin == challenge.string("stream_origin") &&
            discovery.identity.serverInstanceId == challenge.string("server_instance_id") &&
            discovery.identity.identityEpoch == challenge.number("identity_epoch") &&
            hash(discovery.identityPublicKeySpki) == challenge.string("identity_thumbprint_sha256")) { "ACCOUNT_SERVER_IDENTITY_CHANGED" }
        require(pairing.preparePortableRotation(old.accountId, recovering = true)) { "FIRST_BIND_CEREMONY_BUSY" }
        val previousProfile = (pending["previous_profile"] as? JsonPrimitive)?.content?.let(::ServerProfileId)
        val previousCommit = (pending["previous_commit"] as? JsonPrimitive)?.content
        val ownership = store.ownership() ?: PortableAccountOwnership(old.accountId,
            (pending["ownership_since_ms"] as? JsonPrimitive)?.longOrNull ?: System.currentTimeMillis())
        if (previousProfile != null) beforeDisconnect(previousProfile, ownership)
        val result = try { port(origin).request("/portable-accounts/complete", authorization) }
        catch (failure: PortableAccountServerException) {
            if (failure.code == "account_challenge_expired") {
                credentials.clear(CredentialJournalSlots.portableEnrollment)
                require(pairing.releaseExpiredPortableRotation(old.accountId)) { "ACCOUNT_CONTEXT_CHANGED" }
            }
            throw failure
        }
        require(result.string("account_id") == old.accountId && result["rotated"] == JsonPrimitive(true) &&
            result["account"] == replacement.fields && result.number("revision") > 0) { "ACCOUNT_RESPONSE_INVALID" }
        val recovery = (pending["recovery"] as? JsonPrimitive)?.content?.toByteArray(Charsets.UTF_8)
        try { store.commitRotation(old, replacement, recovery, importedSinceMs = ownership.standaloneSinceMs) }
        finally { recovery?.fill(0) }
        if (previousProfile != null) require(pairing.detachAfterPortableRootRotation(old.accountId, previousProfile, requireNotNull(previousCommit))) {
            "ACCOUNT_CONTEXT_CHANGED"
        }
        credentials.clear(CredentialJournalSlots.portableEnrollment)
        if (previousProfile == null) require(pairing.finishPortableRotationGate(old.accountId)) { "ACCOUNT_CONTEXT_CHANGED" }
        else gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)
        mutableState.value = mutableState.value.copy(serverCopyReady = false)
    }

    private suspend fun finish(pending: JsonObject) {
        val authorization = pending["request"]!!.jsonObject
        val request = authorization["request"]!!.jsonObject
        val document = PortableAccountDocument.parse(canonicalAccountRequest(request["account"]!!.jsonObject))
        val snapshot = snapshot(pending, request, document)
        val result = try { port(snapshot.apiOrigin).request("/portable-accounts/complete", authorization) }
        catch (failure: PortableAccountServerException) {
            if (failure.code in setOf("account_challenge_expired", "account_receipt_expired")) {
                credentials.clear(CredentialJournalSlots.portableEnrollment)
                require(pairing.releaseExpiredPortableEnrollment(snapshot)) { "ACCOUNT_CONTEXT_CHANGED" }
                mutableState.value = mutableState.value.copy(pendingEnrollment = false)
            }
            throw failure
        }
        require(result.string("account_id") == document.accountId && result.string("binding_commit_id") == snapshot.bindingCommitId &&
            result.string("session_id") == result.string("session_family_id") && result.number("generation") == 0L)
        val device = DeviceId(result.string("device_id"))
        val spki = Base64.getDecoder().decode(pending.string("identity_spki"))
        val session = EnrollmentSession(device, result.string("session_id"), result.string("session_family_id"), 0,
            result.string("access_token").toByteArray(Charsets.UTF_8), pending.string("refresh").toByteArray(Charsets.US_ASCII))
        require(pairing.completePortableEnrollment(snapshot.copy(expectedDeviceId = device), pending.string("alias"), session, spki)) { "ACCOUNT_BINDING_SAVE_FAILED" }
        credentials.clear(CredentialJournalSlots.portableEnrollment)
        gate.release(FirstBindCeremonyOwner.PORTABLE_ACCOUNT)
        mutableState.value = mutableState.value.copy(serverCopyReady = true)
    }

    private fun snapshot(pending: JsonObject, request: JsonObject, document: PortableAccountDocument): PairingFlowSnapshot {
        val challenge = request["challenge"]!!.jsonObject
        return PairingFlowSnapshot(generationId = pending.string("generation_id"), apiOrigin = challenge.string("api_origin"),
            streamOrigin = challenge.string("stream_origin"), serverProfileId = ServerProfileId(pending.string("profile_id")),
            expectedServerInstanceId = challenge.string("server_instance_id"), expectedIdentityEpoch = challenge.number("identity_epoch"),
            expectedIdentityThumbprintSha256 = challenge.string("identity_thumbprint_sha256"), expectedUserId = UserId(document.accountId),
            expectedDeviceId = null, deviceKeyThumbprintSha256 = keys.publicKeyThumbprintSha256(pending.string("alias")),
            operationId = null, bindingCommitId = request.string("binding_commit_id"))
    }

    private suspend fun command(block: suspend () -> Unit): Boolean = withContext(Dispatchers.IO) {
        if (!commands.tryLock()) return@withContext false
        mutableState.value = mutableState.value.copy(busy = true, error = null)
        try { block(); true }
        catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (error: Exception) {
            val code = error.message.orEmpty().uppercase(java.util.Locale.ROOT)
            mutableState.value = mutableState.value.copy(error = code.takeIf { Regex("[A-Z][A-Z0-9_]{1,80}").matches(it) } ?: "ACCOUNT_OPERATION_FAILED")
            false
        } finally {
            mutableState.value = mutableState.value.copy(busy = false)
            try {
                withContext(NonCancellable) {
                    mutableState.value = mutableState.value.copy(pendingEnrollment = credentials.hasPortableAccountPendingEnrollment())
                }
            } finally { commands.unlock() }
        }
    }

    private suspend fun savePending(pending: JsonObject) {
        val encoded = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("portable-enrollment", null, 0,
            portableAccountPendingCommand = pending.toString()))
        try { credentials.write(CredentialJournalSlots.portableEnrollment, encoded) } finally { encoded.fill(0) }
    }
    private suspend fun readPending(): JsonObject? {
        val bytes = credentials.read(CredentialJournalSlots.portableEnrollment) ?: return null
        return try { Json.parseToJsonElement(requireNotNull(SessionCredentialEnvelopeCodec.decode(bytes).portableAccountPendingCommand)).jsonObject }
        finally { bytes.fill(0) }
    }
}

suspend fun CredentialStore.hasPortableAccountPendingEnrollment(): Boolean {
    val bytes = read(CredentialJournalSlots.portableEnrollment) ?: return false
    bytes.fill(0)
    return true
}

internal fun accountOnly(document: PortableAccountDocument): JsonObject = buildJsonObject { put("account", document.fields) }
internal fun signed(account: UnlockedPortableAccount, purpose: String, request: JsonObject): JsonObject {
    val bytes = canonicalAccountRequest(request)
    val proof = try { account.sign(purpose, bytes) } finally { bytes.fill(0) }
    return try { buildJsonObject { put("request", request); put("proof", Base64.getEncoder().encodeToString(proof)) } } finally { proof.fill(0) }
}
internal fun hash(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
internal fun JsonObject.string(name: String): String = (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: error("ACCOUNT_RESPONSE_INVALID")
internal fun JsonObject.number(name: String): Long = (this[name] as? JsonPrimitive)?.takeUnless { it.isString }?.longOrNull ?: error("ACCOUNT_RESPONSE_INVALID")

internal fun verifyChallenge(response: JsonObject, document: PortableAccountDocument, serverId: String, epoch: Long,
    thumbprint: String, apiOrigin: String, streamOrigin: String, purpose: String) {
    require(response.keys == setOf("challenge", "account") && response["account"] == document.fields)
    val challenge = response["challenge"]!!.jsonObject
    require(challenge.string("account_id") == document.accountId && challenge.string("purpose") == purpose &&
        challenge.string("server_instance_id") == serverId && challenge.number("identity_epoch") == epoch &&
        challenge.string("identity_thumbprint_sha256") == thumbprint && challenge.string("api_origin") == apiOrigin &&
        challenge.string("stream_origin") == streamOrigin && challenge.string("document_sha256") == hash(document.encode())) { "ACCOUNT_SERVER_IDENTITY_CHANGED" }
    require(Instant.parse(challenge.string("expires_at")).isAfter(Instant.now()) &&
        Instant.parse(challenge.string("expires_at")).isBefore(Instant.now().plusSeconds(180))) { "ACCOUNT_CHALLENGE_EXPIRED" }
}
