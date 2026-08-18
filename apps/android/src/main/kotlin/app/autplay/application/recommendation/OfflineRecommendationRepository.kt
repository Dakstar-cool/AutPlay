package app.autplay.application.recommendation

import androidx.room3.withWriteTransaction
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.P04ClientEventHashInput
import app.autplay.application.sync.P04ClientEventHasher
import app.autplay.application.sync.P07PayloadCodec
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.dao.LocalRecommendationCandidateRow
import app.autplay.data.local.dao.RecentRelevantReleaseRow
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.RecommendationPresentationEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.domain.LocalId
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import java.util.Locale
import java.util.UUID
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

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

fun interface RecommendationPackTransport {
    suspend fun fetch(binding: ClientEventBinding, request: RecommendationPackFetchRequest): DownloadedRecommendationPack
}

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
object OfflineRecommendationPackCodec {
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

    /** Converts the bounded HTTP response into the raw Room envelope before strict full decode. */
    fun entityFromDownload(download: DownloadedRecommendationPack, binding: ClientEventBinding): RecommendationPackEntity {
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
        return RecommendationPackEntity(
            offlinePackId = packId,
            serverProfileId = binding.serverProfileId.value,
            ownerUserId = binding.userId.value,
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
        entity: RecommendationPackEntity,
        binding: ClientEventBinding,
        nowMs: Long,
        expiryPolicy: OfflinePackExpiryPolicy = OfflinePackExpiryPolicy.FreshOnly,
    ): DecodedOfflinePack {
        require(nowMs >= 0)
        if (entity.serverProfileId != binding.serverProfileId.value || entity.ownerUserId != binding.userId.value) {
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
        if (userId != binding.userId.value) fail(OfflinePackErrorCode.OWNER_MISMATCH)
        if (deviceId != binding.deviceId.value) fail(OfflinePackErrorCode.DEVICE_MISMATCH)

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

data class LocalRecommendationEvidence(
    val item: OfflinePackItem,
    val localUserTrackRefId: String,
    val title: String,
    val artist: String,
    val isLocallyAvailable: Boolean,
    val preference: String,
    val excludedFromTaste: Boolean,
    val preferenceUpdatedAtMs: Long,
    val latestListenedAtMs: Long?,
    val latestSkipAtMs: Long?,
)

data class HomeRecommendationItem(
    val offlinePackId: String,
    val recommendationRequestId: String,
    val recordingId: String,
    val localUserTrackRefId: String,
    val title: String,
    val artist: String,
    val sourceRank: Int,
    val packPosition: Int,
    val displayPosition: Int,
    val sectionKey: String,
    val surface: String,
    val source: String,
    val reasonCode: String,
)

/** Deterministic on-device policy; it changes only display order and makes no server-ML claim. */
object LocalRecommendationReranker {
    fun rerank(
        pack: DecodedOfflinePack,
        evidence: List<LocalRecommendationEvidence>,
        nowMs: Long,
        limit: Int = pack.request.limit,
        maxArtistRepeats: Int = 2,
    ): List<HomeRecommendationItem> {
        require(nowMs >= 0)
        require(limit in 1..OfflineRecommendationPackCodec.MAX_ITEMS)
        require(maxArtistRepeats in 1..10)

        val filtered = evidence
            .asSequence()
            .filter(LocalRecommendationEvidence::isLocallyAvailable)
            .filterNot(LocalRecommendationEvidence::excludedFromTaste)
            .filterNot { it.preference == "DISLIKED" }
            .distinctBy { it.item.recordingId }
            .sortedWith(
                compareBy<LocalRecommendationEvidence>(
                    { signalBucket(it, nowMs) },
                    { it.item.packPosition },
                    { it.item.sourceRank },
                    { it.item.recordingId },
                ),
            )
            .toList()

        val artistCounts = mutableMapOf<String, Int>()
        val selected = mutableListOf<LocalRecommendationEvidence>()
        for (candidate in filtered) {
            val artistKey = candidate.artist.trim().lowercase(Locale.ROOT)
            if ((artistCounts[artistKey] ?: 0) >= maxArtistRepeats) continue
            artistCounts[artistKey] = (artistCounts[artistKey] ?: 0) + 1
            selected += candidate
            if (selected.size == limit) break
        }

        return selected.mapIndexed { index, candidate ->
            val item = candidate.item
            val displayPosition = index + 1
            HomeRecommendationItem(
                offlinePackId = item.offlinePackId,
                recommendationRequestId = pack.recommendationRequestId,
                recordingId = item.recordingId,
                localUserTrackRefId = candidate.localUserTrackRefId,
                title = candidate.title,
                artist = candidate.artist,
                sourceRank = item.sourceRank,
                packPosition = item.packPosition,
                displayPosition = displayPosition,
                sectionKey = item.section,
                surface = pack.request.surface,
                source = if (displayPosition == item.packPosition) "offline_pack" else "local_rerank",
                reasonCode = item.reasonCode,
            )
        }
    }

    private fun signalBucket(evidence: LocalRecommendationEvidence, nowMs: Long): Int {
        val freshLike = evidence.preference == "LIKED" && evidence.preferenceUpdatedAtMs.isFresh(nowMs, LIKE_FRESHNESS_MS)
        if (freshLike) return 0
        val freshSkip = evidence.latestSkipAtMs?.isFresh(nowMs, SKIP_FRESHNESS_MS) == true
        val recentListen = evidence.latestListenedAtMs?.isFresh(nowMs, REPEAT_FRESHNESS_MS) == true
        return when {
            freshSkip -> 3
            recentListen -> 2
            else -> 1
        }
    }

    private fun Long.isFresh(nowMs: Long, windowMs: Long): Boolean = this <= nowMs && nowMs - this <= windowMs

    private const val LIKE_FRESHNESS_MS = 7L * 24 * 60 * 60 * 1000
    private const val SKIP_FRESHNESS_MS = 24L * 60 * 60 * 1000
    private const val REPEAT_FRESHNESS_MS = 7L * 24 * 60 * 60 * 1000
}

data class HomeFeed(
    val ownerProfileId: String,
    val ownerUserId: String,
    val ownerDeviceId: String,
    val packId: String?,
    val presentationId: String?,
    val isStaleFallback: Boolean,
    val statusCode: String,
    val recommendationSections: Map<String, List<HomeRecommendationItem>>,
    val recentRelevantReleases: List<RecentRelevantReleaseRow>,
)

fun interface PresentationFailureInjector {
    suspend fun afterMappingWrite()
}

data class RecommendationPresentationResult(
    val impressionEventId: LocalId,
    val deviceSequence: Long,
    val duplicate: Boolean,
)

class OfflineRecommendationRepository(
    private val database: AutPlayDatabase,
    private val eventIdFactory: () -> LocalId = { LocalId.random() },
    private val failureInjector: PresentationFailureInjector = PresentationFailureInjector {},
    private val syncScheduler: DeferredWorkScheduler? = null,
) {
    suspend fun refreshPack(
        binding: ClientEventBinding,
        transport: RecommendationPackTransport,
        nowMs: Long,
        request: RecommendationPackFetchRequest = RecommendationPackFetchRequest(),
    ): DecodedOfflinePack {
        val downloaded = transport.fetch(binding, request)
        val entity = OfflineRecommendationPackCodec.entityFromDownload(downloaded, binding)
        return storeVerifiedPack(entity, binding, nowMs)
    }

    suspend fun storeVerifiedPack(entity: RecommendationPackEntity, binding: ClientEventBinding, nowMs: Long): DecodedOfflinePack {
        val decoded = OfflineRecommendationPackCodec.decode(entity, binding, nowMs)
        database.withWriteTransaction {
            // The cursor/lineage is the durable one-owner binding for all pre-v9 profile-scoped
            // Library and History rows. A profile cannot be silently reused by another user.
            resolveLineage(binding, nowMs)
            database.recommendationPackDao().upsert(entity)
        }
        return decoded
    }

    /**
     * Loads only owner/profile/device-bound packs and locally available tracks. A stale pack is
     * considered only after every fresh pack failed and only inside the explicit 24-hour fallback.
     */
    suspend fun loadHomeFeed(binding: ClientEventBinding, nowMs: Long): HomeFeed {
        val dao = database.recommendationPackDao()
        val stored = dao.latest(binding.serverProfileId.value, binding.userId.value, MAX_PACK_CANDIDATES)
        val fresh = stored.firstNotNullOfOrNull { entity -> decodeOrNull(entity, binding, nowMs, OfflinePackExpiryPolicy.FreshOnly) }
        val decoded = fresh ?: stored.firstNotNullOfOrNull { entity ->
            decodeOrNull(entity, binding, nowMs, OfflinePackExpiryPolicy.AllowStaleLocalOnly(HOME_STALE_FALLBACK_MS))
        }
        val releases = dao.recentRelevantReleases(
            binding.serverProfileId.value,
            binding.userId.value,
            MAX_RECENT_RELEASES,
        )
        if (decoded == null || decoded.items.isEmpty()) {
            return HomeFeed(
                binding.serverProfileId.value,
                binding.userId.value,
                binding.deviceId.value,
                null,
                null,
                false,
                "NO_USABLE_OFFLINE_PACK",
                emptyMap(),
                releases,
            )
        }

        val localRows = dao.localCandidates(
            binding.serverProfileId.value,
            binding.userId.value,
            decoded.items.map(OfflinePackItem::recordingId),
            OfflineRecommendationPackCodec.MAX_ITEMS * 2,
        )
        val bestLocalByRecording = localRows
            .groupBy(LocalRecommendationCandidateRow::recordingId)
            .mapValues { (_, rows) -> rows.sortedWith(compareByDescending<LocalRecommendationCandidateRow> { it.isLocallyAvailable }.thenBy { it.localUserTrackRefId }).first() }
        val evidence = decoded.items.mapNotNull { item ->
            bestLocalByRecording[item.recordingId]?.let { row -> row.toEvidence(item) }
        }
        val ranked = LocalRecommendationReranker.rerank(decoded, evidence, nowMs)
        val sections = ranked.groupBy(HomeRecommendationItem::sectionKey)
        val status = when {
            ranked.isEmpty() -> "NO_LOCALLY_AVAILABLE_RECOMMENDATIONS"
            decoded.isStale -> "STALE_LOCAL_FALLBACK"
            else -> "FRESH_OFFLINE_PACK"
        }
        return HomeFeed(
            ownerProfileId = binding.serverProfileId.value,
            ownerUserId = binding.userId.value,
            ownerDeviceId = binding.deviceId.value,
            packId = decoded.offlinePackId,
            // Pack identity is a stable UUID for this durable presentation across recreation.
            presentationId = decoded.offlinePackId,
            isStaleFallback = decoded.isStale,
            statusCode = status,
            recommendationSections = sections,
            recentRelevantReleases = releases,
        )
    }

    /** Persists the semantic mapping first and the existing P04 Offline Journal event atomically. */
    suspend fun recordPresentation(
        binding: ClientEventBinding,
        presentationId: LocalId,
        item: HomeRecommendationItem,
        nowMs: Long,
    ): RecommendationPresentationResult {
        require(nowMs >= 0)
        validatePresentationItem(item)
        val result = database.withWriteTransaction {
            val dao = database.recommendationPackDao()
            val existing = dao.presentation(
                binding.serverProfileId.value,
                binding.userId.value,
                presentationId.value,
                item.recommendationRequestId,
                item.sourceRank,
            )
            if (existing != null) {
                checkMappingMatches(existing, item)
                val journal = checkNotNull(database.journalDao().event(existing.impressionEventId)) {
                    "IMPRESSION_MAPPING_WITHOUT_JOURNAL"
                }
                return@withWriteTransaction RecommendationPresentationResult(
                    LocalId(existing.impressionEventId),
                    journal.deviceSequence,
                    true,
                )
            }

            val rawPack = dao.pack(item.offlinePackId, binding.serverProfileId.value, binding.userId.value)
                ?: error("RECOMMENDATION_PACK_NOT_FOUND")
            val decoded = OfflineRecommendationPackCodec.decode(
                rawPack,
                binding,
                nowMs,
                OfflinePackExpiryPolicy.AllowStaleLocalOnly(HOME_STALE_FALLBACK_MS),
            )
            val original = decoded.items.singleOrNull {
                it.recordingId == item.recordingId && it.sourceRank == item.sourceRank
            } ?: error("RECOMMENDATION_ITEM_NOT_FOUND")
            check(decoded.recommendationRequestId == item.recommendationRequestId) { "RECOMMENDATION_REQUEST_MISMATCH" }
            check(original.packPosition == item.packPosition && original.section == item.sectionKey) {
                "RECOMMENDATION_ITEM_MISMATCH"
            }

            val lineage = resolveLineage(binding, nowMs)
            val sequence = database.journalDao().allocateSequence(lineage.lineageId)
            val eventId = eventIdFactory()
            val payload = impressionPayload(presentationId, item)
            val mapping = RecommendationPresentationEntity(
                serverProfileId = binding.serverProfileId.value,
                ownerUserId = binding.userId.value,
                presentationId = presentationId.value,
                recommendationRequestId = item.recommendationRequestId,
                sourceRank = item.sourceRank,
                impressionEventId = eventId.value,
                recordingId = item.recordingId,
                offlinePackId = item.offlinePackId,
                source = item.source,
                surface = item.surface,
                sectionKey = item.sectionKey,
                displayPosition = item.displayPosition,
                createdAtMs = nowMs,
            )
            dao.insertPresentation(mapping)
            failureInjector.afterMappingWrite()
            database.journalDao().insert(journal(lineage, binding, eventId, sequence, payload, nowMs))
            RecommendationPresentationResult(eventId, sequence, false)
        }
        if (!result.duplicate) {
            syncScheduler?.enqueue(
                DeferredWorkRequest(
                    DeferredWorkKind.SYNC,
                    DeferredWorkSubject.Device(binding.deviceId),
                    binding.serverProfileId,
                ),
            )
        }
        return result
    }

    fun attributionJson(
        presentationId: LocalId,
        impressionEventId: LocalId,
        item: HomeRecommendationItem,
    ): String = P07PayloadCodec.canonicalize(
        buildJsonObject {
            put("display_position", item.displayPosition)
            put("impression_event_local_id", impressionEventId.value)
            put("impression_event_server_id", JsonNull)
            put("offline_pack_id", item.offlinePackId)
            put("presentation_id", presentationId.value)
            put("recommendation_request_id", item.recommendationRequestId)
            put("recording_id", item.recordingId)
            put("section_key", item.sectionKey)
            put("source", item.source)
            put("source_rank", item.sourceRank)
            put("surface", item.surface)
        }.toString(),
    )

    private fun decodeOrNull(
        entity: RecommendationPackEntity,
        binding: ClientEventBinding,
        nowMs: Long,
        policy: OfflinePackExpiryPolicy,
    ): DecodedOfflinePack? = try {
        OfflineRecommendationPackCodec.decode(entity, binding, nowMs, policy)
    } catch (_: OfflinePackException) {
        null
    }

    private suspend fun resolveLineage(binding: ClientEventBinding, nowMs: Long): JournalLineageEntity {
        val journal = database.journalDao()
        val existing = journal.lineageByDeviceId(binding.deviceId.value)
        if (existing != null) {
            check(existing.userId == binding.userId.value) { "JOURNAL_LINEAGE_USER_MISMATCH" }
            ensureCursor(binding, existing, nowMs)
            return existing
        }
        val epoch = binding.journalEpoch ?: LocalId.random()
        check(journal.lineageByJournalEpoch(epoch.value) == null) { "JOURNAL_EPOCH_DEVICE_MISMATCH" }
        return JournalLineageEntity(
            lineageId = LocalId.random().value,
            userId = binding.userId.value,
            deviceId = binding.deviceId.value,
            journalEpoch = epoch.value,
            nextDeviceSequence = 1,
            createdAtMs = nowMs,
        ).also {
            journal.insertLineage(it)
            ensureCursor(binding, it, nowMs)
        }
    }

    private suspend fun ensureCursor(binding: ClientEventBinding, lineage: JournalLineageEntity, nowMs: Long) {
        val existing = database.syncDao().cursor(binding.serverProfileId.value)
        check(
            existing == null ||
                (existing.journalLineageId == lineage.lineageId &&
                    existing.deviceId == lineage.deviceId &&
                    existing.journalEpoch == lineage.journalEpoch),
        ) { "SERVER_PROFILE_LINEAGE_MISMATCH" }
        if (existing == null) {
            database.syncDao().upsertCursor(
                SyncCursorEntity(
                    serverProfileId = binding.serverProfileId.value,
                    journalLineageId = lineage.lineageId,
                    deviceId = lineage.deviceId,
                    journalEpoch = lineage.journalEpoch,
                    opaqueCursor = null,
                    lastPulledServerSequence = 0,
                    lastAckedDeviceSequence = 0,
                    bootstrapSnapshotId = null,
                    bootstrapState = "NOT_STARTED",
                    lastSyncAtMs = null,
                    updatedAtMs = nowMs,
                ),
            )
        }
    }

    private fun impressionPayload(presentationId: LocalId, item: HomeRecommendationItem): String =
        P07PayloadCodec.canonicalize(
            buildJsonObject {
                put("interaction_type", "RECOMMENDATION_IMPRESSION_RECORDED")
                put(
                    "recommendation",
                    buildJsonObject {
                        put("display_position", item.displayPosition)
                        put("offline_pack_id", item.offlinePackId)
                        put("presentation_id", presentationId.value)
                        put("recommendation_request_id", item.recommendationRequestId)
                        put("recording_id", item.recordingId)
                        put("section_key", item.sectionKey)
                        put("source", item.source)
                        put("source_rank", item.sourceRank)
                        put("surface", item.surface)
                    },
                )
            }.toString(),
        )

    private fun journal(
        lineage: JournalLineageEntity,
        binding: ClientEventBinding,
        eventId: LocalId,
        sequence: Long,
        payload: String,
        nowMs: Long,
    ): OfflineJournalEventEntity {
        val idempotencyKey = "interaction-${eventId.value}"
        val hash = P04ClientEventHasher.sha256(
            P04ClientEventHashInput(
                eventId = eventId,
                idempotencyKey = idempotencyKey,
                binding = binding,
                deviceSequence = sequence,
                eventType = "RECOMMENDATION_IMPRESSION_RECORDED",
                aggregateType = "USER_INTERACTION_EVENT",
                aggregateLocalId = eventId,
                aggregateServerId = null,
                baseServerRowVersion = null,
                occurredAt = Instant.ofEpochMilli(nowMs).toString(),
                payloadJson = payload,
            ),
        )
        return OfflineJournalEventEntity(
            eventId = eventId.value,
            journalLineageId = lineage.lineageId,
            idempotencyKey = idempotencyKey,
            userId = binding.userId.value,
            deviceId = binding.deviceId.value,
            serverProfileId = binding.serverProfileId.value,
            deviceSequence = sequence,
            eventType = "RECOMMENDATION_IMPRESSION_RECORDED",
            schemaVersion = 1,
            aggregateType = "USER_INTERACTION_EVENT",
            aggregateLocalId = eventId.value,
            aggregateServerId = null,
            baseServerRowVersion = null,
            payloadJson = payload,
            requestHash = hash,
            occurredAtMs = nowMs,
            state = "PENDING",
            attemptCount = 0,
            nextAttemptAtMs = null,
            leaseToken = null,
            leaseExpiresAtMs = null,
            lastErrorCode = null,
            ackedAtMs = null,
        )
    }

    private fun validatePresentationItem(item: HomeRecommendationItem) {
        listOf(item.offlinePackId, item.recommendationRequestId, item.recordingId).forEach { raw ->
            require(UUID.fromString(raw).toString() == raw) { "RECOMMENDATION_ID_INVALID" }
        }
        require(item.sourceRank in 1..1_000 && item.displayPosition in 1..1_000 && item.packPosition in 1..100)
        val token = Regex("^[a-z][a-z0-9_]{0,99}$")
        require(token.matches(item.source) && token.matches(item.surface) && token.matches(item.sectionKey))
    }

    private fun checkMappingMatches(mapping: RecommendationPresentationEntity, item: HomeRecommendationItem) {
        check(
            mapping.recordingId == item.recordingId &&
                mapping.offlinePackId == item.offlinePackId &&
                mapping.source == item.source &&
                mapping.surface == item.surface &&
                mapping.sectionKey == item.sectionKey &&
                mapping.displayPosition == item.displayPosition,
        ) { "IMPRESSION_PRESENTATION_MISMATCH" }
    }

    private fun LocalRecommendationCandidateRow.toEvidence(item: OfflinePackItem) = LocalRecommendationEvidence(
        item = item,
        localUserTrackRefId = localUserTrackRefId,
        title = title,
        artist = artist,
        isLocallyAvailable = isLocallyAvailable,
        preference = preference,
        excludedFromTaste = excludedFromTaste,
        preferenceUpdatedAtMs = preferenceUpdatedAtMs,
        latestListenedAtMs = latestListenedAtMs,
        latestSkipAtMs = latestSkipAtMs,
    )

    private companion object {
        const val MAX_PACK_CANDIDATES = 5
        const val MAX_RECENT_RELEASES = 12
        const val HOME_STALE_FALLBACK_MS = 24L * 60 * 60 * 1000
    }
}
