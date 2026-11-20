package app.autplay.playback

import app.autplay.data.settings.PlaybackAudioSettings
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.*
import org.junit.Test

class PlaybackToneProcessorTest {
    @Test fun taggedGainAppliesToFirstSampleAfterConfigureAndSeekAndDoesNotLeakToNextTrack() {
        val processor = PlaybackToneProcessor()
        val settings = PlaybackAudioSettings("TRACK")
        processor.configure(48_000, 2, ReplayGainTags(trackGainDb = -6.020599913), settings)
        repeat(2) {
            val frame = doubleArrayOf(0.8, -0.4)
            processor.processFrame(frame)
            assertEquals(0.4, frame[0], 0.000001)
            assertEquals(-0.2, frame[1], 0.000001)
            processor.flush()
        }
        processor.configure(48_000, 2, ReplayGainTags(), settings)
        val next = doubleArrayOf(0.8, -0.4)
        processor.processFrame(next)
        assertArrayEquals(doubleArrayOf(0.8, -0.4), next, 0.0)
    }

    @Test fun equalizerChangesSpectrumAndDisableReturnsToUnityWithoutChannelBleed() {
        val flat = PlaybackAudioSettings()
        val cut = flat.copy(equalizerEnabled = true, equalizerBandsDb = listOf(0, 0, -12, 0, 0))
        assertEquals(0.2512, rms(cut, 1_000.0) / rms(flat, 1_000.0), 0.015)
        assertTrue(rms(cut, 63.0) / rms(flat, 63.0) > 0.9)
        val processor = PlaybackToneProcessor()
        processor.configure(48_000, 2, ReplayGainTags(), cut)
        repeat(2_000) {
            val frame = doubleArrayOf(sin(2 * PI * it / 48), 0.0)
            processor.processFrame(frame)
            assertEquals(0.0, frame[1], 0.0)
        }
        processor.update(flat)
        repeat(1_000) { processor.processFrame(doubleArrayOf(0.1, 0.0)) }
        val restored = doubleArrayOf(0.123, -0.456)
        processor.processFrame(restored)
        assertArrayEquals(doubleArrayOf(0.123, -0.456), restored, 0.0)
    }

    @Test fun combinedGainAndBoostHaveHeadroomAndRemainFiniteAtLowSampleRates() {
        val settings = PlaybackAudioSettings("TRACK", equalizerEnabled = true, equalizerBandsDb = List(5) { 12 })
        for (rate in listOf(8_000, 44_100, 48_000, 96_000)) {
            val processor = PlaybackToneProcessor()
            processor.configure(rate, 1, ReplayGainTags(trackGainDb = 9.0, trackPeak = 1.0), settings)
            repeat(rate) {
                val frame = doubleArrayOf(sin(2 * PI * 1_000 * it / rate))
                processor.processFrame(frame)
                assertTrue(frame[0].isFinite() && abs(frame[0]) < 1.0)
            }
        }
    }

    @Test fun liveGainChangeRampsAndRecreatedSessionStartsAtTheSavedLevel() {
        val processor = PlaybackToneProcessor()
        val tags = ReplayGainTags(trackGainDb = -12.0)
        processor.configure(48_000, 1, tags, PlaybackAudioSettings())
        processor.update(PlaybackAudioSettings("TRACK"))
        val first = doubleArrayOf(0.5)
        processor.processFrame(first)
        assertTrue(first[0] > 0.49)
        repeat(960) { processor.processFrame(doubleArrayOf(0.5)) }
        val settled = doubleArrayOf(0.5)
        processor.processFrame(settled)
        assertEquals(0.125594, settled[0], 0.000001)
        processor.configure(48_000, 1, tags, PlaybackAudioSettings("TRACK"))
        val recreated = doubleArrayOf(0.5)
        processor.processFrame(recreated)
        assertEquals(settled[0], recreated[0], 0.000001)
    }

    private fun rms(settings: PlaybackAudioSettings, frequency: Double): Double {
        val processor = PlaybackToneProcessor()
        processor.configure(48_000, 1, ReplayGainTags(), settings)
        var sum = 0.0
        repeat(48_000) {
            val frame = doubleArrayOf(0.1 * sin(2 * PI * frequency * it / 48_000))
            processor.processFrame(frame)
            if (it >= 24_000) sum += frame[0] * frame[0]
        }
        return sqrt(sum / 24_000)
    }
}
