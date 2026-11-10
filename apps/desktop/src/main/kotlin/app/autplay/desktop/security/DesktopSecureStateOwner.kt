package app.autplay.desktop.security

import app.autplay.application.securestate.*
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/** Process-only evidence produced by the protocol custodian after a positive continuation. */
public class SecurePendingHandoff internal constructor(public val slot: String,
    public val previousValueId: String, public val previousOperation: String,
    public val nextOperation: String, public val receiptSha256: String) {
    override fun toString(): String = "SecurePendingHandoff(<redacted>)"
}
public enum class SecurePendingDispatchMode { ORIGINAL, HISTORICAL_OUTCOME }

/**
 * One lifetime writer; all port calls, including close, run on its single native executor.
 * A caller deadline retires admissions/results, never cancels or interrupts a native mutation.
 * Construct only once per DesktopRuntime. Opening does not initialize an absent namespace.
 */
public class DesktopSecureStateOwner(private val native: SecureNativePort,
    private val now: () -> java.time.Instant = java.time.Instant::now) {
    public val boot: String = UUID.randomUUID().toString()
    private val generation: AtomicLong = AtomicLong(1)
    public val admissionGeneration: Long get() = generation.get()
    public fun requireAdmission(captured: Long): Unit = secureRequire(admissions.get() && !closing.get() && generation.get() == captured, SecureStateStatus.CONTEXT_CHANGED)
    private val admissions: AtomicBoolean = AtomicBoolean(true)
    private val closing: AtomicBoolean = AtomicBoolean(false)
    private val closeQueued: AtomicBoolean = AtomicBoolean(false)
    private val payloadAdmitted: AtomicBoolean = AtomicBoolean(false)
    private val gate: Any = Any()
    internal val accountGate: SecureAuthorityGate = SecureAuthorityGate()
    private val accountLifetimeHeld: AtomicBoolean = AtomicBoolean(false)
    @Volatile private var nativeThread: Thread? = null
    private val executor: ThreadPoolExecutor = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(8), { task -> Thread(task, "autplay-secure-state").apply { isDaemon = false }.also { nativeThread = it } }, ThreadPoolExecutor.AbortPolicy())
    private val deadlines = Executors.newSingleThreadScheduledExecutor { task -> Thread(task, "autplay-secure-deadline").apply { isDaemon = true } }
    private val transients: MutableSet<AutoCloseable> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    @Volatile private var handle: Long = 0
    private var blocked: Boolean = false
    private val drained: CompletableFuture<Unit> = CompletableFuture()

    public fun open(): CompletableFuture<SecureControl?> = operation {
        secureRequire(handle == 0L, SecureStateStatus.CONTEXT_CHANGED)
        handle = native.open()
        val inventory = inventory()
        val bytes = native.readControl(handle)
        if (bytes == null) {
            secureRequire(!inventory.hasControl && inventory.values.isEmpty() && inventory.keyTags.isEmpty())
            null
        } else try { val control = SecureControlCodec.decode(bytes); reconcile(control, inventory); control } finally { bytes.fill(0) }
    }

    /** Explicit product command; caller must establish absence of existing J/account markers. */
    public fun initializeEmpty(existingAccountMarkers: Boolean): CompletableFuture<SecureControl> = operation {
        secureRequire(!existingAccountMarkers, SecureStateStatus.CONTEXT_CHANGED)
        val inventory = inventory()
        secureRequire(!inventory.hasControl && inventory.values.isEmpty() && inventory.keyTags.isEmpty())
        val control = SecureControl(0, 0, emptyList(), null, null, emptyList(), emptyList(), emptyList())
        val bytes = SecureControlCodec.encode(control)
        try {
            uncertain { native.initializeControl(handle, bytes) }
            exactControl(bytes)
            control
        } finally { bytes.fill(0) }
    }

    public fun snapshot(): CompletableFuture<SecureControl> = operation { reload() }

    /** Caller holds the canonical K lifetime lock and has positively inventoried the J namespace. */
    internal fun reserveAccountInstallation(expected: SecureControl, namespaceVirgin: Boolean): CompletableFuture<SecureControl> = operation {
        val current = requireSnapshot(expected)
        secureRequire(namespaceVirgin && current.revision == 0L && current.authorityRevision == 0L &&
            current.jInstallation == null && current.selected.isEmpty() && current.cleanup.isEmpty() &&
            current.keys.isEmpty() && current.reservations.isEmpty() && current.firstBind == null && current.activeBinding == null,
            SecureStateStatus.OUTCOME_PENDING)
        val inventory = inventory()
        secureRequire(inventory.values.isEmpty() && inventory.keyTags.isEmpty(), SecureStateStatus.OUTCOME_PENDING)
        publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), authorityRevision = 1,
            jInstallation = SecureJInstallation(UUID.randomUUID().toString(), UUID.randomUUID().toString(), SecureJInstallationStage.RESERVED)))
    }

    internal fun completeAccountInstallation(expected: SecureControl, storeId: String): CompletableFuture<SecureControl> = operation {
        val current = requireSnapshot(expected)
        val installation = current.jInstallation ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
        secureRequire(installation.storeId == storeId, SecureStateStatus.CONTEXT_CHANGED)
        if (installation.stage == SecureJInstallationStage.READY) current else publishControl(current,
            current.copy(revision = SecureStateLimits.next(current.revision),
                jInstallation = installation.copy(stage = SecureJInstallationStage.READY)))
    }

    /** Pins only the existing native flock; it establishes no store association or witness. */
    internal fun pinAccountLifetime(permit: SecureAuthorityGate.Permit): Boolean {
        accountGate.requireCurrent(permit)
        requireAdmission(admissionGeneration)
        secureRequire(handle != 0L, SecureStateStatus.CLOSED)
        return accountLifetimeHeld.compareAndSet(false, true)
    }
    internal fun releaseAccountLifetime(): Unit {
        secureRequire(accountLifetimeHeld.compareAndSet(true, false), SecureStateStatus.CONTEXT_CHANGED)
        if (closing.get()) accountGate.retire { queueClose() }
    }
    internal fun isNativeOwnerThread(): Boolean = Thread.currentThread() === nativeThread

    /** Fresh protected read is mandatory for every new credential/signing admission. */
    public fun read(slot: String, expected: SecureControl): CompletableFuture<SecureRecord?> = operation {
        val current = requireSnapshot(expected)
        current.selected.find { it.slot == slot }?.let { reference ->
            val decoded = read(reference)
            lateinit var tracked: SecureRecord
            tracked = SecureRecord(decoded.slot, decoded.revision, decoded.transaction, decoded.owner, decoded.payload) { releaseTransient(tracked) }
            retainTransient(tracked)
        }
    }

    public fun reserveCompletion(expected: SecureControl, reservation: CompletionReservation,
        firstBind: FirstBindOwner? = null): CompletableFuture<SecureControl> = operation {
        secureRequire(reservation.revisions > 0 && reservation.bytes >= 18, SecureStateStatus.CAPACITY_REQUIRED)
        val current = requireSnapshot(expected)
        val old = current.reservations.find { it.operation == reservation.operation }
        if (old != null) {
            secureRequire(reservation.slots.containsAll(old.slots) && reservation.revisions >= old.revisions && reservation.bytes >= old.bytes && (firstBind == null || current.firstBind == firstBind), SecureStateStatus.CONTEXT_CHANGED)
            if (old == reservation) current else {
                val extended = current.copy(revision = SecureStateLimits.next(current.revision), reservations = current.reservations.map { if (it.operation == old.operation) reservation else it })
                capacity(extended, inventory()); publishControl(current, extended)
            }
        }
        else {
            secureRequire(firstBind == null || current.firstBind == null || current.firstBind == firstBind, SecureStateStatus.OUTCOME_PENDING)
            val next = current.copy(revision = SecureStateLimits.next(current.revision), reservations = current.reservations + reservation, firstBind = firstBind ?: current.firstBind)
            capacity(next, inventory())
            publishControl(current, next)
        }
    }

    /** Explicit cancellation before a protocol journal was ever selected; not remote rollback. */
    public fun retireUnusedReservation(expected: SecureControl, operationId: String): CompletableFuture<SecureControl> = operation {
        val current = requireSnapshot(expected)
        secureRequire(current.reservations.any { it.operation == operationId }, SecureStateStatus.CONTEXT_CHANGED)
        current.selected.forEach { reference -> read(reference).use { secureRequire(it.owner.operation != operationId, SecureStateStatus.OUTCOME_PENDING) } }
        publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision),
            reservations = current.reservations.filterNot { it.operation == operationId },
            firstBind = if (current.firstBind?.operation == operationId) null else current.firstBind,
            keys = current.keys.map { if (it.ownerOperation == operationId) it.copy(state = DeviceKeyState.RETIRED) else it }))
    }

    /**
     * Factory runs after admission, on the owner. Busy payload work is refused before factory
     * invocation; queued descriptors never retain records or request/response bodies.
     * Completion reservation remains charged until an explicit terminal publication.
     */
    public fun publish(expected: SecureControl, operationId: String, activeBinding: String? = expected.activeBinding,
        terminal: Boolean = false, authorityRevision: Long = expected.authorityRevision,
        handoff: SecurePendingHandoff? = null,
        records: () -> List<SecureRecord>): CompletableFuture<SecureControl> = payloadOperation {
        val current = requireSnapshot(expected)
        secureRequire(authorityRevision == current.authorityRevision || authorityRevision == SecureStateLimits.next(current.authorityRevision), SecureStateStatus.CONTEXT_CHANGED)
        secureRequire(authorityRevision == current.authorityRevision || activeBinding == null, SecureStateStatus.CONTEXT_CHANGED)
        val reservation = current.reservations.singleOrNull { it.operation == operationId }
            ?: throw SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED)
        val produced = records()
        try {
            secureRequire(produced.size in 1..4 && produced.map { it.slot }.distinct().size == produced.size)
            val transaction = produced.first().transaction
            secureRequire(handoff == null || !terminal && handoff.previousOperation == operationId && produced.any { it.slot == handoff.slot && it.owner.operation == handoff.nextOperation }, SecureStateStatus.CONTEXT_CHANGED)
            secureRequire(produced.all { it.slot in reservation.slots && it.owner.authorityRevision == authorityRevision && it.owner.boot == boot }, SecureStateStatus.CONTEXT_CHANGED)
            val staged = ArrayList<Pair<SecureValueReference, ByteArray>>()
            try {
                produced.forEach { record ->
                    record.owner.accountProjection?.let { witness ->
                        secureRequire(current.jInstallation?.let { installation ->
                            installation.stage == SecureJInstallationStage.READY &&
                                installation.storeId == witness.storeId.value
                        } == true, SecureStateStatus.OUTCOME_PENDING)
                        if (witness.lifecycleAllowed && (record.payload is SessionPayload || record.payload is PortableRootPayload))
                            SecureAccountProjectionCodec.requireOwner(witness, record.owner, record.payload)
                    }
                    val previous = current.selected.find { it.slot == record.slot }
                    secureRequire(record.revision == SecureStateLimits.next(previous?.slotRevision ?: 0), SecureStateStatus.CONTEXT_CHANGED)
                    if (previous != null) read(previous).use { before -> requireTransition(before, record, current, handoff) }
                    val framed = if (record.transaction == transaction) record else SecureRecord(record.slot, record.revision, transaction, record.owner, record.payload)
                    val bytes = SecureRecordCodec.encode(framed)
                    staged.add(SecureValueReference(record.slot, UUID.randomUUID().toString(), bytes.size, secureSha256(bytes), record.revision) to bytes)
                }
                val stagedBytes = staged.sumOf { it.second.size.toLong() }
                secureRequire(stagedBytes <= reservation.bytes && staged.size <= reservation.revisions, SecureStateStatus.CAPACITY_REQUIRED)
                val remainingRevisions = reservation.revisions - staged.size
                val remainingBytes = if (remainingRevisions == 0) 0 else reservation.bytes - stagedBytes
                secureRequire(remainingRevisions == 0 || remainingBytes >= 18, SecureStateStatus.CAPACITY_REQUIRED)
                val remaining = reservation.copy(revisions = remainingRevisions, bytes = remainingBytes)
                // Atomically convert reserved capacity into exact charged staging references.
                // Missing staged rows remain charged after a crash; future completion stays reserved.
                val intent = current.copy(revision = SecureStateLimits.next(current.revision), cleanup = current.cleanup + staged.map { it.first },
                    reservations = current.reservations.map { if (it.operation == operationId) remaining else it })
                capacity(intent, inventory(), staged.map { it.first })
                val durableIntent = publishControl(current, intent)
                staged.forEach { (reference, bytes) ->
                    uncertain { native.stageValue(handle, reference.valueId, bytes) }
                    exactValue(reference, bytes)
                }
                val changed = produced.map { it.slot }.toSet()
                val settledOperations = if (terminal) produced.filter { it.payload is PendingPayload && it.owner.stage == SecureStage.TERMINAL }
                    .mapNotNull { it.owner.operation }.toSet() + operationId else emptySet()
                val old = durableIntent.selected.filter { it.slot in changed }
                val next = durableIntent.copy(revision = SecureStateLimits.next(durableIntent.revision),
                    authorityRevision = authorityRevision,
                    selected = durableIntent.selected.filterNot { it.slot in changed } + staged.map { it.first }, activeBinding = activeBinding,
                    cleanup = durableIntent.cleanup.filterNot { it.valueId in staged.map { pair -> pair.first.valueId } } + old,
                    reservations = if (terminal) durableIntent.reservations.filterNot { it.operation in settledOperations } else if (handoff != null) durableIntent.reservations.map { if (it.operation == operationId) it.copy(operation = handoff.nextOperation) else it } else durableIntent.reservations,
                    firstBind = if (terminal && durableIntent.firstBind?.operation == operationId) null else if (handoff != null && durableIntent.firstBind?.operation == operationId) durableIntent.firstBind?.copy(operation = handoff.nextOperation) else durableIntent.firstBind)
                if (activeBinding != null) {
                    val selected = produced.find { it.slot == activeBinding }
                    if (selected != null) secureRequire(selected.payload is SessionPayload && selected.owner.stage == SecureStage.ACTIVE && !selected.owner.deletionVeto && !selected.owner.detached &&
                        selected.owner.accountProjection?.stage == app.autplay.application.desktop.account.AccountProtectedStage.READY &&
                        selected.owner.accountProjection?.lifecycleAllowed == true)
                    val account = selected?.owner?.account ?: current.selected.find { it.slot == activeBinding }?.let { read(it).use { record -> record.owner.account } }
                    produced.filter { it.owner.account == account }.forEach { secureRequire(!it.owner.deletionVeto, SecureStateStatus.OUTCOME_PENDING) }
                    current.selected.filter { it.slot !in changed }.forEach { reference -> read(reference).use { record ->
                        secureRequire(record.owner.account != account || !record.owner.deletionVeto, SecureStateStatus.OUTCOME_PENDING)
                    } }
                }
                capacity(next, inventory())
                publishControl(durableIntent, next)
            } finally { staged.forEach { it.second.fill(0) } }
        } finally { produced.forEach { it.close() } }
    }

    /** Detach is durable before any credential cleanup. It retains every unresolved journal. */
    public fun detach(expected: SecureControl): CompletableFuture<SecureControl> = operation {
        val current = requireSnapshot(expected)
        val detached = publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), authorityRevision = SecureStateLimits.next(current.authorityRevision), activeBinding = null))
        admissions.set(false)
        transients.forEach { runCatching { it.close() } }; transients.clear()
        detached
    }

    public fun createNewKey(expected: SecureControl, operationId: String): CompletableFuture<DeviceKeyIntent> = operation {
        var current = requireSnapshot(expected)
        secureRequire(current.reservations.any { it.operation == operationId }, SecureStateStatus.CAPACITY_REQUIRED)
        var intent = current.keys.singleOrNull { it.ownerOperation == operationId && it.state != DeviceKeyState.RETIRED }
        if (intent == null) {
            secureRequire(current.keys.size < 32, SecureStateStatus.CAPACITY_REQUIRED)
            intent = DeviceKeyIntent("app.autplay.desktop.device-key.v1.${UUID.randomUUID()}", operationId, DeviceKeyState.INTENT, null)
            current = publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), keys = current.keys + intent))
        }
        var selected = requireNotNull(intent)
        val key = if (selected.state == DeviceKeyState.INTENT) {
            selected = selected.copy(state = DeviceKeyState.CREATING)
            current = publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), keys = current.keys.map { if (it.tag == selected.tag) selected else it }))
            uncertain { native.createNewDeviceKey(handle, selected.tag) }
        } else native.publicDeviceKey(handle, selected.tag) // CREATING after restart is lookup-only.
        try {
            DeviceSignatureCodec.validateSpki(key)
            val thumbprint = secureSha256(key)
            secureRequire(selected.thumbprint == null || selected.thumbprint == thumbprint, SecureStateStatus.KEY_MISSING)
            val bound = selected.copy(state = DeviceKeyState.BOUND, thumbprint = thumbprint)
            if (selected != bound) publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), keys = current.keys.map { if (it.tag == selected.tag) bound else it }))
            bound
        } finally { key.fill(0) }
    }

    public fun publicKey(expected: SecureControl, tag: String): CompletableFuture<ByteArray> = operation {
        val current = requireSnapshot(expected); readBoundPublicKey(current, tag)
    }

    /** Protected LOCAL owner inspection deliberately has no session/device/key/expiry admission. */
    public fun localUseGuard(expected: SecureControl, slot: String): CompletableFuture<LocalUseGuard> = operation {
        val current = requireSnapshot(expected)
        val reference = current.selected.singleOrNull { it.slot == slot } ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        read(reference).use { record ->
            val bytes = when (val payload = record.payload) {
                is PortableRootPayload -> payload.localGuardCopy()
                is SessionPayload -> payload.localGuardCopy()
                else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
            }
            try { secureRequire(bytes.isNotEmpty(), SecureStateStatus.OUTCOME_PENDING); SecureMediaGuardCodec.readLocal(bytes) }
            finally { bytes.fill(0) }
        }
    }

    /** Exact retained original, never the highest orphan revision. Caller closes the secret owner. */
    public fun readRetained(expected: SecureControl, reference: SecureValueReference): CompletableFuture<SecureRecord> = operation {
        val current = requireSnapshot(expected)
        secureRequire(reference in current.cleanup || reference in current.selected, SecureStateStatus.CONTEXT_CHANGED)
        val decoded = read(reference)
        lateinit var tracked: SecureRecord
        tracked = SecureRecord(decoded.slot, decoded.revision, decoded.transaction, decoded.owner, decoded.payload) { releaseTransient(tracked) }
        retainTransient(tracked)
    }

    /** Exact pending owner, current protected access and existing key are checked before HTTP. */
    public fun admitPending(expected: SecureControl, slot: String, target: SecureRecordOwner,
        mode: SecurePendingDispatchMode): CompletableFuture<Unit> = operation {
        val current = requireSnapshot(expected)
        val reference = current.selected.singleOrNull { it.slot == slot } ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        read(reference).use { record ->
            val pending = record.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(record.owner == target && target.boot == boot && target.authorityRevision == current.authorityRevision && !target.detached && pending.operation != PendingOperation.PUBLIC_REGISTRATION, SecureStateStatus.CONTEXT_CHANGED)
            secureRequire(target.stage in setOf(SecureStage.PREPARED, SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING) || target.stage == SecureStage.RECORDED && mode == SecurePendingDispatchMode.HISTORICAL_OUTCOME, SecureStateStatus.OUTCOME_PENDING)
            val completion = current.reservations.singleOrNull { it.operation == target.operation } ?: throw SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED)
            // J2 additionally publishes the two typed witness revisions before final K selection.
            // Current typed constructors bound each such value below 256 KiB; the public 1 MiB
            // storage limit is unchanged. Reserve these bytes/counts before any HTTP dispatch.
            val required = if (pending.wireKind == PendingWireKind.DELETION_CANCEL && current.selected.any {
                    it.slot == SecureRecordKind.DELETION_SOURCE.reserved }) 8
                else if (pending.wireKind in app.autplay.application.desktop.account.AccountBindingIntentCodec.GRANTS ||
                    pending.wireKind == PendingWireKind.SESSION_ROTATE) 7 else 5
            secureRequire(completion.revisions >= required && completion.bytes >= required.toLong() * 262_144, SecureStateStatus.CAPACITY_REQUIRED)
            if (mode == SecurePendingDispatchMode.ORIGINAL) secureRequire(target.expiresAtEpochSeconds?.let { it > now().epochSecond } != false, SecureStateStatus.OUTCOME_PENDING)
            val first = current.firstBind
            secureRequire(first == null || first.operation == target.operation && first.ceremony == target.ceremony && first.role == target.role, SecureStateStatus.OUTCOME_PENDING)
            if (current.activeBinding != null) read(current.selected.single { it.slot == current.activeBinding }).use { active ->
                secureRequire(sameBindingTarget(active.owner, target) && active.owner.session == target.session && active.owner.sessionGeneration == target.sessionGeneration, SecureStateStatus.CONTEXT_CHANGED)
            }
            if (pending.operation != PendingOperation.DELETION) current.selected.filter { it.slot in setOf(SecureRecordKind.DELETION_SOURCE.reserved, SecureRecordKind.DELETION_RECIPIENT.reserved) }.forEach { veto -> read(veto).use { secureRequire(it.owner.account != target.account || !it.owner.deletionVeto, SecureStateStatus.OUTCOME_PENDING) } }
            val tag = target.keyTag
            if (tag != null) {
                val key = readBoundPublicKey(current, tag)
                try { secureRequire(secureSha256(key) == target.keyThumbprint, SecureStateStatus.KEY_MISSING) } finally { key.fill(0) }
            } else secureRequire(pending.wireKind == PendingWireKind.PORTABLE_ROTATE, SecureStateStatus.KEY_MISSING)
        }
    }

    /** Only typed D1 prepared commands call this; no arbitrary message/domain native API. */
    internal fun sign(expected: SecureControl, owner: SecureRecordOwner, proofOperation: DeviceProofOperation,
        digest: ByteArray): CompletableFuture<ByteArray> {
        secureRequire(digest.size == 32)
        val owned = digest.copyOf()
        return operation(onSettled = { owned.fill(0) }) {
            val current = requireSnapshot(expected)
            requireOwner(current, owner, proofOperation)
            val tag = requireNotNull(owner.keyTag)
            val key = readBoundPublicKey(current, tag)
            try { secureRequire(secureSha256(key) == owner.keyThumbprint, SecureStateStatus.KEY_MISSING) } finally { key.fill(0) }
            native.signDeviceDigest(handle, tag, proofOperation, owned).let { der ->
                try { DeviceSignatureCodec.derToP1363(der) } finally { der.fill(0) }
            }
        }
    }

    /** Fresh read plus full target match, deletion veto, key identity and active selection. */
    public fun access(expected: SecureControl, owner: SecureRecordOwner): CompletableFuture<SecretBytes> = operation {
        val current = requireSnapshot(expected); requireOwner(current, owner)
        secureRequire(current.activeBinding == owner.profile, SecureStateStatus.CONTEXT_CHANGED)
        val reference = current.selected.single { it.slot == current.activeBinding }
        read(reference).use { record ->
            secureRequire(record.owner == owner && record.owner.stage == SecureStage.ACTIVE && !owner.detached && !owner.deletionVeto, SecureStateStatus.CONTEXT_CHANGED)
            val key = readBoundPublicKey(current, requireNotNull(owner.keyTag)); key.fill(0)
            val payload = record.payload as? SessionPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(payload.accessExpiresAt > now().epochSecond && payload.refreshExpiresAt > now().epochSecond, SecureStateStatus.CONTEXT_CHANGED)
            val policy = payload.transportPolicyCopy()
            try { secureRequire(policy.isNotEmpty() && SecureMediaGuardCodec.readTransport(policy).known, SecureStateStatus.OUTCOME_PENDING) }
            finally { policy.fill(0) }
            val bytes = payload.accessCopy()
            try { transientSecret(bytes) } finally { bytes.fill(0) }
        }
    }

    public fun transientSecret(bytes: ByteArray): SecretBytes {
        lateinit var secret: SecretBytes
        secret = SecretBytes(bytes) { releaseTransient(secret) }
        return retainTransient(secret)
    }
    public fun <T : AutoCloseable> retainTransient(value: T): T = synchronized(gate) {
        if (!admissions.get() || transients.size >= 8) { value.close(); throw SecureStateFailure(if (admissions.get()) SecureStateStatus.CAPACITY_REQUIRED else SecureStateStatus.CLOSED) }
        transients.add(value); value
    }
    public fun releaseTransient(value: AutoCloseable): Unit { transients.remove(value) }

    public fun retireUnreferencedKey(expected: SecureControl, tag: String): CompletableFuture<SecureControl> = operation {
        var current = requireSnapshot(expected)
        val intent = current.keys.singleOrNull { it.tag == tag } ?: throw SecureStateFailure(SecureStateStatus.KEY_MISSING)
        secureRequire(current.reservations.isEmpty(), SecureStateStatus.OUTCOME_PENDING)
        (current.selected + current.cleanup).forEach { reference ->
            val bytes = native.readValue(handle, reference.valueId)
            if (bytes != null) try {
                verify(reference, bytes)
                SecureRecordCodec.decode(bytes).use { secureRequire(it.owner.keyTag != tag, SecureStateStatus.OUTCOME_PENDING) }
            } finally { bytes.fill(0) }
        }
        if (intent.state != DeviceKeyState.RETIRED) current = publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), keys = current.keys.map { if (it.tag == tag) it.copy(state = DeviceKeyState.RETIRED) else it }))
        uncertain { native.deleteRetiredDeviceKey(handle, tag) }
        secureRequire(tag !in inventory().keyTags, SecureStateStatus.OUTCOME_PENDING)
        publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), keys = current.keys.filterNot { it.tag == tag }))
    }

    /** Only exact unreferenced immutable values, including absent staging intents, are reclaimed. */
    public fun retireSettledJournal(expected: SecureControl, slot: String,
        protocolAllowsRetirement: (SecureRecord) -> Boolean): CompletableFuture<SecureControl> = operation {
        val current = requireSnapshot(expected)
        secureRequire(current.activeBinding != slot && current.reservations.none { slot in it.slots }, SecureStateStatus.OUTCOME_PENDING)
        val reference = current.selected.singleOrNull { it.slot == slot } ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        read(reference).use { record ->
            secureRequire(record.payload is PendingPayload && record.owner.stage == SecureStage.TERMINAL && current.firstBind?.operation != record.owner.operation && protocolAllowsRetirement(record), SecureStateStatus.OUTCOME_PENDING)
        }
        publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), selected = current.selected.filterNot { it.slot == slot }, cleanup = current.cleanup + reference))
    }

    /** Only exact unreferenced immutable values, including absent staging intents, are reclaimed. */
    public fun collect(expected: SecureControl): CompletableFuture<SecureControl> = operation {
        var current = requireSnapshot(expected)
        for (reference in current.cleanup.toList()) {
            secureRequire(current.selected.none { it.valueId == reference.valueId })
            // Reservations retain their slot history until their exact operation settles.
            if (current.reservations.any { reference.slot in it.slots }) continue
            val bytes = native.readValue(handle, reference.valueId)
            if (bytes != null) {
                try { verify(reference, bytes); SecureRecordCodec.decode(bytes).close() } finally { bytes.fill(0) }
                uncertain { native.deleteValue(handle, reference.valueId) }
                val after = native.readValue(handle, reference.valueId)
                if (after != null) { after.fill(0); blocked = true; throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING) }
            }
            current = publishControl(current, current.copy(revision = SecureStateLimits.next(current.revision), cleanup = current.cleanup.filterNot { it.valueId == reference.valueId }))
        }
        current
    }

    /** Synchronous lifecycle barrier; drainSettled owns the lock until actual native settlement. */
    public fun retireAdmissions(): Unit {
        admissions.set(false); generation.incrementAndGet()
        transients.forEach { runCatching { it.close() } }; transients.clear()
    }
    public fun drainSettled(): CompletableFuture<Unit> {
        retireAdmissions(); closing.set(true)
        accountGate.retire { queueClose() }
        return drained
    }
    public fun close(): CompletableFuture<Unit> = drainSettled()

    private fun queueClose(): Unit {
        synchronized(gate) {
            if (!accountLifetimeHeld.get() && !drained.isDone && closeQueued.compareAndSet(false, true)) {
                try { executor.execute { settleClose() } }
                catch (_: RejectedExecutionException) {
                    closeQueued.set(false)
                    // A full bounded queue drains through each descriptor's finally block.
                    if (executor.isTerminated && !drained.isDone) drained.completeExceptionally(SecureStateFailure(SecureStateStatus.UNAVAILABLE))
                }
            }
        }
    }

    private fun reload(): SecureControl {
        secureRequire(handle != 0L && !blocked, if (blocked) SecureStateStatus.OUTCOME_PENDING else SecureStateStatus.CLOSED)
        val bytes = native.readControl(handle) ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        return try { SecureControlCodec.decode(bytes).also { reconcile(it, inventory()) } } finally { bytes.fill(0) }
    }
    private fun requireSnapshot(expected: SecureControl): SecureControl = reload().also { secureRequire(it == expected, SecureStateStatus.CONTEXT_CHANGED) }
    private fun requireOwner(control: SecureControl, owner: SecureRecordOwner, proofOperation: DeviceProofOperation? = null) {
        secureRequire(owner.boot == boot && owner.authorityRevision == control.authorityRevision && !owner.detached && !owner.deletionVeto, SecureStateStatus.CONTEXT_CHANGED)
        val firstBind = control.firstBind
        if (firstBind != null) secureRequire(firstBind.operation == owner.operation && firstBind.ceremony == owner.ceremony && firstBind.role == owner.role, SecureStateStatus.OUTCOME_PENDING)
        if (control.activeBinding != null) read(control.selected.single { it.slot == control.activeBinding }).use { active ->
            secureRequire(sameBindingTarget(active.owner, owner) && active.owner.session == owner.session && active.owner.sessionGeneration == owner.sessionGeneration, SecureStateStatus.CONTEXT_CHANGED)
        }
        val cancellationProof = proofOperation in setOf(DeviceProofOperation.DELETION_PREVIEW, DeviceProofOperation.DELETION_CANCEL)
        control.selected.filter { it.slot in setOf(SecureRecordKind.DELETION_SOURCE.reserved, SecureRecordKind.DELETION_RECIPIENT.reserved) }.forEach { reference -> read(reference).use {
            val exactCancel = cancellationProof && it.owner.server == owner.server && it.owner.identityEpoch == owner.identityEpoch && it.owner.identityThumbprint == owner.identityThumbprint && it.owner.apiOrigin == owner.apiOrigin && it.owner.streamOrigin == owner.streamOrigin
            secureRequire(it.owner.account != owner.account || !it.owner.deletionVeto || exactCancel, SecureStateStatus.OUTCOME_PENDING)
        } }
        control.selected.filter { it.slot != owner.profile }.forEach { reference -> read(reference).use {
            if (it.payload is PendingPayload && it.owner.profile == owner.profile && it.owner.stage != SecureStage.TERMINAL)
                secureRequire(it.owner.operation == owner.operation || cancellationProof && it.payload.kind == SecureRecordKind.DELETION_SOURCE, SecureStateStatus.OUTCOME_PENDING)
        } }
    }
    private fun readBoundPublicKey(control: SecureControl, tag: String): ByteArray {
        val intent = control.keys.singleOrNull { it.tag == tag && it.state == DeviceKeyState.BOUND } ?: throw SecureStateFailure(SecureStateStatus.KEY_MISSING)
        val key = native.publicDeviceKey(handle, tag)
        try { DeviceSignatureCodec.validateSpki(key); secureRequire(secureSha256(key) == intent.thumbprint, SecureStateStatus.KEY_MISSING); return key }
        catch (failure: Exception) { key.fill(0); throw failure }
    }
    private fun inventory(): SecureNamespaceInventory = native.inventory(handle).also { value ->
        secureRequire(value.values.size <= 64 && value.keyTags.size <= 32 && value.values.values.all { it in 18..SecureStateLimits.VALUE } && value.values.values.sumOf { it.toLong() } <= SecureStateLimits.TOTAL, SecureStateStatus.CAPACITY_REQUIRED)
    }
    private fun reconcile(control: SecureControl, inventory: SecureNamespaceInventory) {
        secureRequire(inventory.hasControl)
        val known = control.selected + control.cleanup
        secureRequire(inventory.values.keys.all { id -> known.any { it.valueId == id } })
        secureRequire(inventory.keyTags.all { tag -> control.keys.any { it.tag == tag } })
        control.selected.forEach { reference -> secureRequire(inventory.values[reference.valueId] == reference.length); read(reference).close() }
        // A missing bound device key blocks its credential/signing lookup, never creates a key.
        // Independent ROOT/retained LOCAL guard inspection does not require that device key.
        if (control.activeBinding != null) read(control.selected.single { it.slot == control.activeBinding }).use { secureRequire(it.payload is SessionPayload && it.owner.stage == SecureStage.ACTIVE && !it.owner.deletionVeto && !it.owner.detached) }
    }
    private fun capacity(control: SecureControl, inventory: SecureNamespaceInventory, staging: List<SecureValueReference> = emptyList()) {
        val references = (control.selected + control.cleanup + staging).associateBy { it.valueId }
        secureRequire(inventory.values.keys.all { it in references })
        val ids = inventory.values.keys + references.keys
        val missingBytes = references.values.filter { it.valueId !in inventory.values }.sumOf { it.length.toLong() }
        val reservedSlots = control.reservations.flatMap { it.slots }.toSet()
        val logical = references.values.map { it.slot }.toSet() + reservedSlots
        secureRequire(logical.size <= 32 && logical.count { id -> SecureRecordKind.entries.none { it.reserved == id } } <= 24 &&
            ids.size + control.reservations.sumOf { it.revisions } <= 64 &&
            inventory.values.values.sumOf { it.toLong() } + missingBytes + control.reservations.sumOf { it.bytes } <= SecureStateLimits.TOTAL,
            SecureStateStatus.CAPACITY_REQUIRED)
        SecureControlCodec.encode(control).fill(0)
    }
    private fun read(reference: SecureValueReference): SecureRecord {
        val bytes = native.readValue(handle, reference.valueId) ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        return try { verify(reference, bytes); SecureRecordCodec.decode(bytes).also { secureRequire(it.slot == reference.slot && it.revision == reference.slotRevision) } } finally { bytes.fill(0) }
    }
    private fun verify(reference: SecureValueReference, bytes: ByteArray) { secureRequire(bytes.size == reference.length && secureSha256(bytes) == reference.sha256) }
    private fun exactValue(reference: SecureValueReference, expected: ByteArray) {
        val actual = native.readValue(handle, reference.valueId)
        try { secureRequire(actual != null && actual.contentEquals(expected), SecureStateStatus.OUTCOME_PENDING) }
        catch (failure: Exception) { blocked = true; throw failure } finally { actual?.fill(0) }
    }
    private fun exactControl(expected: ByteArray) {
        val actual = native.readControl(handle)
        try { secureRequire(actual != null && actual.contentEquals(expected), SecureStateStatus.OUTCOME_PENDING) }
        catch (failure: Exception) { blocked = true; throw failure } finally { actual?.fill(0) }
    }
    private fun publishControl(old: SecureControl, next: SecureControl): SecureControl {
        secureRequire(next.revision == SecureStateLimits.next(old.revision))
        val before = SecureControlCodec.encode(old); val after = SecureControlCodec.encode(next)
        try { uncertain { native.publishControl(handle, before, after) }; exactControl(after); return next }
        finally { before.fill(0); after.fill(0) }
    }
    private fun requireTransition(before: SecureRecord, after: SecureRecord, control: SecureControl,
        handoff: SecurePendingHandoff?) {
        val oldPayload = before.payload; val newPayload = after.payload
        secureRequire(oldPayload.kind == newPayload.kind, SecureStateStatus.CONTEXT_CHANGED)
        requireLocalGuardTransition(oldPayload, newPayload, before.owner, after.owner, control)
        if (oldPayload is PortableRootPayload && newPayload is PortableRootPayload) {
            secureRequire(!oldPayload.everEnrolled || newPayload.everEnrolled)
            secureRequire(oldPayload.ownershipCutoffMs == newPayload.ownershipCutoffMs)
            val old = oldPayload.lineageCopy(); val next = newPayload.lineageCopy()
            try { val entries = SecureJson.parse(old, 49152).array("predecessors"); val successor = SecureJson.parse(next, 49152).array("predecessors"); secureRequire(successor.containsAll(entries)) } finally { old.fill(0); next.fill(0) }
        }
        if (oldPayload is SessionPayload && newPayload is SessionPayload) {
            val newBinding = control.activeBinding == null && control.firstBind?.operation == after.owner.operation && after.owner.stage == SecureStage.BINDING_COMMIT_PENDING && before.owner.account == after.owner.account
            secureRequire(newBinding || sameBindingTarget(before.owner, after.owner), SecureStateStatus.CONTEXT_CHANGED)
            val oldGeneration = requireNotNull(before.owner.sessionGeneration); val newGeneration = requireNotNull(after.owner.sessionGeneration)
            secureRequire(newBinding && newGeneration == 0L || newGeneration == oldGeneration && after.owner.session == before.owner.session || newGeneration == SecureStateLimits.next(oldGeneration))
            secureRequire(newBinding || oldPayload.refreshExpiresAt == newPayload.refreshExpiresAt)
            val oldM5 = oldPayload.m5; val newM5 = newPayload.m5
            secureRequire(oldPayload.d1.accepts(newPayload.d1) && (oldM5 == null || newM5 != null && oldM5.accepts(newM5)))
            secureRequire(!before.owner.deletionVeto || after.owner.deletionVeto)
        }
        if (oldPayload is PendingPayload && before.owner.stage != SecureStage.TERMINAL) {
            if (handoff != null && handoff.slot == before.slot) {
                secureRequire(newPayload is PendingPayload && before.owner.operation == handoff.previousOperation && after.owner.operation == handoff.nextOperation &&
                    control.selected.any { it.slot == before.slot && it.valueId == handoff.previousValueId } && after.owner.stage == SecureStage.PREPARED && before.owner.stage in setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.RECORDED), SecureStateStatus.OUTCOME_PENDING)
                requireKnownTarget(before.owner, after.owner)
                (newPayload as PendingPayload).requireRetainsSecrets(oldPayload)
                val receipt = (newPayload as PendingPayload).receiptCopy()
                try { secureRequire(secureSha256(receipt) == handoff.receiptSha256, SecureStateStatus.OUTCOME_PENDING) } finally { receipt.fill(0) }
                return // Previous immutable original remains charged in cleanup during the successor.
            }
            if (before.owner.deletionVeto && !after.owner.deletionVeto) requireDeletionClear(before, after, control)
            val pending = newPayload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(oldPayload.wireKind == pending.wireKind, SecureStateStatus.OUTCOME_PENDING)
            pending.requireRetainsSecrets(oldPayload)
            secureRequire(before.owner.copy(boot = after.owner.boot, authorityRevision = after.owner.authorityRevision, stage = after.owner.stage, deletionVeto = after.owner.deletionVeto) == after.owner, SecureStateStatus.OUTCOME_PENDING)
            val allowed = when (before.owner.stage) {
                SecureStage.PREPARED -> setOf(SecureStage.PREPARED, SecureStage.DISPATCHED, SecureStage.TERMINAL)
                SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING -> setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.BINDING_COMMIT_PENDING, SecureStage.RECORDED, SecureStage.TERMINAL)
                SecureStage.BINDING_COMMIT_PENDING -> setOf(SecureStage.BINDING_COMMIT_PENDING, SecureStage.TERMINAL)
                SecureStage.RECORDED -> setOf(SecureStage.RECORDED, SecureStage.TERMINAL)
                else -> emptySet()
            }
            secureRequire(after.owner.stage in allowed, SecureStateStatus.OUTCOME_PENDING)
            val old = oldPayload.requestCopy(); val next = pending.requestCopy()
            try { secureRequire(old.contentEquals(next), SecureStateStatus.OUTCOME_PENDING) } finally { old.fill(0); next.fill(0) }
        }
    }
    private fun requireKnownTarget(previous: SecureRecordOwner, next: SecureRecordOwner) {
        secureRequire(previous.ceremony == next.ceremony && previous.role == next.role && previous.platform == next.platform && previous.protocolVersion == next.protocolVersion && previous.deletionVeto == next.deletionVeto && previous.detached == next.detached, SecureStateStatus.CONTEXT_CHANGED)
        listOf(previous.profile to next.profile, previous.account to next.account, previous.server to next.server,
            previous.identityEpoch to next.identityEpoch, previous.identityThumbprint to next.identityThumbprint,
            previous.apiOrigin to next.apiOrigin, previous.streamOrigin to next.streamOrigin, previous.device to next.device,
            previous.keyTag to next.keyTag, previous.keyThumbprint to next.keyThumbprint,
            previous.bindingCommit to next.bindingCommit, previous.session to next.session, previous.family to next.family,
            previous.sessionGeneration to next.sessionGeneration, previous.claim to next.claim, previous.selfSourcePlatform to next.selfSourcePlatform).forEach { (old, candidate) -> secureRequire(old == null || old == candidate, SecureStateStatus.CONTEXT_CHANGED) }
        val expiry = previous.expiresAtEpochSeconds
        secureRequire(expiry == null || next.expiresAtEpochSeconds?.let { it <= expiry } == true, SecureStateStatus.CONTEXT_CHANGED)
    }
    private fun requireLocalGuardTransition(before: SecurePayload, after: SecurePayload,
        previousOwner: SecureRecordOwner, nextOwner: SecureRecordOwner, control: SecureControl) {
        fun bytes(payload: SecurePayload): ByteArray? = when (payload) {
            is PortableRootPayload -> payload.localGuardCopy()
            is SessionPayload -> payload.localGuardCopy()
            else -> null
        }
        val previous = bytes(before) ?: return
        val next = bytes(after) ?: throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
        try {
            if (previous.contentEquals(next)) return
            secureRequire(next.isNotEmpty() && nextOwner.authorityRevision == SecureStateLimits.next(control.authorityRevision), SecureStateStatus.CONTEXT_CHANGED)
            val candidate = SecureMediaGuardCodec.readLocal(next)
            if (previous.isEmpty()) secureRequire(candidate.revision == 1L)
            else secureRequire(candidate.revision == SecureStateLimits.next(SecureMediaGuardCodec.readLocal(previous).revision))
            secureRequire(previousOwner.account == nextOwner.account, SecureStateStatus.CONTEXT_CHANGED)
        } finally { previous.fill(0); next.fill(0) }
    }
    private fun requireDeletionClear(before: SecureRecord, after: SecureRecord, control: SecureControl) {
        secureRequire(after.owner.stage == SecureStage.TERMINAL, SecureStateStatus.OUTCOME_PENDING)
        if (before.owner.stage == SecureStage.PREPARED) return
        val payload = after.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        val receipt = payload.receiptCopy()
        try {
            if (before.payload.kind == SecureRecordKind.DELETION_SOURCE) {
                for (reference in control.selected) read(reference).use { recipient ->
                    if (SecureDeletionReconciliation.matches(before, recipient, receipt)) return
                }
            }
            val result = SecureJson.parse(receipt, 65536, canonical = false)
            if (before.payload.kind == SecureRecordKind.DELETION_RECIPIENT && result["operation_id"] != null) {
                val request = (before.payload as PendingPayload).requestCopy()
                try {
                    val original = SecureJson.parse(request, 8192, canonical = false)
                    secureRequire(result.string("contract_version") == "v2" && result.number("schema_version") == 2L && result.string("operation_id") == before.owner.operation && result.string("user_id") == before.owner.account && result.string("binding_commit_id") == before.owner.bindingCommit && result.number("code_generation") == SecureStateLimits.next(original.number("expected_code_generation")), SecureStateStatus.OUTCOME_PENDING)
                } finally { request.fill(0) }
            } else secureRequire(result.string("state") in setOf("CANCELLED", "NOT_ACCEPTED") && result.string("account_id") == before.owner.account && result.string("deletion_request_id") == before.owner.operation, SecureStateStatus.OUTCOME_PENDING)
        } finally { receipt.fill(0) }
    }
    private fun sameBindingTarget(first: SecureRecordOwner, next: SecureRecordOwner): Boolean =
        first.profile == next.profile && first.account == next.account && first.server == next.server &&
            first.identityEpoch == next.identityEpoch && first.identityThumbprint == next.identityThumbprint &&
            first.apiOrigin == next.apiOrigin && first.streamOrigin == next.streamOrigin && first.platform == next.platform &&
            first.device == next.device && first.keyTag == next.keyTag &&
            first.keyThumbprint == next.keyThumbprint && first.family == next.family && first.bindingCommit == next.bindingCommit
    private inline fun <T> uncertain(block: () -> T): T = try { block() } catch (_: Exception) { blocked = true; retireAdmissions(); throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING) }

    private fun <T> payloadOperation(block: () -> T): CompletableFuture<T> = synchronized(gate) {
        if (!payloadAdmitted.compareAndSet(false, true)) return failed(SecureStateStatus.CAPACITY_REQUIRED)
        operation(onSettled = { payloadAdmitted.set(false) }, block = block)
    }
    private fun <T> operation(onSettled: () -> Unit = {}, block: () -> T): CompletableFuture<T> = synchronized(gate) {
        if (!admissions.get() || closing.get()) { onSettled(); return failed(SecureStateStatus.CLOSED) }
        val admission = accountGate.admit()
        if (admission == null) { onSettled(); return failed(SecureStateStatus.IN_USE) }
        val captured = generation.get()
        val result = CompletableFuture<T>()
        val settledOnce = AtomicBoolean(false)
        fun settled() {
            if (settledOnce.compareAndSet(false, true)) try { onSettled() } finally { admission.close() }
        }
        val timeout = deadlines.schedule({
            if (!result.isDone) { retireAdmissions(); result.completeExceptionally(SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)) }
        }, 15, TimeUnit.SECONDS)
        result.whenComplete { _, _ -> if (result.isCancelled) retireAdmissions() }
        try {
            executor.execute {
                try {
                    secureRequire(captured == generation.get() && admissions.get() && !closing.get(), SecureStateStatus.CONTEXT_CHANGED)
                    val value = block()
                    settled()
                    if (captured != generation.get() || !result.complete(value)) {
                        when (value) { is AutoCloseable -> value.close(); is ByteArray -> value.fill(0) }
                        result.completeExceptionally(SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED))
                    }
                } catch (failure: Exception) {
                    settled()
                    result.completeExceptionally(if (failure is SecureStateFailure) failure else SecureStateFailure(SecureStateStatus.UNAVAILABLE))
                } catch (_: LinkageError) {
                    retireAdmissions(); settled(); result.completeExceptionally(SecureStateFailure(SecureStateStatus.UNAVAILABLE))
                } finally { settled(); timeout.cancel(false) }
            }
        } catch (_: RejectedExecutionException) { settled(); timeout.cancel(false); result.completeExceptionally(SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED)) }
        result
    }
    private fun settleClose() {
        try { if (handle != 0L) { native.close(handle); handle = 0 }; drained.complete(Unit) }
        catch (_: Exception) { drained.completeExceptionally(SecureStateFailure(SecureStateStatus.UNAVAILABLE)) }
        catch (_: LinkageError) { drained.completeExceptionally(SecureStateFailure(SecureStateStatus.UNAVAILABLE)) }
        finally { deadlines.shutdown(); executor.shutdown() }
    }
    private fun <T> failed(status: SecureStateStatus): CompletableFuture<T> = CompletableFuture<T>().also { it.completeExceptionally(SecureStateFailure(status)) }
}
