package app.autplay.application.face

import app.autplay.domain.face.FaceV2TimelineException
import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class FaceV2TimelineCodecTest {
    private val root = File("../../tests/fixtures/face")
    private fun bytes(name: String) = File(root, name).readBytes()

    @Test fun exactPythonGoldenResultAndDomainHash() {
        val canonical = bytes("v2-timeline.canonical.json")
        val timeline = FaceV2TimelineCodec.decode(canonical)
        assertArrayEquals(canonical, FaceV2TimelineCodec.encode(timeline))
        assertEquals("b0f49f7565e575b90c890a06fa2494d7e28a92371d33b725903985497e387b17",
            FaceV2TimelineCodec.resultHash(timeline))
        assertEquals("application/vnd.autplay.face-timeline.v2+json", FaceV2TimelineCodec.CONTENT_TYPE)
        assertEquals(1024L, timeline.keyframes.first().sampleIndex)
        assertEquals(49024L, timeline.keyframes.last().sampleIndex)
        assertNotEquals(FaceV2TimelineCodec.resultHash(timeline),
            FaceV2TimelineCodec.resultHash(FaceV2TimelineCodec.decode(canonical.decodeToString()
                .replace("\"strength\":0.5", "\"strength\":0.75").toByteArray())))
    }

    @Test fun noncanonicalAndWrongMapAncestryFailClosed() {
        val canonical = bytes("v2-timeline.canonical.json")
        val changedMap = canonical.decodeToString().replace(
            "\"decoder_version\":\"7.1.1\"", "\"decoder_version\":\"other\"").toByteArray()
        val duplicate = canonical.decodeToString().replace(
            "\"schema_version\":2", "\"schema_version\":2,\"schema_version\":2").toByteArray()
        for (candidate in listOf(bytes("v2-timeline.json"), changedMap, duplicate)) {
            assertThrows(FaceV2TimelineException::class.java) {
                FaceV2TimelineCodec.decode(candidate)
            }
        }
    }
}
