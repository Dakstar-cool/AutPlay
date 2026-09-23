package app.autplay.application.face

import app.autplay.domain.face.SourcePresentationMapException
import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class SourcePresentationMapCodecV1Test {
    private val fixtureRoot = File("../../tests/fixtures/face")
    private fun canonical() = File(fixtureRoot, "source-presentation-map-v1.canonical.json").readBytes()

    @Test fun exactPythonGoldenIdentityAndSeekBoundaries() {
        val value = SourcePresentationMapCodecV1.decode(canonical())
        assertArrayEquals(canonical(), SourcePresentationMapCodecV1.encode(value))
        assertEquals(
            "d28b8c2322def220d3b610d9e1b182f2364590d69830e752b4c11847ede31630",
            SourcePresentationMapCodecV1.sha256(value),
        )
        fun sample(position: Long) = value.sampleAt(
            position, value.encodedSourceSha256, value.decoderId, value.decoderVersion,
            value.probeId, value.probeVersion,
        )
        assertEquals(1024L, sample(0))
        assertEquals(49023L, sample(999_999))
        assertNull(sample(1_000_000))
        assertNull(sample(1_499_999))
        assertEquals(49024L, sample(1_500_000))
        assertEquals(73023L, sample(1_999_999))
        assertNull(sample(2_000_000))
        assertNull(sample(-1))
        assertNull(sample(Long.MAX_VALUE))
        assertNull(value.sampleAt(100, value.encodedSourceSha256, value.decoderId,
            "other", value.probeId, value.probeVersion))
    }

    @Test fun unknownEditDuplicateKeyAndNoncanonicalBytesFailClosed() {
        val text = canonical().decodeToString()
        val invalid = listOf(
            File(fixtureRoot, "source-presentation-map-v1.json").readBytes(),
            text.replace("\"schema_version\":1", "\"schema_version\":1,\"schema_version\":1").toByteArray(),
            text.replace("\"source_start_sample\":1024", "\"source_start_sample\":1024,\"unknown_edit\":1").toByteArray(),
            text.replace("\"decoded_sample_count\":80000", "\"decoded_sample_count\":100").toByteArray(),
        )
        for (bytes in invalid) assertThrows(SourcePresentationMapException::class.java) {
            SourcePresentationMapCodecV1.decode(bytes)
        }
    }

    @Test fun vbrMp3DelayAndPaddingGoldenSeeks() {
        val canonical = File(fixtureRoot, "source-presentation-map-v1-mp3.canonical.json").readBytes()
        val value = SourcePresentationMapCodecV1.decode(canonical)
        assertArrayEquals(canonical, SourcePresentationMapCodecV1.encode(value))
        assertEquals("2f0cdfe40210d20e521d2b4ae524fb1659055235ebd2bbecfb29bcb674a55143",
            SourcePresentationMapCodecV1.sha256(value))
        fun sample(position: Long) = value.sampleAt(position, value.encodedSourceSha256,
            value.decoderId, value.decoderVersion, value.probeId, value.probeVersion)
        assertEquals(576L, sample(0))
        assertEquals(44676L, sample(1_000_000))
        assertEquals(66725L, sample(1_499_999))
        assertEquals(66726L, sample(1_500_000))
        assertEquals(88775L, sample(1_999_999))
        assertNull(sample(2_000_000))
    }
}
