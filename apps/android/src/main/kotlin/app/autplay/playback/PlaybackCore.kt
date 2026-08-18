package app.autplay.playback

import app.autplay.domain.LocalId
import java.util.UUID

/**
 * Pure P08 playback values. Persistence adapters deliberately persist only [PlaybackQueueSnapshot]
 * and [LogicalListeningCheckpoint]; neither type has a Vault URL, token, or content URI field.
 */
data class PlaybackQueueSnapshot(
    val snapshotId: LocalId,
    val entries: List<PlaybackQueueEntry>,
    val currentEntryId: LocalId?,
    val currentPositionMs: Long,
) {
    init {
        require(currentPositionMs >= 0)
        require(entries.map { it.queueEntryId }.distinct().size == entries.size)
        require(entries.map { it.position }.distinct().size == entries.size)
        require(entries == entries.sortedBy { it.position }) { "Queue entries must be ordered." }
        require(currentEntryId == null || entries.any { it.queueEntryId == currentEntryId })
    }
}

/** A queue entry, not a track, is the identity of a playable queue item: duplicates are intentional. */
data class PlaybackQueueEntry(
    val queueEntryId: LocalId,
    val trackRefId: LocalId,
    val position: Long,
    val attribution: PlaybackRecommendationAttribution? = null,
)

/** Framework-neutral input for the eventual Media3 mapper. [mediaId] is stable across restoration. */
data class StableMediaItem(
    val mediaId: String,
    val queueEntryId: LocalId,
    val trackRefId: LocalId,
)

object QueueMediaItemMapper {
    fun map(entry: PlaybackQueueEntry): StableMediaItem =
        StableMediaItem(mediaId = entry.queueEntryId.value, queueEntryId = entry.queueEntryId, trackRefId = entry.trackRefId)

    fun restore(snapshot: PlaybackQueueSnapshot): QueueRestore = QueueRestore(
        items = snapshot.entries.map(::map),
        currentIndex = snapshot.currentEntryId?.let { current -> snapshot.entries.indexOfFirst { it.queueEntryId == current } } ?: 0,
        currentPositionMs = snapshot.currentPositionMs,
    )
}

data class QueueRestore(
    val items: List<StableMediaItem>,
    val currentIndex: Int,
    val currentPositionMs: Long,
) {
    init {
        require(currentPositionMs >= 0)
        require((items.isEmpty() && currentIndex == 0) || (items.isNotEmpty() && currentIndex in items.indices))
    }
}

enum class PlaybackUnavailableReason {
    LOCAL_UNREADABLE_AND_VAULT_UNAVAILABLE,
    LOCAL_MISSING_AND_VAULT_UNAVAILABLE,
    LOCAL_PERMISSION_REVOKED_AND_VAULT_UNAVAILABLE,
    VAULT_AUTHORIZATION_UNAVAILABLE,
    NO_AUDIO_SOURCE,
}

/** A URI is runtime-only and must be freshly read before each source selection. */
data class ReadableLocalSource(val runtimeUri: String) {
    init { require(runtimeUri.isNotBlank()) }
}

/** A freshly authorized Vault request. Its URL is intentionally not suitable for persistence. */
data class FreshVaultStream(val runtimeUrl: String) {
    init { require(runtimeUrl.isNotBlank()) }
}

sealed interface LocalSourceProbe {
    data class Readable(val source: ReadableLocalSource) : LocalSourceProbe
    data object Missing : LocalSourceProbe
    data object PermissionRevoked : LocalSourceProbe
    data object Unreadable : LocalSourceProbe
    data object Absent : LocalSourceProbe
}

sealed interface PlaybackSourceSelection {
    data class Local(val source: ReadableLocalSource) : PlaybackSourceSelection
    data class Vault(val stream: FreshVaultStream) : PlaybackSourceSelection
    data class Unavailable(val reason: PlaybackUnavailableReason) : PlaybackSourceSelection
}

/**
 * Resolves local first. The caller supplies a new Vault authorization only after the local probe
 * failed, so cached/persisted server URLs cannot become a source of truth.
 */
object PlaybackSourceResolver {
    fun select(local: LocalSourceProbe, freshVaultStream: (() -> FreshVaultStream?)?): PlaybackSourceSelection = when (local) {
        is LocalSourceProbe.Readable -> PlaybackSourceSelection.Local(local.source)
        else -> freshVaultStream?.invoke()?.let(PlaybackSourceSelection::Vault)
            ?: PlaybackSourceSelection.Unavailable(unavailableReason(local))
    }

    private fun unavailableReason(local: LocalSourceProbe): PlaybackUnavailableReason = when (local) {
        LocalSourceProbe.Missing -> PlaybackUnavailableReason.LOCAL_MISSING_AND_VAULT_UNAVAILABLE
        LocalSourceProbe.PermissionRevoked -> PlaybackUnavailableReason.LOCAL_PERMISSION_REVOKED_AND_VAULT_UNAVAILABLE
        LocalSourceProbe.Unreadable -> PlaybackUnavailableReason.LOCAL_UNREADABLE_AND_VAULT_UNAVAILABLE
        LocalSourceProbe.Absent -> PlaybackUnavailableReason.NO_AUDIO_SOURCE
        is LocalSourceProbe.Readable -> error("Readable source is selected before fallback.")
    }
}

/** Immutable recommendation context copied from the queue entry, never inferred from a track. */
data class PlaybackRecommendationAttribution(
    val recommendationRequestId: String,
    val recordingId: String,
    val sourceRank: Int,
    val source: String,
    val surface: String,
    val localImpressionId: LocalId,
    val serverImpressionId: String?,
) {
    init {
        requireUuid(recommendationRequestId)
        requireUuid(recordingId)
        require(serverImpressionId == null || isUuid(serverImpressionId))
        require(sourceRank in 1..1_000)
        require(TOKEN.matches(source) && TOKEN.matches(surface))
    }
}

/** Durable checkpoint for exactly one logical listening session and its stable event ID. */
data class LogicalListeningCheckpoint(
    val listeningEventId: LocalId,
    val queueEntryId: LocalId,
    val trackRefId: LocalId,
    val startedAtMs: Long,
    val startPositionMs: Long,
    val lastObservedPositionMs: Long,
    val accumulatedPlayedMs: Long,
    val attribution: PlaybackRecommendationAttribution?,
    val ownerBinding: PlaybackSessionOwnerBinding? = null,
    val finalized: Boolean = false,
) {
    init {
        require(startedAtMs >= 0 && startPositionMs >= 0 && lastObservedPositionMs >= 0)
        require(accumulatedPlayedMs in 0..MAX_ACCUMULATED_PLAYED_MS)
    }
}

/** Immutable owner captured when the logical session starts; profile switches cannot retarget it. */
data class PlaybackSessionOwnerBinding(
    val userId: String,
    val deviceId: String,
    val serverProfileId: String,
) {
    init {
        requireUuid(userId)
        requireUuid(deviceId)
        requireUuid(serverProfileId)
    }
}

data class FinalizedListeningEvent(
    val listeningEventId: LocalId,
    val queueEntryId: LocalId,
    val trackRefId: LocalId,
    val startedAtMs: Long,
    val playedMs: Long,
    val durationMs: Long?,
    val endPositionMs: Long,
    val attribution: PlaybackRecommendationAttribution?,
) {
    init { require(playedMs >= 0 && endPositionMs >= 0 && (durationMs == null || durationMs > 0)) }
}

/**
 * Lifecycle callbacks provide a bounded monotonic-clock delta while actively playing. A seek only
 * changes the observed media position and never counts as listening. Finalization is idempotent:
 * a recovered finalized checkpoint returns null instead of a duplicate event.
 */
object LogicalListeningSession {
    fun start(
        entry: PlaybackQueueEntry,
        eventId: LocalId,
        nowMs: Long,
        positionMs: Long,
        ownerBinding: PlaybackSessionOwnerBinding? = null,
    ): LogicalListeningCheckpoint = LogicalListeningCheckpoint(
        eventId,
        entry.queueEntryId,
        entry.trackRefId,
        nowMs,
        positionMs,
        positionMs,
        0,
        entry.attribution,
        ownerBinding,
    )

    fun checkpoint(current: LogicalListeningCheckpoint, positionMs: Long, observedPlaybackDeltaMs: Long): LogicalListeningCheckpoint {
        require(positionMs >= 0 && observedPlaybackDeltaMs in 0..MAX_CHECKPOINT_PLAYED_DELTA_MS)
        return current.copy(
            lastObservedPositionMs = positionMs,
            accumulatedPlayedMs = (current.accumulatedPlayedMs + observedPlaybackDeltaMs).coerceAtMost(MAX_ACCUMULATED_PLAYED_MS),
        )
    }

    fun seek(current: LogicalListeningCheckpoint, positionMs: Long): LogicalListeningCheckpoint {
        require(positionMs >= 0)
        return current.copy(lastObservedPositionMs = positionMs)
    }

    fun finalizeOnce(current: LogicalListeningCheckpoint, endPositionMs: Long, durationMs: Long?, observedPlaybackDeltaMs: Long = 0): Pair<LogicalListeningCheckpoint, FinalizedListeningEvent?> {
        require(endPositionMs >= 0 && observedPlaybackDeltaMs in 0..MAX_CHECKPOINT_PLAYED_DELTA_MS)
        if (current.finalized) return current to null
        val accumulated = (current.accumulatedPlayedMs + observedPlaybackDeltaMs).coerceAtMost(MAX_ACCUMULATED_PLAYED_MS)
        val finalized = current.copy(lastObservedPositionMs = endPositionMs, accumulatedPlayedMs = accumulated, finalized = true)
        return finalized to FinalizedListeningEvent(
            listeningEventId = current.listeningEventId,
            queueEntryId = current.queueEntryId,
            trackRefId = current.trackRefId,
            startedAtMs = current.startedAtMs,
            playedMs = accumulated,
            durationMs = durationMs,
            endPositionMs = endPositionMs,
            attribution = current.attribution,
        )
    }
}

private const val MAX_CHECKPOINT_PLAYED_DELTA_MS = 300_000L
private const val MAX_ACCUMULATED_PLAYED_MS = 604_800_000L

private val TOKEN = Regex("^[a-z][a-z0-9_]{0,99}$")
private fun requireUuid(value: String) { require(isUuid(value)) }
private fun isUuid(value: String): Boolean = runCatching { UUID.fromString(value) }.isSuccess
