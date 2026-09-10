package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.dao.LocalTemporalJournalRow
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.erdtman.jcs.JsonCanonicalizer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineTemporalDeltaTest {
    @Test
    fun impressionKeyMatchesAcceptedRfc8785Vector() {
        val binding = ClientEventBinding(
            UserId("00000000-0000-7000-8000-000000000001"),
            DeviceId("00000000-0000-7000-8000-000000000201"),
            ServerProfileId(PROFILE),
        )

        assertEquals(
            "d6ebda61574ec2c49725856cbe41a23bae20370993c9c8f0a18b136b1d3dd56a",
            OfflineTemporalDeltaCodec.impressionKey(
                binding,
                "00000000-0000-7000-8000-000000000801",
                "00000000-0000-7000-8000-000000000301",
                4,
            ),
        )
    }

    @Test
    fun deltaIsDeterministicBoundedAndLeavesParentBytesAndRanksUntouched() {
        val entity = entity(itemCount = 2)
        val parentBytes = entity.payload.copyOf()
        val parent = OfflineRecommendationPackCodec.decode(entity, BINDING, NOW)
        val base = baseItems(parent)
        val rows = (1..257).map { index ->
            preferenceRow(
                eventId = uuid(1_000 + index),
                deviceSequence = index.toLong(),
                occurredAtMs = NOW - (257 - index) * 1_000L,
                recordingId = if (index % 2 == 0) RECORDING_1 else RECORDING_2,
            )
        }

        val first = requireNotNull(
            OfflineTemporalDeltaCodec.build(entity, parent, BINDING, rows, base, NOW),
        )
        val second = requireNotNull(
            OfflineTemporalDeltaCodec.build(entity, parent, BINDING, rows.reversed(), base, NOW),
        )

        assertArrayEquals(first.payload, second.payload)
        assertArrayEquals(parentBytes, entity.payload)
        val decoded = OfflineTemporalDeltaCodec.decode(first, entity, parent, BINDING, NOW)
        assertEquals(parent.items.map { it.sourceRank }.toSet(), decoded.adjustments.map { it.sourceRank }.toSet())
        assertEquals(256, payload(first.payload).getValue("local_events").jsonArray.size)
        assertEquals(
            "5127e1ed377d98da86b947386fd2a38fe705b5dbf8072243715eed8b5ee499bb",
            first.parentItemsSha256.toHex(),
        )
        assertTrue(decoded.adjustments.all { it.boundedDelta in -0.75..0.75 })
    }

    @Test
    fun episodeGapExpiryAndForeignBindingFailClosed() {
        val entity = entity(itemCount = 1, expiresAtMs = NOW + 10_000)
        val parent = OfflineRecommendationPackCodec.decode(entity, BINDING, NOW)
        val base = baseItems(parent)
        val oldOnly = listOf(
            preferenceRow(uuid(4_001), 1, NOW - OfflineTemporalDeltaCodec.EPISODE_GAP_MS - 1, RECORDING_1),
        )
        assertEquals(null, OfflineTemporalDeltaCodec.build(entity, parent, BINDING, oldOnly, base, NOW))

        val fresh = preferenceRow(uuid(4_002), 2, NOW - 1, RECORDING_1)
        val delta = requireNotNull(OfflineTemporalDeltaCodec.build(entity, parent, BINDING, listOf(fresh), base, NOW))
        assertEquals(entity.expiresAtMs, delta.expiresAtMs)
        assertCode(OfflineTemporalDeltaErrorCode.EXPIRED) {
            OfflineTemporalDeltaCodec.decode(delta, entity, parent, BINDING, delta.expiresAtMs)
        }
        val foreign = fresh.copy(ownerUserId = OTHER_USER)
        assertCode(OfflineTemporalDeltaErrorCode.OWNER_MISMATCH) {
            OfflineTemporalDeltaCodec.build(entity, parent, BINDING, listOf(foreign), base, NOW)
        }
    }

    @Test
    fun tamperedOrServerSequencedEvidenceFailsClosed() {
        val entity = entity(itemCount = 1)
        val parent = OfflineRecommendationPackCodec.decode(entity, BINDING, NOW)
        val delta = requireNotNull(
            OfflineTemporalDeltaCodec.build(
                entity,
                parent,
                BINDING,
                listOf(preferenceRow(uuid(5_001), 1, NOW - 1, RECORDING_1)),
                baseItems(parent),
                NOW,
            ),
        )
        val root = payload(delta.payload)
        val event = root.getValue("local_events").jsonArray.single().jsonObject
        val serverSequencedEvent = JsonObject(event + ("server_sequence" to kotlinx.serialization.json.JsonPrimitive(1)))
        val changed = JsonObject(root + ("local_events" to JsonArray(listOf(serverSequencedEvent))))
        val canonical = JsonCanonicalizer(changed.toString()).encodedUTF8
        val tampered = delta.copy(
            payload = canonical,
            payloadSha256 = MessageDigest.getInstance("SHA-256").digest(canonical),
        )

        assertCode(OfflineTemporalDeltaErrorCode.HASH_INVALID) {
            OfflineTemporalDeltaCodec.decode(tampered, entity, parent, BINDING, NOW)
        }
    }

    private fun baseItems(parent: DecodedOfflinePack): List<HomeRecommendationItem> = parent.items.mapIndexed { index, item ->
        HomeRecommendationItem(
            offlinePackId = item.offlinePackId,
            recommendationRequestId = parent.recommendationRequestId,
            recordingId = item.recordingId,
            localUserTrackRefId = uuid(8_000 + index),
            title = "Track ${index + 1}",
            artist = "Artist ${index + 1}",
            sourceRank = item.sourceRank,
            packPosition = item.packPosition,
            displayPosition = index + 1,
            sectionKey = item.section,
            surface = parent.request.surface,
            source = "offline_pack",
            reasonCode = item.reasonCode,
        )
    }

    private fun preferenceRow(
        eventId: String,
        deviceSequence: Long,
        occurredAtMs: Long,
        recordingId: String,
    ): LocalTemporalJournalRow {
        val raw = """{"attribution":null,"excluded_from_taste":false,"local_user_track_ref_id":"${uuid(9_000 + deviceSequence.toInt())}","preference":"LIKED"}"""
        return LocalTemporalJournalRow(
            sourceEventId = eventId,
            ownerUserId = USER,
            serverProfileId = PROFILE,
            deviceId = DEVICE,
            deviceSequence = deviceSequence,
            sourceEventType = "USER_TRACK_PREFERENCE_SET",
            aggregateLocalId = uuid(9_000 + deviceSequence.toInt()),
            payloadJson = JsonCanonicalizer(raw).encodedString,
            sourceRequestSha256 = MessageDigest.getInstance("SHA-256").digest("request-$eventId".toByteArray()),
            occurredAtMs = occurredAtMs,
            recordingId = recordingId,
        )
    }

    private fun entity(itemCount: Int, expiresAtMs: Long = EXPIRES): RecommendationPackEntity {
        val payload = packPayload(itemCount, expiresAtMs).toByteArray(StandardCharsets.UTF_8)
        return RecommendationPackEntity(
            offlinePackId = PACK,
            serverProfileId = PROFILE,
            ownerUserId = USER,
            catalogSnapshot = 7,
            modelBundleVersion = "cpu-v1",
            payloadVersion = 1,
            payloadEncoding = "RAW_JSON",
            payload = payload,
            payloadSha256 = MessageDigest.getInstance("SHA-256").digest(payload),
            createdAtMs = CREATED,
            expiresAtMs = expiresAtMs,
        )
    }

    private fun packPayload(itemCount: Int, expiresAtMs: Long): String {
        val recordings = listOf(RECORDING_1, RECORDING_2)
        val items = (1..itemCount).joinToString(",") { rank ->
            """{"offline_pack_id":"$PACK","recording_id":"${recordings[rank - 1]}","source_rank":$rank,"pack_position":$rank,"section":"for_you","score":${100 - rank}.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":$rank,"raw_score":${100 - rank}.0,"provenance":{"kind":"explicit"}}]}"""
        }
        val raw = """{"payload_version":1,"offline_pack_id":"$PACK","recommendation_request_id":"$REQUEST","user_id":"$USER","device_id":"$DEVICE","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$expiresAtMs,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":$itemCount,"exploration":0.1,"seed":42,"shadow":false},"items":[$items]}"""
        return JsonCanonicalizer(raw).encodedString
    }

    private fun payload(bytes: ByteArray): JsonObject =
        Json.parseToJsonElement(bytes.toString(StandardCharsets.UTF_8)).jsonObject

    private fun assertCode(expected: OfflineTemporalDeltaErrorCode, action: () -> Unit) {
        val error = assertThrows(OfflineTemporalDeltaException::class.java, action)
        assertEquals(expected, error.code)
    }

    private fun ByteArray.toHex() = joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val OTHER_USER = "32222222-2222-4222-8222-222222222222"
        const val DEVICE = "33333333-3333-4333-8333-333333333333"
        const val PACK = "44444444-4444-4444-8444-444444444444"
        const val REQUEST = "55555555-5555-4555-8555-555555555555"
        const val RECORDING_1 = "66666666-6666-4666-8666-666666666661"
        const val RECORDING_2 = "66666666-6666-4666-8666-666666666662"
        const val CREATED = 1_000L
        const val NOW = 2_000_000L
        const val EXPIRES = NOW + 700_000_000L
        val BINDING = ClientEventBinding(UserId(USER), DeviceId(DEVICE), ServerProfileId(PROFILE))

        fun uuid(number: Int): String = "00000000-0000-4000-8000-${number.toString().padStart(12, '0')}"
    }
}
