package app.autplay.playback

import app.autplay.data.settings.PlaybackAudioSettings
import java.util.Locale
import kotlin.math.min
import kotlin.math.pow

internal data class ReplayGainTags(
    val trackGainDb: Double? = null,
    val trackPeak: Double? = null,
    val albumGainDb: Double? = null,
    val albumPeak: Double? = null,
) {
    fun amplitude(settings: PlaybackAudioSettings): Double {
        val (gain, peak) = when (settings.replayGainMode) {
            "TRACK" -> trackGainDb to trackPeak
            "ALBUM" -> if (albumGainDb != null) albumGainDb to albumPeak else trackGainDb to trackPeak
            else -> return 1.0
        }
        // Untagged audio stays unchanged; preamp applies only to a valid gain tag.
        if (gain == null) return 1.0
        val amplitude = 10.0.pow((gain + settings.preampDb) / 20.0)
        // Missing peak is conservatively treated as full scale, never guessed from gain.
        return if (settings.preventClipping) min(amplitude, 1.0 / (peak ?: 1.0)) else amplitude
    }

    companion object {
        fun parse(entries: Iterable<Pair<String, String>>): ReplayGainTags {
            val values = mutableMapOf<String, Double>()
            for ((key, text) in entries.take(512)) {
                if (key.length > 64 || text.length > 128) continue
                val name = key.trim().uppercase(Locale.ROOT)
                if (name !in KEYS) continue
                val value = text.trim().replace(Regex("(?i)\\s*dB$"), "").toDoubleOrNull() ?: continue
                if (!value.isFinite()) continue
                if (name.endsWith("_GAIN") && value !in -60.0..60.0) continue
                if (name.endsWith("_PEAK") && (value <= 0.0 || value > 64.0)) continue
                values[name] = value
            }
            return ReplayGainTags(values["REPLAYGAIN_TRACK_GAIN"], values["REPLAYGAIN_TRACK_PEAK"],
                values["REPLAYGAIN_ALBUM_GAIN"], values["REPLAYGAIN_ALBUM_PEAK"])
        }

        private val KEYS = setOf("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK", "REPLAYGAIN_ALBUM_GAIN", "REPLAYGAIN_ALBUM_PEAK")
    }
}
