package app.autplay.desktop.presentation

import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import app.autplay.ui.desktop.*
import java.time.Instant
import kotlinx.serialization.json.*

/** Exact protected receipts and possibly-sent issuance targets; never a server-wide inventory. */
internal class DesktopAccountHostInvitations(private val runtime: DesktopAccountRuntime,
    private val controller: DesktopEnrollmentInvitationController) {
    val canCreate: Boolean get() = PendingWireKind.entries.any { it.name == "ENROLLMENT_INVITATION_CREATE" && it.family == PendingOperation.ENROLLMENT }
    val canCancel: Boolean get() = PendingWireKind.entries.any { it.name == "ENROLLMENT_INVITATION_CANCEL" && it.family == PendingOperation.ENROLLMENT }
    private var handles: Map<String, DesktopInvitationHandle> = emptyMap()
    var entries: List<DesktopInvitationView> = emptyList()
        private set
    fun retire(): Unit { handles = emptyMap(); entries = emptyList() }

    suspend fun create(): DesktopManagedInvitation = controller.create(600, confirmed = true).hostAwait()
    suspend fun resumeCreate(slot: String): DesktopManagedInvitation? = controller.resumeCreate(slot).hostAwait()
    suspend fun resumeCancel(slot: String): Unit = controller.resumeCancel(slot).hostAwait()
    suspend fun cancel(id: String): Unit = controller.cancel(handles[id]
        ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED), confirmed = true).hostAwait()

    suspend fun refresh(bindingCommit: String?): Unit {
        if (bindingCommit == null) { retire(); return }
        val snapshot = runtime.secure.snapshot().hostAwait()
        val created = linkedMapOf<String, Instant?>()
        val cancelled = mutableSetOf<String>()
        snapshot.selected.forEach { reference ->
            val record = runtime.secure.read(reference.slot, snapshot).hostAwait() ?: return@forEach
            record.use {
                val payload = it.payload as? PendingPayload ?: return@use
                if (payload.kind != SecureRecordKind.PROFILE_COMMAND || payload.operation != PendingOperation.ENROLLMENT ||
                    it.owner.bindingCommit != bindingCommit) return@use
                val raw = payload.requestCopy()
                val request = try { SecureJson.parse(raw, 16_384, canonical = false) } finally { raw.fill(0) }
                val create = payload.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CREATE &&
                    request.keys == setOf("contract_version", "schema_version", "operation_id", "expires_in_seconds")
                val cancel = payload.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CANCEL &&
                    request.keys == setOf("contract_version", "schema_version", "operation_id")
                if (!create && !cancel) return@use
                if (request.string("contract_version") != "v1" || request.number("schema_version") != 1L ||
                    request.string("operation_id") != it.owner.operation) changed()
                if (it.owner.stage != SecureStage.TERMINAL) {
                    if (create && it.owner.stage in setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.RECORDED)) {
                        val id = requireNotNull(it.owner.operation)
                        if (id != it.owner.claim) changed()
                        // The retained operation ID is a valid cancellation target, not proof of issuance.
                        created[id] = null
                    }
                    return@use
                }
                val receipt = payload.receiptCopy()
                try {
                    // Explicit cancellation of an unsent PREPARED request has no remote receipt.
                    if (receipt.isEmpty()) return@use
                    val response = SecureJson.parse(receipt, 16_384, canonical = false)
                    if (create && "invitation_id" in response) {
                        val id = response.string("invitation_id")
                        if (id != it.owner.operation || id != it.owner.claim) changed()
                        created[id] = Instant.parse(response.string("expires_at"))
                    } else {
                        response.exact("contract_version", "schema_version", "operation_id", "outcome", "terminal_at")
                        if (response.string("contract_version") != "v1" || response.number("schema_version") != 1L ||
                            response.string("outcome") !in setOf("APPLIED", "ALREADY_TERMINAL")) changed()
                        canonicalUuid(response.string("operation_id"))
                        if (cancel && response.string("operation_id") != it.owner.operation) changed()
                        Instant.parse(response.string("terminal_at"))
                        // Creation may retain the exact positive cancellation receipt after local
                        // reconciliation; that receipt is never used to reconstruct its secret.
                        cancelled.add(if (create) requireNotNull(it.owner.operation) else requireNotNull(it.owner.claim))
                    }
                } finally { receipt.fill(0) }
            }
        }
        // Unknown issuance remains visibly pending. An unauthorized/missing cancellation response
        // cannot prove nonacceptance: creation may still be completing on the server.
        val current = created.filter { (id, expiry) -> id !in cancelled && (expiry == null || expiry > Instant.now()) }
            .entries.take(SecureStateLimits.LOGICAL)
        handles = current.associate { it.key to DesktopInvitationHandle(it.key, bindingCommit) }
        entries = current.map { DesktopInvitationView(it.key, it.value?.toString() ?: "\u2014",
            if (it.value == null) DesktopAccountCondition.Pending else DesktopAccountCondition.Ready) }
    }
    private fun changed(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
