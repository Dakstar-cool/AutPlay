package app.autplay.application.desktop.account

/** A page capture is a scheduling/comparison descriptor, never binding or network authority. */
enum class AccountPageKind { PULL, BOOTSTRAP }
enum class AccountPageReset { CURSOR_INVALID, DEVICE_RESET_REQUIRED, BOOTSTRAP_SNAPSHOT_INVALID, USER_REQUEST }

data class AccountSyncState(
    val lineageId: AccountUuid,
    val deviceId: AccountUuid,
    val journalEpoch: AccountUuid,
    val revision: Long,
    val cursor: String?,
    val serverSequence: Long,
    val state: String,
    val snapshotId: AccountUuid?,
    val pageToken: String?,
    val finalCursor: String?,
    val snapshotHighWater: Long?,
    val bootstrapState: String,
    val pendingEventCount: Long,
) { override fun toString(): String = "AccountSyncState" }

data class AccountPageCapture(
    val operationId: AccountUuid,
    val account: AccountLease,
    val userId: AccountUuid,
    val kind: AccountPageKind,
    val original: AccountSyncState,
) {
    /** RESET retains prior evidence locally but starts the next remote snapshot with explicit nulls. */
    val requestSnapshotId: AccountUuid? get() = if (original.bootstrapState == "BOOTSTRAPPING") original.snapshotId else null
    val requestPageToken: String? get() = if (original.bootstrapState == "BOOTSTRAPPING") original.pageToken else null
    override fun toString(): String = "AccountPageCapture"
}

data class AccountPageReceipt(
    val operationId: AccountUuid,
    /** APPLIED, DEFERRED, RESET_REQUIRED or ACK_APPLIED; no implied binding/host readiness. */
    val state: String,
    val hasMore: Boolean,
    val acknowledgedThrough: Long?,
) { override fun toString(): String = "AccountPageReceipt" }

sealed interface AccountPageOutcome<out T> {
    class Completed<T>(val result: T) : AccountPageOutcome<T>
    /** Proven SQL/capture result, but cleanup failed. Only settlement/readback is permitted. */
    class Retained<T>(val result: T, val failure: AccountFailure) : AccountPageOutcome<T>
    /** May include unknown SQL settlement. Absence of a result never proves rollback or releases a live owner. */
    class Failed(val failure: AccountFailure) : AccountPageOutcome<Nothing>
}

/** Keep before awaiting or entering a timeout. Repeated awaits never replay SQL. */
class AccountPageOperation<T>(
    val operationId: AccountUuid,
    private val outcome: suspend () -> AccountPageOutcome<T>,
) {
    suspend fun awaitOutcome(): AccountPageOutcome<T> = outcome()
    suspend fun awaitResult(): T = when (val value = awaitOutcome()) {
        is AccountPageOutcome.Completed -> value.result
        is AccountPageOutcome.Retained -> throw AccountStoreException(value.failure)
        is AccountPageOutcome.Failed -> throw AccountStoreException(value.failure)
    }
    override fun toString(): String = "AccountPageOperation"
}

/**
 * Source-only owner of actual sync v1 responses. Credentials/HTTP/B1 admission are external.
 * Capture one page, perform the external operation outside SQL, then apply or settle that capture.
 * A cancelled caller must recover its ticket and settle the actual external operation first.
 * Late denied ACKs use the EXISTING exact failDelivery/settleDelivery path; no cross-owner grant.
 * Unknown safe pages stay deferred with the old cursor. No automatic purge/compaction is provided.
 */
interface DesktopAccountSyncPages {
    suspend fun readSyncState(lease: AccountLease): AccountSyncState
    /** Retained local projection rows, including explicit incomplete/deleted states. An empty page
     * never establishes complete history/statistics; use portableData's explicit completeness. */
    suspend fun readProjectionPage(lease: AccountLease, kind: AccountProjectionKind, after: AccountUuid?, limit: Int = 100): List<AccountDomainProjection>
    fun capturePage(lease: AccountLease, kind: AccountPageKind): AccountPageOperation<AccountPageCapture>
    /** DEFERRED retains the exact delivery lease for failDelivery/settleDelivery; no ACK checkpoint
     * or delivery state changed. Retry may replay only the retained exact response. */
    fun applyAckPage(delivery: AccountDeliveryLease, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt>
    fun applyPullPage(capture: AccountPageCapture, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt>
    fun applyBootstrapPage(capture: AccountPageCapture, wireBytes: ByteArray): AccountPageOperation<AccountPageReceipt>
    fun resetPage(capture: AccountPageCapture, reason: AccountPageReset): AccountPageOperation<AccountPageReceipt>
    /** Drain only, after actual external settlement; exact capture, also available after retire. */
    suspend fun settlePage(capture: AccountPageCapture): Unit
}
