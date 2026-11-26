package app.autplay.ui

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreReleaseDetailTrack
import app.autplay.application.server.*
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class AlbumTracklistScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun fullEditionShowsMissingTrackAndKeepsLocalPlayback() {
        val edition = AlbumTracklist(AlbumEditionChoice("discogs", "123", ""), listOf(
            AlbumPosition("A1", null, "Song", "Artist", 180000, "recording", true),
            AlbumPosition("B1", null, "Bonus", "Guest", null, null, null)))
        val local = CoreReleaseDetail("album", null, "Album", "Artist", null, null, null, listOf(
            CoreReleaseDetailTrack("first", "rec", 1, 1, "A1", "Song", "Artist", 180000, "local", "recording")))
        var played: String? = null
        compose.setContent { AutPlayTheme {
            CoreProductDetailScreen(CoreProductDetailUiState(DetailTarget(DetailKind.Release, "album"),
                release = local, albumTracklist = AlbumTracklistState(result = edition)), onPlayTrack = { played = it })
        } }
        val list = compose.onNodeWithTag("collection-details")
        list.performScrollToNode(hasTestTag("edition-position-0"))
        compose.onNodeWithTag("edition-position-0").performClick()
        compose.runOnIdle { assertEquals("local", played) }
        list.performScrollToNode(hasTestTag("edition-position-1"))
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        compose.onNodeWithText(context.getString(R.string.album_vault_unknown), useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithText("Bonus", useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithTag("album-track-first").assertDoesNotExist()
    }
}
