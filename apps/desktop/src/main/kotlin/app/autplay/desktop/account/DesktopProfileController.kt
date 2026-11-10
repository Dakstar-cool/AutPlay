package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.application.portableaccount.PortableAccountDocument
import app.autplay.application.securestate.*
import app.autplay.desktop.security.*
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*

public class AccountDeviceHandle internal constructor(internal val id: String, internal val binding: String) {
    override fun toString(): String = "AccountDeviceHandle(<redacted>)"
}
public data class DesktopAccountDevice(public val handle: AccountDeviceHandle, public val name: String,
    public val platform: String, public val current: Boolean)
public data class DesktopAccountSession(public val device: AccountDeviceHandle, public val generation: Long,
    public val current: Boolean)
public data class DesktopProfileViewState(public val devices: List<DesktopAccountDevice> = emptyList(),
    public val sessions: List<DesktopAccountSession> = emptyList(), public val trainingDecision: String? = null,
    public val trainingRevision: Long? = null, public val friendsCanViewStatistics: Boolean? = null,
    public val visibilityRevision: Long? = null)
public enum class AccountExitAction { LOGOUT_CURRENT, LOGOUT_ALL, REVOKE_DEVICE, DISCONNECT_LOCAL }

/** Concrete profile controls. All mutations freeze their final request and authorization in K. */
public class DesktopProfileController(private val runtime: DesktopAccountRuntime) {
    private val mutable = MutableStateFlow(DesktopProfileViewState())
    public val state: StateFlow<DesktopProfileViewState> = mutable.asStateFlow()

    public fun enrollInvitation(rawInvitation: ByteArray): CompletionStage<Unit> {
        val input = rawInvitation.copyOf()
        return runtime.command {
            val server = runtime.requireServer(D1.Family.ENROLLMENT)
            val invitation = SecureJson.parse(input, 16_384, canonical = false)
            controlRequire(invitation.string("contract_version") == "v1" && invitation.number("schema_version") == 1L &&
                invitation.string("server_instance_id") == server.identity.serverInstanceId &&
                invitation.number("identity_epoch") == server.identity.identityEpoch &&
                invitation.string("identity_thumbprint_sha256") == server.identity.thumbprintSha256 &&
                invitation.string("api_origin") == server.identity.apiOrigin && invitation.string("stream_origin") == server.identity.streamOrigin)
            val expires = Instant.parse(invitation.string("expires_at"))
            controlRequire(expires > Instant.now(), AccountControlFailure.EXPIRED)
            val invitationId = invitation.string("invitation_id"); val account = invitation.string("user_id")
            canonicalUuid(invitationId); canonicalUuid(account)
            val secret = invitation.string("invitation_secret").toByteArray(Charsets.US_ASCII)
            val rawSecret = DesktopAccountTrust.decode(invitation.string("invitation_secret"), true, 32); rawSecret.fill(0)
            val operation = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString(); val commit = UUID.randomUUID().toString()
            runtime.secure.snapshot().thenCompose { snapshot ->
                controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
                val slot = UUID.randomUUID().toString()
                runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot, profile), 4,
                    4L * SecureStateLimits.VALUE), FirstBindOwner(operation, operation, SecureRole.RECIPIENT)).thenCompose { reserved ->
                    runtime.secure.createNewKey(reserved, operation).thenCompose { key ->
                        runtime.secure.snapshot().thenCompose { current -> runtime.secure.publicKey(current, key.tag).thenCompose { spki ->
                            val next = freshSecret(32); val nonce = freshSecret(16)
                            try {
                                val context = ProfileV2.enrollmentContext(server.identity, D1.Platform.MACOS, spki,
                                    invitationId, account, operation, commit, secureSha256(secret), secureSha256(next))
                                val body = SecureJson.encode(buildJsonObject {
                                    trustFields(server)
                                    put("exchange_id", operation); put("binding_commit_id", commit)
                                    put("invitation_id", invitationId); put("invitation_secret", secret.toString(Charsets.US_ASCII))
                                    put("expected_user_id", account); put("device_name", deviceName())
                                    put("app_version", appVersion()); put("device_public_key_spki_b64", Base64.getEncoder().encodeToString(spki))
                                    put("device_key_thumbprint_sha256", key.thumbprint); put("next_refresh_token_sha256", secureSha256(next))
                                    put("client_nonce_b64url", nonce.toString(Charsets.US_ASCII)); put("signature_algorithm", "ES256-P1363")
                                })
                                val prepared = try { ProfileV2.rawBody(body).use { ProfileV2.prepare(ProfileV2.Operation.ENROLLMENT_EXCHANGE, it, context, Instant.now()) } }
                                    finally { body.fill(0) }
                                val owner = runtime.pendingOwner(current, server, account, profile, operation, key, expires.epochSecond).copy(bindingCommit = commit)
                                val successor = runtime.secure.transientSecret(next)
                                runtime.signer.sign(current, owner, prepared).thenCompose { signed ->
                                    val bytes = successor.copy()
                                    try { runtime.custody.prepareProfile(current, slot, owner, signed, byteArrayOf(), bytes) }
                                    finally { signed.close(); bytes.fill(0) }
                                }.whenComplete { _, _ -> prepared.close(); successor.close() }
                            } finally { spki.fill(0); next.fill(0); nonce.fill(0) }
                        } }
                    }
                }
            }.thenCompose { runtime.executeSession(it, AccountEndpoint.ENROLL) }.whenComplete { _, _ -> secret.fill(0) }
        }.whenComplete { _, _ -> input.fill(0) }
    }

    public fun listDevicesAndSessions(): CompletionStage<Unit> = runtime.command {
        authorized("listDevices") { snapshot, owner, access, _ ->
            runtime.read(requireNotNull(runtime.selected).transport, AccountEndpoint.DEVICES, access = access).thenCompose { devices ->
                runtime.read(requireNotNull(runtime.selected).transport, AccountEndpoint.SESSIONS, access = access).thenApply { sessions ->
                    devices.use { deviceResponse -> sessions.use { sessionResponse ->
                        val deviceList = deviceResponse.document().array("devices")
                        val sessionList = sessionResponse.document().array("sessions")
                        controlRequire(deviceList.size <= 100 && sessionList.size <= 200)
                        val commit = requireNotNull(owner.bindingCommit)
                        val safeDevices = deviceList.mapNotNull { value ->
                            val item = value as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
                            if (item["revoked_at"] != JsonNull) null else {
                                val id = item.string("device_id"); canonicalUuid(id)
                                val name = item.string("device_name"); DesktopAccountTrust.display(name, 120)
                                val platform = item.string("platform")
                                controlRequire(platform.length in 1..32 && platform.all { it.code in 33..126 })
                                DesktopAccountDevice(AccountDeviceHandle(id, commit), name,
                                    if (platform in setOf("ANDROID", "MACOS", "WEB", "OTHER")) platform else "UNKNOWN", id == owner.device)
                            }
                        }
                        val safeSessions = sessionList.mapNotNull { value ->
                            val item = value as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
                            if (item["revoked_at"] != JsonNull || Instant.parse(item.string("absolute_expires_at")) <= Instant.now()) null
                            else {
                                val device = item.string("device_id"); canonicalUuid(device)
                                DesktopAccountSession(AccountDeviceHandle(device, commit), item.number("generation"), item.boolean("current"))
                            }
                        }
                        runtime.secure.requireAdmission(runtime.secure.admissionGeneration)
                        mutable.value = mutable.value.copy(devices = safeDevices, sessions = safeSessions)
                        Unit
                    } }
                }.whenComplete { _, failure -> if (failure != null) devices.close() }
            }
        }
    }

    public fun rotateSession(): CompletionStage<Unit> = runtime.command {
        runtime.requireServer(D1.Family.SESSION)
        val owner = runtime.binding ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
        runtime.secure.snapshot().thenCompose { snapshot ->
            prepareRenewal(snapshot, requireNotNull(owner.profile))
                .thenCompose { runtime.executeSession(it, AccountEndpoint.ROTATE) }
        }
    }

    /** Restart renewal uses the retained exact session/key; an absent active fence never authorizes it. */
    public fun renewRetainedSession(profileId: String): CompletionStage<Unit> = runtime.command {
        canonicalUuid(profileId); runtime.requireServer(D1.Family.SESSION)
        runtime.secure.snapshot().thenCompose { prepareRenewal(it, profileId) }.thenCompose { runtime.executeSession(it, AccountEndpoint.ROTATE) }
    }

    internal fun prepareRenewal(snapshot: SecureControl, profile: String,
        reconciledPending: Boolean = false): CompletableFuture<SecurePendingTicket> =
        runtime.requireNoPendingForProfile(snapshot, profile).thenCompose { runtime.secure.read(profile, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
            val session = stored.payload as? SessionPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val binding = stored.owner; val server = requireNotNull(runtime.selected)
            runtime.requireTarget(binding, server)
            val selected = snapshot.activeBinding == profile && binding.stage == SecureStage.ACTIVE
            val renewalOnly = reconciledPending && snapshot.activeBinding == null && binding.stage == SecureStage.BINDING_COMMIT_PENDING &&
                binding.accountProjection?.stage == app.autplay.application.desktop.account.AccountProtectedStage.READY
            controlRequire((selected || renewalOnly) && !binding.detached && !binding.deletionVeto &&
                session.hasCredentials && session.refreshExpiresAt > Instant.now().epochSecond, AccountControlFailure.AUTHENTICATION_REQUIRED)
            val policy = session.transportPolicyCopy()
            try { controlRequire(server.transport.matches(SecureMediaGuardCodec.readTransport(policy)), AccountControlFailure.TRUST_REQUIRED) }
            finally { policy.fill(0) }
            val operation = UUID.randomUUID().toString(); val slot = UUID.randomUUID().toString()
            val owner = binding.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision, operation = operation,
                ceremony = operation, role = SecureRole.AUTHENTICATED, stage = SecureStage.PREPARED, expiresAtEpochSeconds = session.refreshExpiresAt)
            runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot, profile), 4, 4L * SecureStateLimits.VALUE)).thenCompose { reserved ->
                runtime.secure.publicKey(reserved, requireNotNull(binding.keyTag)).thenCompose { spki ->
                    val refresh = session.refreshCopy(); val successor = freshSecret(32)
                    try {
                        val context = ProfileV2.rotationContext(server.identity, D1.Platform.MACOS, spki, requireNotNull(binding.account),
                            requireNotNull(binding.device), requireNotNull(binding.session), requireNotNull(binding.family), requireNotNull(binding.bindingCommit),
                            requireNotNull(binding.sessionGeneration), operation, secureSha256(refresh), secureSha256(successor), Instant.ofEpochSecond(session.refreshExpiresAt))
                        val body = SecureJson.encode(buildJsonObject {
                            trustFields(server); put("rotation_id", operation); put("device_id", binding.device); put("parent_session_id", binding.session)
                            put("current_generation", binding.sessionGeneration); put("current_refresh_token", refresh.toString(Charsets.US_ASCII))
                            put("next_refresh_token_sha256", secureSha256(successor)); put("signature_algorithm", "ES256-P1363")
                        })
                        val prepared = try { ProfileV2.rawBody(body).use { ProfileV2.prepare(ProfileV2.Operation.SESSION_ROTATE, it, context, Instant.now()) } } finally { body.fill(0) }
                        val retained = runtime.secure.transientSecret(successor)
                        runtime.signer.sign(reserved, owner, prepared).thenCompose { signed ->
                            val next = retained.copy()
                            try { runtime.custody.prepareProfile(reserved, slot, owner, signed, byteArrayOf(), next) }
                            finally { signed.close(); next.fill(0) }
                        }.whenComplete { _, _ -> prepared.close(); retained.close() }
                    } finally { spki.fill(0); refresh.fill(0); successor.fill(0) }
                }
            }.whenComplete { _, _ -> stored.close() }
        } }

    public fun loadPrivacy(): CompletionStage<Unit> = runtime.command {
        authorized(null) { _, owner, access, capabilities ->
            controlRequire(capabilities.expiresAt > Instant.now() && capabilities.requiredFeatures.isEmpty(),
                AccountControlFailure.UNSUPPORTED)
            val server = requireNotNull(runtime.selected)
            // Training is an optional independent feature. Its absence grants no training read
            // and must not prevent the ordinary current-owner statistics-visibility read.
            val consent: CompletableFuture<Pair<String, Long>?> = if ("shared_training_consent" in capabilities.operations) {
                capabilities.requireOperation("shared_training_consent", Instant.now())
                runtime.read(server.transport, AccountEndpoint.CONSENT_GET, access = access).thenApply { response ->
                    response.use {
                        val value = it.document()
                        requireConsent(value, requireNotNull(owner.account))
                        value.string("decision") to value.number("revision")
                    }
                }
            } else CompletableFuture.completedFuture(null)
            consent.thenCompose { training ->
                runtime.read(server.transport, AccountEndpoint.VISIBILITY_GET, access = access).thenApply { visibility ->
                    visibility.use {
                        runtime.requireTarget(owner, server)
                        controlRequire(capabilities.expiresAt > Instant.now(), AccountControlFailure.UNSUPPORTED)
                        val v = it.document()
                        v.exact("schema_version", "friends_can_view_statistics", "revision")
                        controlRequire(v.number("schema_version") == 1L && v.number("revision") >= 0)
                        mutable.value = mutable.value.copy(trainingDecision = training?.first, trainingRevision = training?.second,
                            friendsCanViewStatistics = v.boolean("friends_can_view_statistics"), visibilityRevision = v.number("revision"))
                        Unit
                    }
                }
            }
        }
    }

    /** Explicit confirmed intent, never an optimistic settings toggle. Unknown outcomes retain K. */
    public fun decideTraining(decision: String): CompletionStage<Unit> {
        val expected = mutable.value.trainingRevision
            ?: return CompletableFuture.failedFuture(AccountControlException(AccountControlFailure.CONFLICT))
        return decideTraining(decision, expected)
    }
    public fun decideTraining(decision: String, expectedRevision: Long): CompletionStage<Unit> = runtime.command {
        controlRequire(decision in setOf("GRANTED", "DENIED", "WITHDRAWN"))
        controlRequire(expectedRevision >= 0 && mutable.value.trainingRevision == expectedRevision, AccountControlFailure.CONFLICT)
        val expected = expectedRevision
        authorized("shared_training_consent") { snapshot, binding, access, _ ->
            val operation = UUID.randomUUID().toString()
            val owner = pending(binding, operation)
            val body = SecureJson.encode(buildJsonObject {
                put("operation_id", operation); put("account_id", binding.account); put("expected_revision", expected)
                put("decision", decision); put("policy_version", 1)
            })
            val slot = consentSlot(requireNotNull(binding.profile), requireNotNull(binding.account), requireNotNull(binding.apiOrigin))
            try { runtime.custody.prepareLifecycle(snapshot, slot, owner, PendingOperation.CONSENT, body, access, DesktopFrozenPendingVerifier::verifyLifecycle) }
            finally { body.fill(0) }
        }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.CONSENT_DECIDE).thenCompose { result ->
            val raw = result.response.copy()
            try {
                val receipt = SecureJson.parse(raw, 4096, canonical = false)
                controlRequire(receipt.string("operation_id") == ticket.owner.operation && receipt.number("applied_revision") > expected &&
                    receipt.string("applied_decision") == decision)
                val policy = JsonObject(receipt.filterKeys { it !in setOf("operation_id", "applied_revision", "applied_decision") })
                requireConsent(policy, requireNotNull(ticket.owner.account))
                controlRequire(policy.number("revision") >= receipt.number("applied_revision"))
                runtime.recordTerminal(result.ticket, raw, { owner, _ -> owner.operation == receipt.string("operation_id") }, false).thenApply {
                    mutable.value = mutable.value.copy(trainingDecision = policy.string("decision"), trainingRevision = policy.number("revision")); Unit
                }
            } finally { raw.fill(0); result.close() }
        } }
    }

    public fun decideStatisticsVisibility(enabled: Boolean): CompletionStage<Unit> {
        val expected = mutable.value.visibilityRevision
            ?: return CompletableFuture.failedFuture(AccountControlException(AccountControlFailure.CONFLICT))
        return decideStatisticsVisibility(enabled, expected)
    }
    public fun decideStatisticsVisibility(enabled: Boolean, expectedRevision: Long): CompletionStage<Unit> = runtime.command {
        controlRequire(expectedRevision >= 0 && mutable.value.visibilityRevision == expectedRevision, AccountControlFailure.CONFLICT)
        val expected = expectedRevision
        authorized(null) { snapshot, binding, access, _ ->
            val operation = UUID.randomUUID().toString()
            val body = SecureJson.encode(buildJsonObject {
                put("operation_id", operation); put("expected_revision", expected); put("friends_can_view_statistics", enabled)
            })
            try { runtime.custody.prepareLifecycle(snapshot, UUID.randomUUID().toString(), pending(binding, operation),
                PendingOperation.VISIBILITY, body, access, DesktopFrozenPendingVerifier::verifyLifecycle) } finally { body.fill(0) }
        }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.VISIBILITY_DECIDE).thenCompose { result ->
            val raw = result.response.copy()
            try {
                val receipt = SecureJson.parse(raw, 4096, canonical = false)
                receipt.exact("schema_version", "operation_id", "friends_can_view_statistics", "revision")
                controlRequire(receipt.number("schema_version") == 1L && receipt.string("operation_id") == ticket.owner.operation &&
                    receipt.number("revision") > expected && receipt.boolean("friends_can_view_statistics") == enabled)
                runtime.recordTerminal(result.ticket, raw, { owner, _ -> owner.operation == receipt.string("operation_id") }, false).thenApply {
                    mutable.value = mutable.value.copy(friendsCanViewStatistics = enabled, visibilityRevision = receipt.number("revision")); Unit
                }
            } finally { raw.fill(0); result.close() }
        } }
    }

    public fun exit(action: AccountExitAction, confirmed: Boolean, password: CharArray = charArrayOf(), target: AccountDeviceHandle? = null): CompletionStage<Unit> {
        val secret = password.copyOf()
        return runtime.command {
            controlRequire(confirmed)
            val capability = when (action) { AccountExitAction.LOGOUT_CURRENT -> "logoutCurrentSession"
                AccountExitAction.LOGOUT_ALL -> "logoutAllSessions"; AccountExitAction.REVOKE_DEVICE -> "revokeDevice"
                AccountExitAction.DISCONNECT_LOCAL -> null }
            authorized(capability) { snapshot, binding, access, _ ->
                controlRequire(target == null || target.binding == binding.bindingCommit, AccountControlFailure.CONTEXT_CHANGED)
                controlRequire((action == AccountExitAction.REVOKE_DEVICE) == (target != null))
                val operation = UUID.randomUUID().toString()
                reauthenticate(snapshot, binding, access, action, operation, target?.id, secret).thenCompose { authorization ->
                    authorization.use {
                        if (action == AccountExitAction.DISCONNECT_LOCAL) runtime.secure.detach(snapshot).thenApply { runtime.retire(); Unit }
                        else {
                            val rawAuthorization = it?.copy()
                            val body = try { SecureJson.encode(buildJsonObject {
                                put("contract_version", "v1"); put("schema_version", 1); put("operation_id", operation)
                                if (rawAuthorization != null) put("portable_authorization", SecureJson.parse(rawAuthorization, 16_384))
                            }) } finally { rawAuthorization?.fill(0) }
                            val variant = when (action) { AccountExitAction.LOGOUT_CURRENT -> PendingOperation.LOGOUT_CURRENT
                                AccountExitAction.LOGOUT_ALL -> PendingOperation.LOGOUT_ALL; else -> PendingOperation.REVOKE }
                            val endpoint = when (action) { AccountExitAction.LOGOUT_CURRENT -> AccountEndpoint.LOGOUT_CURRENT
                                AccountExitAction.LOGOUT_ALL -> AccountEndpoint.LOGOUT_ALL; else -> AccountEndpoint.REVOKE_DEVICE }
                            try { runtime.custody.prepareLifecycle(snapshot, UUID.randomUUID().toString(), pending(binding, operation).copy(claim = target?.id), variant, body, access, DesktopFrozenPendingVerifier::verifyLifecycle) }
                            finally { body.fill(0) }
                                .thenCompose { ticket -> runtime.executePending(ticket, endpoint).thenCompose { result ->
                                    val receipt = result.response.copy()
                                    try {
                                        val value = SecureJson.parse(receipt, 4096, canonical = false)
                                        verifyLifecycleReceipt(value, operation)
                                        val inactive = action != AccountExitAction.REVOKE_DEVICE || target?.id == binding.device
                                        runtime.recordTerminal(result.ticket, receipt, { owner, _ -> owner.operation == operation }, inactive)
                                            .thenApply { if (inactive) runtime.retire(); Unit }
                                    } finally { receipt.fill(0); result.close() }
                                } }
                        }
                    }
                }
            }
        }.whenComplete { _, _ -> secret.fill('\u0000') }
    }

    public fun resumeProtectedCommand(slot: String): CompletionStage<Unit> = runtime.command {
        canonicalUuid(slot)
        runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val pending = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            runtime.requireTarget(stored.owner, requireNotNull(runtime.selected))
            val endpoint = when (pending.operation) {
                PendingOperation.CONSENT -> AccountEndpoint.CONSENT_DECIDE
                PendingOperation.VISIBILITY -> AccountEndpoint.VISIBILITY_DECIDE
                PendingOperation.LOGOUT_CURRENT -> AccountEndpoint.LOGOUT_CURRENT
                PendingOperation.LOGOUT_ALL -> AccountEndpoint.LOGOUT_ALL
                PendingOperation.REVOKE -> AccountEndpoint.REVOKE_DEVICE
                else -> throw AccountControlException(AccountControlFailure.CONFLICT)
            }
            val original = pending.requestCopy()
            val request = try { SecureJson.parse(original, 16_384) } finally { original.fill(0) }
            runtime.resumeProtected(snapshot, slot, stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision)).thenCompose { ticket ->
                runtime.executePending(ticket, endpoint).thenCompose { result ->
                    val raw = result.response.copy()
                    try {
                        val receipt = SecureJson.parse(raw, 4096, canonical = false)
                        controlRequire(receipt.string("operation_id") == ticket.owner.operation && receipt.string("operation_id") == request.string("operation_id"))
                        when (endpoint) {
                            AccountEndpoint.CONSENT_DECIDE -> {
                                val decision = request.string("decision"); val applied = receipt.string("applied_decision")
                                controlRequire(receipt.number("applied_revision") > request.number("expected_revision") &&
                                    (if (decision == "GRANTED") applied == decision else applied in setOf("DENIED", "WITHDRAWN")))
                                val policy = JsonObject(receipt.filterKeys { it !in setOf("operation_id", "applied_revision", "applied_decision") })
                                requireConsent(policy, requireNotNull(ticket.owner.account))
                                controlRequire(policy.number("revision") >= receipt.number("applied_revision"))
                            }
                            AccountEndpoint.VISIBILITY_DECIDE -> {
                                receipt.exact("schema_version", "operation_id", "friends_can_view_statistics", "revision")
                                controlRequire(receipt.number("schema_version") == 1L && receipt.number("revision") > request.number("expected_revision") &&
                                    receipt.boolean("friends_can_view_statistics") == request.boolean("friends_can_view_statistics"))
                            }
                            else -> verifyLifecycleReceipt(receipt, requireNotNull(ticket.owner.operation))
                        }
                        val inactive = pending.operation in setOf(PendingOperation.LOGOUT_CURRENT, PendingOperation.LOGOUT_ALL) ||
                            pending.operation == PendingOperation.REVOKE && ticket.owner.claim == ticket.owner.device
                        runtime.recordTerminal(result.ticket, raw, { owner, _ -> owner.operation == receipt.string("operation_id") }, inactive).thenApply {
                            if (inactive) runtime.retire()
                            else if (endpoint == AccountEndpoint.CONSENT_DECIDE) mutable.value = mutable.value.copy(trainingDecision = receipt.string("decision"), trainingRevision = receipt.number("revision"))
                            else if (endpoint == AccountEndpoint.VISIBILITY_DECIDE) mutable.value = mutable.value.copy(friendsCanViewStatistics = receipt.boolean("friends_can_view_statistics"), visibilityRevision = receipt.number("revision"))
                            Unit
                        }
                    } finally { raw.fill(0); result.close() }
                }
            }.whenComplete { _, _ -> stored.close() }
        } }
    }

    internal fun <T> authorized(operation: String?, work: (SecureControl, SecureRecordOwner, ByteArray, AccountM5Capabilities) -> CompletableFuture<T>): CompletableFuture<T> {
        val binding = runtime.binding ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
        val server = requireNotNull(runtime.selected); runtime.requireTarget(binding, server)
        return runtime.secure.snapshot().thenCompose { snapshot ->
            runtime.secure.read(requireNotNull(binding.profile), snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
                val payload = stored.payload as? SessionPayload ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                controlRequire(stored.owner == binding && snapshot.activeBinding == binding.profile && !binding.deletionVeto && !binding.detached)
                val policy = payload.transportPolicyCopy()
                try { controlRequire(server.transport.matches(SecureMediaGuardCodec.readTransport(policy)), AccountControlFailure.TRUST_REQUIRED) }
                finally { policy.fill(0) }
                runtime.secure.access(snapshot, binding).thenCompose { lease ->
                    val access = lease.copy()
                    runtime.read(server.transport, AccountEndpoint.M5_CAPABILITIES, access = access).thenCompose { response ->
                        response.use {
                            val bytes = it.bodyCopy()
                            val capabilities = try { DesktopAccountTrust.capabilities(bytes, server.identity,
                                requireNotNull(binding.account), requireNotNull(binding.device), payload.m5, Instant.now()) } finally { bytes.fill(0) }
                            // Changed high-water must be published by K before any effect, never cached in UI.
                            controlRequire(capabilities.guard == payload.m5, AccountControlFailure.CONTEXT_CHANGED)
                            if (operation != null) capabilities.requireOperation(operation, Instant.now())
                            work(snapshot, binding, access, capabilities)
                        }
                    }.whenComplete { _, _ -> access.fill(0); lease.close() }
                }.whenComplete { _, _ -> stored.close() }
            }
        }
    }

    private fun reauthenticate(snapshot: SecureControl, binding: SecureRecordOwner, access: ByteArray,
        action: AccountExitAction, operation: String, target: String?, password: CharArray): CompletableFuture<SecretBytes?> {
        val server = requireNotNull(runtime.selected)
        return runtime.secure.read(requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).thenCompose { nullable ->
            if (nullable == null) return@thenCompose CompletableFuture.completedFuture<SecretBytes?>(null)
            val stored = nullable
            val root = stored.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
            val passport = root.passportCopy()
            val document = try { PortableAccountDocument.parse(passport) } finally { passport.fill(0) }
            controlRequire(document.accountId == binding.account)
            runtime.roots.unlock(snapshot, password).thenCompose { unlocked ->
                val challengeRequest = SecureJson.encode(buildJsonObject {
                    put("action", action.name); put("operation_id", operation); put("target_device_id", target?.let(::JsonPrimitive) ?: JsonNull)
                })
                val challenge = if (action == AccountExitAction.DISCONNECT_LOCAL) CompletableFuture.completedFuture(buildJsonObject {
                    put("account", document.fields); put("challenge", buildJsonObject {
                        put("account_id", binding.account); put("action", action.name); put("operation_id", operation)
                        put("binding_commit_id", binding.bindingCommit); put("server_profile_id", binding.profile)
                        put("session_family_id", binding.family); put("expires_at", Instant.now().plusSeconds(120).toString())
                        put("nonce", UUID.randomUUID().toString())
                    })
                }) else runtime.read(server.transport, AccountEndpoint.PORTABLE_REAUTH_CHALLENGE, challengeRequest, access).thenApply { it.use { response -> response.document() } }
                challenge.thenApply<SecretBytes?> { request ->
                    val c = request["challenge"] as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
                    controlRequire(request["account"] == document.fields && c.string("account_id") == binding.account &&
                        c.string("action") == action.name && c.string("operation_id") == operation)
                    if (action != AccountExitAction.DISCONNECT_LOCAL) controlRequire(c.string("server_instance_id") == binding.server &&
                        c.number("identity_epoch") == binding.identityEpoch && c.string("identity_thumbprint_sha256") == binding.identityThumbprint &&
                        c.string("api_origin") == binding.apiOrigin && c.string("stream_origin") == binding.streamOrigin &&
                        c.string("actor_device_id") == binding.device && c.string("actor_session_id") == binding.session && c["target_device_id"] == (target?.let(::JsonPrimitive) ?: JsonNull))
                    val expires = Instant.parse(c.string("expires_at"))
                    controlRequire(expires > Instant.now() && expires < Instant.now().plusSeconds(180), AccountControlFailure.EXPIRED)
                    val bytes = SecureJson.encode(request)
                    val proof = try { unlocked.signLegacy("reauth", bytes) } finally { bytes.fill(0) }
                    val wire = try { SecureJson.encode(buildJsonObject { put("request", request); put("proof", Base64.getEncoder().encodeToString(proof)) }) } finally { proof.fill(0) }
                    try { runtime.secure.transientSecret(wire) } finally { wire.fill(0) }
                }.whenComplete { _, _ -> unlocked.close(); challengeRequest.fill(0) }
            }.whenComplete { _, _ -> stored.close() }
        }
    }

    private fun requireConsent(value: JsonObject, account: String) {
        value.exact("schema_version", "account_id", "decision", "revision", "policy_version", "changed_at")
        controlRequire(value.number("schema_version") == 1L && value.number("policy_version") == 1L && value.string("account_id") == account)
        val decision = value.string("decision"); val revision = value.number("revision")
        controlRequire(decision in setOf("UNKNOWN", "GRANTED", "DENIED", "WITHDRAWN") && (decision == "UNKNOWN") == (revision == 0L))
        if (decision == "UNKNOWN") controlRequire(value["changed_at"] == JsonNull) else Instant.parse(value.string("changed_at"))
    }
    private fun pending(binding: SecureRecordOwner, operation: String): SecureRecordOwner = binding.copy(
        operation = operation, ceremony = operation, stage = SecureStage.PREPARED, role = SecureRole.AUTHENTICATED)
    private fun deviceName(): String = runtime.displayDeviceName
    private fun appVersion(): String = runtime.applicationVersion
}

internal fun JsonObjectBuilder.trustFields(server: AccountSelectedServer) {
    put("contract_version", "v2"); put("schema_version", 2); put("platform", "MACOS")
    expectedTrustFields(server)
}
internal fun JsonObjectBuilder.expectedTrustFields(server: AccountSelectedServer) {
    put("expected_server_instance_id", server.identity.serverInstanceId); put("expected_identity_epoch", server.identity.identityEpoch)
    put("expected_identity_thumbprint_sha256", server.identity.thumbprintSha256)
    put("expected_api_origin", server.identity.apiOrigin); put("expected_stream_origin", server.identity.streamOrigin)
}

internal fun verifyLifecycleReceipt(value: JsonObject, operation: String) {
    value.exact("contract_version", "schema_version", "operation_id", "outcome", "terminal_at")
    controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L && value.string("operation_id") == operation &&
        value.string("outcome") in setOf("APPLIED", "ALREADY_TERMINAL") && value.string("terminal_at").endsWith('Z'))
    controlRequire(Instant.parse(value.string("terminal_at")) <= Instant.now().plusSeconds(120))
}
