package app.autplay.domain.face

import java.util.Collections

class FaceContractException(val code: String = "ml.face.invalid_timeline") :
    IllegalArgumentException(code)

internal fun faceRequire(condition: Boolean, code: String = "ml.face.invalid_timeline") {
    if (!condition) throw FaceContractException(code)
}

internal val faceName = Regex("[a-z][a-z0-9_]{0,63}")
internal val faceReason = Regex("[A-Z][A-Z0-9_]{0,63}")
internal val faceUuid = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
internal val faceHash = Regex("[0-9a-f]{64}")

data class FaceAxis(
    val value: Double?,
    val confidence: Double?,
    val abstained: Boolean,
    val reasonCode: String?,
) {
    init {
        if (abstained) {
            faceRequire(value == null && confidence == null && reasonCode?.matches(faceReason) == true)
        } else {
            faceRequire(value != null && value.isFinite() && value in -1.0..1.0)
            faceRequire(confidence != null && confidence.isFinite() && confidence in 0.0..1.0)
            faceRequire(reasonCode == null)
        }
    }

    companion object {
        fun unknown(reason: String = "NO_ESTIMATE") = FaceAxis(null, null, true, reason)
    }
}

class FaceSemanticState(axes: Map<String, FaceAxis>) {
    val axes: Map<String, FaceAxis> = Collections.unmodifiableMap(LinkedHashMap(axes))

    init {
        faceRequire(this.axes.size <= 64 && this.axes.keys.all { it.matches(faceName) })
    }
}

data class FaceTimelineIdentity(
    val recordingId: String,
    val audioVariantId: String,
    val sourceSha256: String,
    val sourceDurationMs: Long,
    val embeddingModelId: String,
    val embeddingManifestSha256: String,
    val semanticInterpreterId: String,
    val interpreterManifestSha256: String,
    val preprocessingSha256: String,
    val schemaVersion: Int = 1,
    val sourceTimebase: String = "SOURCE_MILLISECONDS_V1",
) {
    init {
        faceRequire(schemaVersion == 1 && sourceTimebase == "SOURCE_MILLISECONDS_V1")
        faceRequire(sourceDurationMs in 1..86_400_000L)
        faceRequire(listOf(recordingId, audioVariantId, embeddingModelId, semanticInterpreterId)
            .all { it.matches(faceUuid) })
        faceRequire(listOf(sourceSha256, embeddingManifestSha256, interpreterManifestSha256,
            preprocessingSha256).all { it.matches(faceHash) })
    }
}

data class FaceKeyframe(val timeMs: Long, val state: FaceSemanticState) {
    init { faceRequire(timeMs in 0..86_400_000L) }
}

data class FaceEvent(
    val timeMs: Long,
    val eventType: String,
    val strength: Double,
    val confidence: Double,
) {
    init {
        faceRequire(timeMs in 0..86_400_000L && eventType.matches(faceName))
        faceRequire(strength.isFinite() && strength in 0.0..1.0)
        faceRequire(confidence.isFinite() && confidence in 0.0..1.0)
    }
}

class TemporalFaceTimeline(
    val identity: FaceTimelineIdentity,
    val trackCharacter: FaceSemanticState,
    keyframes: List<FaceKeyframe>,
    events: List<FaceEvent>,
    val schemaVersion: Int = 1,
) {
    val keyframes: List<FaceKeyframe> = Collections.unmodifiableList(ArrayList(keyframes))
    val events: List<FaceEvent> = Collections.unmodifiableList(ArrayList(events))

    init {
        faceRequire(schemaVersion == 1 && this.keyframes.size <= 4096 && this.events.size <= 4096)
        faceRequire(this.keyframes.isEmpty() || this.keyframes.first().timeMs == 0L)
        val axes = trackCharacter.axes.keys.toMutableSet()
        var previousTime = -1L
        for (frame in this.keyframes) {
            faceRequire(frame.timeMs > previousTime && frame.timeMs <= identity.sourceDurationMs)
            previousTime = frame.timeMs
            axes.addAll(frame.state.axes.keys)
        }
        faceRequire(axes.size <= 64)
        var previousEvent: FaceEvent? = null
        for (event in this.events) {
            faceRequire(event.timeMs <= identity.sourceDurationMs)
            val before = previousEvent
            faceRequire(before == null || event.timeMs > before.timeMs ||
                (event.timeMs == before.timeMs && event.eventType > before.eventType))
            previousEvent = event
        }
    }
}

data class FaceProjectionBinding(
    val serverProfileId: String,
    val userId: String,
    val identity: FaceTimelineIdentity,
    val semanticKey: String,
    val resultHash: String,
    val activationEpoch: Long,
) {
    init {
        faceRequire(serverProfileId.matches(faceUuid) && userId.matches(faceUuid))
        faceRequire(semanticKey.matches(faceHash) && resultHash.matches(faceHash))
        faceRequire(activationEpoch in 1..9_007_199_254_740_991L)
    }
}
