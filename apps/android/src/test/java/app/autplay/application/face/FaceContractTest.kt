package app.autplay.application.face

import app.autplay.domain.face.FaceContractException
import app.autplay.domain.face.FaceSemanticState
import app.autplay.domain.face.TemporalFaceTimeline
import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class FaceContractTest {
    private val fixtureRoot = File("../../tests/fixtures/face/v1")
    private fun bytes(name: String) = File(fixtureRoot, name).readBytes()
    private fun timeline() = FaceContractCodec.decodeTimeline(bytes("timeline.json"))
    private fun projection() = FaceContractCodec.decodeProjection(bytes("projection.json"))
    private fun selection() = projection().let {
        FacePlaybackSelection(it.serverProfileId, it.userId, it.identity, it.activationEpoch, 7)
    }

    @Test fun canonicalHashesMatchPythonGoldenAndPreserveUnknownValues() {
        val timeline = timeline()
        val expected = Json.parseToJsonElement(bytes("hashes.json").decodeToString()).jsonObject
        assertArrayEquals(bytes("timeline.canonical.json"), FaceContractCodec.encodeTimeline(timeline))
        assertEquals(expected.getValue("semantic_key").jsonPrimitive.content, FaceContractCodec.semanticKey(timeline.identity))
        assertEquals(expected.getValue("result_hash").jsonPrimitive.content, FaceContractCodec.resultHash(timeline))
        assertArrayEquals(FaceContractCodec.encodeTimeline(timeline), FaceContractCodec.encodeTimeline(
            FaceContractCodec.decodeTimeline(FaceContractCodec.encodeTimeline(timeline))))
        assertNotNull(timeline.trackCharacter.axes["future_axis"])
        assertTrue(timeline.trackCharacter.axes.getValue("valence").abstained)
        assertNull(timeline.trackCharacter.axes["missing"])
        assertEquals("future_event", timeline.events.first().eventType)
        assertThrows(UnsupportedOperationException::class.java) {
            (timeline.trackCharacter.axes as MutableMap).clear()
        }
    }

    @Test fun sharedInvalidCasesAreRejected() {
        val original = Json.parseToJsonElement(bytes("timeline.json").decodeToString())
        val cases = Json.parseToJsonElement(bytes("invalid-cases.json").decodeToString()).jsonArray
        for (case in cases) {
            val row = case.jsonObject
            val changed = replace(original, row.getValue("path").jsonPrimitive.content.split("."), row.getValue("value"))
            assertThrows(row.getValue("name").jsonPrimitive.content, FaceContractException::class.java) {
                FaceContractCodec.decodeTimeline(changed.toString().toByteArray())
            }
        }
    }

    @Test fun duplicateEscapedKeysAndDepthSizeUtf8NumericLexemesFailClosed() {
        val raw = bytes("timeline.json").decodeToString()
        val cases = listOf(
            "{\"schema_version\":1,\"schema_version\":1}".toByteArray(),
            "{\"axes\":{\"energy\":1,\"en\\u0065rgy\":2}}".toByteArray(),
            "[".repeat(13).plus("]".repeat(13)).toByteArray(),
            ByteArray(1_048_577) { 32 }, byteArrayOf(0xff.toByte()),
            raw.replace("\"time_ms\": 0", "\"time_ms\": 0.0").toByteArray(),
            raw.replace("\"time_ms\": 0", "\"time_ms\": 0e0").toByteArray(),
            raw.replace("\"value\": 0.2", "\"value\": NaN").toByteArray(),
            raw.replace("\"value\": 0.2", "\"value\": 1e999").toByteArray(),
            raw.replace("\"value\": 0.2", "\"value\": " + "9".repeat(400)).toByteArray(),
        )
        val nonJsonNumbers = listOf("+0.2", ".2", "0x1p-1", "1f", "01", "1.")
        for (token in nonJsonNumbers) assertThrows(FaceContractException::class.java) {
            FaceContractCodec.decodeTimeline(raw.replace("\"value\": 0.2", "\"value\": $token").toByteArray())
        }
        for (case in cases) assertThrows(FaceContractException::class.java) {
            FaceContractCodec.decodeTimeline(case)
        }
    }

    @Test fun everyIdentityFieldChangesSemanticKeyButActivationDoesNot() {
        val original = timeline().identity
        val nextId = "30000000-0000-4000-8000-000000000001"
        val changed = listOf(original.copy(recordingId = nextId), original.copy(audioVariantId = nextId),
            original.copy(sourceSha256 = "aa".repeat(32)), original.copy(sourceDurationMs = 11_000),
            original.copy(embeddingModelId = nextId), original.copy(embeddingManifestSha256 = "aa".repeat(32)),
            original.copy(semanticInterpreterId = nextId), original.copy(interpreterManifestSha256 = "aa".repeat(32)),
            original.copy(preprocessingSha256 = "aa".repeat(32)))
        for (candidate in changed) assertNotEquals(FaceContractCodec.semanticKey(original), FaceContractCodec.semanticKey(candidate))
        FaceContractCodec.verifyProjection(timeline(), projection().copy(activationEpoch = 2))
        assertThrows(FaceContractException::class.java) {
            FaceContractCodec.requireSameResult(projection().resultHash, "aa".repeat(32))
        }
    }

    @Test fun interpolationSeekAndPauseUseAbsoluteSourceTimeWithoutDoubleCountingCharacter() {
        val current = selection()
        val sampler = requireNotNull(FaceTimelineSampler.attach(timeline(), projection(), current, current))
        assertEquals(0.0, sampler.sample(current, 5000)!!.axes.getValue("energy").value!!, 0.00001)
        assertEquals(0.5, sampler.sample(current, 5000)!!.axes.getValue("energy").confidence!!, 0.00001)
        assertTrue(sampler.sample(current, 5000)!!.axes.getValue("future_axis").abstained)
        assertEquals(-0.5, sampler.sample(current, 0)!!.axes.getValue("energy").value!!, 0.00001)
        assertEquals(0.5, sampler.sample(current, 10_000)!!.axes.getValue("energy").value!!, 0.00001)
        assertEquals(sampler.sample(current, 2500)!!.axes, sampler.sample(current, 2500)!!.axes)
        assertNull(sampler.sample(current, -1))
        assertNull(sampler.sample(current, 10_001))
    }

    @Test fun lateSourceProfileOwnerEpochGenerationAndCorruptResultFallBack() {
        val current = selection()
        val sampler = requireNotNull(FaceTimelineSampler.attach(timeline(), projection(), current, current))
        val nextId = "30000000-0000-4000-8000-000000000001"
        for (changed in listOf(current.copy(serverProfileId = nextId), current.copy(userId = nextId),
            current.copy(activationEpoch = 2), current.copy(playbackGeneration = 8),
            current.copy(identity = current.identity.copy(audioVariantId = nextId)))) {
            assertNull(sampler.sample(changed, 1000))
        }
        assertNull(FaceTimelineSampler.attach(timeline(), projection().copy(resultHash = "aa".repeat(32)), current, current))
        assertNull(FaceTimelineSampler.attach(timeline(), projection(), current, current.copy(userId = nextId)))
        assertNull(FaceTimelineSampler.attach(timeline(), projection(), current, current.copy(playbackGeneration = 8)))
        val empty = TemporalFaceTimeline(timeline().identity, timeline().trackCharacter, emptyList(), emptyList())
        val binding = projection().copy(resultHash = FaceContractCodec.resultHash(empty))
        val character = FaceTimelineSampler.attach(empty, binding, current, current)!!.sample(current, 2000)!!
        assertEquals(0.2, character.axes.getValue("energy").value!!, 0.00001)
    }

    @Test fun mutableInputCannotChangeAnAttachedResult() {
        val base = timeline()
        val axes = base.trackCharacter.axes.toMutableMap()
        val frames = base.keyframes.toMutableList()
        val events = base.events.toMutableList()
        val value = TemporalFaceTimeline(base.identity, FaceSemanticState(axes), frames, events)
        val hash = FaceContractCodec.resultHash(value)
        axes.clear(); frames.clear(); events.clear()
        assertEquals(hash, FaceContractCodec.resultHash(value))
    }

    private fun replace(value: JsonElement, path: List<String>, replacement: JsonElement): JsonElement {
        if (path.isEmpty()) return replacement
        return if (value is JsonArray) JsonArray(value.mapIndexed { index, item ->
            if (index == path.first().toInt()) replace(item, path.drop(1), replacement) else item
        }) else JsonObject(value.jsonObject.toMutableMap().apply {
            val key = path.first()
            put(key, if (path.size == 1) replacement else replace(getValue(key), path.drop(1), replacement))
        })
    }
}
