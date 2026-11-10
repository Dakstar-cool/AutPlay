package app.autplay.application.desktop.account

/** Exact retained conflict key; labels and server IDs never choose a different aggregate. */
data class AccountConflictKey(val profileId:AccountUuid,val sourceId:AccountUuid,val aggregateType:String,val localId:AccountUuid)
data class AccountConflictReview(val key:AccountConflictKey,val serverId:AccountUuid?,val rawKind:String,
    val rawResolution:String,val baseVersion:Long?,val observedVersion:Long?,val queuedRepairEventId:AccountUuid?=null)
data class AccountRetainedTable(val table:String,val scopeRelation:String,val rows:Long)
/** An inventory is neither an export nor proof of deletion or complete external media inventory. */
data class AccountRetentionInventory(val storeId:AccountUuid,val scopeId:AccountUuid,val accountId:AccountUuid?,
    val tables:List<AccountRetainedTable>,val externalInventoryRequired:List<String>,val unknownRows:Long,
    val ordinaryExportReady:Boolean=false,val physicalDeletionReady:Boolean=false)

interface DesktopAccountMaintenance {
    suspend fun readConflicts(lease:AccountLease,after:AccountConflictKey?,limit:Int=100):List<AccountConflictReview>
    /** Explicit retry preserves the exact event/sequence/hash; a terminal ACK cannot be retried. */
    suspend fun retryDeadLetter(lease:AccountLease,operationId:AccountUuid,eventId:AccountUuid,
        expectedAttempt:Long,expectedLeaseRevision:Long):Unit
    /** Repairs a terminal ACK conflict with a deliberate new event at its reviewed current version.
     * The original and server evidence are retained; unsupported/pull conflicts require fresh review. */
    suspend fun repairConflict(lease:AccountLease,conflict:AccountConflictReview,correction:AccountDomainChange):AccountDomainReceipt
    suspend fun retentionInventory(lease:AccountLease):AccountRetentionInventory
}
