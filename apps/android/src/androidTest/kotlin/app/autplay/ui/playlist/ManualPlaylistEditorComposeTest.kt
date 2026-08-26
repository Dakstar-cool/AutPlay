package app.autplay.ui.playlist

import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import app.autplay.ui.AutPlayTheme
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ManualPlaylistEditorComposeTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun hubOpensExactExistingPlaylistFromBoundedList() {
        val opened = AtomicReference<String?>()
        val playlists = (0 until 200).map { index ->
            ManualPlaylistUi("playlist-$index", "Playlist $index")
        }
        composeRule.setContent {
            AutPlayTheme {
                ManualPlaylistHub(
                    playlists = playlists,
                    selectedTrackRefId = null,
                    actions = ManualPlaylistActions(),
                    onOpenPlaylist = opened::set,
                )
            }
        }

        composeRule.onNodeWithTag("playlist-hub-list")
            .performScrollToNode(androidx.compose.ui.test.hasTestTag("playlist-open-playlist-199"))
        composeRule.onNodeWithTag("playlist-open-playlist-199").performClick()

        assertEquals("playlist-199", opened.get())
    }

    @Test
    fun largePlaylistKeepsLastExactEntryReachable() {
        val entries = (0 until 1_000).map { index ->
            ManualPlaylistEntryUi("entry-$index", "track-$index", "Track $index")
        }
        composeRule.setContent {
            AutPlayTheme {
                ManualPlaylistEditor(
                    playlist = ManualPlaylistUi("large", "Large", entries = entries),
                    actions = ManualPlaylistActions(),
                )
            }
        }

        composeRule.onNodeWithTag("playlist-entry-list")
            .performScrollToNode(androidx.compose.ui.test.hasTestTag("playlist-entry-entry-999"))
        composeRule.onNodeWithTag("playlist-entry-entry-999").assertIsDisplayed()
    }
}
