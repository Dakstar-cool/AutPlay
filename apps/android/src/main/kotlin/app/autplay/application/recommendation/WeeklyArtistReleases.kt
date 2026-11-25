package app.autplay.application.recommendation

import app.autplay.application.artist.artistSearchKey
import app.autplay.application.artist.displayArtists
import java.time.LocalDate
import java.time.format.DateTimeParseException

internal fun recommendedReleaseArtists(items: List<HomeRecommendationItem>,
                                      activity: List<app.autplay.data.local.dao.ReleaseArtistActivity>): Set<String> {
    val liked = mutableMapOf<String, MutableSet<String>>()
    val days = mutableMapOf<String, MutableSet<Long>>()
    for (signal in activity) for (name in displayArtists(signal.artist)) {
        val artist = artistSearchKey(name)
        signal.likedTrack?.let { liked.getOrPut(artist) { mutableSetOf() }.add(it) }
        if (signal.day >= 0) days.getOrPut(artist) { mutableSetOf() }.add(signal.day)
    }
    return items.sortedBy { it.displayPosition }.asSequence().flatMap { displayArtists(it.artist) }
        .map(::artistSearchKey).distinct()
        .filter { liked[it].orEmpty().size >= 3 || days[it].orEmpty().size >= 3 }.take(10).toSet()
}

internal fun isWeeklyArtistRelease(credit: String?, date: String?, artists: Set<String>, today: LocalDate): Boolean {
    if (date == null || artists.isEmpty()) return false
    val released = try { LocalDate.parse(date) } catch (_: DateTimeParseException) { return false }
    return released in today.minusDays(6)..today && displayArtists(credit.orEmpty()).any { artistSearchKey(it) in artists }
}
