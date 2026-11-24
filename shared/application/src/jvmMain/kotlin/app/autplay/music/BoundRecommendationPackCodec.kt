package app.autplay.application.recommendation

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Base64
import java.util.Locale
import java.util.UUID
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer

data class RecommendationPackOwner(val profile: String, val user: String, val device: String)

data class RecommendationPackEnvelope(
    val offlinePackId: String, val serverProfileId: String, val ownerUserId: String?,
    val catalogSnapshot: Long, val modelBundleVersion: String, val payloadVersion: Int,
    val payloadEncoding: String, val payload: ByteArray, val payloadSha256: ByteArray,
    val createdAtMs: Long, val expiresAtMs: Long,
)

enum class OfflinePackErrorCode {
    PAYLOAD_TOO_LARGE,
    HASH_INVALID,
    UNKNOWN_VERSION,
    UNKNOWN_ENCODING,
    MALFORMED_PAYLOAD,
    NON_CANONICAL_PAYLOAD,
    OWNER_MISMATCH,
    DEVICE_MISMATCH,
    ENVELOPE_MISMATCH,
    PAYLOAD_BOUNDS_EXCEEDED,
    EXPIRED,
}

class OfflinePackException(val code: OfflinePackErrorCode) : IllegalArgumentException(code.name)

data class RecommendationPackFetchRequest(
    val context: String = "GENERAL",
    val limit: Int = 25,
    val exploration: Double = 0.2,
    val seed: Long = 0,
    val pipelineKey: String = "cpu-baseline",
    val pipelineVersion: String? = null,
    val shadow: Boolean = false,
    val ttlDays: Int = 7,
) {
    init {
        require(context.matches(Regex("^[A-Z][A-Z0-9_]{0,19}$")))
        require(limit in 1..100 && exploration.isFinite() && exploration in 0.0..1.0)
        require(pipelineKey.matches(Regex("^[a-z][a-z0-9-]{0,99}$")))
        require(pipelineVersion == null || pipelineVersion.length in 1..100)
        require(ttlDays in 1..30)
    }
}

data class DownloadedRecommendationPack(
    val offlinePackId: String,
    val recommendationRequestId: String,
    val payloadVersion: Int,
    val payloadEncoding: String,
    val payloadBase64: String,
    val payloadSha256: String,
    val createdAtMs: Long,
    val expiresAtMs: Long,
)

sealed interface OfflinePackExpiryPolicy {
    data object FreshOnly : OfflinePackExpiryPolicy

    data class AllowStaleLocalOnly(val maxStalenessMs: Long) : OfflinePackExpiryPolicy {
        init {
            require(maxStalenessMs in 1..MAX_STALE_FALLBACK_MS)
        }
    }

    companion object {
        const val MAX_STALE_FALLBACK_MS: Long = 7L * 24 * 60 * 60 * 1000
    }
}

data class OfflinePackPipeline(
    val key: String,
    val version: String,
    val manifestSha256: String,
)

data class OfflinePackRequest(
    val surface: String,
    val context: String,
    val limit: Int,
    val exploration: Double,
    val seed: Long,
    val shadow: Boolean,
)

data class OfflinePackContribution(
    val sourceKey: String,
    val sourceVersion: String,
    val sourceRank: Int,
    val rawScore: Double,
    /** Canonical bounded JSON retained as provenance, never executed on Android. */
    val provenanceJson: String,
)

data class OfflinePackItem(
    val offlinePackId: String,
    val recordingId: String,
    val sourceRank: Int,
    val packPosition: Int,
    val section: String,
    /** A server heuristic ordering value, deliberately not exposed as a probability. */
    val heuristicScore: Double,
    val reasonCode: String,
    val reasonCodes: List<String>,
    val contributions: List<OfflinePackContribution>,
)

data class DecodedOfflinePack(
    val offlinePackId: String,
    val recommendationRequestId: String,
    val userId: String,
    val deviceId: String,
    val pipeline: OfflinePackPipeline,
    val inputSnapshotSha256: String,
    val catalogSnapshot: Long,
    val availabilitySnapshot: String,
    val createdAtMs: Long,
    val expiresAtMs: Long,
    val request: OfflinePackRequest,
    val items: List<OfflinePackItem>,
    val isStale: Boolean,
)

/** Strict, bounded decoder for the model-independent RAW_JSON offline-pack contract. */
object BoundRecommendationPackCodec {
    const val PAYLOAD_VERSION = 1
    const val PAYLOAD_ENCODING = "RAW_JSON"
    const val MAX_PAYLOAD_BYTES = 524_288
    const val MAX_ITEMS = 100

    private val json = Json {
        isLenient = false
        ignoreUnknownKeys = false
        allowSpecialFloatingPointValues = false
    }
    private val safeLowerToken = Regex("^[a-z][a-z0-9_]{0,99}$")
    private val safeUpperToken = Regex("^[A-Z][A-Z0-9_]{0,99}$")
    private val sha256Hex = Regex("^[0-9a-f]{64}$")

    /** Converts the bounded HTTP response into the raw envelope before strict full decode. */
    fun entityFromDownload(download: DownloadedRecommendationPack, binding: RecommendationPackOwner): RecommendationPackEnvelope {
        if (download.payloadVersion != PAYLOAD_VERSION) fail(OfflinePackErrorCode.UNKNOWN_VERSION)
        if (download.payloadEncoding != PAYLOAD_ENCODING) fail(OfflinePackErrorCode.UNKNOWN_ENCODING)
        if (!sha256Hex.matches(download.payloadSha256)) fail(OfflinePackErrorCode.HASH_INVALID)
        if (download.payloadBase64.length > MAX_BASE64_LENGTH) fail(OfflinePackErrorCode.PAYLOAD_TOO_LARGE)
        val bytes = try {
            Base64.getDecoder().decode(download.payloadBase64)
        } catch (_: IllegalArgumentException) {
            fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        }
        if (bytes.isEmpty() || bytes.size > MAX_PAYLOAD_BYTES) fail(OfflinePackErrorCode.PAYLOAD_TOO_LARGE)
        val expectedHash = download.payloadSha256.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        if (!MessageDigest.isEqual(sha256(bytes), expectedHash)) fail(OfflinePackErrorCode.HASH_INVALID)
        val root = try {
            json.parseToJsonElement(bytes.toString(StandardCharsets.UTF_8)) as? JsonObject
        } catch (_: Exception) {
            null
        } ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        val packId = root.requiredUuid("offline_pack_id")
        val requestId = root.requiredUuid("recommendation_request_id")
        if (packId != download.offlinePackId || requestId != download.recommendationRequestId) {
            fail(OfflinePackErrorCode.ENVELOPE_MISMATCH)
        }
        val pipelineVersion = root.requiredObject("pipeline").requiredString("version", MAX_TOKEN_LENGTH)
        return RecommendationPackEnvelope(
            offlinePackId = packId,
            serverProfileId = binding.profile,
            ownerUserId = binding.user,
            catalogSnapshot = root.requiredLong("catalog_snapshot", 0, Long.MAX_VALUE),
            modelBundleVersion = pipelineVersion,
            payloadVersion = PAYLOAD_VERSION,
            payloadEncoding = PAYLOAD_ENCODING,
            payload = bytes,
            payloadSha256 = expectedHash,
            createdAtMs = download.createdAtMs,
            expiresAtMs = download.expiresAtMs,
        )
    }

    fun decode(
        entity: RecommendationPackEnvelope,
        binding: RecommendationPackOwner,
        nowMs: Long,
        expiryPolicy: OfflinePackExpiryPolicy = OfflinePackExpiryPolicy.FreshOnly,
    ): DecodedOfflinePack {
        require(nowMs >= 0)
        if (entity.serverProfileId != binding.profile || entity.ownerUserId != binding.user) {
            fail(OfflinePackErrorCode.OWNER_MISMATCH)
        }
        if (entity.payloadVersion != PAYLOAD_VERSION) fail(OfflinePackErrorCode.UNKNOWN_VERSION)
        if (entity.payloadEncoding != PAYLOAD_ENCODING) fail(OfflinePackErrorCode.UNKNOWN_ENCODING)
        if (entity.payload.isEmpty() || entity.payload.size > MAX_PAYLOAD_BYTES) {
            fail(OfflinePackErrorCode.PAYLOAD_TOO_LARGE)
        }
        if (entity.payloadSha256.size != SHA256_BYTES || !MessageDigest.isEqual(sha256(entity.payload), entity.payloadSha256)) {
            fail(OfflinePackErrorCode.HASH_INVALID)
        }

        val payloadText = entity.payload.toString(StandardCharsets.UTF_8)
        if (!payloadText.toByteArray(StandardCharsets.UTF_8).contentEquals(entity.payload)) {
            fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        }
        val canonical = try {
            JsonCanonicalizer(payloadText).encodedString
        } catch (_: Exception) {
            fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        }
        if (!canonical.toByteArray(StandardCharsets.UTF_8).contentEquals(entity.payload)) {
            fail(OfflinePackErrorCode.NON_CANONICAL_PAYLOAD)
        }
        val root = try {
            json.parseToJsonElement(payloadText) as? JsonObject
        } catch (_: Exception) {
            null
        } ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        validateBounded(root, 0)

        if (root.requiredInt("payload_version") != PAYLOAD_VERSION) fail(OfflinePackErrorCode.UNKNOWN_VERSION)
        val packId = root.requiredUuid("offline_pack_id")
        val requestId = root.requiredUuid("recommendation_request_id")
        val userId = root.requiredUuid("user_id")
        val deviceId = root.requiredUuid("device_id")
        val catalogSnapshot = root.requiredLong("catalog_snapshot", 0, Long.MAX_VALUE)
        val createdAtMs = root.requiredLong("created_at_ms", 0, Long.MAX_VALUE)
        val expiresAtMs = root.requiredLong("expires_at_ms", 0, Long.MAX_VALUE)
        if (
            packId != entity.offlinePackId ||
            userId != entity.ownerUserId ||
            catalogSnapshot != entity.catalogSnapshot ||
            createdAtMs != entity.createdAtMs ||
            expiresAtMs != entity.expiresAtMs ||
            createdAtMs >= expiresAtMs
        ) {
            fail(OfflinePackErrorCode.ENVELOPE_MISMATCH)
        }
        if (userId != binding.user) fail(OfflinePackErrorCode.OWNER_MISMATCH)
        if (deviceId != binding.device) fail(OfflinePackErrorCode.DEVICE_MISMATCH)

        val stale = expiresAtMs <= nowMs
        if (stale) {
            val maxStaleness = (expiryPolicy as? OfflinePackExpiryPolicy.AllowStaleLocalOnly)?.maxStalenessMs
                ?: fail(OfflinePackErrorCode.EXPIRED)
            if (nowMs - expiresAtMs > maxStaleness) fail(OfflinePackErrorCode.EXPIRED)
        }

        val pipelineObject = root.requiredObject("pipeline")
        val pipeline = OfflinePackPipeline(
            key = pipelineObject.requiredString("key", MAX_TOKEN_LENGTH),
            version = pipelineObject.requiredString("version", MAX_TOKEN_LENGTH),
            manifestSha256 = pipelineObject.requiredHex("manifest_sha256"),
        )
        if (entity.modelBundleVersion != pipeline.version) fail(OfflinePackErrorCode.ENVELOPE_MISMATCH)

        val requestObject = root.requiredObject("request")
        if (requestObject.requiredInt("schema_version") != 1 || requestObject.requiredInt("canonicalization_version") != 1) {
            fail(OfflinePackErrorCode.UNKNOWN_VERSION)
        }
        val surface = requestObject.requiredString("surface", MAX_TOKEN_LENGTH)
        if (!safeLowerToken.matches(surface)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        val context = requestObject.requiredString("context", MAX_TOKEN_LENGTH)
        if (!safeUpperToken.matches(context)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        val request = OfflinePackRequest(
            surface = surface,
            context = context,
            limit = requestObject.requiredInt("limit", 1, MAX_ITEMS),
            exploration = requestObject.requiredDouble("exploration", 0.0, 1.0),
            seed = requestObject.requiredLong("seed", Long.MIN_VALUE, Long.MAX_VALUE),
            shadow = requestObject.requiredBoolean("shadow"),
        )

        val itemArray = root.requiredArray("items")
        if (itemArray.size > MAX_ITEMS || itemArray.size > request.limit) {
            fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        }
        val items = itemArray.map { decodeItem(it as? JsonObject ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD), packId) }
        if (
            items.map { it.recordingId }.toSet().size != items.size ||
            items.map { it.sourceRank }.toSet().size != items.size ||
            items.map { it.packPosition }.toSet().size != items.size
        ) {
            fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        }

        return DecodedOfflinePack(
            offlinePackId = packId,
            recommendationRequestId = requestId,
            userId = userId,
            deviceId = deviceId,
            pipeline = pipeline,
            inputSnapshotSha256 = root.requiredHex("input_snapshot_sha256"),
            catalogSnapshot = catalogSnapshot,
            availabilitySnapshot = root.requiredString("availability_snapshot", MAX_SNAPSHOT_LENGTH),
            createdAtMs = createdAtMs,
            expiresAtMs = expiresAtMs,
            request = request,
            items = items.sortedWith(compareBy(OfflinePackItem::packPosition, OfflinePackItem::recordingId)),
            isStale = stale,
        )
    }

    private fun decodeItem(value: JsonObject, packId: String): OfflinePackItem {
        if (value.requiredUuid("offline_pack_id") != packId) fail(OfflinePackErrorCode.ENVELOPE_MISMATCH)
        val section = value.requiredString("section", MAX_TOKEN_LENGTH)
        if (!safeLowerToken.matches(section)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        val reasonCode = value.requiredString("reason_code", MAX_TOKEN_LENGTH)
        if (!safeUpperToken.matches(reasonCode)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        val reasons = value.requiredArray("reason_codes").map { element ->
            val reason = (element as? JsonPrimitive)?.takeIf(JsonPrimitive::isString)?.content
                ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
            if (!safeUpperToken.matches(reason)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
            reason
        }
        if (reasons.size > MAX_REASONS) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        val contributions = value.requiredArray("contributions").map { element ->
            val contribution = element as? JsonObject ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
            OfflinePackContribution(
                sourceKey = contribution.requiredString("source_key", MAX_TOKEN_LENGTH),
                sourceVersion = contribution.requiredString("source_version", MAX_TOKEN_LENGTH),
                sourceRank = contribution.requiredInt("source_rank", 1, MAX_ITEMS * 10),
                rawScore = contribution.requiredDouble("raw_score", -MAX_ABS_SCORE, MAX_ABS_SCORE),
                provenanceJson = contribution.requiredObject("provenance").toString(),
            )
        }
        if (contributions.isEmpty() || contributions.size > MAX_CONTRIBUTIONS) {
            fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        }
        return OfflinePackItem(
            offlinePackId = packId,
            recordingId = value.requiredUuid("recording_id"),
            sourceRank = value.requiredInt("source_rank", 1, MAX_ITEMS * 10),
            packPosition = value.requiredInt("pack_position", 1, MAX_ITEMS),
            section = section,
            heuristicScore = value.requiredDouble("score", -MAX_ABS_SCORE, MAX_ABS_SCORE),
            reasonCode = reasonCode,
            reasonCodes = reasons,
            contributions = contributions,
        )
    }

    private fun validateBounded(value: JsonElement, depth: Int) {
        if (depth > MAX_JSON_DEPTH) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        when (value) {
            is JsonObject -> {
                if (value.size > MAX_OBJECT_PROPERTIES) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
                value.forEach { (key, child) ->
                    if (key.isEmpty() || key.length > MAX_PROPERTY_LENGTH) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
                    validateBounded(child, depth + 1)
                }
            }
            is JsonArray -> {
                if (value.size > MAX_ARRAY_VALUES) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
                value.forEach { validateBounded(it, depth + 1) }
            }
            is JsonPrimitive -> if (value.isString && value.content.length > MAX_STRING_LENGTH) {
                fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
            }
            is JsonNull -> Unit
        }
    }

    private fun JsonObject.requiredObject(name: String): JsonObject =
        this[name] as? JsonObject ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredArray(name: String): JsonArray =
        this[name] as? JsonArray ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredString(name: String, maxLength: Int): String {
        val primitive = this[name] as? JsonPrimitive ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        if (!primitive.isString || primitive.content.isEmpty() || primitive.content.length > maxLength) {
            fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        }
        return primitive.content
    }

    private fun JsonObject.requiredUuid(name: String): String {
        val raw = requiredString(name, UUID_LENGTH)
        val parsed = runCatching { UUID.fromString(raw) }.getOrNull()
            ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        if (parsed.toString() != raw || raw != raw.lowercase(Locale.ROOT)) fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        return raw
    }

    private fun JsonObject.requiredHex(name: String): String {
        val raw = requiredString(name, SHA256_HEX_LENGTH)
        if (!sha256Hex.matches(raw)) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        return raw
    }

    private fun JsonObject.requiredInt(name: String): Int =
        (this[name] as? JsonPrimitive)?.intOrNull ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredInt(name: String, minimum: Int, maximum: Int): Int =
        requiredInt(name).also { if (it !in minimum..maximum) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED) }

    private fun JsonObject.requiredLong(name: String, minimum: Long, maximum: Long): Long {
        val number = (this[name] as? JsonPrimitive)?.longOrNull ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        if (number < minimum || number > maximum) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        return number
    }

    private fun JsonObject.requiredDouble(name: String, minimum: Double, maximum: Double): Double {
        val number = (this[name] as? JsonPrimitive)?.doubleOrNull ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)
        if (!number.isFinite() || number < minimum || number > maximum) fail(OfflinePackErrorCode.PAYLOAD_BOUNDS_EXCEEDED)
        return number
    }

    private fun JsonObject.requiredBoolean(name: String): Boolean =
        (this[name] as? JsonPrimitive)?.booleanOrNull ?: fail(OfflinePackErrorCode.MALFORMED_PAYLOAD)

    private fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)

    private fun fail(code: OfflinePackErrorCode): Nothing = throw OfflinePackException(code)

    private const val SHA256_BYTES = 32
    private const val SHA256_HEX_LENGTH = 64
    private const val UUID_LENGTH = 36
    private const val MAX_TOKEN_LENGTH = 100
    private const val MAX_SNAPSHOT_LENGTH = 512
    private const val MAX_PROPERTY_LENGTH = 100
    private const val MAX_STRING_LENGTH = 4_096
    private const val MAX_JSON_DEPTH = 16
    private const val MAX_OBJECT_PROPERTIES = 100
    private const val MAX_ARRAY_VALUES = 1_000
    private const val MAX_REASONS = 16
    private const val MAX_CONTRIBUTIONS = 16
    private const val MAX_ABS_SCORE = 1_000_000.0
    private const val MAX_BASE64_LENGTH = 699_052
}
