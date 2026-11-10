package app.autplay.desktop.media

import app.autplay.application.desktop.media.*

/** Required future media owner clock: monotonic milliseconds, checked for sleep/rollback discontinuity. */
interface DesktopMediaResolutionClock { fun monotonicMs(): Long }

enum class MediaAdmissionStep { LOCAL_LOOKUP, LOCAL_RESULT, SNAPSHOT, SNAPSHOT_RESULT, HEAD, HEAD_RESULT, ACQUIRE, RESULT, USE, DRAIN }
class MediaAdmissionRequest(
    val capture: SelectionCapture, val intent: SourceIntent?, val step: MediaAdmissionStep,
    val observation: MediaSourceObservation?, val evidence: MediaSourceEvidence?,
    val localLease: MediaLocalReadLease?, val resourceLease: MediaActiveResourceLease?,
) { override fun toString(): String = "MediaAdmissionRequest(<redacted>)" }

/**
 * ACTUAL required J/K/selection owner seam, not a DTO verifier. Hold its common nonblocking gate
 * through action. Read back protected owner/control/value/publication/authority/LOCAL and origin
 * guards + J activation/lease/mapping/intent/attachment, actual lifecycle and latest selection.
 * For NETWORK also verify the actual accepted signed profile-v1 document, trusted identity/time,
 * separate protected M5 high-water and independently applicable D1/B1 kind guards. No new M5 media
 * operation or B1 negotiation. Check session/family/generations and ordinary portable API TTL.
 * No reconstruction from SQL/snapshot. Unknown/missing readbacks refuse with fixed failure codes.
 * LOCAL does NOT inspect/require a network profile/session/device/JWT/M5/B1. Every callback must
 * start/record real bounded owned work synchronously or consume it under this same gate.
 */
interface DesktopMediaOwnerAdmissionPort {
    fun <T> withCurrent(request: MediaAdmissionRequest, action: (MediaLiveAdmission) -> T): T
}
/** Unforgeability is not claimed: only the actual required port's guarded callback authorizes use. */
interface MediaLiveAdmission {
    /** Already the minimum applicable current authority deadline; LOCAL has no remote TTL. */
    val authorityDeadlineMonotonicMs: Long
    val networkAvailable: Boolean
}

/** Started synchronously and retained before any suspension. Await retrieves this exact work. */
interface MediaOwnedWork<T> {
    fun requestCancellation(): Unit
    /**
     * Repeated waits retrieve the same result. A cancelled waiter may throw CancellationException;
     * it never discards the result or cancels actual work. A NonCancellable waiter returns/throws
     * only after actual operation/callback/buffer settlement, including terminal failure.
     */
    suspend fun awaitTerminal(): T
}
interface MediaOwnedLease {
    fun retire(): Unit
    suspend fun awaitReleased(): MediaHandleRelease
}
/**
 * Process-lifetime owner retains work/leases even when a caller dies or drain returns FAILED_RETAINED.
 * These synchronous registration/readback calls must not throw or block. Each start port also owns
 * its worker/result before returning its ticket; capacity is reserved before external work starts.
 */
interface DesktopMediaSettlementPort {
    fun retainWork(work: MediaOwnedWork<*>): Unit
    fun retainLease(lease: MediaOwnedLease): Unit
    fun releaseWorkAfterTerminal(work: MediaOwnedWork<*>): Unit
    fun releaseLeaseAfterConfirmedSettlement(lease: MediaOwnedLease): Unit
}

sealed interface MediaLocalLookup {
    class Found(val lease: MediaLocalReadLease) : MediaLocalLookup
    data class Missing(val availability: LocalAvailability) : MediaLocalLookup
}
enum class MediaCopyAcquisition { USER_DOWNLOAD, EXPLICIT_SOURCE_COPY }
/** Readback from J's exact completion/ownership transaction, never PRESENT attribute metadata. */
class MediaOwnedCopyReadback(
    val copyId: MediaId, val account: AccountLocalCapture, val acquisition: MediaCopyAcquisition,
    val acquisitionId: MediaId, val ownershipAcquisitionId: MediaId, val materializationRevision: Long?,
    val completionRevision: Long, val ownershipCompletionRevision: Long,
    val publicationId: MediaId, val ownershipPublicationId: MediaId,
    val owned: Boolean, val committed: Boolean, val removed: Boolean,
    val originalEvidence: MediaSourceEvidence?, val attachment: JMediaProjectionCapture?,
    /** Non-null only for an authenticated J canonical redirect + fresh exact variant/content evidence. */
    val redirect: MediaCanonicalAttachmentReadback?,
    val acquisitionScopeId: MediaId = account.scopeId,
) { override fun toString(): String = "MediaOwnedCopyReadback(<redacted>)" }
class MediaCanonicalAttachmentReadback(
    val aliasRecordingId: MediaId, val canonicalRecordingId: MediaId,
    val redirectProjectionRevision: Long, val evidence: MediaSourceEvidence,
) { override fun toString(): String = "MediaCanonicalAttachmentReadback(<redacted>)" }

interface MediaLocalReadLease : MediaOwnedLease {
    val copyId: MediaId
    val representation: MediaRepresentation
    val leaseRevision: Long
    /** Null ONLY for the standalone file owner's independently admitted managed import. */
    val ownership: MediaOwnedCopyReadback?
    /**
     * Nonblocking readback of the actual file owner's fresh full-hash/identity/size verification and
     * still-held read-only descriptor/lease from startLocalRead. No hash/IO runs under the gate.
     * Metadata PRESENT or a previous run's digest cannot satisfy this method.
     */
    fun verifyCurrentFileIdentityAndIntegrity(): Unit
    fun <T> withManagedFile(action: (MediaRuntimeSource.ManagedFile) -> T): T
}
interface DesktopMediaLocalSourcePort {
    /**
     * Account track lookup must read exact current attachment; direct AccountCopy permits DETACHED.
     * Resolve opaque locator solely in the owned root. Hash/size/file identity/read-only no-follow
     * checks and held copy read lease are required. Never borrow same-hash bytes across owners.
     * start is nonblocking; the file owner retains in-progress verification across cancellation.
     */
    fun startLocalRead(capture: SelectionCapture, intent: SourceIntent,
        admission: MediaLiveAdmission): MediaOwnedWork<MediaLocalLookup>
}

class MediaSnapshotHttpReply(
    val status: Int, val contentType: String?, val contentEncoding: String?,
    val cacheControl: String?, val pragma: String?, val vary: String?,
    val relevantHeaderBytes: Int, val largestRelevantHeaderBytes: Int, body: ByteArray,
) {
    private val bytes: ByteArray = body.also {
        require(it.size <= DesktopMediaLimits.SNAPSHOT_BYTES) { "PROTOCOL_ERROR" }
    }.copyOf()
    fun bodyBytes(): ByteArray = bytes.copyOf()
    override fun toString(): String = "MediaSnapshotHttpReply(<redacted>)"
}
interface DesktopMediaAuthenticatedControlPort {
    /**
     * One actual current bearer, no refresh implementation here. API's separately confirmed exact
     * HTTPS policy, fixed request route, zero query/body, no redirects/cookies/cache/content coding,
     * <=16KiB selected raw headers, <=8192 ASCII bearer, <=4096 raw response, <=15s total deadline.
     * Reject oversized headers/body during ingress, not after buffering. Enforce 5s inactivity.
     * Obtain usable access from real owner before dispatch; preserve capture/generations and the
     * attempt-wide one-401 budget. No old resolver/prepare-upload/portable TTL fallback or replay.
     */
    fun startSnapshot(capture: SelectionCapture.VaultNetwork, request: MediaSourceSnapshotRequestV1,
        admission: MediaLiveAdmission, deadlineMonotonicMs: Long): MediaOwnedWork<MediaSnapshotHttpReply>
}

class MediaHeadMetadata(val status: Int, val acceptRanges: String?, val totalBytes: Long?,
    val strongEtag: String?, val mediaType: String?, val contentEncoding: String?,
    val bodyBytes: Long, val relevantHeaderBytes: Int, val largestRelevantHeaderBytes: Int) {
    override fun toString(): String = "MediaHeadMetadata(<redacted>)"
}
interface MediaHeadInspection : MediaOwnedLease { val metadata: MediaHeadMetadata }
interface DesktopMediaHeadPort {
    /**
     * Authenticated provisional INSPECT only: zero AVPlayer/GET/audio lease. Exact frozen stream
     * policy/variant route. At most three same-origin exact-route method-preserving redirects, each
     * freshly gated; reject before contact otherwise. HEAD 15s absolute/5s inactivity; bounded
     * selected headers, no body/encoding. All native/trust/session work counts toward settlement.
     */
    fun startHead(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        evidence: MediaSourceEvidence, admission: MediaLiveAdmission,
        deadlineMonotonicMs: Long): MediaOwnedWork<MediaHeadInspection>
}

/** Nonblocking pure mapping from the real qualified media-admission owner; no decoder IO in this call. */
interface DesktopMediaFormatAdmissionPort {
    fun admit(codec: String, container: String, mediaType: String): MediaFormat
}
interface DesktopMediaJournalEvidencePort {
    /**
     * Under actual common gate verify current exact J mapping/UTR server version/Recording and no
     * conflict/tombstone/pending mutation. Download: append the fresh admissionEvidenceId, preserving
     * original intent/sourceEvidenceId/tuple. Playback: equivalent process-only observation, no
     * fabricated durable intent. No REQUESTED target binding/publication occurs in this resolver.
     * Calls are nonblocking starts; SQL worker owns actual settlement and exact result retrieval.
     */
    fun startAcceptObservation(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        observation: MediaSourceObservation, admission: MediaLiveAdmission): MediaOwnedWork<MediaSourceEvidence>
}

interface MediaActiveResourceLease : MediaOwnedLease {
    /** Fresh real snapshot repeated after capacity wait, before matching native HEAD/bytes. */
    val admittedObservation: MediaSourceObservation? get() = null
    val fence: MediaResourceFence
    val copyId: MediaId
    val permitRevision: Long
    val authorityDeadlineMonotonicMs: Long
    /** Dispatch inside a fresh owner gate; exact live purpose headers and transient access only. */
    fun <T> withNetworkSource(action: (MediaRuntimeSource.Network) -> T): T
}
sealed interface MediaResourceAcquisition {
    class Active(val lease: MediaActiveResourceLease) : MediaResourceAcquisition
    /** Actual resource owner retains WAITING liveness/promotion/cancel evidence, never an INSPECT lease. */
    class Waiting(val pending: MediaOwnedLease) : MediaResourceAcquisition
}
interface DesktopMediaResourceAdmissionPort {
    /**
     * Persist operation through J BEFORE acquire. PLAYBACK/PLAY_INSTANCE uses stable play UUID,
     * current Recording + next=null and exact attachment revision. TRANSFER/DOWNLOAD_INTENT uses
     * immutable intentId/variant; never borrows PLAY_INSTANCE. Existing operation/activation/generation
     * state machine only. No digest calculation by this slice. WAITING remains owned until exact
     * cancel or raced promotion/drain/release; one bounded phase, no local polling/refresh loop.
     */
    fun startAcquire(capture: SelectionCapture.VaultNetwork, intent: SourceIntent, evidence: MediaSourceEvidence,
        representation: MediaRepresentation, admission: MediaLiveAdmission,
        deadlineMonotonicMs: Long): MediaOwnedWork<MediaResourceAcquisition>
}
