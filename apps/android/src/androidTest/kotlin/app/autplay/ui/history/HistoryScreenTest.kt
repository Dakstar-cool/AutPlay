package app.autplay.ui.history

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.history.HistoryCursor
import app.autplay.application.history.HistoryItem
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class HistoryScreenTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun duplicateEventsRemainSeparateAndLoadMoreIsActionable() {
        var played: String? = null
        var more = false
        val items = listOf(item("event-2"), item("event-1"))
        compose.setContent {
            AutPlayTheme {
                HistoryScreen(
                    HistoryUiState(items = items, loading = false, nextCursor = HistoryCursor(1, "event-1")),
                    HistoryUiActions(play = { played = it.listeningEventId }, loadMore = { more = true }),
                    PaddingValues(),
                )
            }
        }

        compose.onNodeWithTag("history-event-2").assertIsDisplayed()
        compose.onNodeWithTag("history-event-1").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("history-play-event-1").performClick()
        compose.onNodeWithTag("history-load-more").performScrollTo().performClick()
        compose.runOnIdle {
            assertEquals("event-1", played)
            check(more)
        }
    }

    @Test fun emptyHistoryOffersLibraryAction() {
        var opened = false
        compose.setContent {
            AutPlayTheme {
                HistoryScreen(
                    HistoryUiState(loading = false),
                    HistoryUiActions(openLibrary = { opened = true }),
                    PaddingValues(),
                )
            }
        }
        compose.onNodeWithText(context.getString(R.string.history_open_library)).performClick()
        compose.runOnIdle { check(opened) }
    }

    private fun item(id: String) = HistoryItem(id, "track", "Repeated", "Artist", 1, 1_000, 2_000, .5, false)
}
