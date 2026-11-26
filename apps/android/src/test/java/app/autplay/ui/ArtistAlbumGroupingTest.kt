package app.autplay.ui

import app.autplay.application.library.*
import org.junit.Assert.*
import org.junit.Test

class ArtistAlbumGroupingTest {
    @Test fun albumTilesKeepPreciseOrderAndShowOnlyTheYear() {
        fun release(id: String, date: String?) = CoreReleaseSummary(id, id, "Apashe", date)
        val rows = artistAlbums(emptyList(), emptyList(), listOf(release("March", "2022-03-25"),
            release("Unknown", null), release("December", "2022-12-01"), release("Renaissance", "2020")))
        assertEquals(listOf("December", "March", "Renaissance", "Unknown"), rows.map { it.title })
        assertEquals(listOf("2022", "2022", "2020", null), rows.map { releaseYear(it.date) })
    }

    @Test fun explicitGuestCreditBelongsOnlyToTheNamedGuest() {
        val credit = "Apashe feat. Panther & Odalisk"
        assertFalse(app.autplay.application.artist.hasFeaturedCredit("Apashe", credit))
        assertTrue(app.autplay.application.artist.hasFeaturedCredit("Panther", credit))
        assertTrue(app.autplay.application.artist.hasFeaturedCredit("Odalisk", null, "No Twerk (ft Panther x Odalisk)"))
        assertFalse(app.autplay.application.artist.hasFeaturedCredit("Panther", "Apashe, Panther"))
    }

    @Test fun oneAlbumContainsPlayableTracksFromBothPartialEditions() {
        val releases = listOf(
            CoreReleaseSummary("one", "BALLADS 1", "Joji", releaseGroup = "group"),
            CoreReleaseSummary("two", "Ballads 1", "Joji", releaseGroup = "group"),
            CoreReleaseSummary("other", "BALLADS 1", "Another artist"),
        )
        val tiles = artistAlbums(emptyList(), emptyList(), releases)
        assertEquals(2, tiles.size)
        val group = tiles.single { it.artist == "Joji" }.target!!.stableId
        assertEquals(ARTIST_ALBUM_PREFIX + "one|two", group)
        fun edition(id: String, positions: List<Int>) = CoreReleaseDetail(id, null, "BALLADS 1", "Joji", null,
            null, null, positions.map { pos -> CoreReleaseDetailTrack("$id-$pos", "rec-$pos", 1, pos,
                "$pos", "Song $pos", "Joji", null, "play-$pos") })
        val detail = combineArtistEditions(group, listOf(edition("one", listOf(2)), edition("two", listOf(1, 2))))!!
        assertEquals(listOf("play-1", "play-2"), detail.tracks.map { it.localUserTrackRefId })
    }
}
