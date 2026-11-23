package app.autplay.application.recommendation

import java.util.Locale
import kotlin.math.ln

object WaveWheel {
    private val genres = mapOf(
        "pop" to "pop", "поп" to "pop", "rock" to "rock", "рок" to "rock",
        "hip hop" to "hip_hop", "hiphop" to "hip_hop", "хип хоп" to "hip_hop",
        "electronic" to "electronic", "электроника" to "electronic",
        "jazz" to "jazz", "джаз" to "jazz", "classical" to "classical", "классика" to "classical",
    )
    private val cards = listOf("energy", "pop", "running", "rock", "focus", "electronic",
        "calm", "jazz", "evening", "hip_hop", "sleep", "classical")

    fun genre(value: String): String? = genres[value.trim().lowercase(Locale.ROOT).replace('-', ' ').replace('_', ' ')]

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
