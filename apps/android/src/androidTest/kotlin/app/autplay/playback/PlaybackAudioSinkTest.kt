package app.autplay.playback

import android.os.SystemClock
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.Metadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.extractor.metadata.id3.InternalFrame
import androidx.media3.extractor.metadata.id3.TextInformationFrame
import androidx.media3.extractor.metadata.vorbis.VorbisComment
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.PlaybackAudioSettings
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.roundToInt
import kotlin.math.sin
import org.junit.Assert.*
import org.junit.Test

@UnstableApi
class PlaybackAudioSinkTest {
    @Test fun id3VorbisAndItunesTagsReachTheGainParser() {
        for (entry in listOf(
            TextInformationFrame("TXXX", "replaygain_track_gain", listOf("-6.0 dB")),
            VorbisComment("REPLAYGAIN_TRACK_GAIN", "-6.0 dB"),
            InternalFrame("com.apple.iTunes", "replaygain_track_gain", "-6.0 dB"),
        )) assertEquals(-6.0, replayGainTags(Metadata(entry)).trackGainDb!!, 0.0)
    }

    @Test fun partialWritesNeverDoubleApplyGainAndSeekKeepsItWhileNextTrackResetsIt() = onMain {
        val settings = PlaybackAudioPreferences().apply { value = PlaybackAudioSettings("TRACK") }
        val recording = RecordingAudioOutputProvider(context, maxWriteBytes = 2)
        val sink = createSink(settings, recording)
        val original = samples(ShortArray(1_000) { if (it % 2 == 0) 20_000 else -10_000 })
        val gained = samples(ShortArray(1_000) { if (it % 2 == 0) 10_000 else -5_000 })
        val started = withAudioTrackStartupRamp(gained)
        try {
            sink.setVolume(0f)
            sink.play()
            sink.configure(format(tagged = true), 0, null)
            feed(sink, original)
            drain(sink)
            assertArrayEquals(started, recording.received.toByteArray())
            sink.flush()
            feed(sink, original, 5_000_000)
            sink.configure(format(tagged = false), 0, null)
            feed(sink, original, 5_020_833)
            drain(sink)
            assertArrayEquals(started + started + original, recording.received.toByteArray())
            assertTrue(recording.partialWrites > 0)
        } finally { sink.reset(); sink.release() }
    }

    @Test fun encoderPaddingNeverEntersEqualizerAndGainChangesAtTheExactAudibleBoundary() = onMain {
        val preferences = PlaybackAudioPreferences().apply {
            value = PlaybackAudioSettings("TRACK", equalizerEnabled = true, equalizerBandsDb = listOf(0, -6, 4, 0, 0))
        }
        val recording = RecordingAudioOutputProvider(context, maxWriteBytes = 62)
        val sink = createSink(preferences, recording)
        val first = ShortArray(2_400) { (sin(2 * PI * 997 * it / 48_000) * 8_000).roundToInt().toShort() }
        val second = ShortArray(2_400) { (sin(2 * PI * 997 * (it + first.size) / 48_000) * 8_000).roundToInt().toShort() }
        val expected = ByteBuffer.allocate((first.size + second.size) * 2).order(ByteOrder.nativeOrder())
        val reference = PlaybackToneProcessor()
        fun expectedTrack(samples: ShortArray, tags: ReplayGainTags) {
            reference.configure(48_000, 1, tags, preferences.value)
            samples.forEach {
                val frame = doubleArrayOf(it / 32768.0)
                reference.processFrame(frame)
                expected.putShort((frame[0] * 32768).roundToInt().coerceIn(-32768, 32767).toShort())
            }
        }
        expectedTrack(first, ReplayGainTags(trackGainDb = -6.020599913))
        expectedTrack(second, ReplayGainTags())
        try {
            sink.setVolume(0f)
            sink.play()
            sink.configure(format(true).buildUpon().setEncoderDelay(31).setEncoderPadding(47).build(), 0, null)
            feed(sink, samples(ShortArray(31) { 30_000 } + first + ShortArray(47) { -30_000 }))
            // Staging the successor must not retarget old PCM still waiting in the sink.
            sink.configure(format(false).buildUpon().setEncoderDelay(23).build(), 0, null)
            feed(sink, samples(ShortArray(23) { 30_000 } + second), 50_000)
            drain(sink)
            assertArrayEquals(withAudioTrackStartupRamp(expected.array()), recording.received.toByteArray())
            assertEquals(1, recording.outputsCreated)
            assertTrue(recording.partialWrites > 0)
        } finally { sink.reset(); sink.release() }
    }

    @Test fun seekDiscardsEqualizerHistoryAndFormatChangeDoesNotLeakTrackGain() = onMain {
        val preferences = PlaybackAudioPreferences().apply {
            value = PlaybackAudioSettings("TRACK", equalizerEnabled = true, equalizerBandsDb = listOf(0, 0, -6, 0, 0))
        }
        val recording = RecordingAudioOutputProvider(context)
        val sink = createSink(preferences, recording)
        try {
            sink.setVolume(0f)
            sink.play()
            sink.configure(format(true), 0, null)
            feed(sink, samples(ShortArray(200) { 8_000 }))
            drain(sink)
            val first = recording.received.toByteArray()
            sink.flush()
            feed(sink, samples(ShortArray(200) { 8_000 }), 1_000_000)
            drain(sink)
            assertArrayEquals(first + first, recording.received.toByteArray())
            preferences.value = PlaybackAudioSettings("TRACK")
            sink.configure(format(false).buildUpon().setSampleRate(44_100).setChannelCount(2).build(), 0, null)
            feed(sink, shorts(20_000, -10_000))
            drain(sink)
            assertArrayEquals(first + first + withAudioTrackStartupRamp(shorts(20_000, -10_000), 44_100, 2), recording.received.toByteArray())
        } finally { sink.reset(); sink.release() }
    }

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private fun createSink(preferences: PlaybackAudioPreferences, recording: RecordingAudioOutputProvider): PlaybackAudioSink {
        val sink = PlaybackAudioSink.create(context, preferences, recording)
        // Match MediaCodecAudioRenderer's listener/session acknowledgement. Without a listener,
        // DefaultAudioSink retains an unset session ID in its old output config and recreates it.
        sink.setListener(object : AudioSink.Listener {
            override fun onPositionDiscontinuity() = Unit
            override fun onUnderrun(bufferSize: Int, bufferSizeMs: Long, elapsedSinceLastFeedMs: Long) = Unit
            override fun onSkipSilenceEnabledChanged(skipSilenceEnabled: Boolean) = Unit
            override fun onAudioSessionIdChanged(audioSessionId: Int) { sink.setAudioSessionId(audioSessionId) }
        })
        return sink
    }
    private fun onMain(block: () -> Unit) = InstrumentationRegistry.getInstrumentation().runOnMainSync(block)
    private fun format(tagged: Boolean): Format = Format.Builder().setSampleMimeType(MimeTypes.AUDIO_RAW)
        .setPcmEncoding(C.ENCODING_PCM_16BIT).setSampleRate(48_000).setChannelCount(1)
        .setMetadata(if (tagged) Metadata(VorbisComment("REPLAYGAIN_TRACK_GAIN", "-6.020599913 dB")) else null).build()
    private fun shorts(vararg values: Int): ByteArray = samples(values.map { it.toShort() }.toShortArray())
    private fun samples(values: ShortArray): ByteArray = ByteBuffer.allocate(values.size * 2).order(ByteOrder.nativeOrder())
        .apply { values.forEach(::putShort) }.array()
    private fun feed(sink: PlaybackAudioSink, bytes: ByteArray, timeUs: Long = 0) {
        val buffer = ByteBuffer.allocateDirect(bytes.size).order(ByteOrder.nativeOrder()).put(bytes).apply { flip() }
        val deadline = SystemClock.elapsedRealtime() + 5_000
        while (!sink.handleBuffer(buffer, timeUs, 1)) {
            check(SystemClock.elapsedRealtime() < deadline) { "Audio input did not drain" }
            Thread.sleep(1)
        }
    }
    private fun drain(sink: PlaybackAudioSink) {
        val deadline = SystemClock.elapsedRealtime() + 5_000
        do {
            sink.playToEndOfStream()
            check(SystemClock.elapsedRealtime() < deadline) { "Audio output did not drain" }
            Thread.sleep(1)
        } while (!sink.isEnded)
    }
}
