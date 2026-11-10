package app.autplay.desktop.account

import app.autplay.application.securestate.*
import app.autplay.desktop.security.SecurePendingTicket
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlinx.serialization.json.*

public class DesktopInvitationHandle internal constructor(internal val id: String, internal val binding: String) {
    override fun toString(): String = "DesktopInvitationHandle(<redacted>)"
}
public class DesktopManagedInvitation internal constructor(public val handle: DesktopInvitationHandle,
    public val expiresAt: Instant, private val document: SecretBytes) : AutoCloseable {
    private var displayClaimed: Boolean = false
    /** Dedicated invitation reveal only: no clipboard, log, ordinary export or saved UI state. */
    @Synchronized public fun documentForExplicitDisplay(): SecretBytes {
        controlRequire(!displayClaimed && expiresAt > Instant.now(), AccountControlFailure.EXPIRED)
        displayClaimed = true
        val bytes = document.copy()
        return try { SecretBytes(bytes) } finally { bytes.fill(0); document.close() }
    }
    @Synchronized override fun close(): Unit { displayClaimed = true; document.close() }
    override fun toString(): String = "DesktopManagedInvitation(<redacted>)"
}

/** Exact invitation lifecycle. Admission requires K's distinct closed wire variant. */
public class DesktopEnrollmentInvitationController(private val runtime: DesktopAccountRuntime,
    private val profiles: DesktopProfileController) {
    public fun create(expiresInSeconds: Int, confirmed: Boolean): CompletionStage<DesktopManagedInvitation> = runtime.command {
        controlRequire(confirmed && expiresInSeconds in 60..1800)
        profiles.authorized("createEnrollmentInvitation") { snapshot, binding, access, _ ->
            val operation = UUID.randomUUID().toString()
            val request = SecureJson.encode(buildJsonObject {
                put("contract_version", "v1"); put("schema_version", 1); put("operation_id", operation); put("expires_in_seconds", expiresInSeconds)
            })
            try { freeze(snapshot, binding.copy(operation = operation, ceremony = operation, claim = operation, stage = SecureStage.PREPARED), request, access) }
            finally { request.fill(0) }
        }.thenCompose(::finishCreate)
    }

    public fun cancel(invitation: DesktopInvitationHandle, confirmed: Boolean): CompletionStage<Unit> = runtime.command {
        controlRequire(confirmed)
        profiles.authorized("cancelEnrollmentInvitation") { snapshot, binding, access, _ ->
            controlRequire(invitation.binding == binding.bindingCommit, AccountControlFailure.CONTEXT_CHANGED)
            val operation = UUID.randomUUID().toString()
            val request = SecureJson.encode(buildJsonObject { put("contract_version", "v1"); put("schema_version", 1); put("operation_id", operation) })
            try { freeze(snapshot, binding.copy(operation = operation, ceremony = operation, claim = invitation.id, stage = SecureStage.PREPARED), request, access) }
            finally { request.fill(0) }
        }.thenCompose(::finishCancel)
    }

    public fun resumeCreate(pendingSlot: String): CompletionStage<DesktopManagedInvitation?> = runtime.command {
        reconcileCancelledCreation(pendingSlot).thenCompose { cancelled ->
            if (cancelled) CompletableFuture.completedFuture<DesktopManagedInvitation?>(null)
            else resume(pendingSlot, true).thenCompose(::finishCreate).thenApply<DesktopManagedInvitation?> { it }
        }
    }
    public fun resumeCancel(pendingSlot: String): CompletionStage<Unit> = runtime.command {
        resume(pendingSlot, false).thenCompose(::finishCancel)
    }

    private fun resume(slot: String, create: Boolean): CompletableFuture<SecurePendingTicket> {
        canonicalUuid(slot)
        return runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { nullable ->
            val record = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                runtime.requireTarget(record.owner, requireNotNull(runtime.selected))
                val payload = record.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                controlRequire(payload.kind == SecureRecordKind.PROFILE_COMMAND && payload.operation == PendingOperation.ENROLLMENT)
                val request = payload.requestCopy()
                try { controlRequire(("expires_in_seconds" in SecureJson.parse(request, 4096)) == create && payload.wireKind == wireKind(create)) } finally { request.fill(0) }
                // A lost issuance response cannot be replayed: the server retains only its secret hash.
                if (create) secureRequire(record.owner.stage == SecureStage.PREPARED, SecureStateStatus.OUTCOME_PENDING)
                runtime.resumeProtected(snapshot, slot, record.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision))
            } finally { record.close() }
        } }
    }

    private fun freeze(snapshot: SecureControl, owner: SecureRecordOwner, request: ByteArray,
        authorization: ByteArray): CompletableFuture<SecurePendingTicket> {
        owner.requireBinding()
        val create = "expires_in_seconds" in SecureJson.parse(request, 4096)
        val wireKind = wireKind(create)
        val slot = UUID.randomUUID().toString(); val operation = requireNotNull(owner.operation)
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(request)
        val payload = PendingPayload(SecureRecordKind.PROFILE_COMMAND, PendingOperation.ENROLLMENT, wireKind, request, digest, byteArrayOf(), authorization)
        digest.fill(0)
        return runtime.secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot), 8, 4L * SecureStateLimits.VALUE)).thenCompose { reserved ->
            runtime.secure.publish(reserved, operation) { listOf(SecureRecord(slot, 1, UUID.randomUUID().toString(), owner, payload.duplicate())) }
                .thenApply { SecurePendingTicket(it, slot, owner) }
        }.whenComplete { _, _ -> payload.close() }
    }
    private fun wireKind(create: Boolean): PendingWireKind =
        if (create) PendingWireKind.ENROLLMENT_INVITATION_CREATE else PendingWireKind.ENROLLMENT_INVITATION_CANCEL

    private fun finishCreate(ticket: SecurePendingTicket): CompletableFuture<DesktopManagedInvitation> =
        runtime.executePending(ticket, AccountEndpoint.INVITATION_CREATE).thenCompose { result ->
            val bytes = result.response.copy()
            try {
                val value = SecureJson.parse(bytes, 16_384, canonical = false)
                value.exact("contract_version", "schema_version", "invitation_id", "server_instance_id", "identity_epoch",
                    "identity_thumbprint_sha256", "api_origin", "stream_origin", "user_id", "account_display_name",
                    "issued_at", "expires_at", "invitation_secret", "secret_handling")
                controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L &&
                    value.string("invitation_id") == ticket.owner.operation && value.string("server_instance_id") == ticket.owner.server &&
                    value.number("identity_epoch") == ticket.owner.identityEpoch && value.string("identity_thumbprint_sha256") == ticket.owner.identityThumbprint &&
                    value.string("api_origin") == ticket.owner.apiOrigin && value.string("stream_origin") == ticket.owner.streamOrigin &&
                    value.string("user_id") == ticket.owner.account && value.string("secret_handling") == "DISPLAY_ONCE_NO_CLIPBOARD_NO_LOG_NO_EXPORT")
                DesktopAccountTrust.display(value.string("account_display_name"), 120)
                val issued = Instant.parse(value.string("issued_at")); val expires = Instant.parse(value.string("expires_at"))
                val now = Instant.now()
                controlRequire(issued < expires && expires <= issued.plusSeconds(1800) && expires > now && expires <= now.plusSeconds(1800))
                DesktopAccountTrust.decode(value.string("invitation_secret"), true, 32).fill(0)
                val secret = runtime.secure.transientSecret(bytes)
                try { runtime.recordTerminal(result.ticket, bytes, { owner, _ -> owner.operation == value.string("invitation_id") }, false).thenApply {
                    DesktopManagedInvitation(DesktopInvitationHandle(value.string("invitation_id"), requireNotNull(ticket.owner.bindingCommit)), expires, secret)
                }.whenComplete { _, error -> if (error != null) secret.close() } }
                catch (failure: Exception) { secret.close(); throw failure }
            } finally { bytes.fill(0); result.close() }
        }

    private fun finishCancel(ticket: SecurePendingTicket): CompletableFuture<Unit> =
        runtime.executePending(ticket, AccountEndpoint.INVITATION_CANCEL).thenCompose { result ->
            val bytes = result.response.copy()
            try {
                val value = SecureJson.parse(bytes, 4096, canonical = false)
                value.exact("contract_version", "schema_version", "operation_id", "outcome", "terminal_at")
                controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L &&
                    value.string("operation_id") == ticket.owner.operation && value.string("outcome") in setOf("APPLIED", "ALREADY_TERMINAL"))
                Instant.parse(value.string("terminal_at"))
                val retained = bytes.copyOf()
                try { runtime.recordTerminal(result.ticket, bytes, { owner, _ -> owner.operation == value.string("operation_id") }, false)
                    .thenCompose { control -> settleCancelledIssuances(control, ticket.owner, retained) }.thenApply { Unit }
                    .whenComplete { _, _ -> retained.fill(0) } }
                catch (failure: Exception) { retained.fill(0); throw failure }
            } finally { bytes.fill(0); result.close() }
        }

    /** A positive cancellation receipt also settles a possibly-sent local issuance of that target. */
    private fun settleCancelledIssuances(control: SecureControl, cancelledBy: SecureRecordOwner,
        receipt: ByteArray): CompletableFuture<SecureControl> {
        verifyCancellation(cancelledBy, receipt)
        var effect = CompletableFuture.completedFuture(control)
        control.selected.forEach { reference -> effect = effect.thenCompose { current ->
            runtime.secure.read(reference.slot, current).thenCompose { record ->
                if (record == null) CompletableFuture.completedFuture(current)
                else try {
                    val pending = record.payload as? PendingPayload
                    val settles = pending?.kind == SecureRecordKind.PROFILE_COMMAND &&
                        pending.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CREATE &&
                        record.owner.stage in setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.RECORDED) &&
                        record.owner.operation == cancelledBy.claim && record.owner.claim == record.owner.operation &&
                        sameTarget(record.owner, cancelledBy)
                    if (!settles) CompletableFuture.completedFuture(current)
                    else runtime.recordTerminal(SecurePendingTicket(current, record.slot, record.owner), receipt,
                        { owner, exact -> owner.operation == cancelledBy.claim && sameTarget(owner, cancelledBy) && verifyCancellation(cancelledBy, exact) }, false)
                } finally { record.close() }
            }
        } }
        return effect
    }

    /** Crash after cancel acknowledgement: finish the retained local association without HTTP. */
    private fun reconcileCancelledCreation(slot: String): CompletableFuture<Boolean> {
        canonicalUuid(slot)
        return runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { creation ->
            if (creation == null) throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                runtime.requireTarget(creation.owner, requireNotNull(runtime.selected))
                val pending = creation.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                controlRequire(pending.kind == SecureRecordKind.PROFILE_COMMAND && pending.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CREATE)
                val owner = creation.owner
                var evidence = CompletableFuture.completedFuture<Pair<SecureRecordOwner, ByteArray>?>(null)
                snapshot.selected.forEach { reference -> evidence = evidence.thenCompose { found ->
                    if (found != null) CompletableFuture.completedFuture(found)
                    else runtime.secure.read(reference.slot, snapshot).thenApply { record ->
                        if (record == null) null
                        else try {
                            val payload = record.payload as? PendingPayload
                            if (payload?.kind != SecureRecordKind.PROFILE_COMMAND || payload.wireKind != PendingWireKind.ENROLLMENT_INVITATION_CANCEL ||
                                record.owner.stage != SecureStage.TERMINAL || record.owner.claim != owner.operation || !sameTarget(owner, record.owner)) null
                            else {
                                val bytes = payload.receiptCopy()
                                if (bytes.isEmpty()) { bytes.fill(0); null }
                                else try { verifyCancellation(record.owner, bytes); record.owner to bytes }
                                catch (failure: Exception) { bytes.fill(0); throw failure }
                            }
                        } finally { record.close() }
                    }
                } }
                evidence.thenCompose { found ->
                    if (found == null) CompletableFuture.completedFuture(false)
                    else try { settleCancelledIssuances(snapshot, found.first, found.second).thenApply { true }
                        .whenComplete { _, _ -> found.second.fill(0) } }
                    catch (failure: Exception) { found.second.fill(0); throw failure }
                }
            } finally { creation.close() }
        } }
    }

    private fun sameTarget(left: SecureRecordOwner, right: SecureRecordOwner): Boolean =
        left.profile == right.profile && left.account == right.account && left.server == right.server &&
            left.identityEpoch == right.identityEpoch && left.identityThumbprint == right.identityThumbprint &&
            left.apiOrigin == right.apiOrigin && left.streamOrigin == right.streamOrigin && left.device == right.device &&
            left.keyTag == right.keyTag && left.keyThumbprint == right.keyThumbprint && left.bindingCommit == right.bindingCommit

    private fun verifyCancellation(owner: SecureRecordOwner, receipt: ByteArray): Boolean {
        owner.requireBinding(); canonicalUuid(requireNotNull(owner.claim))
        val value = SecureJson.parse(receipt, 4096, canonical = false)
        value.exact("contract_version", "schema_version", "operation_id", "outcome", "terminal_at")
        controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L &&
            value.string("operation_id") == owner.operation && value.string("outcome") in setOf("APPLIED", "ALREADY_TERMINAL"))
        Instant.parse(value.string("terminal_at"))
        return true
    }
}
