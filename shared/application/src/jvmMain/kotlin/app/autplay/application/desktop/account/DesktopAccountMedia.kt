package app.autplay.application.desktop.account

import app.autplay.application.desktop.media.*

data class AccountMediaRecord(val storeId:AccountUuid,val scopeId:AccountUuid,val revision:Long,val formatVersion:Int=1)
data class AccountMediaTarget(val userTrackRefId:AccountUuid,val userTrackRefRowVersion:Long,val recordingId:AccountUuid,
    val variantId:AccountUuid,val sourceEvidenceId:AccountUuid)
data class MediaDownloadIntent(val record:AccountMediaRecord,val intentId:AccountUuid,val localTrackRefId:AccountUuid,
    val target:AccountMediaTarget?,val storageClass:String,val desiredState:String,val coarseState:String,
    val continuationMode:String,val stopReason:String?,val currentAttempt:Long,val nextEligibleAtMs:Long?,
    val reservedBytes:Long,val completedCopyId:AccountUuid?)
data class MediaCopy(val record:AccountMediaRecord,val copyId:AccountUuid,val localTrackRefId:AccountUuid,
    val source:AccountMediaTarget?,val etag:String?,val digestSha256:String,val totalBytes:Long,val mediaType:String,
    val formatSuffix:String,val storageClass:String,val integrityState:String,val publicationState:String,
    val locatorId:AccountUuid,val completionRevision:Long,val publicationId:AccountUuid)
data class MediaCopyAttachment(val record:AccountMediaRecord,val copyId:AccountUuid,val admissionEvidenceId:AccountUuid?,
    val localTrackRefId:AccountUuid?,val userTrackRefId:AccountUuid?,val userTrackRefRowVersion:Long?,
    val recordingId:AccountUuid?,val projectionRevision:Long?,val redirectProjectionRevision:Long?,val state:String)
data class MediaCopyOwnership(val record:AccountMediaRecord,val copyId:AccountUuid,val localOwner:LocalOwnerKey,
    val kOwnerRef:AccountUuid,val originLocalVetoRef:AccountUuid?,val provenanceKAuthorityRevision:Long,
    val acquisitionKind:String,val acquisitionId:AccountUuid,val completionRevision:Long,val publicationId:AccountUuid,val state:String)
data class MediaAttempt(val record:AccountMediaRecord,val intentId:AccountUuid,val attempt:Long,val bootId:AccountUuid,
    val sourceGeneration:Long,val intentRevision:Long,val attemptLeaseRevision:Long,val sourceEvidenceId:AccountUuid,
    val admissionEvidenceId:AccountUuid?,val operationId:AccountUuid,val resourceActivationId:AccountUuid?,val resourceGeneration:Long?,
    val etag:String?,val digestSha256:String?,val totalBytes:Long?,val prefixId:AccountUuid,val confirmedPrefixBytes:Long,
    val startedAtMs:Long,val deadlineAtMs:Long,val settledBytes:Long,val state:String,val stopReason:String?,
    val replay401Used:Boolean,val reconnectUsed:Boolean,val consecutiveNoProgress:Int)
data class MediaPrefixBlock(val record:AccountMediaRecord,val prefixId:AccountUuid,val blockIndex:Int,
    val offset:Long,val length:Int,val digestSha256:String)
data class MediaPublication(val record:AccountMediaRecord,val publicationId:AccountUuid,val copyId:AccountUuid,
    val intentId:AccountUuid?,val attempt:Long?,val sourceEvidenceId:AccountUuid?,val expectedIntentRevision:Long?,
    val expectedAttemptLeaseRevision:Long?,val expectedOwnerRevision:Long,val expectedOriginVetoRevision:Long?,
    val expectedProjectionRevision:Long?,val attachmentOutcome:String,val materializationRevision:Long?,
    val locatorId:AccountUuid,val prefixId:AccountUuid?,val completionRevision:Long,val stage:String)
data class AccountMediaCopyView(val copy:MediaCopy,val ownership:MediaCopyOwnership,val attachment:MediaCopyAttachment)
/** File owner supplies this only after forced bytes, full hash and read-only file identity checks. */
data class AccountMediaCompletion(val copy:MediaCopy,val ownership:MediaCopyOwnership,
    val attachment:MediaCopyAttachment,val publication:MediaPublication)

interface DesktopAccountMedia {
    suspend fun projection(lease:AccountLease,localTrackId:AccountUuid):JMediaProjectionCapture?
    suspend fun requestDownload(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,localTrackId:AccountUuid,storageClass:String):MediaDownloadIntent
    /** ACQUISITION and frozen target are committed together before any acquisition can be admitted. */
    suspend fun bindDownload(lease:AccountLease,operationId:AccountUuid,expectedIntentRevision:Long,evidence:MediaSourceEvidence,reservedBytes:Long):MediaDownloadIntent
    suspend fun retainObservation(lease:AccountLease,operationId:AccountUuid,evidence:MediaSourceEvidence):Unit
    suspend fun readIntent(lease:AccountLease,intentId:AccountUuid,acquisitionScopeId:AccountUuid=lease.scopeId):MediaDownloadIntent?
    /** Current original scope only; an empty page is EOF. Attempts retain their exact intent identity. */
    suspend fun readIntentPage(lease:AccountLease,after:AccountUuid?,limit:Int=100):List<MediaDownloadIntent>
    suspend fun readEvidence(lease:AccountLease,evidenceId:AccountUuid,acquisitionScopeId:AccountUuid=lease.scopeId):MediaSourceEvidence?
    suspend fun prepareAttempt(lease:AccountLease,operationId:AccountUuid,expectedIntentRevision:Long,
        previousAttemptLeaseRevision:Long?,attempt:MediaAttempt):MediaAttempt
    suspend fun updateAttempt(lease:AccountLease,operationId:AccountUuid,expectedRevision:Long,attempt:MediaAttempt):Unit
    /** Exact terminal outcome after real native/file/resource release; available after retire. */
    suspend fun settleAttempt(lease:AccountLease,operationId:AccountUuid,expectedRevision:Long,attempt:MediaAttempt):Unit
    /** Called after actual writer settlement and fsync; no per-chunk Journal events are created. */
    suspend fun confirmPrefixBlocks(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,attempt:Long,
        expectedAttemptRevision:Long,blocks:List<MediaPrefixBlock>):Unit
    suspend fun readAttempt(lease:AccountLease,intentId:AccountUuid,attempt:Long):MediaAttempt?
    suspend fun readPrefixBlocks(lease:AccountLease,prefixId:AccountUuid,afterIndex:Int,limit:Int=100):List<MediaPrefixBlock>
    suspend fun setDesiredState(lease:AccountLease,operationId:AccountUuid,intentId:AccountUuid,expectedRevision:Long,desiredState:String):Unit
    /** PREPARED SQL record precedes owned file link. No bytes/paths are accepted by J. */
    suspend fun preparePublication(lease:AccountLease,operationId:AccountUuid,completion:AccountMediaCompletion):Unit
    /** Caller holds the actual LOCAL/media-owner guard until this SQL outcome has settled. */
    suspend fun commitPublication(lease:AccountLease,operationId:AccountUuid,publicationId:AccountUuid,
        expectedRevision:Long,currentLocal:AccountLocalCapture,acquisitionScopeId:AccountUuid=lease.scopeId):AccountMediaCopyView
    suspend fun markPublicationUncertain(lease:AccountLease,operationId:AccountUuid,publicationId:AccountUuid,expectedRevision:Long):Unit
    suspend fun readPublication(lease:AccountLease,publicationId:AccountUuid,acquisitionScopeId:AccountUuid=lease.scopeId):MediaPublication?
    /** Returns retained evidence only. File owner still needs current LOCAL gate/hash/held copy lease. */
    suspend fun readCopy(lease:AccountLease,copyId:AccountUuid,acquisitionScopeId:AccountUuid=lease.scopeId):AccountMediaCopyView?
    /** Bounded original-owner Downloads/Copies view, including DETACHED copies after remote logout. */
    suspend fun readOwnedCopies(lease:AccountLease,afterScope:AccountUuid?,afterCopy:AccountUuid?,limit:Int=100):List<AccountMediaCopyView>
    suspend fun attachCopy(lease:AccountLease,operationId:AccountUuid,copyId:AccountUuid,expectedAttachmentRevision:Long,
        attachmentEvidenceId:AccountUuid,redirectProjectionRevision:Long?):MediaCopyAttachment
    /**
     * Current scope, eligible copies in raw copy_id order. Filtering precedes the result limit.
     * Scans at most 100 batches of 100 rows in one guarded J read; empty means real exhaustion.
     * Capacity, unknown or uncertain retained records refuse instead of implying LOCAL absence.
     * Returned evidence still requires the actual LOCAL guard and file-owner verification.
     */
    suspend fun readTrackCopies(lease:AccountLease,trackId:AccountUuid,after:AccountUuid?,limit:Int=100):List<AccountMediaCopyView>
}
