package app.autplay.playback

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.util.UnstableApi
import androidx.media3.extractor.Extractor
import androidx.media3.extractor.ExtractorOutput
import androidx.media3.extractor.ForwardingExtractor
import androidx.media3.extractor.ForwardingExtractorOutput
import androidx.media3.extractor.ForwardingExtractorsFactory
import androidx.media3.extractor.ForwardingTrackOutput
import androidx.media3.extractor.TrackOutput
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull

internal data class ServerLoudness(val gainDb: Double, val truePeak: Double)

@androidx.annotation.OptIn(UnstableApi::class)
internal fun serverLoudness(payload: JsonObject?, source: ResolvedPlaybackSource, profile: String?): ServerLoudness? {
    val measurement = payload?.get("loudness_v1") as? JsonObject ?: return null
    val uri = source.runtimeUri
    val matches = when (source.source) {
        SelectedAudioSource.LOCAL_URI -> source.localSha256 != null &&
            source.localSha256 == (measurement["sha256"] as? JsonPrimitive)?.contentOrNull
        else -> uri.scheme == AndroidPlaybackSourceResolver.VAULT_SCHEME && profile != null && uri.authority == profile &&
            uri.pathSegments.size == 2 && uri.pathSegments.first() == "audio-variants" &&
            uri.lastPathSegment == (measurement["audio_variant_id"] as? JsonPrimitive)?.contentOrNull
    }
    if (!matches) return null
    val gain = (measurement["gain_db"] as? JsonPrimitive)?.doubleOrNull ?: return null
    val peak = (measurement["true_peak"] as? JsonPrimitive)?.doubleOrNull ?: return null
    return ServerLoudness(gain, peak).takeIf { gain in -60.0..60.0 && peak > 0.0 && peak <= 64.0 }
}

/** Bind the measurement to the decoder format so preloading and gapless transitions keep the right gain. */
@UnstableApi
internal class MeasuredAudioExtractors(private val loudness: ServerLoudness) :
    ForwardingExtractorsFactory(playbackExtractorsFactory()) {
    override fun createExtractors(): Array<Extractor> = wrap(super.createExtractors())

    override fun createExtractors(uri: Uri, responseHeaders: Map<String, List<String>>): Array<Extractor> =
        wrap(super.createExtractors(uri, responseHeaders))

    private fun wrap(extractors: Array<Extractor>): Array<Extractor> = Array(extractors.size) { index ->
        object : ForwardingExtractor(extractors[index]) {
            override fun init(output: ExtractorOutput) = super.init(object : ForwardingExtractorOutput(output) {
                override fun track(id: Int, type: Int): TrackOutput {
                    val track = super.track(id, type)
                    if (type != C.TRACK_TYPE_AUDIO) return track
                    return object : ForwardingTrackOutput(track) {
                        override fun format(format: Format) = super.format(format.buildUpon().setCustomData(loudness).build())
                    }
                }
            })
        }
    }
}

@UnstableApi
internal fun playbackGain(format: Format): ReplayGainTags {
    val tags = replayGainTags(format.metadata)
    val measured = format.customData as? ServerLoudness ?: return tags
    return if (tags.trackGainDb != null) tags else tags.copy(trackGainDb = measured.gainDb, trackPeak = measured.truePeak)
}
