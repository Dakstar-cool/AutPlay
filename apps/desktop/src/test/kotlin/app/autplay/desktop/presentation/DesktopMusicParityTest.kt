package app.autplay.desktop.presentation

import app.autplay.application.desktop.*
import app.autplay.application.library.SharedPlaylist
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class DesktopMusicParityTest {
    private val track = DesktopTrack("local-a", "Север", "Маяк", 123_000, "a".repeat(64), 90)
    private val second = track.copy(id = "local-b", title = "Ветер")
    private val library = DesktopLibrarySnapshot(tracks = listOf(track, second))
    private val playlist = DesktopPlaylist("abc", "Вечер", listOf(
        DesktopPlaylistEntry("first", track.id), DesktopPlaylistEntry("second", second.id),
        DesktopPlaylistEntry("again", track.id)), "Порядок важен")

    @Test fun androidDocumentRoundTripRetainsRepeatedOccurrencesAndPrivateLabelsOnly() {
        val encoded = portablePlaylist(playlist, library)
        val decoded = SharedPlaylist.decode(encoded)
        assertEquals(listOf(0, 1, 0), decoded.entries)
        assertEquals(playlist.description, decoded.description)
        assertEquals(listOf(track.id, second.id, track.id), reviewPlaylist(decoded, library).matches)
        assertFalse(encoded.decodeToString().contains(track.id))
        assertFalse(encoded.decodeToString().contains(track.sha256))
        assertTrue(playlistShareBody(playlist, library).getValue("tracks").jsonArray.all {
            it.jsonObject.getValue("ref") == JsonNull
        })
    }

    @Test fun receivedCopyKeepsEveryEntryAndCanBeMatchedAgainAfterRestart() {
        val dir = java.nio.file.Files.createTempDirectory("autplay-received-test").toRealPath()
        val file = SharedPlaylist.decode(portablePlaylist(playlist, library))
        try {
            val store = ReceivedPlaylists(dir)
            store.save(file)
            store.save(file)
            val reopened = ReceivedPlaylists(dir)
            assertEquals(listOf(ReceivedTitle(file.token, playlist.name, 3)), reopened.page(0))
            assertEquals(file, reopened.open(file.token))
            assertEquals(3, reviewPlaylist(reopened.open(file.token), DesktopLibrarySnapshot()).missing)
            assertEquals(0, reviewPlaylist(reopened.open(file.token), library).missing)
        } finally {
            java.nio.file.Files.deleteIfExists(dir.resolve("received-playlists").resolve(file.token + ".autplaylist"))
            java.nio.file.Files.deleteIfExists(dir.resolve("received-playlists"))
            java.nio.file.Files.delete(dir)
        }
    }

    @Test fun ambiguousOrRemovedRecordingsHaveNoImplicitSubstitute() {
        val file = SharedPlaylist.decode(portablePlaylist(playlist, library))
        val changed = DesktopLibrarySnapshot(listOf(track, track.copy(id = "different"), second.copy(collectionState = "REMOVED")))
        assertEquals(3, reviewPlaylist(file, changed).missing)
        assertEquals(listOf(track.id, null, track.id), reviewPlaylist(file, DesktopLibrarySnapshot(listOf(track))).matches)
    }
}
