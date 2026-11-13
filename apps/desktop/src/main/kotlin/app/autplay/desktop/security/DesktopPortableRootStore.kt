package app.autplay.desktop.security

import app.autplay.application.portableaccount.*
import app.autplay.application.securestate.*
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.serialization.json.*

/** Lifecycle-owned wrapper; portable ROOT never enters the native device signer. */
public class DesktopUnlockedRoot internal constructor(private val root: UnlockedPortableAccount,
    private val released: (DesktopUnlockedRoot) -> Unit) : AutoCloseable {
    private val retired = java.util.concurrent.atomic.AtomicBoolean(false)
    private val signing = java.util.concurrent.atomic.AtomicBoolean(false)
    public fun signLegacy(purpose: String, canonicalRequest: ByteArray): ByteArray = owned { root.sign(purpose, canonicalRequest) }
    public fun sign(prepared: PortableDeviceContractV2.PreparedRequest): PortableDeviceContractV2.SignedCommand = owned { root.signDeviceContractV2(prepared) }
    private fun <T> owned(block: () -> T): T {
        secureRequire(!retired.get() && signing.compareAndSet(false, true), SecureStateStatus.CLOSED)
        try {
            val result = block()
            if (retired.get()) {
                when (result) { is ByteArray -> result.fill(0); is AutoCloseable -> result.close() }
                throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
            }
            return result
        } finally { signing.set(false) }
    }
    override fun close() { if (retired.compareAndSet(false, true)) { root.close(); released(this) } }
    override fun toString(): String = "DesktopUnlockedRoot(<redacted>)"
}

/** Protected passport, complete recovery archive, cutoff and conservative enrollment history. */
public class DesktopPortableRootStore(private val secure: DesktopSecureStateOwner) {
    private val slot: String = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)

    public fun create(snapshot: SecureControl, owner: SecureRecordOwner, created: CreatedPortableAccount,
        ownershipCutoffMs: Long): CompletableFuture<SecureControl> {
        val recovery = PortableRecoveryFile.encode(created.document, created.recoveryCode)
        val passport = created.document.encode()
        val lineage = PortableAccountLineage(false, emptyList()).encode(created.document).toByteArray(Charsets.UTF_8)
        return try { install(snapshot, owner, PortableRootPayload(passport, recovery, lineage, ownershipCutoffMs, false), newOnly = true) }
        finally { passport.fill(0); recovery.fill(0); lineage.fill(0) }
    }

    /** Imported roots are always conservatively enrolled, even when an external marker says new. */
    public fun importRecovery(snapshot: SecureControl, owner: SecureRecordOwner, recoveryDocument: ByteArray,
        ownershipCutoffMs: Long): CompletableFuture<SecureControl> {
        PortableRecoveryFile.parse(recoveryDocument).use { recovered ->
            val passport = recovered.document.encode()
            val lineage = PortableAccountLineage(true, recovered.predecessors.map { PortableRecoveryPredecessor(it.document, it.code.copyOf()) })
            val history = try { lineage.encode(recovered.document).toByteArray(Charsets.UTF_8) } finally { lineage.close() }
            // Upgrade only the established recovery wrapper; never rewrite its passport/proof bytes.
            val recovery = PortableRecoveryFile.encode(recovered.document, recovered.code, recovered.predecessors)
            return try { install(snapshot, owner, PortableRootPayload(passport, recovery, history, ownershipCutoffMs, true), newOnly = true) }
            finally { passport.fill(0); recovery.fill(0); history.fill(0) }
        }
    }

    public fun unlock(snapshot: SecureControl, password: CharArray): CompletableFuture<DesktopUnlockedRoot> {
        val owned = password.copyOf()
        return secure.read(slot, snapshot).thenApplyAsync { nullable ->
            val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
            try {
                val payload = stored.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
                val bytes = payload.passportCopy()
                val document = try { PortableAccountDocument.parse(bytes) } finally { bytes.fill(0) }
                val unlocked = DesktopUnlockedRoot(PortableAccountCrypto.unlock(document, owned)) { secure.releaseTransient(it) }
                secure.retainTransient(unlocked)
            } finally { stored.close() }
        }.whenComplete { _, _ -> owned.fill('\u0000') }
    }

    /** Only a password-proven, server-supplied passport may initialize an absent local ROOT. */
    public fun adoptFromLogin(snapshot: SecureControl, owner: SecureRecordOwner,
        document: PortableAccountDocument, password: CharArray, ownershipCutoffMs: Long): CompletableFuture<SecureControl> {
        PortableAccountCrypto.unlock(document, password).close()
        val passport = document.encode()
        val history = PortableAccountLineage(true, emptyList()).encode(document).toByteArray(Charsets.UTF_8)
        return try { install(snapshot, owner, PortableRootPayload(passport, byteArrayOf(), history, ownershipCutoffMs, true), newOnly = true) }
        finally { passport.fill(0); history.fill(0) }
    }

    /** Offline rotation is allowed only before any server attempt, without any other owner. */
    public fun rotateOffline(snapshot: SecureControl, owner: SecureRecordOwner,
        replacement: CreatedPortableAccount): CompletableFuture<SecureControl> = secure.read(slot, snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
        try {
            val root = stored.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(!root.everEnrolled && snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty() && snapshot.selected.all { it.slot == slot }, SecureStateStatus.OUTCOME_PENDING)
            val recovery = root.recoveryCopy()
            val payload = try { rotated(root, recovery, replacement, false) } finally { recovery.fill(0) }
            install(snapshot, owner.copy(accountProjection = stored.owner.accountProjection), payload, newOnly = false)
                .whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }

    /** Called after the exact server ROOT-rotation outcome is verified by its protocol owner. */
    public fun commitServerRotation(snapshot: SecureControl, owner: SecureRecordOwner,
        replacement: CreatedPortableAccount, originalRecovery: ByteArray,
        verifiedOperation: (SecureRecordOwner) -> Boolean): CompletableFuture<SecureControl> = secure.read(slot, snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
        try {
            secureRequire(verifiedOperation(owner) && snapshot.reservations.any { it.operation == owner.operation }, SecureStateStatus.OUTCOME_PENDING)
            val root = stored.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            val payload = rotated(root, originalRecovery, replacement, true)
            install(snapshot, owner.copy(accountProjection = stored.owner.accountProjection), payload, newOnly = false, existingReservation = true)
                .whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }

    /** Explicit secure export owns the destination/no-overwrite/readback ceremony outside K. */
    public fun recoveryForExplicitExport(snapshot: SecureControl): CompletableFuture<SecretBytes> = secure.read(slot, snapshot).thenApply { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
        try {
            val root = stored.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(root.hasRecoveryDocument, SecureStateStatus.UNAVAILABLE)
            val bytes = root.recoveryCopy()
            try { secure.transientSecret(bytes) } finally { bytes.fill(0) }
        } finally { stored.close() }
    }
    public fun acknowledgeVerifiedRecoveryExport(snapshot: SecureControl, readback: ByteArray): CompletableFuture<Unit> = secure.read(slot, snapshot).thenApply { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
        try {
            val exact = (stored.payload as? PortableRootPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)).recoveryCopy()
            try { secureRequire(exact.isNotEmpty() && exact.contentEquals(readback), SecureStateStatus.OUTCOME_PENDING) } finally { exact.fill(0) }
            // Acknowledgment never clears predecessor material or conservative enrollment history.
            Unit
        } finally { stored.close() }
    }

    private fun rotated(root: PortableRootPayload, previousRecovery: ByteArray,
        replacement: CreatedPortableAccount, enrolled: Boolean): PortableRootPayload {
        val previousPassport = root.passportCopy(); val retainedRecovery = root.recoveryCopy()
        try {
            val previous = PortableAccountDocument.parse(previousPassport)
            secureRequire(replacement.document.accountId == previous.accountId && replacement.document.publicId == previous.publicId && replacement.document.keyThumbprint != previous.keyThumbprint, SecureStateStatus.CONTEXT_CHANGED)
            PortableRecoveryFile.parse(previousRecovery).use { supplied ->
                PortableRecoveryFile.parse(retainedRecovery).use { retained ->
                    secureRequire(supplied.document.fields == previous.fields && retained.document.fields == previous.fields)
                    val entries = (supplied.predecessors + retained.predecessors + PortableRecoveryPredecessor(previous, supplied.code))
                        .groupBy { it.document.keyThumbprint }.values.map { versions ->
                            val first = versions.first()
                            secureRequire(versions.all { it.document.fields == first.document.fields && it.code.contentEquals(first.code) })
                            PortableRecoveryPredecessor(first.document, first.code.copyOf())
                        }
                    try {
                        secureRequire(entries.size <= 8, SecureStateStatus.CAPACITY_REQUIRED)
                        val passport = replacement.document.encode()
                        val recovery = PortableRecoveryFile.encode(replacement.document, replacement.recoveryCode, entries)
                        val history = PortableAccountLineage(enrolled || root.everEnrolled, entries).encode(replacement.document).toByteArray(Charsets.UTF_8)
                        val localGuard = root.localGuardCopy()
                        try { return PortableRootPayload(passport, recovery, history, root.ownershipCutoffMs, enrolled || root.everEnrolled, localGuard) }
                        finally { passport.fill(0); recovery.fill(0); history.fill(0); localGuard.fill(0) }
                    } finally { entries.forEach { it.close() } }
                }
            }
        } finally { previousPassport.fill(0); retainedRecovery.fill(0) }
    }

    private fun install(snapshot: SecureControl, owner: SecureRecordOwner, payload: PortableRootPayload,
        newOnly: Boolean, existingReservation: Boolean = false): CompletableFuture<SecureControl> {
        secure.retainTransient(payload)
        try {
        secureRequire(owner.role == SecureRole.LOCAL && owner.boot == secure.boot && owner.authorityRevision == snapshot.authorityRevision)
        val operation = requireNotNull(owner.operation)
        val passport = payload.passportCopy()
        try { secureRequire(PortableAccountDocument.parse(passport).accountId == owner.account) } finally { passport.fill(0) }
        secureRequire(!newOnly || (snapshot.selected.none { it.slot == slot } && snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty()), SecureStateStatus.CONTEXT_CHANGED)
        val admitted = if (existingReservation) CompletableFuture.completedFuture(snapshot) else
            secure.reserveCompletion(snapshot, CompletionReservation(operation, listOf(slot), 1, SecureStateLimits.VALUE.toLong()))
        return admitted.thenCompose { current ->
            secure.publish(current, operation, terminal = !existingReservation) {
                val bytes = payload.passportCopy(); val recovery = payload.recoveryCopy(); val history = payload.lineageCopy(); val localGuard = payload.localGuardCopy()
                try { listOf(SecureRecord(slot, SecureStateLimits.next(current.selected.find { it.slot == slot }?.slotRevision ?: 0), UUID.randomUUID().toString(), owner,
                    PortableRootPayload(bytes, recovery, history, payload.ownershipCutoffMs, payload.everEnrolled, localGuard))) }
                finally { bytes.fill(0); recovery.fill(0); history.fill(0); localGuard.fill(0) }
            }
        }.whenComplete { _, _ -> payload.close(); secure.releaseTransient(payload) }
        } catch (failure: Exception) { payload.close(); secure.releaseTransient(payload); throw failure }
    }
}
