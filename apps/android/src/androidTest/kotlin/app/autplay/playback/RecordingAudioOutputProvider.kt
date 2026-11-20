package app.autplay.playback

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.audio.AudioOutput
import androidx.media3.exoplayer.audio.AudioOutputProvider
import androidx.media3.exoplayer.audio.AudioTrackAudioOutputProvider
import androidx.media3.exoplayer.audio.ForwardingAudioOutput
import androidx.media3.exoplayer.audio.PcmAudioUtil
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Records only bytes actually written after the complete production audio processing pipeline. */
@UnstableApi
internal class RecordingAudioOutputProvider(
    context: Context,
    private val maxWriteBytes: Int = Int.MAX_VALUE,
    private val delegate: AudioOutputProvider = AudioTrackAudioOutputProvider.Builder(context).build(),
) : AudioOutputProvider by delegate {
    val received = ByteArrayOutputStream()
    var outputsCreated: Int = 0
        private set
    var partialWrites: Int = 0
        private set

    override fun getAudioOutput(config: AudioOutputProvider.OutputConfig): AudioOutput {
        outputsCreated++
        return object : ForwardingAudioOutput(delegate.getAudioOutput(config)) {
            override fun write(buffer: ByteBuffer, encodedAccessUnitCount: Int, presentationTimeUs: Long): Boolean {
                val before = buffer.position()
                val limit = buffer.limit()
                val copy = buffer.duplicate()
                buffer.limit(before + minOf(buffer.remaining(), maxWriteBytes))
                try { super.write(buffer, encodedAccessUnitCount, presentationTimeUs) }
                finally { buffer.limit(limit) }
                val bytes = ByteArray(buffer.position() - before)
                copy.get(bytes)
                received.write(bytes)
                if (buffer.hasRemaining()) partialWrites++
                return !buffer.hasRemaining()
            }
        }
    }
}

/** Media3 1.10.1 ramps only the first 20 ms after creating/flushing an output, not each track. */
@UnstableApi
internal fun withAudioTrackStartupRamp(pcm: ByteArray, rate: Int = 48_000, channels: Int = 1): ByteArray {
    val source = ByteBuffer.allocateDirect(pcm.size).order(ByteOrder.nativeOrder()).put(pcm).apply { flip() }
    val result = PcmAudioUtil.rampUpVolume(source, C.ENCODING_PCM_16BIT, channels * 2, 0, rate / 50)
    return ByteArray(result.remaining()).also { result.get(it) }
}
