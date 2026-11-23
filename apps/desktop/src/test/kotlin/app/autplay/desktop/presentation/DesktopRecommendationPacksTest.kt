package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopWaveRecommendation
import app.autplay.application.desktop.account.*
import app.autplay.application.recommendation.*
import java.security.MessageDigest
import java.util.Base64
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class DesktopRecommendationPacksTest {
    @get:Rule val folder = TemporaryFolder()
    private val scope = PackScope(AccountUuid(uuid(1)), AccountUuid(uuid(2)), RecommendationPackOwner(PROFILE, USER, DEVICE))

    @Test fun readsOnlyEligiblePacksAndRetainsPreviousOnFailure() {
        val packs = DesktopRecommendationPacks(folder.root.toPath().toRealPath())
        val body = download()
        packs.store(scope, "GENERAL", body, NOW) { true }
        assertEquals(2, packs.read(scope, "GENERAL", NOW)!!.items.size)
        assertNull(packs.read(scope, "WORK", NOW))
        assertNull(packs.read(scope.copy(owner = scope.owner.copy(user = OTHER_USER)), "GENERAL", NOW))
        assertThrows(IllegalArgumentException::class.java) {
            packs.store(scope.copy(owner = scope.owner.copy(device = OTHER_USER)), "GENERAL", body, NOW) { true }
        }
        assertThrows(IllegalArgumentException::class.java) { packs.store(scope, "SLEEP", body, NOW) { true } }
        assertThrows(IllegalArgumentException::class.java) {
            packs.store(scope, "GENERAL", JsonObject(body + ("payload_sha256" to JsonPrimitive("0".repeat(64)))), NOW) { true }
        }
        var activeChecks = 0
        assertThrows(IllegalStateException::class.java) { packs.store(scope, "GENERAL", body, NOW) { ++activeChecks == 1 } }
        assertNull(packs.read(scope, "GENERAL", EXPIRES))
        val grace = OfflinePackExpiryPolicy.AllowStaleLocalOnly(86_400_000L)
        assertTrue(packs.read(scope, "GENERAL", EXPIRES + 86_400_000, grace)!!.isStale)
        assertNull(packs.read(scope, "GENERAL", EXPIRES + 86_400_001, grace))
        val file = folder.root.walkTopDown().single { it.name == "GENERAL.json" }
        file.writeBytes(byteArrayOf(0xff.toByte()))
        assertNull(packs.read(scope, "GENERAL", NOW))
    }

    @Test fun preservesOnlineAndOfflineAttributionWhenReopeningQueue() {
        val online = DesktopWaveRecommendation(REQUEST, uuid(101), 1, "for_you", "GENERAL")
        val offline = online.copy(offlinePackId = PACK, surface = "home")
        assertEquals(online, restoredWaveRecommendation(online.queueDocument()))
        assertEquals(offline, restoredWaveRecommendation(offline.queueDocument()))
        assertEquals("offline_pack", offline.attribution().getValue("source").jsonPrimitive.content)
    }

    private fun download(): JsonObject {
        val bytes = payload(2).encodeToByteArray()
        return buildJsonObject {
            put("payload_version", 1); put("payload_encoding", "RAW_JSON"); put("offline_pack_id", PACK)
            put("recommendation_request_id", REQUEST); put("created_at_ms", CREATED); put("expires_at_ms", EXPIRES)
            put("payload_base64", Base64.getEncoder().encodeToString(bytes))
            put("payload_sha256", java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)))
        }
    }

    private fun payload(itemCount: Int, expiresAtMs: Long = EXPIRES, requestLimit: Int = itemCount.coerceAtMost(100)): String {
        val items = (1..itemCount).joinToString(",") { rank ->
            """{"offline_pack_id":"$PACK","recording_id":"${uuid(100 + rank)}","source_rank":$rank,"pack_position":$rank,"section":"for_you","score":${100 - rank}.0,"reason_code":"AFFINITY","reason_codes":["AFFINITY"],"contributions":[{"source_key":"library_affinity","source_version":"1","source_rank":$rank,"raw_score":${100 - rank}.0,"provenance":{"kind":"explicit"}}]}"""
        }
        val raw = """{"payload_version":1,"offline_pack_id":"$PACK","recommendation_request_id":"$REQUEST","user_id":"$USER","device_id":"$DEVICE","pipeline":{"key":"cpu_baseline","version":"cpu-v1","manifest_sha256":"${"a".repeat(64)}"},"input_snapshot_sha256":"${"b".repeat(64)}","catalog_snapshot":7,"availability_snapshot":"availability-7","created_at_ms":$CREATED,"expires_at_ms":$expiresAtMs,"request":{"schema_version":1,"canonicalization_version":1,"surface":"home","context":"GENERAL","limit":$requestLimit,"exploration":0.1,"seed":42,"shadow":false},"items":[$items]}"""
        return JsonCanonicalizer(raw).encodedString
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

        fun uuid(number: Int): String = "00000000-0000-4000-8000-${number.toString().padStart(12, '0')}"
    }
}
