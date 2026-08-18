package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import org.erdtman.jcs.JsonCanonicalizer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineRecommendationPackTest {
    @Test
    fun exactHashVersionEncodingOwnerAndExpiryFailClosed() {
        val fresh = entity(payload(itemCount = 2))
        assertFalse(OfflineRecommendationPackCodec.decode(fresh, BINDING, NOW).isStale)

        val tamperedBytes = fresh.payload.copyOf().also { it[it.lastIndex] = ' '.code.toByte() }
        assertCode(OfflinePackErrorCode.HASH_INVALID) {
            OfflineRecommendationPackCodec.decode(fresh.copy(payload = tamperedBytes), BINDING, NOW)
        }
        assertCode(OfflinePackErrorCode.UNKNOWN_VERSION) {
            OfflineRecommendationPackCodec.decode(fresh.copy(payloadVersion = 2), BINDING, NOW)
        }
        assertCode(OfflinePackErrorCode.UNKNOWN_ENCODING) {
            OfflineRecommendationPackCodec.decode(fresh.copy(payloadEncoding = "PROTOBUF"), BINDING, NOW)
        }
        assertCode(OfflinePackErrorCode.OWNER_MISMATCH) {
            OfflineRecommendationPackCodec.decode(fresh.copy(ownerUserId = OTHER_USER), BINDING, NOW)
        }
        assertCode(OfflinePackErrorCode.OWNER_MISMATCH) {
            OfflineRecommendationPackCodec.decode(fresh.copy(ownerUserId = null), BINDING, NOW)
        }

        val stale = entity(payload(itemCount = 1, expiresAtMs = NOW - 1), expiresAtMs = NOW - 1)
        assertCode(OfflinePackErrorCode.EXPIRED) {
            OfflineRecommendationPackCodec.decode(stale, BINDING, NOW)
        }
        val allowed = OfflineRecommendationPackCodec.decode(
            stale,
            BINDING,
            NOW,
            OfflinePackExpiryPolicy.AllowStaleLocalOnly(60_000),
        )
        assertTrue(allowed.isStale)
        assertCode(OfflinePackErrorCode.EXPIRED) {
            OfflineRecommendationPackCodec.decode(
                stale,
                BINDING,
                NOW + 60_001,
                OfflinePackExpiryPolicy.AllowStaleLocalOnly(60_000),
            )
        }
    }

    @Test
    fun canonicalPayloadAndItemBoundsAreEnforced() {
        val canonical = payload(itemCount = 1)
        val nonCanonical = canonical.replaceFirst("{", "{ ")
        assertCode(OfflinePackErrorCode.NON_CANONICAL_PAYLOAD) {
            OfflineRecommendationPackCodec.decode(entityFromBytes(nonCanonical.toByteArray()), BINDING, NOW)
        }

        val tooMany = entity(payload(itemCount = 101, requestLimit = 100))
        assertCode(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED) {
            OfflineRecommendationPackCodec.decode(tooMany, BINDING, NOW)
        }
    }

    @Test
    fun localRerankIsDeterministicFiltersHardSignalsAndRetainsSourceRank() {
        val decoded = OfflineRecommendationPackCodec.decode(entity(payload(itemCount = 6)), BINDING, NOW)
        val items = decoded.items.associateBy(OfflinePackItem::sourceRank)
        val evidence = listOf(
            evidence(items.getValue(1), "Artist A"),
            evidence(items.getValue(2), "Artist A", latestSkipAtMs = NOW - 1_000),
            evidence(items.getValue(3), "Artist B", preference = "LIKED", preferenceUpdatedAtMs = NOW - 1_000),
            evidence(items.getValue(4), "Artist A"),
            evidence(items.getValue(5), "Artist C", preference = "DISLIKED"),
            evidence(items.getValue(6), "Artist D", available = false),
            evidence(items.getValue(1), "Artist A"),
        )

        val first = LocalRecommendationReranker.rerank(decoded, evidence, NOW, maxArtistRepeats = 2)
        val second = LocalRecommendationReranker.rerank(decoded, evidence.reversed(), NOW, maxArtistRepeats = 2)

        assertEquals(first, second)
        assertEquals(listOf(3, 1, 4), first.map(HomeRecommendationItem::sourceRank))
        assertEquals(listOf(1, 2, 3), first.map(HomeRecommendationItem::displayPosition))
        assertEquals(2, first.count { it.artist == "Artist A" })
        assertEquals(3, first.map(HomeRecommendationItem::recordingId).toSet().size)
        assertTrue(first.first().source == "local_rerank")
    }

    @Test
    fun explicitExclusionAlwaysWinsOverFreshLike() {
        val decoded = OfflineRecommendationPackCodec.decode(entity(payload(itemCount = 1)), BINDING, NOW)
        val ranked = LocalRecommendationReranker.rerank(
            decoded,
            listOf(
                evidence(
                    decoded.items.single(),
                    "Artist",
                    preference = "LIKED",
                    preferenceUpdatedAtMs = NOW,
                    excluded = true,
                ),
            ),
            NOW,
        )
        assertTrue(ranked.isEmpty())
    }

    private fun evidence(
        item: OfflinePackItem,
        artist: String,
        preference: String = "NEUTRAL",
        preferenceUpdatedAtMs: Long = 0,
        latestSkipAtMs: Long? = null,
        available: Boolean = true,
        excluded: Boolean = false,
    ) = LocalRecommendationEvidence(
        item = item,
        localUserTrackRefId = uuid(500 + item.sourceRank),
        title = "Track ${item.sourceRank}",
        artist = artist,
        isLocallyAvailable = available,
        preference = preference,
        excludedFromTaste = excluded,
        preferenceUpdatedAtMs = preferenceUpdatedAtMs,
        latestListenedAtMs = null,
        latestSkipAtMs = latestSkipAtMs,
    )

    private fun entity(payload: String, expiresAtMs: Long = EXPIRES): RecommendationPackEntity =
        entityFromBytes(payload.toByteArray(StandardCharsets.UTF_8), expiresAtMs)

    private fun entityFromBytes(bytes: ByteArray, expiresAtMs: Long = EXPIRES) = RecommendationPackEntity(
        offlinePackId = PACK,
        serverProfileId = PROFILE,
        ownerUserId = USER,
        catalogSnapshot = 7,
        modelBundleVersion = "cpu-v1",
        payloadVersion = 1,
        payloadEncoding = "RAW_JSON",
        payload = bytes,
        payloadSha256 = MessageDigest.getInstance("SHA-256").digest(bytes),
        createdAtMs = CREATED,
        expiresAtMs = expiresAtMs,
    )

    private fun payload(itemCount: Int, expiresAtMs: Long = EXPIRES, requestLimit: Int = itemCount.coerceAtMost(100)): String {
        val items = (1..itemCount).joinToString(",") { rank ->
            """{"offline_pack_id":"$PACK","recording_id":"${uuid(100 + rank)}","source_rank":$rank,"pack_position":$rank,"section":"for_you","score":${100 - rank}.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":$rank,"raw_score":${100 - rank}.0,"provenance":{"kind":"explicit"}}]}"""
        }
        val raw = """{"payload_version":1,"offline_pack_id":"$PACK","recommendation_request_id":"$REQUEST","user_id":"$USER","device_id":"$DEVICE","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$expiresAtMs,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":$requestLimit,"exploration":0.1,"seed":42,"shadow":false},"items":[$items]}"""
        return JsonCanonicalizer(raw).encodedString
    }

    private fun assertCode(expected: OfflinePackErrorCode, action: () -> Unit) {
        val error = assertThrows(OfflinePackException::class.java, action)
        assertEquals(expected, error.code)
    }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val USER = "22222222-2222-4222-8222-222222222222"
        const val OTHER_USER = "32222222-2222-4222-8222-222222222222"
        const val DEVICE = "33333333-3333-4333-8333-333333333333"
        const val PACK = "44444444-4444-4444-8444-444444444444"
        const val REQUEST = "55555555-5555-4555-8555-555555555555"
        const val CREATED = 1_000L
        const val NOW = 2_000L
        const val EXPIRES = 10_000L
        val BINDING = ClientEventBinding(UserId(USER), DeviceId(DEVICE), ServerProfileId(PROFILE))

        fun uuid(number: Int): String = "00000000-0000-4000-8000-${number.toString().padStart(12, '0')}"
    }
}
