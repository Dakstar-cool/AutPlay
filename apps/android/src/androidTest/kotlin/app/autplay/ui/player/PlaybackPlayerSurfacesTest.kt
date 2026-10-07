package app.autplay.ui.player

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.test.swipeDown
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.height
import androidx.compose.ui.unit.width
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.HomeSwipeTarget
import app.autplay.ui.HomeSwipeTargets
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class PlaybackPlayerSurfacesTest {
    @get:Rule
    val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun fullPlayerUsesActualNeighboursEvenAtLinearQueueBoundary() {
        val state = androidx.compose.runtime.mutableStateOf(ordinaryState().copy(previousMediaId = "last", nextMediaId = "first"))
        var previousCalls = 0
        var nextCalls = 0
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(state.value, {}, {}, {}, {}, {}, {}, {}, {}, true, {},
                onPrevious = { previousCalls++ }, onNext = { nextCalls++ },
                queueState = app.autplay.ui.queue.QueueEditorUiState(canPrevious = false, canNext = false))
        } }
        val previous = composeRule.onNodeWithContentDescription(context.getString(R.string.action_previous))
        val next = composeRule.onNodeWithContentDescription(context.getString(R.string.action_next))
        previous.assertIsDisplayed().assertIsEnabled().performClick()
        next.assertIsEnabled().performClick()
        assertEquals(1, previousCalls)
        assertEquals(1, nextCalls)
        composeRule.runOnIdle { state.value = state.value.copy(previousMediaId = null, nextMediaId = null) }
        previous.assertIsNotEnabled()
        next.assertIsNotEnabled()
    }

    @Test fun fullPlayerSwipesToLibraryNeighbourWhenTheQueueHasNoNextEntry() {
        var nextCalls = 0
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(
                state = ordinaryState().copy(previousMediaId = null, nextMediaId = null),
                onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                onLike = {}, onDislike = {}, feedbackEnabled = true,
                onObservingChanged = {}, onNext = { nextCalls++ },
                swipeTargets = HomeSwipeTargets(
                    next = HomeSwipeTarget("next-library", "next-library", "Next song", "Artist"),
                ),
            )
        } }

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_next)).assertIsEnabled()
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeLeft() }
        composeRule.waitForIdle()
        assertEquals(1, nextCalls)
    }

    @Test
    fun fullPlayerCoverSwipesInBothDirectionsWithoutCollapsing() {
        val state = androidx.compose.runtime.mutableStateOf(ordinaryState())
        var previousCalls = 0
        var nextCalls = 0
        var collapseCalls = 0
        composeRule.setContent { AutPlayTheme {
            CompositionLocalProvider(LocalPlayerCollapse provides { collapseCalls++ }) {
                NowPlayingScreen(
                    state = state.value,
                    onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                    onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                    onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                    onPrevious = { previousCalls++; state.value = state.value.copy(mediaId = "previous-entry") },
                    onNext = { nextCalls++; state.value = state.value.copy(mediaId = "next-entry") },
                    swipeTargets = HomeSwipeTargets(
                        previous = HomeSwipeTarget("previous", "previous", "Previous song", "Artist"),
                        next = HomeSwipeTarget("next", "next", "Next song", "Artist"),
                    ),
                )
            }
        } }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeLeft() }
        composeRule.runOnIdle { assertEquals(1, nextCalls) }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeRight() }
        composeRule.runOnIdle {
            assertEquals(1, previousCalls)
            assertEquals(0, collapseCalls)
        }
    }

    @Test
    fun lockedPlayerCoverCannotChangeTracks() {
        var transportCalls = 0
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(
                state = ordinaryState().copy(controls = PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE)),
                onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                onLike = {}, onDislike = {}, feedbackEnabled = false, onObservingChanged = {},
                onPrevious = { transportCalls++ }, onNext = { transportCalls++ },
                swipeTargets = HomeSwipeTargets(
                    previous = HomeSwipeTarget("previous", "previous", "Previous song", "Artist"),
                    next = HomeSwipeTarget("next", "next", "Next song", "Artist"),
                ),
            )
        } }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeLeft() }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeRight() }
        composeRule.runOnIdle { assertEquals(0, transportCalls) }
    }

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
    fun nowPlayingKeepsArtworkAndTransportWithoutFace() {
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

        composeRule.onNodeWithTag("autplay-face").assertDoesNotExist()
        composeRule.onNodeWithContentDescription("Fixture track").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsEnabled()
    }

    @Test
    fun endedMediaKeepsArtworkAndPlayControl() {
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

        composeRule.onNodeWithTag("player-cover").assertIsDisplayed()
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

        composeRule.onNodeWithText(context.getString(R.string.player_timeline_locked_wave))
            .performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsNotEnabled()
        composeRule.onNodeWithContentDescription(context.getString(R.string.player_seek_description)).assertIsNotEnabled()
        composeRule.onNodeWithTag("player-wave-by-track").assertDoesNotExist()
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

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like)).performScrollTo().performClick()
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

    @Test
    fun tasteExclusionControlsExposeIndependentDurableIntents() {
        var listenExcluded: Boolean? = null
        var sessionExcluded: Boolean? = null
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
                    listenExcludedFromTaste = false,
                    sessionExcludedFromTaste = true,
                    listenTasteActionAvailable = true,
                    sessionTasteActionAvailable = true,
                    onSetCurrentListenTasteExcluded = { listenExcluded = it },
                    onSetSessionTasteExcluded = { sessionExcluded = it },
                )
            }
        }

        composeRule.onNodeWithTag("taste-exclude-listen").assertDoesNotExist()
        composeRule.onNodeWithTag("player-taste-toggle").performScrollTo().performClick()
        composeRule.onNodeWithTag("taste-exclude-listen").performScrollTo().performClick()
        composeRule.onNodeWithTag("taste-exclude-session").performScrollTo().performClick()
        composeRule.runOnIdle {
            assertEquals(true, listenExcluded)
            assertEquals(false, sessionExcluded)
        }
    }

    @Test
    fun downwardCoverSwipeCollapsesWithoutChangingPlayback() {
        var collapseCalls = 0
        var transportCalls = 0
        composeRule.setContent {
            AutPlayTheme {
                CompositionLocalProvider(LocalPlayerCollapse provides { collapseCalls++ }) {
                    NowPlayingScreen(
                        state = ordinaryState().copy(isPlaying = true),
                        onTogglePlayPause = { transportCalls++ }, onToggleShuffle = {}, onCycleRepeat = {},
                        onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                        onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                    )
                }
            }
        }
        val collapseDistance = composeRule.onNodeWithTag("now-playing-panel").fetchSemanticsNode().boundsInRoot.height * 0.55f
        composeRule.onNodeWithTag("player-cover").performTouchInput {
            swipeDown(startY = height * 0.1f, endY = height * 0.1f + collapseDistance, durationMillis = 500L)
        }
        composeRule.runOnIdle {
            assertEquals(1, collapseCalls)
            assertEquals(0, transportCalls)
        }
        composeRule.onNodeWithTag("player-collapse-header").performTouchInput {
            swipeDown(startY = centerY, endY = centerY + collapseDistance, durationMillis = 500L)
        }
        composeRule.runOnIdle {
            assertEquals(2, collapseCalls)
            assertEquals(0, transportCalls)
        }
    }

    @Test
    fun upwardCoverSwipeKeepsThePlayerOpen() {
        var collapseCalls = 0
        composeRule.setContent {
            AutPlayTheme {
                CompositionLocalProvider(LocalPlayerCollapse provides { collapseCalls++ }) {
                    NowPlayingScreen(
                        state = ordinaryState(),
                        onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                        onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                        onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                    )
                }
            }
        }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeUp() }
        composeRule.runOnIdle { assertEquals(0, collapseCalls) }
    }

    @Test
    fun shortDownwardDragKeepsThePlayerOpen() {
        var collapseCalls = 0
        composeRule.setContent {
            AutPlayTheme {
                CompositionLocalProvider(LocalPlayerCollapse provides { collapseCalls++ }) {
                    NowPlayingScreen(
                        state = ordinaryState(),
                        onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                        onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                        onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                    )
                }
            }
        }
        composeRule.onNodeWithTag("player-cover").performTouchInput {
            swipeDown(startY = centerY, endY = centerY + 24f, durationMillis = 300L)
        }
        composeRule.runOnIdle { assertEquals(0, collapseCalls) }
    }

    @Test
    fun coverUsesFullWidthAndQueueCanBeOpened() {
        composeRule.setContent {
            AutPlayTheme {
                app.autplay.ui.AutPlayAdaptiveShell(
                    selectedDestination = app.autplay.ui.UiDestination.NowPlaying,
                    onDestinationSelected = {},
                ) { _, contentPadding, _ ->
                    NowPlayingScreen(
                        state = ordinaryState().copy(
                            title = "Halfway Back Home", artist = "Owen Coleman",
                            source = PlaybackSourcePresentation.Vault, isPlaying = true,
                        ),
                        onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                        onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                        onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                        modifier = Modifier.padding(contentPadding),
                    )
                }
            }
        }
        val cover = composeRule.onNodeWithTag("player-cover").getUnclippedBoundsInRoot()
        val panel = composeRule.onNodeWithTag("now-playing-panel").getUnclippedBoundsInRoot()
        assertEquals(panel.width.value, cover.width.value, 1f)
        assertEquals(cover.width.value, cover.height.value, 1f)
        composeRule.onNodeWithTag("autplay-face").assertDoesNotExist()
        saveScreenshot("player-layout-collapsed-taste")
        composeRule.onNodeWithTag("player-taste-toggle").performScrollTo().performClick()
        composeRule.onNodeWithTag("taste-exclude-session").performScrollTo().assertIsDisplayed()
        saveScreenshot("player-layout-expanded-taste")
        composeRule.onNodeWithTag("player-open-queue").performScrollTo().performClick()
        composeRule.onNodeWithTag("queue-editor").assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.queue_editor_empty)).assertIsDisplayed()
    }

    @Test fun repeatAndShuffleExposeConfirmedStateAndKeepDisabledState() {
        val state = androidx.compose.runtime.mutableStateOf(ordinaryState())
        var shuffleCalls = 0
        var repeatCalls = 0
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(state.value, {}, { shuffleCalls++ }, { repeatCalls++ }, {}, {}, {}, {}, {}, true, {})
        } }
        val shuffle = composeRule.onNodeWithTag("player-shuffle")
        val repeat = composeRule.onNodeWithTag("player-repeat")
        shuffle.assertIsNotSelected().assert(SemanticsMatcher.expectValue(
            SemanticsProperties.StateDescription, context.getString(R.string.player_surface_shuffle_off)))
        repeat.assertIsNotSelected()
        shuffle.performClick()
        repeat.performClick()
        composeRule.runOnIdle {
            assertEquals(1, shuffleCalls)
            assertEquals(1, repeatCalls)
            state.value = state.value.copy(shuffleModeEnabled = true, repeatMode = RepeatModePresentation.One)
        }
        shuffle.assertIsSelected().assert(SemanticsMatcher.expectValue(
            SemanticsProperties.StateDescription, context.getString(R.string.player_surface_shuffle_on)))
        repeat.assertIsSelected().assert(SemanticsMatcher.expectValue(
            SemanticsProperties.StateDescription, context.getString(R.string.player_repeat_one)))
        composeRule.onNodeWithText("1").assertIsDisplayed()
        composeRule.runOnIdle { state.value = state.value.copy(repeatMode = RepeatModePresentation.All) }
        repeat.assertIsSelected().assert(SemanticsMatcher.expectValue(
            SemanticsProperties.StateDescription, context.getString(R.string.player_repeat_all)))
        composeRule.onNodeWithText("1").assertDoesNotExist()
        composeRule.runOnIdle { state.value = state.value.copy(shuffleEnabled = false, repeatEnabled = false) }
        shuffle.assertIsSelected().assertIsNotEnabled()
        repeat.assertIsSelected().assertIsNotEnabled()
        for (button in listOf(shuffle, repeat)) {
            val bounds = button.getUnclippedBoundsInRoot()
            org.junit.Assert.assertTrue(bounds.width >= 48.dp && bounds.height >= 48.dp)
        }
    }

    @Test fun shortScreenKeepsTransportVisibleWithFullWidthCoverAndLongMetadata() {
        composeRule.setContent { AutPlayTheme {
            Box(Modifier.size(320.dp, 400.dp)) {
                NowPlayingScreen(
                    state = ordinaryState().copy(title = "A long track title repeated twice A long track title repeated twice", artist = "A very long artist name"),
                    onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                    onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                    onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                )
            }
        } }
        val cover = composeRule.onNodeWithTag("player-cover").getUnclippedBoundsInRoot()
        assertEquals(320f, cover.width.value, 1f)
        assertEquals(320f, cover.height.value, 1f)
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsDisplayed()
        composeRule.onNodeWithTag("player-shuffle").assertIsDisplayed()
        composeRule.onNodeWithTag("player-repeat").assertIsDisplayed()
        saveScreenshot("player-fullwidth-cover-short")
        composeRule.onNodeWithTag("player-taste-toggle").performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsDisplayed()
    }

    @Test fun nonSquareArtworkKeepsFullWidthAndOriginalProportions() {
        val intrinsic = androidx.compose.runtime.mutableStateOf(Size(600f, 300f))
        composeRule.setContent { AutPlayTheme {
            val artworkSize = intrinsic.value
            val painter = androidx.compose.runtime.remember(artworkSize) {
                object : Painter() {
                    override val intrinsicSize = artworkSize
                    override fun DrawScope.onDraw() { drawRect(Color.Gray) }
                }
            }
            Box(Modifier.width(320.dp)) { PlayerArtwork("Complete artwork", null, painter = painter) }
        } }
        var artwork = composeRule.onNodeWithContentDescription("Complete artwork").getUnclippedBoundsInRoot()
        assertEquals(320f, artwork.width.value, 1f)
        assertEquals(160f, artwork.height.value, 1f)
        composeRule.runOnIdle { intrinsic.value = Size(300f, 450f) }
        artwork = composeRule.onNodeWithContentDescription("Complete artwork").getUnclippedBoundsInRoot()
        assertEquals(320f, artwork.width.value, 1f)
        assertEquals(480f, artwork.height.value, 1f)
    }

    private fun saveScreenshot(name: String) {
        composeRule.waitForIdle()
        val screenshot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        java.io.File(context.cacheDir, "$name.png").outputStream().use {
            check(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it))
        }
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
