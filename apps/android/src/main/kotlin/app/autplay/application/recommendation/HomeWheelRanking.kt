package app.autplay.application.recommendation

import app.autplay.application.statistics.metadataGenres
import app.autplay.data.local.AutPlayDatabase
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlin.math.ln

data class HomeWheelMix(val genre: String, val packId: String, val trackIds: Set<String>)

internal data class HomeWheelSuggestions(
    val weights: Map<String, Double> = emptyMap(),
    val mixes: Map<String, HomeWheelMix> = emptyMap(),
)

internal object HomeWheelRanking {
    private val genres = mapOf(
        "pop" to "pop", "поп" to "pop", "rock" to "rock", "рок" to "rock",
        "hip hop" to "hip_hop", "hiphop" to "hip_hop", "хип хоп" to "hip_hop",
        "electronic" to "electronic", "электроника" to "electronic",
        "jazz" to "jazz", "джаз" to "jazz", "classical" to "classical", "классика" to "classical",
    )
    private val cards = listOf("energy", "pop", "running", "rock", "focus", "electronic",
        "calm", "jazz", "evening", "hip_hop", "sleep", "classical")

    fun cardGenres(payload: String): Set<String> = metadataGenres(payload).mapNotNullTo(mutableSetOf()) { genre ->
        genres[genre.lowercase(Locale.ROOT).replace('-', ' ').replace('_', ' ')]
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

    fun ring(hour: Int, genreWeights: Map<String, Double> = emptyMap()): List<String> {
        val moments = when (hour) {
            in 6..9 -> listOf("energy", "running", "pop")
            in 10..16 -> listOf("focus", "electronic", "calm")
            in 17..20 -> listOf("evening", "rock", "calm")
            else -> listOf("sleep", "calm", "jazz")
        }
        val ranked = cards.sortedByDescending { id ->
            val place = moments.indexOf(id)
            (if (place >= 0) 3.0 - place else 0.0) + 2.0 * ln(1.0 + genreWeights.getOrDefault(id, 0.0))
        }
        // Starting at the middle card, alternate the next best cards to its right and left.
        return listOf(ranked.first()) + ranked.filterIndexed { index, _ -> index % 2 == 1 } +
            ranked.filterIndexed { index, _ -> index > 0 && index % 2 == 0 }.asReversed()
    }
}
