package app.autplay.domain.face

import java.util.Collections

class FaceV2TimelineException : IllegalArgumentException("ml.face.invalid_v2_timeline")

data class FaceV2Keyframe(val sampleIndex: Long, val state: FaceSemanticState)

data class FaceV2Event(
    val sampleIndex: Long,
    val eventType: String,
    val strength: Double,
    val confidence: Double,
)

/** Decoded-sample result data; no v1 timeline or playback selection is changed. */
class FaceTimelineV2(
    val identity: FaceTimelineIdentityV2,
    val presentationMap: SourcePresentationMapV1,
    val trackCharacter: FaceSemanticState,
    keyframes: List<FaceV2Keyframe>,
    events: List<FaceV2Event>,
) {
    val keyframes: List<FaceV2Keyframe> = Collections.unmodifiableList(ArrayList(keyframes))
    val events: List<FaceV2Event> = Collections.unmodifiableList(ArrayList(events))

    init {
        if (this.keyframes.size > 4096 || this.events.size > 4096 ||
            identity.sourceSha256 != presentationMap.encodedSourceSha256 ||
            identity.decodedSampleRate != presentationMap.decodedSampleRate ||
            identity.decodedSampleCount != presentationMap.decodedSampleCount
        ) throw FaceV2TimelineException()
        var previousFrame = -1L
        val axes = trackCharacter.axes.keys.toMutableSet()
        for (frame in this.keyframes) {
            if (frame.sampleIndex !in 0 until identity.decodedSampleCount ||
                frame.sampleIndex <= previousFrame
            ) throw FaceV2TimelineException()
            axes.addAll(frame.state.axes.keys)
            previousFrame = frame.sampleIndex
        }
        if (axes.size > 64) throw FaceV2TimelineException()
        var previousEvent: Pair<Long, String>? = null
        val name = Regex("[a-z][a-z0-9_]{0,63}")
        for (event in this.events) {
            val key = event.sampleIndex to event.eventType
            if (event.sampleIndex !in 0 until identity.decodedSampleCount ||
                !event.eventType.matches(name) || !event.strength.isFinite() ||
                event.strength !in 0.0..1.0 || !event.confidence.isFinite() ||
                event.confidence !in 0.0..1.0 ||
                (previousEvent != null && (key.first < previousEvent.first ||
                    (key.first == previousEvent.first && key.second <= previousEvent.second)))
            ) throw FaceV2TimelineException()
            previousEvent = key
        }
    }
}
