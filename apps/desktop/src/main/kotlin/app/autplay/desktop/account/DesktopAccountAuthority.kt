package app.autplay.desktop.account

import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.*
import app.autplay.data.desktop.account.createDesktopAccountStore
import app.autplay.data.desktop.account.requireDesktopAccountVirginNamespace
import app.autplay.desktop.security.*
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*

/** Must fence external admission and await real HTTP/media/file/work settlement, without retiring K. */
public fun interface DesktopAccountExternalDrain {
    public fun quiesce(): CompletableFuture<Unit>
    /** Exact existing SESSION_ROTATE only; default owners still require a complete external drain. */
    public fun quiesceRenewal(owner: SecureRecordOwner): CompletableFuture<Unit> = quiesce()
}

/** Sole protected projection owner. Retain this object even when startup/close fails. */
public class DesktopAccountAuthority(
    private val secure: DesktopSecureStateOwner,
    private val external: DesktopAccountExternalDrain,
) : DesktopAccountAuthorityPort, SecureBindingProjection {
    private val job: CompletableJob = SupervisorJob()
    private val worker: CoroutineScope = CoroutineScope(job + Dispatchers.Default)
    private val busy = AtomicBoolean(false)
    private val closing = AtomicBoolean(false)
    private val closed = AtomicBoolean(false)
    private val capturesOpen = AtomicBoolean(true)
    private val guardPublication = AtomicBoolean(false)
    private val lifetime = AtomicReference<Lifetime?>(null)
    private val active = AtomicReference<AccountLease?>(null)
    private val retained = java.util.concurrent.ConcurrentHashMap<UUID, CompletableFuture<*>>()
    private var store: DesktopAccountStore? = null

    @Synchronized public fun attachStore(value: DesktopAccountStore): Unit {
        accountRequire(store == null); store = value
    }

    /** Explicit first-install command only, while K.open owns its canonical lifetime lock. */
    public fun initializeVirginProtectedNamespace(): CompletableFuture<Unit> = task {
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        try {
            requireDesktopAccountVirginNamespace()
            read(permit) { secure.initializeEmpty(existingAccountMarkers = false) }
        } finally { permit.close() }
        Unit
    }

    public fun initialize(): CompletableFuture<AccountReconciliationSnapshot> = task {
        val initial = database().initialize()
        initial.unresolvedBindingIntent?.let { intent ->
            val snapshot = await(secure.snapshot())
            val record = await(secure.read(intent.pendingSlot.value, snapshot))
            val terminal = record?.use { it.owner.stage == SecureStage.TERMINAL } == true
            if (terminal) database().cancelBindingIntent(intent.intentId)
        }
        database().reconciliationSnapshot()
    }

    override fun tryAcquireLifetime(): AccountProtectedLifetime? {
        if (closing.get() || lifetime.get() != null) return null
        val permit = secure.accountGate.tryAcquire() ?: return null
        try {
            var snapshot = read(permit) { secure.snapshot() }
            if (!secure.pinAccountLifetime(permit)) return null
            try {
                if (snapshot.jInstallation == null) {
                    requireDesktopAccountVirginNamespace()
                    snapshot = read(permit) { secure.reserveAccountInstallation(snapshot, namespaceVirgin = true) }
                }
                val installation = snapshot.jInstallation ?: reconciliation()
                val state = if (installation.stage == SecureJInstallationStage.RESERVED)
                    AccountProtectedStoreState.Reserved(AccountUuid(installation.storeId))
                else AccountProtectedStoreState.Existing(AccountUuid(installation.storeId))
                return Lifetime(state).also { accountRequire(lifetime.compareAndSet(null, it)) }
            } catch (failure: Throwable) { secure.releaseAccountLifetime(); throw failure }
        } finally { permit.close() }
    }

    private inner class Lifetime(override val storeState: AccountProtectedStoreState) : AccountProtectedLifetime {
        private val ended = AtomicBoolean(false)
        override fun completeInstallation(storeId: AccountUuid): Boolean {
            if (ended.get()) return false
            val permit = secure.accountGate.tryAcquire() ?: return false
            try {
                val snapshot = read(permit) { secure.snapshot() }
                val result = read(permit) { secure.completeAccountInstallation(snapshot, storeId.value) }
                val installation = result.jInstallation ?: return false
                return installation.storeId == storeId.value && installation.stage == SecureJInstallationStage.READY
            } finally { permit.close() }
        }

        override fun tryCaptureIntent(storeId: AccountUuid, intent: AccountBindingIntent): AutoCloseable? {
            if (ended.get() || !capturesOpen.get() || guardPublication.get()) return null
            val permit = secure.accountGate.tryAcquire() ?: return null
            try {
                val snapshot = read(permit) { secure.snapshot() }
                installation(snapshot, storeId)
                val stored = read(permit) { secure.read(intent.pendingSlot.value, snapshot) } ?: reconciliation()
                stored.use { requireIntent(it, snapshot, intent) }
                return permit
            } catch (failure: Throwable) { permit.close(); throw failure }
        }

        override fun tryCaptureIntentCancellation(storeId: AccountUuid, intent: AccountBindingIntent): AutoCloseable? {
            if (ended.get() || !capturesOpen.get()) return null
            val permit = secure.accountGate.tryAcquire() ?: return null
            try {
                val snapshot = read(permit) { secure.snapshot() }
                installation(snapshot, storeId)
                val terminal = read(permit) { secure.read(intent.pendingSlot.value, snapshot) } ?: reconciliation()
                terminal.use {
                    accountRequire(it.owner.stage == SecureStage.TERMINAL && it.owner.operation == intent.operationId.value)
                    val reference = snapshot.cleanup.singleOrNull { old -> old.slot == it.slot && old.slotRevision == it.revision - 1 }
                        ?: reconciliation()
                    read(permit) { secure.readRetained(snapshot, reference) }.use { original ->
                        val pending = original.payload as? PendingPayload ?: reconciliation()
                        val selected = it.payload as? PendingPayload ?: reconciliation()
                        val owner = original.owner
                        accountRequire(owner.stage == SecureStage.PREPARED && owner.operation == intent.operationId.value &&
                            owner.authorityRevision == intent.authorityRevision && owner.profile == intent.profileId.value &&
                            owner.server == intent.serverInstanceId.value && owner.identityEpoch == intent.identityEpoch &&
                            owner.identityThumbprint == intent.serverThumbprint && owner.keyThumbprint == intent.deviceKeyThumbprint &&
                            owner.account == intent.knownUserId?.value && owner.device == intent.knownDeviceId?.value &&
                            pending.wireKind == intent.wireKind && selected.wireKind == intent.wireKind)
                        val before = pending.requestCopy(); val after = selected.requestCopy()
                        try { accountRequire(before.contentEquals(after)) } finally { before.fill(0); after.fill(0) }
                    }
                }
                return permit
            } catch (failure: Throwable) { permit.close(); throw failure }
        }

        override fun tryCapture(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture? =
            capture(expectedStoreId, preparation, activation = false)
        override fun tryCaptureForActivation(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture? =
            capture(expectedStoreId, preparation, activation = true)
        private fun capture(expectedStoreId: AccountUuid, preparation: AccountScopePreparation, activation: Boolean): AccountProtectedCapture? {
            if (ended.get() || !capturesOpen.get() || !activation && guardPublication.get()) return null
            val permit = secure.accountGate.tryAcquire() ?: return null
            try {
                val snapshot = read(permit) { secure.snapshot() }
                installation(snapshot, expectedStoreId)
                val record = read(permit) { secure.read(slot(preparation), snapshot) } ?: reconciliation()
                record.use {
                    val witness = it.owner.accountProjection ?: reconciliation()
                    accountRequire(witness.storeId == expectedStoreId && witness.preparation == preparation)
                    validate(permit, snapshot, it, witness)
                    // During the final local commit only activation may finish; domain admission waits for K ACTIVE.
                    val visible = if (!activation && witness.stage == AccountProtectedStage.READY && preparation.binding != null &&
                        (snapshot.activeBinding != it.slot || it.owner.stage != SecureStage.ACTIVE))
                        witness.copy(lifecycleAllowed = false) else witness
                    return Capture(permit, it.slot, visible)
                }
            } catch (failure: Throwable) { permit.close(); throw failure }
        }

        override fun close(): Unit {
            if (ended.compareAndSet(false, true)) {
                accountRequire(lifetime.compareAndSet(this, null)); secure.releaseAccountLifetime()
            }
        }
    }

    private inner class Capture(private val permit: SecureAuthorityGate.Permit, private val selectedSlot: String,
        override val witness: AccountProtectedWitness) : AccountProtectedCapture {
        private val ended = AtomicBoolean(false)
        override fun completeReconciliation(readback: AccountActivationReadback): AccountProtectedWitness? {
            accountRequire(!ended.get() && readback.storeId == witness.storeId && readback.preparation == witness.preparation &&
                readback.activationRevision == witness.preparation.expectedActivationRevision + 1)
            var snapshot = read(permit) { secure.snapshot() }
            val record = read(permit) { secure.read(selectedSlot, snapshot) } ?: reconciliation()
            record.use {
                accountRequire(it.owner.accountProjection == witness)
                validate(permit, snapshot, it, witness)
                val ready = witness.copy(stage = AccountProtectedStage.READY, permitsNewDeviceLineage = false)
                if (ready != witness) snapshot = publishWitness(permit, snapshot, it, ready)
                val selected = read(permit) { secure.read(selectedSlot, snapshot) } ?: reconciliation()
                selected.use { value ->
                    accountRequire(value.owner.accountProjection == ready)
                    validate(permit, snapshot, value, ready)
                }
                return ready
            }
        }
        override fun close(): Unit { if (ended.compareAndSet(false, true)) permit.close() }
    }

    /** Original intent is durable/read back BEFORE dispatch. A sent original cannot backfill a lost intent. */
    override fun beforeDispatch(ticket: SecurePendingTicket): CompletableFuture<SecureBindingDispatchPin> = task {
        accountRequire(!guardPublication.get())
        val inspection = await(secure.read(ticket.slot, ticket.control)) ?: reconciliation()
        var localChoice = AccountBindingLocalChoice.KEEP_LOCAL
        var selectedChanges: List<AccountUuid> = emptyList()
        val wire = inspection.use {
            accountRequire(it.owner == ticket.owner)
            val payload = it.payload as? PendingPayload ?: reconciliation()
            if (payload.wireKind in AccountBindingIntentCodec.GRANTS) {
                val authorization = payload.authorizationCopy()
                try { AccountCommandContextCodec.read(authorization).use { context ->
                    val selection = context.materialization.copy()
                    try {
                        val document = SecureJson.parse(selection, 8192)
                        localChoice = AccountBindingLocalChoice.valueOf(document.string("decision"))
                        selectedChanges = document.array("change_ids").map { id ->
                            AccountUuid((id as kotlinx.serialization.json.JsonPrimitive).content)
                        }
                    }
                    finally { selection.fill(0) }
                } } finally { authorization.fill(0) }
            }
            payload.wireKind
        }
        if (wire in AccountBindingIntentCodec.GRANTS || wire == PendingWireKind.SESSION_ROTATE) {
            await(if (wire == PendingWireKind.SESSION_ROTATE) external.quiesceRenewal(ticket.owner) else external.quiesce())
            active.getAndSet(null)?.let { database().suspendScope(it) }
        }
        if (wire in AccountBindingIntentCodec.GRANTS) {
            val operation = AccountUuid(requireNotNull(ticket.owner.operation))
            var intent = database().readBindingIntent(operation)
            if (intent == null) {
                accountRequire(ticket.owner.stage == SecureStage.PREPARED)
                val sql = database().reconciliationSnapshot()
                val root = await(secure.read(rootSlot, ticket.control))
                val portable = root?.use { record ->
                    accountRequire(record.payload is PortableRootPayload && record.owner.account == ticket.owner.account)
                    AccountUuid(requireNotNull(record.owner.account))
                }
                intent = AccountBindingIntent(operation, operation, AccountUuid(ticket.slot), ticket.owner.authorityRevision,
                    sql.activationRevision, AccountUuid(requireNotNull(ticket.owner.server)), requireNotNull(ticket.owner.identityEpoch),
                    requireNotNull(ticket.owner.identityThumbprint), requireNotNull(ticket.owner.keyThumbprint),
                    AccountUuid(requireNotNull(ticket.owner.profile)), portable, AccountUuid.random(),
                    ticket.owner.account?.let(::AccountUuid), ticket.owner.device?.let(::AccountUuid), wire, localChoice, selectedChanges)
                database().prepareBindingIntent(intent)
            }
            accountRequire(database().readBindingIntent(operation) == intent)
            accountRequire(intent.localDataChoice == localChoice && intent.selectedChangeIds == selectedChanges)
        }
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        try {
            // These SQL reads do not capture K or open a transaction. The same gate excludes every J mutation.
            val sql = database().reconciliationSnapshot()
            val snapshot = read(permit) { secure.snapshot() }
            accountRequire(snapshot == ticket.control)
            installation(snapshot, sql.storeId)
            val record = read(permit) { secure.read(ticket.slot, snapshot) } ?: reconciliation()
            record.use {
                accountRequire(it.owner == ticket.owner && it.owner.boot == secure.boot &&
                    it.owner.authorityRevision == snapshot.authorityRevision)
                if (wire in AccountBindingIntentCodec.GRANTS) {
                    val intent = database().readBindingIntent(AccountUuid(requireNotNull(it.owner.operation))) ?: reconciliation()
                    requireIntent(it, snapshot, intent)
                    accountRequire(sql.activationRevision == intent.priorActivationRevision)
                } else if (it.owner.role == SecureRole.AUTHENTICATED) {
                    val witness = it.owner.accountProjection ?: reconciliation()
                    accountRequire(witness.storeId == sql.storeId && witness.preparation.scope.scopeId == sql.selectedScope &&
                        witness.preparation.expectedActivationRevision + 1 == sql.activationRevision &&
                        witness.sessionFamily?.value == it.owner.family && witness.sessionGeneration == it.owner.sessionGeneration &&
                        witness.currentAuthorityRevision == snapshot.authorityRevision &&
                        (wire == PendingWireKind.SESSION_ROTATE || sql.rawActivationState == "ACTIVE"))
                } else if (wire == PendingWireKind.PORTABLE_ROTATE) {
                    accountRequire(snapshot.activeBinding == null && it.owner.protocolVersion == 1)
                    val root = read(permit) { secure.read(rootSlot, snapshot) } ?: reconciliation()
                    root.use { value ->
                        accountRequire(value.payload is PortableRootPayload && value.owner.account == it.owner.account)
                        value.owner.accountProjection?.let { witness -> accountRequire(witness.storeId == sql.storeId) }
                    }
                }
            }
            DispatchPin(permit)
        } catch (failure: Throwable) { permit.close(); throw failure }
    }

    private inner class DispatchPin(private val permit: SecureAuthorityGate.Permit) : SecureBindingDispatchPin {
        override fun <T> submit(action: () -> CompletableFuture<T>): CompletableFuture<T> = secure.accountGate.submit(permit, action)
        override fun close(): Unit = permit.close()
    }

    override fun cancelIntent(ticket: SecurePendingTicket): CompletableFuture<Unit> = task {
        accountRequire(ticket.owner.stage == SecureStage.PREPARED)
        val operation = AccountUuid(requireNotNull(ticket.owner.operation))
        if (database().readBindingIntent(operation) != null) database().cancelBindingIntent(operation)
        Unit
    }

    /** K already selected exact credentials + response. No ceremony or synthetic grant occurs here. */
    override fun commit(owner: SecureRecordOwner): CompletableFuture<Unit> = task {
        finishSelectedActivation(activateCredential(owner, freshAttempt = false))
        Unit
    }

    private suspend fun activateCredential(owner: SecureRecordOwner, freshAttempt: Boolean): AccountLease {
        val sql = database().reconciliationSnapshot()
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        val plan: AccountScopePreparation
        try {
            var snapshot = read(permit) { secure.snapshot() }
            installation(snapshot, sql.storeId)
            val record = read(permit) { secure.read(requireNotNull(owner.profile), snapshot) } ?: reconciliation()
            record.use {
                accountRequire(sameOwner(it.owner, owner) && it.payload is SessionPayload &&
                    !it.owner.deletionVeto && !it.owner.detached)
                val payload = it.payload as SessionPayload
                val existing = it.owner.accountProjection
                if (!freshAttempt && existing != null) {
                    plan = existing.preparation
                    val refreshed = existing.copy(capabilityRevision = payload.d1.revision, lifecycleAllowed = true,
                        currentAuthorityRevision = snapshot.authorityRevision)
                    if (it.owner.boot != secure.boot || existing != refreshed) snapshot = publishWitness(permit, snapshot, it, refreshed,
                        it.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision)) else validate(permit, snapshot, it, existing)
                } else {
                    val pending = pendingFor(permit, snapshot, it.owner)
                    try {
                        val original = pending?.payload as? PendingPayload
                        val intent = if (!freshAttempt && original?.wireKind in AccountBindingIntentCodec.GRANTS)
                            database().readBindingIntent(AccountUuid(requireNotNull(owner.operation))) ?: reconciliation() else null
                        val previous = existing ?: pending?.owner?.accountProjection
                        val remote = AccountRemoteOwner(AccountUuid(requireNotNull(owner.server)), AccountUuid(requireNotNull(owner.account)))
                        val knownScope = sql.scopes.singleOrNull { scope -> scope.identity.remote == remote }?.identity
                        val scope = if (intent != null) knownScope?.also {
                            accountRequire(it.portableAccountId == intent.portableAccountId)
                        } ?: AccountScopeIdentity(AccountUuid.random(), intent.portableAccountId, remote)
                        else previous?.preparation?.scope?.also { accountRequire(it.remote == remote) } ?: reconciliation()
                        val newDevice = intent != null && pending != null && pending.owner.device == null &&
                            it.owner.sessionGeneration == 0L && original?.wireKind in AccountBindingIntentCodec.GRANTS
                        if (intent != null) {
                            val protectedOriginal = pending ?: reconciliation()
                            accountRequire(protectedOriginal.owner.stage == SecureStage.BINDING_COMMIT_PENDING)
                            requireIntent(protectedOriginal, snapshot, intent, finalizing = true)
                            val receipt = original!!.receiptCopy()
                            try { accountRequire(receipt.isNotEmpty()) } finally { receipt.fill(0) }
                        }
                        val oldBinding = previous?.preparation?.binding
                        if (!newDevice) {
                            val retained = previous ?: reconciliation()
                            accountRequire(oldBinding != null && oldBinding.deviceId.value == owner.device &&
                                retained.preparation.bindingCommit.value == owner.bindingCommit && scope == retained.preparation.scope)
                        }
                        val epoch = if (newDevice) requireNotNull(intent).requestedJournalEpoch else requireNotNull(oldBinding).journalEpoch
                        val revision = snapshot.authorityRevision
                        plan = AccountScopePreparation(AccountUuid(requireNotNull(owner.bindingCommit)), scope,
                            AccountUuid(requireNotNull(owner.profile)), AccountRemoteBinding(AccountUuid(requireNotNull(owner.device)), epoch,
                                requireNotNull(owner.identityEpoch), requireNotNull(owner.identityThumbprint), requireNotNull(owner.keyThumbprint)),
                            intent?.authorityRevision ?: revision, sql.activationRevision, AccountUuid.random(), intent?.intentId)
                        val witness = AccountProtectedWitness(sql.storeId, plan, AccountProtectedStage.BINDING_COMMIT_PENDING,
                            AccountUuid(requireNotNull(owner.family)), requireNotNull(owner.sessionGeneration), payload.d1.revision, true, newDevice, revision)
                        snapshot = publishWitness(permit, snapshot, it, witness,
                            it.owner.copy(boot = secure.boot, authorityRevision = revision, stage = SecureStage.BINDING_COMMIT_PENDING))
                    } finally { pending?.close() }
                }
            }
        } finally { permit.close() }
        database().prepareScope(plan)
        return database().activateScope(plan.activationId).also { active.set(it) }
    }

    override fun matches(owner: SecureRecordOwner): CompletableFuture<Boolean> = task {
        val lease = active.get() ?: return@task false
        val sql = database().reconciliationSnapshot()
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        try {
            val snapshot = read(permit) { secure.snapshot() }
            val record = read(permit) { secure.read(requireNotNull(owner.profile), snapshot) } ?: return@task false
            record.use {
                val witness = it.owner.accountProjection ?: return@task false
                validate(permit, snapshot, it, witness)
                sameOwner(it.owner, owner) && witness.stage == AccountProtectedStage.READY &&
                    lease.storeId == sql.storeId && lease.scopeId == sql.selectedScope &&
                    lease.activationRevision == sql.activationRevision && sql.rawActivationState == "ACTIVE" &&
                    lease.authorityRevision == owner.authorityRevision && lease.sessionFamily == witness.sessionFamily &&
                    lease.sessionGeneration == witness.sessionGeneration && lease.capabilityRevision == witness.capabilityRevision
            }
        } finally { permit.close() }
    }

    /** Explicit local ROOT selection after the actual create/import/unlock flow; remote remains null. */
    override fun activateLocalRoot(): CompletableFuture<Unit> = activateRoot().thenApply { Unit }

    public fun activateRoot(): CompletableFuture<AccountLease> = task {
        await(external.quiesce())
        active.getAndSet(null)?.let { database().suspendScope(it) }
        val sql = database().reconciliationSnapshot()
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        val plan: AccountScopePreparation
        try {
            val snapshot = read(permit) { secure.snapshot() }
            accountRequire(snapshot.activeBinding == null)
            val root = read(permit) { secure.read(rootSlot, snapshot) } ?: reconciliation()
            root.use {
                accountRequire(it.payload is PortableRootPayload)
                val account = AccountUuid(requireNotNull(it.owner.account))
                val scope = sql.scopes.singleOrNull { scope -> scope.identity.remote == null && scope.identity.portableAccountId == account }?.identity
                    ?: AccountScopeIdentity(AccountUuid.random(), account, null)
                val prior = it.owner.accountProjection
                plan = if (prior != null && prior.stage != AccountProtectedStage.READY) prior.preparation.also { retained ->
                    accountRequire(retained.scope == scope && retained.expectedAuthorityRevision == snapshot.authorityRevision)
                } else AccountScopePreparation(AccountUuid.random(), scope,
                    prior?.preparation?.profileId ?: AccountUuid.random(), null, snapshot.authorityRevision,
                    sql.activationRevision, AccountUuid.random())
                publishWitness(permit, snapshot, it, AccountProtectedWitness(sql.storeId, plan,
                    AccountProtectedStage.BINDING_COMMIT_PENDING, null, 0, null, true, false),
                    it.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision))
            }
        } finally { permit.close() }
        database().prepareScope(plan)
        database().activateScope(plan.activationId).also { active.set(it); guardPublication.set(false) }
    }

    /** Caller has freshly verified exact current authority; this never repeats a credential ceremony. */
    public fun reconcileAfterRevalidation(original: SecureRecordOwner,
        revalidate: (SecureRecordOwner) -> CompletableFuture<Boolean>): CompletableFuture<AccountLease> = task {
        await(external.quiesce())
        active.getAndSet(null)?.let { database().suspendScope(it) }
        accountRequire(await(revalidate(original)))
        val snapshot = await(secure.snapshot())
        val current = await(secure.read(requireNotNull(original.profile), snapshot)) ?: reconciliation()
        val owner = current.use {
            accountRequire(it.owner.copy(boot = original.boot, authorityRevision = original.authorityRevision,
                stage = original.stage, accountProjection = null) == original.copy(accountProjection = null))
            it.owner
        }
        activateCredential(owner, freshAttempt = original.accountProjection?.stage == AccountProtectedStage.READY).also {
            finishSelectedActivation(it)
        }
    }

    override fun reconcile(owner: SecureRecordOwner, revalidate: (SecureRecordOwner) -> CompletableFuture<Boolean>): CompletableFuture<Unit> =
        reconcileAfterRevalidation(owner, revalidate).thenApply { Unit }

    /** Deny old SQL admission before publication, while leaving the exact originals and K alive. */
    public fun quiesceForGuardPublication(owner: SecureRecordOwner): CompletableFuture<Unit> {
        guardPublication.set(true)
        return task {
            await(external.quiesce())
            val permit = secure.accountGate.tryAcquire() ?: unavailable()
            try {
                val snapshot = read(permit) { secure.snapshot() }
                val record = read(permit) { secure.read(requireNotNull(owner.profile), snapshot) } ?: reconciliation()
                record.use { accountRequire(it.owner == owner && owner.authorityRevision == snapshot.authorityRevision &&
                    !owner.detached && !owner.deletionVeto) }
            } finally { permit.close() }
            active.getAndSet(null)?.let { database().suspendScope(it) }
            Unit
        }
    }

    /** Fresh connection-guard changes also need this new activation; the prior issued lease is never edited. */
    override fun reconcileGuards(owner: SecureRecordOwner): CompletableFuture<Unit> =
        reactivateAfterGuardPublication(owner).thenApply { Unit }

    public fun reactivateAfterGuardPublication(owner: SecureRecordOwner): CompletableFuture<AccountLease> = task {
        accountRequire(owner.accountProjection?.lifecycleAllowed == false && owner.stage == SecureStage.BINDING_COMMIT_PENDING)
        await(external.quiesce())
        active.getAndSet(null)?.let { database().suspendScope(it) }
        activateCredential(owner, freshAttempt = true).also { finishSelectedActivation(it) }
    }

    /** Reconcile exact saved grant locally, while keeping K non-ACTIVE, solely to permit refresh renewal. */
    override fun prepareExpiredBinding(owner: SecureRecordOwner): CompletableFuture<Unit> = task {
        await(external.quiesce())
        active.getAndSet(null)?.let { database().suspendScope(it) }
        val snapshot = await(secure.snapshot())
        val record = await(secure.read(requireNotNull(owner.profile), snapshot)) ?: reconciliation()
        record.use {
            val payload = it.payload as? SessionPayload ?: reconciliation()
            accountRequire(it.owner == owner && !owner.detached && !owner.deletionVeto && payload.hasCredentials &&
                payload.accessExpiresAt <= System.currentTimeMillis() / 1000 && payload.refreshExpiresAt > System.currentTimeMillis() / 1000)
        }
        activateCredential(owner, freshAttempt = owner.accountProjection?.stage == AccountProtectedStage.READY).also {
            finishSelectedActivation(it, selectActive = false)
        }
        Unit
    }

    public fun currentLease(): CompletableFuture<AccountLease> = task {
        accountRequire(!guardPublication.get())
        val lease = active.get() ?: unavailable()
        val sql = database().reconciliationSnapshot()
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        try {
            val snapshot = read(permit) { secure.snapshot() }
            val record = read(permit) { secure.read(if (lease.sessionFamily == null) rootSlot else lease.profileId.value, snapshot) } ?: reconciliation()
            record.use {
                val witness = it.owner.accountProjection ?: reconciliation()
                validate(permit, snapshot, it, witness)
                accountRequire(witness.stage == AccountProtectedStage.READY && witness.lifecycleAllowed &&
                    witness.storeId == lease.storeId && witness.preparation.scope.scopeId == lease.scopeId &&
                    witness.preparation.profileId == lease.profileId && witness.currentAuthorityRevision == lease.authorityRevision &&
                    witness.preparation.expectedActivationRevision + 1 == lease.activationRevision &&
                    witness.sessionFamily == lease.sessionFamily && witness.sessionGeneration == lease.sessionGeneration &&
                    witness.capabilityRevision == lease.capabilityRevision && sql.bootId == lease.bootId &&
                    sql.selectedScope == lease.scopeId && sql.activationRevision == lease.activationRevision && sql.rawActivationState == "ACTIVE" &&
                    (lease.sessionFamily == null || snapshot.activeBinding == record.slot && record.owner.stage == SecureStage.ACTIVE))
                lease
            }
        } finally { permit.close() }
    }

    /** External drain must finish before this fence; K remains alive until J actually closes. */
    public fun suspend(): CompletableFuture<Unit> = task {
        await(external.quiesce())
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        try {
            val snapshot = read(permit) { secure.snapshot() }
            read(permit) { secure.detach(snapshot) }
        } finally { permit.close() }
        capturesOpen.set(false)
        active.getAndSet(null)?.let { database().suspendScope(it) }
        Unit
    }

    public fun drainSettled(): CompletableFuture<Unit> = CompletableFuture.allOf(*retained.values.toTypedArray()).thenApply { Unit }

    /** May be retried after failure; partial connection/files/K lifetime remain owned until real close. */
    public fun closeStore(): CompletableFuture<Unit> {
        closing.set(true); capturesOpen.set(false)
        return task(allowClosing = true) {
            await(external.quiesce())
            active.set(null)
            database().retire(); database().close()
            closed.set(true); job.complete()
            Unit
        }
    }

    /** Finish only this retained response/readback. No POST, token issue, replay or new lineage. */
    private suspend fun finishSelectedActivation(lease: AccountLease, selectActive: Boolean = true): Unit {
        val permit = secure.accountGate.tryAcquire() ?: unavailable()
        val records = ArrayList<SecureRecord>()
        try {
            val sql = database().reconciliationSnapshot()
            accountRequire(sql.storeId == lease.storeId && sql.bootId == lease.bootId && sql.selectedScope == lease.scopeId &&
                sql.activationRevision == lease.activationRevision && sql.rawActivationState == "ACTIVE")
            val snapshot = read(permit) { secure.snapshot() }
            val session = read(permit) { secure.read(lease.profileId.value, snapshot) } ?: reconciliation()
            records.add(session)
            val witness = session.owner.accountProjection ?: reconciliation()
            accountRequire(witness.stage == AccountProtectedStage.READY && witness.storeId == lease.storeId &&
                witness.preparation.bindingCommit == lease.bindingCommit && witness.sessionFamily == lease.sessionFamily &&
                witness.sessionGeneration == lease.sessionGeneration && witness.capabilityRevision == lease.capabilityRevision)
            validate(permit, snapshot, session, witness)
            val pending = pendingFor(permit, snapshot, session.owner)
            if (pending != null) {
                if (pending.owner.stage == SecureStage.BINDING_COMMIT_PENDING) {
                    records.add(pending)
                    val receipt = (pending.payload as PendingPayload).receiptCopy()
                    try {
                        val response = SecureJson.parse(receipt, 65536, canonical = false)
                        accountRequire(response.string("session_id") == session.owner.session && response.string("platform") == "MACOS")
                        if ("device_id" in response) accountRequire(response.string("device_id") == session.owner.device)
                        if ("binding_commit_id" in response) accountRequire(response.string("binding_commit_id") == session.owner.bindingCommit)
                        if ((pending.payload as PendingPayload).wireKind == PendingWireKind.DELETION_CANCEL) {
                            val sourceSlot = requireNotNull(SecureRecordKind.DELETION_SOURCE.reserved)
                            val source = read(permit) { secure.read(sourceSlot, snapshot) }
                            if (source != null) {
                                if (source.owner.account == session.owner.account && source.owner.deletionVeto) {
                                    if (!SecureDeletionReconciliation.matches(source, pending, receipt)) { source.close(); reconciliation() }
                                    records.add(source)
                                } else source.close()
                            }
                        }
                    } finally { receipt.fill(0) }
                } else pending.close()
            }
            val completion = snapshot.reservations.singleOrNull { it.operation == session.owner.operation }
            val operation = completion?.operation ?: UUID.randomUUID().toString()
            val reserved = if (completion != null) {
                accountRequire(records.all { it.slot in completion.slots })
                read(permit) { secure.reserveCompletion(snapshot, completion.copy(revisions = maxOf(records.size, completion.revisions),
                    bytes = maxOf(records.size.toLong() * 262_144, completion.bytes))) }
            } else read(permit) { secure.reserveCompletion(snapshot, CompletionReservation(operation,
                records.map { it.slot }, records.size, records.size.toLong() * SecureStateLimits.VALUE)) }
            read(permit) { secure.publish(reserved, operation, activeBinding = if (selectActive) session.slot else null, terminal = true) {
                val output = ArrayList<SecureRecord>()
                try {
                    for (record in records) {
                        val bytes = SecureRecordCodec.encode(record)
                        val payload = try {
                            if (record.payload.kind == SecureRecordKind.DELETION_SOURCE) {
                                val receipt = (requireNotNull(pending).payload as PendingPayload).receiptCopy()
                                try { (record.payload as PendingPayload).duplicate(receipt) } finally { receipt.fill(0) }
                            } else SecureRecordCodec.decode(bytes).payload
                        } finally { bytes.fill(0) }
                        output.add(SecureRecord(record.slot, SecureStateLimits.next(record.revision), UUID.randomUUID().toString(),
                            record.owner.copy(boot = secure.boot, authorityRevision = reserved.authorityRevision,
                                stage = if (record === session) {
                                    if (selectActive) SecureStage.ACTIVE else SecureStage.BINDING_COMMIT_PENDING
                                } else SecureStage.TERMINAL,
                                deletionVeto = false), payload))
                    }
                    output
                } catch (failure: Throwable) { output.forEach { it.close() }; throw failure }
            } }
        } finally { records.forEach { it.close() }; permit.close() }
        if (selectActive) guardPublication.set(false)
    }

    private fun requireIntent(record: SecureRecord, snapshot: SecureControl, intent: AccountBindingIntent, finalizing: Boolean = false): Unit {
        val owner = record.owner
        val pending = record.payload as? PendingPayload ?: reconciliation()
        accountRequire((finalizing || owner.boot == secure.boot) &&
            (if (finalizing) snapshot.authorityRevision >= intent.authorityRevision else snapshot.authorityRevision == intent.authorityRevision) &&
            owner.authorityRevision == intent.authorityRevision && record.slot == intent.pendingSlot.value &&
            owner.operation == intent.operationId.value && owner.profile == intent.profileId.value &&
            owner.server == intent.serverInstanceId.value && owner.identityEpoch == intent.identityEpoch &&
            owner.identityThumbprint == intent.serverThumbprint && owner.keyThumbprint == intent.deviceKeyThumbprint &&
            owner.account == intent.knownUserId?.value && owner.device == intent.knownDeviceId?.value &&
            pending.wireKind == intent.wireKind && !owner.detached &&
            (!owner.deletionVeto || pending.wireKind == PendingWireKind.DELETION_CANCEL) &&
            snapshot.firstBind?.operation == owner.operation)
    }

    private fun validate(permit: SecureAuthorityGate.Permit, snapshot: SecureControl, record: SecureRecord,
        witness: AccountProtectedWitness): Unit {
        installation(snapshot, witness.storeId)
        accountRequire(record.owner.boot == secure.boot && record.owner.authorityRevision == snapshot.authorityRevision)
        SecureAccountProjectionCodec.requireOwner(witness, record.owner, record.payload)
        for (reference in snapshot.selected) {
            val other = read(permit) { secure.read(reference.slot, snapshot) } ?: reconciliation()
            other.use {
                val committedCancellation = it.payload is PendingPayload && (it.payload as PendingPayload).wireKind == PendingWireKind.DELETION_CANCEL &&
                    it.owner.operation == record.owner.operation && it.owner.stage == SecureStage.BINDING_COMMIT_PENDING &&
                    record.owner.stage == SecureStage.BINDING_COMMIT_PENDING
                val resolvedSource = if (it.payload.kind == SecureRecordKind.DELETION_SOURCE && it.owner.deletionVeto &&
                    record.owner.stage == SecureStage.BINDING_COMMIT_PENDING) {
                    val recipient = pendingFor(permit, snapshot, record.owner)
                    recipient?.use { selected ->
                        val response = (selected.payload as PendingPayload).receiptCopy()
                        try { SecureDeletionReconciliation.matches(it, selected, response) } finally { response.fill(0) }
                    } == true
                } else false
                accountRequire(it.owner.account != record.owner.account || !it.owner.deletionVeto || committedCancellation || resolvedSource)
            }
        }
        val payload = record.payload
        if (payload is SessionPayload) {
            accountRequire(payload.hasCredentials)
            val key = read(permit) { secure.publicKey(snapshot, requireNotNull(record.owner.keyTag)) }
            try { accountRequire(secureSha256(key) == record.owner.keyThumbprint) } finally { key.fill(0) }
            accountRequire(record.owner.stage in setOf(SecureStage.BINDING_COMMIT_PENDING, SecureStage.ACTIVE))
        }
    }

    private fun pendingFor(permit: SecureAuthorityGate.Permit, snapshot: SecureControl, owner: SecureRecordOwner): SecureRecord? {
        var found: SecureRecord? = null
        try {
            for (reference in snapshot.selected) {
                val record = read(permit) { secure.read(reference.slot, snapshot) } ?: reconciliation()
                if (record.payload is PendingPayload && record.owner.operation == owner.operation &&
                    record.owner.profile == owner.profile && record.owner.bindingCommit == owner.bindingCommit &&
                    record.owner.server == owner.server && record.owner.keyThumbprint == owner.keyThumbprint) {
                    if (found != null) { record.close(); reconciliation() }
                    found = record
                } else record.close()
            }
            return found
        } catch (failure: Throwable) { found?.close(); throw failure }
    }

    private fun publishWitness(permit: SecureAuthorityGate.Permit, snapshot: SecureControl, record: SecureRecord,
        witness: AccountProtectedWitness, owner: SecureRecordOwner = record.owner): SecureControl {
        val completion = snapshot.reservations.singleOrNull { it.operation == record.owner.operation && record.slot in it.slots }
        val operation = completion?.operation ?: UUID.randomUUID().toString()
        val required = if (witness.stage == AccountProtectedStage.READY) 3 else 4
        val reserved = if (completion != null) read(permit) { secure.reserveCompletion(snapshot,
            completion.copy(revisions = maxOf(required, completion.revisions), bytes = maxOf(required.toLong() * 262_144, completion.bytes))) }
        else read(permit) { secure.reserveCompletion(snapshot,
            CompletionReservation(operation, listOf(record.slot), 1, SecureStateLimits.VALUE.toLong())) }
        return read(permit) { secure.publish(reserved, operation, activeBinding = null, terminal = completion == null) {
            val bytes = SecureRecordCodec.encode(record)
            val payload = try { SecureRecordCodec.decode(bytes).payload } finally { bytes.fill(0) }
            listOf(SecureRecord(record.slot, SecureStateLimits.next(record.revision), UUID.randomUUID().toString(),
                owner.copy(accountProjection = witness), payload))
        } }
    }

    private fun installation(snapshot: SecureControl, id: AccountUuid): Unit = accountRequire(
        snapshot.jInstallation?.storeId == id.value && snapshot.jInstallation?.stage == SecureJInstallationStage.READY)
    private fun sameOwner(stored: SecureRecordOwner, expected: SecureRecordOwner): Boolean =
        stored.copy(stage = expected.stage, accountProjection = null) == expected.copy(accountProjection = null)
    private fun slot(plan: AccountScopePreparation): String = if (plan.binding == null) rootSlot else plan.profileId.value
    @Synchronized private fun database(): DesktopAccountStore = store ?: unavailable()
    private fun <T> read(permit: SecureAuthorityGate.Permit, action: () -> CompletableFuture<T>): T =
        await(secure.accountGate.submit(permit, action))
    private fun <T> await(future: CompletableFuture<T>): T {
        accountRequire(!secure.isNativeOwnerThread())
        return try { future.join() } catch (failure: CompletionException) { throw failure.cause ?: failure }
    }
    private fun <T> task(allowClosing: Boolean = false, action: suspend () -> T): CompletableFuture<T> {
        if (closed.get() || closing.get() && !allowClosing) return CompletableFuture.failedFuture(AccountStoreException(AccountFailure.ACCOUNT_CLOSED))
        if (!busy.compareAndSet(false, true)) return CompletableFuture.failedFuture(AccountStoreException(AccountFailure.ACCOUNT_BUSY))
        val actual = CompletableFuture<T>(); val id = UUID.randomUUID(); retained[id] = actual
        worker.launch {
            try { val value = action(); busy.set(false); retained.remove(id); actual.complete(value) }
            catch (failure: Throwable) {
                busy.set(false); retained.remove(id)
                actual.completeExceptionally(if (failure is AccountStoreException) failure else AccountStoreException(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED))
            }
        }
        return actual.minimalCompletionStage().toCompletableFuture()
    }
    private fun accountRequire(value: Boolean): Unit { if (!value) reconciliation() }
    private fun reconciliation(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    private fun unavailable(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
    private companion object { val rootSlot: String = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved) }
}

/** Allocation only. Retain both owners before initialize; failures never discard a partial startup. */
public class DesktopAccountAuthorityOwners private constructor(
    public val authority: DesktopAccountAuthority, public val store: DesktopAccountStore,
    private val secure: DesktopSecureStateOwner,
) {
    /** Account commands/files/HTTP -> authority -> durable fence -> actual J close -> actual K close. */
    public fun shutdown(controller: DesktopAccountController): CompletableFuture<Unit> {
        controller.close()
        return controller.drain().toCompletableFuture().thenCompose { authority.drainSettled() }
            .thenCompose { authority.suspend() }.thenCompose { authority.closeStore() }.thenCompose { secure.close() }
    }
    public companion object {
        public fun create(secure: DesktopSecureStateOwner, external: DesktopAccountExternalDrain): DesktopAccountAuthorityOwners {
            val authority = DesktopAccountAuthority(secure, external)
            val store = createDesktopAccountStore(authority)
            authority.attachStore(store)
            return DesktopAccountAuthorityOwners(authority, store, secure)
        }
    }
}
