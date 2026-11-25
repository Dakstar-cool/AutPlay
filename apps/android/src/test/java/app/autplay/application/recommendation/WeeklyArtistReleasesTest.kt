package app.autplay.application.recommendation

import java.time.LocalDate
import org.junit.Assert.*
import org.junit.Test

class WeeklyArtistReleasesTest {
    @Test fun selectsTheFirstTenDistinctRecommendedArtistsAndSevenCompleteDates() {
        val rows = (1..13).reversed().map { i ->
            HomeRecommendationItem("pack", "request", "$i", "$i", "Track", if (i == 2) "Artist 1" else "Artist $i",
                i, i, i, "FOR_YOU", "HOME", "offline_pack", "TEST")
        }
        val activity = (1..13).flatMap { i -> (1..3).map {
            app.autplay.data.local.dao.ReleaseArtistActivity("Artist $i", "$i-$it", -1)
        } }
        assertTrue(recommendedReleaseArtists(rows, activity.take(2)).isEmpty())
        val listens = (1L..3L).map { day ->
            app.autplay.data.local.dao.ReleaseArtistActivity("Artist 1", null, day)
        }
        assertTrue(recommendedReleaseArtists(rows, List(5) { listens.first() }).isEmpty())
        assertEquals(1, recommendedReleaseArtists(rows, listens).size)
        val artists = recommendedReleaseArtists(rows, activity)
        assertEquals(10, artists.size)
        val today = LocalDate.parse("2026-11-25")
        fun matches(artist: String, date: String?) = isWeeklyArtistRelease(artist, date, artists, today)
        assertTrue(matches("Artist 11", "2026-11-19"))
        assertTrue(matches("Guest feat. ARTIST 1", "2026-11-25"))
        assertFalse(matches("Artist 12", "2026-11-25"))
        for (date in listOf("2026-11-18", "2026-11-26", "2026-11", "2026-11-31", null)) {
            assertFalse(matches("Artist 1", date))
        }
    }
}
