package app.autplay.desktop.security

import app.autplay.application.securestate.*
import java.util.UUID
import java.util.concurrent.CompletableFuture

/** Real shared K/J/media authority gate. Quiescence leaves this K writer alive until settlement. */
public interface ProtectedMediaPublicationGate {
    public fun quiesce(owner: SecureRecordOwner): CompletableFuture<Unit>
    public fun quiesceConnection(owner: SecureRecordOwner): CompletableFuture<Unit> = quiesce(owner)
    public fun confirmLocalTransition(owner: SecureRecordOwner, previous: LocalUseGuard?, next: LocalUseGuard): CompletableFuture<Unit>
    public fun commitLocalProjection(publication: ProtectedLocalPublication): CompletableFuture<Unit>
    public fun localProjectionMatches(publication: ProtectedLocalPublication): CompletableFuture<Boolean>
    /** Must verify current M5/D1/B1 evidence and explicit transport re-trust; never a J/snapshot DTO. */
    public fun confirmConnectionGuards(owner: SecureRecordOwner, d1: CapabilityGuard,
        m5: M5CapabilityGuard?, transport: ProtectedTransportPolicy): CompletableFuture<Unit>
}
public class ProtectedLocalPublication(public val owner: SecureRecordOwner,
    public val value: SecureValueReference, public val publicationCommit: String,
    public val controlRevision: Long, public val authorityRevision: Long, public val guard: LocalUseGuard) {
    override fun toString(): String = "ProtectedLocalPublication(<redacted>)"
}

/** M1 subrecords stay in the existing ROOT/binding owner; no guarded media catalog or new slot. */
public class DesktopProtectedMediaState(private val secure: DesktopSecureStateOwner,
    private val gate: ProtectedMediaPublicationGate,
    private val account: SecureBindingProjection) {
    public fun local(snapshot: SecureControl, slot: String): CompletableFuture<LocalUseGuard> = secure.localUseGuard(snapshot, slot)

    public fun publishLocal(snapshot: SecureControl, slot: String, expected: LocalUseGuard?,
        next: LocalUseGuard): CompletableFuture<SecureControl> = secure.read(slot, snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
        try {
            secureRequire(next.state in setOf("AVAILABLE", "LOCKED", "FORGOTTEN") && next.veto in setOf("NONE", "DEVICE_REVOKED", "ACCOUNT_REVOKED", "DELETION_PENDING", "DELETED"))
            val previousBytes = localBytes(stored.payload)
            val previous = try { if (previousBytes.isEmpty()) null else SecureMediaGuardCodec.readLocal(previousBytes) } finally { previousBytes.fill(0) }
            secureRequire(previous == expected && next.revision == SecureStateLimits.next(previous?.revision ?: 0), SecureStateStatus.CONTEXT_CHANGED)
            val authority = SecureStateLimits.next(snapshot.authorityRevision)
            val owner = stored.owner.copy(boot = secure.boot, authorityRevision = authority,
                stage = if (stored.payload is SessionPayload && !stored.owner.detached && next.available)
                    SecureStage.BINDING_COMMIT_PENDING else stored.owner.stage,
                accountProjection = stored.owner.accountProjection?.copy(lifecycleAllowed = false, currentAuthorityRevision = authority))
            val operation = UUID.randomUUID().toString(); val transaction = UUID.randomUUID().toString()
            gate.quiesce(stored.owner).thenCompose { gate.confirmLocalTransition(stored.owner, previous, next) }.thenCompose {
                secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot), 1, SecureStateLimits.VALUE.toLong()))
            }.thenCompose { reserved ->
                secure.publish(reserved, operation, activeBinding = null, terminal = true, authorityRevision = authority) {
                    val bytes = SecureMediaGuardCodec.local(next)
                    try {
                        val payload = when (val value = stored.payload) {
                            is PortableRootPayload -> value.withLocalGuard(bytes)
                            is SessionPayload -> value.withLocalGuard(bytes)
                            else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
                        }
                        listOf(SecureRecord(slot, SecureStateLimits.next(stored.revision), transaction, owner, payload))
                    } finally { bytes.fill(0) }
                }
            }.thenCompose { committed ->
                val publication = ProtectedLocalPublication(owner, committed.selected.single { it.slot == slot }, transaction, committed.revision, authority, next)
                gate.commitLocalProjection(publication).thenCompose { gate.localProjectionMatches(publication) }.thenCompose { matched ->
                    secureRequire(matched, SecureStateStatus.OUTCOME_PENDING); secure.snapshot()
                }
            }.whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }

    public fun publishConnection(snapshot: SecureControl, slot: String, d1: CapabilityGuard,
        m5: M5CapabilityGuard?, policy: ProtectedTransportPolicy): CompletableFuture<SecureControl> = secure.read(slot, snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
        try {
            val payload = stored.payload as? SessionPayload ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
            secureRequire(policy.known && payload.d1.accepts(d1), SecureStateStatus.CONTEXT_CHANGED)
            val oldM5 = payload.m5
            secureRequire(oldM5 == null || m5 != null && oldM5.accepts(m5), SecureStateStatus.CONTEXT_CHANGED)
            val oldBytes = payload.transportPolicyCopy(); val newBytes = SecureMediaGuardCodec.transport(policy)
            val policyChanged = try { !oldBytes.contentEquals(newBytes) } finally { newBytes.fill(0) }
            try {
                if (policyChanged) secureRequire(policy.revision == SecureStateLimits.next(if (oldBytes.isEmpty()) 0 else SecureMediaGuardCodec.readTransport(oldBytes).revision), SecureStateStatus.CONTEXT_CHANGED)
            } finally { oldBytes.fill(0) }
            val operation = UUID.randomUUID().toString()
            val guardChanged = policyChanged || d1 != payload.d1 || m5 != payload.m5
            val authority = if (guardChanged) SecureStateLimits.next(snapshot.authorityRevision) else snapshot.authorityRevision
            val owner = stored.owner.copy(boot = secure.boot, authorityRevision = authority,
                stage = if (guardChanged) SecureStage.BINDING_COMMIT_PENDING else stored.owner.stage,
                accountProjection = if (guardChanged) stored.owner.accountProjection?.copy(lifecycleAllowed = false,
                    capabilityRevision = d1.revision, currentAuthorityRevision = authority) else stored.owner.accountProjection)
            val quiescence = if (guardChanged) gate.quiesceConnection(stored.owner) else CompletableFuture.completedFuture(Unit)
            quiescence.thenCompose { gate.confirmConnectionGuards(stored.owner, d1, m5, policy) }.thenCompose {
                secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot), 1, SecureStateLimits.VALUE.toLong()))
            }.thenCompose { reserved ->
                secure.publish(reserved, operation, activeBinding = if (guardChanged) null else reserved.activeBinding, terminal = true, authorityRevision = authority) {
                    val transport = SecureMediaGuardCodec.transport(policy)
                    try { listOf(SecureRecord(slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), owner, payload.withConnectionGuards(d1, m5, transport))) }
                    finally { transport.fill(0) }
                }
            }.thenCompose { published ->
                if (!guardChanged || owner.accountProjection == null) CompletableFuture.completedFuture(published)
                else account.reconcileGuards(owner).thenCompose { secure.snapshot() }
            }.whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }

    /** Only after a durable remote inactive fence; LOCAL owner/guard and pending evidence survive. */
    public fun clearInactiveCredentials(snapshot: SecureControl, slot: String): CompletableFuture<SecureControl> = secure.read(slot, snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        try {
            secureRequire(snapshot.activeBinding != slot, SecureStateStatus.CONTEXT_CHANGED)
            val payload = stored.payload as? SessionPayload ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
            val operation = UUID.randomUUID().toString()
            secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot), 1, SecureStateLimits.VALUE.toLong())).thenCompose { reserved ->
                secure.publish(reserved, operation, terminal = true) {
                    listOf(SecureRecord(slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(),
                        stored.owner.copy(boot = secure.boot, authorityRevision = reserved.authorityRevision, stage = SecureStage.INACTIVE, detached = true), payload.withoutCredentials()))
                }
            }.whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }
    private fun localBytes(payload: SecurePayload): ByteArray = when (payload) {
        is PortableRootPayload -> payload.localGuardCopy()
        is SessionPayload -> payload.localGuardCopy()
        else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
    }
}
