package app.autplay.application.recommendation

import app.autplay.data.local.dao.WaveRecordingRow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WaveRecordingVersionsTest {
    private fun track(title: String, artist: String = "Artist", kind: String? = null, version: String? = null) =
        waveVersion(WaveRecordingRow("ref", title, artist, kind, version))

    @Test fun remixAndCreditedCoverReferToTheOriginalPerformerAndSong() {
        val original = track("Song")
        for (version in listOf(track("Song (DJ Remix)"), track("Song", kind = "REMIX"),
            track("Song – Remix"), track("Song [Artist Cover]", "Band"),
            track("Song", "Band", version = "Originally by Artist"))) {
            assertTrue(version.alternate)
            assertEquals(original.song, version.song)
        }
        assertEquals(track("Side A - Side B").song, track("Side A - Side B (Live)").song)
        assertFalse(track("Live and Let Die").alternate)
    }

    @Test fun anUncreditedCoverCannotUnlockThroughATitleOrItsOwnPerformer() {
        val cover = track("Song (Cover)", "Band")
        assertTrue(cover.alternate)
        assertNull(cover.song)
        assertTrue(track("Песня (кавер на Автор)", "Группа").song == track("Песня", "Автор").song)
        assertTrue(track("Song (Remix)", "Other").song != track("Song").song)
    }

    @Test fun confirmedCoverWithAmbiguousOriginalNeverFallsBackToItsDisplayCredit() {
        val row = WaveRecordingRow("ref", "Song (Artist Cover)", "Band", null, null,
            recordingMbid = "cover", workMbid = "work", performanceRole = "COVER")
        assertTrue(waveVersion(row).alternate)
        assertNull(waveVersion(row).song)
        assertNull(waveVersion(row.copy(originalRecordingMbid = "cover")).song)
    }

    @Test fun waveSpacesVariationsAndNeverFillsAnIncompleteSlot() {
        val originals = (1..38).map { WaveRecordingRow("o$it", "Song $it", "Artist", null, null) }
        val mixes = (1..4).map { WaveRecordingRow("m$it", "Different $it", "DJ", null, null,
            recordingMbid = "mix$it", performanceRole = "REMIX", originalRecordingMbid = "source$it") }
        val queue = selectSongs(mixes + originals, emptyList(), wave = true)
        assertEquals(40, queue.size)
        assertEquals(listOf(20, 40), queue.mapIndexedNotNull { i, id -> (i + 1).takeIf { id.startsWith("m") } })
        assertEquals(18, selectSongs(mixes + originals.take(18), emptyList(), wave = true).size)
        assertEquals(4, selectSongs(mixes, emptyList(), wave = false).size)
    }

    @Test fun retitledVersionsShareCooldownAndOneSlotRegardlessOfCredit() {
        val original = WaveRecordingRow("a", "Day", "Singer", null, null, recordingMbid = "recording")
        val mix = WaveRecordingRow("b", "Night", "DJ", null, null, recordingMbid = "mix",
            workMbid = "work", performanceRole = "REMIX", originalRecordingMbid = "recording")
        val live = WaveRecordingRow("c", "Another title", "Band", null, null, recordingMbid = "live",
            workMbid = "work", performanceRole = "VERSION")
        assertEquals(listOf("b"), selectSongs(listOf(mix, live, original), emptyList(), wave = false))
        assertEquals(listOf("a"), selectSongs(listOf(original, live, mix), emptyList(), wave = false))
        assertTrue(selectSongs(listOf(original, live), listOf(mix), wave = false).isEmpty())
    }
}
