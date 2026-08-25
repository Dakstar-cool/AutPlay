package app.autplay.playback

import android.content.Context
import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.TeeAudioProcessor
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicIntegerArray
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** A bounded, process-local visual projection. It never exposes or retains decoded PCM. */
internal data class PlaybackAudioContourFrame(
    val energy: Float = 0f,
    val contour: List<Float> = List(CONTOUR_POINT_COUNT) { 0f },
)

internal object PlaybackAudioContourRuntime {
    private val emptyFrame = PlaybackAudioContourFrame()
    private val observers = linkedSetOf<String>()
    private val observing = AtomicBoolean(false)
    private val mutable = MutableStateFlow(emptyFrame)
    val state: StateFlow<PlaybackAudioContourFrame> = mutable.asStateFlow()

    @Synchronized
    fun setSurfaceObserving(surfaceId: String, value: Boolean) {
        if (value) observers += surfaceId else observers -= surfaceId
        observing.set(observers.isNotEmpty())
        if (!value && observers.isEmpty()) mutable.value = emptyFrame
    }

    fun isObservationRequested(): Boolean = observing.get()

    fun publish(frame: PlaybackAudioContourFrame) {
        mutable.value = if (observing.get()) frame else emptyFrame
    }

    fun reset() {
        mutable.value = emptyFrame
    }
}

/**
 * Pass-through Media3 sink that reduces the latest PCM buffer to normalized visual energy.
 * The audio callback performs bounded work and writes only atomic scalar projections.
 */
@UnstableApi
internal class PlaybackAudioContourSink : TeeAudioProcessor.AudioBufferSink {
    private val energyBits = AtomicInteger(0f.toRawBits())
    private val contourBits = AtomicIntegerArray(CONTOUR_POINT_COUNT)
    private val sums = FloatArray(CONTOUR_POINT_COUNT)
    private val counts = IntArray(CONTOUR_POINT_COUNT)
    @Volatile private var encoding: Int = C.ENCODING_INVALID

    override fun flush(sampleRateHz: Int, channelCount: Int, encoding: Int) {
        this.encoding = encoding
        reset()
    }

    override fun handleBuffer(buffer: ByteBuffer) {
        if (!PlaybackAudioContourRuntime.isObservationRequested()) return
        val sampleBytes = when (encoding) {
            C.ENCODING_PCM_16BIT -> 2
            C.ENCODING_PCM_FLOAT -> 4
            else -> return
        }
        val availableSamples = buffer.remaining() / sampleBytes
        if (availableSamples <= 0) return
        val sampledCount = minOf(availableSamples, MAX_SAMPLES_PER_BUFFER)
        val stride = maxOf(1, availableSamples / sampledCount)
        val start = buffer.position()
        for (index in 0 until CONTOUR_POINT_COUNT) {
            sums[index] = 0f
            counts[index] = 0
        }
        var sum = 0f
        var count = 0
        var sampleIndex = 0
        while (sampleIndex < availableSamples && count < sampledCount) {
            val byteIndex = start + sampleIndex * sampleBytes
            val value = when (encoding) {
                C.ENCODING_PCM_16BIT -> readLittleEndianShort(buffer, byteIndex) / 32768f
                C.ENCODING_PCM_FLOAT -> Float.fromBits(readLittleEndianInt(buffer, byteIndex)).coerceIn(-1f, 1f)
                else -> 0f
            }
            val square = value * value
            val bin = (count * CONTOUR_POINT_COUNT / sampledCount).coerceAtMost(CONTOUR_POINT_COUNT - 1)
            sums[bin] += square
            counts[bin]++
            sum += square
            count++
            sampleIndex += stride
        }
        if (count == 0) return
        val rawEnergy = kotlin.math.sqrt(sum / count).coerceIn(0f, 1f)
        val previous = Float.fromBits(energyBits.get())
        val smoothed = (previous * 0.68f + rawEnergy * 0.32f).coerceIn(0f, 1f)
        energyBits.set(smoothed.toRawBits())
        for (index in 0 until CONTOUR_POINT_COUNT) {
            val raw = if (counts[index] == 0) 0f else kotlin.math.sqrt(sums[index] / counts[index])
            val old = Float.fromBits(contourBits.get(index))
            contourBits.set(index, (old * 0.58f + raw * 0.42f).coerceIn(0f, 1f).toRawBits())
        }
    }

    fun snapshot(): PlaybackAudioContourFrame = PlaybackAudioContourFrame(
        energy = Float.fromBits(energyBits.get()),
        contour = List(CONTOUR_POINT_COUNT) { index -> Float.fromBits(contourBits.get(index)) },
    )

    fun reset() {
        energyBits.set(0f.toRawBits())
        for (index in 0 until CONTOUR_POINT_COUNT) contourBits.set(index, 0f.toRawBits())
    }

    private companion object {
        const val MAX_SAMPLES_PER_BUFFER = 384

        fun readLittleEndianShort(buffer: ByteBuffer, index: Int): Short {
            val low = buffer.get(index).toInt() and 0xFF
            val high = buffer.get(index + 1).toInt() and 0xFF
            return ((high shl 8) or low).toShort()
        }

        fun readLittleEndianInt(buffer: ByteBuffer, index: Int): Int =
            (buffer.get(index).toInt() and 0xFF) or
                ((buffer.get(index + 1).toInt() and 0xFF) shl 8) or
                ((buffer.get(index + 2).toInt() and 0xFF) shl 16) or
                ((buffer.get(index + 3).toInt() and 0xFF) shl 24)
    }
}

@UnstableApi
internal class ReactivePlaybackRenderersFactory(
    context: Context,
    private val contourSink: PlaybackAudioContourSink,
) : DefaultRenderersFactory(context) {
    override fun buildAudioSink(
        context: Context,
        enableFloatOutput: Boolean,
        enableAudioOutputPlaybackParameters: Boolean,
    ): AudioSink = DefaultAudioSink.Builder(context)
        .setEnableFloatOutput(enableFloatOutput)
        .setEnableAudioOutputPlaybackParameters(enableAudioOutputPlaybackParameters)
        .setAudioProcessors(arrayOf<AudioProcessor>(TeeAudioProcessor(contourSink)))
        .build()
}

internal const val CONTOUR_POINT_COUNT = 12
