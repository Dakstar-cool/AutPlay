package app.autplay.ui.player

import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertWidthIsAtLeast
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
import androidx.compose.ui.test.click
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.test.swipeDown
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Density
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

    @Test fun restoringQueueShowsPreparationBeforeCurrentItemArrives() {
        val state = androidx.compose.runtime.mutableStateOf(ordinaryState().copy(mediaId = null,
            connection = app.autplay.playback.presentation.PlaybackConnectionState.Connected))
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(state.value, {}, {}, {}, {}, {}, {}, {}, {}, true, {})
        } }
        composeRule.onNodeWithText(context.getString(R.string.player_preparing)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.state_playback_unavailable_title)).assertDoesNotExist()
        composeRule.runOnIdle { state.value = state.value.copy(context = ActiveQueueContext.Absent) }
        composeRule.onNodeWithText(context.getString(R.string.player_nothing_playing)).assertIsDisplayed()
    }

    @Test fun miniPlayerDownwardSwipeDismissesWithoutOpeningOrTogglingPlayback() {
        var dismissCalls = 0
        var openCalls = 0
        var toggleCalls = 0
        composeRule.setContent { AutPlayTheme {
            PlaybackMiniPlayer(ordinaryState().copy(
                playbackStatus = PlaybackStatus.Buffering,
                source = PlaybackSourcePresentation.Unknown,
                controls = PlaybackControlGate.Locked(PlaybackControlLockReason.SOURCE_UNAVAILABLE),
                dismissEnabled = true,
            ), { openCalls++ }, { toggleCalls++ }, {},
                onDismiss = { dismissCalls++ })
        } }
        val dragDistance = 100f * context.resources.displayMetrics.density
        composeRule.onNodeWithTag("persistent-mini-player").performTouchInput {
            swipeDown(startY = centerY, endY = centerY + dragDistance, durationMillis = 300L)
        }
        composeRule.waitForIdle()
        composeRule.runOnIdle {
            assertEquals(1, dismissCalls)
            assertEquals(0, openCalls)
            assertEquals(0, toggleCalls)
        }
    }

    @Test fun miniPlayerClaimsHorizontalDragsOverButtonsAndKeepsShortDragsFromSkipping() {
        val calls = mutableListOf<String>()
        val state = androidx.compose.runtime.mutableStateOf(ordinaryState().copy(previousMediaId = "before", nextMediaId = "after"))
        composeRule.setContent { AutPlayTheme {
            PlaybackMiniPlayer(state.value, { calls += "open" }, { calls += "toggle" }, {},
                likeEnabled = true, onLike = { calls += "like" }, onDismiss = { calls += "dismiss" },
                onPrevious = { calls += "previous" }, onNext = { calls += "next" })
        } }
        val mini = composeRule.onNodeWithTag("persistent-mini-player")
        val like = composeRule.onNodeWithTag("mini-player-like")
        like.performTouchInput { swipeLeft(startX = centerX, endX = centerX - 100.dp.toPx()) }
        mini.performTouchInput { swipeRight() }
        mini.performTouchInput { swipeLeft(startX = centerX, endX = centerX - 32.dp.toPx()) }
        mini.performTouchInput {
            down(center)
            moveTo(center.copy(x = centerX - 100.dp.toPx()))
            cancel()
        }
        like.performTouchInput { click() }
        composeRule.runOnIdle { assertEquals(listOf("next", "previous", "like"), calls) }

        composeRule.runOnIdle { state.value = state.value.copy(nextMediaId = null, previousMediaId = state.value.mediaId) }
        mini.performTouchInput { swipeLeft() }
        mini.performTouchInput { swipeRight() }
        composeRule.runOnIdle { state.value = state.value.copy(nextMediaId = "after", previousMediaId = "before",
            controls = PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE)) }
        mini.performTouchInput { swipeLeft() }
        mini.performTouchInput { swipeRight() }
        mini.performTouchInput { click(center.copy(x = width * .25f)) }
        composeRule.runOnIdle { assertEquals(listOf("next", "previous", "like", "open"), calls) }
    }

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
    fun compactMiniPlayerGivesLongTitlesRoomAndGrowsWithLargeText() {
        val fontScale = androidx.compose.runtime.mutableFloatStateOf(1f)
        val title = "Midnight Train Along the Coast and Beyond the Observatory"
        val artist = "A long artist credit that also needs to stay on one line"
        composeRule.setContent {
            val density = LocalDensity.current.density
            CompositionLocalProvider(LocalDensity provides Density(density, fontScale.floatValue)) {
                AutPlayTheme {
                    Box(Modifier.width(320.dp).testTag("mini-slot")) {
                        PlaybackMiniPlayer(ordinaryState().copy(title = title, artist = artist), {}, {}, {})
                    }
                }
            }
        }
        val slot = composeRule.onNodeWithTag("mini-slot")
        val titleNode = composeRule.onNodeWithText(title, useUnmergedTree = true)
        val artistNode = composeRule.onNodeWithText(artist, useUnmergedTree = true)
        val normalHeight = slot.getUnclippedBoundsInRoot().height
        org.junit.Assert.assertTrue("Mini-player height: $normalHeight", normalHeight <= 55.dp)
        org.junit.Assert.assertTrue(titleNode.getUnclippedBoundsInRoot().width >= 150.dp)
        val buttons = listOf(
            composeRule.onNodeWithTag("mini-player-like"),
            composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)),
        )
        for (button in buttons) {
            button.assertWidthIsAtLeast(48.dp).assertHeightIsAtLeast(48.dp)
        }
        composeRule.runOnIdle { fontScale.floatValue = 2f }
        val expanded = slot.getUnclippedBoundsInRoot()
        val firstLine = titleNode.assertIsDisplayed().getUnclippedBoundsInRoot()
        val secondLine = artistNode.assertIsDisplayed().getUnclippedBoundsInRoot()
        org.junit.Assert.assertTrue(expanded.height > normalHeight)
        org.junit.Assert.assertTrue(firstLine.top >= expanded.top && firstLine.bottom <= secondLine.top &&
            secondLine.bottom <= expanded.bottom)
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

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like)).performClick()
        composeRule.runOnIdle { check(cleared) }

        composeRule.onNodeWithTag("player-sleep-timer").performClick()
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

        composeRule.onNodeWithTag("player-sleep-timer").performClick()
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
        composeRule.onNodeWithTag("player-more").performClick()
        composeRule.onNodeWithTag("player-recommendations").performClick()
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
        val collapseDistance = 130f * context.resources.displayMetrics.density
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
    fun vaultPlayerFitsArtworkDownloadAndQueueInTheViewport() {
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
                        serverConnected = true,
                    )
                }
            }
        }
        val cover = composeRule.onNodeWithTag("player-cover").getUnclippedBoundsInRoot()
        val panel = composeRule.onNodeWithTag("now-playing-panel").getUnclippedBoundsInRoot()
        org.junit.Assert.assertTrue(cover.width > 0.dp && cover.width <= panel.width * 0.78f)
        assertEquals((panel.left + panel.width / 2).value, (cover.left + cover.width / 2).value, 1f)
        assertEquals(cover.width.value, cover.height.value, 1f)
        composeRule.onNodeWithTag("autplay-face").assertDoesNotExist()
        assertPlayerFits()
        composeRule.onNodeWithTag("player-download-track").assertIsDisplayed()
        composeRule.onNodeWithTag("player-sleep-timer").assertIsDisplayed()
        app.autplay.saveUiScreenshot("player-connected")
        composeRule.onNodeWithTag("player-recommendations").assertDoesNotExist()
        composeRule.onNodeWithTag("player-open-queue").assertIsDisplayed().performClick()
        composeRule.onNodeWithTag("queue-editor").assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.queue_editor_empty)).assertIsDisplayed()
    }

    @Test fun heartsFlankPlayAndConnectionSwitchesOnlyTheMiddleUtility() {
        val online = androidx.compose.runtime.mutableStateOf(true)
        val preference = androidx.compose.runtime.mutableStateOf(PlaybackPreferenceUiState.Neutral)
        val calls = mutableListOf<String>()
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(state = ordinaryState().copy(source = PlaybackSourcePresentation.Vault),
                onTogglePlayPause = {}, onToggleShuffle = { calls += "shuffle" }, onCycleRepeat = {},
                onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                onLike = { preference.value = PlaybackPreferenceUiState.Liked },
                onDislike = { preference.value = PlaybackPreferenceUiState.Disliked },
                onClearPreference = { preference.value = PlaybackPreferenceUiState.Neutral },
                preference = preference.value, feedbackEnabled = true, onObservingChanged = {},
                onDownload = { calls += "download" }, serverConnected = online.value)
        } }
        fun action(id: Int) = composeRule.onNodeWithContentDescription(context.getString(id))
        val dislike = action(R.string.action_dislike)
        val like = action(R.string.action_like)
        val transport = listOf(dislike, action(R.string.action_previous), action(R.string.action_play),
            action(R.string.action_next), like).map { it.getUnclippedBoundsInRoot() }
        for ((a, b) in transport.zipWithNext()) {
            org.junit.Assert.assertTrue(a.right <= b.left)
            assertEquals((a.top + a.height / 2).value, (b.top + b.height / 2).value, 1f)
        }
        dislike.performClick().assertIsSelected()
        like.performClick().assertIsSelected()
        dislike.assertIsNotSelected()
        like.performClick().assertIsNotSelected()
        val download = composeRule.onNodeWithTag("player-download-track")
        val middle = download.getUnclippedBoundsInRoot()
        val repeat = composeRule.onNodeWithTag("player-repeat").getUnclippedBoundsInRoot()
        val timer = composeRule.onNodeWithTag("player-sleep-timer").getUnclippedBoundsInRoot()
        org.junit.Assert.assertTrue(transport[2].bottom <= middle.top && repeat.right <= middle.left && middle.right <= timer.left)
        composeRule.onNodeWithText(context.getString(R.string.music_download_track)).assertDoesNotExist()
        composeRule.onNodeWithTag("player-shuffle").assertDoesNotExist()
        download.performClick()
        composeRule.runOnIdle { online.value = false }
        download.assertDoesNotExist()
        val shuffle = composeRule.onNodeWithTag("player-shuffle")
        assertEquals(middle, shuffle.getUnclippedBoundsInRoot())
        shuffle.performClick()
        composeRule.runOnIdle { assertEquals(listOf("download", "shuffle"), calls) }
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

    @Test fun shortScreenFitsArtworkSeekAndTransportWithLongMetadata() {
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
        org.junit.Assert.assertTrue(cover.width > 0.dp && cover.width < 320.dp)
        assertEquals(cover.width.value, cover.height.value, 1f)
        assertPlayerFits()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_play)).assertIsDisplayed()
        composeRule.onNodeWithTag("player-shuffle").assertIsDisplayed()
        composeRule.onNodeWithTag("player-repeat").assertIsDisplayed()
    }

    @Test fun fullscreenArtworkInterpolatesCoverBoundsInBothDirections() {
        val opened = androidx.compose.runtime.mutableStateOf(false)
        val painter = object : Painter() {
            override val intrinsicSize = Size(600f, 300f)
            override fun DrawScope.onDraw() { drawRect(Color.Gray) }
        }
        composeRule.setContent { AutPlayTheme {
            if (opened.value) FullscreenPlayerArtwork("Landscape cover", painter,
                androidx.compose.ui.geometry.Rect(24f, 80f, 224f, 180f), animations = true, onClosed = { opened.value = false })
        } }
        composeRule.mainClock.autoAdvance = false
        try {
            composeRule.runOnIdle { opened.value = true }
            composeRule.mainClock.advanceTimeByFrame()
            val image = composeRule.onNodeWithTag("fullscreen-artwork-image")
            val frame = composeRule.onNodeWithTag("fullscreen-artwork").fetchSemanticsNode().boundsInRoot
            val targetWidth = minOf(frame.width, frame.height * 2f)
            fun awaitIntermediateWidth() = composeRule.waitUntil(5_000) {
                // Android lays out the dialog window independently of the Compose animation clock.
                composeRule.mainClock.advanceTimeByFrame()
                val width = image.fetchSemanticsNode().boundsInRoot.width
                width > 201f && width < targetWidth - 1f
            }
            awaitIntermediateWidth()
            composeRule.mainClock.advanceTimeBy(400)
            val full = image.fetchSemanticsNode().boundsInRoot
            assertEquals(targetWidth, full.width, 1f)
            assertEquals(2f, full.width / full.height, .01f)

            composeRule.onNodeWithContentDescription(context.getString(R.string.player_close_artwork)).performClick()
            awaitIntermediateWidth()
            composeRule.mainClock.advanceTimeBy(300)
            image.assertDoesNotExist()
        } finally { composeRule.mainClock.autoAdvance = true }
    }

    @Test fun draggingACoverWithoutNeighboursKeepsTheArtworkViewerClosed() {
        composeRule.setContent { AutPlayTheme {
            NowPlayingScreen(ordinaryState(), {}, {}, {}, {}, {}, {}, {}, {}, true, {})
        } }
        val cover = composeRule.onNodeWithTag("player-cover")
        cover.performTouchInput { swipeLeft(startX = width * .8f, endX = width * .2f) }
        cover.performTouchInput { swipeUp(startY = height * .7f, endY = height * .3f) }
        composeRule.onNodeWithTag("fullscreen-artwork").assertDoesNotExist()
        cover.performTouchInput { click() }
        composeRule.onNodeWithTag("fullscreen-artwork").assertIsDisplayed()
    }

    @Test fun fullscreenPortraitArtworkFitsImmediatelyWithMotionDisabled() {
        val painter = object : Painter() {
            override val intrinsicSize = Size(300f, 900f)
            override fun DrawScope.onDraw() { drawRect(Color.Gray) }
        }
        val opened = androidx.compose.runtime.mutableStateOf(true)
        composeRule.setContent { AutPlayTheme {
            if (opened.value) FullscreenPlayerArtwork("Portrait cover", painter,
                androidx.compose.ui.geometry.Rect.Zero, animations = false, onClosed = { opened.value = false })
        } }
        val frame = composeRule.onNodeWithTag("fullscreen-artwork").fetchSemanticsNode().boundsInRoot
        val image = composeRule.onNodeWithTag("fullscreen-artwork-image")
        val art = image.fetchSemanticsNode().boundsInRoot
        assertEquals(3f, art.height / art.width, .01f)
        assertEquals(minOf(frame.height, frame.width * 3f), art.height, 1f)
        image.performTouchInput { click() }
        image.assertDoesNotExist()
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
            Box(Modifier.width(320.dp)) { PlayerArtwork("Complete artwork", painter = painter) }
        } }
        var artwork = composeRule.onNodeWithContentDescription("Complete artwork").getUnclippedBoundsInRoot()
        assertEquals(320f, artwork.width.value, 1f)
        assertEquals(160f, artwork.height.value, 1f)
        composeRule.runOnIdle { intrinsic.value = Size(300f, 450f) }
        artwork = composeRule.onNodeWithContentDescription("Complete artwork").getUnclippedBoundsInRoot()
        assertEquals(320f, artwork.width.value, 1f)
        assertEquals(480f, artwork.height.value, 1f)
    }

    private fun assertPlayerFits() {
        val range = composeRule.onNodeWithTag("now-playing-scroll").fetchSemanticsNode()
            .config[SemanticsProperties.VerticalScrollAxisRange]
        assertEquals(0f, range.maxValue(), 0f)
        composeRule.onNodeWithContentDescription(context.getString(R.string.player_seek_description)).assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like)).assertIsDisplayed()
        composeRule.onNodeWithTag("player-transport-controls").assertIsDisplayed()
        composeRule.onNodeWithTag("player-open-queue").assertIsDisplayed()
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
