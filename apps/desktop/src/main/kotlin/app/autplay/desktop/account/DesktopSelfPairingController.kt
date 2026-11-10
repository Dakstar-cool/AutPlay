package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.SelfDeviceContractV2 as SelfV2
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

public data class DesktopSelfPairingViewState(public val state: String = "IDLE", public val deviceName: String? = null,
    public val comparisonCode: String? = null, public val accountLabel: String? = null, public val recipientPlatform: String? = null)

/** TEXT/file QR2 input and SAS comparison. Camera capture remains outside this controller. */
public class DesktopSelfPairingController(private val runtime: DesktopAccountRuntime,
    private val profiles: DesktopProfileController) : AutoCloseable {
    private val mutable = MutableStateFlow(DesktopSelfPairingViewState())
    public val state = mutable.asStateFlow()
    private var sourceStart: SelfV2.WireRequest? = null
    private var recipientClaim: SelfV2.WireRequest? = null
    private var currentOwner: SecureRecordOwner? = null
    private var status: JsonObject? = null
    private var qr: SelfV2.Qr? = null
    private var secrets: SecretBytes? = null

    public fun startSource(): CompletionStage<Unit> = runtime.command {
        val server = runtime.requireServer(D1.Family.SELF)
        profiles.authorized("self_device_pairing") { snapshot, binding, access, _ ->
            val ceremony = UUID.randomUUID().toString(); val operation = UUID.randomUUID().toString()
            val rendezvous = freshSecret(32)
            try {
                val body = SecureJson.encode(buildJsonObject {
                    selfFields(server, ceremony); put("operation_id", operation); put("rendezvous_secret_sha256", secureSha256(rendezvous))
                })
                val prepared = try { SelfV2.prepare(SelfV2.Operation.START, body, SelfV2.sourceContext(server.identity, D1.Platform.MACOS, ceremony), Instant.now()) }
                    finally { body.fill(0) }
                val command = SelfV2.completeBearer(prepared)
                val wire = command.canonicalDocumentCopy()
                val envelope = try { SelfSecretEnvelope.encode(access, rendezvous, byteArrayOf(), wire) } finally { wire.fill(0) }
                val owner = binding.copy(role = SecureRole.SOURCE, ceremony = ceremony, operation = operation, stage = SecureStage.PREPARED, selfSourcePlatform = "MACOS")
                try { runtime.prepareSelf(snapshot, owner, command, envelope, byteArrayOf()).thenApply { sourceStart = command; it } }
                finally { envelope.fill(0) }
            } finally { rendezvous.fill(0) }
        }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.SELF_START).thenCompose { result ->
            val raw = result.response.copy()
            runtime.secure.read(result.ticket.slot, result.ticket.control).thenCompose { stored ->
                val record = stored ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                val authorization = (record.payload as PendingPayload).ceremonyAuthorizationCopy()
                try {
                    val envelope = SelfSecretEnvelope.read(authorization)
                    envelope.use {
                        val rendezvous = it.second.copy()
                        try {
                            val start = requireNotNull(sourceStart)
                            qr?.close(); qr = runtime.secure.retainTransient(SelfV2.qrFromStart(start, raw, rendezvous, Instant.now()))
                            val value = SecureJson.parse(raw, 65_536)
                            progress(result.ticket, raw).thenApply { owner -> currentOwner = owner; publishStatus(value); Unit }
                        } finally { rendezvous.fill(0) }
                    }
                } finally { authorization.fill(0); record.close() }
            }.whenComplete { _, _ -> raw.fill(0); result.close() }
        } }
    }

    public fun qrForExplicitDisplay(): SecretBytes {
        val bytes = requireNotNull(qr).encode()
        return try { runtime.secure.transientSecret(bytes) } finally { bytes.fill(0) }
    }

    public fun claimTextOrFile(qrUtf8: ByteArray): CompletionStage<Unit> {
        val rawQr = qrUtf8.copyOf()
        return runtime.command {
            val server = runtime.requireServer(D1.Family.SELF)
            val parsed = SelfV2.readQr(rawQr, server.identity, server.discovery, D1.Platform.MACOS, Instant.now())
            runtime.secure.retainTransient(parsed)
            qr?.close(); qr = parsed
            val operation = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString()
            val claim = UUID.randomUUID().toString(); val slot = requireNotNull(SecureRecordKind.SELF_RECIPIENT.reserved)
            runtime.secure.snapshot().thenCompose { snapshot ->
                controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
                runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot, profile), 4, 4L * SecureStateLimits.VALUE),
                    FirstBindOwner(parsed.ceremonyId, operation, SecureRole.RECIPIENT)).thenCompose { reserved ->
                    runtime.secure.createNewKey(reserved, operation).thenCompose { key ->
                        runtime.secure.snapshot().thenCompose { current -> runtime.secure.publicKey(current, key.tag).thenCompose { spki ->
                            val poll = freshSecret(32); val rendezvous = parsed.rendezvousSecretCopy()
                            try {
                                val context = SelfV2.recipientContext(parsed, D1.Platform.MACOS, spki, Instant.now())
                                val body = SecureJson.encode(buildJsonObject {
                                    selfFields(server, parsed.ceremonyId); put("claim_id", claim); put("poll_secret_sha256", secureSha256(poll))
                                    put("device_public_key_spki_b64", Base64.getEncoder().encodeToString(spki)); put("device_key_thumbprint_sha256", key.thumbprint)
                                    put("device_name", runtime.displayDeviceName); put("app_version", runtime.applicationVersion)
                                })
                                val prepared = try { SelfV2.prepare(SelfV2.Operation.CLAIM, body, context, Instant.now()) } finally { body.fill(0) }
                                val owner = runtime.pendingOwner(current, server, null, profile, operation, key, parsed.expiresAt.epochSecond,
                                    ceremony = parsed.ceremonyId, claim = claim).copy(bindingCommit = null, selfSourcePlatform = parsed.sourcePlatform.name)
                                val pollOwner = runtime.secure.transientSecret(poll); val rendezvousOwner = runtime.secure.transientSecret(rendezvous)
                                runtime.signer.sign(current, owner, prepared).thenCompose { command ->
                                    val wire = command.canonicalDocumentCopy(); val p = pollOwner.copy(); val r = rendezvousOwner.copy()
                                    val envelope = try { SelfSecretEnvelope.encode(r, p, rawQr, wire) } finally { wire.fill(0); r.fill(0) }
                                    try { runtime.prepareSelf(current, owner, command, envelope, byteArrayOf(), rawQr).thenApply { recipientClaim = command; it } }
                                    finally { p.fill(0); envelope.fill(0) }
                                }.whenComplete { _, _ -> pollOwner.close(); rendezvousOwner.close() }
                            } finally { spki.fill(0); poll.fill(0); rendezvous.fill(0) }
                        } }
                    }
                }
            }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.SELF_CLAIM).thenCompose { result ->
                val raw = result.response.copy()
                try {
                    SelfV2.continuationContext(requireNotNull(recipientClaim), raw)
                    val value = SecureJson.parse(raw, 65_536)
                    progress(result.ticket, raw).thenApply { owner -> currentOwner = owner; publishStatus(value); Unit }
                } finally { raw.fill(0); result.close() }
            } }
        }.whenComplete { _, _ -> rawQr.fill(0) }
    }

    public fun refreshSourceStatus(): CompletionStage<Unit> = runtime.command {
        val owner = requireNotNull(currentOwner); controlRequire(owner.role == SecureRole.SOURCE)
        profiles.authorized("self_device_pairing") { _, binding, access, _ ->
            controlRequire(binding.bindingCommit == owner.bindingCommit && binding.session == owner.session, AccountControlFailure.CONTEXT_CHANGED)
            runtime.read(requireNotNull(runtime.selected).transport, AccountEndpoint.SELF_STATUS, access = access, id = owner.ceremony).thenApply { response ->
                response.use {
                    val bytes = it.bodyCopy()
                    try {
                        val value = SecureJson.parse(bytes, 65_536, canonical = false)
                        controlRequire(value.string("ceremony_id") == owner.ceremony && value.string("source_platform") == "MACOS")
                        if (value.string("state") in setOf("OPEN", "CLAIMED")) SelfV2.decisionContext(requireNotNull(sourceStart), bytes)
                        publishStatus(value); Unit
                    } finally { bytes.fill(0) }
                }
            }
        }
    }

    /** User must compare the displayed SAS; an approve click without the matching code is refused. */
    public fun decideSource(action: String, comparedCode: String? = null): CompletionStage<Unit> = runtime.command {
        controlRequire(action in setOf("APPROVE", "REJECT", "CANCEL"))
        val owner = requireNotNull(currentOwner); controlRequire(owner.role == SecureRole.SOURCE)
        val value = requireNotNull(status); val server = requireNotNull(runtime.selected)
        if (action == "APPROVE") controlRequire(comparedCode != null && comparedCode == value.string("comparison_code"), AccountControlFailure.CONFLICT)
        profiles.authorized("self_device_pairing") { snapshot, _, access, _ ->
            val operation = UUID.randomUUID().toString()
            val rawStatus = SecureJson.encode(value)
            val context = try { SelfV2.decisionContext(requireNotNull(sourceStart), rawStatus) } finally { rawStatus.fill(0) }
            val body = SecureJson.encode(buildJsonObject {
                selfFields(server, requireNotNull(owner.ceremony)); put("operation_id", operation); put("action", action)
                put("expected_revision", value.number("revision")); put("claim_id", value["claim_id"] ?: JsonNull)
                put("claim_request_sha256", value["claim_request_sha256"] ?: JsonNull)
                put("comparison_code", if (action == "APPROVE") JsonPrimitive(requireNotNull(comparedCode)) else JsonNull)
            })
            val prepared = try { SelfV2.prepare(SelfV2.Operation.DECISION, body, context, Instant.now()) } finally { body.fill(0) }
            val command = SelfV2.completeBearer(prepared)
            val start = requireNotNull(sourceStart).canonicalDocumentCopy()
            val envelope = try { SelfSecretEnvelope.encode(access, byteArrayOf(), byteArrayOf(), start) } finally { start.fill(0) }
            try { handoff(snapshot, owner, owner.copy(operation = operation, stage = SecureStage.PREPARED), command, envelope, byteArrayOf()) }
            finally { envelope.fill(0) }
        }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.SELF_DECISION).thenCompose { result ->
            val raw = result.response.copy()
            try {
                val response = SecureJson.parse(raw, 65_536, canonical = false)
                controlRequire(response.string("ceremony_id") == owner.ceremony && response.number("revision") > value.number("revision"))
                val terminal = response.string("state") in setOf("REJECTED", "CANCELLED", "EXCHANGED", "EXPIRED")
                if (terminal) runtime.recordTerminal(result.ticket, raw, { retained, _ -> retained.ceremony == response.string("ceremony_id") }, false)
                    .thenApply { currentOwner = null; publishStatus(response); Unit }
                else progress(result.ticket, raw).thenApply { currentOwner = it; publishStatus(response); Unit }
            } finally { raw.fill(0); result.close() }
        } }
    }

    public fun restoreProtectedCeremony(source: Boolean): CompletionStage<Unit> = runtime.command {
        val server = requireNotNull(runtime.selected)
        val slot = requireNotNull((if (source) SecureRecordKind.SELF_SOURCE else SecureRecordKind.SELF_RECIPIENT).reserved)
        runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            runtime.requireTarget(stored.owner, server)
            val pending = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            controlRequire(pending.operation == PendingOperation.SELF && stored.owner.stage != SecureStage.TERMINAL)
            val bytes = pending.ceremonyAuthorizationCopy(); val material = try { SelfSecretEnvelope.read(bytes) } finally { bytes.fill(0) }
            val original = material.original.copy(); val receipt = pending.receiptCopy(); val current = pending.requestCopy()
            val restore = if (source) {
                sourceStart = SelfV2.readRequest(SelfV2.Operation.START, original,
                    SelfV2.sourceContext(server.identity, D1.Platform.MACOS, requireNotNull(stored.owner.ceremony)))
                CompletableFuture.completedFuture(Unit)
            } else runtime.secure.publicKey(snapshot, requireNotNull(stored.owner.keyTag)).thenApply { spki ->
                val rawQr = material.qr.copy()
                try {
                    val expiry = Instant.parse(SecureJson.parse(rawQr, 4096).string("expires_at"))
                    controlRequire(expiry.epochSecond == stored.owner.expiresAtEpochSeconds)
                    val context = app.autplay.application.desktop.accountflow.DesktopRetainedCeremonyCodecs.selfRecipientContext(rawQr, server.identity, D1.Platform.MACOS, spki,
                        requireNotNull(stored.owner.ceremony), expiry)
                    recipientClaim = SelfV2.readRequest(SelfV2.Operation.CLAIM, original, context)
                    Unit
                } finally { spki.fill(0); rawQr.fill(0) }
            }
            restore.thenCompose {
                val nextOwner = stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision)
                runtime.resumeProtected(snapshot, slot, nextOwner).thenCompose { ticket ->
                    currentOwner = ticket.owner
                    if (stored.owner.stage == SecureStage.RECORDED) {
                        if (!source) SelfV2.continuationContext(requireNotNull(recipientClaim), receipt)
                        else if (pending.wireKind == PendingWireKind.SELF_START) {
                            val rendezvous = material.second.copy()
                            try { qr?.close(); qr = runtime.secure.retainTransient(SelfV2.qrFromStart(requireNotNull(sourceStart), receipt, rendezvous, Instant.now())) }
                            finally { rendezvous.fill(0) }
                        }
                        publishStatus(SecureJson.parse(receipt, 65_536, canonical = false)); CompletableFuture.completedFuture(Unit)
                    } else {
                        val body = SecureJson.parse(current, 8192)
                        val endpoint = if (source) { if ("action" in body) AccountEndpoint.SELF_DECISION else AccountEndpoint.SELF_START }
                            else if ("exchange_id" in body) AccountEndpoint.SELF_EXCHANGE else if ("claim_request_sha256" in body) AccountEndpoint.SELF_POLL else AccountEndpoint.SELF_CLAIM
                        if (endpoint == AccountEndpoint.SELF_EXCHANGE) runtime.executeSession(ticket, endpoint)
                        else runtime.executePending(ticket, endpoint).thenCompose { result ->
                            val raw = result.response.copy()
                            try {
                                val value = SecureJson.parse(raw, 65_536, canonical = false)
                                if (!source) SelfV2.continuationContext(requireNotNull(recipientClaim), raw)
                                progress(result.ticket, raw).thenApply { currentOwner = it; publishStatus(value); Unit }
                            } finally { raw.fill(0); result.close() }
                        }
                    }
                }
            }.whenComplete { _, _ -> original.fill(0); receipt.fill(0); current.fill(0); material.close(); stored.close() }
        } }
    }

    public fun pollRecipient(): CompletionStage<Unit> = recipientContinuation(exchange = false, confirmedAccount = false)
    public fun exchangeRecipient(confirmedAccount: Boolean): CompletionStage<Unit> = recipientContinuation(true, confirmedAccount)

    private fun recipientContinuation(exchange: Boolean, confirmedAccount: Boolean): CompletableFuture<Unit> = runtime.command {
        val previous = requireNotNull(currentOwner); controlRequire(previous.role == SecureRole.RECIPIENT)
        val server = requireNotNull(runtime.selected); val value = requireNotNull(status)
        if (exchange) controlRequire(confirmedAccount && value.string("state") == "APPROVED" &&
            requireNotNull(previous.expiresAtEpochSeconds) > Instant.now().epochSecond, AccountControlFailure.CONFLICT)
        runtime.secure.snapshot().thenCompose { snapshot ->
            runtime.secure.read(requireNotNull(SecureRecordKind.SELF_RECIPIENT.reserved), snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                val bytes = (stored.payload as PendingPayload).ceremonyAuthorizationCopy()
                val envelope = try { SelfSecretEnvelope.read(bytes) } finally { bytes.fill(0) }
                val operation = UUID.randomUUID().toString(); val commit = if (exchange) UUID.randomUUID().toString() else null
                val owner = stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision,
                    operation = operation, account = if (exchange) value.string("account_id") else stored.owner.account,
                    bindingCommit = commit, stage = SecureStage.PREPARED)
                val rawStatus = SecureJson.encode(value)
                val context = try { SelfV2.continuationContext(requireNotNull(recipientClaim), rawStatus) } finally { rawStatus.fill(0) }
                val refresh = if (exchange) freshSecret(32) else byteArrayOf()
                val claim = requireNotNull(recipientClaim)
                val claimBytes = claim.canonicalDocumentCopy()
                val originalClaim = try { SecureJson.parse(claimBytes, 8192) } finally { claimBytes.fill(0) }
                val body = SecureJson.encode(buildJsonObject {
                    selfFields(server, requireNotNull(owner.ceremony)); put("claim_id", originalClaim.getValue("claim_id")); put("claim_request_sha256", claim.requestSha256)
                    if (exchange) {
                        put("exchange_id", operation); put("binding_commit_id", commit); put("approval_operation_id", value.getValue("approval_operation_id"))
                        put("confirmed_account_id", value.getValue("account_id")); put("next_refresh_token_sha256", secureSha256(refresh))
                    }
                })
                val prepared = try { SelfV2.prepare(if (exchange) SelfV2.Operation.EXCHANGE else SelfV2.Operation.POLL, body, context, Instant.now()) }
                    finally { body.fill(0) }
                val nextSecret = runtime.secure.transientSecret(refresh)
                runtime.signer.sign(snapshot, stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision), prepared).thenCompose { command ->
                    val poll = envelope.second.copy(); val original = envelope.original.copy(); val rawQr = envelope.qr.copy()
                    val auth = try { SelfSecretEnvelope.encode(poll, poll, rawQr, original) } finally { poll.fill(0); original.fill(0); rawQr.fill(0) }
                    val successor = nextSecret.copy()
                    try { handoff(snapshot, stored.owner, owner, command, auth, successor) }
                    finally { auth.fill(0); successor.fill(0) }
                }.thenCompose { ticket ->
                    if (exchange) runtime.executeSession(ticket, AccountEndpoint.SELF_EXCHANGE)
                    else runtime.executePending(ticket, AccountEndpoint.SELF_POLL).thenCompose { result ->
                        val raw = result.response.copy()
                        try { SelfV2.continuationContext(claim, raw); val value = SecureJson.parse(raw, 65_536); progress(result.ticket, raw).thenApply {
                            currentOwner = it; publishStatus(value); Unit
                        } } finally { raw.fill(0); result.close() }
                    }
                }.whenComplete { _, _ -> stored.close(); envelope.close(); nextSecret.close(); refresh.fill(0) }
            }
        }
    }

    private fun progress(ticket: SecurePendingTicket, receipt: ByteArray): CompletableFuture<SecureRecordOwner> {
        val owned = receipt.copyOf()
        return runtime.secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                val owner = stored.owner.copy(stage = SecureStage.RECORDED)
                runtime.secure.publish(ticket.control, requireNotNull(owner.operation)) {
                    listOf(SecureRecord(stored.slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), owner,
                        (stored.payload as PendingPayload).duplicate(owned)))
                }.thenApply { owner }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> owned.fill(0) }
    }

    private fun handoff(snapshot: SecureControl, previous: SecureRecordOwner, next: SecureRecordOwner,
        command: SelfV2.WireRequest, authorization: ByteArray, successor: ByteArray): CompletableFuture<SecurePendingTicket> {
        val slot = requireNotNull((if (next.role == SecureRole.SOURCE) SecureRecordKind.SELF_SOURCE else SecureRecordKind.SELF_RECIPIENT).reserved)
        SecureJournalRestoration.requireScope(command, next)
        val headers = runtime.secure.transientSecret(authorization); val refresh = runtime.secure.transientSecret(successor)
        return runtime.secure.read(slot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                controlRequire(stored.owner.operation == previous.operation && stored.owner.stage == SecureStage.RECORDED && stored.owner.ceremony == next.ceremony)
                val prior = stored.payload as PendingPayload; val receipt = prior.receiptCopy()
                val header = headers.copy(); val successorCopy = refresh.copy()
                val authority = try { AccountCommandContextCodec.replacingAuthorization(prior, header) } finally { header.fill(0) }
                val wire = command.canonicalDocumentCopy(); val proof = command.signatureCopy() ?: byteArrayOf()
                val digest = ByteArray(32) { command.requestSha256.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
                val pending = try { prior.secretContextCopy().use { secrets -> PendingPayload(prior.kind, PendingOperation.SELF,
                    PendingWireKind.valueOf("SELF_${command.operation.name}"), wire, digest, proof, authority, successorCopy, secrets = secrets) } }
                    finally { wire.fill(0); digest.fill(0); proof.fill(0); authority.fill(0); successorCopy.fill(0) }
                runtime.custody.advancePrepared(SecurePendingTicket(snapshot, slot, stored.owner), next, pending, receipt,
                    grantProducing = command.operation == SelfV2.Operation.EXCHANGE) { original, target, exact ->
                    val value = SecureJson.parse(exact, 65_536, canonical = false)
                    original.owner == stored.owner && target == next && value.string("ceremony_id") == next.ceremony &&
                        value.number("revision") > 0 && value.string("state") in setOf("OPEN", "CLAIMED", "APPROVED")
                }.whenComplete { _, _ -> receipt.fill(0); stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> headers.close(); refresh.close() }
    }

    private fun JsonObjectBuilder.selfFields(server: AccountSelectedServer, ceremony: String) {
        trustFields(server); put("ceremony_id", ceremony); put("requested_at", Instant.now().toString())
    }
    private fun publishStatus(value: JsonObject) {
        val previous = status
        controlRequire(value.string("state") in setOf("OPEN", "CLAIMED", "APPROVED", "REJECTED", "CANCELLED", "EXCHANGED", "EXPIRED"))
        controlRequire(previous == null || value.string("ceremony_id") != previous.string("ceremony_id") || value.number("revision") >= previous.number("revision"))
        status = value
        val sas = (value["comparison_code"] as? JsonPrimitive)?.content
        controlRequire(sas == null || Regex("[0-9]{12}").matches(sas))
        mutable.value = DesktopSelfPairingViewState(value.string("state"), (value["device_name"] as? JsonPrimitive)?.content,
            sas, (value["account_label"] as? JsonPrimitive)?.content, (value["recipient_platform"] as? JsonPrimitive)?.takeIf { it.isString }?.content)
    }
    override fun close() { qr?.close(); qr = null; secrets?.close(); secrets = null; currentOwner = null; status = null }
}

/** Fixed binary named header/poll/QR/original fields preserve exact original evidence within 8192B. */
internal object SelfSecretEnvelope {
    private val magic = "APSELF1\n".toByteArray(Charsets.US_ASCII)
    fun encode(header: ByteArray, poll: ByteArray, qr: ByteArray, original: ByteArray): ByteArray {
        val fields = listOf(header, poll, qr, original)
        val length = 24 + fields.sumOf { it.size }
        controlRequire(length <= 8192)
        return ByteBuffer.allocate(length).put(magic).also { buffer -> fields.forEach { buffer.putInt(it.size).put(it) } }.array()
    }
    fun read(bytes: ByteArray): SelfSecretFields {
        controlRequire(bytes.size in 24..8192)
        val buffer = ByteBuffer.wrap(bytes); val prefix = ByteArray(8).also(buffer::get)
        controlRequire(prefix.contentEquals(magic))
        val fields = ArrayList<ByteArray>()
        try {
            repeat(4) { controlRequire(buffer.remaining() >= 4); val length = buffer.int
                controlRequire(length in 0..buffer.remaining()); fields.add(ByteArray(length).also(buffer::get)) }
            controlRequire(!buffer.hasRemaining())
            return SelfSecretFields(SecretBytes(fields[0]), SecretBytes(fields[1]), SecretBytes(fields[2]), SecretBytes(fields[3]))
        } finally { fields.forEach { it.fill(0) } }
    }
    fun isEnvelope(bytes: ByteArray): Boolean = bytes.size >= magic.size && bytes.copyOfRange(0, magic.size).contentEquals(magic)
}
internal class SelfSecretFields(val header: SecretBytes, val second: SecretBytes, val qr: SecretBytes, val original: SecretBytes) : AutoCloseable {
    override fun close() { header.close(); second.close(); qr.close(); original.close() }
}
