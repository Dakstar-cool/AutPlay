package app.autplay

import android.app.ActivityManager
import android.content.ContentValues
import android.content.Intent
import android.net.Uri
import android.provider.MediaStore
import androidx.test.filters.SdkSuppress
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assert
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.click
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.isHeading
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.isSelected
import androidx.compose.ui.unit.width
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.isEnabled
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.test.swipeDown
import androidx.compose.ui.test.swipeUp
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.CURRENT_ONBOARDING_REVISION
import app.autplay.application.importing.CreateLocalImportCommand
import app.autplay.application.importing.ImportResolverState
import app.autplay.application.importing.ImportRowInput
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.importing.MatchCandidateInput
import app.autplay.application.importing.RecordShadowEvaluationCommand
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.PlaylistEntity
import app.autplay.data.local.entity.PlaylistEntryEntity
import app.autplay.data.local.entity.ReleaseProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.data.local.entity.TrackMetadataEntity
import kotlinx.coroutines.flow.first
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.search.refreshTrackSearch
import app.autplay.domain.LocalId
import app.autplay.playback.AutPlayPlaybackService
import app.autplay.playback.PlaybackShutdownPersistence
import app.autplay.playback.PlaybackUiState
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackRuntimeState
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.ui.theme.AutPlayAccent
import app.autplay.ui.theme.autPlayColorScheme
import app.autplay.ui.AppLanguage
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OfflineLibraryScreenTest {
    @get:Rule
    val composeRule = createEmptyComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val defaultStrings get() = localizedAppContext(context, AppLanguage.English)
    private var scenario: ActivityScenario<MainActivity>? = null
    private val waveSamples = mutableListOf<Uri>()

    @Before
    fun setUp() = runBlocking {
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = ServerProfileId("51111111-1111-4111-8111-111111111111"),
                activeUserId = UserId("31111111-1111-4111-8111-111111111111"),
                deviceId = DeviceId("41111111-1111-4111-8111-111111111111"),
                serverBaseUrl = "https://offline.test",
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
    }

    @After
    fun tearDown() = runBlocking {
        if (PlaybackRuntimeState.state.value.isPlaying) {
            ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.Stop)
            composeRule.waitUntil(10_000) { !PlaybackRuntimeState.state.value.isPlaying }
        }
        scenario?.close()
        stopPlaybackService()
        waveSamples.forEach { audio -> context.contentResolver.delete(audio, null, null) }
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
    }

    private suspend fun stopPlaybackService() {
        // Starting a queue for unavailable audio can also leave a service holding the fixture database.
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync { context.stopService(Intent(context, AutPlayPlaybackService::class.java)) }
        val manager = context.getSystemService(ActivityManager::class.java)
        composeRule.waitUntil(10_000) {
            @Suppress("DEPRECATION")
            manager.getRunningServices(Int.MAX_VALUE).none { it.service.className == AutPlayPlaybackService::class.java.name }
        }
        instrumentation.waitForIdleSync()
        PlaybackShutdownPersistence.awaitPending()
        PlaybackRuntimeState.publish(PlaybackUiState())
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun homeStartsLocalWaveAndContinuesSameQueueAfterPause() {
        val strings = localizedAppContext(context, AppLanguage.Russian)
        val title = "Ночной берег"
        val trackId = importPlayableSample(title, artist = "Линия моря")
        runBlocking { seedSunsetArtwork(context, AutPlayRuntime.database(context), "legacy-unscoped", trackId.value) }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        val start = hasTestTag("home-my-wave") and isEnabled()
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(start).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(start).performClick()
        composeRule.waitUntil(15_000) { PlaybackRuntimeState.state.value.isPlaying }
        val queueId = requireNotNull(PlaybackRuntimeState.state.value.queueSnapshotId)
        val hero = hasAnyAncestor(hasTestTag("home-playback-hero"))
        val pause = hasContentDescription(strings.getString(R.string.action_pause)) and hero
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(pause).fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("home-artist-glow")
        composeRule.onNode(pause).performClick()
        composeRule.waitUntil(10_000) { !PlaybackRuntimeState.state.value.isPlaying }
        composeRule.onNodeWithTag("home-wave-wheel").assertIsDisplayed()
        scenario?.close()
        runBlocking { stopPlaybackService() }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(start).fetchSemanticsNodes().isNotEmpty() }
        val resumedSong = hasText("Линия моря — $title")
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(resumedSong).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("home-product-list").performScrollToNode(resumedSong)
        composeRule.onNode(resumedSong).assertIsDisplayed()
        composeRule.onNodeWithTag("home-wave-wheel").assertIsDisplayed()
        composeRule.onNodeWithTag("home-track-carousel").assertDoesNotExist()
        composeRule.onNodeWithTag("persistent-mini-player").assertDoesNotExist()
        saveUiScreenshot("home-review-ready")
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_continue)).performClick()
        composeRule.waitUntil(15_000) { PlaybackRuntimeState.state.value.isPlaying }
        composeRule.onNode(pause).assertIsDisplayed()
        composeRule.onNodeWithTag("home-track-carousel").assertIsDisplayed()
        composeRule.onNodeWithTag("persistent-mini-player").assertDoesNotExist()
        saveUiScreenshot("home-review-playing")
        waitForNavigation(strings.getString(R.string.nav_search)).performClick()
        composeRule.onNodeWithTag("persistent-mini-player").assertIsDisplayed()
        saveUiScreenshot("home-review-mini")
        waitForNavigation(strings.getString(R.string.nav_home)).performClick()
        composeRule.onNodeWithTag("persistent-mini-player").assertDoesNotExist()
        org.junit.Assert.assertEquals(queueId, PlaybackRuntimeState.state.value.queueSnapshotId)
        org.junit.Assert.assertEquals(trackId.value, PlaybackRuntimeState.state.value.localUserTrackRefId)
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun miniPlayerControlsTheSameTrackAcrossNavigation() {
        val title = "Midnight Train Along the Coast and Beyond the Observatory"
        val trackId = startLocalWave(title)
        val queueId = requireNotNull(PlaybackRuntimeState.state.value.queueSnapshotId)
        val strings = localizedAppContext(context, AppLanguage.Russian)
        val mini = hasAnyAncestor(hasTestTag("persistent-mini-player"))
        val pause = hasContentDescription(strings.getString(R.string.action_pause)) and mini
        val play = hasContentDescription(strings.getString(R.string.action_play)) and mini
        composeRule.onNodeWithTag("persistent-mini-player").assertDoesNotExist()
        waitForNavigation(strings.getString(R.string.nav_search)).performClick()
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(pause and isEnabled()).fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("mini-player")
        composeRule.onNode(pause).performTouchInput { click() }
        composeRule.waitUntil(10_000) { !PlaybackRuntimeState.state.value.isPlaying }
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        val like = hasTestTag("mini-player-like")
        composeRule.onNode(like).performTouchInput { click() }
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(like and isSelected()).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("library-favorites").performClick()
        val libraryLike = hasTestTag("library-like-${trackId.value}") and isSelected()
        scrollLibraryTo(libraryLike)
        composeRule.onNode(libraryLike).assertIsDisplayed()
        composeRule.onNode(libraryLike).performClick()
        composeRule.waitUntil(10_000) {
            composeRule.onAllNodesWithText(strings.getString(R.string.library_favorites_empty)).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag("library-favorites").performClick()
        scrollLibraryTo(hasTestTag("library-track-${trackId.value}"))
        composeRule.onNode(play).performTouchInput { click() }
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.isPlaying }
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        composeRule.onNodeWithTag("now-playing-panel").assertIsDisplayed()
        org.junit.Assert.assertTrue(PlaybackRuntimeState.state.value.isPlaying)
        org.junit.Assert.assertEquals(queueId, PlaybackRuntimeState.state.value.queueSnapshotId)
        org.junit.Assert.assertEquals(trackId.value, PlaybackRuntimeState.state.value.localUserTrackRefId)

        composeRule.onNodeWithContentDescription(strings.getString(R.string.player_collapse)).performClick()
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        composeRule.onNodeWithTag("persistent-mini-player").performTouchInput {
            swipeDown(startY = centerY, endY = centerY + height * 1.35f, durationMillis = 350)
        }
        composeRule.waitUntil(10_000) {
            composeRule.onAllNodesWithTag("persistent-mini-player").fetchSemanticsNodes().isEmpty() &&
                !PlaybackRuntimeState.state.value.isPlaying
        }
        org.junit.Assert.assertEquals(queueId, PlaybackRuntimeState.state.value.queueSnapshotId)
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun miniPlayerSwipesThroughThePlayingAndPausedQueue() {
        val last = importPlayableSample("На рассвете").value
        val middle = importPlayableSample("Северный ветер").value
        val first = importPlayableSample("Ночной берег").value
        val strings = localizedAppContext(context, AppLanguage.Russian)
        val previous = strings.getString(R.string.player_previous_track)
        val next = strings.getString(R.string.player_next_track)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        val row = hasTestTag("library-track-$first")
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(row).performClick()
        val mini = composeRule.onNodeWithTag("persistent-mini-player")
        fun awaitTrack(id: String, title: String, playing: Boolean, vararg directions: String) {
            val titleInMini = hasText(title) and hasAnyAncestor(hasTestTag("persistent-mini-player"))
            composeRule.waitUntil(15_000) {
                val current = PlaybackRuntimeState.state.value
                val actions = composeRule.onAllNodesWithTag("persistent-mini-player").fetchSemanticsNodes()
                    .singleOrNull()?.config?.getOrNull(SemanticsActions.CustomActions).orEmpty()
                current.localUserTrackRefId == id && current.isPlaying == playing &&
                    actions.map { it.label }.toSet() == directions.toSet() &&
                    composeRule.onAllNodes(titleInMini, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
            }
        }
        awaitTrack(first, "Ночной берег", true, next)
        val queue = requireNotNull(PlaybackRuntimeState.state.value.queueSnapshotId)
        mini.performTouchInput { swipeRight(startX = width * .25f, endX = width * .8f) }
        awaitTrack(first, "Ночной берег", true, next)
        saveUiScreenshot("mini-before-swipe")

        // Start over the pause button and release over the title.
        mini.performTouchInput { swipeLeft(startX = width * .95f, endX = width * .25f) }
        awaitTrack(middle, "Северный ветер", true, previous, next)
        saveUiScreenshot("mini-after-swipe")
        mini.performTouchInput { swipeRight() }
        awaitTrack(first, "Ночной берег", true, next)
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_pause)).performTouchInput { click() }
        awaitTrack(first, "Ночной берег", false, next)
        mini.performTouchInput { swipeLeft() }
        awaitTrack(middle, "Северный ветер", false, previous, next)
        mini.performTouchInput { swipeLeft() }
        awaitTrack(last, "На рассвете", false, previous)
        mini.performTouchInput { swipeLeft() }
        awaitTrack(last, "На рассвете", false, previous)
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        composeRule.onNodeWithTag("now-playing-panel").assertIsDisplayed()
        composeRule.onNodeWithTag("player-repeat").performClick()
        composeRule.onNodeWithContentDescription(strings.getString(R.string.player_repeat_once)).performClick()
        composeRule.onNodeWithContentDescription(strings.getString(R.string.player_repeat_one)).assertIsDisplayed()
        val collapse = composeRule.onNodeWithContentDescription(strings.getString(R.string.player_collapse))
        collapse.performClick()
        awaitTrack(last, "На рассвете", false, previous, next)
        mini.performTouchInput { swipeLeft() }
        awaitTrack(first, "Ночной берег", false, previous, next)

        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        val shuffle = hasTestTag("player-shuffle")
        composeRule.onNode(shuffle).performClick()
        composeRule.waitUntil(5_000) { composeRule.onAllNodes(shuffle and isSelected()).fetchSemanticsNodes().isNotEmpty() }
        collapse.performClick()
        awaitTrack(first, "Ночной берег", false, previous, next)
        mini.performTouchInput { swipeLeft() }
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.localUserTrackRefId in setOf(middle, last) }
        if (PlaybackRuntimeState.state.value.localUserTrackRefId == middle) {
            awaitTrack(middle, "Северный ветер", false, previous, next)
        } else {
            awaitTrack(last, "На рассвете", false, previous, next)
        }
        mini.performTouchInput { swipeRight() }
        awaitTrack(first, "Ночной берег", false, previous, next)
        org.junit.Assert.assertEquals(queue, PlaybackRuntimeState.state.value.queueSnapshotId)
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun nowPlayingFitsAndSecondaryActionsWorkDuringPlayback() {
        val trackId = startLocalWave("Midnight Train Along the Coast and Beyond the Observatory")
        val strings = localizedAppContext(context, AppLanguage.Russian)
        composeRule.onNodeWithContentDescription(strings.getString(R.string.home_hero_open_player)).performClick()
        val panel = composeRule.onNodeWithTag("now-playing-panel").getUnclippedBoundsInRoot()
        val cover = composeRule.onNodeWithTag("player-cover").getUnclippedBoundsInRoot()
        val seek = composeRule.onNodeWithContentDescription(strings.getString(R.string.player_seek_description))
        val transport = composeRule.onNodeWithTag("player-transport-controls").getUnclippedBoundsInRoot()
        val timeline = seek.getUnclippedBoundsInRoot()
        val scroll = composeRule.onNodeWithTag("now-playing-scroll").fetchSemanticsNode()
            .config[SemanticsProperties.VerticalScrollAxisRange]
        org.junit.Assert.assertEquals(0f, scroll.maxValue(), 0f)
        org.junit.Assert.assertTrue(panel.top <= cover.top && cover.bottom <= timeline.top &&
            timeline.bottom <= transport.top && transport.bottom <= panel.bottom)
        composeRule.onNodeWithTag("player-sleep-timer").assertIsDisplayed()
        composeRule.onNodeWithTag("player-recommendations").assertDoesNotExist()
        val like = hasContentDescription(strings.getString(R.string.action_like))
        composeRule.onNode(like).assertIsDisplayed().performTouchInput { click() }
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(like and isSelected()).fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("now-playing")
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_pause)).performTouchInput { click() }
        composeRule.waitUntil(10_000) { !PlaybackRuntimeState.state.value.isPlaying }
        seek.performTouchInput { click(Offset(width * 0.5f, centerY)) }
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.positionMs in 15_000L..25_000L }
        composeRule.onNodeWithTag("player-open-queue").performTouchInput { click() }
        composeRule.onNodeWithTag("queue-editor").assertIsDisplayed()
        androidx.test.espresso.Espresso.pressBack()
        composeRule.onNodeWithTag("player-more").performTouchInput { click() }
        composeRule.onNodeWithTag("player-recommendations").assertIsDisplayed()
        saveUiScreenshot("now-playing-menu")
        androidx.test.espresso.Espresso.pressBack()
        composeRule.onNodeWithTag("player-sleep-timer").performClick()
        composeRule.onNodeWithTag("sleep-timer-confirm").performClick()
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.sleepTimerDeadlineElapsedRealtimeMs != null }
        composeRule.onNodeWithTag("player-sleep-timer").performClick()
        composeRule.onNodeWithText(strings.getString(R.string.player_sleep_timer_cancel)).performClick()
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.sleepTimerDeadlineElapsedRealtimeMs == null }
        composeRule.onNodeWithTag("player-more").performClick()
        composeRule.onNodeWithTag("player-recommendations").performClick()
        composeRule.onNodeWithTag("taste-exclude-session").performClick()
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.sessionExcludedFromTaste }
        androidx.test.espresso.Espresso.pressBack()
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_play)).performTouchInput { click() }
        composeRule.waitUntil(10_000) { PlaybackRuntimeState.state.value.isPlaying }
        org.junit.Assert.assertEquals(trackId.value, PlaybackRuntimeState.state.value.localUserTrackRefId)
    }

    @androidx.annotation.RequiresApi(29)
    private fun importPlayableSample(title: String, binding: app.autplay.application.sync.ClientEventBinding? = null, artist: String = "AutPlay"): LocalId {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val trackId = LocalId.random()
        val resolver = context.contentResolver
        val metadata = ContentValues().apply {
            put(MediaStore.Audio.Media.DISPLAY_NAME, "autplay-test-${trackId.value}.wav")
            put(MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
            put(MediaStore.Audio.Media.IS_PENDING, 1)
        }
        val audio = requireNotNull(resolver.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, metadata))
        waveSamples += audio
        val fixture = Uri.parse("content://${instrumentation.context.packageName}.readable/audio/process-home")
        requireNotNull(resolver.openInputStream(fixture)).use { input ->
            requireNotNull(resolver.openOutputStream(audio)).use { output -> input.copyTo(output) }
        }
        resolver.update(audio, ContentValues().apply { put(MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
        val language = AppLanguage.Russian
        runBlocking {
            applicationNonSecretSettingsStore(context).update(NonSecretSettings(
                appLanguage = language.storedValue, onboardingRevision = CURRENT_ONBOARDING_REVISION, pendingPublicId = "activity_fixture",
                activeUserId = binding?.userId, activeServerProfileId = binding?.serverProfileId, deviceId = binding?.deviceId,
                serverBaseUrl = binding?.let { "https://offline.test" },
            ))
            LibraryVerticalSliceRepository(AutPlayRuntime.database(context)).importUri(
                binding = binding, trackRefId = trackId, libraryEntryId = LocalId.random(),
                audioStateId = LocalId.random(), changeId = LocalId.random(),
                title = title, artist = artist,
                inspection = ContentUriInspector(resolver).inspect(audio.toString()),
                persistedPermission = false, now = System.currentTimeMillis(),
            )
        }
        return trackId
    }

    @androidx.annotation.RequiresApi(29)
    private fun startLocalWave(title: String): LocalId {
        val trackId = importPlayableSample(title)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        val start = hasTestTag("home-my-wave") and isEnabled()
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(start).fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("home-wave-idle")
        composeRule.onNode(start).assertIsDisplayed().performClick()
        composeRule.waitUntil(20_000) { PlaybackRuntimeState.state.value.isPlaying }
        return trackId
    }

    @Test
    fun emptyLocalHomeShowsImportAndNoCollectionHeadings() {
        runBlocking {
            applicationNonSecretSettingsStore(context).update(NonSecretSettings(
                appLanguage = AppLanguage.Russian.storedValue, onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ))
        }
        scenario = ActivityScenario.launch(MainActivity::class.java)
        composeRule.waitUntil(15_000) { composeRule.onAllNodesWithTag("home-add-first-track").fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("home-add-first-track").assertIsDisplayed()
        val home = composeRule.onNodeWithTag("home-product-list").fetchSemanticsNode()
        val indexOf = home.config[SemanticsProperties.IndexForKey]
        for (key in listOf("home:new-releases", "home:recent-heading", "home:recommendations-heading",
                "home:playlists-heading", "home:offline-heading", "home:contexts-heading")) {
            org.junit.Assert.assertEquals(key, -1, indexOf(key))
        }
        saveUiScreenshot("home-empty")
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun homeWithoutRecommendationsOmitsAlbumsAndRecentRowPlaysLocalAudio() = runBlocking {
        val title = "Обратный путь"
        val trackId = importPlayableSample(title)
        val db = AutPlayRuntime.database(context)
        val recordingId = LocalId.random().value
        db.catalogProjectionDao().upsertRecordings(listOf(recording(recordingId, title)))
        db.libraryDao().upsertTrackRef(requireNotNull(db.libraryDao().trackRef(trackId.value)).copy(localRecordingId = recordingId))
        seedReleases(recordingId, listOf("Library album"))
        scenario = ActivityScenario.launch(MainActivity::class.java)
        composeRule.waitUntil(15_000) { composeRule.onAllNodesWithTag("home-product-list").fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-recent"))
        composeRule.onNodeWithTag("home-recent").assert(hasText(title)).assertIsDisplayed()
        composeRule.onNodeWithTag("home-release-strip").assertDoesNotExist()
        composeRule.onNodeWithTag("home-recent").performTouchInput { click() }
        composeRule.waitUntil(15_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == trackId.value }
        }
        composeRule.onNodeWithTag("now-playing-panel").assertIsDisplayed()
        Unit
    }

    @Test
    fun libraryRowRemainsVisibleAfterActivityRecreation() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        assertEmptyLibrary()
        runBlocking { seedLibraryTrack(PROFILE) }
        assertSingleTrackVisible()

        scenario?.recreate()

        openLibrary()
        assertSingleTrackVisible()
    }

    @Test
    fun previouslyLikedTrackWithoutMembershipAppearsInBothListsAndCanBeRemoved() = runBlocking {
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(onboardingRevision = CURRENT_ONBOARDING_REVISION, pendingPublicId = "activity_fixture"),
        )
        seedLibraryTrack("legacy-unscoped")
        val database = AutPlayRuntime.database(context)
        val liked = LocalId.random()
        val sample = requireNotNull(database.libraryDao().trackRef(TRACK))
        database.libraryDao().upsertTrackRef(sample.copy(localUserTrackRefId = liked.value, rawTitle = "Liked in player"))
        LibraryVerticalSliceRepository(database).setPlaybackPreference(null, liked, LocalId.random(), "LIKED", null, 2)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        val row = hasTestTag("library-track-${liked.value}")
        scrollLibraryTo(row)
        composeRule.onNode(row).assert(hasText("Liked in player"))
        composeRule.onNodeWithTag("library-favorites").performClick()
        scrollLibraryTo(row)
        composeRule.onNodeWithTag("library-track-$TRACK").assertDoesNotExist()
        composeRule.onNodeWithTag("library-actions-${liked.value}").performClick()
        composeRule.onNodeWithText(defaultStrings.getString(R.string.action_remove)).performClick()
        scrollLibraryTo(hasText(defaultStrings.getString(R.string.library_favorites_empty)))
        composeRule.onNodeWithTag("library-favorites").performClick()
        assertSingleTrackVisible()
        composeRule.onNode(row).assertDoesNotExist()
        org.junit.Assert.assertEquals("NEUTRAL", database.libraryDao().preference(liked.value)?.preference)
    }

    @Test
    fun freshLaunchWithoutServerConfigurationPersistsStandaloneChangeAcrossRecreation() = runBlocking {
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                onboardingRevision = CURRENT_ONBOARDING_REVISION,
                pendingPublicId = "activity_fixture",
            ),
        )
        scenario = ActivityScenario.launch(MainActivity::class.java)

        openLibrary()
        assertEmptyLibrary()
        seedLibraryTrack("legacy-unscoped")
        assertSingleTrackVisible()
        scenario?.recreate()
        openLibrary()
        assertSingleTrackVisible()
        Unit
    }

    @Test
    fun libraryTrackTapCreatesTheExactQueueAndKeepsLibraryOpen() = runBlocking {
        seedLibraryTrack(PROFILE)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        assertSingleTrackVisible()
        // The list extends behind bottom navigation; reveal the row before tapping its center.
        composeRule.onNodeWithTag("library-product-list").performTouchInput {
            swipeUp(startY = center.y)
        }
        saveUiScreenshot("track-row-library")
        composeRule.onNodeWithTag("library-track-$TRACK").performClick()

        composeRule.waitUntil(timeoutMillis = 10_000) {
            runBlocking {
                val dao = AutPlayRuntime.database(context).queueDao()
                val snapshot = dao.activeSnapshotOnce() ?: return@runBlocking false
                val entries = dao.entries(snapshot.queueSnapshotId, 10)
                entries.size == 1 && entries.single().localUserTrackRefId == TRACK &&
                    snapshot.currentEntryId == entries.single().queueEntryId
            }
        }
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        assertSingleTrackVisible()
        // This unavailable fixture proves queue activation and navigation, not audio playback.
        Unit
    }

    @Test
    fun libraryDownloadShortcutOpensDownloadsAndRestoresThatRoute() {
        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        composeRule.onNodeWithTag("library-downloads").assertIsDisplayed().performClick()
        assertDownloadsRoute()

        scenario?.recreate()

        assertDownloadsRoute()
    }

    @Test
    fun libraryBrowsesAlbumAndPlaylistGridsWithPersistentShortcuts() = runBlocking {
        val language = AppLanguage.Russian
        applicationNonSecretSettingsStore(context).mutate { it.copy(appLanguage = language.storedValue) }
        val strings = localizedAppContext(context, language)
        val database = AutPlayRuntime.database(context)
        val recordingId = LocalId.random().value
        database.catalogProjectionDao().upsertRecordings(listOf(recording(recordingId, "Offline sample")))
        seedLibraryTrack(PROFILE, recordingId)
        val artwork = "a".repeat(64)
        val metadata = """{"revision":1,"state":"READY","artwork_sha256":"$artwork"}"""
        database.trackMetadataDao().upsert(TrackMetadataEntity(PROFILE, TRACK, 1, metadata, artwork, 1))
        val albums = seedReleases(recordingId, listOf("Northern Lights", "After the Rain", "Night Train"))
        val playlists = listOf("Morning Walk", "Evening Drive", "On Repeat").mapIndexed { i, title ->
            val id = LocalId.random().value
            database.playlistDao().upsertPlaylist(PlaylistEntity(id, null, title, null, "PRIVATE", "MANUAL", null, null,
                "LOCAL_ONLY", null, 0, 1, i.toLong(), null, PROFILE))
            if (i != 0) database.playlistDao().upsertEntry(PlaylistEntryEntity(LocalId.random().value, null,
                id, TRACK, "A", "A", null, 1, "LOCAL_ONLY", null, 0, null, PROFILE))
            id to title
        }.reversed()
        val playlistCovers = database.playlistDao().libraryPlaylists(PROFILE, 10).first()
        org.junit.Assert.assertEquals(TRACK, playlistCovers.first().artworkTrackId)
        org.junit.Assert.assertNull(playlistCovers.last().artworkTrackId)
        org.junit.Assert.assertTrue(database.catalogProjectionDao().releasesForProfile(PROFILE, 10).first().all { it.artworkTrackId == TRACK })
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        composeRule.waitUntil(10_000) { composeRule.onAllNodesWithTag("library-track-$TRACK").fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("library-tracks")
        composeRule.onNodeWithTag("library-sort").performClick()
        composeRule.onNodeWithTag("library-sort-Title").performClick()
        composeRule.onNodeWithTag("library-sort").performClick()
        composeRule.onNodeWithTag("library-sort-Title").assert(isSelected())
        androidx.test.espresso.Espresso.pressBack()
        composeRule.onNodeWithTag("library-favorites").performClick()
        composeRule.onNodeWithText(strings.getString(R.string.library_favorites_empty)).assertIsDisplayed()
        composeRule.onNodeWithTag("library-favorites").performClick()
        composeRule.onNodeWithTag("library-track-$TRACK").assertIsDisplayed()
        for ((section, prefix, entries) in listOf(
            Triple("Albums", "library-album:", albums), Triple("Playlists", "library-playlist:", playlists),
        )) {
            composeRule.onNodeWithTag("library-sections").performScrollToNode(hasTestTag("library-tab-$section"))
            composeRule.onNodeWithTag("library-tab-$section").performClick()
            val cards = entries.map { composeRule.onNodeWithTag(prefix + it.first) }
            composeRule.waitUntil(10_000) { composeRule.onAllNodesWithTag(prefix + entries[0].first).fetchSemanticsNodes().isNotEmpty() }
            val grid = composeRule.onNodeWithTag("library-product-list")
            grid.performScrollToNode(hasTestTag(prefix + entries[0].first))
            val first = cards[0].getUnclippedBoundsInRoot()
            val second = cards[1].getUnclippedBoundsInRoot()
            org.junit.Assert.assertEquals(first.top, second.top)
            org.junit.Assert.assertTrue(first.right <= second.left)
            grid.performScrollToNode(hasTestTag(prefix + entries[2].first))
            cards[2].assertIsDisplayed()
            org.junit.Assert.assertEquals(first.left, cards[2].getUnclippedBoundsInRoot().left)
            composeRule.onNodeWithTag("library-downloads").assertIsDisplayed()
            composeRule.onNodeWithTag("library-add").assertIsDisplayed()
            saveUiScreenshot(if (section == "Albums") "library-albums" else "library-playlists")
            grid.performScrollToNode(hasTestTag(prefix + entries[1].first))
            cards[1].assertIsDisplayed().performTouchInput { click() }
            composeRule.waitUntil(10_000) {
                composeRule.onAllNodesWithTag("library-product-list").fetchSemanticsNodes().isEmpty() &&
                    composeRule.onAllNodesWithText(entries[1].second).fetchSemanticsNodes().isNotEmpty()
            }
            androidx.test.espresso.Espresso.pressBack()
            composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        }
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun searchShowsReadableLocalAudioAndStartsTheSelectedTrack() {
        val track = importPlayableSample("Тихий свет")
        val strings = localizedAppContext(context, AppLanguage.Russian)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_search)).performClick()
        composeRule.onNodeWithTag("unified-search-query").performTextInput("Тихий")
        composeRule.onNodeWithTag("local-search-submit").performClick()
        androidx.test.espresso.Espresso.closeSoftKeyboard()
        val tag = "local-result-${track.value}"
        val offline = hasTestTag(tag) and hasText(strings.getString(R.string.search_source_phone_offline))
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(offline).fetchSemanticsNodes().isNotEmpty() }
        saveUiScreenshot("search-phone")
        // A lost file and a restored file update an already open result without resubmitting the query.
        val audio = AutPlayRuntime.database(context).localAudioDao()
        val original = runBlocking { audio.statesForPlayback(track.value, 1).single() }
        runBlocking { audio.upsertState(original.copy(status = "MISSING")) }
        val metadata = hasTestTag(tag) and hasText(strings.getString(R.string.search_source_library_metadata))
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(metadata).fetchSemanticsNodes().isNotEmpty() }
        runBlocking { audio.upsertState(original) }
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(offline).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(offline).performClick()
        composeRule.waitUntil(15_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == track.value }
        }
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun jointArtistPickerOpensAbovePlayerAndQueueWithoutChangingPlayback() = runBlocking {
        val binding = app.autplay.application.sync.ClientEventBinding(
            UserId(LocalId.random().value), DeviceId(LocalId.random().value), ServerProfileId(PROFILE))
        val artists = listOf("SVRGN", "RAGETRAIN")
        val titles = listOf("BASS PESADÃO", "Second recording")
        val tracks = titles.map { importPlayableSample(it, binding, artists.joinToString(" & ")).value }
        val db = AutPlayRuntime.database(context)
        val fixture = seedCollectionPages(context, db, PROFILE, tracks, titles, artists)
        val strings = localizedAppContext(context, AppLanguage.Russian)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        fun tap(tag: String) {
            composeRule.waitUntil(15_000) { composeRule.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty() }
            composeRule.onNodeWithTag(tag).performClick()
        }
        fun assertPicker() {
            val row = hasTestTag("player-artist-${fixture.artistIds[1]}") and hasAnyAncestor(isDialog())
            composeRule.waitUntil(10_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }
            composeRule.onNode(row).assertIsDisplayed()
            composeRule.onNodeWithTag("player-artist-${fixture.artistIds[0]}").assertIsDisplayed()
        }
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        tap("library-track-${tracks[0]}")
        composeRule.waitUntil(15_000) { PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == tracks[0] } }
        val queue = requireNotNull(db.queueDao().activeSnapshotOnce())
        tap("mini-player-open-artists")
        assertPicker()
        androidx.test.espresso.Espresso.pressBack()
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput {
            click(Offset(10f, center.y))
        }
        tap("player-open-artist")
        assertPicker()
        androidx.test.espresso.Espresso.pressBack()
        tap("player-open-queue")
        tap("queue-open-artists")
        assertPicker()
        saveUiScreenshot("joint-artists-over-queue")
        androidx.test.espresso.Espresso.pressBack()
        tap("queue-open-artists")
        assertPicker()
        tap("player-artist-${fixture.artistIds[1]}")
        val heading = hasText("RAGETRAIN") and isHeading()
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(heading).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(heading).assertIsDisplayed()
        composeRule.onNodeWithTag("player-artist-picker").assertDoesNotExist()
        val after = requireNotNull(db.queueDao().activeSnapshotOnce())
        org.junit.Assert.assertEquals(queue.queueSnapshotId, after.queueSnapshotId)
        org.junit.Assert.assertEquals(queue.currentEntryId, after.currentEntryId)
        org.junit.Assert.assertTrue(PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == tracks[0] })
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun albumArtistAndPlaylistUseExactTracksAndCollectionQueues() = runBlocking {
        val binding = app.autplay.application.sync.ClientEventBinding(UserId(LocalId.random().value),
            DeviceId(LocalId.random().value), ServerProfileId(PROFILE))
        val names = listOf("Обратный путь", "Тихий свет")
        val tracks = names.map { importPlayableSample(it, binding).value }
        val db = AutPlayRuntime.database(context)
        val pages = seedCollectionPages(context, db, PROFILE, tracks, names)
        val strings = localizedAppContext(context, AppLanguage.Russian)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        fun tag(name: String) = composeRule.onNodeWithTag(name)
        fun awaitTag(name: String) = composeRule.waitUntil(15_000) {
            composeRule.onAllNodesWithTag(name).fetchSemanticsNodes().isNotEmpty()
        }
        fun openTile(tab: String, tile: String) {
            tag("library-sections").performScrollToNode(hasTestTag("library-tab-$tab"))
            tag("library-tab-$tab").performClick()
            awaitTag(tile)
            tag(tile).performClick()
            awaitTag("collection-listen")
        }
        fun checkCover() {
            composeRule.waitUntil(10_000) {
                val pixels = tag("collection-artwork").captureToImage().toPixelMap()
                val sun = pixels[pixels.width / 2, pixels.height / 2]
                val sea = pixels[pixels.width / 2, pixels.height * 9 / 10]
                sun.red > .9f && sun.green > .55f && sun.blue < .5f && sea.blue > sea.red
            }
        }
        fun back() = androidx.test.espresso.Espresso.pressBack()
        suspend fun checkQueue(collection: String, expected: List<String>, index: Int) {
            composeRule.waitUntil(15_000) {
                PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == expected[index] } &&
                    runBlocking { db.queueDao().activeSnapshotOnce()?.sourceContextId == collection }
            }
            val snapshot = requireNotNull(db.queueDao().activeSnapshotOnce())
            val entries = db.queueDao().entries(snapshot.queueSnapshotId, 20)
            org.junit.Assert.assertEquals(expected, entries.map { it.localUserTrackRefId })
            org.junit.Assert.assertEquals(entries[index].queueEntryId, snapshot.currentEntryId)
        }
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        openTile("Albums", "library-album:${pages.album}")
        tag("collection-artwork").assertIsDisplayed()
        tag("album-track-${pages.albumEntries[1]}").assertIsDisplayed()
        checkCover()
        saveUiScreenshot("detail-album")
        tag("collection-open-artist").performClick()
        awaitTag("artist-header")
        tag("collection-details").performScrollToNode(hasTestTag("artist-album-${pages.serverAlbum}"))
        back()
        awaitTag("collection-listen")
        tag("collection-listen").performClick()
        checkQueue(pages.album, tracks, 0)
        tag("album-track-${pages.albumEntries[1]}").performClick()
        checkQueue(pages.album, tracks, 1)
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        awaitTag("player-open-artist")
        tag("player-open-artist").performClick()
        awaitTag("artist-header")
        tag("collection-details").performScrollToNode(hasText(strings.getString(R.string.detail_artist_popular)))
        composeRule.onNodeWithText(strings.getString(R.string.detail_artist_popular)).assertIsDisplayed()
        tag("collection-details").performScrollToNode(hasTestTag("artist-track-${pages.recordings[1]}"))
        tag("artist-track-${pages.recordings[1]}").assertIsDisplayed()
        saveUiScreenshot("detail-artist")
        tag("collection-details").performScrollToNode(hasText(strings.getString(R.string.detail_artist_info)))
        composeRule.onNodeWithText(strings.getString(R.string.detail_artist_info)).performClick()
        tag("collection-details").performScrollToNode(hasText(strings.getString(R.string.detail_artist_credits)))
        composeRule.onNodeWithText(strings.getString(R.string.detail_artist_credits)).assertIsDisplayed()
        tag("collection-details").performScrollToNode(hasTestTag("artist-album-${pages.serverAlbum}"))
        tag("artist-album-${pages.serverAlbum}").performClick()
        awaitTag("collection-listen")
        back()
        awaitTag("collection-details")
        tag("collection-details").performScrollToNode(hasTestTag("artist-album-${pages.serverAlbum}"))
        back()
        awaitTag("player-open-artist")
        back()
        awaitTag("collection-listen")
        back()
        openTile("Playlists", "library-playlist:${pages.playlist}")
        tag("playlist-entry-${pages.playlistEntries.last()}").assertIsDisplayed()
        checkCover()
        saveUiScreenshot("detail-playlist")
        tag("collection-listen").performClick()
        val sequence = listOf(tracks[0], tracks[1], tracks[0])
        checkQueue(pages.playlist, sequence, 0)
        tag("playlist-entry-${pages.playlistEntries.last()}").performClick()
        composeRule.waitUntil(10_000) {
            runBlocking {
                val current = db.queueDao().activeSnapshotOnce() ?: return@runBlocking false
                db.queueDao().entries(current.queueSnapshotId, 10).lastOrNull()?.queueEntryId == current.currentEntryId
            }
        }
        checkQueue(pages.playlist, sequence, 2)
        tag("playlist-toggle-edit").performClick()
        composeRule.onNodeWithText(strings.getString(R.string.playlist_rename)).assertIsDisplayed()
        tag("playlist-toggle-edit").performClick()
        tag("playlist-entry-${pages.playlistEntries.last()}").assertIsDisplayed()
        Unit
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun playerBackdropFollowsArtworkAndThemeDuringPlayback() = runBlocking {
        // The library queue is newest first. All three entries play real local audio.
        val noCover = importPlayableSample("Тишина").value
        val warm = importPlayableSample("Вечерний свет").value
        val cool = importPlayableSample("Ночной берег").value
        val db = AutPlayRuntime.database(context)
        seedSunsetArtwork(context, db, "legacy-unscoped", cool)
        seedSunsetArtwork(context, db, "legacy-unscoped", warm, warm = true)
        val settings = applicationNonSecretSettingsStore(context)
        val strings = localizedAppContext(context, AppLanguage.Russian)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        val row = hasTestTag("library-track-$cool")
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(row).performClick()
        fun awaitPlayback(id: String) = composeRule.waitUntil(15_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == id }
        }
        awaitPlayback(cool)
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        val panel = composeRule.onNodeWithTag("now-playing-panel")
        val next = composeRule.onNodeWithContentDescription(strings.getString(R.string.action_next))
        fun checkBackdrop(light: Boolean, label: String, matches: (Color) -> Boolean) {
            panel.assertIsDisplayed()
            var header = Color.Transparent
            var footer = Color.Transparent
            composeRule.waitUntil(10_000) {
                val pixels = panel.captureToImage().toPixelMap()
                header = pixels[2, 2]
                footer = pixels[2, pixels.height - 3]
                matches(header) && (if (light) footer.luminance() > .65f else footer.luminance() < .15f)
            }
            fun contrast(foreground: Color, background: Color): Float {
                val a = foreground.luminance() + .05f
                val b = background.luminance() + .05f
                return maxOf(a, b) / minOf(a, b)
            }
            val scheme = autPlayColorScheme(!light, AutPlayAccent.Coral)
            org.junit.Assert.assertTrue("$label header: $header", contrast(scheme.onSurface, header) >= 4.5f)
            org.junit.Assert.assertTrue("$label controls: $footer", contrast(scheme.onSurfaceVariant, footer) >= 4.5f)
            composeRule.onNodeWithTag("player-transport-controls").assertIsDisplayed()
            composeRule.onNodeWithContentDescription(strings.getString(R.string.player_seek_description)).assertIsDisplayed()
            saveUiScreenshot(label)
        }
        checkBackdrop(false, "player-cool-dark") { it.blue - it.red > .08f }
        next.performClick()
        awaitPlayback(warm)
        checkBackdrop(false, "player-warm-dark") { it.red - it.blue > .18f }
        settings.mutate { it.copy(appearanceMode = "LIGHT") }
        checkBackdrop(true, "player-warm-light") { it.red - it.blue > .1f }
        next.performClick()
        awaitPlayback(noCover)
        checkBackdrop(true, "player-no-cover") { it == autPlayColorScheme(false, AutPlayAccent.Coral).background }
        Unit
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun fullscreenArtworkOpensAndReturnsDuringPlayback() {
        val withoutArt = importPlayableSample("После заката").value
        val withArt = importPlayableSample("Ночной берег").value
        runBlocking { seedSunsetArtwork(context, AutPlayRuntime.database(context), "legacy-unscoped", withArt) }
        val strings = localizedAppContext(context, AppLanguage.Russian)
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        val row = hasTestTag("library-track-$withArt")
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(row).performClick()
        composeRule.waitUntil(15_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == withArt }
        }
        val queue = PlaybackRuntimeState.state.value.queueSnapshotId
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }
        val cover = composeRule.onNodeWithTag("player-cover")
        composeRule.waitUntil(10_000) {
            val pixels = cover.captureToImage().toPixelMap()
            val sun = pixels[pixels.width / 2, pixels.height / 2]
            sun.red > .9f && sun.green in 0.5f..0.8f
        }
        saveUiScreenshot("artwork-player")
        cover.performTouchInput { click() }
        val viewer = composeRule.onNodeWithTag("fullscreen-artwork")
        val image = composeRule.onNodeWithTag("fullscreen-artwork-image")
        viewer.assertIsDisplayed()
        image.assert(hasContentDescription("Ночной берег"))
        val screen = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        try {
            val frame = viewer.fetchSemanticsNode().boundsInRoot
            val art = image.fetchSemanticsNode().boundsInRoot
            org.junit.Assert.assertEquals(screen.width.toFloat(), frame.width, 1f)
            org.junit.Assert.assertEquals(screen.height.toFloat(), frame.height, 1f)
            org.junit.Assert.assertEquals(minOf(frame.width, frame.height), art.width, 1f)
            org.junit.Assert.assertEquals(art.width, art.height, 1f)
            org.junit.Assert.assertEquals(frame.center.y, art.center.y, 1f)
        } finally { screen.recycle() }
        val session = androidx.media3.session.SessionToken(context, android.content.ComponentName(context, AutPlayPlaybackService::class.java))
        val controller = androidx.media3.session.MediaController.Builder(context, session)
            .buildAsync().get(5, java.util.concurrent.TimeUnit.SECONDS)
        try {
            val start = composeRule.runOnUiThread { controller.currentPosition }
            composeRule.waitUntil(5_000) {
                composeRule.runOnUiThread { controller.isPlaying && controller.currentPosition >= start + 250 }
            }
        } finally { composeRule.runOnUiThread { controller.release() } }
        saveUiScreenshot("artwork-fullscreen")

        androidx.test.espresso.Espresso.pressBack()
        viewer.assertDoesNotExist()
        composeRule.onNodeWithTag("now-playing-panel").assertIsDisplayed()
        org.junit.Assert.assertTrue(PlaybackRuntimeState.state.value.isPlaying)
        org.junit.Assert.assertEquals(withArt, PlaybackRuntimeState.state.value.localUserTrackRefId)
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_pause)).performClick()
        composeRule.waitUntil(5_000) { !PlaybackRuntimeState.state.value.isPlaying }
        cover.performTouchInput { click() }
        viewer.assertIsDisplayed()
        // A media command can change the song while its artwork is being viewed.
        runBlocking { ServicePlaybackSessionOwner(context).dispatch(PlaybackCommand.Next) }
        composeRule.waitUntil(10_000) {
            composeRule.onAllNodes(hasTestTag("fullscreen-artwork-image") and hasContentDescription("После заката"))
                .fetchSemanticsNodes().isNotEmpty()
        }
        image.performTouchInput { click() }
        viewer.assertDoesNotExist()
        cover.assertIsDisplayed()
        org.junit.Assert.assertEquals(withoutArt, PlaybackRuntimeState.state.value.localUserTrackRefId)
        org.junit.Assert.assertFalse(PlaybackRuntimeState.state.value.isPlaying)
        org.junit.Assert.assertEquals(queue, PlaybackRuntimeState.state.value.queueSnapshotId)
    }

    @Test
    @SdkSuppress(minSdkVersion = 29)
    fun myWaveToFullscreenArtworkAcrossMusicScreens() = runBlocking {
        val binding = app.autplay.application.sync.ClientEventBinding(UserId(LocalId.random().value),
            DeviceId(LocalId.random().value), ServerProfileId(PROFILE))
        val titles = listOf("Обратный путь", "Тихий свет")
        val tracks = titles.map { importPlayableSample(it, binding).value }
        val db = AutPlayRuntime.database(context)
        val pages = seedCollectionPages(context, db, PROFILE, tracks, titles)
        tracks.forEach { db.refreshTrackSearch(it) }
        val strings = localizedAppContext(context, AppLanguage.Russian)

        fun node(tag: String) = composeRule.onNodeWithTag(tag)
        fun shown(tag: String) = node(tag).also {
            composeRule.waitUntil(15_000) { composeRule.onAllNodesWithTag(tag).fetchSemanticsNodes().isNotEmpty() }
            it.assertIsDisplayed()
        }
        fun awaitTrack(id: String) = composeRule.waitUntil(15_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == id }
        }
        fun back() = androidx.test.espresso.Espresso.pressBack()

        scenario = ActivityScenario.launch(MainActivity::class.java)
        val wave = hasTestTag("home-my-wave") and isEnabled()
        composeRule.waitUntil(15_000) { composeRule.onAllNodes(wave).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNode(wave).performClick()
        composeRule.waitUntil(20_000) {
            PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId in tracks }
        }
        val waveQueue = requireNotNull(PlaybackRuntimeState.state.value.queueSnapshotId)
        node("persistent-mini-player").assertDoesNotExist()
        saveUiScreenshot("journey-wave")
        val home = node("home-product-list")
        home.performScrollToNode(hasTestTag("home-recent"))
        composeRule.onAllNodesWithTag("home-recent")[0].assertIsDisplayed()
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        node("library-sections").performScrollToNode(hasTestTag("library-tab-Albums"))
        shown("library-tab-Albums").performClick()
        shown("library-album:${pages.album}").performClick()
        shown("album-track-${pages.albumEntries.last()}")
        shown("collection-open-artist").performClick()
        shown("artist-track-${pages.recordings.last()}")
        shown("artist-album-${pages.serverAlbum}")
        back()
        shown("collection-listen")
        back()

        waitForNavigation(strings.getString(R.string.nav_search)).performClick()
        shown("unified-search-query").performTextInput("Тихий")
        node("local-search-submit").performClick()
        androidx.test.espresso.Espresso.closeSoftKeyboard()
        val result = shown("local-result-${tracks.last()}")
        result.assert(hasText(strings.getString(R.string.search_source_phone_offline)))
        org.junit.Assert.assertEquals(waveQueue, PlaybackRuntimeState.state.value.queueSnapshotId)
        result.performClick()
        awaitTrack(tracks.last())

        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        shown("library-track-${tracks.last()}")
        shown("library-favorites")
        shown("library-downloads")
        shown("library-sort")
        shown("library-add")
        shown("library-sections").performScrollToNode(hasTestTag("library-tab-Playlists"))
        node("library-tab-Playlists").performClick()
        shown("library-playlist:${pages.playlist}").performClick()
        shown("collection-listen").performClick()
        awaitTrack(tracks.first())
        val playlistQueue = requireNotNull(db.queueDao().activeSnapshotOnce())
        org.junit.Assert.assertEquals(pages.playlist, playlistQueue.sourceContextId)
        val mini = shown("persistent-mini-player")
        mini.performTouchInput { swipeLeft() }
        awaitTrack(tracks.last())
        mini.performTouchInput { swipeRight() }
        awaitTrack(tracks.first())
        mini.performTouchInput { swipeLeft() }
        awaitTrack(tracks.last())
        composeRule.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click() }

        val panel = shown("now-playing-panel")
        val scroll = node("now-playing-scroll").fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange]
        org.junit.Assert.assertEquals(0f, scroll.maxValue(), 0f)
        shown("player-transport-controls")
        shown("player-open-queue").performClick()
        shown("queue-editor")
        back()
        shown("player-sleep-timer")
        shown("player-more").performClick()
        shown("player-recommendations")
        back()
        composeRule.waitUntil(10_000) {
            val color = panel.captureToImage().toPixelMap()[2, 2]
            color.blue - color.red > .08f
        }
        saveUiScreenshot("journey-player")
        shown("player-cover").performTouchInput { click() }
        val image = shown("fullscreen-artwork-image").assert(hasContentDescription(titles.last()))
        val pixels = image.captureToImage().toPixelMap()
        val sun = pixels[pixels.width / 2, pixels.height / 2]
        org.junit.Assert.assertTrue(sun.red > .9f && sun.green in 0.5f..0.8f)
        saveUiScreenshot("journey-fullscreen")
        back()
        shown("now-playing-panel")
        awaitTrack(tracks.last())
        org.junit.Assert.assertEquals(playlistQueue.queueSnapshotId, PlaybackRuntimeState.state.value.queueSnapshotId)
    }

    @Test
    fun completedLocalSearchRestoresResultsAfterActivityRecreation() = runBlocking {
        seedLibraryTrack(PROFILE)
        AutPlayRuntime.database(context).refreshTrackSearch(TRACK)
        scenario = ActivityScenario.launch(MainActivity::class.java)

        waitForNavigation(defaultStrings.getString(R.string.nav_search)).performClick()
        composeRule.onNodeWithTag("unified-search-query").performTextInput("Offline sample")
        composeRule.onNodeWithTag("local-search-submit").performClick()
        val row = hasTestTag("local-result-$TRACK") and hasText("Offline sample")
        composeRule.waitUntil(10_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }

        scenario?.recreate()

        composeRule.waitUntil(10_000) { composeRule.onAllNodes(row).fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("unified-search-query").assertTextContains("Offline sample")
        Unit
    }

    @Test
    fun importScreenExplainsAmbiguousCandidatesAndAppliesManualAccept() = runBlocking {
        val database = AutPlayDatabase.open(context)
        val repository = LocalImportReviewRepository(database)
        database.catalogProjectionDao().upsertRecordings(listOf(recording("71111111-1111-4111-8111-111111111111", "Studio"), recording("72111111-1111-4111-8111-111111111111", "Live")))
        val job = repository.createOrResume(
            CreateLocalImportCommand(
                serverProfileId = "51111111-1111-4111-8111-111111111111",
                adapterId = "ui-fixture",
                adapterVersion = "1",
                envelopeVersion = 1,
                inputSha256 = "c".repeat(64),
                rows = listOf(ImportRowInput("row:0", 0, "Ambiguous", "Artist", rawProvenanceJson = "{\"schema_version\":1}")),
                nowMs = 1,
            ),
        )
        val entry = repository.entriesOnce(job.importJobId).single()
        repository.recordShadowEvaluation(
            RecordShadowEvaluationCommand(
                importEntryId = entry.importEntryId,
                idempotencyKey = "ui-eval",
                resolverState = ImportResolverState.REVIEW_REQUIRED,
                evidenceMode = "METADATA_ONLY",
                matcherVersion = "ui-shadow/1",
                explanationJson = "{\"schema_version\":1,\"reason_code\":\"VERSION_CONFLICT\"}",
                candidates = listOf(candidate("71111111-1111-4111-8111-111111111111", 1, "Studio"), candidate("72111111-1111-4111-8111-111111111111", 2, "Live")),
                nowMs = 2,
            ),
        )
        database.close()

        scenario = ActivityScenario.launch(MainActivity::class.java)
        openLibrary()
        composeRule.onNodeWithTag("library-tab-Review").assertDoesNotExist()
        waitForNavigation(defaultStrings.getString(R.string.nav_home)).performClick()
        val openReview = hasText(defaultStrings.getString(R.string.home_problems_open)) and hasClickAction()
        composeRule.onNodeWithTag("home-product-list").performScrollToNode(openReview)
        composeRule.onNode(openReview).performClick()
        composeRule.onNodeWithTag("library-product-list").assertDoesNotExist()
        val importSummary = defaultStrings.getString(R.string.import_summary, 1)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(importSummary).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText(importSummary).performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithText(defaultStrings.getString(R.string.import_review_item)).performScrollTo().performClick()
        val conflict = defaultStrings.getString(R.string.import_candidate_conflict)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(conflict).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onAllNodesWithText(conflict)[0]
            .performScrollTo()
            .assertIsDisplayed()
        composeRule.onNodeWithText(defaultStrings.getString(R.string.import_accept_candidate, 1)).performScrollTo().performClick()
        val resolvedSummary = defaultStrings.getString(R.string.import_review_summary, 0, 1, 0, 0)
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(resolvedSummary).fetchSemanticsNodes().isNotEmpty()
        }
    }

    @Test
    fun profileSettingsShareOneEntryAndReturnToTheirSource() = runBlocking {
        val language = AppLanguage.Russian
        applicationNonSecretSettingsStore(context).update(NonSecretSettings(
            onboardingRevision = CURRENT_ONBOARDING_REVISION,
            pendingPublicId = "meloman",
            appLanguage = language.storedValue,
        ))
        val strings = localizedAppContext(context, language)
        fun back() = composeRule.onNodeWithContentDescription(strings.getString(R.string.action_back)).performClick()
        fun row(route: String) = composeRule.onNodeWithTag("settings-row-$route")
        seedLibraryTrack("legacy-unscoped")
        scenario = ActivityScenario.launch(MainActivity::class.java)
        waitForNavigation(strings.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithTag("library-sections").performScrollToNode(hasTestTag("library-tab-Albums"))
        composeRule.onNodeWithTag("library-tab-Albums").performClick()
        saveUiScreenshot("settings-entry")
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_open_profile_settings)).performClick()
        composeRule.onNodeWithContentDescription(strings.getString(R.string.action_open_settings)).assertDoesNotExist()
        row("profile").assertTextContains("meloman")
        row("settings-account").assertTextContains(strings.getString(R.string.settings_account_local_summary))
        row("settings-downloads").assertDoesNotExist()
        saveUiScreenshot("settings-hub")

        row("profile").performClick()
        composeRule.onNodeWithText(strings.getString(R.string.statistics_title)).assertIsDisplayed()
        composeRule.onNodeWithText(strings.getString(R.string.profile_friends)).assertExists()
        composeRule.onNodeWithText(strings.getString(R.string.portable_account_title)).assertDoesNotExist()
        saveUiScreenshot("settings-profile")
        back()
        row("settings-music").performScrollTo().performClick()
        composeRule.onNodeWithContentDescription(strings.getString(R.string.settings_metered_downloads)).assertIsDisplayed()
        saveUiScreenshot("settings-music")
        composeRule.onNodeWithTag("settings-add-audio").performScrollTo().assertIsDisplayed()
        back()
        row("settings-playback").performScrollTo().performClick()
        composeRule.onNodeWithText(strings.getString(R.string.settings_smooth_track_transitions)).assertIsDisplayed()
        saveUiScreenshot("settings-sound")
        scenario?.onActivity { it.onBackPressedDispatcher.onBackPressed() }
        row("profile").assertExists()
        back()
        composeRule.onNode(hasTestTag("library-tab-Albums") and isSelected()).assertIsDisplayed()

        waitForNavigation(strings.getString(R.string.nav_home)).performClick()
        composeRule.waitUntil(10_000) { composeRule.onAllNodesWithTag("home-listen-together").fetchSemanticsNodes().isNotEmpty() }
        composeRule.onNodeWithTag("home-listen-together").performClick()
        composeRule.onNodeWithTag("wave-connect").performClick()
        composeRule.onNodeWithText(strings.getString(R.string.settings_account_devices)).assertIsDisplayed()
        composeRule.onNodeWithText(strings.getString(R.string.portable_account_title)).assertExists()
        back()
        composeRule.onNodeWithTag("wave-connect").assertIsDisplayed()
        Unit
    }

    private fun waitForNavigation(label: String) = composeRule.onNode(hasText(label) and hasClickAction()).also {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithText(label).fetchSemanticsNodes().isNotEmpty()
        }
    }

    private fun candidate(id: String, rank: Int, title: String) = MatchCandidateInput(
        localRecordingId = id,
        rank = rank,
        rawScore = 0.9,
        confidence = 0.9,
        evidenceTier = "T1",
        titleSnapshot = title,
        artistSnapshot = "Artist",
        featureEvidenceJson = "[{\"feature\":\"title_similarity\",\"present\":true,\"value\":1.0,\"extractor_version\":\"title/1\"}]",
        hardConflictsJson = "[\"VERSION_MARKER_CONFLICT\"]",
        candidateOriginsJson = "[{\"generator\":\"metadata\",\"rank\":$rank}]",
        extractorVersionsJson = "{\"schema_version\":1}",
    )

    private fun openLibrary() {
        waitForNavigation(defaultStrings.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        composeRule.onNode(
            hasText(defaultStrings.getString(R.string.library_section_offline)) and hasClickAction(),
        ).assertDoesNotExist()
        composeRule.onNode(
            hasText(defaultStrings.getString(R.string.library_filter_unavailable)) and hasClickAction(),
        ).assertDoesNotExist()
    }

    private fun scrollLibraryTo(matcher: SemanticsMatcher) {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            runCatching {
                composeRule.onNodeWithTag("library-product-list").performScrollToNode(matcher)
            }.isSuccess
        }
    }

    private fun assertEmptyLibrary() {
        val empty = defaultStrings.getString(R.string.library_empty_body)
        scrollLibraryTo(hasText(empty))
        composeRule.onNodeWithText(empty).assertIsDisplayed()
        composeRule.onAllNodesWithTag("library-track-$TRACK").assertCountEquals(0)
    }

    private fun assertSingleTrackVisible() {
        scrollLibraryTo(hasTestTag("library-track-$TRACK"))
        composeRule.onAllNodesWithTag("library-track-$TRACK").assertCountEquals(1)
        composeRule.onNodeWithTag("library-track-$TRACK")
            .assert(hasText("Offline sample"))
            .assert(hasText("AutPlay test"))
            .assertIsDisplayed()
    }

    private fun assertDownloadsRoute() {
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule.onAllNodesWithTag("downloads-list").fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag("downloads-list").assertIsDisplayed()
        val empty = defaultStrings.getString(R.string.downloads_empty)
        composeRule.onNodeWithTag("downloads-list").performScrollToNode(hasText(empty))
        composeRule.onNodeWithText(empty).assertIsDisplayed()
        composeRule.onNodeWithTag("library-product-list").assertDoesNotExist()
    }

    private suspend fun seedReleases(recordingId: String, titles: List<String>): List<Pair<String, String>> {
        val database = AutPlayRuntime.database(context)
        return titles.mapIndexed { i, title ->
            val id = LocalId.random().value
            database.catalogProjectionDao().upsertReleases(listOf(ReleaseProjectionEntity(
                id, null, null, title, "AutPlay test", null, "ALBUM", null, 1, i.toLong(), false)))
            database.catalogProjectionDao().upsertReleaseTracks(listOf(ReleaseTrackProjectionEntity(
                LocalId.random().value, null, id, recordingId, 1, 1, null, "Offline sample", "AutPlay test", null)))
            id to title
        }.reversed()
    }

    private suspend fun seedLibraryTrack(profileId: String, recordingId: String? = null) {
        val database = AutPlayRuntime.database(context)
        database.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = TRACK,
                serverUserTrackRefId = null,
                localRecordingId = recordingId,
                serverRecordingId = null,
                resolutionStatus = "UNRESOLVED",
                rawTitle = "Offline sample",
                rawArtist = "AutPlay test",
                rawAlbum = null,
                rawDurationMs = null,
                resolutionConfidence = null,
                syncState = "LOCAL_ONLY",
                serverRowVersion = null,
                lastLocalSequence = 0,
                createdAtMs = 1,
                updatedAtMs = 1,
                deletedAtMs = null,
                serverProfileId = profileId,
            ),
        )
        database.libraryDao().upsertEntry(
            LibraryEntryEntity(
                localLibraryEntryId = ENTRY,
                serverLibraryEntryId = null,
                localUserTrackRefId = TRACK,
                addedAtMs = 1,
                source = "TEST_FIXTURE",
                availabilityStatus = "UNAVAILABLE",
                syncState = "LOCAL_ONLY",
                serverRowVersion = null,
                lastLocalSequence = 0,
                removedAtMs = null,
                updatedAtMs = 1,
                serverProfileId = profileId,
            ),
        )
    }

    private fun recording(id: String, title: String) = RecordingProjectionEntity(
        localRecordingId = id,
        serverRecordingId = null,
        redirectServerRecordingId = null,
        title = title,
        normalizedTitle = title.lowercase(),
        displayArtist = "Artist",
        normalizedArtist = "artist",
        artistCreditJson = "{\"schema_version\":1}",
        durationMs = 180_000,
        recordingKind = "SONG",
        versionText = null,
        explicitState = 0,
        artworkRef = null,
        catalogVersion = 0,
        projectionUpdatedAtMs = 1,
    )

    private companion object {
        const val PROFILE = "51111111-1111-4111-8111-111111111111"
        const val TRACK = "91111111-1111-4111-8111-111111111111"
        const val ENTRY = "92111111-1111-4111-8111-111111111111"
    }
}
