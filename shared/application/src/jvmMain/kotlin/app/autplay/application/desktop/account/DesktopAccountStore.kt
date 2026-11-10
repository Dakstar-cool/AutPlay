package app.autplay.application.desktop.account

import java.util.UUID

/** J1 metadata only. None of these values is a credential or a media/source capability. */
@JvmInline
value class AccountUuid(val value: String) {
    init {
        val canonical = try { UUID.fromString(value).toString() } catch (_: Exception) { null }
        require(canonical == value) { "ACCOUNT_ID_INVALID" }
    }
    override fun toString(): String = "AccountUuid"
    companion object { fun random(): AccountUuid = AccountUuid(UUID.randomUUID().toString()) }
}

@JvmInline
value class CatalogOwnerScope(val value: String) {
    init { require(value.matches(Regex("[0-9a-f]{64}"))) { "ACCOUNT_OWNER_SCOPE_INVALID" } }
    override fun toString(): String = "CatalogOwnerScope"
}

object DesktopAccountLimits {
    const val MAX_REVISION: Long = 9_007_199_254_740_991L
    // New UTC millisecond events: retain the nonnegative domain bound and Python's year 9999 cap.
    const val MAX_EVENT_TIME_MS: Long = 253_402_300_799_999L
    const val OWNER_SCOPES: Int = 8
    const val PROFILES: Int = 32
    const val PAGE: Int = 100
    const val CALLERS: Int = 8
    const val WORK: Int = 1_024
    const val DESCRIPTOR_BYTES: Int = 1_024
    const val PAGE_BYTES: Long = 8_388_608
    /** Hard page mutation admission ceiling; wire bytes alone never authorize derived rows. */
    const val PAGE_GROWTH_BYTES: Long = PAGE_BYTES * 4 + 2_097_152
    const val PAYLOAD_BYTES: Int = 262_144
    const val CURSOR_BYTES: Int = 8_192
    const val PENDING_EVENTS: Long = 200_000
    const val EVENT_COPY_BYTES: Long = 256L * 1_024 * 1_024
    const val PORTABLE_RECORDS: Long = 200_000
    const val PORTABLE_BYTES: Long = 64L * 1_024 * 1_024
    const val PORTABLE_PAGE: Int = 32
    const val PORTABLE_PAGE_BYTES: Int = 524_288
    const val MATERIALIZE_FACTS: Int = 100
    const val MAIN_BYTES: Long = 512L * 1_024 * 1_024
    const val MAIN_PAGES: Long = 131_072
    const val COMBINED_BYTES: Long = 640L * 1_024 * 1_024
    const val FREE_RESERVE_BYTES: Long = 256L * 1_024 * 1_024
    const val WAL_SOFT_BYTES: Long = 64L * 1_024 * 1_024
    const val PROGRESS_BYTES: Long = 16L * 1_024 * 1_024
    const val LEASE_MS: Long = 60_000
    const val ATTEMPTS: Int = 8
}

enum class AccountFailure {
    ACCOUNT_CLOSED, ACCOUNT_BUSY, ACCOUNT_DATA_UNAVAILABLE, ACCOUNT_STORAGE_FULL,
    ACCOUNT_FORMAT_UNSUPPORTED, ACCOUNT_RECONCILIATION_REQUIRED, ACCOUNT_SCOPE_CHANGED,
    ACCOUNT_AUTHORITY_UNAVAILABLE, ACCOUNT_IDENTITY_CONFLICT, ACCOUNT_UNSUPPORTED_OPERATION,
    ACCOUNT_LEASE_STALE, ACCOUNT_CLOCK_ROLLBACK, ACCOUNT_INPUT_INVALID, ACCOUNT_DELIVERY_BLOCKED,
}

/** Stable code only: never attach native exception text, identifiers, paths or personal payloads. */
class AccountStoreException(val failure: AccountFailure) : IllegalStateException(failure.name)

data class AccountRemoteOwner(
    val serverInstanceId: AccountUuid,
    val userId: AccountUuid,
) { override fun toString(): String = "AccountRemoteOwner" }

data class AccountScopeIdentity(
    val scopeId: AccountUuid,
    val portableAccountId: AccountUuid?,
    val remote: AccountRemoteOwner?,
) {
    init { require(portableAccountId != null || remote != null) { "ACCOUNT_OWNER_REQUIRED" } }
    override fun toString(): String = "AccountScopeIdentity"
}

data class AccountRemoteBinding(
    val deviceId: AccountUuid,
    val journalEpoch: AccountUuid,
    val identityEpoch: Long,
    val serverThumbprint: String,
    val deviceKeyThumbprint: String,
) {
    init {
        require(identityEpoch in 1..DesktopAccountLimits.MAX_REVISION)
        require(serverThumbprint.matches(Regex("[0-9a-f]{64}")))
        require(deviceKeyThumbprint.matches(Regex("[0-9a-f]{64}")))
    }
    override fun toString(): String = "AccountRemoteBinding"
}

/** Immutable binding preparation; selected standalone copying uses the separate consent/materialization owner. */
data class AccountScopePreparation(
    val bindingCommit: AccountUuid,
    val scope: AccountScopeIdentity,
    val profileId: AccountUuid,
    val binding: AccountRemoteBinding?,
    val expectedAuthorityRevision: Long,
    val expectedActivationRevision: Long,
    val activationId: AccountUuid = bindingCommit,
    val intentId: AccountUuid? = null,
) {
    init {
        require((scope.remote == null) == (binding == null))
        require(expectedAuthorityRevision in 1..DesktopAccountLimits.MAX_REVISION)
        require(expectedActivationRevision in 0 until DesktopAccountLimits.MAX_REVISION)
    }
    override fun toString(): String = "AccountScopePreparation"
}

enum class AccountProtectedStage { PREPARING, BINDING_COMMIT_PENDING, READY }

/** Non-secret comparison evidence supplied by the approved actual K/J protected owner. */
data class AccountProtectedWitness(
    val storeId: AccountUuid,
    val preparation: AccountScopePreparation,
    val stage: AccountProtectedStage,
    val sessionFamily: AccountUuid?,
    val sessionGeneration: Long,
    val capabilityRevision: Long?,
    val lifecycleAllowed: Boolean,
    /** True only for a genuinely new authenticated device; never inferred from an absent J row. */
    val permitsNewDeviceLineage: Boolean,
    /** Current K fence; the immutable preparation retains its originally captured revision. */
    val currentAuthorityRevision: Long = preparation.expectedAuthorityRevision,
) {
    init {
        require(sessionGeneration in 0..DesktopAccountLimits.MAX_REVISION)
        require(currentAuthorityRevision in preparation.expectedAuthorityRevision..DesktopAccountLimits.MAX_REVISION)
        require(capabilityRevision == null || capabilityRevision in 1..DesktopAccountLimits.MAX_REVISION)
        require((preparation.binding == null) == (sessionFamily == null))
    }
    override fun toString(): String = "AccountProtectedWitness"
}

sealed interface AccountProtectedStoreState {
    /** Legacy port shape; production first creation requires the protected Reserved association. */
    data object Virgin : AccountProtectedStoreState
    /** Protected RESERVED permits only this exact first creation or its exact restart readback. */
    data class Reserved(val storeId: AccountUuid) : AccountProtectedStoreState
    data class Existing(val storeId: AccountUuid) : AccountProtectedStoreState
    data object Unavailable : AccountProtectedStoreState
}

/** Implemented by the desktop K/J bridge; acquire K's canonical lifetime lock nonblockingly. */
interface DesktopAccountAuthorityPort {
    fun tryAcquireLifetime(): AccountProtectedLifetime?
}

interface AccountProtectedLifetime : AutoCloseable {
    val storeState: AccountProtectedStoreState
    /** Called after exact schema/store/file ownership readback, before ordinary J initialization. */
    fun completeInstallation(storeId: AccountUuid): Boolean = false
    /** The common gate stays held from protected original comparison through SQL readback. */
    fun tryCaptureIntent(storeId: AccountUuid, intent: AccountBindingIntent): AutoCloseable? = null
    fun tryCaptureIntentCancellation(storeId: AccountUuid, intent: AccountBindingIntent): AutoCloseable? = null
    /**
     * Must hold the common gate until capture.close; never wait on J. expectedStoreId is comparison
     * input, never authority to rebuild protected state. A Virgin owner must use its approved existing
     * publication flow; an Existing owner must already match its protected store association.
     */
    fun tryCapture(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture?
    fun tryCaptureForActivation(expectedStoreId: AccountUuid, preparation: AccountScopePreparation): AccountProtectedCapture? =
        tryCapture(expectedStoreId, preparation)
    override fun close(): Unit
}

interface AccountProtectedCapture : AutoCloseable {
    val witness: AccountProtectedWitness
    /** Outside SQL only. Must publish/read back K's EXISTING reconciliation stage, no ceremony. */
    fun completeReconciliation(readback: AccountActivationReadback): AccountProtectedWitness?
    override fun close(): Unit
}

data class AccountActivationReadback(
    val storeId: AccountUuid,
    val preparation: AccountScopePreparation,
    val activationRevision: Long,
) { override fun toString(): String = "AccountActivationReadback" }

data class AccountLease(
    val storeId: AccountUuid,
    val scopeId: AccountUuid,
    val profileId: AccountUuid,
    val bootId: AccountUuid,
    val activationRevision: Long,
    val bindingCommit: AccountUuid,
    val authorityRevision: Long,
    val sessionFamily: AccountUuid?,
    val sessionGeneration: Long,
    val capabilityRevision: Long?,
) { override fun toString(): String = "AccountLease" }

data class AccountScopeSnapshot(
    val identity: AccountScopeIdentity,
    val rawState: String,
    val profileIds: List<AccountUuid>,
) { override fun toString(): String = "AccountScopeSnapshot" }

data class AccountReconciliationSnapshot(
    val storeId: AccountUuid,
    val bootId: AccountUuid,
    val activationRevision: Long,
    val selectedScope: AccountUuid?,
    val rawActivationState: String,
    val unresolvedPreparation: AccountScopePreparation?,
    val rawPreparationStage: String?,
    val scopes: List<AccountScopeSnapshot>,
    val unresolvedBindingIntent: AccountBindingIntent? = null,
) { override fun toString(): String = "AccountReconciliationSnapshot" }

/** Only an explicit new account-mode UTR create is implemented. No local import/source attribution. */
data class AccountCreateTrack(
    val operationId: AccountUuid,
    val trackRefId: AccountUuid,
    val libraryEntryId: AccountUuid,
    val title: String,
    val artist: String,
    val occurredAtMs: Long,
) {
    init {
        require(occurredAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS)
        for (text in listOf(title, artist)) {
            require(text.length <= 4_096 && '\u0000' !in text)
            var index = 0
            while (index < text.length) {
                val character = text[index++]
                if (character.isHighSurrogate()) {
                    require(index < text.length && text[index++].isLowSurrogate())
                } else require(!character.isLowSurrogate())
            }
        }
    }
    override fun toString(): String = "AccountCreateTrack"
}

data class AccountMutationReceipt(
    val operationId: AccountUuid,
    val trackRefId: AccountUuid,
    val libraryEntryId: AccountUuid,
    val eventId: AccountUuid?,
    val localIntentId: AccountUuid?,
) { override fun toString(): String = "AccountMutationReceipt" }

data class AccountTrackProjection(
    val trackRefId: AccountUuid,
    val libraryEntryId: AccountUuid,
    val title: String,
    val artist: String,
    val serverTrackRefId: AccountUuid?,
    val observedServerVersion: Long?,
) { override fun toString(): String = "AccountTrackProjection" }

data class AccountDeliveryStatus(
    val eventId: AccountUuid,
    val lineageId: AccountUuid,
    val originalProfileId: AccountUuid,
    val sequence: Long,
    val rawState: String,
    val attempts: Int,
    val nextRetryAtMs: Long,
    val rawLastError: String?,
    val leaseRevision: Long = 0,
) { override fun toString(): String = "AccountDeliveryStatus" }

data class AccountDeliveryLease(
    val account: AccountLease,
    val lineageId: AccountUuid,
    val originalProfileId: AccountUuid,
    val token: AccountUuid,
    val revision: Long,
    val expiresAtMs: Long,
) { override fun toString(): String = "AccountDeliveryLease" }

class AccountDeliveryClaim(val lease: AccountDeliveryLease, events: List<AccountEventV1>) {
    val events: List<AccountEventV1> = java.util.Collections.unmodifiableList(events.toList())
    override fun toString(): String = "AccountDeliveryClaim"
}

enum class AccountDeliveryFailure { RETRYABLE, AUTHORITY_UNAVAILABLE, UNSUPPORTED, CONFLICT }

/** Exact committed scheduling identity for draining ONLY; never admission to send or renew. */
data class AccountDeliverySettlement(val operationId: AccountUuid, val lease: AccountDeliveryLease) {
    override fun toString(): String = "AccountDeliverySettlement"
}

sealed interface AccountDeliveryOutcome<out T> {
    class Completed<T>(val result: T) : AccountDeliveryOutcome<T> {
        override fun toString(): String = "AccountDeliveryOutcome.Completed"
    }
    /** SQL is proven committed, but later failure (including capture cleanup) denies usable success. */
    class CommittedForSettlement(val handle: AccountDeliverySettlement, val failure: AccountFailure) : AccountDeliveryOutcome<Nothing> {
        override fun toString(): String = "AccountDeliveryOutcome.CommittedForSettlement"
    }
    /** No proven committed handle. Includes rollback AND ambiguous SQL; never infer non-commit. */
    class Failed(val failure: AccountFailure) : AccountDeliveryOutcome<Nothing> {
        override fun toString(): String = "AccountDeliveryOutcome.Failed"
    }
}

/**
 * Retain this ticket BEFORE entering a timeout/cancellable caller scope. It identifies one admitted
 * operation; every await retrieves that exact outcome, never another claim/renewal or a SQL replay.
 * awaitResult throws on cleanup failure; awaitOutcome may recover its COMMITTED drain-only handle.
 * Neither absence of a handle nor a failure code proves SQL rolled back. Unknown evidence stays closed.
 * A historical result grants no network authority and cannot settle a newer lease revision.
 * Keep the latest renewal ticket until its result is recovered and the actual external pass settles.
 */
class AccountDeliveryOperation<T>(
    val operationId: AccountUuid,
    private val readOutcome: suspend () -> AccountDeliveryOutcome<T>,
) {
    suspend fun awaitOutcome(): AccountDeliveryOutcome<T> = readOutcome()
    suspend fun awaitResult(): T = when (val outcome = awaitOutcome()) {
        is AccountDeliveryOutcome.Completed -> outcome.result
        is AccountDeliveryOutcome.CommittedForSettlement -> throw AccountStoreException(outcome.failure)
        is AccountDeliveryOutcome.Failed -> throw AccountStoreException(outcome.failure)
    }
    override fun toString(): String = "AccountDeliveryOperation"
}

/**
 * Account metadata, Journal and M1 record owner. initialize opens no other store.
 * Actual host/transport/file owners must hold their captured authority and drain external effects.
 * Call retire synchronously before switch/sleep/Quit; close waits for actual admitted SQL settlement.
 * No timeout/cancellation proves settlement. No API purges retained data.
 */
interface DesktopAccountStore {
    /** Optional for existing port implementations; the SQLite owner supplies the real page owner. */
    val syncPages: DesktopAccountSyncPages? get() = null
    val domain: DesktopAccountDomain? get() = null
    val portableData: DesktopAccountPortableData? get() = null
    val materialization: DesktopAccountMaterialization? get() = null
    val accountIntents: DesktopAccountIntentMaterialization? get() = null
    val media: DesktopAccountMedia? get() = null
    val catalog: DesktopAccountCatalog? get() = null
    val interactions:DesktopAccountInteractions? get() = null
    val maintenance:DesktopAccountMaintenance? get() = null
    val recoveryReads:DesktopAccountRecoveryReads? get() = null
    val importReviewCommands:DesktopAccountImportReviewCommands? get() = null
    suspend fun initialize(): AccountReconciliationSnapshot
    suspend fun reconciliationSnapshot(): AccountReconciliationSnapshot
    suspend fun prepareBindingIntent(intent: AccountBindingIntent): AccountBindingIntent =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun readBindingIntent(intentId: AccountUuid): AccountBindingIntent? =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun cancelBindingIntent(intentId: AccountUuid): Unit =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun prepareScope(preparation: AccountScopePreparation): AccountReconciliationSnapshot
    suspend fun activateScope(activationId: AccountUuid): AccountLease
    suspend fun suspendScope(lease: AccountLease): AccountReconciliationSnapshot
    suspend fun createTrack(lease: AccountLease, change: AccountCreateTrack): AccountMutationReceipt
    /** Compatibility view; use syncPages.readProjectionPage for nullable remote fields and certainty. */
    suspend fun readTracks(lease: AccountLease, after: AccountUuid?, limit: Int = 100): List<AccountTrackProjection>
    suspend fun readDelivery(lease: AccountLease, after: AccountUuid?, limit: Int = 100): List<AccountDeliveryStatus>
    /**
     * Synchronously retain the returned ticket before awaiting. Scheduling only: a contiguous prefix
     * after the whole lineage checkpoint, stopping at a gap, alias, delayed or blocked head.
     * Network admission and original-alias reconciliation remain external obligations.
     */
    fun claimPending(lease: AccountLease): AccountDeliveryOperation<AccountDeliveryClaim?>
    fun renewDelivery(lease: AccountDeliveryLease): AccountDeliveryOperation<AccountDeliveryLease>
    /** Scheduling failure only; successful response application belongs to syncPages. */
    suspend fun failDelivery(lease: AccountDeliveryLease, reason: AccountDeliveryFailure, retryAfterMs: Long = 0): Unit
    /** After actual external settlement only; exact operation/token/revision, available after retire. */
    suspend fun settleDelivery(handle: AccountDeliverySettlement, reason: AccountDeliveryFailure, retryAfterMs: Long = 0): Unit
    fun retire(): Unit
    suspend fun close(): Unit
}
