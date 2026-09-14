package app.autplay.application.face

import app.autplay.domain.face.FaceAxis
import app.autplay.domain.face.FaceContractException
import app.autplay.domain.face.FaceProjectionBinding
import app.autplay.domain.face.FaceSemanticState
import app.autplay.domain.face.FaceTimelineIdentity
import app.autplay.domain.face.TemporalFaceTimeline

/** Supplied by the playback owner, never copied from an untrusted projection response. */
data class FacePlaybackSelection(
    val serverProfileId: String,
    val userId: String,
    val identity: FaceTimelineIdentity,
    val activationEpoch: Long,
    val playbackGeneration: Long,
)

/** Verified immutable attachment; hash work runs once, outside the animation frame path. */
class FaceTimelineSampler private constructor(
    private val timeline: TemporalFaceTimeline,
    private val selection: FacePlaybackSelection,
) {
    /** Null means neutral local fallback. Source position handles pause, seek and queue rebase. */
    fun sample(current: FacePlaybackSelection, sourcePositionMs: Long): FaceSemanticState? {
        if (current != selection || sourcePositionMs !in 0..timeline.identity.sourceDurationMs) return null
        val frames = timeline.keyframes
        if (frames.isEmpty()) return timeline.trackCharacter
        var low = 0
        var high = frames.lastIndex
        while (low < high) {
            val middle = (low + high + 1) ushr 1
            if (frames[middle].timeMs <= sourcePositionMs) low = middle else high = middle - 1
        }
        val left = frames[low]
        if (left.timeMs == sourcePositionMs || low == frames.lastIndex) return left.state
        val right = frames[low + 1]
        val fraction = (sourcePositionMs - left.timeMs).toDouble() / (right.timeMs - left.timeMs)
        return FaceSemanticState((left.state.axes.keys + right.state.axes.keys).associateWith { name ->
            val start = left.state.axes[name]
            val end = right.state.axes[name]
            when {
                start == null || end == null -> FaceAxis.unknown()
                start.abstained -> start
                end.abstained -> end
                else -> FaceAxis(
                    requireNotNull(start.value) + (requireNotNull(end.value) - start.value) * fraction,
                    minOf(requireNotNull(start.confidence), requireNotNull(end.confidence)),
                    false, null,
                )
            }
        })
    }

    companion object {
        fun attach(
            timeline: TemporalFaceTimeline,
            binding: FaceProjectionBinding,
            requested: FacePlaybackSelection,
            current: FacePlaybackSelection,
        ): FaceTimelineSampler? {
            if (requested != current || binding.serverProfileId != current.serverProfileId || binding.userId != current.userId ||
                binding.identity != current.identity || binding.activationEpoch != current.activationEpoch ||
                current.playbackGeneration < 0
            ) return null
            try { FaceContractCodec.verifyProjection(timeline, binding) }
            catch (_: FaceContractException) { return null }
            return FaceTimelineSampler(timeline, current)
        }
    }
}
