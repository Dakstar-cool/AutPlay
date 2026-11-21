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
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** One personal queue, whether the P11 order arrived just now or in an offline pack. */
internal class PersonalWaveQueue(private val database: AutPlayDatabase) {
    private class Preparation(val owner: ClientEventBinding, val wave: PersonalWaveSession) {
        val created = android.os.SystemClock.elapsedRealtime()
        val result = CompletableDeferred<ServerRecommendationResult?>()
    }

    private var pending: Preparation? = null

    private suspend fun newSession(profile: String?): PersonalWaveSession {
        val previous = database.queueDao().activeSnapshotOnce()
        return PersonalWaveSession.start(previous?.takeIf { it.serverProfileId == profile }?.listeningContext)
    }

    /** Owned by the visible Home screen. Only start() may activate a queue or record impressions. */
    suspend fun prepare(
        binding: ClientEventBinding,
        fetch: suspend (ClientEventBinding, PersonalWaveSession) -> ServerRecommendationResult,
        warmTracks: suspend (List<LocalId>) -> Unit,
    ) {
        val slot = Preparation(binding, newSession(binding.serverProfileId.value))
        synchronized(this) { pending = slot }
        try {
            val result = fetchAvailable(binding, slot.wave, fetch) ?: return
            val order = remoteOrder(result)
            slot.result.complete(result)
            if (order.items.isNotEmpty()) {
                // A fresh installation can receive its server ranking before the first library sync.
                val tracks = database.syncDao().observeCursor(binding.serverProfileId.value).map { cursor ->
                    if (synchronized(this) { pending !== slot }) return@map emptyList()
                    if (cursor?.deviceId != binding.deviceId.value ||
                        (binding.journalEpoch != null && cursor.journalEpoch != binding.journalEpoch.value)) return@map null
                    rankedEntries(binding, slot.wave, order, true, System.currentTimeMillis())
                        .take(3).map { it.trackRefId }.takeIf { it.isNotEmpty() }
                }.first { it != null }.orEmpty()
                if (tracks.isNotEmpty()) warmTracks(tracks)
            }
            // Keep the selection available while this screen is visible. A later tap checks its age.
            kotlinx.coroutines.awaitCancellation()
        } finally {
            synchronized(this) { if (pending === slot) pending = null }
            slot.result.complete(null)
        }
    }

    suspend fun start(
        binding: ClientEventBinding?,
        replacement: OrdinaryQueueReplacementGuard,
        online: Boolean,
        stillActive: () -> Boolean,
        mix: HomeWheelMix? = null,
        fetch: suspend (ClientEventBinding, PersonalWaveSession) -> ServerRecommendationResult,
    ): LocalId? {
        val profile = binding?.serverProfileId?.value
        if (mix != null && binding == null) return null
        val current = if (mix == null) newSession(profile) else PersonalWaveSession.start("GENERAL")
        val slot = if (mix != null) null else synchronized(this) {
            pending.also { pending = null }?.takeIf {
                online && it.owner == binding && it.wave.mode == current.mode &&
                    it.wave.date == current.date && it.wave.hour == current.hour &&
                    android.os.SystemClock.elapsedRealtime() - it.created < 120_000
            }
        }
        val wave = slot?.wave ?: current
        val remote = when {
            mix != null -> null
            slot != null -> slot.result.await()
            online && binding != null -> fetchAvailable(binding, wave, fetch)
            else -> null
        }
        return BindingAuthorityWriteGate.serialized {
            check(stillActive()) { "WAVE_OWNER_CHANGED" }
            database.withWriteTransaction {
                val journalBinding = binding?.let {
                    val cursor = SyncBindingInitializer(database).ensure(it)
                    check(it.journalEpoch == null || it.journalEpoch.value == cursor.journalEpoch)
                    it.copy(journalEpoch = LocalId(cursor.journalEpoch))
                }
                val now = System.currentTimeMillis()
                val ranked = if (binding == null) null else remote?.let(::remoteOrder) ?: cachedOrder(binding, now)

                if (mix != null && ranked?.pack?.value != mix.packId) return@withWriteTransaction null
                val recommended = if (ranked != null && binding != null) {
                    rankedEntries(binding, wave, ranked, online && remote != null, now, mix)
                } else emptyList()
                if (mix != null && recommended.size < 2) return@withWriteTransaction null
                val entries = recommended.ifEmpty {
                    val owner = profile ?: "legacy-unscoped"
                    val library = database.libraryDao()
                    val sampled = library.playbackCandidates(
                        owner, vaultAvailable = false, limit = 200,
                        onlyUnresolved = remote != null, randomOrder = remote != null,
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

    private fun remoteOrder(result: ServerRecommendationResult): WaveOrder {
        require(result.replay == "served" && result.items.size <= OfflineRecommendationPackCodec.MAX_ITEMS)
        return WaveOrder(LocalId(result.requestId), null, result.items.map { item ->
            WaveItem(LocalId(item.recordingId), item.sourceRank, item.section, item.confirmedVariation, item.frequentWaveSkips)
        })
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
        mix: HomeWheelMix? = null,
    ): List<NewPlaybackQueueEntry> {
        require(order.items.all { it.rank in 1..100 && SECTION.matches(it.section) })
        require(order.items.distinctBy { it.recording }.size == order.items.size)
        require(order.items.distinctBy { it.rank }.size == order.items.size)
        val candidates = database.recommendationPackDao().localCandidates(
            binding.serverProfileId.value, binding.userId.value, order.items.map { it.recording.value }, 200,
        ).filter { (vaultAvailable || it.isLocallyAvailable) && !it.excludedFromTaste && it.preference != "DISLIKED" }
        val genreRefs = mix?.let {
            val refs = candidates.map { row -> row.localUserTrackRefId }.filter(it.trackIds::contains)
            database.trackMetadataDao().forTracks(binding.serverProfileId.value, refs)
                .filter { row -> it.genre in HomeWheelRanking.cardGenres(row.payloadJson) }
                .mapTo(hashSetOf()) { row -> row.localUserTrackRefId }
        }
        val byRecording = candidates.filter { genreRefs == null || it.localUserTrackRefId in genreRefs }
            .groupBy { it.recordingId }
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
