package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2 as RecoveryV2
import app.autplay.application.securestate.*
import app.autplay.desktop.security.SecurePendingTicket
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*

public data class DesktopServerRecoveryViewState(public val accountLabel: String? = null,
    public val role: String? = null, public val confirmationRequired: Boolean = false,
    public val deletionPending: Boolean = false, public val codeExportRequired: Boolean = false)
public class DesktopDeletionReview internal constructor(internal val owner: SecureRecordOwner,
    internal val codeGeneration: Long, internal val authorityGeneration: Long, internal val observedAt: Instant,
    public val allowed: Boolean, public val reason: String?) {
    override fun toString(): String = "DesktopDeletionReview(<redacted>)"
}

/** Server recovery codes are distinct from portable ROOT recovery codes and formats. */
public object DesktopServerRecoveryCode {
    private const val alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    public fun normalize(text: String): ByteArray {
        controlRequire(text.length <= 96 && text.all { it.code < 128 })
        val normalized = text.filterNot { it in " \t\r\n-" }.uppercase(java.util.Locale.ROOT)
        controlRequire(normalized.length == 32 && normalized.all { it in alphabet })
        return normalized.toByteArray(Charsets.US_ASCII)
    }
    internal fun create(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }.also { bytes ->
        bytes.indices.forEach { bytes[it] = alphabet[bytes[it].toInt() and 31].code.toByte() }
    }
    internal fun verifier(server: String, account: String, code: ByteArray): String {
        canonicalUuid(server); canonicalUuid(account)
        val s = UUID.fromString(server); val a = UUID.fromString(account)
        val identifiers = ByteBuffer.allocate(32).putLong(s.mostSignificantBits).putLong(s.leastSignificantBits)
            .putLong(a.mostSignificantBits).putLong(a.leastSignificantBits).array()
        val digest = MessageDigest.getInstance("SHA-256")
        try { digest.update("autplay:account-recovery-code:v1\u0000".toByteArray(Charsets.US_ASCII)); digest.update(identifiers)
            return digest.digest(code).joinToString("") { "%02x".format(it.toInt() and 255) }
        } finally { identifiers.fill(0) }
    }
}

/** Only the explicit recovery surface receives this closeable document, never ordinary view state. */
public class DesktopServerRecoveryInput private constructor(
    internal val account: String, internal val label: String, internal val trust: JsonObject,
    private val code: SecretBytes,
) : AutoCloseable {
    internal fun codeCopy(): ByteArray = code.copy()
    override fun close(): Unit = code.close()
    override fun toString(): String = "DesktopServerRecoveryInput(<redacted>)"
    public companion object {
        public fun parse(bytes: ByteArray): DesktopServerRecoveryInput {
            val doc = SecureJson.parse(bytes, 4096, canonical = false)
            doc.exact("format", "version", "server_instance_id", "identity_epoch", "identity_thumbprint_sha256", "api_origin", "stream_origin", "account_id", "account_label", "code")
            controlRequire(doc.string("format") == "autplay-account-recovery" && doc.number("version") == 1L)
            canonicalUuid(doc.string("account_id")); canonicalUuid(doc.string("server_instance_id")); sha256Text(doc.string("identity_thumbprint_sha256"))
            DesktopAccountTrust.display(doc.string("account_label"), 200)
            val code = DesktopServerRecoveryCode.normalize(doc.string("code"))
            try { return DesktopServerRecoveryInput(doc.string("account_id"), doc.string("account_label"), JsonObject(doc.filterKeys {
                it in setOf("server_instance_id", "identity_epoch", "identity_thumbprint_sha256", "api_origin", "stream_origin") }), SecretBytes(code)) }
            finally { code.fill(0) }
        }
    }
}

/** Explicit recipient preview -> confirmation -> exact K commit/outcome. No code enters J or settings. */
public class DesktopServerRecoveryController(private val runtime: DesktopAccountRuntime,
    private val profiles: DesktopProfileController) : AutoCloseable {
    private val mutable = MutableStateFlow(DesktopServerRecoveryViewState())
    public val state = mutable.asStateFlow()
    private var preview: RecoveryPreview? = null

    public fun configureSourceCode(confirmed: Boolean): CompletionStage<Unit> = runtime.command {
        controlRequire(confirmed)
        profiles.authorized("account_recovery") { snapshot, binding, access, _ ->
            val server = requireNotNull(runtime.selected)
            runtime.read(server.transport, AccountEndpoint.RECOVERY_STATUS, access = access).thenCompose { response -> response.use {
                val status = it.document()
                controlRequire(status.string("contract_version") == "v1" && status.number("schema_version") == 1L && status.string("account_id") == binding.account)
                val expected = status.number("code_generation"); val operation = UUID.randomUUID().toString()
                val code = DesktopServerRecoveryCode.create()
                val document = serverRecoveryDocument(server, requireNotNull(binding.account), status.string("account_label"), code)
                val unsigned = SecureJson.encode(buildJsonObject {
                    expectedTrustFields(server); put("contract_version", "v1"); put("schema_version", 1)
                    put("account_id", binding.account); put("operation_id", operation); put("requested_at", Instant.now().toString())
                    put("expected_code_generation", expected); put("next_code_verifier_sha256", DesktopServerRecoveryCode.verifier(server.identity.serverInstanceId, requireNotNull(binding.account), code))
                })
                val request = try { SecureJson.encode(JsonObject(SecureJson.parse(unsigned, 8192) + ("request_sha256" to JsonPrimitive(secureSha256(unsigned))))) }
                    finally { unsigned.fill(0) }
                val authorization = RecoverySecretEnvelope.encode(access, document)
                val secrets = PendingSecretContext(nextRecoveryCode = code, recoveryDocument = document)
                val owner = binding.copy(operation = operation, ceremony = operation, role = SecureRole.SOURCE, stage = SecureStage.PREPARED, protocolVersion = 1)
                try { runtime.custody.prepareRecoverySource(snapshot, owner, request, authorization, secrets) { frozen, wire, retained ->
                    val body = SecureJson.parse(wire, 8192)
                    body.string("account_id") == frozen.account && body.string("operation_id") == frozen.operation &&
                        DesktopFrozenPendingVerifier.verifyRecoverySecrets(frozen, body, retained, true)
                }.thenCompose { finishSourceConfiguration(it, expected) } }
                finally { request.fill(0); authorization.fill(0); document.fill(0); code.fill(0); secrets.close() }
            } }
        }
    }

    public fun resumeSourceConfiguration(): CompletionStage<Unit> = runtime.command {
        runtime.secure.snapshot().thenCompose { snapshot ->
            val slot = requireNotNull(SecureRecordKind.RECOVERY_SOURCE.reserved)
            runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val record = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                try {
                    runtime.requireTarget(record.owner, requireNotNull(runtime.selected))
                    val payload = record.payload as PendingPayload; val request = payload.requestCopy()
                    val expected = try { SecureJson.parse(request, 8192).number("expected_code_generation") } finally { request.fill(0) }
                    if (record.owner.stage == SecureStage.TERMINAL) {
                        val receipt = payload.receiptCopy()
                        try {
                            val response = SecureJson.parse(receipt, 4096, canonical = false)
                            controlRequire(response.string("operation_id") == record.owner.operation && response.string("account_id") == record.owner.account &&
                                response.boolean("configured") && response.number("code_generation") == SecureStateLimits.next(expected))
                            mutable.value = mutable.value.copy(codeExportRequired = true)
                            CompletableFuture.completedFuture(Unit)
                        } finally { receipt.fill(0) }
                    } else runtime.resumeProtected(snapshot, slot, record.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision))
                        .thenCompose { finishSourceConfiguration(it, expected) }
                } finally { record.close() }
            }
        }
    }

    private fun finishSourceConfiguration(ticket: app.autplay.desktop.security.SecurePendingTicket, expected: Long): CompletableFuture<Unit> =
        runtime.executePending(ticket, AccountEndpoint.RECOVERY_CONFIGURE).thenCompose { result ->
            val raw = result.response.copy()
            try {
                val response = SecureJson.parse(raw, 4096, canonical = false)
                response.exact("contract_version", "schema_version", "operation_id", "account_id", "code_generation", "configured", "replayed")
                controlRequire(response.string("contract_version") == "v1" && response.number("schema_version") == 1L &&
                    response.string("operation_id") == ticket.owner.operation && response.string("account_id") == ticket.owner.account &&
                    response.number("code_generation") == SecureStateLimits.next(expected) && response.boolean("configured"))
                response.boolean("replayed")
                runtime.recordTerminal(result.ticket, raw, { owner, _ -> owner.operation == response.string("operation_id") }, false).thenApply {
                    mutable.value = mutable.value.copy(codeExportRequired = true); Unit
                }
            } finally { raw.fill(0); result.close() }
        }

    public fun documentForExplicitExport(source: Boolean, deletionCancellation: Boolean = false): CompletionStage<SecretBytes> = runtime.secure.snapshot().thenCompose { snapshot ->
        controlRequire(!source || !deletionCancellation)
        val kind = if (source) SecureRecordKind.RECOVERY_SOURCE else if (deletionCancellation) SecureRecordKind.DELETION_RECIPIENT else SecureRecordKind.RECOVERY_RECIPIENT
        val slot = requireNotNull(kind.reserved)
        runtime.secure.read(slot, snapshot).thenApply { nullable ->
            val record = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                controlRequire(record.owner.stage == SecureStage.TERMINAL)
                val authorization = (record.payload as PendingPayload).ceremonyAuthorizationCopy()
                val document = try { RecoverySecretEnvelope.document(authorization) } finally { authorization.fill(0) }
                try { runtime.secure.transientSecret(document) } finally { document.fill(0) }
            } finally { record.close() }
        }
    }
    public fun acknowledgeExport(source: Boolean, exactReadback: ByteArray, deletionCancellation: Boolean = false): CompletionStage<Unit> {
        val owned = exactReadback.copyOf()
        return documentForExplicitExport(source, deletionCancellation).thenApply { secret -> secret.use {
            val current = it.copy()
            try { controlRequire(current.contentEquals(owned), AccountControlFailure.CONFLICT); mutable.value = mutable.value.copy(codeExportRequired = false); Unit }
            finally { current.fill(0) }
        } }.whenComplete { _, _ -> owned.fill(0) }
    }

    public fun previewFile(input: DesktopServerRecoveryInput, cancelDeletion: Boolean = false): CompletionStage<Unit> {
        val code = input.codeCopy()
        return previewCode(input.account, code, cancelDeletion, input.trust).whenComplete { _, _ -> code.fill(0) }
    }
    /** The account and server come from explicit selections; a pasted code never chooses an origin. */
    public fun previewCode(account: String, code: ByteArray, cancelDeletion: Boolean = false): CompletionStage<Unit> =
        previewCode(account, code, cancelDeletion, null)

    private fun previewCode(account: String, sourceCode: ByteArray, deletion: Boolean, fileTrust: JsonObject?): CompletableFuture<Unit> {
        val code = sourceCode.copyOf()
        return runtime.command {
            canonicalUuid(account)
            val normalized = DesktopServerRecoveryCode.normalize(code.toString(Charsets.US_ASCII))
            try { controlRequire(normalized.contentEquals(code)) } finally { normalized.fill(0) }
            val server = runtime.requireServer(if (deletion) D1.Family.DELETION else D1.Family.RECOVERY)
            fileTrust?.let {
                controlRequire(it.string("server_instance_id") == server.identity.serverInstanceId && it.number("identity_epoch") == server.identity.identityEpoch &&
                    it.string("identity_thumbprint_sha256") == server.identity.thumbprintSha256 && it.string("api_origin") == server.identity.apiOrigin &&
                    it.string("stream_origin") == server.identity.streamOrigin, AccountControlFailure.TRUST_REQUIRED)
            }
            runtime.secure.snapshot().thenCompose { snapshot ->
                controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
                val operation = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString()
                val slot = requireNotNull((if (deletion) SecureRecordKind.DELETION_RECIPIENT else SecureRecordKind.RECOVERY_RECIPIENT).reserved)
                runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot, profile), 4, 4L * SecureStateLimits.VALUE),
                    FirstBindOwner(operation, operation, SecureRole.RECIPIENT)).thenCompose { reserved ->
                    runtime.secure.createNewKey(reserved, operation).thenCompose { key ->
                        runtime.secure.snapshot().thenCompose { current -> runtime.secure.publicKey(current, key.tag).thenCompose { spki ->
                            try {
                                val owner = runtime.pendingOwner(current, server, account, profile, operation, key, null).copy(bindingCommit = null)
                                val context = if (deletion) RecoveryV2.deletionPreviewContext(server.identity, D1.Platform.MACOS, spki, account, operation)
                                    else RecoveryV2.recoveryPreviewContext(server.identity, D1.Platform.MACOS, spki, account, operation)
                                val body = recoveryBody(server, owner, spki, emptyMap())
                                val prepared = try { RecoveryV2.prepare(context, body) } finally { body.fill(0) }
                                runtime.signer.sign(current, owner, prepared).thenCompose { signed ->
                                    val secrets = if (deletion) PendingSecretContext(deletionCode = code) else PendingSecretContext(recoveryCode = code)
                                    try { runtime.custody.prepareRecoveryOrDeletion(current, owner, signed, code, byteArrayOf(), secrets) { frozen, wire, retained ->
                                        DesktopFrozenPendingVerifier.verifyRecoverySecrets(frozen, SecureJson.parse(wire, 8192), retained, false)
                                    } }
                                    finally { signed.close(); secrets.close() }
                                }.whenComplete { _, _ -> prepared.close() }
                            } finally { spki.fill(0) }
                        } }
                    }
                }
            }.thenCompose { ticket -> runtime.executePending(ticket, if (deletion) AccountEndpoint.DELETION_PREVIEW else AccountEndpoint.RECOVERY_PREVIEW).thenCompose { result ->
                val raw = result.response.copy()
                try {
                    val response = SecureJson.parse(raw, 65_536, canonical = false)
                    controlRequire(response.string("contract_version") == "v2" && response.number("schema_version") == 2L && response.string("platform") == "MACOS" &&
                        response.string("account_id") == account && response.boolean("confirmation_required"))
                    if (!deletion) controlRequire(response.string("operation_id") == ticket.owner.operation && response.string("server_instance_id") == ticket.owner.server && response.number("identity_epoch") == ticket.owner.identityEpoch)
                    val label = response.string("account_label"); DesktopAccountTrust.display(label, 200)
                    val generation = response.number("code_generation"); controlRequire(generation > 0)
                    val retained = runtime.secure.transientSecret(code)
                    recordPreview(result.ticket, raw).thenApply {
                        preview?.close(); preview = RecoveryPreview(result.ticket.owner, retained, generation, label, response, deletion)
                        mutable.value = DesktopServerRecoveryViewState(label, if (deletion) null else response.string("role"), true, deletion)
                        Unit
                    }.whenComplete { _, error -> if (error != null) retained.close() }
                } finally { raw.fill(0); result.close() }
            } }
        }.whenComplete { _, _ -> code.fill(0) }
    }

    public fun confirmAccount(confirmed: Boolean): CompletionStage<Unit> = runtime.command {
        controlRequire(confirmed)
        val preview = this.preview ?: throw AccountControlException(AccountControlFailure.CONFLICT)
        val server = runtime.requireServer(if (preview.deletion) D1.Family.DELETION else D1.Family.RECOVERY)
        runtime.requireTarget(preview.owner, server)
        runtime.secure.snapshot().thenCompose { snapshot ->
            val operation = UUID.randomUUID().toString(); val commit = UUID.randomUUID().toString()
            val owner = preview.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision,
                operation = operation, bindingCommit = commit, stage = SecureStage.PREPARED, deletionVeto = false)
            runtime.secure.publicKey(snapshot, requireNotNull(owner.keyTag)).thenCompose { spki ->
                val nextCode = DesktopServerRecoveryCode.create(); val refresh = freshSecret(32); val oldCode = preview.code.copy()
                try {
                    val verifier = DesktopServerRecoveryCode.verifier(requireNotNull(owner.server), requireNotNull(owner.account), nextCode)
                    val context = if (preview.deletion) RecoveryV2.deletionCancelContext(server.identity, D1.Platform.MACOS, spki,
                        requireNotNull(owner.account), operation, requireNotNull(owner.account), preview.codeGeneration, verifier, secureSha256(refresh), commit,
                        preview.response.string("deletion_request_id"), preview.response.number("revision"))
                    else RecoveryV2.recoveryCommitContext(server.identity, D1.Platform.MACOS, spki, requireNotNull(owner.account), operation,
                        requireNotNull(owner.account), preview.codeGeneration, verifier, secureSha256(refresh), commit)
                    val fields = buildMap<String, JsonElement> {
                        put("confirmed_account_id", JsonPrimitive(requireNotNull(owner.account))); put("expected_code_generation", JsonPrimitive(preview.codeGeneration))
                        put("next_code_verifier_sha256", JsonPrimitive(verifier)); put("next_refresh_token_sha256", JsonPrimitive(secureSha256(refresh)))
                        put("binding_commit_id", JsonPrimitive(commit))
                        if (preview.deletion) { put("deletion_request_id", preview.response.getValue("deletion_request_id")); put("expected_revision", preview.response.getValue("revision")) }
                    }
                    val body = recoveryBody(server, owner, spki, fields)
                    val prepared = try { RecoveryV2.prepare(context, body) } finally { body.fill(0) }
                    val document = serverRecoveryDocument(server, requireNotNull(owner.account), preview.label, nextCode)
                    val authorization = RecoverySecretEnvelope.encode(oldCode, document)
                    val retained = runtime.secure.transientSecret(authorization); val successor = runtime.secure.transientSecret(refresh)
                    try {
                        runtime.signer.sign(snapshot, preview.owner.copy(boot = runtime.secure.boot,
                            authorityRevision = snapshot.authorityRevision, deletionVeto = false), prepared).thenCompose { signed ->
                            val headers = retained.copy(); val next = successor.copy()
                            try { handoffPreview(snapshot, preview.owner, owner, signed, headers, next) }
                            finally { signed.close(); headers.fill(0); next.fill(0) }
                        }.whenComplete { _, _ -> prepared.close(); retained.close(); successor.close() }
                    } finally { document.fill(0); authorization.fill(0) }
                } finally { spki.fill(0); nextCode.fill(0); refresh.fill(0); oldCode.fill(0) }
            }
        }.thenCompose { ticket -> runtime.executeSession(ticket, if (preview.deletion) AccountEndpoint.DELETION_CANCEL else AccountEndpoint.RECOVERY_COMMIT) }
            .thenApply { mutable.value = mutable.value.copy(confirmationRequired = false, codeExportRequired = true); preview.close(); this.preview = null; Unit }
    }

    public fun reviewDeletion(): CompletionStage<DesktopDeletionReview> = runtime.command {
        profiles.authorized("account_deletion") { _, owner, access, _ ->
            runtime.read(requireNotNull(runtime.selected).transport, AccountEndpoint.DELETION_STATUS, access = access).thenApply { response -> response.use {
                val value = it.document()
                value.exact("contract_version", "schema_version", "account_id", "authority_generation", "code_generation", "can_request", "reason")
                controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L && value.string("account_id") == owner.account)
                val reason = value.nullableString("reason")
                controlRequire(reason == null || reason in setOf("last_owner_required", "recovery_setup_required", "deletion_initializing"))
                DesktopDeletionReview(owner, value.number("code_generation"), value.number("authority_generation"), Instant.now(), value.boolean("can_request"), reason)
            } }
        }
    }

    public fun requestDeletion(review: DesktopDeletionReview, confirmed: Boolean, recoveryCode: ByteArray): CompletionStage<Unit> {
        val code = recoveryCode.copyOf()
        return runtime.command {
        val codeGeneration = review.codeGeneration; val authorityGeneration = review.authorityGeneration
        controlRequire(confirmed && review.allowed && review.observedAt.plusSeconds(120) > Instant.now() && codeGeneration > 0 && authorityGeneration > 0)
        val normalized = DesktopServerRecoveryCode.normalize(code.toString(Charsets.US_ASCII))
        try { controlRequire(normalized.contentEquals(code)) } finally { normalized.fill(0) }
        runtime.requireServer(D1.Family.DELETION)
        profiles.authorized("account_deletion") { snapshot, binding, access, _ ->
            controlRequire(binding == review.owner, AccountControlFailure.CONTEXT_CHANGED)
            val server = requireNotNull(runtime.selected); val operation = UUID.randomUUID().toString()
            val owner = binding.copy(role = SecureRole.SOURCE, operation = operation, ceremony = operation, stage = SecureStage.PREPARED)
            runtime.secure.publicKey(snapshot, requireNotNull(binding.keyTag)).thenCompose { spki ->
                try {
                    val context = RecoveryV2.deletionRequestContext(server.identity, D1.Platform.MACOS, spki, requireNotNull(binding.account), operation,
                        requireNotNull(binding.account), codeGeneration, authorityGeneration)
                    val fields = mapOf("confirmed_account_id" to JsonPrimitive(requireNotNull(binding.account)),
                        "expected_code_generation" to JsonPrimitive(codeGeneration), "expected_authority_generation" to JsonPrimitive(authorityGeneration))
                    val body = recoveryBody(server, owner, spki, fields)
                    val prepared = try { RecoveryV2.prepare(context, body) } finally { body.fill(0) }
                    val headers = DeletionSecretEnvelope.encode(access, code)
                    val token = try { runtime.secure.transientSecret(headers) } finally { headers.fill(0) }
                    runtime.signer.sign(snapshot, owner, prepared).thenCompose { signed ->
                        val bytes = token.copy()
                        val secrets = PendingSecretContext(deletionCode = code)
                        try { runtime.custody.prepareRecoveryOrDeletion(snapshot, owner, signed, bytes, byteArrayOf(), secrets) { frozen, wire, retained ->
                            DesktopFrozenPendingVerifier.verifyRecoverySecrets(frozen, SecureJson.parse(wire, 8192), retained, false)
                        } }
                        finally { signed.close(); bytes.fill(0); secrets.close() }
                    }.whenComplete { _, _ -> prepared.close(); token.close() }
                } finally { spki.fill(0) }
            }
        }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.DELETION_REQUEST).thenCompose { result ->
            val receipt = result.response.copy()
            try { runtime.recordDeletionOutcome(result.ticket, receipt).thenApply {
                mutable.value = mutable.value.copy(deletionPending = it.owner.deletionVeto); Unit
            } } finally { receipt.fill(0); result.close() }
        } }
        }.whenComplete { _, _ -> code.fill(0) }
    }

    public fun resolveDeletionOutcome(proveNonAcceptance: Boolean = false): CompletionStage<Unit> = runtime.command {
        runtime.secure.snapshot().thenCompose { snapshot ->
            val slot = requireNotNull(SecureRecordKind.DELETION_SOURCE.reserved)
            runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                try {
                    runtime.requireTarget(stored.owner, requireNotNull(runtime.selected))
                    controlRequire(stored.payload is PendingPayload && (stored.payload as PendingPayload).operation == PendingOperation.DELETION &&
                        stored.owner.stage in setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.RECORDED))
                    runtime.resumeProtected(snapshot, slot, stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision))
                } finally { stored.close() }
            }
        }.thenCompose { ticket -> runtime.executePending(ticket, if (proveNonAcceptance) AccountEndpoint.DELETION_RESOLVE else AccountEndpoint.DELETION_RECEIPT).thenCompose { result ->
            val raw = result.response.copy()
            try { runtime.recordDeletionOutcome(result.ticket, raw).thenApply {
                mutable.value = mutable.value.copy(deletionPending = it.owner.deletionVeto); Unit
            } } finally { raw.fill(0); result.close() }
        } }
    }

    public fun restoreRecipientPreview(deletionCancellation: Boolean = false): CompletionStage<Unit> = runtime.command {
        val server = requireNotNull(runtime.selected)
        runtime.secure.snapshot().thenCompose { snapshot ->
            val slot = requireNotNull((if (deletionCancellation) SecureRecordKind.DELETION_RECIPIENT else SecureRecordKind.RECOVERY_RECIPIENT).reserved)
            runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                runtime.requireTarget(stored.owner, server)
                controlRequire(stored.owner.stage == SecureStage.RECORDED)
                val payload = stored.payload as PendingPayload
                val original = payload.requestCopy()
                try { controlRequire("next_refresh_token_sha256" !in SecureJson.parse(original, 8192)) } finally { original.fill(0) }
                val auth = payload.ceremonyAuthorizationCopy(); val code = try { RecoverySecretEnvelope.code(auth) } finally { auth.fill(0) }
                val receipt = payload.receiptCopy()
                val value = try { SecureJson.parse(receipt, 65_536, canonical = false) } finally { receipt.fill(0) }
                controlRequire(value.string("account_id") == stored.owner.account && value.boolean("confirmation_required") && value.number("code_generation") > 0)
                val retained = runtime.secure.transientSecret(code); code.fill(0)
                val owner = stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision)
                runtime.resumeProtected(snapshot, slot, owner).thenApply { ticket ->
                    preview?.close(); preview = RecoveryPreview(ticket.owner, retained, value.number("code_generation"), value.string("account_label"), value, deletionCancellation)
                    mutable.value = DesktopServerRecoveryViewState(value.string("account_label"), if (deletionCancellation) null else value.string("role"), true, deletionCancellation)
                    Unit
                }.whenComplete { _, failure -> stored.close(); if (failure != null) retained.close() }
            }
        }
    }

    private fun recordPreview(ticket: app.autplay.desktop.security.SecurePendingTicket, receipt: ByteArray): CompletableFuture<SecureControl> {
        val owned = receipt.copyOf()
        return runtime.secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                val payload = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                runtime.secure.publish(ticket.control, requireNotNull(stored.owner.operation)) {
                    listOf(SecureRecord(stored.slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(),
                        stored.owner.copy(stage = SecureStage.RECORDED), payload.duplicate(owned)))
                }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> owned.fill(0) }
    }

    private fun handoffPreview(snapshot: SecureControl, previous: SecureRecordOwner, next: SecureRecordOwner,
        command: RecoveryV2.SignedCommand, authorization: ByteArray, successor: ByteArray): CompletableFuture<app.autplay.desktop.security.SecurePendingTicket> {
        val slot = requireNotNull((if (command.bodyKind == RecoveryV2.BodyKind.DELETION_CANCEL) SecureRecordKind.DELETION_RECIPIENT else SecureRecordKind.RECOVERY_RECIPIENT).reserved)
        val wire = command.fullOriginalWireCopy(); val digest = command.requestDigestCopy(); val proof = command.signatureCopy()
        val owner = next.copy(deletionVeto = command.bodyKind == RecoveryV2.BodyKind.DELETION_CANCEL)
        val bodyKind = command.bodyKind
        SecureJournalRestoration.requireScope(command, owner)
        val headers = runtime.secure.transientSecret(authorization); val refresh = runtime.secure.transientSecret(successor)
        return runtime.secure.read(slot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                controlRequire(stored.owner.operation == previous.operation && stored.owner.ceremony == next.ceremony && stored.owner.stage == SecureStage.RECORDED)
                val prior = stored.payload as PendingPayload; val receipt = prior.receiptCopy()
                val header = headers.copy(); val nextRefresh = refresh.copy()
                val authority = AccountCommandContextCodec.replacingAuthorization(prior, header)
                val document = try { RecoverySecretEnvelope.document(header) } finally { header.fill(0) }
                val nextCode = DesktopServerRecoveryCode.normalize(SecureJson.parse(document, 4096).string("code"))
                val payload = try { prior.secretContextCopy().use { old ->
                    val recovery = old.recoveryCodeCopy(); val deletion = old.deletionCodeCopy()
                    try { PendingSecretContext(recoveryCode = recovery, deletionCode = deletion, nextRecoveryCode = nextCode, recoveryDocument = document).use { secrets ->
                        controlRequire(DesktopFrozenPendingVerifier.verifyRecoverySecrets(owner, SecureJson.parse(wire, 8192), secrets, false))
                        PendingPayload(prior.kind, prior.operation, PendingWireKind.valueOf(bodyKind.name), wire, digest, proof, authority, nextRefresh, secrets = secrets)
                    } } finally { recovery.fill(0); deletion.fill(0) }
                } } finally { authority.fill(0); document.fill(0); nextCode.fill(0); nextRefresh.fill(0) }
                runtime.custody.advancePrepared(SecurePendingTicket(snapshot, slot, stored.owner), owner, payload, receipt, true) { original, target, exact ->
                    val value = SecureJson.parse(exact, 65_536, canonical = false)
                    original.owner == stored.owner && target == owner && value.string("account_id") == owner.account && value.boolean("confirmation_required") &&
                        value.number("code_generation") == SecureJson.parse(wire, 8192).number("expected_code_generation") &&
                        (!owner.deletionVeto || value.string("deletion_request_id") == SecureJson.parse(wire, 8192).string("deletion_request_id"))
                }.whenComplete { _, _ -> receipt.fill(0); stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> wire.fill(0); digest.fill(0); proof.fill(0); headers.close(); refresh.close() }
    }

    private fun recoveryBody(server: AccountSelectedServer, owner: SecureRecordOwner, spki: ByteArray, fields: Map<String, JsonElement>): ByteArray =
        SecureJson.encode(buildJsonObject {
            trustFields(server); put("operation_id", owner.operation); put("account_id", owner.account); put("requested_at", Instant.now().toString())
            put("device_public_key_spki_b64", Base64.getEncoder().encodeToString(spki)); put("device_key_thumbprint_sha256", owner.keyThumbprint)
            put("device_name", runtime.displayDeviceName); put("app_version", runtime.applicationVersion)
            fields.forEach { (name, value) -> put(name, value) }
        })
    override fun close() { preview?.close(); preview = null }
}

private class RecoveryPreview(val owner: SecureRecordOwner, val code: SecretBytes, val codeGeneration: Long,
    val label: String, val response: JsonObject, val deletion: Boolean) : AutoCloseable {
    override fun close(): Unit = code.close()
}
/** Closed named recovery header/archive, bounded inside K's existing authorization field. */
internal object RecoverySecretEnvelope {
    fun encode(header: ByteArray, replacementDocument: ByteArray): ByteArray = SecureJson.encode(buildJsonObject {
        put("format", "autplay-desktop-recovery-headers-1"); put("code_header", header.toString(Charsets.US_ASCII))
        put("replacement_document", SecureJson.parse(replacementDocument, 4096))
    }).also { controlRequire(it.size <= 8192) }
    fun header(bytes: ByteArray): ByteArray {
        val value = SecureJson.parse(bytes, 8192); value.exact("format", "code_header", "replacement_document")
        controlRequire(value.string("format") == "autplay-desktop-recovery-headers-1")
        return value.string("code_header").toByteArray(Charsets.US_ASCII)
    }
    fun document(bytes: ByteArray): ByteArray {
        val value = SecureJson.parse(bytes, 8192); value.exact("format", "code_header", "replacement_document")
        controlRequire(value.string("format") == "autplay-desktop-recovery-headers-1")
        return SecureJson.encode(value.getValue("replacement_document"))
    }
    fun code(bytes: ByteArray): ByteArray {
        if (bytes.firstOrNull() != '{'.code.toByte()) return bytes.copyOf()
        val value = SecureJson.parse(bytes, 8192); value.exact("format", "code_header", "replacement_document")
        controlRequire(value.string("format") == "autplay-desktop-recovery-headers-1")
        return DesktopServerRecoveryCode.normalize(value.string("code_header"))
    }
}
internal fun serverRecoveryDocument(server: AccountSelectedServer, account: String, label: String, code: ByteArray): ByteArray =
    SecureJson.encode(buildJsonObject {
        put("format", "autplay-account-recovery"); put("version", 1); put("server_instance_id", server.identity.serverInstanceId)
        put("identity_epoch", server.identity.identityEpoch); put("identity_thumbprint_sha256", server.identity.thumbprintSha256)
        put("api_origin", server.identity.apiOrigin); put("stream_origin", server.identity.streamOrigin)
        put("account_id", account); put("account_label", label); put("code", code.toString(Charsets.US_ASCII))
    })

internal object DeletionSecretEnvelope {
    private val magic = "APDELET1".toByteArray(Charsets.US_ASCII)
    fun encode(bearer: ByteArray, code: ByteArray): ByteArray {
        controlRequire(bearer.size + code.size + 16 <= 8192)
        return ByteBuffer.allocate(bearer.size + code.size + 16).put(magic).putInt(bearer.size).put(bearer).putInt(code.size).put(code).array()
    }
    fun isEnvelope(bytes: ByteArray): Boolean = bytes.size >= 8 && bytes.copyOfRange(0, 8).contentEquals(magic)
    fun read(bytes: ByteArray): Pair<SecretBytes, SecretBytes> {
        controlRequire(bytes.size in 16..8192 && isEnvelope(bytes))
        val buffer = ByteBuffer.wrap(bytes); buffer.position(8)
        val count = buffer.int; controlRequire(count in 0..buffer.remaining() - 4)
        val bearer = ByteArray(count).also(buffer::get); val remaining = buffer.int
        try {
            controlRequire(remaining == buffer.remaining()); val code = ByteArray(remaining).also(buffer::get)
            try { return SecretBytes(bearer) to SecretBytes(code) } finally { code.fill(0) }
        } finally { bearer.fill(0) }
    }
}
