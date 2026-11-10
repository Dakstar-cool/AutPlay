package app.autplay.desktop.security

import app.autplay.application.devicecompatibility.ProfileDeviceContractV2
import app.autplay.application.devicecompatibility.SelfDeviceContractV2
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2
import app.autplay.application.portableaccount.PortableDeviceContractV2
import app.autplay.application.securestate.*
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.serialization.json.*

/** J owns its real durable projection; a volatile marker is never an implementation of this port. */
public interface SecureBindingProjection {
    public fun beforeDispatch(ticket: SecurePendingTicket): CompletableFuture<SecureBindingDispatchPin>
    public fun reconcile(owner: SecureRecordOwner, revalidate: (SecureRecordOwner) -> CompletableFuture<Boolean>): CompletableFuture<Unit>
    public fun activateLocalRoot(): CompletableFuture<Unit>
    public fun reconcileGuards(owner: SecureRecordOwner): CompletableFuture<Unit>
    public fun prepareExpiredBinding(owner: SecureRecordOwner): CompletableFuture<Unit>
    public fun cancelIntent(ticket: SecurePendingTicket): CompletableFuture<Unit>
    public fun commit(owner: SecureRecordOwner): CompletableFuture<Unit>
    public fun matches(owner: SecureRecordOwner): CompletableFuture<Boolean>
}
/** Shared K/J pin, held through the actual transport settlement, independent of caller cancellation. */
public interface SecureBindingDispatchPin : AutoCloseable {
    public fun <T> submit(action: () -> CompletableFuture<T>): CompletableFuture<T>
    override fun close(): Unit
}
public class SecurePendingTicket(public val control: SecureControl, public val slot: String, public val owner: SecureRecordOwner) {
    override fun toString(): String = "SecurePendingTicket(<redacted>)"
}
public class SecureDispatchResult(public val ticket: SecurePendingTicket, public val response: SecretBytes) : AutoCloseable {
    override fun close(): Unit = response.close()
    override fun toString(): String = "SecureDispatchResult(<redacted>)"
}
public class SecureReplayMaterial(request: ByteArray, authorization: ByteArray, public val wireKind: PendingWireKind,
    public val mode: SecurePendingDispatchMode,
    private val released: (SecureReplayMaterial) -> Unit = {}) : AutoCloseable {
    public val request: SecretBytes = SecretBytes(request)
    public val authorization: SecretBytes = SecretBytes(authorization)
    override fun close() { request.close(); authorization.close(); released(this) }
    override fun toString(): String = "SecureReplayMaterial(<redacted>)"
}

/** Concrete credential-first custody, exact replay and projection handoff. No HTTP/provider policy. */
public class DesktopSecureCustody(private val secure: DesktopSecureStateOwner, private val projection: SecureBindingProjection) {
    public fun prepareProfile(snapshot: SecureControl, slot: String, owner: SecureRecordOwner,
        command: ProfileDeviceContractV2.SignedCommand, authorization: ByteArray, successor: ByteArray): CompletableFuture<SecurePendingTicket> {
        secureRequire(command.platform.name == "MACOS")
        SecureJournalRestoration.requireScope(command, owner)
        val operation = when (command.operation) {
            ProfileDeviceContractV2.Operation.ENROLLMENT_EXCHANGE -> PendingOperation.ENROLLMENT
            ProfileDeviceContractV2.Operation.SESSION_ROTATE -> PendingOperation.ROTATION
            ProfileDeviceContractV2.Operation.TRUSTED_CHALLENGE, ProfileDeviceContractV2.Operation.TRUSTED_EXCHANGE -> PendingOperation.TRUSTED_EXCHANGE
            else -> PendingOperation.ADMISSION
        }
        val wire = command.transientWireCopy(); val digest = command.requestDigestCopy(); val proof = command.signatureCopy()
        val grant = command.operation in setOf(ProfileDeviceContractV2.Operation.ENROLLMENT_EXCHANGE, ProfileDeviceContractV2.Operation.ADMISSION_EXCHANGE, ProfileDeviceContractV2.Operation.TRUSTED_EXCHANGE)
        return try { savePending(snapshot, slot, owner, PendingPayload(SecureRecordKind.PROFILE_COMMAND, operation, PendingWireKind.valueOf(command.operation.name), wire, digest, proof, authorization, successor), grant = grant) }
        finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    }
    public fun prepareSelf(snapshot: SecureControl, owner: SecureRecordOwner,
        command: SelfDeviceContractV2.WireRequest, authorization: ByteArray, successor: ByteArray,
        secrets: PendingSecretContext, originalQrDocument: ByteArray = byteArrayOf()): CompletableFuture<SecurePendingTicket> {
        secureRequire(command.platform.name == "MACOS" && command.platformRole.name == owner.role.name)
        SecureJournalRestoration.requireScope(command, owner)
        secureRequire(originalQrDocument.size <= 4096)
        if (command.operation == SelfDeviceContractV2.Operation.CLAIM) { secureRequire(originalQrDocument.isNotEmpty()); SecureJson.parse(originalQrDocument, 4096, canonical = false) }
        requireSelfSecrets(command, authorization, secrets)
        val kind = if (owner.role == SecureRole.SOURCE) SecureRecordKind.SELF_SOURCE else SecureRecordKind.SELF_RECIPIENT
        val wire = command.canonicalDocumentCopy(); val proof = command.signatureCopy() ?: byteArrayOf()
        val digestText = command.requestSha256.also(::sha256Text)
        val digest = ByteArray(32) { digestText.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        return try { savePending(snapshot, requireNotNull(kind.reserved), owner, PendingPayload(kind, PendingOperation.SELF, PendingWireKind.valueOf("SELF_${command.operation.name}"), wire, digest, proof, authorization, successor, receipt = originalQrDocument, secrets = secrets), grant = command.operation == SelfDeviceContractV2.Operation.EXCHANGE) }
        finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    }
    public fun prepareRecoveryOrDeletion(snapshot: SecureControl, owner: SecureRecordOwner,
        command: RecoveryDeletionDeviceContractV2.SignedCommand, authorization: ByteArray, successor: ByteArray,
        secrets: PendingSecretContext, verifyFrozenSecrets: (SecureRecordOwner, ByteArray, PendingSecretContext) -> Boolean): CompletableFuture<SecurePendingTicket> {
        secureRequire(command.platform.name == "MACOS")
        SecureJournalRestoration.requireScope(command, owner)
        val recovery = command.bodyKind in setOf(RecoveryDeletionDeviceContractV2.BodyKind.RECOVERY_PREVIEW, RecoveryDeletionDeviceContractV2.BodyKind.RECOVERY_COMMIT)
        val kind = if (recovery) { if (owner.role == SecureRole.SOURCE) SecureRecordKind.RECOVERY_SOURCE else SecureRecordKind.RECOVERY_RECIPIENT }
            else { if (owner.role == SecureRole.SOURCE) SecureRecordKind.DELETION_SOURCE else SecureRecordKind.DELETION_RECIPIENT }
        val wire = command.fullOriginalWireCopy(); val digest = command.requestDigestCopy(); val proof = command.signatureCopy()
        return try {
            secureRequire(verifyFrozenSecrets(owner, wire, secrets), SecureStateStatus.CONTEXT_CHANGED)
            savePending(snapshot, requireNotNull(kind.reserved), owner.copy(deletionVeto = !recovery), PendingPayload(kind, if (recovery) PendingOperation.RECOVERY else PendingOperation.DELETION, PendingWireKind.valueOf(command.bodyKind.name), wire, digest, proof, authorization, successor, secrets = secrets), grant = command.bodyKind in setOf(RecoveryDeletionDeviceContractV2.BodyKind.RECOVERY_COMMIT, RecoveryDeletionDeviceContractV2.BodyKind.DELETION_CANCEL))
        }
        finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    }
    public fun prepareRecoverySource(snapshot: SecureControl, owner: SecureRecordOwner,
        exactRequest: ByteArray, authorization: ByteArray, secrets: PendingSecretContext,
        verifyFrozenConfigure: (SecureRecordOwner, ByteArray, PendingSecretContext) -> Boolean): CompletableFuture<SecurePendingTicket> {
        owner.requireBinding()
        secureRequire(owner.role == SecureRole.SOURCE && owner.protocolVersion == 1 && verifyFrozenConfigure(owner, exactRequest, secrets), SecureStateStatus.CONTEXT_CHANGED)
        val digest = MessageDigest.getInstance("SHA-256").digest(exactRequest)
        try { return savePending(snapshot, requireNotNull(SecureRecordKind.RECOVERY_SOURCE.reserved), owner,
            PendingPayload(SecureRecordKind.RECOVERY_SOURCE, PendingOperation.RECOVERY, PendingWireKind.RECOVERY_CONFIGURE,
                exactRequest, digest, byteArrayOf(), authorization, secrets = secrets), grant = false) }
        finally { digest.fill(0) }
    }
    public fun preparePortable(snapshot: SecureControl, owner: SecureRecordOwner,
        command: PortableDeviceContractV2.SignedCommand, login: Boolean, successor: ByteArray,
        recoveryArchive: ByteArray = byteArrayOf(), previousBinding: ByteArray = byteArrayOf()): CompletableFuture<SecurePendingTicket> {
        val wire = command.transientWireCopy(); val digest = MessageDigest.getInstance("SHA-256").digest(wire)
        try {
        val doc = SecureJson.parse(wire, 16384)
        val request = doc["request"] as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        val challenge = request["challenge"] as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire(owner.protocolVersion == 2 && request.string("platform") == "MACOS" && request.string("contract_version") == "v2" && request.number("schema_version") == 2L &&
            request.string("binding_commit_id") == owner.bindingCommit && challenge.string("account_id") == owner.account &&
            challenge.string("server_instance_id") == owner.server && challenge.number("identity_epoch") == owner.identityEpoch &&
            challenge.string("identity_thumbprint_sha256") == owner.identityThumbprint && challenge.string("api_origin") == owner.apiOrigin &&
            challenge.string("stream_origin") == owner.streamOrigin && challenge.string("purpose") == (if (login) "LOGIN" else "REGISTER"), SecureStateStatus.CONTEXT_CHANGED)
        val deviceKey = java.util.Base64.getDecoder().decode(request.string("device_public_key_spki_b64"))
        try { DeviceSignatureCodec.validateSpki(deviceKey); secureRequire(secureSha256(deviceKey) == owner.keyThumbprint, SecureStateStatus.CONTEXT_CHANGED) } finally { deviceKey.fill(0) }
        val proof = java.util.Base64.getDecoder().decode(doc.string("proof"))
        return try { savePending(snapshot, requireNotNull(SecureRecordKind.PORTABLE_COMMAND.reserved), owner,
            PendingPayload(SecureRecordKind.PORTABLE_COMMAND, if (login) PendingOperation.PORTABLE_LOGIN else PendingOperation.PORTABLE_REGISTER,
                if (login) PendingWireKind.PORTABLE_LOGIN else PendingWireKind.PORTABLE_REGISTER,
                wire, digest, proof, successorRefresh = successor, recoveryArchive = recoveryArchive, previousBinding = previousBinding), grant = true) }
        finally { proof.fill(0) }
        } finally { wire.fill(0); digest.fill(0) }
    }

    /** Established portable ROOT rotation authorization, including both original DER proofs. */
    public fun preparePortableRotation(snapshot: SecureControl, owner: SecureRecordOwner,
        exactAuthorization: ByteArray, recoveryArchive: ByteArray, previousBinding: ByteArray,
        verifyFrozenRootAuthorization: (SecureRecordOwner, ByteArray) -> Boolean): CompletableFuture<SecurePendingTicket> {
        secureRequire(owner.protocolVersion == 1 && verifyFrozenRootAuthorization(owner, exactAuthorization), SecureStateStatus.CONTEXT_CHANGED)
        val document = SecureJson.parse(exactAuthorization, 16384)
        document.exact("request", "proof", "replacement_proof")
        val request = document["request"] as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire(request["replacement_account"] is JsonObject)
        val proof = java.util.Base64.getDecoder().decode(document.string("proof"))
        val replacementProof = java.util.Base64.getDecoder().decode(document.string("replacement_proof"))
        val digest = MessageDigest.getInstance("SHA-256").digest(exactAuthorization)
        try {
            secureRequire(proof.size in 8..72 && replacementProof.size in 8..72 && recoveryArchive.isNotEmpty())
            return savePending(snapshot, requireNotNull(SecureRecordKind.PORTABLE_COMMAND.reserved), owner,
                PendingPayload(SecureRecordKind.PORTABLE_COMMAND, PendingOperation.PORTABLE_ROTATE, PendingWireKind.PORTABLE_ROTATE, exactAuthorization,
                    digest, proof, recoveryArchive = recoveryArchive, previousBinding = previousBinding), grant = true)
        } finally { proof.fill(0); replacementProof.fill(0); digest.fill(0) }
    }

    /** Exact lifecycle/consent requests are bounded named variants; this is not a JSON store API. */
    public fun prepareLifecycle(snapshot: SecureControl, slot: String, owner: SecureRecordOwner,
        operation: PendingOperation, exactRequest: ByteArray, authorization: ByteArray,
        verifyFrozenRequest: (PendingOperation, SecureRecordOwner, ByteArray) -> Boolean): CompletableFuture<SecurePendingTicket> {
        secureRequire(operation in setOf(PendingOperation.LOGOUT_CURRENT, PendingOperation.LOGOUT_ALL, PendingOperation.REVOKE, PendingOperation.CONSENT, PendingOperation.VISIBILITY))
        owner.requireBinding()
        secureRequire(verifyFrozenRequest(operation, owner, exactRequest), SecureStateStatus.CONTEXT_CHANGED)
        val digest = MessageDigest.getInstance("SHA-256").digest(exactRequest)
        return try { savePending(snapshot, slot, owner,
            PendingPayload(if (operation == PendingOperation.CONSENT) SecureRecordKind.TRAINING_CONSENT else SecureRecordKind.LIFECYCLE_COMMAND,
                operation, PendingWireKind.valueOf(operation.name), exactRequest, digest, byteArrayOf(), authorization), grant = false) } finally { digest.fill(0) }
    }

    private fun savePending(snapshot: SecureControl, slot: String, owner: SecureRecordOwner,
        payload: PendingPayload, grant: Boolean): CompletableFuture<SecurePendingTicket> {
        secure.retainTransient(payload)
        try {
        secureRequire(owner.boot == secure.boot && owner.authorityRevision == snapshot.authorityRevision && owner.operation != null && owner.ceremony != null && owner.stage == SecureStage.PREPARED)
        val operation = requireNotNull(owner.operation)
        val rootSlot = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)
        val rootRead = if (grant) secure.read(rootSlot, snapshot) else CompletableFuture.completedFuture<SecureRecord?>(null)
        val result = rootRead.thenCompose { root ->
            try {
                if (root != null) secureRequire(root.owner.account == owner.account, SecureStateStatus.CONTEXT_CHANGED)
                val slots = listOfNotNull(slot, owner.profile, if (root != null) rootSlot else null).distinct()
                val reservation = CompletionReservation(operation, slots, 8, 4L * SecureStateLimits.VALUE)
                val first = if (snapshot.activeBinding == null) FirstBindOwner(requireNotNull(owner.ceremony), operation, owner.role) else null
                secure.reserveCompletion(snapshot, reservation, first).thenCompose { reserved ->
                    secure.publish(reserved, operation, activeBinding = if (payload.operation == PendingOperation.DELETION) null else reserved.activeBinding) {
                        val records = ArrayList<SecureRecord>()
                        try {
                            records.add(record(reserved, slot, owner, duplicatePending(payload)))
                            if (root != null) {
                                val stored = root.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
                                val passport = stored.passportCopy(); val recovery = stored.recoveryCopy(); val history = stored.lineageCopy(); val localGuard = stored.localGuardCopy()
                                try {
                                    val doc = SecureJson.parse(history, 49152)
                                    val enrolled = SecureJson.encode(JsonObject(doc + ("ever_enrolled" to JsonPrimitive(true))))
                                    try { records.add(record(reserved, rootSlot, root.owner.copy(boot = secure.boot, authorityRevision = reserved.authorityRevision), PortableRootPayload(passport, recovery, enrolled, stored.ownershipCutoffMs, true, localGuard))) }
                                    finally { enrolled.fill(0) }
                                } finally { passport.fill(0); recovery.fill(0); history.fill(0); localGuard.fill(0) }
                            }
                            records
                        } catch (failure: Exception) { records.forEach { it.close() }; throw failure }
                    }.thenApply { SecurePendingTicket(it, slot, owner) }
                }.whenComplete { _, _ -> root?.close() }
            } catch (failure: Exception) { root?.close(); throw failure }
        }
        return result.whenComplete { _, _ -> payload.close(); secure.releaseTransient(payload) }
        } catch (failure: Exception) { payload.close(); secure.releaseTransient(payload); throw failure }
    }

    /** A resumed historical owner is explicitly re-fenced; original wire/proofs remain unchanged. */
    public fun resume(snapshot: SecureControl, slot: String, expectedTarget: SecureRecordOwner,
        verifyFrozenOriginal: (SecureRecord) -> Boolean): CompletableFuture<SecurePendingTicket> =
        secure.read(slot, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                val resumed = stored.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision)
                secureRequire(resumed == expectedTarget && stored.payload is PendingPayload && stored.owner.stage != SecureStage.TERMINAL, SecureStateStatus.CONTEXT_CHANGED)
                secureRequire(verifyFrozenOriginal(stored), SecureStateStatus.CONTEXT_CHANGED)
                val reservation = snapshot.reservations.single { it.operation == resumed.operation }
                val resumedCapacity = reservation.copy(revisions = maxOf(8, reservation.revisions), bytes = maxOf(4L * SecureStateLimits.VALUE, reservation.bytes))
                secure.reserveCompletion(snapshot, resumedCapacity, snapshot.firstBind).thenCompose { reserved ->
                    secure.publish(reserved, requireNotNull(resumed.operation)) { listOf(record(reserved, slot, resumed, duplicatePending(stored.payload as PendingPayload))) }
                }
                    .thenApply { SecurePendingTicket(it, slot, resumed) }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }

    /** Marks possibly-sent before exposing a request. Cancellation never removes this evidence. */
    public fun dispatch(ticket: SecurePendingTicket, mode: SecurePendingDispatchMode = SecurePendingDispatchMode.ORIGINAL,
        transport: (SecureReplayMaterial) -> CompletableFuture<ByteArray>): CompletableFuture<SecureDispatchResult> {
        val completion = ticket.control.reservations.singleOrNull { it.operation == ticket.owner.operation }
            ?: return CompletableFuture.failedFuture(SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED))
        // Sent marker + credential-first pair + J witness/publish pair + final selected pair.
        // This consumes the existing 64 revision / installation budget, never another logical slot.
        val sourceSlot = requireNotNull(SecureRecordKind.DELETION_SOURCE.reserved)
        val cancellationSource = ticket.slot == SecureRecordKind.DELETION_RECIPIENT.reserved &&
            ticket.control.selected.any { it.slot == sourceSlot }
        val required = if (cancellationSource) 8 else 7
        return secure.reserveCompletion(ticket.control, completion.copy(
            slots = (completion.slots + if (cancellationSource) listOf(sourceSlot) else emptyList()).distinct(),
            revisions = maxOf(required, completion.revisions),
            bytes = maxOf(required.toLong() * 262_144, completion.bytes))).thenCompose { reserved ->
            dispatchPinned(SecurePendingTicket(reserved, ticket.slot, ticket.owner), mode, transport)
        }
    }

    private fun dispatchPinned(ticket: SecurePendingTicket, mode: SecurePendingDispatchMode,
        transport: (SecureReplayMaterial) -> CompletableFuture<ByteArray>): CompletableFuture<SecureDispatchResult> =
        projection.beforeDispatch(ticket).thenCompose { pin ->
        pin.submit { secure.read(ticket.slot, ticket.control) }.thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                secureRequire(stored.owner == ticket.owner && stored.payload is PendingPayload, SecureStateStatus.CONTEXT_CHANGED)
                val pending = stored.payload as PendingPayload
                secureRequire(pending.operation != PendingOperation.PUBLIC_REGISTRATION, SecureStateStatus.CONTEXT_CHANGED)
                // Issuance returns its secret once; unlike exchange, it has no server replay receipt.
                // A durable possibly-sent marker therefore forbids every subsequent creation POST.
                if (pending.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CREATE) {
                    secureRequire(stored.owner.stage == SecureStage.PREPARED && mode == SecurePendingDispatchMode.ORIGINAL,
                        SecureStateStatus.OUTCOME_PENDING)
                }
                val sentOwner = stored.owner.copy(stage = if (stored.owner.stage == SecureStage.RECORDED) SecureStage.RECORDED else SecureStage.DISPATCHED)
                pin.submit { secure.admitPending(ticket.control, ticket.slot, ticket.owner, mode) }.thenCompose {
                    pin.submit { secure.publish(ticket.control, requireNotNull(sentOwner.operation)) { listOf(record(ticket.control, ticket.slot, sentOwner, duplicatePending(pending))) } }
                }.thenCompose { sentControl ->
                    val generation = secure.admissionGeneration
                    secure.requireAdmission(generation)
                    val request = pending.requestCopy(); val authorization = pending.authorizationCopy()
                    val replay = try { secure.retainTransient(SecureReplayMaterial(request, authorization, pending.wireKind, mode) { secure.releaseTransient(it) }) } finally { request.fill(0); authorization.fill(0) }
                    try { transport(replay).thenApply { response ->
                        try { secure.requireAdmission(generation); secureRequire(response.size <= 65536); SecureDispatchResult(SecurePendingTicket(sentControl, ticket.slot, sentOwner), secure.transientSecret(response)) } finally { response.fill(0) }
                    }.whenComplete { _, _ -> replay.close() } }
                    catch (_: Exception) { replay.close(); throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING) }
                }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> pin.close() }
        }.handle { value, failure ->
            if (failure != null) {
                val cause = failure.cause ?: failure
                throw if (cause is SecureStateFailure) SecureStateFailure(cause.status) else SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            }
            value
        }

    /**
     * Positive protocol continuation transfers the durable first-bind owner/reservation in the
     * same publication. Old exact signed originals remain named cleanup references, charged and
     * readable with readRetained while the successor owns this slot. No dismissal/timeout handoff.
     */
    public fun advancePrepared(ticket: SecurePendingTicket, nextOwner: SecureRecordOwner,
        nextPayload: PendingPayload, exactPriorResponse: ByteArray, grantProducing: Boolean,
        verifyContinuation: (SecureRecord, SecureRecordOwner, ByteArray) -> Boolean): CompletableFuture<SecurePendingTicket> {
        secureRequire(exactPriorResponse.size in 2..65536)
        val receipt = secure.transientSecret(exactPriorResponse)
        secure.retainTransient(nextPayload)
        val result = secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val previous = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                val verified = receipt.copy()
                try { secureRequire(previous.owner == ticket.owner && nextOwner.stage == SecureStage.PREPARED && verifyContinuation(previous, nextOwner, verified), SecureStateStatus.CONTEXT_CHANGED) }
                finally { verified.fill(0) }
                val rootSlot = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)
                val rootRead = if (grantProducing) secure.read(rootSlot, ticket.control) else CompletableFuture.completedFuture<SecureRecord?>(null)
                rootRead.thenCompose { root ->
                    try {
                        if (root != null) secureRequire(root.owner.account == nextOwner.account, SecureStateStatus.CONTEXT_CHANGED)
                        val oldOperation = requireNotNull(ticket.owner.operation); val newOperation = requireNotNull(nextOwner.operation)
                        val oldReservation = ticket.control.reservations.single { it.operation == oldOperation }
                        val extended = oldReservation.copy(slots = (oldReservation.slots + listOfNotNull(ticket.slot, nextOwner.profile, if (root != null) rootSlot else null)).distinct(),
                            revisions = maxOf(8, oldReservation.revisions), bytes = maxOf(4L * SecureStateLimits.VALUE, oldReservation.bytes))
                        secure.reserveCompletion(ticket.control, extended, ticket.control.firstBind).thenCompose { reserved ->
                            val bytes = receipt.copy()
                            val handoff = try { SecurePendingHandoff(ticket.slot, reserved.selected.single { it.slot == ticket.slot }.valueId, oldOperation, newOperation, secureSha256(bytes)) } finally { bytes.fill(0) }
                            secure.publish(reserved, oldOperation, handoff = handoff) {
                                val resultBytes = receipt.copy()
                                val records = ArrayList<SecureRecord>()
                                try {
                                    records.add(record(reserved, ticket.slot, nextOwner, nextPayload.duplicate(resultBytes)))
                                    if (root != null) records.add(record(reserved, rootSlot, root.owner.copy(boot = secure.boot, authorityRevision = reserved.authorityRevision), (root.payload as PortableRootPayload).markEnrollmentAttempted()))
                                    records
                                } catch (failure: Exception) { records.forEach { it.close() }; throw failure }
                                finally { resultBytes.fill(0) }
                            }.thenApply { SecurePendingTicket(it, ticket.slot, nextOwner) }
                        }.whenComplete { _, _ -> root?.close() }
                    } catch (failure: Exception) { root?.close(); throw failure }
                }.whenComplete { _, _ -> previous.close() }
            } catch (failure: Exception) { previous.close(); throw failure }
        }
        return result.whenComplete { _, _ -> receipt.close(); nextPayload.close(); secure.releaseTransient(nextPayload) }
    }

    /** Complete local binding wins before any caller considers a grant-producing replay. */
    public fun durableBinding(snapshot: SecureControl, expected: SecureRecordOwner): CompletableFuture<Boolean> =
        secure.read(requireNotNull(expected.profile), snapshot).thenCompose { stored ->
            if (stored == null) CompletableFuture.completedFuture(false)
            else try {
                val same = stored.owner == expected && stored.payload is SessionPayload && snapshot.activeBinding == expected.profile && expected.stage == SecureStage.ACTIVE
                if (same) projection.matches(expected) else CompletableFuture.completedFuture(false)
            } finally { stored.close() }
        }

    /** Response must be verified by SecureSessionResponseCodec against this exact pending owner. */
    public fun commitSession(ticket: SecurePendingTicket, verified: VerifiedSecureSession): CompletableFuture<SecureControl> {
        secure.retainTransient(verified)
        return secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                secureRequire(stored.owner == ticket.owner && verified.pendingOwner == stored.owner, SecureStateStatus.CONTEXT_CHANGED)
                val pending = stored.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
                val profile = requireNotNull(verified.owner.profile)
                val committing = verified.owner.copy(stage = SecureStage.BINDING_COMMIT_PENDING)
                secure.publish(ticket.control, requireNotNull(ticket.owner.operation), activeBinding = null) {
                    listOf(record(ticket.control, profile, committing, verified.payloadCopy()),
                        record(ticket.control, ticket.slot, stored.owner.copy(stage = SecureStage.BINDING_COMMIT_PENDING), verified.pendingCopy(pending)))
                }.thenCompose { committed ->
                    // Real J projection is required; failure retains credentials and exact pending evidence.
                    projection.commit(committing).thenCompose { secure.snapshot() }.thenCompose { projected ->
                        secure.read(profile, projected).thenCompose { selected ->
                            val session = selected ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
                            try {
                                secureRequire(session.payload is SessionPayload && session.owner.copy(accountProjection = null) ==
                                    committing.copy(stage = SecureStage.ACTIVE, accountProjection = null) && projected.activeBinding == profile,
                                    SecureStateStatus.CONTEXT_CHANGED)
                                projection.matches(session.owner).thenApply { matched ->
                                    secureRequire(matched, SecureStateStatus.OUTCOME_PENDING)
                                    projected
                                }.whenComplete { _, _ -> session.close() }
                            } catch (failure: Exception) { session.close(); throw failure }
                        }
                    }
                }.whenComplete { _, _ -> stored.close(); verified.close() }
            } catch (failure: Exception) { stored.close(); verified.close(); throw failure }
        }.whenComplete { _, _ -> verified.close(); secure.releaseTransient(verified) }
    }

    /** Explicit protocol terminal outcome only; UI dismissal must never invoke this method. */
    public fun recordTerminal(ticket: SecurePendingTicket, exactReceipt: ByteArray,
        protocolVerified: (SecureRecordOwner, ByteArray) -> Boolean, inactive: Boolean): CompletableFuture<SecureControl> {
        secureRequire(exactReceipt.size <= 65536)
        return secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                secureRequire(stored.owner == ticket.owner && protocolVerified(stored.owner, exactReceipt), SecureStateStatus.CONTEXT_CHANGED)
                val pending = stored.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
                secureRequire(pending.operation != PendingOperation.DELETION, SecureStateStatus.OUTCOME_PENDING)
                secure.publish(ticket.control, requireNotNull(ticket.owner.operation), activeBinding = if (inactive) null else ticket.control.activeBinding, terminal = true) {
                    listOf(record(ticket.control, ticket.slot, stored.owner.copy(stage = SecureStage.TERMINAL), duplicatePending(pending, exactReceipt)))
                }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }
    }

    public fun cancelBeforeDispatch(ticket: SecurePendingTicket): CompletableFuture<SecureControl> =
        secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                secureRequire(stored.owner == ticket.owner && stored.owner.stage == SecureStage.PREPARED, SecureStateStatus.OUTCOME_PENDING)
                val pending = stored.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
                secure.publish(ticket.control, requireNotNull(ticket.owner.operation), terminal = true) {
                    listOf(record(ticket.control, ticket.slot, stored.owner.copy(stage = SecureStage.TERMINAL, deletionVeto = false), duplicatePending(pending)))
                }.thenCompose { current -> projection.cancelIntent(ticket).thenApply { current } }
                    .whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }

    public fun resumeBindingAfterRevalidation(snapshot: SecureControl, originalOwner: SecureRecordOwner,
        revalidate: (SecureRecordOwner) -> CompletableFuture<Boolean>): CompletableFuture<SecureControl> =
        secure.read(requireNotNull(originalOwner.profile), snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
            try {
                secureRequire(stored.owner == originalOwner && stored.payload is SessionPayload && !originalOwner.detached && !originalOwner.deletionVeto && (snapshot.activeBinding == null || snapshot.activeBinding == originalOwner.profile), SecureStateStatus.CONTEXT_CHANGED)
                projection.reconcile(originalOwner, revalidate).thenCompose { secure.snapshot() }
                    .whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }

    /** SOURCE reconciliation keeps an accepted deletion veto until verified cancellation/nonacceptance. */
    public fun recordDeletionOutcome(ticket: SecurePendingTicket, exactReceipt: ByteArray): CompletableFuture<SecurePendingTicket> =
        secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            try {
                secureRequire(stored.owner == ticket.owner && stored.payload is PendingPayload && stored.payload.kind == SecureRecordKind.DELETION_SOURCE, SecureStateStatus.CONTEXT_CHANGED)
                val pending = stored.payload as PendingPayload
                val result = SecureJson.parse(exactReceipt, 65536, canonical = false)
                secureRequire(result.string("contract_version") == "v2" && result.number("schema_version") == 2L && result.string("platform") == "MACOS" && result.string("deletion_request_id") == stored.owner.operation && result.string("account_id") == stored.owner.account)
                val state = result.string("state")
                if (state == "NOT_ACCEPTED") {
                    result.exact("contract_version", "schema_version", "platform", "deletion_request_id", "account_id", "request_sha256", "state", "resolved_at", "replayed")
                    val wire = pending.requestCopy()
                    try { secureRequire(result.string("request_sha256") == SecureJson.parse(wire, 8192, canonical = false).string("request_sha256")) } finally { wire.fill(0) }
                    java.time.Instant.parse(result.string("resolved_at"))
                } else {
                    result.exact("contract_version", "schema_version", "platform", "deletion_request_id", "account_id", "state", "revision", "requested_at", "cancel_before", "replayed")
                    secureRequire(state in setOf("PENDING", "CANCELLED") && result.number("revision") > 0)
                    secureRequire(java.time.Instant.parse(result.string("cancel_before")) > java.time.Instant.parse(result.string("requested_at")))
                    if (stored.owner.stage == SecureStage.RECORDED) {
                        val previous = pending.receiptCopy()
                        try { secureRequire(result.number("revision") >= SecureJson.parse(previous, 65536, canonical = false).number("revision")) } finally { previous.fill(0) }
                    }
                }
                result.boolean("replayed")
                val terminal = state != "PENDING"
                val owner = stored.owner.copy(stage = if (terminal) SecureStage.TERMINAL else SecureStage.RECORDED, deletionVeto = !terminal)
                secure.publish(ticket.control, requireNotNull(owner.operation), activeBinding = null, terminal = terminal) {
                    listOf(record(ticket.control, ticket.slot, owner, duplicatePending(pending, exactReceipt)))
                }.thenApply { SecurePendingTicket(it, ticket.slot, owner) }.whenComplete { _, _ -> stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }

    private fun record(snapshot: SecureControl, slot: String, owner: SecureRecordOwner, payload: SecurePayload): SecureRecord =
        SecureRecord(slot, SecureStateLimits.next(snapshot.selected.find { it.slot == slot }?.slotRevision ?: 0), UUID.randomUUID().toString(), owner, payload)

    private fun requireSelfSecrets(command: SelfDeviceContractV2.WireRequest, authorization: ByteArray, secrets: PendingSecretContext) {
        val wire = command.canonicalDocumentCopy(); val rendezvous = secrets.rendezvousCopy(); val poll = secrets.pollCopy()
        try {
            val body = SecureJson.parse(wire, 8192)
            if ("rendezvous_secret_sha256" in body) secureRequire(body.string("rendezvous_secret_sha256") == secureSha256(rendezvous))
            if ("poll_secret_sha256" in body) secureRequire(body.string("poll_secret_sha256") == secureSha256(poll))
            if (command.operation == SelfDeviceContractV2.Operation.CLAIM) secureRequire(authorization.contentEquals(rendezvous))
            if (command.operation in setOf(SelfDeviceContractV2.Operation.POLL, SelfDeviceContractV2.Operation.EXCHANGE)) secureRequire(authorization.contentEquals(poll))
        } finally { wire.fill(0); rendezvous.fill(0); poll.fill(0) }
    }
}

/** Clone named fields through the strict local codec; byte ownership never escapes to diagnostics. */
internal fun duplicatePending(payload: PendingPayload, receipt: ByteArray? = null): PendingPayload = payload.duplicate(receipt)
