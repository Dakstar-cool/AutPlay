package app.autplay.application.face

import app.autplay.domain.face.FaceV2IdentityException
import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class FaceV2IdentityCodecTest {
    private val fixtureRoot = File("../../tests/fixtures/face")
    private fun bytes(name: String) = File(fixtureRoot, name).readBytes()
    private fun map() = SourcePresentationMapCodecV1.decode(bytes("source-presentation-map-v1.canonical.json"))

    @Test fun exactPythonGoldenAndMapAncestry() {
        val canonical = bytes("v2-identity.canonical.json")
        val identity = FaceV2IdentityCodec.decode(canonical, map())
        assertArrayEquals(canonical, FaceV2IdentityCodec.encode(identity))
        assertEquals(
            "12b13bf6cc0d2d4f71aa014d7a4b89e27986e489dfa22135d55ca0d58be4cce4",
            FaceV2IdentityCodec.semanticKey(identity),
        )
        assertNotEquals(FaceV2IdentityCodec.semanticKey(identity),
            FaceV2IdentityCodec.semanticKey(identity.copy(executionProfileSha256 = "0".repeat(64))))
        assertNotEquals(FaceV2IdentityCodec.semanticKey(identity),
            FaceV2IdentityCodec.semanticKey(identity.copy(sourcePresentationMapSha256 = "0".repeat(64))))
    }

    @Test fun changedMapDuplicateAndNoncanonicalIdentityFailClosed() {
        val canonical = bytes("v2-identity.canonical.json")
        val identity = FaceV2IdentityCodec.decode(canonical, map())
        val changedMapHash = FaceV2IdentityCodec.encode(
            identity.copy(sourcePresentationMapSha256 = "0".repeat(64)))
        val duplicate = canonical.decodeToString().replace(
            "\"schema_version\":2", "\"schema_version\":2,\"schema_version\":2").toByteArray()
        for (input in listOf(changedMapHash, duplicate, bytes("v2-identity.json"))) {
            assertThrows(FaceV2IdentityException::class.java) {
                FaceV2IdentityCodec.decode(input, map())
            }
        }
    }
}
