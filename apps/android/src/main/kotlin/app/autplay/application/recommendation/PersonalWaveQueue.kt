package app.autplay.application.recommendation

import androidx.room3.withWriteTransaction
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.OrdinaryQueueReplacementGuard
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.server.ServerFeatureHttpException
import app.autplay.application.server.ServerRecommendationResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncBindingInitializer
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.SessionRequiredException
import app.autplay.domain.LocalId
import java.io.IOException
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** One personal queue, whether the P11 order arrived just now or in an offline pack. */
internal class PersonalWaveQueue(private val database: AutPlayDatabase) {
    suspend fun start(
        binding: ClientEventBinding?,
        replacement: OrdinaryQueueReplacementGuard,
        online: Boolean,
        stillActive: () -> Boolean,
        fetch: suspend (ClientEventBinding, PersonalWaveSession) -> ServerRecommendationResult,
    ): LocalId? {
        val profile = binding?.serverProfileId?.value
        val previous = database.queueDao().activeSnapshotOnce()
        val previousMode = previous?.takeIf { it.serverProfileId == profile }?.listeningContext
        val wave = PersonalWaveSession.start(previousMode)
        val remote = if (online && binding != null) fetchAvailable(binding, wave, fetch) else null
        return BindingAuthorityWriteGate.serialized {
            check(stillActive()) { "WAVE_OWNER_CHANGED" }
            database.withWriteTransaction {
                val journalBinding = binding?.let {
                    val cursor = SyncBindingInitializer(database).ensure(it)
                    check(it.journalEpoch == null || it.journalEpoch.value == cursor.journalEpoch)
                    it.copy(journalEpoch = LocalId(cursor.journalEpoch))
                }
                val now = System.currentTimeMillis()
                val ranked = if (binding == null) null else remote?.let {
                    require(it.replay == "served" && it.items.size <= OfflineRecommendationPackCodec.MAX_ITEMS)
                    WaveOrder(LocalId(it.requestId), null, it.items.map { item ->
                        WaveItem(LocalId(item.recordingId), item.sourceRank, item.section, item.confirmedVariation, item.frequentWaveSkips)
                    })
                } ?: cachedOrder(binding, now)

                val recommended = if (ranked != null && binding != null) {
                    rankedEntries(binding, wave, ranked, online && remote != null, now)
                } else emptyList()
                val entries = recommended.ifEmpty {
                    val owner = profile ?: "legacy-unscoped"
                    val library = database.libraryDao()
                    val sampled = library.randomPlaybackCandidates(
                        owner, vaultAvailable = false, limit = 200,
                        onlyUnresolved = remote != null,
                    )
                    library.selectSongRefs(owner, sampled, now, wave = true,
                        confirmedVariationRecordings = ranked?.confirmedVariations.orEmpty(), lessFrequentRecordings = ranked?.lessFrequent.orEmpty()).map {
                        NewPlaybackQueueEntry(LocalId.random(), LocalId(it), "WAVE", "LOCAL_THEN_VAULT")
                    }
                }
                if (entries.isEmpty()) return@withWriteTransaction null
                check(stillActive()) { "WAVE_OWNER_CHANGED" }
                PlaybackPersistenceRepository(database).activateQueue(
                    wave.id, entries, "USER", wave.queueContext(journalBinding), profile, wave.mode, now,
                    ordinaryReplacementGuard = replacement,
                )
                wave.id
            }
        }
    }

    private suspend fun fetchAvailable(
        binding: ClientEventBinding,
        wave: PersonalWaveSession,
        fetch: suspend (ClientEventBinding, PersonalWaveSession) -> ServerRecommendationResult,
    ): ServerRecommendationResult? = try {
        withTimeoutOrNull(4_000) { fetch(binding, wave) }
    } catch (_: IOException) {
        null
    } catch (_: SessionRequiredException) {
        // Offline packs and local audio remain scoped to the current device binding.
        null
    } catch (error: ServerFeatureHttpException) {
        if (error.status == 429 || error.status in 500..599) null else throw error
    }

    private suspend fun cachedOrder(binding: ClientEventBinding, now: Long): WaveOrder? {
        val packs = database.recommendationPackDao().latest(binding.serverProfileId.value, binding.userId.value, 8)
        for (row in packs) {
            val pack = try {
                OfflineRecommendationPackCodec.decode(row, binding, now, OfflinePackExpiryPolicy.FreshOnly)
            } catch (_: OfflinePackException) {
                continue
            }
            val useSkipHints = currentSkipHints(pack.createdAtMs, now)
            return WaveOrder(LocalId(pack.recommendationRequestId), LocalId(pack.offlinePackId),
                pack.items.sortedBy { it.packPosition }.map {
                    WaveItem(LocalId(it.recordingId), it.sourceRank, it.section, LIKED_ORIGINAL_VERSION in it.reasonCodes, useSkipHints && "FREQUENT_WAVE_SKIPS" in it.reasonCodes)
                })
        }
        return null
    }

    private suspend fun rankedEntries(
        binding: ClientEventBinding, wave: PersonalWaveSession, order: WaveOrder, vaultAvailable: Boolean, now: Long,
    ): List<NewPlaybackQueueEntry> {
        require(order.items.all { it.rank in 1..100 && SECTION.matches(it.section) })
        require(order.items.distinctBy { it.recording }.size == order.items.size)
        require(order.items.distinctBy { it.rank }.size == order.items.size)
        val candidates = database.recommendationPackDao().localCandidates(
            binding.serverProfileId.value, binding.userId.value, order.items.map { it.recording.value }, 200,
        ).filter { (vaultAvailable || it.isLocallyAvailable) && !it.excludedFromTaste && it.preference != "DISLIKED" }
        val byRecording = candidates.groupBy { it.recordingId }
        val ranked = order.items.flatMap { item ->
            byRecording[item.recording.value].orEmpty().sortedByDescending { it.isLocallyAvailable }
                .map { it.localUserTrackRefId to item }
        }.toMap()
        val eligible = database.libraryDao().selectSongRefs(binding.serverProfileId.value, ranked.keys.toList(), now, wave = true,
            confirmedVariationRecordings = order.confirmedVariations, lessFrequentRecordings = order.lessFrequent)
        return buildList {
            for (ref in eligible) {
                val item = ranked.getValue(ref)
                val position = size + 1
                val attribution = buildJsonObject {
                    put("recommendation_request_id", order.request.value)
                    put("recording_id", item.recording.value)
                    put("source_rank", item.rank)
                    put("display_position", position)
                    put("section_key", item.section)
                    put("source", if (order.pack == null) "online" else "offline_pack")
                    put("surface", "personal_wave")
                    put("presentation_id", wave.id.value)
                    put("impression_event_local_id", LocalId.random().value)
                    order.pack?.let { put("offline_pack_id", it.value) }
                }
                add(NewPlaybackQueueEntry(LocalId.random(), LocalId(ref),
                    "RECOMMENDED", "LOCAL_THEN_VAULT", attribution.toString()))
            }
        }
    }

    private data class WaveItem(val recording: LocalId, val rank: Int, val section: String, val confirmedVariation: Boolean, val frequentWaveSkips: Boolean)
    private data class WaveOrder(val request: LocalId, val pack: LocalId?, val items: List<WaveItem>) {
        val lessFrequent = items.filter { it.frequentWaveSkips }.mapTo(mutableSetOf()) { it.recording.value }
        val confirmedVariations = items.filter { it.confirmedVariation }.mapTo(mutableSetOf()) { it.recording.value }
    }

    private companion object {
        val SECTION = Regex("[a-z][a-z0-9_]{0,99}")
    }
}
