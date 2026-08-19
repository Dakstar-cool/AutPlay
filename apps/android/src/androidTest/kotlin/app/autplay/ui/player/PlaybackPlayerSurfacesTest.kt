package app.autplay.ui.player

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.AutPlayTheme
import org.junit.Rule
import org.junit.Test

class PlaybackPlayerSurfacesTest {
    @get:Rule
    val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun ordinaryMiniPlayerShowsMetadataAndEnabledTransport() {
        composeRule.setContent {
            AutPlayTheme {
                PlaybackMiniPlayer(
                    state = ordinaryState(),
                    onOpen = {},
                    onTogglePlayPause = {},
                    onObservingChanged = {},
                )
            }
        }

        composeRule.onNodeWithText("Fixture track").assertIsDisplayed()
        composeRule.onNodeWithText("Fixture artist").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsEnabled()
    }

    @Test
    fun waveTimelineAndDirectTransportFailClosed() {
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState().copy(
                        context = ActiveQueueContext.Loaded("wave", "entry-1", "WAVE"),
                        controls = PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE),
                        seekEnabled = false,
                        shuffleEnabled = false,
                        repeatEnabled = false,
                    ),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = false,
                    onObservingChanged = {},
                )
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.player_timeline_locked_wave)).assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsNotEnabled()
        composeRule.onNodeWithContentDescription(context.getString(R.string.player_seek_description)).assertIsNotEnabled()
    }

    private fun ordinaryState() = PlaybackPresentationState(
        mediaId = "entry-1",
        title = "Fixture track",
        artist = "Fixture artist",
        positionMs = 42_000,
        bufferedPositionMs = 75_000,
        durationMs = 180_000,
        isSeekable = true,
        context = ActiveQueueContext.Loaded("queue", "entry-1", "USER"),
        controls = PlaybackControlGate.Allowed,
        seekEnabled = true,
        shuffleEnabled = true,
        repeatEnabled = true,
    )
}
