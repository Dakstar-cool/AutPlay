package app.autplay.application.desktop.account

/** Exact safe response retained by J. A read never grants authority to apply or send. */
public class AccountRetainedSyncPage(public val operationId: AccountUuid, public val lineageId: AccountUuid,
    public val rawKind: String, public val reason: String, bytes: ByteArray, eventIds: List<AccountUuid>) {
    private val original = bytes.copyOf()
    public val eventIds: List<AccountUuid> = java.util.Collections.unmodifiableList(eventIds.toList())
    init { require(original.size in 1..DesktopAccountLimits.PAGE_BYTES.toInt() && eventIds.size <= 100) }
    public fun wireCopy(): ByteArray = original.copyOf()
    override fun toString(): String = "AccountRetainedSyncPage"
}
public data class AccountConflictCursor(public val profileId: AccountUuid, public val sourceId: AccountUuid,
    public val aggregateType: String, public val localId: AccountUuid)
public class AccountConflictDocument(public val cursor: AccountConflictCursor, public val serverId: AccountUuid?,
    public val kind: String, public val rawResolutionState: String, public val baseVersion: Long?,
    public val observedVersion: Long?, bytes: ByteArray) {
    private val original = bytes.copyOf()
    init { require(original.size in 1..DesktopAccountLimits.PAGE_BYTES.toInt()) }
    public fun documentCopy(): ByteArray = original.copyOf()
    override fun toString(): String = "AccountConflictDocument"
}
public data class AccountConflictPage(public val documents: List<AccountConflictDocument>, public val next: AccountConflictCursor?)
public interface DesktopAccountRecoveryReads {
    public suspend fun deferredPage(lease: AccountLease): AccountRetainedSyncPage?
    /** Whole original safe documents, <=8 MiB across this page; no label/summary substitution. */
    public suspend fun conflicts(lease: AccountLease, after: AccountConflictCursor? = null, limit: Int = 1): AccountConflictPage
}
