package app.autplay.domain.face

import java.util.Collections

class SourcePresentationMapException : IllegalArgumentException("ml.face.invalid_presentation_map")

private fun mapRequire(condition: Boolean) {
    if (!condition) throw SourcePresentationMapException()
}

data class PresentationSegmentV1(
    val presentationStartUs: Long,
    val presentationEndUs: Long,
    val sourceStartSample: Long,
)

/** Separate from the immutable Face v1 millisecond contract. */
class SourcePresentationMapV1(
    val encodedSourceSha256: String,
    val decodedSampleRate: Long,
    val decodedSampleCount: Long,
    val decoderId: String,
    val decoderVersion: String,
    val probeId: String,
    val probeVersion: String,
    val leadingTrimSamples: Long,
    val trailingTrimSamples: Long,
    val encoderDelaySamples: Long?,
    val encoderPaddingSamples: Long?,
    segments: List<PresentationSegmentV1>,
) {
    val segments: List<PresentationSegmentV1> = Collections.unmodifiableList(ArrayList(segments))

    init {
        mapRequire(encodedSourceSha256.matches(Regex("[0-9a-f]{64}")))
        mapRequire(decodedSampleRate in 8_000L..384_000L)
        mapRequire(decodedSampleCount in 1L..33_177_600_000L)
        val identity = Regex("[A-Za-z0-9._-]{1,128}")
        mapRequire(listOf(decoderId, decoderVersion, probeId, probeVersion).all { it.matches(identity) })
        mapRequire(leadingTrimSamples >= 0 && trailingTrimSamples >= 0)
        mapRequire(leadingTrimSamples <= decodedSampleCount - trailingTrimSamples)
        mapRequire(encoderDelaySamples == null || encoderDelaySamples >= 0)
        mapRequire(encoderPaddingSamples == null || encoderPaddingSamples >= 0)
        mapRequire(this.segments.size in 1..256)
        var previousEnd = -1L
        for (segment in this.segments) {
            val start = segment.presentationStartUs
            val end = segment.presentationEndUs
            mapRequire(start >= 0 && start >= previousEnd && end > start && end <= 86_400_000_000L)
            mapRequire(segment.sourceStartSample >= 0)
            val last = segment.sourceStartSample + (end - start - 1L) * decodedSampleRate / 1_000_000L
            mapRequire(last < decodedSampleCount)
            previousEnd = end
        }
    }

    /** Null is a neutral output for a seek gap, boundary or source/decoder mismatch. */
    fun sampleAt(
        positionUs: Long,
        encodedSourceSha256: String,
        decoderId: String,
        decoderVersion: String,
        probeId: String,
        probeVersion: String,
    ): Long? {
        if (positionUs !in 0L..86_400_000_000L ||
            encodedSourceSha256 != this.encodedSourceSha256 ||
            decoderId != this.decoderId || decoderVersion != this.decoderVersion ||
            probeId != this.probeId || probeVersion != this.probeVersion
        ) return null
        var low = 0
        var high = segments.size
        while (low < high) {
            val middle = (low + high) / 2
            if (segments[middle].presentationStartUs <= positionUs) low = middle + 1
            else high = middle
        }
        val index = low - 1
        if (index < 0) return null
        val segment = segments[index]
        if (positionUs >= segment.presentationEndUs) return null
        val sample = segment.sourceStartSample +
            (positionUs - segment.presentationStartUs) * decodedSampleRate / 1_000_000L
        return sample.takeIf { it < decodedSampleCount }
    }
}
