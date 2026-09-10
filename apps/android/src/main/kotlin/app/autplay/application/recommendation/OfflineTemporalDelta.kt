package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.dao.LocalTemporalJournalRow
import app.autplay.data.local.entity.RecommendationPackEntity
import app.autplay.data.local.entity.RecommendationTemporalDeltaEntity
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.Locale
import java.util.UUID
import kotlin.math.abs
import kotlin.math.max
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

enum class OfflineTemporalDeltaErrorCode {
    ENVELOPE_MISMATCH,
    HASH_INVALID,
    NON_CANONICAL_PAYLOAD,
    MALFORMED_PAYLOAD,
    OWNER_MISMATCH,
    DEVICE_MISMATCH,
    PARENT_MISMATCH,
    POLICY_MISMATCH,
    EVENT_INVALID,
    ADJUSTMENT_INVALID,
    EXPIRED,
}

class OfflineTemporalDeltaException(val code: OfflineTemporalDeltaErrorCode) : IllegalArgumentException(code.name)

data class OfflineTemporalAdjustment(
    val recordingId: String,
    val sourceRank: Int,
    val displayPosition: Int,
    val boundedDelta: Double,
    val impressionEventId: String,
    val impressionKeySha256: String,
)

data class DecodedOfflineTemporalDelta(
    val deltaId: String,
    val offlinePackId: String,
    val recommendationRequestId: String,
    val createdAtMs: Long,
    val expiresAtMs: Long,
    val adjustments: List<OfflineTemporalAdjustment>,
)

private data class ProjectedLocalEvidence(
    val sourceEventId: String,
    val recordingId: String,
    val signedStrength: Double,
    val originWeight: Double,
    val document: JsonObject,
)

/**
 * Accepted R1A current-episode control for Android. It is deliberately separate from Sona-Lite:
 * the projector can only move verified parent items and never changes server scores/source ranks.
 */
object OfflineTemporalDeltaCodec {
    const val PAYLOAD_VERSION = 1
    const val PAYLOAD_ENCODING = "RAW_JSON"
    const val POLICY_KEY = "adaptive-taste"
    const val POLICY_VERSION = "1"
    const val POLICY_SHA256 = "6fc6c3348475618696eec0ad171515d50e6f586ebf515f67f5add4ef245179db"
    const val MAX_LOCAL_EVENTS = 256
    const val MAX_SOURCE_ROWS = 257
    const val EPISODE_GAP_MS = 1_800_000L
    const val MAX_DELTA_LIFETIME_MS = 604_800_000L

    private val json = Json {
        isLenient = false
        ignoreUnknownKeys = false
        allowSpecialFloatingPointValues = false
    }
    private val sha256Hex = Regex("^[0-9a-f]{64}$")

    fun build(
        parentEntity: RecommendationPackEntity,
        parent: DecodedOfflinePack,
        binding: ClientEventBinding,
        sourceRows: List<LocalTemporalJournalRow>,
        mandatoryFilteredItems: List<HomeRecommendationItem>,
        nowMs: Long,
    ): RecommendationTemporalDeltaEntity? {
        require(nowMs >= 0)
        validateParentEnvelope(parentEntity, parent, binding)
        if (nowMs >= parent.expiresAtMs || mandatoryFilteredItems.isEmpty()) return null

        val episode = currentEpisode(sourceRows, binding, parent.createdAtMs, nowMs)
        val evidenceGroups = episode.map { normalize(it, binding) }
        val boundedGroups = mutableListOf<List<ProjectedLocalEvidence>>()
        var evidenceCount = 0
        for (group in evidenceGroups.asReversed()) {
            if (group.isEmpty()) continue
            if (evidenceCount + group.size > MAX_LOCAL_EVENTS) continue
            boundedGroups += group
            evidenceCount += group.size
        }
        val evidence = boundedGroups.asReversed().flatten()
        if (evidence.isEmpty()) return null

        val scoreByRecording = evidence
            .groupBy(ProjectedLocalEvidence::sourceEventId)
            .values
            .flatMap { values ->
                val denominator = max(1.0, values.sumOf { abs(it.signedStrength) })
                values.map { value ->
                    value.recordingId to (value.signedStrength / denominator * value.originWeight)
                }
            }
            .groupBy({ it.first }, { it.second })
            .mapValues { (_, values) -> values.sum().coerceIn(-TOTAL_ABSOLUTE_CAP, TOTAL_ABSOLUTE_CAP) }
        if (mandatoryFilteredItems.none { (scoreByRecording[it.recordingId] ?: 0.0) != 0.0 }) return null

        val ordered = mandatoryFilteredItems.sortedWith(
            compareBy<HomeRecommendationItem>(
                { it.packPosition - (scoreByRecording[it.recordingId] ?: 0.0) },
                HomeRecommendationItem::packPosition,
                HomeRecommendationItem::sourceRank,
                HomeRecommendationItem::recordingId,
            ),
        )
        val adjustments = ordered.mapIndexed { index, item ->
            val impressionKey = impressionKey(binding, parent.recommendationRequestId, item.recordingId, item.sourceRank)
            OfflineTemporalAdjustment(
                recordingId = item.recordingId,
                sourceRank = item.sourceRank,
                displayPosition = index + 1,
                boundedDelta = scoreByRecording[item.recordingId] ?: 0.0,
                impressionEventId = deterministicUuid("autplay-r1b-impression:$impressionKey"),
                impressionKeySha256 = impressionKey,
            )
        }
        val parentItems = parentItems(parent)
        val parentItemsHash = canonicalSha256(parentItems)
        val parentPackHash = parentEntity.payloadSha256.toHex().also {
            if (!sha256Hex.matches(it)) fail(OfflineTemporalDeltaErrorCode.PARENT_MISMATCH)
        }
        val deltaIdentity = buildJsonObject {
            put("device_id", binding.deviceId.value)
            put("feature_policy_sha256", POLICY_SHA256)
            put("offline_pack_id", parent.offlinePackId)
            put("owner_user_id", binding.userId.value)
            put("recommendation_request_id", parent.recommendationRequestId)
            put("server_profile_id", binding.serverProfileId.value)
            put(
                "source_event_ids",
                buildJsonArray {
                    evidence.map(ProjectedLocalEvidence::sourceEventId).distinct().forEach {
                        add(JsonPrimitive(it))
                    }
                },
            )
        }
        val deltaId = deterministicUuid("autplay-r1b-delta:${canonicalSha256(deltaIdentity)}")
        val expiresAtMs = minOf(parent.expiresAtMs, Math.addExact(nowMs, MAX_DELTA_LIFETIME_MS))
        if (expiresAtMs <= nowMs) return null

        val withoutSelfHash = buildJsonObject {
            put("schema_version", PAYLOAD_VERSION)
            put("delta_id", deltaId)
            put("server_profile_id", binding.serverProfileId.value)
            put("owner_user_id", binding.userId.value)
            put("device_id", binding.deviceId.value)
            put("offline_pack_id", parent.offlinePackId)
            put("parent_pack_sha256", parentPackHash)
            put("parent_items_sha256", parentItemsHash)
            put("parent_expires_at_ms", parent.expiresAtMs)
            put("parent_items", parentItems)
            put("recommendation_request_id", parent.recommendationRequestId)
            put(
                "feature_policy",
                buildJsonObject {
                    put("key", POLICY_KEY)
                    put("version", POLICY_VERSION)
                    put("content_sha256", POLICY_SHA256)
                },
            )
            put("cutoff_at_ms", parent.createdAtMs)
            put("local_events", JsonArray(evidence.map(ProjectedLocalEvidence::document)))
            put(
                "adjustments",
                buildJsonArray {
                    adjustments.forEach { adjustment ->
                        add(
                            buildJsonObject {
                                put("recording_id", adjustment.recordingId)
                                put("source_rank", adjustment.sourceRank)
                                put("display_position", adjustment.displayPosition)
                                put("bounded_delta", adjustment.boundedDelta)
                                put("mandatory_filter_passed", true)
                                put("impression_event_id", adjustment.impressionEventId)
                                put("impression_key_sha256", adjustment.impressionKeySha256)
                            },
                        )
                    }
                },
            )
            put("created_at_ms", nowMs)
            put("expires_at_ms", expiresAtMs)
        }
        val deltaHash = canonicalSha256(withoutSelfHash)
        val complete = JsonObject(withoutSelfHash + ("delta_sha256" to JsonPrimitive(deltaHash)))
        val payload = canonicalBytes(complete)
        return RecommendationTemporalDeltaEntity(
            deltaId = deltaId,
            serverProfileId = binding.serverProfileId.value,
            ownerUserId = binding.userId.value,
            deviceId = binding.deviceId.value,
            offlinePackId = parent.offlinePackId,
            recommendationRequestId = parent.recommendationRequestId,
            featurePolicyVersion = POLICY_VERSION,
            featurePolicySha256 = POLICY_SHA256.hexBytes(),
            parentPackSha256 = parentEntity.payloadSha256.copyOf(),
            parentItemsSha256 = parentItemsHash.hexBytes(),
            payloadVersion = PAYLOAD_VERSION,
            payloadEncoding = PAYLOAD_ENCODING,
            payload = payload,
            payloadSha256 = sha256(payload),
            cutoffAtMs = parent.createdAtMs,
            createdAtMs = nowMs,
            expiresAtMs = expiresAtMs,
        )
    }

    fun decode(
        entity: RecommendationTemporalDeltaEntity,
        parentEntity: RecommendationPackEntity,
        parent: DecodedOfflinePack,
        binding: ClientEventBinding,
        nowMs: Long,
    ): DecodedOfflineTemporalDelta {
        require(nowMs >= 0)
        validateParentEnvelope(parentEntity, parent, binding)
        if (entity.serverProfileId != binding.serverProfileId.value || entity.ownerUserId != binding.userId.value) {
            fail(OfflineTemporalDeltaErrorCode.OWNER_MISMATCH)
        }
        if (entity.deviceId != binding.deviceId.value) fail(OfflineTemporalDeltaErrorCode.DEVICE_MISMATCH)
        if (
            entity.payloadVersion != PAYLOAD_VERSION ||
            entity.payloadEncoding != PAYLOAD_ENCODING ||
            entity.featurePolicyVersion != POLICY_VERSION ||
            !MessageDigest.isEqual(entity.featurePolicySha256, POLICY_SHA256.hexBytes())
        ) fail(OfflineTemporalDeltaErrorCode.POLICY_MISMATCH)
        if (entity.payload.isEmpty() || entity.payloadSha256.size != SHA256_BYTES ||
            !MessageDigest.isEqual(sha256(entity.payload), entity.payloadSha256)
        ) fail(OfflineTemporalDeltaErrorCode.HASH_INVALID)
        if (!MessageDigest.isEqual(parentEntity.payloadSha256, entity.parentPackSha256)) {
            fail(OfflineTemporalDeltaErrorCode.PARENT_MISMATCH)
        }

        val payloadText = entity.payload.toString(StandardCharsets.UTF_8)
        val canonical = runCatching { JsonCanonicalizer(payloadText).encodedString }.getOrElse {
            fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
        }
        if (!canonical.toByteArray(StandardCharsets.UTF_8).contentEquals(entity.payload)) {
            fail(OfflineTemporalDeltaErrorCode.NON_CANONICAL_PAYLOAD)
        }
        val root = runCatching { json.parseToJsonElement(payloadText) as? JsonObject }.getOrNull()
            ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
        if (root.keys != DELTA_KEYS) fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
        val declaredHash = root.requiredHex("delta_sha256")
        if (canonicalSha256(JsonObject(root.filterKeys { it != "delta_sha256" })) != declaredHash) {
            fail(OfflineTemporalDeltaErrorCode.HASH_INVALID)
        }

        val deltaId = root.requiredUuid("delta_id")
        val packId = root.requiredUuid("offline_pack_id")
        val requestId = root.requiredUuid("recommendation_request_id")
        val createdAtMs = root.requiredLong("created_at_ms")
        val expiresAtMs = root.requiredLong("expires_at_ms")
        val parentExpiresAtMs = root.requiredLong("parent_expires_at_ms")
        if (
            root.requiredLong("schema_version") != PAYLOAD_VERSION.toLong() ||
            deltaId != entity.deltaId ||
            packId != entity.offlinePackId ||
            packId != parent.offlinePackId ||
            requestId != entity.recommendationRequestId ||
            requestId != parent.recommendationRequestId ||
            root.requiredUuid("server_profile_id") != binding.serverProfileId.value ||
            root.requiredUuid("owner_user_id") != binding.userId.value ||
            root.requiredUuid("device_id") != binding.deviceId.value ||
            root.requiredLong("cutoff_at_ms") != entity.cutoffAtMs ||
            createdAtMs != entity.createdAtMs ||
            expiresAtMs != entity.expiresAtMs ||
            parentExpiresAtMs != parent.expiresAtMs
        ) fail(OfflineTemporalDeltaErrorCode.ENVELOPE_MISMATCH)
        if (
            createdAtMs >= expiresAtMs ||
            expiresAtMs > parentExpiresAtMs ||
            expiresAtMs - createdAtMs > MAX_DELTA_LIFETIME_MS ||
            nowMs >= expiresAtMs ||
            nowMs >= parentExpiresAtMs
        ) fail(OfflineTemporalDeltaErrorCode.EXPIRED)

        val policy = root.requiredObject("feature_policy")
        if (
            policy.keys != POLICY_KEYS ||
            policy.requiredString("key") != POLICY_KEY ||
            policy.requiredString("version") != POLICY_VERSION ||
            policy.requiredHex("content_sha256") != POLICY_SHA256
        ) fail(OfflineTemporalDeltaErrorCode.POLICY_MISMATCH)
        val observedParentPackHash = root.requiredHex("parent_pack_sha256")
        if (observedParentPackHash != parentEntity.payloadSha256.toHex()) {
            fail(OfflineTemporalDeltaErrorCode.PARENT_MISMATCH)
        }
        val expectedParentItems = parentItems(parent)
        val parentItemsHash = canonicalSha256(expectedParentItems)
        if (
            root.requiredArray("parent_items") != expectedParentItems ||
            root.requiredHex("parent_items_sha256") != parentItemsHash ||
            !MessageDigest.isEqual(entity.parentItemsSha256, parentItemsHash.hexBytes())
        ) fail(OfflineTemporalDeltaErrorCode.PARENT_MISMATCH)

        validateEvents(root.requiredArray("local_events"), binding)
        val parentPairs = parent.items.map { it.recordingId to it.sourceRank }.toSet()
        val adjustments = root.requiredArray("adjustments").map { element ->
            val value = element as? JsonObject ?: fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
            if (value.keys != ADJUSTMENT_KEYS || value.requiredBoolean("mandatory_filter_passed") != true) {
                fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
            }
            val recordingId = value.requiredUuid("recording_id")
            val sourceRank = value.requiredLong("source_rank").toInt()
            val displayPosition = value.requiredLong("display_position").toInt()
            val boundedDelta = value.requiredDouble("bounded_delta")
            val impressionEventId = value.requiredUuid("impression_event_id")
            val impressionKey = value.requiredHex("impression_key_sha256")
            if (
                sourceRank !in 1..1_000 ||
                displayPosition !in 1..1_000 ||
                boundedDelta !in -1.0..1.0 ||
                recordingId to sourceRank !in parentPairs ||
                impressionKey != impressionKey(binding, requestId, recordingId, sourceRank) ||
                impressionEventId != deterministicUuid("autplay-r1b-impression:$impressionKey")
            ) fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
            OfflineTemporalAdjustment(
                recordingId,
                sourceRank,
                displayPosition,
                boundedDelta,
                impressionEventId,
                impressionKey,
            )
        }
        if (
            adjustments.size > OfflineRecommendationPackCodec.MAX_ITEMS ||
            adjustments.map { it.recordingId to it.sourceRank }.toSet().size != adjustments.size ||
            adjustments.map(OfflineTemporalAdjustment::displayPosition).toSet().size != adjustments.size ||
            adjustments.map(OfflineTemporalAdjustment::impressionEventId).toSet().size != adjustments.size ||
            adjustments.map(OfflineTemporalAdjustment::impressionKeySha256).toSet().size != adjustments.size
        ) fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
        return DecodedOfflineTemporalDelta(deltaId, packId, requestId, createdAtMs, expiresAtMs, adjustments)
    }

    fun apply(
        delta: DecodedOfflineTemporalDelta,
        mandatoryFilteredItems: List<HomeRecommendationItem>,
    ): List<HomeRecommendationItem> {
        val byPair = mandatoryFilteredItems.associateBy { it.recordingId to it.sourceRank }
        if (byPair.size != mandatoryFilteredItems.size || byPair.keys != delta.adjustments.map { it.recordingId to it.sourceRank }.toSet()) {
            fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
        }
        val positions = delta.adjustments.map(OfflineTemporalAdjustment::displayPosition).sorted()
        if (positions != (1..delta.adjustments.size).toList()) fail(OfflineTemporalDeltaErrorCode.ADJUSTMENT_INVALID)
        return delta.adjustments
            .sortedWith(compareBy(OfflineTemporalAdjustment::displayPosition, OfflineTemporalAdjustment::sourceRank))
            .map { adjustment ->
                byPair.getValue(adjustment.recordingId to adjustment.sourceRank).copy(
                    displayPosition = adjustment.displayPosition,
                    source = "local_temporal_delta",
                    deltaId = delta.deltaId,
                    impressionEventId = adjustment.impressionEventId,
                    impressionKeySha256 = adjustment.impressionKeySha256,
                )
            }
    }

    fun impressionKey(
        binding: ClientEventBinding,
        recommendationRequestId: String,
        recordingId: String,
        sourceRank: Int,
    ): String = canonicalSha256(
        buildJsonObject {
            put("owner_user_id", binding.userId.value)
            put("device_id", binding.deviceId.value)
            put("recommendation_request_id", recommendationRequestId)
            put("recording_id", recordingId)
            put("source_rank", sourceRank)
        },
    )

    private fun currentEpisode(
        rows: List<LocalTemporalJournalRow>,
        binding: ClientEventBinding,
        cutoffAtMs: Long,
        nowMs: Long,
    ): List<LocalTemporalJournalRow> {
        if (rows.size > MAX_SOURCE_ROWS) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        val byId = linkedMapOf<String, LocalTemporalJournalRow>()
        rows.forEach { row ->
            validateSourceRow(row, binding, cutoffAtMs, nowMs)
            val prior = byId.putIfAbsent(row.sourceEventId, row)
            if (prior != null && !prior.sameValue(row)) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        }
        val descending = byId.values.sortedWith(
            compareByDescending<LocalTemporalJournalRow>(LocalTemporalJournalRow::occurredAtMs)
                .thenByDescending(LocalTemporalJournalRow::deviceSequence)
                .thenByDescending(LocalTemporalJournalRow::sourceEventId),
        )
        val newest = descending.firstOrNull() ?: return emptyList()
        if (nowMs - newest.occurredAtMs > EPISODE_GAP_MS) return emptyList()
        val episodeDescending = mutableListOf<LocalTemporalJournalRow>()
        var newerAt = newest.occurredAtMs
        for (row in descending) {
            if (newerAt - row.occurredAtMs > EPISODE_GAP_MS) break
            episodeDescending += row
            newerAt = row.occurredAtMs
            if (episodeDescending.size == MAX_LOCAL_EVENTS) break
        }
        return episodeDescending.asReversed()
    }

    private fun normalize(row: LocalTemporalJournalRow, binding: ClientEventBinding): List<ProjectedLocalEvidence> {
        val recordingId = row.recordingId?.validUuid() ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        val payload = runCatching { json.parseToJsonElement(row.payloadJson) as? JsonObject }.getOrNull()
            ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        if (runCatching { JsonCanonicalizer(row.payloadJson).encodedString }.getOrNull() != row.payloadJson) {
            fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        }
        val specifications = when (row.sourceEventType) {
            "USER_TRACK_PREFERENCE_SET" -> preferenceSpecifications(payload)
            "LISTENING_EVENT_RECORDED" -> listeningSpecifications(payload)
            else -> emptyList()
        }
        return specifications.map { specification ->
            val causal = specification.causalAttribution
            val withoutHash = buildJsonObject {
                put("schema_version", 1)
                put("evidence_id", deterministicUuid("autplay-r1b-evidence:${row.sourceEventId}:${specification.signalKey}"))
                put("source_event_id", row.sourceEventId)
                put("owner_user_id", binding.userId.value)
                put("server_profile_id", binding.serverProfileId.value)
                put("device_id", binding.deviceId.value)
                put("device_sequence", row.deviceSequence)
                put("server_sequence", JsonNull)
                put("source_event_type", row.sourceEventType)
                put("signal_key", specification.signalKey)
                put("derivation_key", specification.derivationKey)
                put("recording_id", recordingId)
                put(
                    "dimensions",
                    buildJsonArray {
                        add(
                            buildJsonObject {
                                put("kind", "BOUNDED_METADATA_TOKEN")
                                put("key", "recording_id:$recordingId")
                            },
                        )
                    },
                )
                put("occurred_at_ms", row.occurredAtMs)
                put("received_at_ms", row.occurredAtMs)
                put("effective_at_ms", row.occurredAtMs)
                put("time_classification", "LOCAL_UNSYNCED_TIME")
                put("origin_lane", specification.originLane)
                put("signed_strength", specification.signedStrength)
                put("quality_weight", specification.qualityWeight)
                put("excluded_from_taste", specification.excludedFromTaste)
                put("source_request_sha256", row.sourceRequestSha256.toHex())
                if (causal != null) {
                    put("recommendation_request_id", causal.recommendationRequestId)
                    put("impression_event_id", causal.impressionEventId)
                    put("recommendation_source_rank", causal.sourceRank)
                }
            }
            val document = JsonObject(
                withoutHash + ("normalized_evidence_sha256" to JsonPrimitive(canonicalSha256(withoutHash))),
            )
            ProjectedLocalEvidence(
                sourceEventId = row.sourceEventId,
                recordingId = recordingId,
                signedStrength = specification.signedStrength,
                originWeight = specification.originWeight,
                document = document,
            )
        }
    }

    private fun preferenceSpecifications(payload: JsonObject): List<EvidenceSpecification> {
        val excluded = payload.boolean("excluded_from_taste")
        if (excluded) return listOf(EvidenceSpecification.exclusion())
        return when (payload.string("preference")) {
            "LIKED" -> listOf(EvidenceSpecification("EXPLICIT_LIKE", "PREFERENCE_TRANSITION_V1", "EXPLICIT", 1.0, 1.0, 1.0))
            "DISLIKED" -> listOf(EvidenceSpecification("EXPLICIT_DISLIKE", "PREFERENCE_TRANSITION_V1", "EXPLICIT", 0.0, 1.0, 1.0))
            "NEUTRAL" -> emptyList()
            else -> fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        }
    }

    private fun listeningSpecifications(payload: JsonObject): List<EvidenceSpecification> {
        val excluded = payload.boolean("excluded_from_taste")
        if (excluded) return listOf(EvidenceSpecification.exclusion())
        val origin = payload.string("event_origin")
        val originLane = when (origin) {
            "ORGANIC" -> "ORGANIC"
            "RECOMMENDED" -> "RECOMMENDATION"
            else -> "SOURCE_QUEUE"
        }
        val originWeight = when (originLane) {
            "ORGANIC" -> 0.8
            "RECOMMENDATION" -> 0.35
            else -> 0.6
        }
        val causal = if (originLane == "RECOMMENDATION") causalAttribution(payload) else null
        val values = mutableListOf<EvidenceSpecification>()
        when (originLane) {
            "ORGANIC" -> values += EvidenceSpecification("FINALIZED_ORGANIC_LISTEN", "BASE_LISTEN_V1", originLane, 0.4, 1.0, originWeight)
            "RECOMMENDATION" -> values += EvidenceSpecification("FINALIZED_RECOMMENDATION_LISTEN", "BASE_LISTEN_V1", originLane, 0.2, 1.0, originWeight, causalAttribution = causal)
        }
        val playedMs = payload.long("played_ms")
        if (playedMs < 0) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        val ratioElement = payload["completion_ratio"]
        val ratio = if (ratioElement == null || ratioElement is JsonNull) null else {
            (ratioElement as? JsonPrimitive)?.doubleOrNull?.also {
                if (!it.isFinite() || it !in 0.0..1.0) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
            } ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        }
        val outcome = when {
            (ratio != null && ratio <= 0.2) || playedMs < 30_000 -> -0.45
            ratio != null && ratio >= 0.8 -> 0.3
            else -> null
        }
        if (outcome != null) {
            values += EvidenceSpecification(
                signalKey = if (outcome < 0) "FINALIZED_SHORT_LISTEN_SKIP" else "FINALIZED_COMPLETION",
                derivationKey = "OUTCOME_CLASSIFIER_V1",
                originLane = originLane,
                signedStrength = outcome,
                qualityWeight = 1.0,
                originWeight = originWeight,
                causalAttribution = causal,
            )
        }
        return values
    }

    private fun causalAttribution(payload: JsonObject): CausalAttribution {
        val recommendation = payload["recommendation"] as? JsonObject
            ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        return CausalAttribution(
            recommendationRequestId = recommendation.string("recommendation_request_id").validUuid()
                ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID),
            impressionEventId = recommendation.string("impression_event_local_id").validUuid()
                ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID),
            sourceRank = recommendation.long("source_rank").toInt().also {
                if (it !in 1..1_000) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
            },
        )
    }

    private fun validateEvents(events: JsonArray, binding: ClientEventBinding) {
        if (events.size > MAX_LOCAL_EVENTS) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
        val evidenceIds = mutableSetOf<String>()
        events.forEach { element ->
            val event = element as? JsonObject ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
            val hash = event.requiredHex("normalized_evidence_sha256")
            if (canonicalSha256(JsonObject(event.filterKeys { it != "normalized_evidence_sha256" })) != hash) {
                fail(OfflineTemporalDeltaErrorCode.HASH_INVALID)
            }
            if (
                event.requiredUuid("owner_user_id") != binding.userId.value ||
                event.requiredUuid("server_profile_id") != binding.serverProfileId.value
            ) fail(OfflineTemporalDeltaErrorCode.OWNER_MISMATCH)
            if (event.requiredUuid("device_id") != binding.deviceId.value) {
                fail(OfflineTemporalDeltaErrorCode.DEVICE_MISMATCH)
            }
            if (event["server_sequence"] !is JsonNull || event.requiredString("time_classification") != "LOCAL_UNSYNCED_TIME") {
                fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
            }
            if (!evidenceIds.add(event.requiredUuid("evidence_id"))) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
            event.requiredUuid("source_event_id")
            event.requiredUuid("recording_id")
            event.requiredHex("source_request_sha256")
        }
    }

    private fun validateSourceRow(row: LocalTemporalJournalRow, binding: ClientEventBinding, cutoffAtMs: Long, nowMs: Long) {
        if (
            row.ownerUserId != binding.userId.value ||
            row.serverProfileId != binding.serverProfileId.value
        ) fail(OfflineTemporalDeltaErrorCode.OWNER_MISMATCH)
        if (row.deviceId != binding.deviceId.value) fail(OfflineTemporalDeltaErrorCode.DEVICE_MISMATCH)
        if (
            row.sourceEventId.validUuid() == null ||
            row.deviceSequence < 1 ||
            row.occurredAtMs <= cutoffAtMs ||
            row.occurredAtMs > nowMs ||
            row.sourceRequestSha256.size != SHA256_BYTES ||
            row.sourceEventType !in SOURCE_EVENT_TYPES
        ) fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)
    }

    private fun validateParentEnvelope(
        entity: RecommendationPackEntity,
        parent: DecodedOfflinePack,
        binding: ClientEventBinding,
    ) {
        if (
            entity.offlinePackId != parent.offlinePackId ||
            entity.serverProfileId != binding.serverProfileId.value ||
            entity.ownerUserId != binding.userId.value ||
            parent.userId != binding.userId.value
        ) fail(OfflineTemporalDeltaErrorCode.OWNER_MISMATCH)
        if (parent.deviceId != binding.deviceId.value) fail(OfflineTemporalDeltaErrorCode.DEVICE_MISMATCH)
        if (entity.payloadSha256.size != SHA256_BYTES || !MessageDigest.isEqual(sha256(entity.payload), entity.payloadSha256)) {
            fail(OfflineTemporalDeltaErrorCode.PARENT_MISMATCH)
        }
    }

    private fun parentItems(parent: DecodedOfflinePack): JsonArray = buildJsonArray {
        parent.items.sortedWith(compareBy(OfflinePackItem::packPosition, OfflinePackItem::sourceRank)).forEach { item ->
            add(
                buildJsonObject {
                    put("recording_id", item.recordingId)
                    put("source_rank", item.sourceRank)
                },
            )
        }
    }

    private fun canonicalBytes(value: JsonElement): ByteArray =
        JsonCanonicalizer(value.toString()).encodedUTF8

    private fun canonicalSha256(value: JsonElement): String = sha256(canonicalBytes(value)).toHex()

    private fun sha256(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)

    private fun deterministicUuid(material: String): String {
        val bytes = sha256(material.toByteArray(StandardCharsets.UTF_8)).copyOfRange(0, 16)
        bytes[6] = ((bytes[6].toInt() and 0x0f) or 0x80).toByte()
        bytes[8] = ((bytes[8].toInt() and 0x3f) or 0x80).toByte()
        val high = bytes.take(8).fold(0L) { acc, byte -> (acc shl 8) or (byte.toLong() and 0xff) }
        val low = bytes.drop(8).fold(0L) { acc, byte -> (acc shl 8) or (byte.toLong() and 0xff) }
        return UUID(high, low).toString()
    }

    private fun LocalTemporalJournalRow.sameValue(other: LocalTemporalJournalRow): Boolean =
        copy(sourceRequestSha256 = byteArrayOf()) == other.copy(sourceRequestSha256 = byteArrayOf()) &&
            sourceRequestSha256.contentEquals(other.sourceRequestSha256)

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun String.hexBytes(): ByteArray = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private fun String.validUuid(): String? = runCatching { UUID.fromString(this).toString() }
        .getOrNull()
        ?.takeIf { it == this && this == lowercase(Locale.ROOT) }

    private fun JsonObject.requiredObject(name: String): JsonObject =
        this[name] as? JsonObject ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredArray(name: String): JsonArray =
        this[name] as? JsonArray ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredString(name: String): String {
        val value = this[name] as? JsonPrimitive ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
        if (!value.isString) fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
        return value.content
    }

    private fun JsonObject.requiredUuid(name: String): String = requiredString(name).validUuid()
        ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredHex(name: String): String = requiredString(name).also {
        if (!sha256Hex.matches(it)) fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)
    }

    private fun JsonObject.requiredLong(name: String): Long =
        (this[name] as? JsonPrimitive)?.longOrNull ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredDouble(name: String): Double =
        (this[name] as? JsonPrimitive)?.doubleOrNull?.takeIf(Double::isFinite)
            ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.requiredBoolean(name: String): Boolean =
        (this[name] as? JsonPrimitive)?.booleanOrNull ?: fail(OfflineTemporalDeltaErrorCode.MALFORMED_PAYLOAD)

    private fun JsonObject.string(name: String): String =
        (this[name] as? JsonPrimitive)?.takeIf(JsonPrimitive::isString)?.content
            ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)

    private fun JsonObject.long(name: String): Long =
        (this[name] as? JsonPrimitive)?.longOrNull ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)

    private fun JsonObject.boolean(name: String): Boolean =
        (this[name] as? JsonPrimitive)?.booleanOrNull ?: fail(OfflineTemporalDeltaErrorCode.EVENT_INVALID)

    private fun fail(code: OfflineTemporalDeltaErrorCode): Nothing = throw OfflineTemporalDeltaException(code)

    private data class CausalAttribution(
        val recommendationRequestId: String,
        val impressionEventId: String,
        val sourceRank: Int,
    )

    private data class EvidenceSpecification(
        val signalKey: String,
        val derivationKey: String,
        val originLane: String,
        val signedStrength: Double,
        val qualityWeight: Double,
        val originWeight: Double,
        val excludedFromTaste: Boolean = false,
        val causalAttribution: CausalAttribution? = null,
    ) {
        companion object {
            fun exclusion() = EvidenceSpecification(
                "EXCLUDE_FROM_TASTE",
                "EXCLUSION_PROJECTION_V1",
                "EXCLUSION",
                0.0,
                0.0,
                0.0,
                excludedFromTaste = true,
            )
        }
    }

    private val SOURCE_EVENT_TYPES = setOf("USER_TRACK_PREFERENCE_SET", "LISTENING_EVENT_RECORDED")
    private val POLICY_KEYS = setOf("key", "version", "content_sha256")
    private val ADJUSTMENT_KEYS = setOf(
        "recording_id",
        "source_rank",
        "display_position",
        "bounded_delta",
        "mandatory_filter_passed",
        "impression_event_id",
        "impression_key_sha256",
    )
    private val DELTA_KEYS = setOf(
        "schema_version",
        "delta_id",
        "server_profile_id",
        "owner_user_id",
        "device_id",
        "offline_pack_id",
        "parent_pack_sha256",
        "parent_items_sha256",
        "parent_expires_at_ms",
        "parent_items",
        "recommendation_request_id",
        "feature_policy",
        "cutoff_at_ms",
        "local_events",
        "adjustments",
        "created_at_ms",
        "expires_at_ms",
        "delta_sha256",
    )
    private const val TOTAL_ABSOLUTE_CAP = 0.75
    private const val SHA256_BYTES = 32
}
