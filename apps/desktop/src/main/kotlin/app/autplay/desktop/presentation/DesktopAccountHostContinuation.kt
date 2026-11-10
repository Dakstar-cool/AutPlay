package app.autplay.desktop.presentation

import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import java.util.UUID
import kotlinx.serialization.json.*

/** Dispatches only the retained original; a view ID can never choose a new protocol operation. */
internal class DesktopAccountHostContinuation(private val runtime: DesktopAccountRuntime,
    private val profiles: DesktopProfileController, private val admission: DesktopAdmissionController,
    private val self: DesktopSelfPairingController, private val recovery: DesktopServerRecoveryController,
    private val portable: DesktopPortableRecoveryController, private val invitations: DesktopAccountHostInvitations) {
    suspend fun resume(slot: String, target: String): DesktopManagedInvitation? {
        val snapshot = runtime.secure.snapshot().hostAwait()
        val record = runtime.secure.read(slot, snapshot).hostAwait() ?: changed()
        val operation: PendingOperation; val wire: PendingWireKind; val role: SecureRole
        val stage: SecureStage
        record.use {
            if (it.owner.operation != target || it.owner.stage == SecureStage.TERMINAL) changed()
            runtime.requireTarget(it.owner, runtime.selected ?: changed())
            val payload = it.payload as? PendingPayload ?: changed()
            operation = payload.operation; wire = payload.wireKind; role = it.owner.role; stage = it.owner.stage
        }
        when (operation) {
            PendingOperation.CONSENT, PendingOperation.VISIBILITY, PendingOperation.LOGOUT_CURRENT,
            PendingOperation.LOGOUT_ALL, PendingOperation.REVOKE -> profiles.resumeProtectedCommand(slot).hostAwait()
            PendingOperation.PORTABLE_ROTATE -> portable.resumeRotation().hostAwait()
            PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE -> admission.restoreProtectedRequest(slot).hostAwait()
            PendingOperation.SELF -> self.restoreProtectedCeremony(role == SecureRole.SOURCE).hostAwait()
            PendingOperation.RECOVERY -> when (wire) {
                PendingWireKind.RECOVERY_CONFIGURE -> recovery.resumeSourceConfiguration().hostAwait()
                PendingWireKind.RECOVERY_PREVIEW -> {
                    if (stage != SecureStage.RECORDED) replayPreview(slot, false)
                    recovery.restoreRecipientPreview(false).hostAwait()
                }
                else -> runtime.resumePending(slot).hostAwait()
            }
            PendingOperation.DELETION -> when {
                wire == PendingWireKind.DELETION_PREVIEW -> {
                    if (stage != SecureStage.RECORDED) replayPreview(slot, true)
                    recovery.restoreRecipientPreview(true).hostAwait()
                }
                role == SecureRole.SOURCE && stage == SecureStage.PREPARED -> replayDeletionRequest(slot)
                role == SecureRole.SOURCE -> recovery.resolveDeletionOutcome().hostAwait()
                else -> runtime.resumePending(slot).hostAwait()
            }
            PendingOperation.ENROLLMENT -> when (wire) {
                PendingWireKind.ENROLLMENT_INVITATION_CREATE -> return invitations.resumeCreate(slot)
                PendingWireKind.ENROLLMENT_INVITATION_CANCEL -> invitations.resumeCancel(slot)
                else -> runtime.resumePending(slot).hostAwait()
            }
            PendingOperation.PORTABLE_REGISTER, PendingOperation.PORTABLE_LOGIN, PendingOperation.ROTATION -> runtime.resumePending(slot).hostAwait()
            else -> throw AccountControlException(AccountControlFailure.UNSUPPORTED)
        }
        return null
    }

    private suspend fun replayPreview(slot: String, deletion: Boolean): Unit {
        runtime.command {
            runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: changed()
                try {
                    runtime.requireTarget(stored.owner, runtime.selected ?: changed())
                    val payload = stored.payload as? PendingPayload ?: changed()
                    if (payload.wireKind != (if (deletion) PendingWireKind.DELETION_PREVIEW else PendingWireKind.RECOVERY_PREVIEW)) changed()
                    runtime.resumeProtected(snapshot, slot, stored.owner.copy(boot = runtime.secure.boot,
                        authorityRevision = snapshot.authorityRevision))
                } finally { stored.close() }
            } }.thenCompose { ticket -> runtime.executePending(ticket,
                if (deletion) AccountEndpoint.DELETION_PREVIEW else AccountEndpoint.RECOVERY_PREVIEW).thenCompose { result ->
                val bytes = result.response.copy()
                try {
                    val response = SecureJson.parse(bytes, 65_536, canonical = false)
                    if (response.string("contract_version") != "v2" || response.number("schema_version") != 2L ||
                        response.string("platform") != "MACOS" || response.string("account_id") != ticket.owner.account ||
                        !response.boolean("confirmation_required") || response.number("code_generation") < 1) changed()
                    DesktopAccountTrust.display(response.string("account_label"), 200)
                    if (!deletion && (response.string("operation_id") != ticket.owner.operation ||
                        response.string("server_instance_id") != ticket.owner.server || response.number("identity_epoch") != ticket.owner.identityEpoch)) changed()
                    val receipt = runtime.secure.transientSecret(bytes)
                    runtime.secure.read(slot, result.ticket.control).thenCompose { nullable ->
                        val stored = nullable ?: changed()
                        val raw = receipt.copy()
                        val frozen = try { (stored.payload as PendingPayload).duplicate(raw) } finally { raw.fill(0) }
                        try {
                            val owner = stored.owner.copy(stage = SecureStage.RECORDED)
                            runtime.secure.publish(result.ticket.control, requireNotNull(owner.operation)) {
                                listOf(SecureRecord(slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), owner,
                                    frozen.duplicate()))
                            }.thenApply { Unit }.whenComplete { _, _ -> frozen.close(); stored.close() }
                        } catch (error: Exception) { frozen.close(); stored.close(); throw error }
                    }.whenComplete { _, _ -> receipt.close() }
                } finally { bytes.fill(0); result.close() }
            } }
        }.hostAwait()
    }

    private suspend fun replayDeletionRequest(slot: String): Unit {
        runtime.command {
            runtime.secure.snapshot().thenCompose { snapshot -> runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: changed()
                try {
                    if ((stored.payload as? PendingPayload)?.wireKind != PendingWireKind.DELETION_REQUEST ||
                        stored.owner.stage != SecureStage.PREPARED || stored.owner.role != SecureRole.SOURCE) changed()
                    runtime.requireTarget(stored.owner, runtime.selected ?: changed())
                    runtime.resumeProtected(snapshot, slot, stored.owner.copy(boot = runtime.secure.boot,
                        authorityRevision = snapshot.authorityRevision))
                } finally { stored.close() }
            } }.thenCompose { ticket -> runtime.executePending(ticket, AccountEndpoint.DELETION_REQUEST).thenCompose { result ->
                val bytes = result.response.copy()
                try { runtime.recordDeletionOutcome(result.ticket, bytes).thenApply { Unit } }
                finally { bytes.fill(0); result.close() }
            } }
        }.hostAwait()
    }
    private fun changed(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
