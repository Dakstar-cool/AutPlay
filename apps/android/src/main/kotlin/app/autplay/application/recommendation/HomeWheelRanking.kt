package app.autplay.application.recommendation

import app.autplay.application.statistics.metadataGenres
import app.autplay.data.local.AutPlayDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

data class HomeWheelMix(val genre: String, val packId: String, val trackIds: Set<String>)

internal data class HomeWheelSuggestions(
    val weights: Map<String, Double> = emptyMap(),
    val mixes: Map<String, HomeWheelMix> = emptyMap(),
)

internal object HomeWheelRanking {
    fun cardGenres(payload: String): Set<String> = metadataGenres(payload).mapNotNullTo(mutableSetOf()) { genre ->
        WaveWheel.genre(genre)
    }

    suspend fun suggestions(database: AutPlayDatabase, owner: String, feed: HomeFeed?): HomeWheelSuggestions =
        withContext(Dispatchers.IO) {
            val preferences = database.libraryDao().preferencesForProfile(owner, 100).first()
            val excluded = preferences.filter { it.excludedFromTaste || it.preference == "DISLIKED" }
                .mapTo(hashSetOf()) { it.localUserTrackRefId }
            val liked = preferences.filter { !it.excludedFromTaste && it.preference == "LIKED" }
                .mapTo(hashSetOf()) { it.localUserTrackRefId }
            val ranks = feed?.takeIf { it.ownerProfileId == owner }?.recommendationSections.orEmpty()
                .values.flatten().associate { it.localUserTrackRefId to it.sourceRank }
            val refs = (liked + ranks.keys) - excluded
            if (refs.isEmpty()) return@withContext HomeWheelSuggestions()
            val genreTracks = mutableMapOf<String, MutableSet<String>>()
            val scores = mutableMapOf<String, Double>()
            for (row in database.trackMetadataDao().forTracks(owner, refs.toList())) {
                val id = row.localUserTrackRefId
                val weight = (if (id in liked) 2.0 else 0.0) + (ranks[id]?.let { 1.5 / it.coerceAtLeast(1) } ?: 0.0)
                for (genre in cardGenres(row.payloadJson)) {
                    scores[genre] = scores.getOrDefault(genre, 0.0) + weight
                    if (id in ranks) genreTracks.getOrPut(genre) { mutableSetOf() }.add(id)
                }
            }
            val packId = feed?.takeUnless { it.isStaleFallback }?.packId
            val mixes = if (packId == null) emptyMap() else genreTracks.filterValues { it.size >= 2 }
                .mapValues { (genre, refs) -> HomeWheelMix(genre, packId, refs) }
            HomeWheelSuggestions(scores, mixes)
        }

    fun ring(hour: Int, genreWeights: Map<String, Double> = emptyMap()): List<String> = WaveWheel.ring(hour, genreWeights)
}
