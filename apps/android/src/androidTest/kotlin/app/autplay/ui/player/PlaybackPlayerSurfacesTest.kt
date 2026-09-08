package app.autplay.ui.player

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.semantics.SemanticsActions
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
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
    fun nowPlayingShowsAccessibleResonanceLensWithoutReplacingArtwork() {
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState(),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = true,
                    onObservingChanged = {},
                )
            }
        }

        composeRule.onNodeWithTag("autplay-face").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Fixture track").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.face_description_paused))
            .assertIsDisplayed()
    }

    @Test
    fun resonanceLensKeepsItsRigAspectRatio() {
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState(),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = true,
                    onObservingChanged = {},
                )
            }
        }

        val bounds = composeRule.onNodeWithTag("autplay-face").getUnclippedBoundsInRoot()
        val measuredRatio = (bounds.right - bounds.left) / (bounds.bottom - bounds.top)
        assertEquals(1.84f, measuredRatio, 0.02f)
    }

    @Test
    fun endedMediaIsIdleRatherThanFalselyPaused() {
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState().copy(playbackStatus = PlaybackStatus.Ended),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = true,
                    onObservingChanged = {},
                )
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.face_description_idle))
            .assertIsDisplayed()
        composeRule.onAllNodesWithContentDescription(context.getString(R.string.face_description_paused))
            .assertCountEquals(0)
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
        composeRule.onNodeWithTag("player-wave-by-track").performScrollTo().assertIsNotEnabled()
    }

    @Test
    fun selectedFeedbackCanReturnToNeutralAndTimerUsesMinuteDial() {
        var cleared = false
        var scheduledDurationMs: Long? = null
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState(),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = true,
                    onObservingChanged = {},
                    preference = PlaybackPreferenceUiState.Liked,
                    onClearPreference = { cleared = true },
                    onSetSleepTimer = { scheduledDurationMs = it },
                )
            }
        }

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like)).performClick()
        composeRule.runOnIdle { check(cleared) }

        composeRule.onNodeWithTag("player-sleep-timer").performScrollTo().performClick()
        composeRule.onNodeWithTag("sleep-timer-dial")
            .performSemanticsAction(SemanticsActions.SetProgress) { action -> action(37f) }
        composeRule.onNodeWithTag("sleep-timer-confirm").performClick()
        composeRule.runOnIdle { check(scheduledDurationMs == 37L * 60_000L) }
    }

    @Test
    fun timerCanStopAfterCurrentTrack() {
        var stopAfterTrack = false
        composeRule.setContent {
            AutPlayTheme {
                NowPlayingScreen(
                    state = ordinaryState(),
                    onTogglePlayPause = {},
                    onToggleShuffle = {},
                    onCycleRepeat = {},
                    onSeekBegin = {},
                    onSeekUpdate = {},
                    onSeekCommit = {},
                    onLike = {},
                    onDislike = {},
                    feedbackEnabled = true,
                    onObservingChanged = {},
                    onStopAfterCurrentTrack = { stopAfterTrack = true },
                )
            }
        }

        composeRule.onNodeWithTag("player-sleep-timer").performScrollTo().performClick()
        composeRule.onNodeWithTag("sleep-timer-after-track").performClick()
        composeRule.onNodeWithTag("sleep-timer-confirm").performClick()
        composeRule.runOnIdle { check(stopAfterTrack) }
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
        playbackStatus = PlaybackStatus.Ready,
    )
}
