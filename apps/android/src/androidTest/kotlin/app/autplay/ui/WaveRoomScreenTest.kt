package app.autplay.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import app.autplay.WaveRoomActions
import app.autplay.WaveRoomScreen
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.wave.WaveQueueItem
import app.autplay.application.wave.WaveUiState
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class WaveRoomScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun hostCreatesAddsMusicAndConfirmsClose() {
        val state = mutableStateOf(WaveUiState())
        var created = 0
        var added: String? = null
        var closed = 0
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    WaveRoomScreen(state.value, available = true,
                        libraryEntries = listOf(CoreLibraryEntrySummary("library", "track", "Room song", "AutPlay", 0, "LOCAL", false, true)),
                        actions = WaveRoomActions(
                            create = { created++; state.value = WaveUiState(roomId = "room", isHost = true, roomCode = "01ABCDEFGH") },
                            addTrack = { added = it; state.value = state.value.copy(queue = listOf(WaveQueueItem("entry", "Room song", "AutPlay", true))) },
                            close = { closed++ },
                        ),
                    )
                }
            }
        }
        compose.onNodeWithTag("wave-create").performScrollTo().performClick()
        compose.onNodeWithTag("wave-start").performScrollTo().assertIsNotEnabled()
        compose.onNodeWithTag("wave-add-track").performScrollTo().performClick()
        compose.onNodeWithTag("wave-pick-track-0").performClick()
        compose.onNodeWithTag("wave-queue-entry").assertExists()
        compose.onNodeWithTag("wave-close").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(0, closed) }
        compose.onNodeWithTag("wave-close-confirm").performClick()
        compose.runOnIdle { assertEquals(1, created); assertEquals("track", added); assertEquals(1, closed) }
    }

    @Test fun joinNormalizesCodeAndPendingDisablesDuplicateSubmission() {
        val pending = mutableStateOf(false)
        var joined: String? = null
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    WaveRoomScreen(WaveUiState(), available = true, pending = pending.value,
                        actions = WaveRoomActions(join = { joined = it; pending.value = true }))
                }
            }
        }
        compose.onNodeWithTag("wave-code-input").performScrollTo().performTextInput("01abcdefgh")
        compose.onNodeWithTag("wave-join").performScrollTo().performClick().assertIsNotEnabled()
        compose.onNodeWithTag("wave-create").assertIsNotEnabled()
        compose.runOnIdle { assertEquals("01ABCDEFGH", joined) }
    }

    @Test fun memberSeesQueueAndLeaveWithoutHostControls() {
        var left = 0
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    WaveRoomScreen(WaveUiState(roomId = "room", queue = listOf(WaveQueueItem("entry", "Member song", null, false))),
                        available = true, actions = WaveRoomActions(leave = { left++ }))
                }
            }
        }
        compose.onNodeWithText("Member song").assertExists()
        compose.onNodeWithTag("wave-add-track").assertDoesNotExist()
        compose.onNodeWithTag("wave-start").assertDoesNotExist()
        compose.onNodeWithTag("wave-close").assertDoesNotExist()
        compose.onNodeWithTag("wave-leave").performScrollTo().performClick()
        compose.runOnIdle { assertEquals(1, left) }
    }
}
