package app.autplay.playback

import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertTrue
import org.junit.Test

@UnstableApi
class PlaybackAudioContourTest {
    @Test
    fun pcm16IsReducedToBoundedEnergyWithoutRetainingSamples() {
        val sink = PlaybackAudioContourSink()
        PlaybackAudioContourRuntime.setSurfaceObserving("unit-test", true)
        try {
            sink.flush(44_100, 1, C.ENCODING_PCM_16BIT)
            val pcm = ByteBuffer.allocate(512).order(ByteOrder.LITTLE_ENDIAN)
            repeat(256) { index ->
                pcm.putShort(if (index % 2 == 0) 16_384.toShort() else (-16_384).toShort())
            }
            pcm.flip()
            sink.handleBuffer(pcm)

            val frame = sink.snapshot()
            assertTrue(pcm.position() == 0)
            assertTrue(frame.energy in 0.01f..1f)
            assertTrue(frame.contour.size == CONTOUR_POINT_COUNT)
            assertTrue(frame.contour.all { it in 0f..1f })
        } finally {
            PlaybackAudioContourRuntime.setSurfaceObserving("unit-test", false)
        }
    }
}
