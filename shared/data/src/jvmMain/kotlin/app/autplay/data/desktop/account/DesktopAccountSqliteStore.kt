package app.autplay.data.desktop.account

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import app.autplay.application.desktop.account.*
import java.nio.file.Files
import java.nio.file.Path
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Creates a resource owner WITHOUT opening a file/driver. The caller retains it before initialize,
 * including if its coroutine is cancelled; finally close it and do not abandon an uncertain close.
 * Production has no data-root override and requires an explicit approved external K implementation.
 * This source is UNVERIFIED: no driver, database, hash, test or native code was run to author it.
 */
fun createDesktopAccountStore(authority: DesktopAccountAuthorityPort): DesktopAccountStore =
    DesktopAccountSqliteStore(authority, AccountPrivateFiles(Path.of(System.getProperty("user.home"))))

/**
 * J1/M1 source owner: one serial connection for domain/Journal, sync pages, portable alias closure,
 * explicit selected materialization, catalog proofs, policy/review/work and typed media records.
 * Every child repository uses this owner's authority capture, transaction and physical budget.
 * Network, K custody, actual media/file leases, UI lifecycle and derived search belong to their
 * real host owners. No source grant, secret command or protected high-water is stored in J.
 * Inventory is descriptive; operator-assisted isolated-copy export and physical deletion remain
 * separately authorized procedures. All source and pinned-runtime qualification is UNVERIFIED.
 */
internal class DesktopAccountSqliteStore(
    private val authority: DesktopAccountAuthorityPort,
    private val files: AccountPrivateFiles,
    private val wallMs: () -> Long = System::currentTimeMillis,
    private val monotonicNanos: () -> Long = System::nanoTime,
    private val openConnection: (String) -> SQLiteConnection = { BundledSQLiteDriver().open(it) },
) : DesktopAccountStore, DesktopAccountSyncPages {
    override val syncPages: DesktopAccountSyncPages get() = this
    private val serial: AccountSerialOwner = AccountSerialOwner()
    private val bootId: AccountUuid = AccountUuid.random()
    private var protectedLifetime: AccountProtectedLifetime? = null
    private var connection: SQLiteConnection? = null
    private var attempted: Boolean = false
    private var initialized: Boolean = false
    private var uncertain: Boolean = false
    private var storeId: AccountUuid? = null
    private var activeLease: AccountLease? = null
    private var activeWitness: AccountProtectedWitness? = null
    private var lastIssuedActivationRevision: Long = -1
    private var lastIssuedLease: AccountLease? = null
    private var liveDelivery: AccountDeliveryClaim? = null
    private var liveDeliveryOperationId: AccountUuid? = null
    private var deliveryDeadlineNanos: Long = 0
    private var deliveryReconciliationRequired: Boolean = false
    private var livePage: AccountPageCapture? = null
    private var pageReconciliationRequired: Boolean = false
    private val pageSql: AccountPageSql = AccountPageSql(snapshotUncertain = { uncertain = true }) { db() }
    private val repositoryOwner: AccountRepositoryOwner = object : AccountRepositoryOwner {
        override suspend fun <T> read(lease: AccountLease, action: (AccountPageSql, AccountProtectedWitness) -> T): T =
            serial.submit { usable(); admitted(lease) { action(pageSql, it) } }

        override suspend fun <T> write(lease: AccountLease, operation: AccountUuid, kind: String, input: ByteArray,
            action: (AccountPageSql, AccountProtectedWitness, Boolean) -> T): T {
            val frozen = AccountSyncWireV1.boundedCopy(input)
            return serial.submit {
                usable(); noDelivery()
                admitted(lease) { witness ->
                    fun committed(): Boolean {
                        val old = query("SELECT kind,exact_input FROM j_repository_commit WHERE scope_id=? AND operation_id=?",
                            lease.scopeId, operation) { it.getText(0) to it.getBlob(1) }.singleOrNull() ?: return false
                        if (old.first != kind || !old.second.contentEquals(frozen)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                        return true
                    }
                    if (committed()) return@admitted action(pageSql, witness, true)
                    val result = transaction(progress = false, reserve = DesktopAccountLimits.PAGE_GROWTH_BYTES,
                        readback = { if (committed()) CommitReadback.APPLIED else CommitReadback.NOT_APPLIED }) {
                        pageSql.budgeted(DesktopAccountLimits.PAGE_GROWTH_BYTES, frozen.size) {
                            val value = action(pageSql, witness, false)
                            if(kind in setOf("DOMAIN","CHECKPOINT","SOURCE_COPY","SOURCE_MATERIALIZE","ROOT_INTENT_PREPARE","ROOT_INTENT_MATERIALIZE","PRESENTED","CONFLICT_REPAIR") &&
                                frozen.size.toLong()>DesktopAccountLimits.EVENT_COPY_BYTES-accountEventCopyBytes(pageSql))fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                            if(kind in setOf("PORTABLE_PAGE","PORTABLE_MERGE"))witness.preparation.scope.portableAccountId?.let{
                                AccountPortableMerge.capacity(pageSql,it,frozen.size.toLong())
                            }
                            pageSql.commitMarker("INSERT INTO j_repository_commit(scope_id,operation_id,kind,exact_input) VALUES(?,?,?,?)",
                                lease.scopeId, operation, kind, frozen)
                            value
                        }
                    }
                    if (!committed()) ambiguity()
                    result
                }
            }
        }
        override suspend fun <T> settle(lease: AccountLease, operation: AccountUuid, kind: String, input: ByteArray,
            action: (AccountPageSql, Boolean) -> T): T {
            val frozen = AccountSyncWireV1.boundedCopy(input)
            return serial.submitSettlement {
                usable()
                // Historical issued handle permits only exact drain CAS, never a new effect or admission.
                if (lease != lastIssuedLease || lease.storeId != id() || lease.bootId != bootId) fail(AccountFailure.ACCOUNT_LEASE_STALE)
                fun committed(): Boolean {
                    val old = query("SELECT kind,exact_input FROM j_repository_commit WHERE scope_id=? AND operation_id=?", lease.scopeId, operation) {
                        it.getText(0) to it.getBlob(1)
                    }.singleOrNull() ?: return false
                    if (old.first != kind || !old.second.contentEquals(frozen)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                    return true
                }
                if (committed()) return@submitSettlement action(pageSql, true)
                transaction(progress = true, reserve = 2_097_152,
                    readback = { if (committed()) CommitReadback.APPLIED else CommitReadback.NOT_APPLIED }) {
                    pageSql.budgeted(2_097_152, frozen.size) {
                        val result = action(pageSql, false)
                        pageSql.commitMarker("INSERT INTO j_repository_commit(scope_id,operation_id,kind,exact_input) VALUES(?,?,?,?)",
                            lease.scopeId, operation, kind, frozen)
                        result
                    }
                }
            }
        }
    }
    override val domain: DesktopAccountDomain = AccountDomainRepository(repositoryOwner)
    override val portableData: DesktopAccountPortableData = AccountPortableRepository(repositoryOwner)
    override val materialization: DesktopAccountMaterialization = AccountMaterializationRepository(repositoryOwner)
    override val accountIntents:DesktopAccountIntentMaterialization = AccountRootIntentRepository(repositoryOwner)
    override val media: DesktopAccountMedia = AccountMediaRepository(repositoryOwner)
    override val catalog: DesktopAccountCatalog = AccountCatalogRepository(repositoryOwner)
    override val interactions:DesktopAccountInteractions = AccountInteractionRepository(repositoryOwner)
    override val maintenance:DesktopAccountMaintenance = AccountMaintenanceRepository(repositoryOwner)
    override val recoveryReads:DesktopAccountRecoveryReads = AccountRecoveryReadRepository(repositoryOwner)
    override val importReviewCommands:DesktopAccountImportReviewCommands = AccountImportReviewRepository(repositoryOwner)

    override suspend fun initialize(): AccountReconciliationSnapshot = serial.submit {
        if (initialized) return@submit snapshot()
        if (attempted) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        attempted = true
        val lifetime = authority.tryAcquireLifetime() ?: fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        protectedLifetime = lifetime
        val protectedState = lifetime.storeState
        if (protectedState is AccountProtectedStoreState.Unavailable) fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        val existed = files.acquire()
        if (!existed && protectedState !is AccountProtectedStoreState.Reserved) {
            fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
        if (existed && Files.size(files.database) == 0L) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        if (!existed) files.createNewDatabase()
        val opened = openConnection(files.database.toString())
        connection = opened
        // Refuse an unknown database BEFORE changing persistent pragmas or installing tables.
        if (existed) {
            if (number("PRAGMA application_id") != DesktopAccountSchema.APPLICATION_ID ||
                number("PRAGMA user_version") != DesktopAccountSchema.VERSION) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            val identity = query("SELECT store_id,format,schema_version FROM j_store WHERE singleton=1") {
                Triple(AccountUuid(it.getText(0)), it.getText(1), it.getLong(2))
            }.singleOrNull() ?: fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            if (identity.second != DesktopAccountSchema.FORMAT || identity.third != DesktopAccountSchema.VERSION) {
                fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            }
            val protectedId = when (protectedState) {
                is AccountProtectedStoreState.Existing -> protectedState.storeId
                is AccountProtectedStoreState.Reserved -> protectedState.storeId
                else -> null
            }
            if (protectedId != identity.first) {
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            storeId = identity.first
        } else {
            opened.execSQL("PRAGMA page_size=4096")
            storeId = (protectedState as AccountProtectedStoreState.Reserved).storeId
        }
        configure()
        if (!existed) transaction(progress = true, reserve = 1_048_576) {
            for (statement in DesktopAccountSchema.create) db().execSQL(statement)
            for (statement in DesktopAccountPageSchema.create) db().execSQL(statement)
            execute("INSERT INTO j_schema_component(name,version) VALUES(?,1)", DesktopAccountPageSchema.COMPONENT)
            execute("INSERT INTO j_store(singleton,store_id,format,schema_version,last_wall_ms) VALUES(1,?,?,1,?)",
                id(), DesktopAccountSchema.FORMAT, now())
            execute("INSERT INTO j_activation(singleton,revision,work_revision,state) VALUES(1,0,0,'SUSPENDED')")
            db().execSQL("PRAGMA application_id=0x41504A31")
            db().execSQL("PRAGMA user_version=1")
        }
        completeInitialPageSchema()
        if (number("PRAGMA application_id") != DesktopAccountSchema.APPLICATION_ID ||
            number("PRAGMA user_version") != DesktopAccountSchema.VERSION) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        files.verify()
        // A reserved initialization can finish only after the exact owned SQL marker is readable.
        if (!lifetime.completeInstallation(id())) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        // OLD ACTIVE is never a boot authorization. Unknown states remain exactly as stored.
        if (number("SELECT count(*) FROM j_event_delivery WHERE state='IN_FLIGHT'") > DesktopAccountLimits.PAGE) {
            fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
        transaction(progress = true, reserve = 2_097_152) {
            execute("UPDATE j_activation SET state='SUSPENDED',boot_id=NULL WHERE state='ACTIVE'")
            execute("UPDATE j_scope SET state='SUSPENDED' WHERE state='ACTIVE'")
            execute("""UPDATE j_event_delivery SET state=CASE WHEN attempt>=8 THEN 'DEAD_LETTER' ELSE 'PENDING' END,
                next_retry_ms=max(next_retry_ms,coalesce(lease_expires_ms,0)),lease_boot=NULL,lease_token=NULL,
                lease_expires_ms=NULL WHERE state='IN_FLIGHT' AND (lease_boot IS NULL OR lease_boot<>?)""", bootId)
            execute("UPDATE j_work SET state='UNCERTAIN' WHERE state='RUNNING' AND boot_id<>?", bootId)
            execute("""UPDATE j_media_attempt SET state='UNCERTAIN',stop_reason='UNCERTAIN'
                WHERE state IN ('PREPARED','WAITING','RUNNING','DRAINING') AND boot_id<>?""", bootId)
        }
        files.verify()
        val result = snapshot()
        initialized = true
        result
    }

    override suspend fun reconciliationSnapshot(): AccountReconciliationSnapshot = serial.submit { usable(); snapshot() }

    override suspend fun prepareBindingIntent(intent: AccountBindingIntent): AccountBindingIntent = serial.submit {
        val intent = AccountBindingIntentCodec.decode(AccountBindingIntentCodec.encode(intent))
        usable(); noDelivery(); noExternalWork()
        val capture = protectedLifetime?.tryCaptureIntent(id(), intent)
            ?: fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        try {
            val bytes = AccountBindingIntentCodec.encode(intent)
            val existing = bindingIntent(intent.intentId)
            if (existing != null) {
                if (existing != intent) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                return@submit existing
            }
            if (activationRevision() != intent.priorActivationRevision ||
                number("SELECT count(*) FROM j_binding_intent WHERE stage='PREPARED'") != 0L ||
                number("SELECT count(*) FROM j_scope_prepare WHERE stage='PREPARED'") != 0L) {
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            if (number("SELECT count(*) FROM j_binding_intent") + number("SELECT count(*) FROM j_scope_prepare") >=
                DesktopAccountLimits.WORK - 1) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
            activeLease = null; activeWitness = null
            transaction(progress = false, reserve = 262_144) {
                execute("""INSERT INTO j_binding_intent(intent_id,operation_id,pending_slot,authority_revision,
                    prior_activation_revision,exact_intent,stage) VALUES(?,?,?,?,?,?,'PREPARED')""",
                    intent.intentId, intent.operationId, intent.pendingSlot, intent.authorityRevision,
                    intent.priorActivationRevision, bytes)
                execute("UPDATE j_activation SET state='SUSPENDED',boot_id=NULL WHERE singleton=1")
                execute("UPDATE j_scope SET state='SUSPENDED' WHERE state='ACTIVE'")
            }
            if (bindingIntent(intent.intentId) != intent) ambiguity()
            intent
        } finally { capture.close() }
    }

    override suspend fun readBindingIntent(intentId: AccountUuid): AccountBindingIntent? = serial.submit {
        usable(); bindingIntent(intentId)
    }

    override suspend fun cancelBindingIntent(intentId: AccountUuid): Unit = serial.submit {
        usable(); noDelivery(); noExternalWork()
        val intent = bindingIntent(intentId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (text("SELECT stage FROM j_binding_intent WHERE intent_id=?", intentId) == "CANCELLED_UNSENT") return@submit
        if (text("SELECT stage FROM j_binding_intent WHERE intent_id=?", intentId) != "PREPARED") {
            fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
        val capture = protectedLifetime?.tryCaptureIntentCancellation(id(), intent)
            ?: fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        try {
            transaction(progress = true, reserve = 131_072) {
                execute("UPDATE j_binding_intent SET stage='CANCELLED_UNSENT' WHERE intent_id=? AND stage='PREPARED' AND descendant_activation IS NULL", intentId)
                changedOne()
            }
            if (text("SELECT stage FROM j_binding_intent WHERE intent_id=?", intentId) != "CANCELLED_UNSENT") ambiguity()
        } finally { capture.close() }
    }

    private fun bindingIntent(intentId: AccountUuid): AccountBindingIntent? = query(
        "SELECT exact_intent FROM j_binding_intent WHERE intent_id=?", intentId) {
        AccountBindingIntentCodec.decode(it.getBlob(0))
    }.singleOrNull()

    override suspend fun prepareScope(preparation: AccountScopePreparation): AccountReconciliationSnapshot = serial.submit {
        usable()
        noDelivery()
        noExternalWork()
        captured(preparation, activation = true) { witness, _ ->
            if (!witness.lifecycleAllowed) {
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            }
            val existing = readPreparation(preparation.activationId)
            if (existing != null) {
                if (existing.first != preparation || existing.second !in setOf("PREPARED", "APPLIED")) {
                    fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                }
                if (activationRevision() != preparation.expectedActivationRevision + 1 ||
                    text("SELECT activation_id FROM j_activation WHERE singleton=1") != preparation.activationId.value) {
                    fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                }
                return@captured snapshot()
            }
            val revision = activationRevision()
            if (preparation.expectedActivationRevision != revision) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            if (number("SELECT count(*) FROM j_scope_prepare WHERE stage NOT IN ('APPLIED','ABORTED')") != 0L) {
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            val unresolvedIntent = query("SELECT intent_id FROM j_binding_intent WHERE stage='PREPARED' LIMIT 2") { AccountUuid(it.getText(0)) }
            if (unresolvedIntent.any { it != preparation.intentId }) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            if (number("SELECT count(*) FROM j_binding_intent") + number("SELECT count(*) FROM j_scope_prepare") >=
                DesktopAccountLimits.WORK) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
            preparation.intentId?.let { intentId ->
                val intent = bindingIntent(intentId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                val binding = preparation.binding ?: fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                val remote = preparation.scope.remote ?: fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                if (intent.authorityRevision != preparation.expectedAuthorityRevision ||
                    intent.priorActivationRevision != preparation.expectedActivationRevision ||
                    intent.profileId != preparation.profileId || intent.portableAccountId != preparation.scope.portableAccountId ||
                    intent.serverInstanceId != remote.serverInstanceId || intent.identityEpoch != binding.identityEpoch ||
                    intent.serverThumbprint != binding.serverThumbprint || intent.deviceKeyThumbprint != binding.deviceKeyThumbprint ||
                    intent.requestedJournalEpoch != binding.journalEpoch ||
                    intent.knownUserId?.let { it != remote.userId } == true ||
                    intent.knownDeviceId?.let { it != binding.deviceId } == true) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                if (text("SELECT stage FROM j_binding_intent WHERE intent_id=?", intentId) != "PREPARED") {
                    fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                }
            }
            val state = text("SELECT state FROM j_activation WHERE singleton=1")
            if (state !in setOf("SUSPENDED", "ACTIVE")) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            // All previously admitted SQL has settled on this executor. The external gate owns other drains.
            activeLease = null
            activeWitness = null
            transaction(progress = false, reserve = 262_144) {
                ensureScope(preparation.scope)
                ensureProfileCompatible(preparation.scope.scopeId, preparation.profileId)
                val binding = preparation.binding
                execute("""INSERT INTO j_scope_prepare(activation_id,binding_commit,intent_id,scope_id,profile_id,device_id,journal_epoch,
                    identity_epoch,server_thumbprint,device_key_thumbprint,authority_revision,prior_activation_revision,stage)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'PREPARED')""",
                    preparation.activationId, preparation.bindingCommit, preparation.intentId,
                    preparation.scope.scopeId, preparation.profileId, binding?.deviceId,
                    binding?.journalEpoch, binding?.identityEpoch, binding?.serverThumbprint, binding?.deviceKeyThumbprint,
                    preparation.expectedAuthorityRevision, revision)
                execute("UPDATE j_scope SET state='SUSPENDED' WHERE state='ACTIVE'")
                preparation.intentId?.let { intentId ->
                    execute("UPDATE j_binding_intent SET stage='FINALIZED',descendant_activation=? WHERE intent_id=? AND stage='PREPARED'",
                        preparation.activationId, intentId)
                    changedOne()
                }
                execute("""UPDATE j_activation SET revision=?,binding_commit=?,activation_id=?,state='PREPARED',boot_id=NULL
                    WHERE singleton=1 AND revision=?""", revision + 1, preparation.bindingCommit, preparation.activationId, revision)
                changedOne()
            }
            if (readPreparation(preparation.activationId)?.first != preparation || activationRevision() != revision + 1) {
                ambiguity()
            }
            snapshot()
        }
    }

    override suspend fun activateScope(activationId: AccountUuid): AccountLease = serial.submit {
        usable()
        noDelivery()
        noExternalWork()
        val (plan, stage) = readPreparation(activationId) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (stage !in setOf("PREPARED", "APPLIED")) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        captured(plan, activation = true) { witness, capture ->
            if (!witness.lifecycleAllowed || witness.stage !in setOf(AccountProtectedStage.BINDING_COMMIT_PENDING, AccountProtectedStage.READY)) {
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
            }
            val expectedRevision = plan.expectedActivationRevision + 1
            if (activationRevision() != expectedRevision ||
                text("SELECT activation_id FROM j_activation WHERE singleton=1") != plan.activationId.value) {
                fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            }
            if (text("SELECT state FROM j_activation WHERE singleton=1") !in setOf("PREPARED", "SUSPENDED", "ACTIVE")) {
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            val current = activeLease
            if (current != null) {
                val prior = activeWitness
                if (prior != null && sameCurrent(prior, witness) && witness.stage == AccountProtectedStage.READY) {
                    activationReadback(plan, expectedRevision, witness.currentAuthorityRevision)
                    return@captured current
                }
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            }
            // Reconciliation may finish a never-issued commit or a new boot. A retired lease within
            // this boot requires a NEW preparation/revision, even if selection returns A-B-A.
            if (expectedRevision <= lastIssuedActivationRevision) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            activeLease = null
            activeWitness = null
            transaction(progress = true, reserve = 524_288) {
                val lineage = ensureLineage(plan, witness.permitsNewDeviceLineage)
                ensureAlias(plan, lineage)
                if (lineage != null) ensureSyncCursor(plan.scope.scopeId, plan.profileId, lineage)
                execute("UPDATE j_scope_prepare SET stage='APPLIED' WHERE activation_id=? AND stage IN ('PREPARED','APPLIED')", plan.activationId)
                changedOne()
                execute("UPDATE j_scope SET state='ACTIVE' WHERE scope_id=? AND state IN ('PREPARED','SUSPENDED','ACTIVE')", plan.scope.scopeId)
                changedOne()
                execute("""UPDATE j_activation SET scope_id=?,profile_id=?,authority_revision=?,boot_id=?,state='ACTIVE'
                    WHERE singleton=1 AND revision=? AND activation_id=?""", plan.scope.scopeId, plan.profileId,
                    witness.currentAuthorityRevision, bootId, expectedRevision, plan.activationId)
                changedOne()
            }
            val readback = activationReadback(plan, expectedRevision, witness.currentAuthorityRevision)
            // No SQL transaction is open. This is a reconciliation completion, NOT an atomic K+J commit.
            val completed = capture.completeReconciliation(readback) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            if (!sameCurrent(witness, completed) || completed.stage != AccountProtectedStage.READY || !completed.lifecycleAllowed) {
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            activationReadback(plan, expectedRevision, completed.currentAuthorityRevision)
            val lease = AccountLease(id(), plan.scope.scopeId, plan.profileId, bootId, expectedRevision,
                plan.bindingCommit, completed.currentAuthorityRevision, completed.sessionFamily, completed.sessionGeneration,
                completed.capabilityRevision)
            activeWitness = completed
            activeLease = lease
            lastIssuedActivationRevision = expectedRevision
            lastIssuedLease = lease
            lease
        }
    }

    override suspend fun suspendScope(lease: AccountLease): AccountReconciliationSnapshot = serial.submit {
        usable()
        noDelivery()
        noExternalWork()
        // Suspension must remain available after K becomes unavailable/vetoed; it grants no authority.
        if (lease != lastIssuedLease || lease.storeId != id() || lease.bootId != bootId ||
            number("""SELECT count(*) FROM j_activation WHERE singleton=1 AND scope_id=? AND profile_id=?
                AND revision=? AND binding_commit=? AND authority_revision=?""", lease.scopeId, lease.profileId,
                lease.activationRevision, lease.bindingCommit, lease.authorityRevision) != 1L) {
            fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        }
        activeLease = null
        activeWitness = null
        transaction(progress = true, reserve = 131_072) {
            execute("UPDATE j_activation SET state='SUSPENDED',boot_id=NULL WHERE singleton=1 AND revision=? AND scope_id=?",
                lease.activationRevision, lease.scopeId)
            changedOne()
            execute("UPDATE j_scope SET state='SUSPENDED' WHERE scope_id=? AND state='ACTIVE'", lease.scopeId)
        }
        snapshot()
    }

    override suspend fun createTrack(lease: AccountLease, change: AccountCreateTrack): AccountMutationReceipt = serial.submit {
        usable()
        noDelivery()
        admitted(lease) { witness ->
            val plan = witness.preparation
            val prior = existingMutation(lease.scopeId, change.operationId)
            if (prior != null) {
                if (!prior.matches(change)) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                return@admitted prior.receipt
            }
            var receipt: AccountMutationReceipt? = null
            transaction(progress = false, reserve = 262_144) {
                val remote = plan.scope.remote
                val binding = plan.binding
                val lineage = if (remote != null && binding != null) {
                    lineage(plan) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                } else null
                val event = if (lineage != null && remote != null && binding != null) {
                    val sequence = number("SELECT next_sequence FROM j_lineage WHERE scope_id=? AND lineage_id=?", lease.scopeId, lineage)
                    if (sequence >= DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                    val generated = AccountEventV1.createTrack(change, remote, binding, lease.profileId, sequence)
                    eventCapacity(generated.byteSize.toLong())
                    execute("UPDATE j_lineage SET next_sequence=? WHERE scope_id=? AND lineage_id=? AND next_sequence=?",
                        sequence + 1, lease.scopeId, lineage, sequence)
                    changedOne()
                    execute("""INSERT INTO j_event(scope_id,event_id,lineage_id,profile_id,user_id,device_id,sequence,
                        idempotency_key,event_type,aggregate_type,aggregate_local_id,canonical_bytes,request_hash)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", lease.scopeId, generated.eventId, lineage, generated.profileId,
                        generated.userId, generated.deviceId, sequence, generated.idempotencyKey, generated.eventType,
                        generated.aggregateType, generated.aggregateLocalId, generated.canonicalBytes, generated.requestHash)
                    execute("INSERT INTO j_event_delivery(scope_id,event_id,state,attempt,next_retry_ms) VALUES(?,?,'PENDING',0,0)",
                        lease.scopeId, generated.eventId)
                    generated
                } else {
                    val payload = AccountEventV1.trackPayload(change)
                    eventCapacity(payload.size.toLong())
                    execute("""INSERT INTO j_local_intent(scope_id,change_id,profile_id,aggregate_local_id,event_type,
                        aggregate_type,schema_version,occurred_at_ms,canonical_payload,state)
                        VALUES(?,?,?,?,'USER_TRACK_REF_CREATED','USER_TRACK_REF',1,?,?,'LOCAL')""",
                        lease.scopeId, change.operationId, lease.profileId, change.trackRefId, change.occurredAtMs, payload)
                    val localSequence=next(number("SELECT coalesce(max(local_sequence),0) FROM j_local_intent_order WHERE scope_id=?",lease.scopeId))
                    execute("INSERT INTO j_local_intent_order(scope_id,change_id,local_sequence) VALUES(?,?,?)",lease.scopeId,change.operationId,localSequence)
                    null
                }
                execute("""INSERT INTO j_track_ref(scope_id,track_ref_id,operation_id,profile_id,title,artist,occurred_at_ms)
                    VALUES(?,?,?,?,?,?,?)""", lease.scopeId, change.trackRefId, change.operationId, lease.profileId,
                    change.title, change.artist, change.occurredAtMs)
                if (number("SELECT count(*) FROM j_library_relation WHERE scope_id=? AND entry_id=?", lease.scopeId, change.libraryEntryId) != 0L) {
                    fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
                }
                execute("INSERT INTO j_library_entry(scope_id,entry_id,track_ref_id) VALUES(?,?,?)",
                    lease.scopeId, change.libraryEntryId, change.trackRefId)
                receipt = AccountMutationReceipt(change.operationId, change.trackRefId, change.libraryEntryId,
                    event?.eventId, if (event == null) change.operationId else null)
            }
            val readback = existingMutation(lease.scopeId, change.operationId)
            if (readback == null || !readback.matches(change) || readback.receipt != receipt) ambiguity()
            checkNotNull(receipt)
        }
    }

    override suspend fun readTracks(lease: AccountLease, after: AccountUuid?, limit: Int): List<AccountTrackProjection> = serial.submit {
        usable()
        if (limit !in 1..DesktopAccountLimits.PAGE) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        admitted(lease) {
            query("""SELECT t.track_ref_id,l.entry_id,t.title,t.artist,t.server_track_ref_id,t.observed_version
                FROM j_track_ref t JOIN (SELECT * FROM j_library_entry UNION ALL SELECT * FROM j_library_relation) l
                    ON l.scope_id=t.scope_id AND l.track_ref_id=t.track_ref_id
                LEFT JOIN j_account_projection_visibility ts ON ts.scope_id=t.scope_id AND ts.aggregate_type='USER_TRACK_REF' AND ts.local_id=t.track_ref_id
                LEFT JOIN j_account_projection_visibility ls ON ls.scope_id=l.scope_id AND ls.aggregate_type='LIBRARY_ENTRY' AND ls.local_id=l.entry_id
                WHERE t.scope_id=? AND (? IS NULL OR t.track_ref_id>?) AND coalesce(ts.effective_deleted,0)=0 AND coalesce(ls.effective_deleted,0)=0
                AND (ts.state IS NULL OR ts.state='COMPLETE') AND (ls.state IS NULL OR ls.state='COMPLETE')
                AND (ts.snapshot_confirmed IS NULL OR ts.snapshot_confirmed=1 OR ts.pending=1)
                AND (ls.snapshot_confirmed IS NULL OR ls.snapshot_confirmed=1 OR ls.pending=1)
                ORDER BY t.track_ref_id LIMIT ?""",
                lease.scopeId, after, after, limit) {
                AccountTrackProjection(AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), it.getText(2), it.getText(3),
                    nullableText(it, 4)?.let(::AccountUuid), nullableLong(it, 5))
            }
        }
    }

    override suspend fun readDelivery(lease: AccountLease, after: AccountUuid?, limit: Int): List<AccountDeliveryStatus> = serial.submit {
        usable()
        if (limit !in 1..DesktopAccountLimits.PAGE) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        admitted(lease) {
            query("""SELECT e.event_id,e.lineage_id,e.profile_id,e.sequence,d.state,d.attempt,d.next_retry_ms,d.last_error,d.lease_revision
                FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                WHERE e.scope_id=? AND (? IS NULL OR e.event_id>?) ORDER BY e.event_id LIMIT ?""",
                lease.scopeId, after, after, limit) {
                AccountDeliveryStatus(AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), AccountUuid(it.getText(2)),
                    it.getLong(3), it.getText(4), it.getLong(5).toInt(), it.getLong(6), nullableText(it, 7),it.getLong(8))
            }
        }
    }

    override fun claimPending(lease: AccountLease): AccountDeliveryOperation<AccountDeliveryClaim?> = serial.startDelivery { operationId, committed ->
        usable()
        noDelivery()
        admitted(lease) { witness ->
            val binding = witness.preparation.binding ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
            val lineage = lineage(witness.preparation) ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            val time = conservativeNow()
            val checkpoint = number("SELECT ack_checkpoint FROM j_lineage WHERE scope_id=? AND lineage_id=?", lease.scopeId, lineage)
            val allocated = number("SELECT next_sequence FROM j_lineage WHERE scope_id=? AND lineage_id=?", lease.scopeId, lineage)
            var expectedSequence = checkpoint + 1
            val events = ArrayList<AccountEventV1>()
            var bytes = 1_024L // Reserve fixed v1 push binding/envelope bytes as well as commas.
            // Examine the WHOLE lineage, including unavailable delivery rows and immutable old aliases.
            // No ACK is inferred from a terminal label; only the separately applied checkpoint counts.
            db().prepare("""SELECT e.canonical_bytes,e.request_hash,e.event_id,e.sequence,e.user_id,e.device_id,
                e.profile_id,e.idempotency_key,e.event_type,e.aggregate_type,e.aggregate_local_id,
                d.state,d.attempt,d.next_retry_ms
                FROM j_event e LEFT JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
                WHERE e.scope_id=? AND e.lineage_id=? AND e.sequence>? ORDER BY e.sequence LIMIT 100""").use { statement ->
                bind(statement, arrayOf(lease.scopeId, lineage, checkpoint))
                // Stream directly into the byte-bounded batch; never retain 100 maximal event blobs.
                while (statement.step()) {
                    if (statement.getLong(3) != expectedSequence || statement.getText(6) != lease.profileId.value ||
                        nullableText(statement, 11) != "PENDING" || statement.isNull(12) || statement.getLong(12) >= 8) {
                        if (events.isEmpty()) fail(AccountFailure.ACCOUNT_DELIVERY_BLOCKED)
                        break
                    }
                    if (statement.getLong(13) > time) break // A delayed head blocks every later sequence.
                    val event = AccountEventV1.fromCanonical(statement.getBlob(0))
                    if (!event.requestHash.contentEquals(statement.getBlob(1)) || event.eventId.value != statement.getText(2) ||
                        event.sequence != statement.getLong(3) || event.userId.value != statement.getText(4) ||
                        event.deviceId.value != statement.getText(5) || event.profileId.value != statement.getText(6) ||
                        event.idempotencyKey != statement.getText(7) || event.eventType != statement.getText(8) ||
                        event.aggregateType != statement.getText(9) || event.aggregateLocalId.value != statement.getText(10) ||
                        event.userId != witness.preparation.scope.remote?.userId ||
                        event.deviceId != binding.deviceId || event.profileId != lease.profileId) ambiguity()
                    if (bytes + event.byteSize + 1 > DesktopAccountLimits.PAGE_BYTES) {
                        if (events.isEmpty()) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                        break
                    }
                    events.add(event)
                    bytes += event.byteSize + 1
                    expectedSequence++
                }
            }
            if (events.isEmpty()) {
                if (expectedSequence < allocated && number("""SELECT count(*) FROM j_event
                    WHERE scope_id=? AND lineage_id=? AND sequence=?""", lease.scopeId, lineage, expectedSequence) != 1L) {
                    fail(AccountFailure.ACCOUNT_DELIVERY_BLOCKED)
                }
                return@admitted null
            }
            // One active pass prevents competing J growth. Reserve its bounded ACK/defer outcome
            // before exposing any scheduling identity to the external transport owner.
            // Hold ACK derivation plus claim/status overhead BEFORE dispatch; the separate 16 MiB
            // progress reserve remains available for rollback deferral and exact token settlement.
            reserveExternalOutcome(DesktopAccountLimits.PAGE_GROWTH_BYTES)
            val token = AccountUuid.random()
            val revision = nextWorkRevision()
            val expires = boundedAdd(time, DesktopAccountLimits.LEASE_MS)
            val claim = AccountDeliveryClaim(AccountDeliveryLease(lease, lineage, lease.profileId, token, revision, expires), events)
            val deadline = monotonicNanos() + DesktopAccountLimits.LEASE_MS * 1_000_000
            deliveryTransaction(claim, progress = false) {
                execute("UPDATE j_activation SET work_revision=? WHERE singleton=1 AND work_revision=?", revision, revision - 1)
                changedOne()
                for (event in events) {
                    execute("""UPDATE j_event_delivery SET state='IN_FLIGHT',attempt=attempt+1,lease_boot=?,
                        lease_token=?,lease_revision=?,lease_expires_ms=? WHERE scope_id=? AND event_id=?
                        AND state='PENDING' AND attempt<8 AND next_retry_ms<=?""", bootId, token, revision, expires,
                        lease.scopeId, event.eventId, time)
                    changedOne()
                }
                rememberWall(time)
            }
            deliveryDeadlineNanos = deadline
            liveDelivery = claim
            liveDeliveryOperationId = operationId
            committed(claim.lease) // Survives captured().finally, but only as a drain-only failed outcome.
            claim
        }
    }

    override fun renewDelivery(lease: AccountDeliveryLease): AccountDeliveryOperation<AccountDeliveryLease> = serial.startDelivery { operationId, committed ->
        usable()
        val claim = exactDelivery(lease)
        admitted(lease.account) {
            val time = conservativeNow()
            if (monotonicNanos() - deliveryDeadlineNanos >= 0 || time >= lease.expiresAtMs) fail(AccountFailure.ACCOUNT_LEASE_STALE)
            val revision = nextWorkRevision()
            val renewed = lease.copy(revision = revision, expiresAtMs = boundedAdd(time, DesktopAccountLimits.LEASE_MS))
            val renewedClaim = AccountDeliveryClaim(renewed, claim.events)
            val deadline = monotonicNanos() + DesktopAccountLimits.LEASE_MS * 1_000_000
            deliveryTransaction(claim, progress = true) {
                execute("UPDATE j_activation SET work_revision=? WHERE singleton=1 AND work_revision=?", revision, revision - 1)
                changedOne()
                for (event in claim.events) {
                    execute("""UPDATE j_event_delivery SET lease_revision=?,lease_expires_ms=? WHERE scope_id=? AND event_id=?
                        AND state='IN_FLIGHT' AND lease_boot=? AND lease_token=? AND lease_revision=?""", revision,
                        renewed.expiresAtMs, lease.account.scopeId, event.eventId, bootId, lease.token, lease.revision)
                    changedOne()
                }
                rememberWall(time)
            }
            liveDelivery = renewedClaim
            liveDeliveryOperationId = operationId
            deliveryDeadlineNanos = deadline
            committed(renewed) // Exact successor, never the now-stale input renewal revision.
            renewed
        }
    }

    override suspend fun failDelivery(lease: AccountDeliveryLease, reason: AccountDeliveryFailure, retryAfterMs: Long): Unit =
        serial.submitSettlement { finishDelivery(lease, reason, retryAfterMs) }

    override suspend fun settleDelivery(handle: AccountDeliverySettlement, reason: AccountDeliveryFailure, retryAfterMs: Long): Unit =
        serial.submitSettlement { finishDelivery(handle.lease, reason, retryAfterMs, handle.operationId) }

    private fun finishDelivery(
        lease: AccountDeliveryLease, reason: AccountDeliveryFailure, retryAfterMs: Long, operationId: AccountUuid? = null,
    ): Unit {
        // Settlement grants no authority and must drain a known token even after an unrelated
        // authority/capture failure retired normal admissions. Unknown SQL delivery outcome blocks.
        if (!initialized || deliveryReconciliationRequired) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        files.verify()
        if (retryAfterMs !in 0..DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        if (operationId != null && operationId != liveDeliveryOperationId) fail(AccountFailure.ACCOUNT_LEASE_STALE)
        val claim = exactDelivery(lease)
        // A late scheduling failure may settle ONLY this original token. It never authorizes transport.
        val time = maxOf(now(), number("SELECT last_wall_ms FROM j_store WHERE singleton=1"))
        deliveryTransaction(claim, progress = true) {
            for (event in claim.events) {
                val attempts = number("SELECT attempt FROM j_event_delivery WHERE scope_id=? AND event_id=?",
                    lease.account.scopeId, event.eventId)
                val state = when {
                    attempts >= DesktopAccountLimits.ATTEMPTS -> "DEAD_LETTER"
                    reason == AccountDeliveryFailure.AUTHORITY_UNAVAILABLE -> "AUTHORITY_BLOCKED"
                    reason == AccountDeliveryFailure.UNSUPPORTED -> "UNSUPPORTED"
                    reason == AccountDeliveryFailure.CONFLICT -> "CONFLICT"
                    else -> "PENDING"
                }
                val delay = maxOf(minOf(3_600_000L, 1_000L shl (attempts.toInt() - 1).coerceIn(0, 7)), retryAfterMs)
                execute("""UPDATE j_event_delivery SET state=?,next_retry_ms=?,last_error=?,lease_boot=NULL,
                    lease_token=NULL,lease_expires_ms=NULL WHERE scope_id=? AND event_id=? AND state='IN_FLIGHT'
                    AND lease_boot=? AND lease_token=? AND lease_revision=?""", state, boundedAdd(time, delay),
                    reason.name, lease.account.scopeId, event.eventId, bootId, lease.token, lease.revision)
                changedOne()
            }
            rememberWall(time)
        }
        liveDelivery = null
        liveDeliveryOperationId = null
    }

    override suspend fun readSyncState(lease: AccountLease): AccountSyncState = serial.submit {
        usable()
        admitted(lease) { syncState(lease) }
    }

    override suspend fun readProjectionPage(lease: AccountLease, kind: AccountProjectionKind, after: AccountUuid?, limit: Int): List<AccountDomainProjection> = serial.submit {
        usable()
        if (limit !in 1..DesktopAccountLimits.PAGE) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        admitted(lease) { AccountProjectionReader(pageSql, lease.scopeId).read(kind, after, limit) }
    }

    private fun syncState(lease: AccountLease): AccountSyncState = query("""SELECT c.lineage_id,l.device_id,l.journal_epoch,
        c.revision,c.opaque_cursor,c.server_sequence,c.state,b.snapshot_id,b.page_token,b.final_cursor,b.high_water,b.state,
        (SELECT count(*) FROM j_event e JOIN j_event_delivery d ON d.scope_id=e.scope_id AND d.event_id=e.event_id
            WHERE e.scope_id=c.scope_id AND e.lineage_id=c.lineage_id AND d.state NOT IN ('ACKNOWLEDGED','REJECTED'))
        FROM j_sync_cursor c JOIN j_lineage l ON l.scope_id=c.scope_id AND l.lineage_id=c.lineage_id
        JOIN j_bootstrap b ON b.scope_id=c.scope_id AND b.profile_id=c.profile_id
        WHERE c.scope_id=? AND c.profile_id=?""", lease.scopeId, lease.profileId) {
        AccountSyncState(AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), AccountUuid(it.getText(2)), it.getLong(3),
            nullableText(it, 4), it.getLong(5), it.getText(6), nullableText(it, 7)?.let(::AccountUuid),
            nullableText(it, 8), nullableText(it, 9), nullableLong(it, 10), it.getText(11), it.getLong(12))
    }.singleOrNull() ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)

    override fun capturePage(lease: AccountLease, kind: AccountPageKind): AccountPageOperation<AccountPageCapture> =
        serial.startPage { operation, retained ->
            usable()
            noDelivery()
            admitted(lease) { witness ->
                val state = syncState(lease)
                val remote = witness.preparation.scope.remote ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                val binding = witness.preparation.binding ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                if (state.deviceId != binding.deviceId || state.journalEpoch != binding.journalEpoch) ambiguity()
                if (state.revision >= DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
                if (kind == AccountPageKind.PULL && (state.state !in setOf("READY", "DEFERRED") || state.bootstrapState != "READY")) {
                    fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                }
                if (kind == AccountPageKind.BOOTSTRAP && (state.state !in setOf("NOT_STARTED", "RESET_REQUIRED", "BOOTSTRAPPING", "DEFERRED") ||
                        state.bootstrapState !in setOf("NOT_STARTED", "RESET_REQUIRED", "BOOTSTRAPPING"))) {
                    fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
                }
                reserveExternalOutcome(DesktopAccountLimits.PAGE_GROWTH_BYTES)
                val capture = AccountPageCapture(operation, lease, remote.userId, kind, state)
                livePage = capture
                retained(capture) // Recover exact drain-only capture after capture.close failure.
                capture
            }
        }

    private fun exactPage(capture: AccountPageCapture): Unit {
        if (livePage != capture || capture.account.bootId != bootId) fail(AccountFailure.ACCOUNT_LEASE_STALE)
        if (syncState(capture.account) != capture.original) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }

    override suspend fun settlePage(capture: AccountPageCapture): Unit = serial.submitSettlement {
        // No K call or new authorization. An uncertain SQL result retains the owner for reopen.
        if (pageReconciliationRequired) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (livePage != capture || capture.account.bootId != bootId) fail(AccountFailure.ACCOUNT_LEASE_STALE)
        livePage = null
    }

    override fun applyPullPage(capture: AccountPageCapture, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt> =
        applyServerPage(capture, AccountSyncWireV1.boundedCopy(wireBytes), AccountPageKind.PULL)

    override fun applyBootstrapPage(capture: AccountPageCapture, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt> =
        applyServerPage(capture, AccountSyncWireV1.boundedCopy(wireBytes), AccountPageKind.BOOTSTRAP)

    private fun applyServerPage(capture: AccountPageCapture, bytes: ByteArray, kind: AccountPageKind): AccountPageOperation<AccountPageReceipt> =
        serial.startPage { operation, retained ->
            usable()
            exactPage(capture)
            if (capture.kind != kind) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
            admitted(capture.account) { witness ->
                val binding = witness.preparation.binding ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                if (witness.preparation.scope.remote?.userId != capture.userId ||
                    binding.deviceId != capture.original.deviceId ||
                    binding.journalEpoch != capture.original.journalEpoch) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                val root = AccountSyncWireV1.document(bytes) // Unsafe/malformed raw bytes are never archived.
                AccountSyncWireV1.binding(root, capture.original.deviceId, capture.account.profileId, capture.original.journalEpoch)
                if ("user_id" in root && root.id("user_id") != capture.userId) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                requireDeferredReplay(capture.account, kind.name, bytes)
                val page = try {
                    if (kind == AccountPageKind.PULL) AccountSyncWireV1.pull(root, capture) else AccountSyncWireV1.bootstrap(root, capture)
                } catch (failure: AccountStoreException) {
                    if (failure.failure != AccountFailure.ACCOUNT_INPUT_INVALID) throw failure
                    null // Safe but structurally unsupported response: exact bounded deferral below.
                } catch (_: IllegalArgumentException) { null }
                var deferred: String? = if (page == null) "INVALID_PAGE_SHAPE" else null
                var result: AccountPageReceipt? = null
                if (page != null) try {
                    result = pageTransaction(operation, capture.account, capture.original.lineageId, kind.name, bytes) {
                        val writer = AccountProjectionWriter(pageSql, capture.account.scopeId, capture.account.profileId,
                            capture.original.lineageId, operation, if (kind == AccountPageKind.BOOTSTRAP) page.snapshot else null)
                        for (change in page.changes) {
                            if (change.event != null) {
                                val known = query("""SELECT event_id,server_sequence,exact_event FROM j_applied_event
                                    WHERE scope_id=? AND profile_id=? AND (event_id=? OR server_sequence=?) LIMIT 2""",
                                    capture.account.scopeId, capture.account.profileId, change.event, change.sequence) {
                                    Triple(it.getText(0), it.getLong(1), it.getBlob(2))
                                }
                                val eventBytes = change.original.toString().toByteArray(Charsets.UTF_8)
                                if (known.isNotEmpty()) {
                                    if (known.size != 1 || known[0].first != change.event.value || known[0].second != change.sequence ||
                                        !known[0].third.contentEquals(eventBytes)) throw AccountPageDeferred("CONTRADICTORY_SERVER_EVENT")
                                    continue
                                }
                                if (checkNotNull(change.sequence) <= capture.original.serverSequence) throw AccountPageDeferred("SERVER_SEQUENCE_REGRESSION")
                            }
                            writer.apply(change)
                            if (kind == AccountPageKind.BOOTSTRAP && change.operation == "UPSERT" &&
                                change.type !in AccountCatalogProjectionWriter.families) {
                                val record = change.original.toString().toByteArray(Charsets.UTF_8)
                                val member = query("""SELECT safe_record FROM j_bootstrap_member WHERE scope_id=? AND profile_id=?
                                    AND snapshot_id=? AND aggregate_type=? AND server_id=?""", capture.account.scopeId,
                                    capture.account.profileId, page.snapshot, change.type, change.server) { it.getBlob(0) }.singleOrNull()
                                if (member != null && !member.contentEquals(record)) throw AccountPageDeferred("CONTRADICTORY_SNAPSHOT_MEMBER")
                                if (member == null) pageSql.mutate("j_bootstrap_member", listOf(capture.account.scopeId, capture.account.profileId, page.snapshot, change.type, change.server), """INSERT INTO j_bootstrap_member(scope_id,profile_id,snapshot_id,aggregate_type,server_id,safe_record)
                                    VALUES(?,?,?,?,?,?)""", capture.account.scopeId, capture.account.profileId, page.snapshot, change.type, change.server, record)
                            }
                            if (kind == AccountPageKind.BOOTSTRAP && change.tombstone != null) {
                                pageSql.mutate("j_bootstrap_tombstone", listOf(capture.account.scopeId, capture.account.profileId, page.snapshot, change.tombstone.id), """INSERT INTO j_bootstrap_tombstone(scope_id,profile_id,snapshot_id,tombstone_id)
                                    VALUES(?,?,?,?) ON CONFLICT(scope_id,profile_id,snapshot_id,tombstone_id) DO NOTHING""",
                                    capture.account.scopeId, capture.account.profileId, page.snapshot, change.tombstone.id)
                            }
                            if (change.event != null) pageSql.mutate("j_applied_event", listOf(capture.account.scopeId, capture.account.profileId, change.event), """INSERT INTO j_applied_event(scope_id,profile_id,event_id,server_sequence,exact_event)
                                VALUES(?,?,?,?,?)""", capture.account.scopeId, capture.account.profileId, change.event, change.sequence,
                                change.original.toString().toByteArray(Charsets.UTF_8))
                        }
                        if (kind == AccountPageKind.BOOTSTRAP) {
                            val priorHighWater = number("""SELECT coalesce(max(value),0) FROM (
                                SELECT high_water AS value FROM j_snapshot_head WHERE scope_id=?
                                UNION ALL SELECT server_sequence AS value FROM j_sync_cursor WHERE scope_id=?)""",
                                capture.account.scopeId, capture.account.scopeId)
                            if (checkNotNull(page.highWater) < priorHighWater) throw AccountPageDeferred("SNAPSHOT_HIGH_WATER_REGRESSION")
                            if (!page.more && unresolvedDependencies(capture.account, page.snapshot) != 0L) {
                                throw AccountPageDeferred("SNAPSHOT_DEPENDENCY_INCOMPLETE")
                            }
                            pageSql.mutate("j_bootstrap", listOf(capture.account.scopeId, capture.account.profileId), """UPDATE j_bootstrap SET snapshot_id=?,page_token=?,final_cursor=?,high_water=?,state=?
                                WHERE scope_id=? AND profile_id=?""", page.snapshot, page.next, page.finalCursor, page.highWater,
                                if (page.more) "BOOTSTRAPPING" else "READY", capture.account.scopeId, capture.account.profileId)
                            changedOne()
                            if (!page.more) pageSql.mutate("j_snapshot_head", listOf(capture.account.scopeId), """INSERT INTO j_snapshot_head(scope_id,profile_id,snapshot_id,high_water) VALUES(?,?,?,?)
                                ON CONFLICT(scope_id) DO UPDATE SET profile_id=excluded.profile_id,snapshot_id=excluded.snapshot_id,high_water=excluded.high_water""",
                                capture.account.scopeId, capture.account.profileId, page.snapshot, page.highWater)
                        }
                        if (kind == AccountPageKind.PULL && unresolvedDependencies(capture.account, null) != 0L) {
                            throw AccountPageDeferred("PULL_DEPENDENCY_INCOMPLETE")
                        }
                        val state = if (kind == AccountPageKind.BOOTSTRAP && page.more) "BOOTSTRAPPING" else "READY"
                        val cursor = if (kind == AccountPageKind.PULL) page.next else if (page.more) capture.original.cursor else page.finalCursor
                        val sequence = if (kind == AccountPageKind.PULL) maxOf(capture.original.serverSequence,
                            page.changes.maxOfOrNull { it.sequence ?: 0 } ?: 0) else if (page.more) capture.original.serverSequence else checkNotNull(page.highWater)
                        if (sequence < capture.original.serverSequence) throw AccountPageDeferred("SNAPSHOT_HIGH_WATER_REGRESSION")
                        pageSql.mutate("j_sync_cursor", listOf(capture.account.scopeId, capture.account.profileId), """UPDATE j_sync_cursor SET revision=revision+1,opaque_cursor=?,server_sequence=?,state=?
                            WHERE scope_id=? AND profile_id=? AND lineage_id=? AND revision=?""", cursor, sequence, state,
                            capture.account.scopeId, capture.account.profileId, capture.original.lineageId, capture.original.revision)
                        changedOne()
                        pageSql.mutate("j_deferred_page", listOf(capture.account.scopeId, capture.account.profileId), "UPDATE j_deferred_page SET state='RESOLVED' WHERE scope_id=? AND profile_id=? AND state='BLOCKED'",
                            capture.account.scopeId, capture.account.profileId)
                        AccountPageReceipt(operation, "APPLIED", page.more, null)
                    }
                } catch (failure: AccountPageDeferred) { deferred = failure.reason }
                catch (failure: AccountStoreException) {
                    deferred = when (failure.failure) {
                        AccountFailure.ACCOUNT_INPUT_INVALID -> "INVALID_PROJECTION_SHAPE"
                        AccountFailure.ACCOUNT_STORAGE_FULL -> "ACCOUNT_STORAGE_FULL"
                        else -> throw failure
                    }
                }
                if (deferred != null) result = deferPage(operation, capture.account, capture.original.lineageId, kind.name, bytes, checkNotNull(deferred))
                val receipt = checkNotNull(result)
                livePage = null // Actual external response and SQL have settled; no capture grants authority.
                retained(receipt)
                receipt
            }
        }

    override fun resetPage(capture: AccountPageCapture, reason: AccountPageReset): AccountPageOperation<AccountPageReceipt> =
        serial.startPage { operation, retained ->
            usable()
            exactPage(capture)
            admitted(capture.account) {
                if (reason == AccountPageReset.BOOTSTRAP_SNAPSHOT_INVALID &&
                    (capture.kind != AccountPageKind.BOOTSTRAP || capture.original.bootstrapState != "BOOTSTRAPPING")) {
                    fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                }
                val result = pageTransaction(operation, capture.account, capture.original.lineageId, "RESET_${reason.name}", null) {
                    // Retain the old incremental cursor, snapshot/page/final evidence, pending events and allocator.
                    pageSql.mutate("j_sync_cursor", listOf(capture.account.scopeId, capture.account.profileId), "UPDATE j_sync_cursor SET state='RESET_REQUIRED',revision=revision+1 WHERE scope_id=? AND profile_id=? AND revision=?",
                        capture.account.scopeId, capture.account.profileId, capture.original.revision)
                    changedOne()
                    pageSql.mutate("j_bootstrap", listOf(capture.account.scopeId, capture.account.profileId), "UPDATE j_bootstrap SET state='RESET_REQUIRED' WHERE scope_id=? AND profile_id=?", capture.account.scopeId, capture.account.profileId)
                    changedOne()
                    pageSql.mutate("j_deferred_page", listOf(capture.account.scopeId, capture.account.profileId), "UPDATE j_deferred_page SET state='RESET_RETAINED' WHERE scope_id=? AND profile_id=? AND state='BLOCKED'",
                        capture.account.scopeId, capture.account.profileId)
                    AccountPageReceipt(operation, "RESET_REQUIRED", false, null)
                }
                livePage = null
                retained(result)
                result
            }
        }

    override fun applyAckPage(delivery: AccountDeliveryLease, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt> {
        val bytes = AccountSyncWireV1.boundedCopy(wireBytes)
        return serial.startPage { operation, retained ->
            usable()
            if (livePage != null) fail(AccountFailure.ACCOUNT_BUSY)
            val claim = exactDelivery(delivery)
            admitted(delivery.account) { witness ->
                val binding = witness.preparation.binding ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
                val root = AccountSyncWireV1.document(bytes)
                if ("user_id" in root && root.id("user_id") != witness.preparation.scope.remote?.userId) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                requireDeferredReplay(delivery.account, "ACK", bytes)
                val page = AccountSyncWireV1.acks(root, binding.deviceId, delivery.originalProfileId, binding.journalEpoch)
                val byId = page.acks.associateBy { it.event }
                if (byId.size != page.acks.size || page.acks.any { ack -> claim.events.none { it.eventId == ack.event } }) {
                    fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                }
                val checkpoint = number("SELECT ack_checkpoint FROM j_lineage WHERE scope_id=? AND lineage_id=?", delivery.account.scopeId, delivery.lineageId)
                val allocated = number("SELECT next_sequence FROM j_lineage WHERE scope_id=? AND lineage_id=?", delivery.account.scopeId, delivery.lineageId)
                if (page.through !in checkpoint until allocated) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                val time = maxOf(now(), number("SELECT last_wall_ms FROM j_store WHERE singleton=1"))
                val result = try { pageTransaction(operation, delivery.account, delivery.lineageId, "ACK", bytes) {
                    val writer = AccountProjectionWriter(pageSql, delivery.account.scopeId, delivery.originalProfileId, delivery.lineageId, operation)
                    for (event in claim.events) {
                        val ack = byId[event.eventId]
                        if (ack != null && (ack.sequence != event.sequence || ack.local != event.aggregateLocalId ||
                                ack.type != event.aggregateType || event.profileId != delivery.originalProfileId ||
                                event.userId != witness.preparation.scope.remote?.userId || event.deviceId != binding.deviceId)) {
                            fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                        }
                        val terminal = ack != null && ack.effective in setOf("APPLIED", "CONFLICT", "REJECTED") &&
                            ack.outcome in setOf("APPLIED", "CONFLICT", "REJECTED", "DUPLICATE") &&
                            !(ack.effective == "REJECTED" && (ack.retryable || ack.error in setOf("JOURNAL_RESET_REQUIRED",
                                "EVENT_HASH_MISMATCH", "DEVICE_SEQUENCE_REUSE", "IDEMPOTENCY_KEY_REUSE", "DEVICE_SEQUENCE_GAP", "BATCH_SEQUENCE_NOT_ASCENDING"))) &&
                            event.sequence <= page.through
                        if (ack != null && ack.effective in setOf("APPLIED", "CONFLICT") && !terminal) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                        if (terminal) {
                            val value = checkNotNull(ack)
                            if (value.effective in setOf("APPLIED", "CONFLICT")) writer.acknowledge(event, value)
                            if (value.effective == "CONFLICT") {
                                val conflict = checkNotNull(value.conflict)
                                // The exact complete ACK/snapshot/additive bytes are retained ONCE
                                // in j_page_result. This bounded evidence links that immutable reply.
                                val evidence = JsonObject(mapOf("conflict_kind" to checkNotNull(conflict["conflict_kind"]),
                                    "resolution_state" to checkNotNull(conflict["resolution_state"]),
                                    "page_operation_id" to JsonPrimitive(operation.value),
                                    "ack_index" to JsonPrimitive(page.acks.indexOf(value))) +
                                    conflict.filterKeys { it == "server_event_id" })
                                writer.conflict(value.type, value.local, value.server, conflict.string("conflict_kind", 100),
                                    evidence, value.version, event.baseServerRowVersion, conflict.string("resolution_state", 100), event.eventId)
                            }
                            pageSql.mutate("j_ack", listOf(delivery.account.scopeId, event.eventId), """INSERT INTO j_ack(scope_id,event_id,outcome,terminal,server_id,observed_version,raw_outcome,page_operation_id,ack_index)
                                VALUES(?,?,?,1,?,?,?,?,?)""", delivery.account.scopeId, event.eventId, value.effective,
                                value.server, value.version, value.outcome, operation, page.acks.indexOf(value))
                        }
                        val attempts = number("SELECT attempt FROM j_event_delivery WHERE scope_id=? AND event_id=?", delivery.account.scopeId, event.eventId)
                        val state = when {
                            terminal && ack?.effective == "APPLIED" -> "ACKNOWLEDGED"
                            terminal && ack?.effective == "CONFLICT" -> "CONFLICT"
                            terminal -> "REJECTED"
                            attempts >= DesktopAccountLimits.ATTEMPTS -> "DEAD_LETTER"
                            else -> "PENDING"
                        }
                        val retry = maxOf(minOf(3_600_000L, 1_000L shl (attempts.toInt() - 1).coerceIn(0, 7)), ack?.retryAfter ?: 0)
                        pageSql.mutate("j_event_delivery", listOf(delivery.account.scopeId, event.eventId), """UPDATE j_event_delivery SET state=?,next_retry_ms=?,last_error=?,lease_boot=NULL,lease_token=NULL,lease_expires_ms=NULL
                            WHERE scope_id=? AND event_id=? AND state='IN_FLIGHT' AND lease_boot=? AND lease_token=? AND lease_revision=?""",
                            state, if (terminal) 0 else boundedAdd(time, retry), if (terminal) null else "ACK_RETRY_REQUIRED",
                            delivery.account.scopeId, event.eventId, bootId, delivery.token, delivery.revision)
                        changedOne()
                    }
                    var through = checkpoint
                    val prefix = query("""SELECT e.sequence,a.terminal FROM j_event e LEFT JOIN j_ack a ON a.scope_id=e.scope_id AND a.event_id=e.event_id
                        WHERE e.scope_id=? AND e.lineage_id=? AND e.sequence>? ORDER BY e.sequence LIMIT 201""",
                        delivery.account.scopeId, delivery.lineageId, checkpoint) { it.getLong(0) to nullableLong(it, 1) }
                    for ((sequence, terminal) in prefix) {
                        if (sequence != through + 1 || terminal != 1L) break
                        through = sequence
                    }
                    if (through > page.through) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                    pageSql.mutate("j_lineage", listOf(delivery.account.scopeId, delivery.lineageId), "UPDATE j_lineage SET ack_checkpoint=? WHERE scope_id=? AND lineage_id=? AND ack_checkpoint=?",
                        through, delivery.account.scopeId, delivery.lineageId, checkpoint)
                    changedOne()
                    pageSql.mutate("j_store", listOf(1), "UPDATE j_store SET last_wall_ms=max(last_wall_ms,?) WHERE singleton=1", time)
                    pageSql.mutate("j_sync_cursor", listOf(delivery.account.scopeId, delivery.originalProfileId), """UPDATE j_sync_cursor
                        SET state=CASE (SELECT state FROM j_bootstrap b WHERE b.scope_id=j_sync_cursor.scope_id AND b.profile_id=j_sync_cursor.profile_id)
                            WHEN 'READY' THEN 'READY' WHEN 'NOT_STARTED' THEN 'NOT_STARTED'
                            WHEN 'BOOTSTRAPPING' THEN 'BOOTSTRAPPING' WHEN 'RESET_REQUIRED' THEN 'RESET_REQUIRED' ELSE 'DEFERRED' END
                        WHERE scope_id=? AND profile_id=? AND state='DEFERRED' AND EXISTS(SELECT 1 FROM j_deferred_page d
                            WHERE d.scope_id=j_sync_cursor.scope_id AND d.profile_id=j_sync_cursor.profile_id AND d.kind='ACK' AND d.state='BLOCKED')""",
                        delivery.account.scopeId, delivery.originalProfileId)
                    pageSql.mutate("j_deferred_page", listOf(delivery.account.scopeId, delivery.originalProfileId),
                        "UPDATE j_deferred_page SET state='RESOLVED' WHERE scope_id=? AND profile_id=? AND kind='ACK' AND state='BLOCKED'",
                        delivery.account.scopeId, delivery.originalProfileId)
                    AccountPageReceipt(operation, "ACK_APPLIED", false, through)
                } } catch (failure: AccountPageDeferred) {
                    deferPage(operation, delivery.account, delivery.lineageId, "ACK", bytes, failure.reason)
                } catch (failure: AccountStoreException) {
                    if (failure.failure != AccountFailure.ACCOUNT_STORAGE_FULL) throw failure
                    deferPage(operation, delivery.account, delivery.lineageId, "ACK", bytes, "ACCOUNT_STORAGE_FULL")
                }
                // A complex ACK has NOT settled its delivery rows. The caller retains the exact
                // lease and must use failDelivery/settleDelivery after actual external settlement.
                if (result.state == "ACK_APPLIED") {
                    liveDelivery = null
                    liveDeliveryOperationId = null
                }
                retained(result)
                result
            }
        }
    }

    /** Validate every supported FK without allocating a whole-snapshot member/reference list. */
    private fun unresolvedDependencies(lease: AccountLease, snapshot: AccountUuid?): Long {
        data class Reference(val table: String, val childType: String, val childKey: String, val parentKey: String, val parentType: String)
        val references = listOf(
            Reference("j_library_entry", "LIBRARY_ENTRY", "entry_id", "track_ref_id", "USER_TRACK_REF"),
            Reference("j_library_relation", "LIBRARY_ENTRY", "entry_id", "track_ref_id", "USER_TRACK_REF"),
            Reference("j_preference", "USER_TRACK_PREFERENCE", "preference_id", "track_ref_id", "USER_TRACK_REF"),
            Reference("j_listening", "LISTENING_EVENT", "listening_id", "track_ref_id", "USER_TRACK_REF"),
            Reference("j_playlist_entry", "PLAYLIST_ENTRY", "entry_id", "track_ref_id", "USER_TRACK_REF"),
            Reference("j_playlist_entry", "PLAYLIST_ENTRY", "entry_id", "playlist_id", "PLAYLIST"),
        )
        for (ref in references) {
            val arguments = arrayListOf<Any?>(lease.scopeId)
            val current = if (snapshot == null) "(r.snapshot_confirmed IS NULL OR r.snapshot_confirmed=1)" else {
                arguments.add(lease.profileId); arguments.add(snapshot)
                """EXISTS(SELECT 1 FROM j_bootstrap_member m WHERE m.scope_id=r.scope_id AND m.profile_id=?
                    AND m.snapshot_id=? AND m.aggregate_type=r.aggregate_type AND m.server_id=r.server_id)"""
            }
            val parentTable = if (ref.parentType == "USER_TRACK_REF") "j_track_ref" else "j_playlist"
            val parentKey = if (ref.parentType == "USER_TRACK_REF") "track_ref_id" else "playlist_id"
            val membership = if (snapshot == null) "1" else {
                arguments.add(lease.profileId); arguments.add(snapshot)
                """EXISTS(SELECT 1 FROM j_bootstrap_member m WHERE m.scope_id=p.scope_id AND m.profile_id=?
                    AND m.snapshot_id=? AND m.aggregate_type=p.aggregate_type AND m.server_id=p.server_id)"""
            }
            // _owned_row checks existence/owner, not deleted_at: history and live library/track
            // relations may legitimately refer to a deleted UTR. Bootstrap excludes deleted
            // playlists AND their entries, so a live snapshot entry needs a live playlist member.
            val tombstone = if (snapshot == null) """EXISTS(SELECT 1 FROM j_tombstone t WHERE t.scope_id=p.scope_id
                AND t.aggregate_type=p.aggregate_type AND t.server_id=p.server_id)""" else if (ref.parentType == "PLAYLIST") "0" else {
                arguments.add(lease.profileId); arguments.add(snapshot)
                """EXISTS(SELECT 1 FROM j_bootstrap_tombstone b JOIN j_tombstone t
                    ON t.scope_id=b.scope_id AND t.tombstone_id=b.tombstone_id WHERE b.scope_id=p.scope_id
                    AND b.profile_id=? AND b.snapshot_id=? AND t.aggregate_type=p.aggregate_type AND t.server_id=p.server_id)"""
            }
            if (number("""SELECT EXISTS(SELECT 1 FROM ${ref.table} c JOIN j_account_projection_visibility r
                ON r.scope_id=c.scope_id AND r.aggregate_type='${ref.childType}' AND r.local_id=c.${ref.childKey}
                WHERE c.scope_id=? AND r.pending=0 AND $current AND NOT EXISTS(
                    SELECT 1 FROM j_projection_state p JOIN $parentTable d ON d.scope_id=p.scope_id AND d.$parentKey=p.local_id
                    WHERE p.scope_id=c.scope_id AND p.aggregate_type='${ref.parentType}' AND p.local_id=c.${ref.parentKey}
                    AND ((p.state='COMPLETE' AND $membership) OR (p.state IN ('COMPLETE','DEPENDENCY') AND $tombstone))))""",
                    *arguments.toTypedArray()) != 0L) return 1
        }
        return 0
    }

    private fun requireDeferredReplay(lease: AccountLease, kind: String, bytes: ByteArray): Unit {
        val blocked = query("""SELECT d.kind,d.state,r.safe_wire FROM j_deferred_page d JOIN j_page_result r ON r.operation_id=d.operation_id
            WHERE d.scope_id=? AND d.profile_id=?""",
            lease.scopeId, lease.profileId) { Triple(it.getText(0), it.getText(1), it.getBlob(2)) }.singleOrNull()
        if (blocked != null && blocked.second !in setOf("BLOCKED", "RESOLVED", "RESET_RETAINED")) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (blocked != null && blocked.first !in setOf("PULL", "BOOTSTRAP", "ACK")) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (blocked != null && blocked.second == "BLOCKED" && (blocked.first != kind || !blocked.third.contentEquals(bytes))) fail(AccountFailure.ACCOUNT_DELIVERY_BLOCKED)
    }

    private fun deferPage(operation: AccountUuid, lease: AccountLease, lineage: AccountUuid, kind: String, bytes: ByteArray, reason: String): AccountPageReceipt =
        pageTransaction(operation, lease, lineage, kind, bytes, progress = true, deferral = true) {
            pageSql.mutate("j_deferred_page", listOf(lease.scopeId, lease.profileId), """INSERT INTO j_deferred_page(scope_id,profile_id,operation_id,kind,reason,state)
                VALUES(?,?,?,?,?,'BLOCKED') ON CONFLICT(scope_id,profile_id) DO UPDATE SET operation_id=excluded.operation_id,
                kind=excluded.kind,reason=excluded.reason,state='BLOCKED'""", lease.scopeId,
                lease.profileId, operation, kind, reason)
            pageSql.mutate("j_sync_cursor", listOf(lease.scopeId, lease.profileId),
                "UPDATE j_sync_cursor SET state='DEFERRED' WHERE scope_id=? AND profile_id=?", lease.scopeId, lease.profileId)
            changedOne()
            AccountPageReceipt(operation, "DEFERRED", false, null)
        }

    private fun pageTransaction(operation: AccountUuid, lease: AccountLease, lineage: AccountUuid, kind: String, bytes: ByteArray?,
        progress: Boolean = kind.startsWith("RESET_"), deferral: Boolean = false,
        action: () -> AccountPageReceipt): AccountPageReceipt {
        if (number("SELECT count(*) FROM j_page_result WHERE operation_id=?", operation) != 0L) ambiguity()
        var expected: AccountPageReceipt? = null
        fun readback(): CommitReadback {
            files.verify()
            val actual = query("""SELECT scope_id,profile_id,lineage_id,activation_revision,kind,state,has_more,ack_through,safe_wire
                FROM j_page_result WHERE operation_id=?""", operation) {
                val matching = it.getText(0) == lease.scopeId.value && it.getText(1) == lease.profileId.value &&
                    it.getText(2) == lineage.value && it.getLong(3) == lease.activationRevision && it.getText(4) == kind &&
                    (if (bytes == null) it.isNull(8) else !it.isNull(8) && it.getBlob(8).contentEquals(bytes))
                matching to AccountPageReceipt(operation, it.getText(5), it.getLong(6) == 1L, nullableLong(it, 7))
            }.singleOrNull() ?: return CommitReadback.NOT_APPLIED
            return if (actual.first && actual.second == expected) CommitReadback.APPLIED else CommitReadback.AMBIGUOUS
        }
        // Hold the entire shared ceiling, not a wire-size multiplier. A refusal rolls back before
        // a separate bounded raw-page/marker deferral can use the previously held progress reserve.
        val reserve = if (deferral) 2_097_152L + (bytes?.size ?: 0) else
            if (progress || kind == "ACK") DesktopAccountLimits.PROGRESS_BYTES else DesktopAccountLimits.PAGE_GROWTH_BYTES
        // <=10 MiB raw deferral cannot consume the whole 16 MiB status/settlement reserve.
        if (deferral && reserve > DesktopAccountLimits.PROGRESS_BYTES - 2_097_152) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        fun uncertainPage(): Unit {
            pageReconciliationRequired = true
            if (kind == "ACK") deliveryReconciliationRequired = true
        }
        val result = transaction(progress = progress, reserve = reserve, onAmbiguous = ::uncertainPage, readback = ::readback) {
            pageSql.budgeted(reserve, bytes?.size ?: 0) {
                val value = action()
                expected = value
                pageSql.commitMarker("""INSERT INTO j_page_result(operation_id,scope_id,profile_id,lineage_id,activation_revision,kind,state,has_more,ack_through,safe_wire)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", operation, lease.scopeId, lease.profileId, lineage, lease.activationRevision,
                    kind, value.state, if (value.hasMore) 1 else 0, value.acknowledgedThrough, bytes)
                value
            }
        }
        val outcome = try { readback() } catch (_: Throwable) { CommitReadback.AMBIGUOUS }
        if (outcome != CommitReadback.APPLIED) { uncertainPage(); ambiguity() }
        return result
    }

    override fun retire(): Unit = serial.retire()

    override suspend fun close(): Unit = serial.close {
        // Expiry/cancellation cannot prove the claim's external owner settled. finish it explicitly first.
        noDelivery()
        if (initialized) noExternalWork()
        activeLease = null
        activeWitness = null
        connection?.close()
        connection = null
        initialized = false
        files.close()
        protectedLifetime?.close()
        protectedLifetime = null
    }

    /** Exact recognized unpublished source shape only; unknown objects/rows are retained. */
    private fun completeInitialPageSchema(): Unit {
        fun objectName(statement: String): String = Regex("^CREATE (?:UNIQUE )?(?:TABLE|INDEX|TRIGGER|VIEW) ([a-z0-9_]+)")
            .find(statement)?.groupValues?.get(1) ?: fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        fun verify(statements: List<String>): Unit {
            for (statement in statements) {
                val stored = query("SELECT sql FROM sqlite_schema WHERE name=?", objectName(statement)) { it.getText(0) }.singleOrNull()
                if (stored?.trim() != statement.trim()) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            }
        }
        verify(DesktopAccountSchema.create)
        val installed = number("SELECT count(*) FROM sqlite_schema WHERE type='table' AND name='j_schema_component'") == 1L
        if (!installed) {
            // Any partially installed/colliding known name is contradictory, never normalized away.
            for (statement in DesktopAccountPageSchema.create) {
                if (number("SELECT count(*) FROM sqlite_schema WHERE name=?", objectName(statement)) != 0L) {
                    fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
                }
            }
            transaction(progress = true, reserve = 2_097_152) {
                for (statement in DesktopAccountPageSchema.create) db().execSQL(statement)
                execute("INSERT INTO j_schema_component(name,version) VALUES(?,1)", DesktopAccountPageSchema.COMPONENT)
            }
        }
        verify(DesktopAccountPageSchema.create)
        if (number("SELECT count(*) FROM j_schema_component WHERE name=? AND version=1", DesktopAccountPageSchema.COMPONENT) != 1L) {
            fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
        }
        for ((component, statements) in listOf(AccountIntentSchema.COMPONENT to AccountIntentSchema.create,
                AccountRepositorySchema.COMPONENT to AccountRepositorySchema.create,
                AccountCatalogSchema.COMPONENT to AccountCatalogSchema.create, AccountMediaSchema.COMPONENT to AccountMediaSchema.create,
                AccountInteractionSchema.COMPONENT to AccountInteractionSchema.create,
                AccountImportReviewSchema.COMPONENT to AccountImportReviewSchema.create)) {
            val present = query("SELECT version FROM j_schema_component WHERE name=?", component) { it.getLong(0) }.singleOrNull()
            if (present == null) {
                for (statement in statements) if (number("SELECT count(*) FROM sqlite_schema WHERE name=?", objectName(statement)) != 0L) {
                    fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
                }
                transaction(progress = true, reserve = 2_097_152) {
                    statements.forEach { db().execSQL(it) }
                    execute("INSERT INTO j_schema_component(name,version) VALUES(?,1)", component)
                }
            } else if (present != 1L) fail(AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED)
            verify(statements)
        }
        // At most 32 retained aliases; no new epoch or remote BOUND status is inferred here.
        val aliases = query("SELECT scope_id,profile_id,lineage_id FROM j_profile_alias WHERE lineage_id IS NOT NULL LIMIT 33") {
            Triple(AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), AccountUuid(it.getText(2)))
        }
        if (aliases.size > DesktopAccountLimits.PROFILES) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        transaction(progress = true, reserve = 524_288) {
            aliases.forEach { (scope, profile, lineage) -> ensureSyncCursor(scope, profile, lineage) }
        }
    }

    private fun ensureSyncCursor(scope: AccountUuid, profile: AccountUuid, lineage: AccountUuid): Unit {
        val prior = query("SELECT lineage_id FROM j_sync_cursor WHERE scope_id=? AND profile_id=?", scope, profile) { it.getText(0) }.singleOrNull()
        if (prior != null && prior != lineage.value) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        if (prior == null) {
            execute("""INSERT INTO j_sync_cursor(scope_id,profile_id,lineage_id,revision,server_sequence,state)
                VALUES(?,?,?,0,0,'NOT_STARTED')""", scope, profile, lineage)
            execute("INSERT INTO j_bootstrap(scope_id,profile_id,state) VALUES(?,?,'NOT_STARTED')", scope, profile)
        } else if (number("SELECT count(*) FROM j_bootstrap WHERE scope_id=? AND profile_id=?", scope, profile) != 1L) {
            fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        }
    }

    private fun reserveExternalOutcome(bytes: Long): Unit {
        // All owned statements have closed and no transaction is open at external admission.
        if (files.walSoftLimitReached()) {
            val result = query("PRAGMA wal_checkpoint(TRUNCATE)") { it.getLong(0) }.singleOrNull()
            if (result != 0L || files.walSoftLimitReached()) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        }
        files.requireSpace(number("PRAGMA page_count") * 4_096, bytes, progress = false)
    }

    private fun configure(): Unit {
        val connection = db()
        connection.execSQL("PRAGMA foreign_keys=ON")
        connection.execSQL("PRAGMA busy_timeout=1000")
        if (text("PRAGMA journal_mode=WAL").lowercase() != "wal") fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        connection.execSQL("PRAGMA synchronous=FULL")
        connection.execSQL("PRAGMA cache_size=-8192")
        connection.execSQL("PRAGMA temp_store=MEMORY")
        if (number("PRAGMA max_page_count=131072") != DesktopAccountLimits.MAIN_PAGES ||
            number("PRAGMA foreign_keys") != 1L || number("PRAGMA busy_timeout") != 1_000L ||
            number("PRAGMA synchronous") != 2L || number("PRAGMA page_size") != 4_096L ||
            number("PRAGMA cache_size") != -8_192L || number("PRAGMA temp_store") != 2L) {
            fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        }
        files.verify()
    }

    private fun snapshot(): AccountReconciliationSnapshot {
        val activation = query("SELECT revision,scope_id,state FROM j_activation WHERE singleton=1") {
            Triple(it.getLong(0), nullableText(it, 1)?.let(::AccountUuid), it.getText(2))
        }.singleOrNull() ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        val scopes = query("SELECT scope_id,account_id,server_instance_id,user_id,catalog_owner_scope,state FROM j_scope ORDER BY scope_id LIMIT 9") {
            scope(it) to it.getText(5)
        }
        if (scopes.size > DesktopAccountLimits.OWNER_SCOPES ||
            number("SELECT count(*) FROM j_profile_alias") > DesktopAccountLimits.PROFILES) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        val unresolved = query("SELECT activation_id FROM j_scope_prepare WHERE stage='PREPARED' LIMIT 2") { AccountUuid(it.getText(0)) }
        if (unresolved.size > 1) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        val pendingCommit = unresolved.singleOrNull() ?: if (activeLease == null) {
            query("SELECT activation_id FROM j_activation WHERE singleton=1") { nullableText(it, 0)?.let(::AccountUuid) }.singleOrNull()
        } else null
        val pending = pendingCommit?.let(::readPreparation)
        return AccountReconciliationSnapshot(id(), bootId, activation.first, activation.second, activation.third,
            pending?.first, pending?.second, scopes.map { (identity, state) ->
                AccountScopeSnapshot(identity, state, query("SELECT profile_id FROM j_profile_alias WHERE scope_id=? ORDER BY profile_id LIMIT 33",
                    identity.scopeId) { AccountUuid(it.getText(0)) })
            }, query("SELECT intent_id FROM j_binding_intent WHERE stage='PREPARED' LIMIT 2") { AccountUuid(it.getText(0)) }
                .also { if (it.size > 1) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) }.singleOrNull()?.let(::bindingIntent))
    }

    private fun readPreparation(commit: AccountUuid): Pair<AccountScopePreparation, String>? = query("""SELECT
        s.scope_id,s.account_id,s.server_instance_id,s.user_id,s.catalog_owner_scope,
        p.profile_id,p.device_id,p.journal_epoch,p.identity_epoch,p.server_thumbprint,p.device_key_thumbprint,
        p.authority_revision,p.prior_activation_revision,p.stage,p.binding_commit,p.intent_id
        FROM j_scope_prepare p JOIN j_scope s ON s.scope_id=p.scope_id WHERE p.activation_id=?""", commit) {
        val binding = nullableText(it, 6)?.let { device -> AccountRemoteBinding(AccountUuid(device),
            AccountUuid(it.getText(7)), it.getLong(8), it.getText(9), it.getText(10)) }
        AccountScopePreparation(AccountUuid(it.getText(14)), scope(it), AccountUuid(it.getText(5)), binding,
            it.getLong(11), it.getLong(12), commit, nullableText(it, 15)?.let(::AccountUuid)) to it.getText(13)
    }.singleOrNull()

    private fun scope(statement: SQLiteStatement): AccountScopeIdentity {
        val server = nullableText(statement, 2)
        return AccountScopeIdentity(AccountUuid(statement.getText(0)), nullableText(statement, 1)?.let(::AccountUuid),
            server?.let { AccountRemoteOwner(AccountUuid(it), AccountUuid(statement.getText(3))) })
    }

    private fun ensureScope(identity: AccountScopeIdentity): Unit {
        val existing = query("SELECT scope_id,account_id,server_instance_id,user_id,catalog_owner_scope FROM j_scope WHERE scope_id=?",
            identity.scopeId, read = ::scope).singleOrNull()
        if (existing != null) {
            if (existing != identity) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
            val state = text("SELECT state FROM j_scope WHERE scope_id=?", identity.scopeId)
            if (state !in setOf("PREPARED", "SUSPENDED", "ACTIVE")) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            return
        }
        if (number("SELECT count(*) FROM j_scope") >= DesktopAccountLimits.OWNER_SCOPES) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        // Global identity-registry checks enforce uniqueness; domain queries never fall back to other scopes.
        val remote = identity.remote
        val duplicate = if (remote == null) number("SELECT count(*) FROM j_scope WHERE account_id=? AND server_instance_id IS NULL", identity.portableAccountId)
            else number("SELECT count(*) FROM j_scope WHERE server_instance_id=? AND user_id=?", remote.serverInstanceId, remote.userId)
        if (duplicate != 0L) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        execute("INSERT INTO j_scope(scope_id,account_id,server_instance_id,user_id,catalog_owner_scope,state) VALUES(?,?,?,?,?,'PREPARED')",
            identity.scopeId, identity.portableAccountId, remote?.serverInstanceId, remote?.userId, null)
    }

    private fun ensureProfileCompatible(scope: AccountUuid, profile: AccountUuid): Unit {
        val owner = query("SELECT scope_id FROM j_profile_alias WHERE profile_id=?", profile) { AccountUuid(it.getText(0)) }.singleOrNull()
        if (owner != null && owner != scope) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        if (owner == null && number("SELECT count(*) FROM j_profile_alias") >= DesktopAccountLimits.PROFILES) {
            fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        }
    }

    private fun lineage(plan: AccountScopePreparation): AccountUuid? {
        val remote = plan.scope.remote ?: return null
        val binding = plan.binding ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        return query("SELECT lineage_id,user_id,journal_epoch FROM j_lineage WHERE scope_id=? AND server_instance_id=? AND device_id=?",
            plan.scope.scopeId, remote.serverInstanceId, binding.deviceId) {
            if (it.getText(1) != remote.userId.value || it.getText(2) != binding.journalEpoch.value) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
            AccountUuid(it.getText(0))
        }.singleOrNull()
    }

    private fun ensureLineage(plan: AccountScopePreparation, allowNew: Boolean): AccountUuid? {
        val remote = plan.scope.remote ?: return null
        val binding = plan.binding ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        lineage(plan)?.let { return it }
        if (!allowNew) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (number("SELECT count(*) FROM j_lineage WHERE server_instance_id=? AND device_id=?", remote.serverInstanceId, binding.deviceId) != 0L) {
            fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
        }
        val lineage = AccountUuid.random()
        execute("""INSERT INTO j_lineage(scope_id,lineage_id,server_instance_id,user_id,device_id,journal_epoch,next_sequence,ack_checkpoint)
            VALUES(?,?,?,?,?,?,1,0)""", plan.scope.scopeId, lineage, remote.serverInstanceId, remote.userId, binding.deviceId, binding.journalEpoch)
        return lineage
    }

    private fun ensureAlias(plan: AccountScopePreparation, lineage: AccountUuid?): Unit {
        ensureProfileCompatible(plan.scope.scopeId, plan.profileId)
        val existing = query("SELECT lineage_id FROM j_profile_alias WHERE scope_id=? AND profile_id=?", plan.scope.scopeId, plan.profileId) {
            nullableText(it, 0)?.let(::AccountUuid) to true
        }.singleOrNull()
        if (existing != null) {
            if (existing.first != lineage) fail(AccountFailure.ACCOUNT_IDENTITY_CONFLICT)
            return
        }
        if (number("SELECT count(*) FROM j_profile_alias") >= DesktopAccountLimits.PROFILES) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        execute("INSERT INTO j_profile_alias(scope_id,profile_id,lineage_id) VALUES(?,?,?)", plan.scope.scopeId, plan.profileId, lineage)
    }

    private data class StoredMutation(
        val track: AccountUuid, val entry: AccountUuid, val title: String, val artist: String,
        val occurredAtMs: Long, val receipt: AccountMutationReceipt,
    ) {
        fun matches(change: AccountCreateTrack): Boolean = receipt.operationId == change.operationId &&
            track == change.trackRefId && entry == change.libraryEntryId && title == change.title &&
            artist == change.artist && occurredAtMs == change.occurredAtMs
    }

    // Read retained values without passing them through a NEW-command constructor's current bounds.
    private fun existingMutation(scope: AccountUuid, operation: AccountUuid): StoredMutation? = query("""SELECT
        t.track_ref_id,l.entry_id,t.title,t.artist,t.occurred_at_ms,e.event_id,i.change_id,e.canonical_bytes,i.canonical_payload
        FROM j_track_ref t JOIN j_library_entry l ON l.scope_id=t.scope_id AND l.track_ref_id=t.track_ref_id
        LEFT JOIN j_event e ON e.scope_id=t.scope_id AND e.event_id=t.operation_id
        LEFT JOIN j_local_intent i ON i.scope_id=t.scope_id AND i.change_id=t.operation_id
        WHERE t.scope_id=? AND t.operation_id=?""", scope, operation) {
        val track = AccountUuid(it.getText(0))
        val entry = AccountUuid(it.getText(1))
        val event = nullableText(it, 5)?.let(::AccountUuid)
        val intent = nullableText(it, 6)?.let(::AccountUuid)
        if ((event == null) == (intent == null)) ambiguity()
        // A later clean remote patch must not change the immutable create operation's retry identity.
        val original = if (event != null) {
            val retained = AccountEventV1.fromCanonical(it.getBlob(7))
            if (retained.aggregateLocalId != track || retained.eventType != "USER_TRACK_REF_CREATED") {
                fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
            }
            (Json.parseToJsonElement(retained.canonicalBytes.toString(Charsets.UTF_8)) as JsonObject).obj("payload")
        } else AccountSyncWireV1.document(it.getBlob(8))
        if (original.id("library_entry_local_id") != entry) ambiguity()
        StoredMutation(track, entry, original.string("title", 4096, 0), original.string("artist", 4096, 0), it.getLong(4),
            AccountMutationReceipt(operation, track, entry, event, intent))
    }.singleOrNull()

    private fun activationReadback(plan: AccountScopePreparation, revision: Long,
        authorityRevision: Long = plan.expectedAuthorityRevision): AccountActivationReadback {
        files.verify()
        val matched = number("""SELECT count(*) FROM j_activation a JOIN j_scope_prepare p ON p.activation_id=a.activation_id
            WHERE a.singleton=1 AND a.scope_id=? AND a.profile_id=? AND a.binding_commit=? AND a.revision=?
            AND a.authority_revision=? AND a.boot_id=? AND a.state='ACTIVE' AND p.stage='APPLIED' AND p.activation_id=?""",
            plan.scope.scopeId, plan.profileId, plan.bindingCommit, revision, authorityRevision, bootId, plan.activationId)
        if (matched != 1L || readPreparation(plan.activationId)?.first != plan) ambiguity()
        return AccountActivationReadback(id(), plan, revision)
    }

    private fun <T> captured(plan: AccountScopePreparation, activation: Boolean = false,
        action: (AccountProtectedWitness, AccountProtectedCapture) -> T): T {
        val capture = try {
            if (activation) protectedLifetime?.tryCaptureForActivation(id(), plan) else protectedLifetime?.tryCapture(id(), plan)
        } catch (_: Throwable) { ambiguity() }
        if (capture == null) {
            activeLease = null
            activeWitness = null
            fail(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        }
        try {
            val witness = capture.witness
            if (witness.storeId != id() || witness.preparation != plan) {
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            }
            return action(witness, capture)
        } catch (failure: Throwable) {
            if (failure !is AccountStoreException) {
                uncertain = true
                activeLease = null
                activeWitness = null
            }
            throw sanitized(failure)
        } finally {
            try { capture.close() } catch (_: Throwable) { ambiguity() }
        }
    }

    private fun <T> admitted(lease: AccountLease, action: (AccountProtectedWitness) -> T): T {
        exactLease(lease)
        val previous = activeWitness ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        return captured(previous.preparation) { witness, _ ->
            if (witness.stage != AccountProtectedStage.READY || !witness.lifecycleAllowed || !sameCurrent(previous, witness)) {
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
            }
            activationReadback(witness.preparation, lease.activationRevision, witness.currentAuthorityRevision)
            action(witness)
        }
    }

    private fun sameCurrent(first: AccountProtectedWitness, second: AccountProtectedWitness): Boolean =
        first.copy(stage = AccountProtectedStage.READY, permitsNewDeviceLineage = false) ==
            second.copy(stage = AccountProtectedStage.READY, permitsNewDeviceLineage = false)

    private fun exactLease(lease: AccountLease): Unit {
        if (lease != activeLease || lease.storeId != id() || lease.bootId != bootId) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
    }

    private fun exactDelivery(lease: AccountDeliveryLease): AccountDeliveryClaim {
        val claim = liveDelivery ?: fail(AccountFailure.ACCOUNT_LEASE_STALE)
        if (claim.lease != lease || lease.account.bootId != bootId) fail(AccountFailure.ACCOUNT_LEASE_STALE)
        return claim
    }

    private fun noDelivery(): Unit {
        if (deliveryReconciliationRequired || pageReconciliationRequired) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
        if (liveDelivery != null || livePage != null) fail(AccountFailure.ACCOUNT_BUSY)
    }
    private fun noExternalWork(): Unit {
        if (number("SELECT count(*) FROM j_work WHERE state='RUNNING' AND boot_id=?",bootId)!=0L ||
            number("SELECT count(*) FROM j_media_attempt WHERE state IN ('PREPARED','WAITING','RUNNING','DRAINING') AND boot_id=?",bootId)!=0L) {
            fail(AccountFailure.ACCOUNT_BUSY)
        }
    }
    private fun activationRevision(): Long = number("SELECT revision FROM j_activation WHERE singleton=1")
    private fun nextWorkRevision(): Long {
        val prior = number("SELECT work_revision FROM j_activation WHERE singleton=1")
        if (prior >= DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        return prior + 1
    }

    private fun eventCapacity(additionalBytes: Long): Unit {
        val count = number("SELECT count(*) FROM j_event_delivery WHERE state NOT IN ('ACKNOWLEDGED','REJECTED')") +
            number("""SELECT count(*) FROM j_local_intent i WHERE i.materialized_event_id IS NULL
                AND NOT EXISTS(SELECT 1 FROM j_account_intent_link m WHERE m.source_scope_id=i.scope_id AND m.change_id=i.change_id)""")
        val bytes = accountEventCopyBytes(pageSql)
        if (count >= DesktopAccountLimits.PENDING_EVENTS || bytes > DesktopAccountLimits.EVENT_COPY_BYTES - additionalBytes) {
            fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        }
    }

    private fun conservativeNow(): Long {
        val time = now()
        if (time < number("SELECT last_wall_ms FROM j_store WHERE singleton=1")) fail(AccountFailure.ACCOUNT_CLOCK_ROLLBACK)
        return time
    }

    private fun rememberWall(time: Long): Unit = execute("UPDATE j_store SET last_wall_ms=max(last_wall_ms,?) WHERE singleton=1", time)
    private fun boundedAdd(first: Long, second: Long): Long {
        if (second > DesktopAccountLimits.MAX_REVISION - first) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        return first + second
    }
    private fun now(): Long = wallMs().also { if (it !in 0..DesktopAccountLimits.MAX_REVISION) fail(AccountFailure.ACCOUNT_CLOCK_ROLLBACK) }
    private fun usable(): Unit { if (!initialized || uncertain) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED) }
    private fun id(): AccountUuid = storeId ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    private fun db(): SQLiteConnection = connection ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    private fun ambiguity(): Nothing {
        uncertain = true
        activeLease = null
        activeWitness = null
        fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }

    private enum class CommitReadback { APPLIED, NOT_APPLIED, AMBIGUOUS }

    private data class DeliveryRow(
        val eventId: AccountUuid, val state: String, val attempt: Long, val retryAtMs: Long,
        val boot: String?, val token: String?, val revision: Long, val expiresAtMs: Long?, val error: String?,
    )
    private data class DeliveryReadback(val workRevision: Long, val lastWallMs: Long, val rows: List<DeliveryRow>)

    private fun deliveryReadback(claim: AccountDeliveryClaim): DeliveryReadback {
        files.verify()
        if (claim.events.size !in 1..DesktopAccountLimits.PAGE) ambiguity()
        return DeliveryReadback(number("SELECT work_revision FROM j_activation WHERE singleton=1"),
            number("SELECT last_wall_ms FROM j_store WHERE singleton=1"), claim.events.map { event ->
                query("""SELECT state,attempt,next_retry_ms,lease_boot,lease_token,lease_revision,lease_expires_ms,last_error
                    FROM j_event_delivery WHERE scope_id=? AND event_id=?""", claim.lease.account.scopeId, event.eventId) {
                    DeliveryRow(event.eventId, it.getText(0), it.getLong(1), it.getLong(2), nullableText(it, 3),
                        nullableText(it, 4), it.getLong(5), nullableLong(it, 6), nullableText(it, 7))
                }.singleOrNull() ?: ambiguity()
            })
    }

    private fun deliveryTransaction(claim: AccountDeliveryClaim, progress: Boolean, action: () -> Unit): Unit {
        val before = deliveryReadback(claim)
        var expected: DeliveryReadback? = null
        transaction(progress, reserve = 2_097_152, onAmbiguous = { deliveryReconciliationRequired = true }, readback = {
            // Only after all statements, COMMIT and rollback/fence have actually settled.
            // One bounded read, no SQL/domain mutation replay or driver-version-specific state API.
            val actual = deliveryReadback(claim)
            when {
                expected != null && expected != before && actual == expected -> CommitReadback.APPLIED
                actual == before -> CommitReadback.NOT_APPLIED
                else -> CommitReadback.AMBIGUOUS
            }
        }) {
            action()
            expected = deliveryReadback(claim)
        }
    }

    private fun <T> transaction(
        progress: Boolean, reserve: Long, onAmbiguous: (() -> Unit)? = null,
        readback: (() -> CommitReadback)? = null, action: () -> T,
    ): T {
        if (!progress && files.walSoftLimitReached()) {
            // All statements/readers from this owner have settled. Busy is one refusal, not a retry loop.
            val result = query("PRAGMA wal_checkpoint(TRUNCATE)") { it.getLong(0) }.singleOrNull()
            if (result != 0L || files.walSoftLimitReached()) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
        }
        files.requireSpace(number("PRAGMA page_count") * 4_096, reserve, progress)
        var begun = false
        var committing = false
        var settledResult: (() -> T)? = null
        try {
            db().execSQL("BEGIN IMMEDIATE")
            begun = true
            val result = action()
            settledResult = { result }
            files.verify()
            committing = true
            db().execSQL("COMMIT")
            begun = false
            committing = false
            return result
        } catch (failure: Throwable) {
            if (begun) {
                // A failed COMMIT may leave a transaction active, roll it back, or have applied.
                // Never read our own uncommitted writes as proof of a durable outcome.
                val ended = settleFailedTransaction()
                if (committing && ended && readback != null) {
                    val outcome = try { readback() } catch (_: Throwable) { CommitReadback.AMBIGUOUS }
                    when (outcome) {
                        CommitReadback.APPLIED -> return checkNotNull(settledResult).invoke()
                        CommitReadback.NOT_APPLIED -> throw sanitized(failure)
                        CommitReadback.AMBIGUOUS -> { uncertain = true; onAmbiguous?.invoke() }
                    }
                } else if (committing || !ended) { uncertain = true; onAmbiguous?.invoke() }
            }
            if (uncertain) {
                activeLease = null
                activeWitness = null
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            }
            if (failure is AccountPageDeferred) throw failure
            throw sanitized(failure)
        }
    }

    private fun settleFailedTransaction(): Boolean {
        try {
            db().execSQL("ROLLBACK")
            return true
        } catch (_: Throwable) { /* Could be already committed/auto-rolled-back or still unavailable. */ }
        return try {
            // SQLite BEGIN cannot nest. This single empty fence proves no old transaction remains;
            // its successful ROLLBACK ends the fence before any outcome SELECT. Same writer only.
            db().execSQL("BEGIN IMMEDIATE")
            db().execSQL("ROLLBACK")
            true
        } catch (_: Throwable) { false }
    }

    private fun execute(sql: String, vararg values: Any?): Unit {
        db().prepare(sql).use { statement ->
            bind(statement, values)
            if (statement.step()) ambiguity()
        }
    }
    private fun changedOne(): Unit { if (number("SELECT changes()") != 1L) fail(AccountFailure.ACCOUNT_SCOPE_CHANGED) }
    private fun number(sql: String, vararg values: Any?): Long = query(sql, *values) { it.getLong(0) }.singleOrNull()
        ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    private fun text(sql: String, vararg values: Any?): String = query(sql, *values) { it.getText(0) }.singleOrNull()
        ?: fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    private fun <T> query(sql: String, vararg values: Any?, read: (SQLiteStatement) -> T): List<T> = db().prepare(sql).use { statement ->
        bind(statement, values)
        val result = ArrayList<T>()
        while (statement.step()) result.add(read(statement))
        result
    }
    private fun bind(statement: SQLiteStatement, values: Array<out Any?>): Unit {
        for ((offset, value) in values.withIndex()) when (value) {
            null -> statement.bindNull(offset + 1)
            is AccountUuid -> statement.bindText(offset + 1, value.value)
            is String -> statement.bindText(offset + 1, value)
            is Long -> statement.bindLong(offset + 1, value)
            is Int -> statement.bindLong(offset + 1, value.toLong())
            is ByteArray -> statement.bindBlob(offset + 1, value)
            else -> fail(AccountFailure.ACCOUNT_INPUT_INVALID)
        }
    }
    private fun nullableText(statement: SQLiteStatement, column: Int): String? = if (statement.isNull(column)) null else statement.getText(column)
    private fun nullableLong(statement: SQLiteStatement, column: Int): Long? = if (statement.isNull(column)) null else statement.getLong(column)
}
