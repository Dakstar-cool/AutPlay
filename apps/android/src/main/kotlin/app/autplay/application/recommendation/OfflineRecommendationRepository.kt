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
import java.time.Instant
import java.util.Locale
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import app.autplay.data.security.BindingAuthorityWriteGate

fun interface RecommendationPackTransport {
    suspend fun fetch(binding: ClientEventBinding, request: RecommendationPackFetchRequest): DownloadedRecommendationPack
}

private fun ClientEventBinding.packOwner() = RecommendationPackOwner(serverProfileId.value, userId.value, deviceId.value)

object OfflineRecommendationPackCodec {
    const val MAX_ITEMS = BoundRecommendationPackCodec.MAX_ITEMS
    const val MAX_PAYLOAD_BYTES = BoundRecommendationPackCodec.MAX_PAYLOAD_BYTES
    const val PAYLOAD_VERSION = BoundRecommendationPackCodec.PAYLOAD_VERSION
    const val PAYLOAD_ENCODING = BoundRecommendationPackCodec.PAYLOAD_ENCODING

    fun entityFromDownload(download: DownloadedRecommendationPack, binding: ClientEventBinding): RecommendationPackEntity =
        BoundRecommendationPackCodec.entityFromDownload(download, binding.packOwner()).run {
            RecommendationPackEntity(offlinePackId, serverProfileId, ownerUserId, catalogSnapshot, modelBundleVersion,
                payloadVersion, payloadEncoding, payload, payloadSha256, createdAtMs, expiresAtMs)
        }

    fun decode(entity: RecommendationPackEntity, binding: ClientEventBinding, nowMs: Long,
        expiryPolicy: OfflinePackExpiryPolicy = OfflinePackExpiryPolicy.FreshOnly): DecodedOfflinePack = entity.run {
        val envelope = RecommendationPackEnvelope(offlinePackId, serverProfileId, ownerUserId, catalogSnapshot,
            modelBundleVersion, payloadVersion, payloadEncoding, payload, payloadSha256, createdAtMs, expiresAtMs)
        BoundRecommendationPackCodec.decode(envelope, binding.packOwner(), nowMs, expiryPolicy)
    }
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
    val deltaId: String? = null,
    val impressionEventId: String? = null,
    val impressionKeySha256: String? = null,
)

/** Deterministic on-device policy; it changes only display order and makes no server-ML claim. */
object LocalRecommendationReranker {
    /** Mandatory local eligibility in immutable parent order, without an adaptive claim. */
    fun baseOrder(
        pack: DecodedOfflinePack,
        evidence: List<LocalRecommendationEvidence>,
        limit: Int = pack.request.limit,
        maxArtistRepeats: Int = 2,
    ): List<HomeRecommendationItem> {
        require(limit in 1..OfflineRecommendationPackCodec.MAX_ITEMS)
        require(maxArtistRepeats in 1..10)
        val filtered = evidence
            .asSequence()
            .filter(LocalRecommendationEvidence::isLocallyAvailable)
            .filterNot(LocalRecommendationEvidence::excludedFromTaste)
            .filterNot { it.preference == "DISLIKED" }
            .distinctBy { it.item.recordingId }
            .sortedWith(compareBy({ it.item.packPosition }, { it.item.sourceRank }, { it.item.recordingId }))
            .toList()
        val artistCounts = mutableMapOf<String, Int>()
        return buildList {
            for (candidate in filtered) {
                val artistKey = candidate.artist.trim().lowercase(Locale.ROOT)
                if ((artistCounts[artistKey] ?: 0) >= maxArtistRepeats) continue
                artistCounts[artistKey] = (artistCounts[artistKey] ?: 0) + 1
                add(candidate.toHomeItem(pack, size + 1, "offline_pack"))
                if (size == limit) break
            }
        }
    }

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
            val displayPosition = index + 1
            candidate.toHomeItem(
                pack,
                displayPosition,
                if (displayPosition == candidate.item.packPosition) "offline_pack" else "local_rerank",
            )
        }
    }

    private fun LocalRecommendationEvidence.toHomeItem(
        pack: DecodedOfflinePack,
        displayPosition: Int,
        source: String,
    ) = HomeRecommendationItem(
        offlinePackId = item.offlinePackId,
        recommendationRequestId = pack.recommendationRequestId,
        recordingId = item.recordingId,
        localUserTrackRefId = localUserTrackRefId,
        title = title,
        artist = artist,
        sourceRank = item.sourceRank,
        packPosition = item.packPosition,
        displayPosition = displayPosition,
        sectionKey = item.section,
        surface = pack.request.surface,
        source = source,
        reasonCode = item.reasonCode,
    )

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
    val releaseArtists: Set<String> = emptySet(),
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
        isBindingActive: suspend () -> Boolean = { true },
    ): DecodedOfflinePack {
        val downloaded = transport.fetch(binding, request)
        val entity = OfflineRecommendationPackCodec.entityFromDownload(downloaded, binding)
        return BindingAuthorityWriteGate.serialized {
            check(isBindingActive()) { "RECOMMENDATION_PROFILE_NOT_ACTIVE" }
            storeVerifiedPack(entity, binding, nowMs)
        }
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
    suspend fun loadHomeFeed(binding: ClientEventBinding, nowMs: Long): HomeFeed = database.withWriteTransaction {
        val dao = database.recommendationPackDao()
        val stored = dao.latest(binding.serverProfileId.value, binding.userId.value, MAX_PACK_CANDIDATES)
        val fresh = stored.firstNotNullOfOrNull { entity -> decodeOrNull(entity, binding, nowMs, OfflinePackExpiryPolicy.FreshOnly) }
        val decoded = fresh ?: stored.firstNotNullOfOrNull { entity ->
            decodeOrNull(entity, binding, nowMs, OfflinePackExpiryPolicy.AllowStaleLocalOnly(HOME_STALE_FALLBACK_MS))
        }
        if (decoded == null || decoded.items.isEmpty()) {
            return@withWriteTransaction HomeFeed(
                binding.serverProfileId.value,
                binding.userId.value,
                binding.deviceId.value,
                null,
                null,
                false,
                "NO_USABLE_OFFLINE_PACK",
                emptyMap(),
                emptyList(),
            ).withWeeklyReleases(binding, nowMs)
        }

        val localRows = dao.localCandidates(
            binding.serverProfileId.value,
            binding.userId.value,
            decoded.items.map(OfflinePackItem::recordingId),
            OfflineRecommendationPackCodec.MAX_ITEMS * 2,
        )
        val orderedRows = localRows.groupBy { it.recordingId }
        val orderedRefs = decoded.items.sortedBy { it.packPosition }.flatMap { item ->
            orderedRows[item.recordingId].orEmpty().filter { it.isLocallyAvailable && !it.excludedFromTaste && it.preference != "DISLIKED" }
                .sortedBy { it.localUserTrackRefId }.map { it.localUserTrackRefId }
        }
        val confirmedVariations = decoded.items.filter { LIKED_ORIGINAL_VERSION in it.reasonCodes }
            .mapTo(mutableSetOf()) { it.recordingId }
        val eligibleRefs = database.libraryDao().selectSongRefs(binding.serverProfileId.value, orderedRefs, nowMs, wave = false,
            confirmedVariationRecordings = confirmedVariations, lessFrequentRecordings = decoded.items.filter { currentSkipHints(decoded.createdAtMs, nowMs) && "FREQUENT_HOME_SKIPS" in it.reasonCodes }
                .mapTo(mutableSetOf()) { it.recordingId }).toSet()
        val bestLocalByRecording = localRows.filter { it.localUserTrackRefId in eligibleRefs }
            .groupBy(LocalRecommendationCandidateRow::recordingId)
            .mapValues { (_, rows) -> rows.sortedWith(compareByDescending<LocalRecommendationCandidateRow> { it.isLocallyAvailable }.thenBy { it.localUserTrackRefId }).first() }
        val evidence = decoded.items.mapNotNull { item ->
            bestLocalByRecording[item.recordingId]?.let { row -> row.toEvidence(item) }
        }
        val baseItems = LocalRecommendationReranker.baseOrder(decoded, evidence)
        val parentEntity = stored.single { it.offlinePackId == decoded.offlinePackId }
        val ranked = if (decoded.isStale) {
            baseItems
        } else {
            loadOrCreateDelta(parentEntity, decoded, binding, baseItems, nowMs)
        }
        val sections = ranked.groupBy(HomeRecommendationItem::sectionKey)
        val status = when {
            ranked.isEmpty() -> "NO_LOCALLY_AVAILABLE_RECOMMENDATIONS"
            decoded.isStale -> "STALE_LOCAL_FALLBACK"
            else -> "FRESH_OFFLINE_PACK"
        }
        HomeFeed(
            ownerProfileId = binding.serverProfileId.value,
            ownerUserId = binding.userId.value,
            ownerDeviceId = binding.deviceId.value,
            packId = decoded.offlinePackId,
            // Pack identity is a stable UUID for this durable presentation across recreation.
            presentationId = decoded.offlinePackId,
            isStaleFallback = decoded.isStale,
            statusCode = status,
            recommendationSections = sections,
            recentRelevantReleases = emptyList(),
        ).withWeeklyReleases(binding, nowMs)
    }

    private suspend fun HomeFeed.withWeeklyReleases(binding: ClientEventBinding, nowMs: Long): HomeFeed {
        val profile = binding.serverProfileId.value
        val cursor = database.syncDao().cursor(profile) ?: return this
        if (cursor.deviceId != binding.deviceId.value ||
            database.journalDao().lineageById(cursor.journalLineageId)?.userId != binding.userId.value) return this
        val dao = database.recommendationPackDao()
        val artists = recommendedReleaseArtists(recommendationSections.values.flatten(),
            dao.releaseArtistActivity(profile, nowMs - 30L * 86_400_000, nowMs))
        if (artists.isEmpty()) return this
        val today = java.time.Instant.ofEpochMilli(nowMs).atZone(java.time.ZoneId.systemDefault()).toLocalDate()
        val releases = dao.recentRelevantReleases(profile, binding.userId.value, today.minusDays(6).toString(), today.toString())
            .filter { isWeeklyArtistRelease(it.artist, it.releaseDateText, artists, today) }
        return copy(recentRelevantReleases = releases, releaseArtists = artists)
    }

    /** Immediate local unbinding boundary. Raw journal/library/media rows remain sealed in place. */
    suspend fun purgeLocalRecommendationContext(profileId: String) {
        require(UUID.fromString(profileId).toString() == profileId)
        database.withWriteTransaction {
            val dao = database.recommendationPackDao()
            dao.deletePresentationsForProfile(profileId)
            dao.deleteDeltasForProfile(profileId)
            dao.deletePacksForProfile(profileId)
        }
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
                if (item.deltaId != null) {
                    val rawPack = dao.pack(item.offlinePackId, binding.serverProfileId.value, binding.userId.value)
                        ?: error("RECOMMENDATION_PACK_NOT_FOUND")
                    val decoded = OfflineRecommendationPackCodec.decode(
                        rawPack,
                        binding,
                        nowMs,
                        OfflinePackExpiryPolicy.AllowStaleLocalOnly(HOME_STALE_FALLBACK_MS),
                    )
                    validateTemporalPresentation(dao, rawPack, decoded, binding, item, nowMs)
                }
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
            validateTemporalPresentation(dao, rawPack, decoded, binding, item, nowMs)

            val lineage = resolveLineage(binding, nowMs)
            val sequence = database.journalDao().allocateSequence(lineage.lineageId)
            val eventId = item.impressionEventId?.let(::LocalId) ?: eventIdFactory()
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

    /** Called inside the queue checkpoint transaction, once the player actually plays the entry. */
    internal suspend fun recordWavePlaybackImpression(
        binding: ClientEventBinding, attribution: JsonObject, nowMs: Long,
    ) {
        fun member(key: String) = attribution.getValue(key).jsonPrimitive.content
        val eventId = LocalId(member("impression_event_local_id"))
        val mapping = RecommendationPresentationEntity(
            binding.serverProfileId.value, binding.userId.value, member("presentation_id"),
            member("recommendation_request_id"), member("source_rank").toInt(), eventId.value,
            member("recording_id"), attribution["offline_pack_id"]?.jsonPrimitive?.content,
            member("source"), member("surface"), member("section_key"), member("display_position").toInt(), nowMs,
        )
        val dao = database.recommendationPackDao()
        val existing = dao.presentation(mapping.serverProfileId, mapping.ownerUserId, mapping.presentationId,
            mapping.recommendationRequestId, mapping.sourceRank)
        if (existing != null) {
            check(existing.copy(createdAtMs = nowMs) == mapping && database.journalDao().event(eventId.value) != null)
            return
        }
        val lineage = resolveLineage(binding, nowMs)
        val sequence = database.journalDao().allocateSequence(lineage.lineageId)
        val payload = P07PayloadCodec.canonicalize(buildJsonObject {
            put("interaction_type", "RECOMMENDATION_IMPRESSION_RECORDED")
            put("recommendation", JsonObject(attribution - "impression_event_local_id"))
        }.toString())
        dao.insertPresentation(mapping)
        database.journalDao().insert(journal(lineage, binding, eventId, sequence, payload, nowMs))
    }

    /** Starts only the tapped, already presented item; no impression or continuation is invented. */
    suspend fun activatePresentedPlayback(
        binding: ClientEventBinding,
        presentationId: LocalId,
        item: HomeRecommendationItem,
        impressionEventId: LocalId,
        nowMs: Long,
        isBindingActive: suspend () -> Boolean,
    ): LocalId = BindingAuthorityWriteGate.serialized {
        check(isBindingActive()) { "RECOMMENDATION_PROFILE_NOT_ACTIVE" }
        require(nowMs >= 0)
        validatePresentationItem(item)
        database.withWriteTransaction {
            val dao = database.recommendationPackDao()
            val mapping = checkNotNull(dao.presentation(
                binding.serverProfileId.value, binding.userId.value, presentationId.value,
                item.recommendationRequestId, item.sourceRank,
            )) { "RECOMMENDATION_IMPRESSION_REQUIRED" }
            checkMappingMatches(mapping, item)
            check(mapping.impressionEventId == impressionEventId.value) { "RECOMMENDATION_IMPRESSION_MISMATCH" }
            val journal = checkNotNull(database.journalDao().event(impressionEventId.value)) {
                "IMPRESSION_MAPPING_WITHOUT_JOURNAL"
            }
            check(
                journal.userId == binding.userId.value && journal.deviceId == binding.deviceId.value &&
                    journal.serverProfileId == binding.serverProfileId.value &&
                    journal.eventType == "RECOMMENDATION_IMPRESSION_RECORDED" &&
                    journal.occurredAtMs <= nowMs,
            ) { "RECOMMENDATION_IMPRESSION_MISMATCH" }
            val cursor = checkNotNull(database.syncDao().cursor(binding.serverProfileId.value)) {
                "SERVER_PROFILE_LINEAGE_MISMATCH"
            }
            check(
                cursor.journalLineageId == journal.journalLineageId &&
                    cursor.deviceId == binding.deviceId.value &&
                    (binding.journalEpoch == null || cursor.journalEpoch == binding.journalEpoch.value),
            ) { "SERVER_PROFILE_LINEAGE_MISMATCH" }
            val payload = Json.parseToJsonElement(journal.payloadJson) as? JsonObject
            val shown = checkNotNull(payload?.get("recommendation") as? JsonObject) {
                "RECOMMENDATION_IMPRESSION_MISMATCH"
            }
            check(
                shown["presentation_id"] == JsonPrimitive(presentationId.value) &&
                    shown["recommendation_request_id"] == JsonPrimitive(item.recommendationRequestId) &&
                    shown["recording_id"] == JsonPrimitive(item.recordingId) &&
                    shown["source_rank"] == JsonPrimitive(item.sourceRank) &&
                    shown["offline_pack_id"] == JsonPrimitive(item.offlinePackId) &&
                    shown["source"] == JsonPrimitive(mapping.source) &&
                    shown["display_position"] == JsonPrimitive(mapping.displayPosition),
            ) { "RECOMMENDATION_IMPRESSION_MISMATCH" }
            // A delta may expire while its parent presentation remains visible. Preserve the
            // first impression's exact source, display position and delta identity, even then.
            val frozenAttribution = P07PayloadCodec.canonicalize(buildJsonObject {
                shown.forEach { (key, value) -> put(key, value) }
                put("impression_event_local_id", impressionEventId.value)
                put("impression_event_server_id", JsonNull)
            }.toString())
            val eligible = dao.localCandidates(
                binding.serverProfileId.value, binding.userId.value, listOf(item.recordingId),
                OfflineRecommendationPackCodec.MAX_ITEMS * 2,
            ).any { row ->
                row.localUserTrackRefId == item.localUserTrackRefId && row.recordingId == item.recordingId &&
                    row.isLocallyAvailable && !row.excludedFromTaste && row.preference != "DISLIKED"
            }
            check(eligible) { "RECOMMENDATION_ITEM_NO_LONGER_ELIGIBLE" }
            val previous = database.queueDao().activeSnapshotOnce()
            check(isBindingActive()) { "RECOMMENDATION_PROFILE_NOT_ACTIVE" }
            val snapshotId = LocalId.random()
            app.autplay.application.playback.PlaybackPersistenceRepository(database).activateQueue(
                snapshotId = snapshotId,
                entries = listOf(app.autplay.application.playback.NewPlaybackQueueEntry(
                    queueEntryId = LocalId.random(),
                    trackRefId = LocalId(item.localUserTrackRefId),
                    sourceOrigin = "RECOMMENDED",
                    sourceAudioPolicy = "LOCAL_THEN_VAULT",
                    recommendationAttributionJson = frozenAttribution,
                )),
                queueType = "USER",
                sourceContextId = presentationId.value,
                serverProfileId = binding.serverProfileId.value,
                listeningContext = "GENERAL",
                nowMs = nowMs,
                ordinaryReplacementGuard = app.autplay.application.playback.OrdinaryQueueReplacementGuard(
                    previous?.queueSnapshotId, previous?.queueType, binding.serverProfileId.value,
                ),
            )
            snapshotId
        }
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
            item.deltaId?.let { put("offline_temporal_delta_id", it) }
            item.impressionKeySha256?.let { put("impression_key_sha256", it) }
        }.toString(),
    )

    private suspend fun loadOrCreateDelta(
        parentEntity: RecommendationPackEntity,
        parent: DecodedOfflinePack,
        binding: ClientEventBinding,
        baseItems: List<HomeRecommendationItem>,
        nowMs: Long,
    ): List<HomeRecommendationItem> {
        val dao = database.recommendationPackDao()
        dao.deleteExpiredDeltas(nowMs)
        val stored = dao.delta(
            binding.serverProfileId.value,
            binding.userId.value,
            binding.deviceId.value,
            parent.offlinePackId,
            parent.recommendationRequestId,
        )
        if (stored != null) {
            return try {
                OfflineTemporalDeltaCodec.apply(
                    OfflineTemporalDeltaCodec.decode(stored, parentEntity, parent, binding, nowMs),
                    baseItems,
                )
            } catch (_: OfflineTemporalDeltaException) {
                baseItems
            }
        }
        val rows = dao.localTemporalEvents(
            binding.serverProfileId.value,
            binding.userId.value,
            binding.deviceId.value,
            parent.createdAtMs,
            nowMs,
            OfflineTemporalDeltaCodec.MAX_SOURCE_ROWS,
        )
        val created = try {
            OfflineTemporalDeltaCodec.build(parentEntity, parent, binding, rows, baseItems, nowMs)
        } catch (_: OfflineTemporalDeltaException) {
            null
        } ?: return baseItems
        dao.insertDelta(created)
        return OfflineTemporalDeltaCodec.apply(
            OfflineTemporalDeltaCodec.decode(created, parentEntity, parent, binding, nowMs),
            baseItems,
        )
    }

    private suspend fun validateTemporalPresentation(
        dao: app.autplay.data.local.dao.RecommendationPackDao,
        parentEntity: RecommendationPackEntity,
        parent: DecodedOfflinePack,
        binding: ClientEventBinding,
        item: HomeRecommendationItem,
        nowMs: Long,
    ) {
        val deltaFields = listOf(item.deltaId, item.impressionEventId, item.impressionKeySha256)
        if (deltaFields.all { it == null }) {
            check(item.source == "offline_pack") { "RECOMMENDATION_DELTA_MISSING" }
            return
        }
        check(deltaFields.all { it != null } && item.source == "local_temporal_delta") {
            "RECOMMENDATION_DELTA_INCOMPLETE"
        }
        val stored = dao.delta(
            binding.serverProfileId.value,
            binding.userId.value,
            binding.deviceId.value,
            parent.offlinePackId,
            parent.recommendationRequestId,
        ) ?: error("RECOMMENDATION_DELTA_NOT_FOUND")
        check(stored.deltaId == item.deltaId) { "RECOMMENDATION_DELTA_MISMATCH" }
        val decoded = OfflineTemporalDeltaCodec.decode(stored, parentEntity, parent, binding, nowMs)
        val adjustment = decoded.adjustments.singleOrNull {
            it.recordingId == item.recordingId && it.sourceRank == item.sourceRank
        } ?: error("RECOMMENDATION_DELTA_ITEM_NOT_FOUND")
        check(
            adjustment.displayPosition == item.displayPosition &&
                adjustment.impressionEventId == item.impressionEventId &&
                adjustment.impressionKeySha256 == item.impressionKeySha256,
        ) { "RECOMMENDATION_DELTA_ITEM_MISMATCH" }
        val eligible = dao.localCandidates(
            binding.serverProfileId.value,
            binding.userId.value,
            listOf(item.recordingId),
            2,
        ).singleOrNull { row ->
            row.recordingId == item.recordingId &&
                row.isLocallyAvailable &&
                !row.excludedFromTaste &&
                row.preference != "DISLIKED"
        }
        check(eligible != null) { "RECOMMENDATION_ITEM_NO_LONGER_ELIGIBLE" }
    }

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
                        item.deltaId?.let { put("offline_temporal_delta_id", it) }
                        item.impressionKeySha256?.let { put("impression_key_sha256", it) }
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
        listOfNotNull(item.deltaId, item.impressionEventId).forEach { raw ->
            require(UUID.fromString(raw).toString() == raw) { "RECOMMENDATION_DELTA_ID_INVALID" }
        }
        item.impressionKeySha256?.let { require(Regex("^[0-9a-f]{64}$").matches(it)) }
        require(item.sourceRank in 1..1_000 && item.displayPosition in 1..1_000 && item.packPosition in 1..100)
        val token = Regex("^[a-z][a-z0-9_]{0,99}$")
        require(token.matches(item.source) && token.matches(item.surface) && token.matches(item.sectionKey))
    }

    private fun checkMappingMatches(mapping: RecommendationPresentationEntity, item: HomeRecommendationItem) {
        check(
                mapping.recordingId == item.recordingId &&
                mapping.offlinePackId == item.offlinePackId &&
                mapping.surface == item.surface &&
                mapping.sectionKey == item.sectionKey &&
                (item.impressionEventId == null || mapping.impressionEventId == item.impressionEventId),
        ) { "IMPRESSION_PRESENTATION_IDENTITY_MISMATCH" }
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
        const val HOME_STALE_FALLBACK_MS = 24L * 60 * 60 * 1000
    }
}
