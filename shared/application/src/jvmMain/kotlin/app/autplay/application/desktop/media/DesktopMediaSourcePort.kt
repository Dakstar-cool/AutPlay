package app.autplay.application.desktop.media

/** Runtime-neutral source contract. JVM host composition owns the actual K/J/HTTP/native graph. */
interface DesktopMediaSourcePort {
    suspend fun resolve(capture: SelectionCapture, intent: SourceIntent): SourceResolution
    suspend fun readSourceSnapshot(capture: SelectionCapture.VaultNetwork, userTrackRefId: MediaId): MediaSourceObservation
}

sealed interface SourceIntent {
    data class StandaloneSelection(val trackId: MediaId) : SourceIntent {
        override fun toString(): String = "SourceIntent.StandaloneSelection"
    }
    data class AccountCopy(val copyId: MediaId, val acquisitionScopeId: MediaId? = null) : SourceIntent {
        override fun toString(): String = "SourceIntent.AccountCopy"
    }
    data class VaultPlayback(val projection: JMediaProjectionCapture, val playInstanceId: MediaId) : SourceIntent {
        override fun toString(): String = "SourceIntent.VaultPlayback"
    }
    /** REQUESTED/unbound descriptors are deliberately not source intents. Each attempt gets a fresh observation ID. */
    data class BoundVaultDownload(
        val intentId: MediaId, val intentRevision: Long, val sourceEvidenceId: MediaId,
        val original: MediaSourceEvidence, val admissionEvidenceId: MediaId,
        val currentProjection: JMediaProjectionCapture,
    ) : SourceIntent {
        init {
            DesktopMediaLimits.positive(intentRevision)
            require(original.evidenceId == sourceEvidenceId && original.intentId == intentId &&
                original.kind == MediaObservationKind.ACQUISITION && admissionEvidenceId != sourceEvidenceId) { "INTENT_STALE" }
        }
        override fun toString(): String = "SourceIntent.BoundVaultDownload"
    }
}

enum class MediaCode {
    AUTH_REQUIRED, AUTHORIZATION_DENIED, SOURCE_UNKNOWN, OFFLINE, CAPABILITY_UNAVAILABLE,
    RESOURCE_WAITING, RESOURCE_STALE, TLS_UNTRUSTED, ORIGIN_REJECTED, REDIRECT_REJECTED,
    REPRESENTATION_INVALID, REPRESENTATION_CHANGED, RANGE_INVALID, NETWORK_TIMEOUT,
    NETWORK_DROPPED, UNSUPPORTED_MEDIA, SEEK_FAILED, SEEK_INTERRUPTED, STORAGE_FULL,
    CHECKSUM_MISMATCH, PUBLICATION_UNCERTAIN, CANCELLED, OWNER_STALE, OWNER_UNAVAILABLE,
    RECONCILIATION_REQUIRED, ACCESS_RENEWAL_FAILED, INTENT_STALE, PROTOCOL_ERROR,
    CAPACITY_EXCEEDED, DRAIN_UNCONFIRMED,
}
enum class MediaStage { RESOLVE, LOCAL_OWNER, AUTH, ADMISSION, HEAD, RANGE, DECODE, SEEK, WRITE, VERIFY, PUBLISH, HANDOFF, DRAIN }
enum class MediaRetryAction { NONE, RETRY, RECONNECT, REOPEN, UNLOCK, RESUME, RECONCILE }
data class MediaFailure(val code: MediaCode, val stage: MediaStage, val retryAction: MediaRetryAction)
class MediaSourceException(val failure: MediaFailure) : IllegalStateException(failure.code.name)
enum class LocalAvailability { ABSENT, PARTIAL, VERIFIED_RETAINED, ATTRIBUTE_ONLY, CORRUPT, UNKNOWN }

sealed interface SourceResolution {
    class Allow(val handle: MediaSourceHandle) : SourceResolution {
        override fun toString(): String = "SourceResolution.ALLOW"
    }
    data class Deny(val failure: MediaFailure) : SourceResolution
    data class Offline(val localAvailability: LocalAvailability) : SourceResolution
    data class Unknown(val failure: MediaFailure, val localAvailability: LocalAvailability) : SourceResolution
}

data class MediaRepresentation(val digestSha256: String?, val totalBytes: Long,
    val mediaType: String, val format: MediaFormat, val kind: MediaRepresentationKind) {
    init {
        digestSha256?.let(DesktopMediaLimits::digest)
        require(kind != MediaRepresentationKind.VAULT_CONTENT || digestSha256 != null) { "REPRESENTATION_INVALID" }
        require(totalBytes in 1..DesktopMediaLimits.MAX_OBJECT_BYTES) { "REPRESENTATION_INVALID" }
        require(mediaType.toByteArray(Charsets.UTF_8).size in 1..128 && mediaType.all { it.code in 33..126 }) { "REPRESENTATION_INVALID" }
    }
    val strongEtag: String? get() = if (kind == MediaRepresentationKind.VAULT_CONTENT) "\"sha256-$digestSha256\"" else null
    override fun toString(): String = "MediaRepresentation(<redacted>)"
}
enum class MediaRepresentationKind { INDEPENDENT_LOCAL, VAULT_CONTENT }
enum class MediaFormat(val suffix: String) { MP3("mp3"), AAC("aac"), M4A("m4a"), FLAC("flac"), OPUS("opus"), WAV("wav") }
enum class MediaResourceType { PLAY_INSTANCE, DOWNLOAD_INTENT }

/** Existing resource-v1 purpose fence; attachmentRevision is explicitly null for download. */
data class MediaResourceFence(
    val type: MediaResourceType, val resourceId: MediaId, val operationId: MediaId,
    val activationId: MediaId, val generation: Long, val attachmentRevision: Long?,
    val targetVariantId: MediaId?, val recordingId: MediaId?, val nextRecordingId: MediaId?,
) {
    init {
        DesktopMediaLimits.positive(generation)
        require((type == MediaResourceType.PLAY_INSTANCE) == (attachmentRevision != null)) { "RESOURCE_STALE" }
        require(if (type == MediaResourceType.PLAY_INSTANCE) recordingId != null && targetVariantId == null
            else recordingId == null && targetVariantId != null) { "RESOURCE_STALE" }
        require(nextRecordingId == null) { "RESOURCE_STALE" }
        attachmentRevision?.let(DesktopMediaLimits::generation)
    }
    override fun toString(): String = "MediaResourceFence(<redacted>)"
}

/** Narrow owned transient buffer. No String/header-map accessor and no refresh/custody operation. */
class TransientMediaBearer(input: ByteArray) : AutoCloseable {
    private val bytes: ByteArray = input.also {
        require(it.size in 1..8_192 && it.all { byte -> (byte.toInt() and 255) in 33..126 }) { "AUTH_REQUIRED" }
    }.copyOf()
    private var closed: Boolean = false
    /** Consumer must not retain the temporary copy. Framework copies still need their owner's drain. */
    @Synchronized fun <T> withBytes(action: (ByteArray) -> T): T {
        check(!closed) { "AUTH_REQUIRED" }
        val temporary = bytes.copyOf()
        return try { action(temporary) } finally { temporary.fill(0) }
    }
    @Synchronized override fun close(): Unit { if (!closed) { closed = true; bytes.fill(0) } }
    override fun toString(): String = "TransientMediaBearer(<redacted>)"
}

/** Runtime capabilities only. Never serialize these into UI, Journal, work descriptors or logs. */
sealed interface MediaRuntimeSource {
    class ManagedFile(val managedPath: String, val fileIdentity: String) : MediaRuntimeSource {
        init {
            require(managedPath.toByteArray(Charsets.UTF_8).size in 1..4_096 &&
                managedPath.startsWith('/') && managedPath.none { it.code < 32 || it == '\\' }) { "SOURCE_UNKNOWN" }
            require(Regex("(?:0|[1-9][0-9]{0,19}):(?:0|[1-9][0-9]{0,19})").matches(fileIdentity)) { "SOURCE_UNKNOWN" }
        }
        override fun toString(): String = "MediaRuntimeSource.ManagedFile(<redacted>)"
    }
    class Network(val streamPolicy: MediaTransportPolicy, val variantId: MediaId,
        val bearer: TransientMediaBearer, val resource: MediaResourceFence,
        val credentialGeneration: Long, val sessionGeneration: Long, val accessExpiresAtMs: Long) : MediaRuntimeSource {
        init {
            DesktopMediaLimits.generation(credentialGeneration); DesktopMediaLimits.generation(sessionGeneration)
            DesktopMediaLimits.positive(accessExpiresAtMs)
        }
        val path: String get() = "/api/v1/stream/audio-variants/${variantId.value}"
        override fun toString(): String = "MediaRuntimeSource.Network(<redacted>)"
    }
}

/** Every use must dispatch bounded owned work inside a fresh actual owner-admission gate. */
sealed interface MediaSourceHandle {
    val copyId: MediaId
    val representation: MediaRepresentation
    val permitRevision: Long
    val evidence: MediaSourceEvidence?
    fun <T> withUse(action: (MediaRuntimeSource) -> T): T
    /** Synchronous fence only. Caller retains this handle until awaitReleased reports actual settlement. */
    fun retire(): Unit
    suspend fun awaitReleased(): MediaHandleRelease
}
enum class MediaHandleRelease { RELEASED, FAILED_RETAINED }

/** Required lease owner, including all admitted native/network/file work. No timeout-based release. */
interface MediaHandleOwner {
    val copyId: MediaId
    val representation: MediaRepresentation
    val permitRevision: Long
    fun <T> withCurrentUse(action: (MediaRuntimeSource) -> T): T
    fun retire(): Unit
    suspend fun awaitReleased(): MediaHandleRelease
}

class ManagedLocalHandle(private val owner: MediaHandleOwner) : MediaSourceHandle {
    override val copyId: MediaId get() = owner.copyId
    override val representation: MediaRepresentation get() = owner.representation
    override val permitRevision: Long get() = owner.permitRevision
    override val evidence: MediaSourceEvidence? get() = null
    override fun <T> withUse(action: (MediaRuntimeSource) -> T): T = owner.withCurrentUse(action)
    override fun retire(): Unit = owner.retire()
    override suspend fun awaitReleased(): MediaHandleRelease = owner.awaitReleased()
    override fun toString(): String = "ManagedLocalHandle(<redacted>)"
}
class RetainedAccountHandle(private val owner: MediaHandleOwner,
    override val evidence: MediaSourceEvidence?) : MediaSourceHandle {
    override val copyId: MediaId get() = owner.copyId
    override val representation: MediaRepresentation get() = owner.representation
    override val permitRevision: Long get() = owner.permitRevision
    override fun <T> withUse(action: (MediaRuntimeSource) -> T): T = owner.withCurrentUse(action)
    override fun retire(): Unit = owner.retire()
    override suspend fun awaitReleased(): MediaHandleRelease = owner.awaitReleased()
    override fun toString(): String = "RetainedAccountHandle(<redacted>)"
}
/** Evidence is the current admission observation; original acquisition remains separately immutable. */
class VaultPermit(private val owner: MediaHandleOwner, override val evidence: MediaSourceEvidence,
    val originalEvidence: MediaSourceEvidence?, val capture: SelectionCapture.VaultNetwork,
    val resource: MediaResourceFence) : MediaSourceHandle {
    override val copyId: MediaId get() = owner.copyId
    override val representation: MediaRepresentation get() = owner.representation
    override val permitRevision: Long get() = owner.permitRevision
    override fun <T> withUse(action: (MediaRuntimeSource) -> T): T = owner.withCurrentUse(action)
    override fun retire(): Unit = owner.retire()
    override suspend fun awaitReleased(): MediaHandleRelease = owner.awaitReleased()
    override fun toString(): String = "VaultPermit(<redacted>)"
}
