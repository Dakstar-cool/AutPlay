package app.autplay.playback

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.audio.AudioProcessor.AudioFormat
import androidx.media3.common.audio.AudioProcessor.StreamMetadata
import androidx.media3.common.audio.AudioProcessor.UnhandledAudioFormatException
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioOffloadSupport
import androidx.media3.exoplayer.audio.AudioOutputProvider
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.ForwardingAudioSink
import app.autplay.data.settings.PlaybackAudioSettings
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

internal class PlaybackAudioPreferences {
    @Volatile var value: PlaybackAudioSettings = PlaybackAudioSettings()
}

@UnstableApi
internal class PlaybackRenderersFactory(context: Context, private val preferences: PlaybackAudioPreferences) :
    DefaultRenderersFactory(context) {
    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioOutputPlaybackParams: Boolean): AudioSink =
        PlaybackAudioSink.create(context, preferences)
}

/** Stages decoder metadata; Media3 drains the old stream before activating the new gain. */
@UnstableApi
internal class PlaybackAudioSink private constructor(
    sink: AudioSink,
    private val processor: PlaybackPcmProcessor,
) : ForwardingAudioSink(sink) {
    // Effects must use the PCM pipeline even when disabled, so toggling cannot restart playback.
    override fun supportsFormat(format: Format): Boolean = getFormatSupport(format) != AudioSink.SINK_FORMAT_UNSUPPORTED

    override fun getFormatSupport(format: Format): Int =
        if (format.sampleMimeType == MimeTypes.AUDIO_RAW) super.getFormatSupport(format)
        else AudioSink.SINK_FORMAT_UNSUPPORTED

    override fun getFormatOffloadSupport(format: Format): AudioOffloadSupport = AudioOffloadSupport.DEFAULT_UNSUPPORTED

    override fun configure(inputFormat: Format, specifiedBufferSize: Int, outputChannels: IntArray?) {
        super.configure(inputFormat, specifiedBufferSize, outputChannels)
        processor.stageMetadata(playbackGain(inputFormat))
    }

    override fun flush() {
        // An explicit seek/stop discards filter history. A gapless pipeline flush does not.
        processor.discardHistory()
        super.flush()
    }

    companion object {
        fun create(
            context: Context,
            preferences: PlaybackAudioPreferences,
            outputProvider: AudioOutputProvider? = null,
        ): PlaybackAudioSink {
            val processor = PlaybackPcmProcessor(preferences)
            val sink = DefaultAudioSink.Builder(context)
                // Custom processors run after encoder delay/padding trimming and PCM conversion.
                .setEnableFloatOutput(false)
                .setAudioProcessors(arrayOf(processor))
                .apply { outputProvider?.let(::setAudioOutputProvider) }
                .build()
            return PlaybackAudioSink(sink, processor)
        }
    }
}

/** Always active: live preferences and same-format track metadata must reach every PCM buffer. */
@UnstableApi
internal class PlaybackPcmProcessor(private val preferences: PlaybackAudioPreferences) : BaseAudioProcessor() {
    private var tone = PlaybackToneProcessor()
    private var tags = ReplayGainTags()
    private var pendingTags: ReplayGainTags? = null
    private var resetHistory = true
    private var frame = DoubleArray(0)

    fun stageMetadata(metadata: ReplayGainTags) { pendingTags = metadata }

    fun discardHistory() { resetHistory = true }

    override fun onConfigure(inputAudioFormat: AudioFormat): AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) throw UnhandledAudioFormatException(inputAudioFormat)
        return inputAudioFormat
    }

    override fun onFlush(streamMetadata: StreamMetadata) {
        if (inputAudioFormat == AudioFormat.NOT_SET) return
        pendingTags?.let { tags = it }
        pendingTags = null
        tone.configure(inputAudioFormat.sampleRate, inputAudioFormat.channelCount, tags, preferences.value)
        if (resetHistory) tone.flush()
        resetHistory = false
        frame = DoubleArray(inputAudioFormat.channelCount)
    }

    override fun onReset() {
        tone = PlaybackToneProcessor()
        tags = ReplayGainTags()
        pendingTags = null
        resetHistory = true
        frame = DoubleArray(0)
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val length = inputBuffer.remaining()
        if (length == 0) return
        check(frame.isNotEmpty() && length % (frame.size * 2) == 0) { "INVALID_PCM_FRAME" }
        tone.update(preferences.value)
        val output = replaceOutputBuffer(length).order(ByteOrder.nativeOrder())
        if (tone.isIdentity) output.put(inputBuffer)
        else {
            inputBuffer.order(ByteOrder.nativeOrder())
            while (inputBuffer.hasRemaining()) {
                for (channel in frame.indices) frame[channel] = inputBuffer.short / 32768.0
                tone.processFrame(frame)
                for (sample in frame) output.putShort((sample * 32768).roundToInt().coerceIn(-32768, 32767).toShort())
            }
        }
        output.flip()
    }
}
