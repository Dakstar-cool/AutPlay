package app.autplay.ui.downloads

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class DownloadsScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun allCoarseStatesRenderTogetherAndActionsUseIntentIdentity() {
        var action: String? = null
        val downloads = listOf(
            row("requested", "REQUESTED"),
            row("queued", "QUEUED"),
            row("downloading", "DOWNLOADING"),
            row("paused", "PAUSED"),
            row("completed", "COMPLETED"),
            row("failed", "FAILED", "NETWORK"),
            row("cancelled", "CANCELLED"),
        )
        compose.setContent {
            AutPlayTheme {
                DownloadsScreen(
                    downloads = downloads,
                    pendingIntentId = null,
                    canDownloadSelected = false,
                    actions = DownloadsUiActions(
                        play = { action = "play:${it.stableId}" },
                        cancel = { action = "cancel:${it.stableId}" },
                        retry = { action = "retry:${it.stableId}" },
                    ),
                    contentPadding = PaddingValues(),
                )
            }
        }

        downloads.forEach { download ->
            compose.onNodeWithTag("downloads-list")
                .performScrollToNode(hasTestTag("download-${download.stableId}"))
            compose.onNodeWithTag("download-${download.stableId}").assertExists()
        }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-play-completed"))
        compose.onNodeWithTag("download-play-completed").performClick()
        compose.runOnIdle { assertEquals("play:completed", action) }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-retry-failed"))
        compose.onNodeWithTag("download-retry-failed").performClick()
        compose.runOnIdle { assertEquals("retry:failed", action) }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-cancel-downloading"))
        compose.onNodeWithTag("download-cancel-downloading").performClick()
        compose.runOnIdle { assertEquals("cancel:downloading", action) }
    }

    private fun row(id: String, state: String, failure: String? = null) = DownloadIntentPresentation(
        stableId = id,
        localUserTrackRefId = "track-$id",
        state = state,
        title = "Track $id",
        artist = "Artist",
        failureCode = failure,
    )
}
