package app.autplay.application.desktop.account

import kotlinx.serialization.json.JsonObject

/** Account-mode commands only; descriptive IDs are never inferred from names or byte hashes. */
sealed interface AccountDomainCommand {
    val localId: AccountUuid
    data class PatchTrack(override val localId: AccountUuid, val fields: JsonObject) : AccountDomainCommand
    data class Library(override val localId: AccountUuid, val trackId: AccountUuid, val removed: Boolean) : AccountDomainCommand
    data class Preference(override val localId: AccountUuid, val trackId: AccountUuid, val value: String,
        val excludedFromTaste: Boolean, val attribution: JsonObject? = null) : AccountDomainCommand
    data class Playlist(override val localId: AccountUuid, val name: String, val description: String?,
        val create: Boolean) : AccountDomainCommand
    data class Entry(override val localId: AccountUuid, val playlistId: AccountUuid, val trackId: AccountUuid,
        val positionKey: String, val move: Boolean, val attribution: JsonObject? = null,
        val serverOrdering:Boolean=false,val beforeEntryId:AccountUuid?=null) : AccountDomainCommand
    data class Delete(override val localId: AccountUuid, val aggregateType: String) : AccountDomainCommand
    /** Exact specialized v1 payload, checked against the supported field/enum semantics by the adapter. */
    data class Listen(override val localId: AccountUuid, val payload: JsonObject, val startedAtMs:Long?=null) : AccountDomainCommand
    data class Interaction(override val localId:AccountUuid,val payload:JsonObject):AccountDomainCommand
}

data class AccountDomainChange(val operationId: AccountUuid, val occurredAtMs: Long, val command: AccountDomainCommand)

data class AccountDomainExpected(val kind: AccountProjectionKind, val localId: AccountUuid,
    val header: AccountLocalDomainHeader?)
data class AccountDomainReview(val expected: List<AccountDomainExpected>,
    val playlistId: AccountUuid? = null, val occurrenceOrder: List<AccountUuid>? = null)
data class AccountDomainReceipt(val operationId: AccountUuid, val aggregateId: AccountUuid,
    val eventId: AccountUuid?, val localIntentId: AccountUuid?)

data class AccountQueueEntry(val entryId: AccountUuid, val trackId: AccountUuid, val position: Int)
data class AccountQueueSnapshot(val queueId: AccountUuid, val revision: Long, val entries: List<AccountQueueEntry>,
    val repeat: String, val shuffle: Boolean, val sessionId: AccountUuid?, val currentEntryId: AccountUuid?)
data class AccountListeningCheckpoint(val listeningId: AccountUuid, val queueId: AccountUuid,
    val queueEntryId: AccountUuid, val sessionId: AccountUuid, val trackId: AccountUuid,
    val startedAtMs: Long, val playedMs: Long, val positionMs: Long, val revision: Long,
    val origin: String, val context: String, val excludedFromTaste: Boolean, val finalized: Boolean)

enum class AccountPolicyKind { TRAINING_CONSENT, STATISTICS_VISIBILITY }
data class AccountConfirmedPolicy(val kind: AccountPolicyKind, val revision: Long, val enabled: Boolean,
    val confirmedAtMs: Long, val operationId: AccountUuid, val pendingKReference: AccountUuid?,
    val rawState: String = if (enabled) "ENABLED" else "DISABLED") {
    val available:Boolean get() = when(kind){
        AccountPolicyKind.TRAINING_CONSENT -> rawState in setOf("GRANTED","DENIED","WITHDRAWN")
        AccountPolicyKind.STATISTICS_VISIBILITY -> rawState in setOf("ENABLED","DISABLED")
    }
}
enum class AccountWorkKind { SYNC, PORTABLE, METADATA, IMPORT_REVIEW, DOWNLOAD }
data class AccountWorkDescriptor(val workId: AccountUuid, val kind: AccountWorkKind,
    val subjectId: AccountUuid, val generation: Long, val nextAttemptAtMs: Long)
data class AccountWorkClaim(val descriptor: AccountWorkDescriptor, val bootId: AccountUuid,
    val leaseToken: AccountUuid, val leaseRevision: Long, val expiresAtMs: Long)
data class AccountImportReview(override val reviewId: AccountUuid, override val localJobId: AccountUuid,
    override val localEntryId: AccountUuid, val candidateId: AccountUuid, override val serverJobId: AccountUuid,
    override val serverEntryId: AccountUuid, val rank: Int, override val predecessor: AccountUuid?, override val operationId: AccountUuid
) : AccountImportReviewReference

interface DesktopAccountDomain {
    suspend fun readExact(lease: AccountLease, kind: AccountProjectionKind, id: AccountUuid): AccountLocalDomainRow?
    suspend fun readTrackPreferences(lease: AccountLease, trackId: AccountUuid): List<AccountLocalDomainRow>
    suspend fun readMutationOutcome(lease: AccountLease, change: AccountDomainChange, review: AccountDomainReview): AccountDomainReceipt?
    suspend fun readEditablePlaylist(lease: AccountLease, playlistId: AccountUuid): AccountPlaylistPage
    /** Compare the displayed headers/order and write the existing domain event in one transaction. */
    suspend fun mutateReviewed(lease: AccountLease, change: AccountDomainChange, review: AccountDomainReview): AccountDomainReceipt
    suspend fun readProjectionPage(lease: AccountLease, kind: AccountProjectionKind, after: AccountUuid?, limit: Int = 100): List<AccountLocalDomainRow>
    /** Scoped occurrence order and track metadata under the same guarded read; no name-based join. */
    suspend fun readPlaylistPage(lease: AccountLease, playlistId: AccountUuid,
        after: AccountPlaylistCursor?, limit: Int = 100): AccountPlaylistPage
    suspend fun mutate(lease: AccountLease, change: AccountDomainChange): AccountDomainReceipt
    suspend fun saveQueue(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long, queue: AccountQueueSnapshot): Unit
    suspend fun readQueue(lease: AccountLease, queueId: AccountUuid): AccountQueueSnapshot?
    /** The checkpoint and nonfinal logical listen are a single SQL effect. No queue wire is minted. */
    suspend fun checkpoint(lease: AccountLease, operationId: AccountUuid, expectedRevision: Long,
        checkpoint: AccountListeningCheckpoint, finalPayload: JsonObject? = null): AccountDomainReceipt?
    suspend fun readCheckpoint(lease: AccountLease, listeningId: AccountUuid): AccountListeningCheckpoint?
    suspend fun confirmPolicy(lease: AccountLease, policy: AccountConfirmedPolicy): Unit
    suspend fun readPolicies(lease: AccountLease): List<AccountConfirmedPolicy>
    suspend fun retainImportReview(lease: AccountLease, review: AccountImportReview): Unit
    suspend fun readImportReviews(lease: AccountLease, after: AccountUuid?, limit: Int = 100): List<AccountImportReview>
    suspend fun enqueueWork(lease: AccountLease, operationId: AccountUuid, work: AccountWorkDescriptor): Unit
    suspend fun claimWork(lease: AccountLease, operationId: AccountUuid, workId: AccountUuid): AccountWorkClaim?
    suspend fun renewWork(lease: AccountLease, operationId: AccountUuid, claim: AccountWorkClaim): AccountWorkClaim
    /** Exact per-operation claim/renew result, including after retire; never permission to execute. */
    suspend fun recoverWorkOutcome(lease: AccountLease, operationId: AccountUuid): AccountWorkClaim?
    /** Available only after the external effect has actually drained; expiry is not drain evidence. */
    suspend fun settleWork(lease: AccountLease, operationId: AccountUuid, claim: AccountWorkClaim,
        state: String, nextAttemptAtMs: Long): Unit
}

/** Source identity is issued by J for the canonical already-open standalone owner. No path is stored. */
data class AccountSourceRegistration(val sourceStoreId: AccountUuid, val ownerReference: AccountUuid)
class AccountSelectedFact(val changeId: String, val aggregateId: String, val kind: String,
    val version: Long, val occurredAtMs: Long, bytes: ByteArray) {
    private val original: ByteArray = bytes.copyOf()
    val canonicalBytes: ByteArray get() = original.copyOf()
    override fun toString(): String = "AccountSelectedFact"
}
class AccountSelectedSourceSnapshot(val source: AccountSourceRegistration, facts: List<AccountSelectedFact>,
    dependencies: Map<String, JsonObject>) {
    val facts: List<AccountSelectedFact> = java.util.Collections.unmodifiableList(facts.toList())
    val dependencies: Map<String, JsonObject> = java.util.Collections.unmodifiableMap(dependencies.toMap())
    override fun toString(): String = "AccountSelectedSourceSnapshot"
}
/** Holds the standalone admission barrier; exact bytes/dependencies must be rechecked before return. */
interface AccountSelectedSourceCapture : AutoCloseable {
    val snapshot: AccountSelectedSourceSnapshot
    fun stillExact(): Boolean
    override fun close(): Unit
}
interface DesktopSelectedSourcePort {
    /** No SQL transaction is held while acquiring this barrier. At most 100 explicit IDs. */
    suspend fun capture(source: AccountSourceRegistration, selectedChangeIds: List<String>): AccountSelectedSourceCapture
}
data class AccountSelectionConsent(val selectionId: AccountUuid, val reviewRevision: Long,
    val ownershipCutoffMs: Long, val bindingCommitId: AccountUuid, val authorityRevision: Long,
    val selectedChangeIds: List<String>)
data class AccountMaterializationLink(val sourceChangeId: String, val targetScopeId: AccountUuid,
    val targetAccountId: AccountUuid?, val targetAggregateId: AccountUuid,
    val lineageId: AccountUuid?, val eventId: AccountUuid?, val localIntentId: AccountUuid?)
data class AccountSelectedFactStatus(val changeId:String,val kind:String,val rawState:String,val reason:String?)
data class AccountSelectionStatus(val selectionId:AccountUuid,val reviewRevision:Long,val rawState:String,
    val facts:List<AccountSelectedFactStatus>,val links:List<AccountMaterializationLink>)
interface DesktopAccountMaterialization {
    suspend fun readSourceRegistration(lease: AccountLease): AccountSourceRegistration?
    suspend fun readSelection(lease:AccountLease,selectionId:AccountUuid):AccountSelectionStatus?
    suspend fun registerSource(lease: AccountLease, operationId: AccountUuid, canonicalOwnerReference: AccountUuid): AccountSourceRegistration
    /** User consent is required per frozen selection; this API never selects or captures extra facts. */
    suspend fun copySelected(lease: AccountLease, consent: AccountSelectionConsent,
        source: AccountSourceRegistration, port: DesktopSelectedSourcePort): Unit
    suspend fun materializeSelected(lease: AccountLease, selectionId: AccountUuid,
        reviewRevision: Long, port: DesktopSelectedSourcePort): List<AccountMaterializationLink>
}
