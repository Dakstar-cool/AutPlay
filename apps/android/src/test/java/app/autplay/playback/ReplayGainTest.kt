package app.autplay.playback

import app.autplay.data.settings.PlaybackAudioSettings
import org.junit.Assert.*
import org.junit.Test

class ReplayGainTest {
    @Test fun parsesCaseUnitsAndRejectsNonFiniteAndMalformedTags() {
        val tags = ReplayGainTags.parse(listOf("replaygain_track_gain" to " -6.0 dB ",
            "REPLAYGAIN_TRACK_PEAK" to "0.8", "REPLAYGAIN_ALBUM_GAIN" to "NaN",
            "REPLAYGAIN_ALBUM_PEAK" to "-1"))
        assertEquals(-6.0, tags.trackGainDb!!, 0.0)
        assertEquals(0.8, tags.trackPeak!!, 0.0)
        assertNull(tags.albumGainDb)
        assertNull(tags.albumPeak)
    }

    @Test fun modesPreampFallbackAndDisableHaveDefinedLevels() {
        val tags = ReplayGainTags(trackGainDb = -6.0, albumGainDb = -12.0)
        assertEquals(1.0, tags.amplitude(PlaybackAudioSettings()), 0.0)
        assertEquals(0.501187, tags.amplitude(PlaybackAudioSettings("TRACK")), 0.00001)
        assertEquals(0.251189, tags.amplitude(PlaybackAudioSettings("ALBUM")), 0.00001)
        assertEquals(1.0, tags.amplitude(PlaybackAudioSettings("TRACK", preampDb = 6)), 0.0)
        assertEquals(0.501187, tags.copy(albumGainDb = null).amplitude(PlaybackAudioSettings("ALBUM")), 0.00001)
        assertEquals(1.0, ReplayGainTags().amplitude(PlaybackAudioSettings("TRACK", preampDb = 12)), 0.0)
        assertEquals(1.0, tags.amplitude(PlaybackAudioSettings("FUTURE")), 0.0)
    }

    @Test fun peakProtectionCapsPositiveGainIncludingUnknownPeak() {
        val settings = PlaybackAudioSettings("TRACK", preampDb = 3)
        val tags = ReplayGainTags(trackGainDb = 6.0, trackPeak = 0.8)
        assertEquals(1.25, tags.amplitude(settings), 0.0)
        assertEquals(1.0, tags.copy(trackPeak = null).amplitude(settings), 0.0)
        assertTrue(tags.amplitude(settings.copy(preventClipping = false)) > 2.0)
    }
}
