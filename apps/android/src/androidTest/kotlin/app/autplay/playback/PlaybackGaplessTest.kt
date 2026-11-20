package app.autplay.playback

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.source.LoadEventInfo
import androidx.media3.exoplayer.source.MediaLoadData
import androidx.media3.exoplayer.util.EventLogger
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.PlaybackAudioSettings
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.roundToInt
import org.junit.Assert.*
import org.junit.Test

@UnstableApi
class PlaybackGaplessTest {
    @Test fun taggedFlacToUntaggedFlacChangesGainAtTheDecodedBoundaryWithContinuousEqualizer() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val preferences = PlaybackAudioPreferences().apply {
            value = PlaybackAudioSettings("TRACK", equalizerEnabled = true, equalizerBandsDb = listOf(0, -6, 4, 0, 0))
        }
        val expected = ByteBuffer.allocate(48_000 * 2).order(ByteOrder.nativeOrder())
        val reference = PlaybackToneProcessor()
        repeat(2) { track ->
            reference.configure(48_000, 1, if (track == 0) ReplayGainTags(trackGainDb = -6.020599913) else ReplayGainTags(), preferences.value)
            repeat(24_000) { index ->
                val frame = doubleArrayOf((((index + track * 24_000) * 311 % 16_001) - 8_000) / 32768.0)
                reference.processFrame(frame)
                expected.putShort((frame[0] * 32768).roundToInt().coerceIn(-32768, 32767).toShort())
            }
        }
        val files = listOf("tagged", "untagged").map { name ->
            File.createTempFile("gapless-$name-", ".flac", context.cacheDir).apply {
                instrumentation.context.assets.open("gapless/$name.flac").use { input -> outputStream().use(input::copyTo) }
            }
        }
        val recording = RecordingAudioOutputProvider(context)
        val successorLoaded = CountDownLatch(1)
        val ended = CountDownLatch(1)
        val error = AtomicReference<PlaybackException?>()
        val player = AtomicReference<ExoPlayer>()
        var transitions = 0
        var readyOnce = false
        var rebuffers = 0
        try {
            instrumentation.runOnMainSync {
                val renderers = object : DefaultRenderersFactory(context) {
                    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
                        PlaybackAudioSink.create(context, preferences, recording)
                }
                val builder = ExoPlayer.Builder(context, renderers)
                    .setMediaSourceFactory(PlaybackMediaSourceFactory.create(context))
                    .setLoadControl(PlaybackTransitions.loadControl())
                player.set(builder.build().also {
                    it.volume = 0f
                    it.addAnalyticsListener(EventLogger())
                    it.addAnalyticsListener(object : AnalyticsListener {
                        override fun onLoadCompleted(
                            eventTime: AnalyticsListener.EventTime,
                            loadEventInfo: LoadEventInfo,
                            mediaLoadData: MediaLoadData,
                        ) {
                            if (loadEventInfo.uri.lastPathSegment == files[1].name) successorLoaded.countDown()
                        }
                    })
                    it.setPreloadConfiguration(ExoPlayer.PreloadConfiguration(PlaybackTransitions.preloadDurationUs(true)))
                    it.addListener(object : Player.Listener {
                        override fun onPlaybackStateChanged(state: Int) {
                            if (state == Player.STATE_BUFFERING && readyOnce) rebuffers++
                            if (state == Player.STATE_READY) readyOnce = true
                            if (state == Player.STATE_ENDED) ended.countDown()
                        }
                        override fun onMediaItemTransition(item: MediaItem?, reason: Int) {
                            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) transitions++
                        }
                        override fun onPlayerError(failure: PlaybackException) { error.set(failure); ended.countDown() }
                    })
                    it.setMediaItems(files.mapIndexed { index, file -> MediaItem.Builder().setMediaId("flac-$index").setUri(file.toURI().toString()).build() })
                    it.prepare()
                })
            }
            // Both half-second fixtures must be resident to isolate the decoded transition from file I/O.
            assertTrue("Second FLAC was not preloaded", successorLoaded.await(10, TimeUnit.SECONDS))
            instrumentation.runOnMainSync { player.get().play() }
            assertTrue(ended.await(15, TimeUnit.SECONDS))
            instrumentation.runOnMainSync {
                assertNull(error.get())
                assertEquals(1, transitions)
            }
            assertArrayEquals(withAudioTrackStartupRamp(expected.array()), recording.received.toByteArray())
            assertEquals(1, recording.outputsCreated)
            assertEquals("Unexpected buffering after the initial READY state", 0, rebuffers)
        } finally {
            instrumentation.runOnMainSync { player.get()?.release() }
            files.forEach { assertTrue(it.delete()) }
        }
    }

    @Test fun twoCompletePcmTracksReachTheSinkWithoutMissingOrDuplicatedSamplesOrRebuffering() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val samples = 48_000 // Two complete one-second tracks of a continuous 48 kHz tone.
        val pcm = ByteBuffer.allocate(samples * 2).order(ByteOrder.LITTLE_ENDIAN)
        repeat(samples) { pcm.putShort((sin(2 * PI * 440 * it / 48_000) * 8_000).toInt().toShort()) }
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
            .put("RIFF".toByteArray()).putInt(36 + pcm.capacity()).put("WAVEfmt ".toByteArray()).putInt(16)
            .putShort(1).putShort(1).putInt(48_000).putInt(96_000).putShort(2).putShort(16)
            .put("data".toByteArray()).putInt(pcm.capacity()).array()
        val file = File.createTempFile("gapless-", ".wav", context.cacheDir)
        file.writeBytes(header + pcm.array())
        val recording = RecordingAudioOutputProvider(context)
        val ended = CountDownLatch(1)
        val error = AtomicReference<PlaybackException?>()
        val player = AtomicReference<ExoPlayer>()
        var readyOnce = false
        var rebuffers = 0
        var transitions = 0
        try {
            instrumentation.runOnMainSync {
                val renderers = object : DefaultRenderersFactory(context) {
                    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink {
                        return PlaybackAudioSink.create(context, PlaybackAudioPreferences(), recording)
                    }
                }
                val builder = ExoPlayer.Builder(context, renderers)
                    .setMediaSourceFactory(PlaybackMediaSourceFactory.create(context))
                    .setLoadControl(PlaybackTransitions.loadControl())
                player.set(builder.build().also {
                    it.volume = 0f
                    it.setPreloadConfiguration(ExoPlayer.PreloadConfiguration(PlaybackTransitions.preloadDurationUs(true)))
                    it.addListener(object : Player.Listener {
                        override fun onPlaybackStateChanged(state: Int) {
                            if (state == Player.STATE_BUFFERING && readyOnce) rebuffers++
                            if (state == Player.STATE_READY) readyOnce = true
                            if (state == Player.STATE_ENDED) ended.countDown()
                        }
                        override fun onMediaItemTransition(item: MediaItem?, reason: Int) {
                            if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) transitions++
                        }
                        override fun onPlayerError(failure: PlaybackException) { error.set(failure); ended.countDown() }
                    })
                    it.setMediaItems(listOf("first", "second").map { id -> MediaItem.Builder().setMediaId(id).setUri(file.toURI().toString()).build() })
                    it.prepare()
                    it.play()
                })
            }
            assertTrue(ended.await(15, TimeUnit.SECONDS))
            instrumentation.runOnMainSync {
                assertNull(error.get())
                assertEquals(1, transitions)
                assertEquals(0, rebuffers)
            }
            assertArrayEquals(withAudioTrackStartupRamp(pcm.array() + pcm.array()), recording.received.toByteArray())
            assertEquals("Gapless must reuse the real AudioTrack", 1, recording.outputsCreated)
        } finally {
            instrumentation.runOnMainSync { player.get()?.release() }
            assertTrue(file.delete())
        }
    }
}
