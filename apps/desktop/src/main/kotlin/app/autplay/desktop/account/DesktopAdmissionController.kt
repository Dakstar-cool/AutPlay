package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.application.securestate.*
import app.autplay.desktop.security.*
import java.nio.ByteBuffer
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*

public data class DesktopAdmissionViewState(public val state: String = "IDLE", public val accountLabel: String? = null)

/** Admission and trusted-key bootstrap have independent D1 proofs; neither masquerades as Android. */
public class DesktopAdmissionController(private val runtime: DesktopAccountRuntime) : AutoCloseable {
    private val mutable = MutableStateFlow(DesktopAdmissionViewState())
    public val state = mutable.asStateFlow()
    private var parent: ProfileV2.SignedCommand? = null
    private var owner: SecureRecordOwner? = null
    private var slot: String? = null
    private var response: JsonObject? = null
    private var earliestPollNanos = 0L

    public fun requestAdmission(nickname: String): CompletionStage<Unit> = runtime.command {
        DesktopAccountTrust.display(nickname, 120)
        val server = runtime.requireServer(D1.Family.ADMISSION)
        runtime.secure.snapshot().thenCompose { snapshot ->
            controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty() &&
                snapshot.selected.none { it.slot == SecureRecordKind.PORTABLE_ROOT.reserved }, AccountControlFailure.CONFLICT)
            val operation = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString(); val pendingSlot = UUID.randomUUID().toString()
            runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(pendingSlot, profile), 4, 4L * SecureStateLimits.VALUE),
                FirstBindOwner(operation, operation, SecureRole.RECIPIENT)).thenCompose { reserved ->
                runtime.secure.createNewKey(reserved, operation).thenCompose { key -> runtime.secure.snapshot().thenCompose { current ->
                    runtime.secure.publicKey(current, key.tag).thenCompose { spki ->
                        val nonce = freshSecret(32)
                        try {
                            val context = ProfileV2.admissionSubmitContext(server.identity, D1.Platform.MACOS, spki, operation)
                            val bytes = SecureJson.encode(buildJsonObject {
                                trustFields(server); keyFields(spki, requireNotNull(key.thumbprint))
                                put("request_id", operation); put("client_nonce_b64url", nonce.toString(Charsets.US_ASCII))
                                put("api_major", 1); put("nickname", nickname); put("app_version", runtime.applicationVersion); put("requested_at", Instant.now().toString())
                            })
                            val prepared = try { ProfileV2.rawBody(bytes).use { ProfileV2.prepare(ProfileV2.Operation.ADMISSION_SUBMIT, it, context, Instant.now()) } }
                                finally { bytes.fill(0) }
                            val pendingOwner = runtime.pendingOwner(current, server, null, profile, operation, key, null).copy(bindingCommit = null)
                            runtime.signer.sign(current, pendingOwner, prepared).thenCompose { signed ->
                                val original = signed.transientWireCopy()
                                val auth = try { AdmissionSecretEnvelope.encode(byteArrayOf(), original) } finally { original.fill(0) }
                                try { runtime.custody.prepareProfile(current, pendingSlot, pendingOwner, signed, auth, byteArrayOf()).thenApply {
                                    parent?.close(); parent = signed; slot = pendingSlot; it
                                } } finally { auth.fill(0) }
                            }.whenComplete { _, _ -> prepared.close() }
                        } finally { spki.fill(0); nonce.fill(0) }
                    }
                } }
            }
        }.thenCompose { ticket -> sendProgress(ticket, AccountEndpoint.ADMISSION_SUBMIT, created = true) }
    }

    /** Reuses a proven EXISTING protected key. It never creates a replacement key on lookup failure. */
    public fun beginTrustedReenrollment(retainedProfile: String): CompletionStage<Unit> = runtime.command {
        canonicalUuid(retainedProfile)
        val server = runtime.requireServer(D1.Family.TRUSTED)
        runtime.secure.snapshot().thenCompose { snapshot ->
            controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
            runtime.secure.read(retainedProfile, snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                controlRequire(stored.payload is SessionPayload && !stored.owner.deletionVeto)
                runtime.requireTarget(stored.owner, server)
                val operation = UUID.randomUUID().toString(); val pendingSlot = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString()
                val rootSlot = SecureRecordKind.PORTABLE_ROOT.reserved?.takeIf { marker -> snapshot.selected.any { it.slot == marker } }
                runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOfNotNull(pendingSlot, profile, rootSlot), 4, 4L * SecureStateLimits.VALUE),
                    FirstBindOwner(operation, operation, SecureRole.RECIPIENT)).thenCompose { current ->
                    runtime.secure.publicKey(current, requireNotNull(stored.owner.keyTag)).thenCompose { spki ->
                        val nonce = freshSecret(32)
                        try {
                            val context = ProfileV2.trustedChallengeContext(server.identity, D1.Platform.MACOS, spki, requireNotNull(stored.owner.account), operation)
                            val bytes = SecureJson.encode(buildJsonObject {
                                trustFields(server); keyFields(spki, requireNotNull(stored.owner.keyThumbprint)); put("account_id", stored.owner.account)
                                put("challenge_request_id", operation); put("client_nonce_b64url", nonce.toString(Charsets.US_ASCII))
                            })
                            val prepared = try { ProfileV2.rawBody(bytes).use { ProfileV2.prepare(ProfileV2.Operation.TRUSTED_CHALLENGE, it, context, Instant.now()) } }
                                finally { bytes.fill(0) }
                            val pendingOwner = stored.owner.copy(boot = runtime.secure.boot, authorityRevision = current.authorityRevision, profile = profile,
                                operation = operation, ceremony = operation, role = SecureRole.RECIPIENT, bindingCommit = null, session = null, family = null,
                                sessionGeneration = null, device = null, detached = false, stage = SecureStage.PREPARED)
                            runtime.signer.sign(current, pendingOwner, prepared).thenCompose { signed ->
                                val original = signed.transientWireCopy(); val auth = try { AdmissionSecretEnvelope.encode(byteArrayOf(), original) } finally { original.fill(0) }
                                try { runtime.custody.prepareProfile(current, pendingSlot, pendingOwner, signed, auth, byteArrayOf()).thenApply {
                                    parent?.close(); parent = signed; slot = pendingSlot; it
                                } } finally { auth.fill(0) }
                            }.whenComplete { _, _ -> prepared.close() }
                        } finally { nonce.fill(0); spki.fill(0) }
                    }
                }.whenComplete { _, _ -> stored.close() }
            }
        }.thenCompose { ticket -> sendProgress(ticket, AccountEndpoint.TRUSTED_CHALLENGE, created = false) }
    }

    public fun poll(): CompletionStage<Unit> = continueAdmission(exchange = false, recover = false, confirmed = false)
    public fun recoverPollOwnership(): CompletionStage<Unit> = continueAdmission(false, true, false)
    public fun confirmApprovedAccount(confirmed: Boolean): CompletionStage<Unit> = continueAdmission(true, false, confirmed)

    private fun continueAdmission(exchange: Boolean, recover: Boolean, confirmed: Boolean): CompletableFuture<Unit> = runtime.command {
        val previous = requireNotNull(owner); val server = requireNotNull(runtime.selected); val original = requireNotNull(parent)
        controlRequire(original.operation == ProfileV2.Operation.ADMISSION_SUBMIT)
        controlRequire(System.nanoTime() >= earliestPollNanos, AccountControlFailure.CAPACITY)
        if (exchange) controlRequire(confirmed && response?.string("state") == "APPROVED", AccountControlFailure.CONFLICT)
        runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(requireNotNull(slot), snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val retained = (stored.payload as PendingPayload).ceremonyAuthorizationCopy()
            val envelope = try { AdmissionSecretEnvelope.read(retained) } finally { retained.fill(0) }
            val bearer = envelope.first.copy(); val refresh = if (exchange) freshSecret(32) else byteArrayOf(); val nonce = freshSecret(32)
            runtime.secure.publicKey(snapshot, requireNotNull(previous.keyTag)).thenCompose { spki ->
                try {
                    val operation = UUID.randomUUID().toString(); val commit = if (exchange) UUID.randomUUID().toString() else null
                    val account = if (exchange) requireNotNull(response).string("approved_account_id") else null
                    val kind = if (exchange) ProfileV2.Operation.ADMISSION_EXCHANGE else if (recover) ProfileV2.Operation.ADMISSION_RECOVER else ProfileV2.Operation.ADMISSION_POLL
                    val context = when (kind) {
                        ProfileV2.Operation.ADMISSION_EXCHANGE -> ProfileV2.admissionExchangeContext(original, server.identity, D1.Platform.MACOS, spki,
                            requireNotNull(account), operation, requireNotNull(commit), secureSha256(bearer), secureSha256(refresh))
                        ProfileV2.Operation.ADMISSION_RECOVER -> ProfileV2.admissionRecoverContext(original, server.identity, D1.Platform.MACOS, spki)
                        else -> ProfileV2.admissionPollContext(original, server.identity, D1.Platform.MACOS, spki)
                    }
                    val originalDigest = original.requestDigestCopy()
                    val bytes = try { SecureJson.encode(buildJsonObject {
                        trustFields(server); put("request_id", previous.ceremony); put("device_key_thumbprint_sha256", previous.keyThumbprint)
                        if (recover || exchange) put("request_sha256", originalDigest.joinToString("") { "%02x".format(it.toInt() and 255) })
                        put(if (recover) "recovery_nonce_b64url" else "client_nonce_b64url", nonce.toString(Charsets.US_ASCII))
                        if (exchange) {
                            put("device_public_key_jwk", jwk(spki)); put("exchange_id", operation); put("binding_commit_id", commit)
                            put("approved_account_id", account); put("poll_bearer_sha256", secureSha256(bearer)); put("next_refresh_token_sha256", secureSha256(refresh))
                        }
                    }) } finally { originalDigest.fill(0) }
                    val prepared = try { ProfileV2.rawBody(bytes).use { ProfileV2.prepare(kind, it, context, Instant.now()) } } finally { bytes.fill(0) }
                    val next = previous.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision, operation = operation,
                        account = account, bindingCommit = commit, stage = SecureStage.PREPARED)
                    val successor = runtime.secure.transientSecret(refresh)
                    runtime.signer.sign(snapshot, previous.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision), prepared).thenCompose { signed ->
                        val originalWire = envelope.second.copy(); val auth = try { AdmissionSecretEnvelope.encode(bearer, originalWire) } finally { originalWire.fill(0) }
                        val nextRefresh = successor.copy()
                        try { handoff(snapshot, stored, next, signed, auth, nextRefresh) }
                        finally { signed.close(); auth.fill(0); nextRefresh.fill(0) }
                    }.whenComplete { _, _ -> prepared.close(); successor.close() }
                } finally { spki.fill(0) }
            }.thenCompose { ticket -> if (exchange) runtime.executeSession(ticket, AccountEndpoint.ADMISSION_EXCHANGE)
                else sendProgress(ticket, if (recover) AccountEndpoint.ADMISSION_RECOVER else AccountEndpoint.ADMISSION_POLL, recover) }
                .whenComplete { _, _ -> stored.close(); envelope.first.close(); envelope.second.close(); bearer.fill(0); refresh.fill(0); nonce.fill(0) }
        } }
    }

    public fun completeTrustedReenrollment(confirmedAccount: Boolean): CompletionStage<Unit> = runtime.command {
        controlRequire(confirmedAccount)
        val previous = requireNotNull(owner); val server = requireNotNull(runtime.selected); val original = requireNotNull(parent)
        controlRequire(original.operation == ProfileV2.Operation.TRUSTED_CHALLENGE)
        val challenge = requireNotNull(response); val credential = challenge.string("challenge").toByteArray(Charsets.US_ASCII)
        val expires = Instant.parse(challenge.string("expires_at"))
        runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(requireNotNull(slot), snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            runtime.secure.publicKey(snapshot, requireNotNull(previous.keyTag)).thenCompose { spki ->
                val refresh = freshSecret(32); val nonce = freshSecret(32)
                val operation = UUID.randomUUID().toString(); val commit = UUID.randomUUID().toString()
                try {
                    val context = ProfileV2.trustedExchangeContext(original, server.identity, D1.Platform.MACOS, spki,
                        challenge.string("challenge_id"), credential, operation, commit, secureSha256(refresh), expires, Instant.now())
                    val bytes = SecureJson.encode(buildJsonObject {
                        trustFields(server); keyFields(spki, requireNotNull(previous.keyThumbprint)); put("account_id", previous.account)
                        put("challenge_id", challenge.string("challenge_id")); put("challenge", credential.toString(Charsets.US_ASCII))
                        put("exchange_id", operation); put("binding_commit_id", commit); put("next_refresh_token_sha256", secureSha256(refresh))
                        put("client_nonce_b64url", nonce.toString(Charsets.US_ASCII))
                    })
                    val prepared = try { ProfileV2.rawBody(bytes).use { ProfileV2.prepare(ProfileV2.Operation.TRUSTED_EXCHANGE, it, context, Instant.now()) } } finally { bytes.fill(0) }
                    val next = previous.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision, operation = operation,
                        bindingCommit = commit, stage = SecureStage.PREPARED)
                    val successor = runtime.secure.transientSecret(refresh)
                    runtime.signer.sign(snapshot, previous.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision), prepared).thenCompose { signed ->
                        val originalWire = original.transientWireCopy(); val auth = try { AdmissionSecretEnvelope.encode(byteArrayOf(), originalWire) } finally { originalWire.fill(0) }
                        val nextRefresh = successor.copy()
                        try { handoff(snapshot, stored, next, signed, auth, nextRefresh) }
                        finally { signed.close(); auth.fill(0); nextRefresh.fill(0) }
                    }.whenComplete { _, _ -> prepared.close(); successor.close() }
                } finally { spki.fill(0); refresh.fill(0); nonce.fill(0) }
            }.thenCompose { runtime.executeSession(it, AccountEndpoint.TRUSTED_EXCHANGE) }.whenComplete { _, _ -> stored.close() }
        } }.whenComplete { _, _ -> credential.fill(0) }
    }

    public fun restoreProtectedRequest(pendingSlot: String): CompletionStage<Unit> = runtime.command {
        canonicalUuid(pendingSlot)
        val server = requireNotNull(runtime.selected)
        runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(pendingSlot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            runtime.requireTarget(stored.owner, server)
            val pending = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            controlRequire(pending.operation in setOf(PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE) && stored.owner.stage != SecureStage.TERMINAL)
            val auth = pending.ceremonyAuthorizationCopy(); val material = try { AdmissionSecretEnvelope.read(auth) } finally { auth.fill(0) }
            val original = material.second.copy(); val receipt = pending.receiptCopy(); val current = pending.requestCopy()
            runtime.secure.publicKey(snapshot, requireNotNull(stored.owner.keyTag)).thenCompose { spki ->
                try {
                    val doc = SecureJson.parse(original, 16_384)
                    val kind = if ("challenge_request_id" in doc) ProfileV2.Operation.TRUSTED_CHALLENGE else ProfileV2.Operation.ADMISSION_SUBMIT
                    val context = if (kind == ProfileV2.Operation.TRUSTED_CHALLENGE) ProfileV2.trustedChallengeContext(server.identity,
                        D1.Platform.MACOS, spki, requireNotNull(stored.owner.account), doc.string("challenge_request_id"))
                        else ProfileV2.admissionSubmitContext(server.identity, D1.Platform.MACOS, spki, doc.string("request_id"))
                    parent?.close(); parent = app.autplay.application.desktop.accountflow.DesktopRetainedCeremonyCodecs.profileParent(kind, original, context)
                    val resumed = stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision)
                    runtime.resumeProtected(snapshot, pendingSlot, resumed).thenCompose { ticket ->
                        owner = ticket.owner; slot = pendingSlot
                        if (stored.owner.stage == SecureStage.RECORDED) {
                            response = SecureJson.parse(receipt, 65_536, canonical = false)
                            val state = if (kind == ProfileV2.Operation.TRUSTED_CHALLENGE) "TRUSTED_CHALLENGE"
                                else (response?.get("state") as? JsonPrimitive)?.content ?: "PENDING"
                            mutable.value = DesktopAdmissionViewState(state, (response?.get("approved_account_label") as? JsonPrimitive)?.content)
                            CompletableFuture.completedFuture(Unit)
                        } else {
                            val endpoint = when (pending.wireKind) {
                                PendingWireKind.TRUSTED_CHALLENGE -> AccountEndpoint.TRUSTED_CHALLENGE
                                PendingWireKind.TRUSTED_EXCHANGE -> AccountEndpoint.TRUSTED_EXCHANGE
                                PendingWireKind.ADMISSION_SUBMIT -> AccountEndpoint.ADMISSION_SUBMIT
                                PendingWireKind.ADMISSION_EXCHANGE -> AccountEndpoint.ADMISSION_EXCHANGE
                                PendingWireKind.ADMISSION_RECOVER -> AccountEndpoint.ADMISSION_RECOVER
                                PendingWireKind.ADMISSION_POLL -> AccountEndpoint.ADMISSION_POLL
                                else -> throw AccountControlException(AccountControlFailure.CONFLICT)
                            }
                            if (endpoint in setOf(AccountEndpoint.ADMISSION_EXCHANGE, AccountEndpoint.TRUSTED_EXCHANGE)) runtime.executeSession(ticket, endpoint)
                            else sendProgress(ticket, endpoint, endpoint in setOf(AccountEndpoint.ADMISSION_SUBMIT, AccountEndpoint.ADMISSION_RECOVER))
                        }
                    }
                } finally { spki.fill(0) }
            }.whenComplete { _, _ -> original.fill(0); current.fill(0); receipt.fill(0); material.first.close(); material.second.close(); stored.close() }
        } }
    }

    /** Only an explicit reveal surface may receive the short-lived administrator review locator. */
    public fun reviewLocatorForExplicitDisplay(): SecretBytes {
        val value = requireNotNull(response).string("review_locator").toByteArray(Charsets.US_ASCII)
        return try { runtime.secure.transientSecret(value) } finally { value.fill(0) }
    }

    private fun sendProgress(ticket: SecurePendingTicket, endpoint: AccountEndpoint, created: Boolean): CompletableFuture<Unit> =
        runtime.executePending(ticket, endpoint).thenCompose { result ->
            val raw = result.response.copy()
            val value = try { SecureJson.parse(raw, 65_536, canonical = false) } catch (failure: Exception) { raw.fill(0); result.close(); throw failure }
            controlRequire(value.string("contract_version") == "v2" && value.number("schema_version") == 2L && value.string("platform") == "MACOS")
            if (endpoint == AccountEndpoint.TRUSTED_CHALLENGE) {
                controlRequire(value.string("challenge_id") == ticket.owner.ceremony && value.string("device_key_thumbprint_sha256") == ticket.owner.keyThumbprint &&
                    value.string("expected_server_instance_id") == ticket.owner.server && value.number("expected_identity_epoch") == ticket.owner.identityEpoch &&
                    value.string("expected_identity_thumbprint_sha256") == ticket.owner.identityThumbprint && value.string("expected_api_origin") == ticket.owner.apiOrigin &&
                    value.string("expected_stream_origin") == ticket.owner.streamOrigin)
                DesktopAccountTrust.decode(value.string("challenge"), true, 16).fill(0)
            } else {
                controlRequire(value.string("request_id") == ticket.owner.ceremony)
                if (created) { DesktopAccountTrust.decode(value.string("poll_bearer"), true, 16).fill(0)
                    DesktopAccountTrust.decode(value.string("review_locator"), true, 16).fill(0) }
            }
            runtime.secure.read(result.ticket.slot, result.ticket.control).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                val pending = stored.payload as PendingPayload
                val auth = pending.ceremonyAuthorizationCopy(); val fields = try { AdmissionSecretEnvelope.read(auth) } finally { auth.fill(0) }
                val original = fields.second.copy()
                val header = if (created) value.string("poll_bearer").toByteArray(Charsets.US_ASCII) else fields.first.copy()
                val replacementAuth = try { AdmissionSecretEnvelope.encode(header, original) } finally { header.fill(0); original.fill(0); fields.first.close(); fields.second.close() }
                val wire = pending.requestCopy(); val successor = pending.successorCopy()
                // Exact signed current request stays unchanged; only the freshly received secret result is added.
                val digest = pending.requestDigestCopy()
                val proof = pending.signatureCopy()
                val authority = AccountCommandContextCodec.replacingAuthorization(pending, replacementAuth)
                val nextPayload = PendingPayload(SecureRecordKind.PROFILE_COMMAND, pending.operation, pending.wireKind, wire, digest, proof, authority, successor, raw)
                wire.fill(0); digest.fill(0); proof.fill(0); successor.fill(0); replacementAuth.fill(0); authority.fill(0)
                val nextOwner = stored.owner.copy(stage = SecureStage.RECORDED)
                runtime.secure.publish(result.ticket.control, requireNotNull(nextOwner.operation)) {
                    listOf(SecureRecord(stored.slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), nextOwner, nextPayload.duplicate()))
                }.thenApply {
                    owner = nextOwner; slot = stored.slot; response = value; earliestPollNanos = System.nanoTime() + 2_000_000_000
                    mutable.value = DesktopAdmissionViewState(if (endpoint == AccountEndpoint.TRUSTED_CHALLENGE) "TRUSTED_CHALLENGE" else if (created) "PENDING" else value.string("state"),
                        (value["approved_account_label"] as? JsonPrimitive)?.content)
                    Unit
                }.whenComplete { _, _ -> stored.close(); nextPayload.close() }
            }.whenComplete { _, _ -> raw.fill(0); result.close() }
        }

    private fun handoff(snapshot: SecureControl, stored: SecureRecord, next: SecureRecordOwner,
        command: ProfileV2.SignedCommand, authorization: ByteArray, successor: ByteArray): CompletableFuture<SecurePendingTicket> {
        val wire = command.transientWireCopy(); val digest = command.requestDigestCopy(); val proof = command.signatureCopy()
        val receipt = (stored.payload as PendingPayload).receiptCopy()
        val authority = AccountCommandContextCodec.replacingAuthorization(stored.payload as PendingPayload, authorization)
        val pending = PendingPayload(SecureRecordKind.PROFILE_COMMAND,
            if (command.operation == ProfileV2.Operation.TRUSTED_EXCHANGE) PendingOperation.TRUSTED_EXCHANGE else PendingOperation.ADMISSION,
            PendingWireKind.valueOf(command.operation.name), wire, digest, proof, authority, successor, receipt)
        wire.fill(0); digest.fill(0); proof.fill(0); authority.fill(0)
        SecureJournalRestoration.requireScope(command, next)
        val grant = command.operation in setOf(ProfileV2.Operation.ADMISSION_EXCHANGE, ProfileV2.Operation.TRUSTED_EXCHANGE)
        return runtime.custody.advancePrepared(SecurePendingTicket(snapshot, stored.slot, stored.owner), next, pending, receipt, grant) { original, target, exact ->
            val response = SecureJson.parse(exact, 65_536, canonical = false)
            original.owner == stored.owner && original.owner.stage == SecureStage.RECORDED && target == next &&
                response.string("contract_version") == "v2" && response.number("schema_version") == 2L && response.string("platform") == "MACOS" &&
                (if (command.operation == ProfileV2.Operation.TRUSTED_EXCHANGE) response.string("challenge_id") == next.ceremony && response.string("device_key_thumbprint_sha256") == next.keyThumbprint
                else response.string("request_id") == next.ceremony && (!grant || response.string("state") == "APPROVED" && response.string("approved_account_id") == next.account))
        }.whenComplete { _, _ -> receipt.fill(0) }
    }
    private fun JsonObjectBuilder.keyFields(spki: ByteArray, thumbprint: String) { put("device_key_thumbprint_sha256", thumbprint); put("device_public_key_jwk", jwk(spki)) }
    private fun jwk(spki: ByteArray): JsonObject {
        controlRequire(spki.size == 91)
        return buildJsonObject { put("kty", "EC"); put("crv", "P-256")
            put("x", Base64.getUrlEncoder().withoutPadding().encodeToString(spki.copyOfRange(27, 59)))
            put("y", Base64.getUrlEncoder().withoutPadding().encodeToString(spki.copyOfRange(59, 91))) }
    }
    override fun close() { parent?.close(); parent = null; owner = null; response = null }
}

internal object AdmissionSecretEnvelope {
    private val magic = "APADMIT1".toByteArray(Charsets.US_ASCII)
    fun encode(header: ByteArray, original: ByteArray): ByteArray {
        controlRequire(header.size + original.size + 16 <= 8192)
        return ByteBuffer.allocate(header.size + original.size + 16).put(magic).putInt(header.size).put(header).putInt(original.size).put(original).array()
    }
    fun read(bytes: ByteArray): Pair<SecretBytes, SecretBytes> {
        val buffer = ByteBuffer.wrap(bytes); controlRequire(bytes.size in 16..8192)
        controlRequire(ByteArray(8).also(buffer::get).contentEquals(magic))
        val count = buffer.int; controlRequire(count in 0..buffer.remaining() - 4)
        val first = ByteArray(count).also(buffer::get); val remaining = buffer.int
        try { controlRequire(remaining == buffer.remaining()); val second = ByteArray(remaining).also(buffer::get)
            try { return SecretBytes(first) to SecretBytes(second) } finally { second.fill(0) }
        } finally { first.fill(0) }
    }
    fun isEnvelope(bytes: ByteArray): Boolean = bytes.size >= 8 && bytes.copyOfRange(0, 8).contentEquals(magic)
}
