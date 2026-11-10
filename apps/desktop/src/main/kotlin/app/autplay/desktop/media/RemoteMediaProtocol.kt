package app.autplay.desktop.media

import java.nio.ByteBuffer
import java.util.UUID

/** Separate IPC1 contract. No type here constitutes account, resource, or audio admission. */
internal object RemoteMediaProtocol {
    const val NAME: String = "autplay.remote-media"
    const val MAX_FRAME: Int = 65_536
    const val MAX_INTEGER: Long = 9_007_199_254_740_991L
    const val MAX_BYTES: Long = 2_147_483_648L
    const val MAX_POSITION: Long = 604_800_000L
    const val MAX_RANGE: Long = 262_144L
    const val MAX_DATA: Int = 61_440
    val SUFFIXES: Set<String> = setOf("mp3", "aac", "m4a", "flac", "opus", "wav")

    enum class Code {
        AUTH_REQUIRED, AUTHORIZATION_DENIED, SOURCE_UNKNOWN, OFFLINE, CAPABILITY_UNAVAILABLE,
        RESOURCE_WAITING, RESOURCE_STALE, TLS_UNTRUSTED, ORIGIN_REJECTED, REDIRECT_REJECTED,
        REPRESENTATION_INVALID, REPRESENTATION_CHANGED, RANGE_INVALID, NETWORK_TIMEOUT,
        NETWORK_DROPPED, UNSUPPORTED_MEDIA, SEEK_FAILED, SEEK_INTERRUPTED, STORAGE_FULL,
        CHECKSUM_MISMATCH, PUBLICATION_UNCERTAIN, CANCELLED, OWNER_STALE, OWNER_UNAVAILABLE,
        RECONCILIATION_REQUIRED, ACCESS_RENEWAL_FAILED, INTENT_STALE, PROTOCOL_ERROR,
        CAPACITY_EXCEEDED, DRAIN_UNCONFIRMED,
    }
    enum class Purpose { INSPECT_PLAYBACK, INSPECT_DOWNLOAD }
    enum class TransportMode { PLATFORM_PKI, CONFIRMED_SPKI }
    enum class ResourceType { PLAY_INSTANCE, DOWNLOAD_INTENT }
    enum class RenewalReason { PREEXPIRY, REJECTED_401 }
    enum class RetireReason { SELECTION, CANCEL, AUTHORITY, SLEEP, ATTEMPT_BOUNDARY, ERROR }
    enum class AckOutcome { ADMITTED, APPLIED, DRAINED, REJECTED }
    enum class Disposition { START, WRITTEN, DISCARDED }
    enum class ReadKind { HEAD, RANGE, CACHE, FILE }
    enum class ReadDecision { ALLOW, DENY }
    enum class PlaybackStatus { IDLE, BUFFERING, READY, ENDED, ERROR }
    enum class SeekOutcome { COMPLETED, INTERRUPTED, FAILED }
    enum class TransferOutcome { DELIVERED, CANCELLED, FAILED }
    enum class NetworkStatus { SATISFIED, UNSATISFIED, UNKNOWN }

    data class SourceKey(val runId: UUID, val permitId: UUID, val generation: Long)
    data class IntentFence(
        val desiredIntentRevision: Long, val selectionRevision: Long,
        val sleepFenceRevision: Long, val timerFenceRevision: Long,
    )
    data class Lifetime(val authorityExpiresAtMs: Long, val remainingLifetimeMs: Long)
    data class Representation(
        val etag: String?, val totalBytes: Long, val mediaType: String, val formatSuffix: String,
    )

    /** Ownership is transferred to the bridge. Never serialize via String/DTO/debug formatting. */
    class AccessBytes private constructor(private val bytes: ByteArray) : AutoCloseable {
        private var closed: Boolean = false
        @Synchronized internal fun <T> useBytes(action: (ByteArray) -> T): T {
            if (closed) throw Invalid()
            return action(bytes)
        }
        @Synchronized override fun close(): Unit { bytes.fill(0); closed = true }
        override fun toString(): String = "AccessBytes(REDACTED)"
        companion object {
            fun takeOwnership(bytes: ByteArray): AccessBytes {
                if (bytes.size !in 1..8192 || bytes.any { (it.toInt() and 255) !in 33..126 }) {
                    bytes.fill(0)
                    throw Invalid()
                }
                return AccessBytes(bytes)
            }
        }
    }

    sealed class Command(val op: String) {
        final override fun toString(): String = "RemoteMediaCommand($op)"
        open val source: SourceKey? = null
        open val secret: AccessBytes? = null
    }
    class SetNetworkPolicy(val revision: Long, val allowMeteredDownloads: Boolean) : Command("setNetworkPolicy")
    class InstallSource(
        override val source: SourceKey, val purpose: Purpose, val executionAttempt: Long,
        val copyId: UUID, val variantId: UUID, val streamOrigin: String, val streamUrl: String,
        val transportMode: TransportMode, val transportPolicyRevision: Long, val tlsSpkiSha256: String?,
        override val secret: AccessBytes, val credentialGeneration: Long, val sessionGeneration: Long,
        val accessExpiresAtMs: Long, val permitRevision: Long, val lifetime: Lifetime,
    ) : Command("installSource")
    class InstallRetained(
        override val source: SourceKey, val copyId: UUID, val managedPath: String,
        val fileIdentity: String, val representation: Representation, val localLeaseRevision: Long,
        val permitRevision: Long, val lifetime: Lifetime,
    ) : Command("installRetained")
    class AuthorizeBytes(
        override val source: SourceKey, val permitRevision: Long, val representation: Representation,
        val resourceType: ResourceType, val operationId: UUID, val activationId: UUID,
        val resourceGeneration: Long, val attachmentRevision: Long?, val lifetime: Lifetime,
    ) : Command("authorizeBytes")
    class RenewPermit(
        override val source: SourceKey, val expectedPermitRevision: Long, val permitRevision: Long,
        val lifetime: Lifetime,
    ) : Command("renewPermit")
    class BeginAccessRenewal(
        override val source: SourceKey, val renewalId: Long, val renewalReason: RenewalReason,
        val expectedPermitRevision: Long, val expectedCredentialGeneration: Long,
        val expectedSessionGeneration: Long, val renewalDeadlineAtMs: Long, val remainingRenewalMs: Long,
    ) : Command("beginAccessRenewal")
    class ReplaceAccess(
        override val source: SourceKey, val renewalId: Long, val renewalReason: RenewalReason,
        val expectedPermitRevision: Long, val permitRevision: Long,
        val expectedCredentialGeneration: Long, val credentialGeneration: Long,
        val expectedSessionGeneration: Long, val sessionGeneration: Long,
        override val secret: AccessBytes, val accessExpiresAtMs: Long, val lifetime: Lifetime,
    ) : Command("replaceAccess")
    class UpdateIntent(val fence: IntentFence, val wantsPlay: Boolean) : Command("updateIntent")
    class ActivateAudio(
        override val source: SourceKey, val fence: IntentFence, val audioLeaseId: UUID,
        val positionMs: Long,
    ) : Command("activateAudio") // Wire autoplay is always explicitly false.
    class Play(override val source: SourceKey, val fence: IntentFence, val audioLeaseId: UUID) : Command("play")
    class Pause(override val source: SourceKey, val fence: IntentFence, val audioLeaseId: UUID) : Command("pause")
    class Seek(
        override val source: SourceKey, val fence: IntentFence, val audioLeaseId: UUID,
        val seekId: Long, val positionMs: Long,
    ) : Command("seek")
    class StartTransfer(
        override val source: SourceKey, val transferId: UUID, val attempt: Long,
        val intentRevision: Long, val attemptLeaseRevision: Long, val offset: Long,
    ) : Command("startTransfer")
    class RetireSource(override val source: SourceKey, val reason: RetireReason) : Command("retireSource")
    class Close : Command("close")

    sealed class Reply {
        final override fun toString(): String = "RemoteMediaReply(REDACTED)"
    }
    // START opens one empty flow-control slot initially or once after exact accessApplied.
    // Its sequence is zero; a renewal does not reset the emitted data sequence or nextOffset.
    // Capacity never substitutes for the current per-read and file-owner authority checks.
    class DataConsumed(
        val source: SourceKey, val transferId: UUID, val attempt: Long, val sequence: Long,
        val nextOffset: Long, val creditBytes: Int, val disposition: Disposition,
    ) : Reply()
    class ReadResponse(
        val source: SourceKey, val readId: Long, val permitRevision: Long,
        val credentialGeneration: Long?, val transportPolicyRevision: Long?, val decision: ReadDecision,
        val lifetime: Lifetime?, val code: Code?,
    ) : Reply()

    sealed interface Incoming
    sealed class Event : Incoming {
        final override fun toString(): String = "RemoteMediaEvent(REDACTED)"
    }
    class Hello : Event()
    class Ready : Event()
    class Ack(val id: Long, val outcome: AckOutcome, val code: Code?) : Event()
    sealed class SourceEvent(val source: SourceKey, val eventSeq: Long) : Event()
    class Inspected(
        source: SourceKey, eventSeq: Long, val permitRevision: Long, val credentialGeneration: Long,
        val transportPolicyRevision: Long, val representation: Representation,
    ) : SourceEvent(source, eventSeq)
    class Playback(
        source: SourceKey, eventSeq: Long, val audioLeaseId: UUID, val fence: IntentFence,
        val status: PlaybackStatus, val positionMs: Long, val durationMs: Long?,
        val isPlaying: Boolean, val code: Code?,
    ) : SourceEvent(source, eventSeq)
    class SeekResult(
        source: SourceKey, eventSeq: Long, val audioLeaseId: UUID, val fence: IntentFence,
        val seekId: Long, val outcome: SeekOutcome, val positionMs: Long, val code: Code?,
    ) : SourceEvent(source, eventSeq)
    class AuthNeeded(
        source: SourceKey, eventSeq: Long, val executionAttempt: Long, val renewalId: Long,
        val permitRevision: Long, val rejectedCredentialGeneration: Long, val sessionGeneration: Long,
    ) : SourceEvent(source, eventSeq)
    class AccessState(
        source: SourceKey, eventSeq: Long, val applied: Boolean, val renewalId: Long,
        val renewalReason: RenewalReason, val permitRevision: Long, val credentialGeneration: Long,
        val sessionGeneration: Long,
    ) : SourceEvent(source, eventSeq)
    class TransferEnd(
        source: SourceKey, eventSeq: Long, val transferId: UUID, val attempt: Long,
        val nextOffset: Long, val outcome: TransferOutcome, val code: Code?,
    ) : SourceEvent(source, eventSeq)
    class SourceFailure(source: SourceKey, eventSeq: Long, val code: Code) : SourceEvent(source, eventSeq)
    class ReadAdmission(
        source: SourceKey, eventSeq: Long, val readId: Long, val permitRevision: Long,
        val credentialGeneration: Long?, val transportPolicyRevision: Long?, val kind: ReadKind,
        val start: Long?, val end: Long?,
    ) : SourceEvent(source, eventSeq)
    class Network(
        val runId: UUID, val revision: Long, val status: NetworkStatus,
        val expensive: Boolean?, val constrained: Boolean?,
    ) : Event()

    /** Exactly one owned frame, transferred to the file owner; close wipes it after settlement. */
    class DataFrame(
        val transferId: UUID, val attempt: Long, val sequence: Long, val offset: Long,
        private val payload: ByteArray,
    ) : Incoming, AutoCloseable {
        val size: Int get() = payload.size
        fun <T> withBytes(action: (ByteBuffer) -> T): T = action(ByteBuffer.wrap(payload).asReadOnlyBuffer())
        override fun close(): Unit = payload.fill(0)
        override fun toString(): String = "RemoteMediaData(REDACTED)"
    }
    class Invalid : Exception("PROTOCOL_ERROR")

    internal sealed class Scalar {
        class Text(val value: String) : Scalar()
        class Integer(val value: Long) : Scalar()
        class BooleanValue(val value: Boolean) : Scalar()
        class Secret(val value: AccessBytes) : Scalar()
        object Null : Scalar()
        final override fun toString(): String = "REDACTED"
    }
    internal class Fields : LinkedHashMap<String, Scalar>() {
        fun putValue(key: String, value: Any?): Unit {
            put(key, when (value) {
                null -> Scalar.Null
                is String -> Scalar.Text(value)
                is UUID -> Scalar.Text(value.toString())
                is Enum<*> -> Scalar.Text(value.name)
                is Long -> Scalar.Integer(value)
                is Int -> Scalar.Integer(value.toLong())
                is Boolean -> Scalar.BooleanValue(value)
                is AccessBytes -> Scalar.Secret(value)
                else -> throw Invalid()
            })
        }
        override fun toString(): String = "RemoteMediaFields(REDACTED)"
    }

    internal fun commandFields(runId: UUID, id: Long, command: Command): Fields = Fields().apply {
        putValue("v", 1); putValue("type", "command"); putValue("id", id)
        putValue("op", command.op); putValue("runId", runId)
        command.source?.let { if (it.runId != runId) throw Invalid(); source(it) }
        when (command) {
            is SetNetworkPolicy -> { putValue("revision", command.revision); putValue("allowMeteredDownloads", command.allowMeteredDownloads) }
            is InstallSource -> {
                putValue("purpose", command.purpose); putValue("executionAttempt", command.executionAttempt)
                putValue("copyId", command.copyId); putValue("variantId", command.variantId)
                putValue("streamOrigin", command.streamOrigin); putValue("streamUrl", command.streamUrl)
                putValue("transportMode", command.transportMode); putValue("transportPolicyRevision", command.transportPolicyRevision)
                putValue("tlsSpkiSha256", command.tlsSpkiSha256); putValue("accessToken", command.secret)
                putValue("credentialGeneration", command.credentialGeneration); putValue("sessionGeneration", command.sessionGeneration)
                putValue("accessExpiresAtMs", command.accessExpiresAtMs); putValue("permitRevision", command.permitRevision); lifetime(command.lifetime)
            }
            is InstallRetained -> {
                putValue("copyId", command.copyId); putValue("managedPath", command.managedPath)
                putValue("fileIdentity", command.fileIdentity); representation(command.representation)
                putValue("localLeaseRevision", command.localLeaseRevision); putValue("permitRevision", command.permitRevision); lifetime(command.lifetime)
            }
            is AuthorizeBytes -> {
                putValue("permitRevision", command.permitRevision); representation(command.representation)
                putValue("resourceType", command.resourceType); putValue("operationId", command.operationId)
                putValue("activationId", command.activationId); putValue("resourceGeneration", command.resourceGeneration)
                putValue("attachmentRevision", command.attachmentRevision); lifetime(command.lifetime)
            }
            is RenewPermit -> {
                putValue("expectedPermitRevision", command.expectedPermitRevision); putValue("permitRevision", command.permitRevision); lifetime(command.lifetime)
            }
            is BeginAccessRenewal -> {
                putValue("renewalId", command.renewalId); putValue("renewalReason", command.renewalReason)
                putValue("expectedPermitRevision", command.expectedPermitRevision)
                putValue("expectedCredentialGeneration", command.expectedCredentialGeneration)
                putValue("expectedSessionGeneration", command.expectedSessionGeneration)
                putValue("renewalDeadlineAtMs", command.renewalDeadlineAtMs); putValue("remainingRenewalMs", command.remainingRenewalMs)
            }
            is ReplaceAccess -> {
                putValue("renewalId", command.renewalId); putValue("renewalReason", command.renewalReason)
                putValue("expectedPermitRevision", command.expectedPermitRevision); putValue("permitRevision", command.permitRevision)
                putValue("expectedCredentialGeneration", command.expectedCredentialGeneration); putValue("credentialGeneration", command.credentialGeneration)
                putValue("expectedSessionGeneration", command.expectedSessionGeneration); putValue("sessionGeneration", command.sessionGeneration)
                putValue("accessToken", command.secret); putValue("accessExpiresAtMs", command.accessExpiresAtMs); lifetime(command.lifetime)
            }
            is UpdateIntent -> { intent(command.fence); putValue("wantsPlay", command.wantsPlay) }
            is ActivateAudio -> {
                intent(command.fence); putValue("audioLeaseId", command.audioLeaseId)
                putValue("positionMs", command.positionMs); putValue("autoplay", false)
            }
            is Play -> { intent(command.fence); putValue("audioLeaseId", command.audioLeaseId) }
            is Pause -> { intent(command.fence); putValue("audioLeaseId", command.audioLeaseId) }
            is Seek -> {
                intent(command.fence); putValue("audioLeaseId", command.audioLeaseId)
                putValue("seekId", command.seekId); putValue("positionMs", command.positionMs)
            }
            is StartTransfer -> {
                putValue("transferId", command.transferId); putValue("attempt", command.attempt)
                putValue("intentRevision", command.intentRevision); putValue("attemptLeaseRevision", command.attemptLeaseRevision)
                putValue("offset", command.offset)
            }
            is RetireSource -> putValue("reason", command.reason)
            is Close -> Unit
        }
    }
    internal fun replyFields(reply: Reply): Fields = Fields().apply {
        putValue("v", 1)
        when (reply) {
            is DataConsumed -> {
                putValue("type", "dataConsumed"); source(reply.source)
                putValue("transferId", reply.transferId); putValue("attempt", reply.attempt); putValue("sequence", reply.sequence)
                putValue("nextOffset", reply.nextOffset); putValue("creditBytes", reply.creditBytes); putValue("disposition", reply.disposition)
            }
            is ReadResponse -> {
                putValue("type", "readDecision"); source(reply.source)
                putValue("readId", reply.readId); putValue("permitRevision", reply.permitRevision)
                putValue("credentialGeneration", reply.credentialGeneration); putValue("transportPolicyRevision", reply.transportPolicyRevision)
                putValue("decision", reply.decision); putValue("authorityExpiresAtMs", reply.lifetime?.authorityExpiresAtMs)
                putValue("remainingLifetimeMs", reply.lifetime?.remainingLifetimeMs); putValue("code", reply.code)
            }
        }
    }
    internal fun helloFields(): Fields = Fields().apply {
        putValue("v", 1); putValue("type", "hello"); putValue("protocol", NAME); putValue("maxFrameBytes", MAX_FRAME)
    }
    private fun Fields.source(source: SourceKey): Unit {
        putValue("runId", source.runId); putValue("permitId", source.permitId); putValue("generation", source.generation)
    }
    private fun Fields.intent(fence: IntentFence): Unit {
        putValue("desiredIntentRevision", fence.desiredIntentRevision); putValue("selectionRevision", fence.selectionRevision)
        putValue("sleepFenceRevision", fence.sleepFenceRevision); putValue("timerFenceRevision", fence.timerFenceRevision)
    }
    private fun Fields.lifetime(lifetime: Lifetime): Unit {
        putValue("authorityExpiresAtMs", lifetime.authorityExpiresAtMs); putValue("remainingLifetimeMs", lifetime.remainingLifetimeMs)
    }
    private fun Fields.representation(representation: Representation): Unit {
        putValue("etag", representation.etag); putValue("totalBytes", representation.totalBytes)
        putValue("mediaType", representation.mediaType); putValue("formatSuffix", representation.formatSuffix)
    }
}
