package app.autplay

import android.content.res.Configuration
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.releasewatch.OkHttpReleaseFeedPort
import app.autplay.application.releasewatch.ReleaseFeedAction
import app.autplay.application.releasewatch.ReleaseFeedState
import app.autplay.application.releasewatch.WatchReason
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import app.autplay.ui.AutPlayAppearance
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.AutPlayThemeMode
import app.autplay.ui.HomeProductScreen
import app.autplay.ui.HomeReleaseFeedUiItem
import app.autplay.ui.HomeReleaseFeedUiState
import app.autplay.ui.HomeReleaseFeedUiStatus
import app.autplay.ui.HomeReleaseUiItem
import app.autplay.ui.HomeScreenUiState
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Actual Compose rendering, with controlled HTTP through production state/transport for paging.
 * This is not MainActivity wiring, a real backend, or hands-on TalkBack qualification.
 */
class HomeReleaseFeedScreenTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private val displayed = mutableStateOf(HomeReleaseFeedUiState())
    private var server: MockWebServer? = null
    private val clients = mutableListOf<OkHttpClient>()
    private val played = mutableListOf<String>()
    private val discoveries = mutableListOf<String>()
    private var syncs = 0
    private var retries = 0
    private var impressions = 0
    private var positiveFeedback = 0

    @After fun closeServer() {
        clients.forEach { it.dispatcher.cancelAll() }
        server?.shutdown()
        clients.forEach { it.connectionPool.evictAll(); it.dispatcher.executorService.shutdown() }
    }

    @Test fun disconnectedLoadingEmptyAndFailuresRenderTheirOwnMessageAndRetryPolicy() {
        render()
        val cases = listOf(
            Triple(HomeReleaseFeedUiStatus.Disconnected, R.string.release_feed_disconnected, false),
            Triple(HomeReleaseFeedUiStatus.Loading, R.string.release_feed_loading, false),
            Triple(HomeReleaseFeedUiStatus.Ready, R.string.home_empty_releases, false),
            Triple(HomeReleaseFeedUiStatus.Offline, R.string.release_feed_offline, true),
            Triple(HomeReleaseFeedUiStatus.Session, R.string.release_feed_session, true),
            Triple(HomeReleaseFeedUiStatus.Unavailable, R.string.release_feed_unavailable, true),
            Triple(HomeReleaseFeedUiStatus.Error, R.string.release_feed_error, true),
        )
        cases.forEach { (status, message, retry) ->
            compose.runOnIdle { displayed.value = HomeReleaseFeedUiState(status) }
            show(context.getString(message)).assertIsDisplayed()
            // A bound owner's failed feed must not fall back to the supplied local/global list.
            compose.onNodeWithText("Local fallback sentinel").assertDoesNotExist()
            if (retry) {
                show(context.getString(R.string.action_retry)).assertIsEnabled().performClick()
            } else {
                compose.onNodeWithText(context.getString(R.string.action_retry)).assertDoesNotExist()
            }
        }
        compose.runOnIdle { assertEquals(4, retries) }
    }

    @Test fun releaseActionsUseExactEvidenceOrLocalReferenceAndUnsyncedReadyOpensSync() {
        render()
        val cases = listOf(
            item(1, ReleaseFeedState.DISCOVERED, ReleaseFeedAction.OPEN_DISCOVERY) to R.string.release_feed_open_discovery,
            item(2, ReleaseFeedState.REVIEW_REQUIRED, ReleaseFeedAction.REVIEW, WatchReason.LISTENING_HISTORY) to R.string.release_feed_open_review,
            item(3, ReleaseFeedState.READY, ReleaseFeedAction.PLAY, localId = "local-owner-track") to R.string.action_play,
            item(4, ReleaseFeedState.READY, ReleaseFeedAction.PLAY) to R.string.release_feed_sync_to_play,
        )
        cases.forEach { (release, action) ->
            compose.runOnIdle { displayed.value = HomeReleaseFeedUiState(HomeReleaseFeedUiStatus.Ready, listOf(release)) }
            show(release.title).assertIsDisplayed()
            val reason = if (release.reason == WatchReason.MANUAL_FOLLOW) R.string.release_feed_manual_reason else R.string.release_feed_history_reason
            show(context.getString(reason)).assertIsDisplayed()
            val statusLabel = when (release.state) {
                ReleaseFeedState.DISCOVERED -> R.string.release_feed_discovered
                ReleaseFeedState.REVIEW_REQUIRED -> R.string.release_feed_review_required
                else -> R.string.release_feed_ready
            }
            show(context.getString(statusLabel)).assertIsDisplayed()
            show(context.getString(action)).assertIsEnabled().performClick()
        }
        compose.runOnIdle {
            assertEquals(listOf(id(101), id(102)), discoveries)
            assertEquals(listOf("local-owner-track"), played)
            assertEquals(1, syncs)
            assertEquals(0, impressions)
            assertEquals(0, positiveFeedback)
        }
    }

    @Test fun acquiringUnavailableAndFailedCardsHaveNoReleaseAction() {
        render()
        listOf(
            ReleaseFeedState.ACQUIRING to R.string.release_feed_acquiring,
            ReleaseFeedState.UNAVAILABLE to R.string.release_feed_item_unavailable,
            ReleaseFeedState.FAILED to R.string.release_feed_failed,
        ).forEachIndexed { index, (state, label) ->
            compose.runOnIdle { displayed.value = HomeReleaseFeedUiState(HomeReleaseFeedUiStatus.Ready, listOf(item(index, state, ReleaseFeedAction.NONE))) }
            show(context.getString(label)).assertIsDisplayed()
            listOf(R.string.action_play, R.string.release_feed_open_discovery, R.string.release_feed_open_review, R.string.release_feed_sync_to_play).forEach {
                compose.onNodeWithText(context.getString(it)).assertDoesNotExist()
            }
        }
        compose.runOnIdle { assertTrue(played.isEmpty()); assertTrue(discoveries.isEmpty()); assertEquals(0, syncs) }
    }

    @Test fun realHttpPagesAppendWithoutDuplicatesAndExhaustionRemovesMore() {
        val endpoint = endpoint()
        endpoint.enqueue(MockResponse().setBody(page((1..20).map { wireItem(it) }, "opaque+/=")))
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(20), wireItem(21)), null)))
        val state = networkState(endpoint)
        render(state)
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.READY }
        show(context.getString(R.string.release_feed_more)).performClick()
        compose.waitUntil(10_000) { state.items.size == 21 && !state.loadingMore }
        show("Release 21").assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.release_feed_more)).assertDoesNotExist()
        compose.runOnIdle { assertEquals((1..21).map(::id), state.items.map { it.candidateId }) }
        val first = endpoint.takeRequest(2, TimeUnit.SECONDS)
        val second = endpoint.takeRequest(2, TimeUnit.SECONDS)
        assertNotNull(first); assertNotNull(second)
        assertNull(first!!.requestUrl!!.queryParameter("cursor"))
        assertEquals("opaque+/=", second!!.requestUrl!!.queryParameter("cursor"))
        assertEquals("Bearer owner-token", second.getHeader("Authorization"))
        assertEquals("no-store", second.getHeader("Cache-Control"))
        assertEquals(2, endpoint.requestCount)
    }

    @Test fun failedNextPageDisablesExistingActionAndRenderedRetryStartsNewSnapshot() {
        val endpoint = endpoint()
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(1, "READY", "PLAY")), "next-page")))
        endpoint.enqueue(MockResponse().setResponseCode(500).setBody("{}"))
        endpoint.enqueue(MockResponse().setBody(page(emptyList(), null)))
        val state = networkState(endpoint)
        render(state)
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.READY }
        show(context.getString(R.string.release_feed_more)).performClick()
        compose.waitUntil(10_000) { state.pageFailure == HomeReleaseFeedStatus.ERROR }
        show("Release 1").assertIsDisplayed()
        show(context.getString(R.string.release_feed_sync_to_play)).assertIsNotEnabled()
        show(context.getString(R.string.release_feed_error)).assertIsDisplayed()
        show(context.getString(R.string.action_retry)).performClick()
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.READY && state.items.isEmpty() }
        show(context.getString(R.string.home_empty_releases)).assertIsDisplayed()
        compose.onNodeWithText("Release 1").assertDoesNotExist()
        val requests = (1..3).map { checkNotNull(endpoint.takeRequest(2, TimeUnit.SECONDS)) }
        assertNull(requests[0].requestUrl!!.queryParameter("cursor"))
        assertEquals("next-page", requests[1].requestUrl!!.queryParameter("cursor"))
        assertNull(requests[2].requestUrl!!.queryParameter("cursor"))
        compose.runOnIdle { assertEquals(0, syncs); assertTrue(played.isEmpty()) }
    }

    @Test fun unavailableNextPageClearsOldCardsAndSessionRefusalRendersSignIn() {
        val endpoint = endpoint()
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(1)), "next-page")))
        endpoint.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
        endpoint.enqueue(MockResponse().setResponseCode(403).setBody("{}"))
        val state = networkState(endpoint)
        render(state)
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.READY }
        show(context.getString(R.string.release_feed_more)).performClick()
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.UNAVAILABLE }
        show(context.getString(R.string.release_feed_unavailable)).assertIsDisplayed()
        compose.onNodeWithText("Release 1").assertDoesNotExist()
        compose.runOnIdle { assertTrue(state.items.isEmpty()); assertNull(state.nextCursor) }
        show(context.getString(R.string.action_retry)).performClick()
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.SESSION }
        show(context.getString(R.string.release_feed_session)).assertIsDisplayed()
        compose.onNodeWithText("Local fallback sentinel").assertDoesNotExist()
    }

    @Test fun repeatedServerCursorFailsClosedWithoutAppendingOrLeavingMoreAction() {
        val endpoint = endpoint()
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(1)), "same-cursor")))
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(2)), "same-cursor")))
        val state = networkState(endpoint)
        render(state)
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.READY }
        show(context.getString(R.string.release_feed_more)).performClick()
        compose.waitUntil(10_000) { state.pageFailure == HomeReleaseFeedStatus.ERROR }
        show(context.getString(R.string.release_feed_error)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.release_feed_more)).assertDoesNotExist()
        compose.runOnIdle { assertEquals(listOf(id(1)), state.items.map { it.candidateId }) }
        show(context.getString(R.string.release_feed_open_discovery)).assertIsNotEnabled()
        assertEquals(2, endpoint.requestCount)
    }

    @Test fun malformedNonReadyPlaybackResponseRendersErrorWithoutReleaseCard() {
        val endpoint = endpoint()
        endpoint.enqueue(MockResponse().setBody(page(listOf(wireItem(1, "DISCOVERED", "PLAY")), null)))
        val state = networkState(endpoint)
        render(state)
        compose.waitUntil(10_000) { state.status == HomeReleaseFeedStatus.ERROR }
        show(context.getString(R.string.release_feed_error)).assertIsDisplayed()
        compose.onNodeWithText("Release 1").assertDoesNotExist()
        compose.runOnIdle { assertTrue(played.isEmpty()); assertEquals(0, syncs) }
    }

    @Test fun loadingMoreIsDisabledAndFeedVisibilityDoesNotCallRecommendationCallbacks() {
        displayed.value = HomeReleaseFeedUiState(HomeReleaseFeedUiStatus.Ready,
            listOf(item(1, ReleaseFeedState.REVIEW_REQUIRED, ReleaseFeedAction.REVIEW)), hasMore = true, loadingMore = true)
        render()
        show(context.getString(R.string.release_feed_loading_more)).assertIsNotEnabled()
        show("Release 1").assertIsDisplayed()
        compose.runOnIdle { assertEquals(0, impressions); assertEquals(0, positiveFeedback) }
        // A screen callback assertion; journal/server no-positive-feedback is a separate connected gate.
    }

    @Test fun englishLightLargeTextReviewActionIsReachableAndHasButtonSemantics() = checkLargeText("en", AutPlayThemeMode.Light)
    @Test fun englishDarkLargeTextReviewActionIsReachableAndHasButtonSemantics() = checkLargeText("en", AutPlayThemeMode.Dark)
    @Test fun russianLightLargeTextReviewActionIsReachableAndHasButtonSemantics() = checkLargeText("ru", AutPlayThemeMode.Light)
    @Test fun russianDarkLargeTextReviewActionIsReachableAndHasButtonSemantics() = checkLargeText("ru", AutPlayThemeMode.Dark)

    private fun checkLargeText(locale: String, mode: AutPlayThemeMode) {
        val configuration = Configuration(context.resources.configuration).apply { setLocale(Locale.forLanguageTag(locale)) }
        val localized = context.createConfigurationContext(configuration)
        val density = Density(context.resources.displayMetrics.density, fontScale = 2f)
        displayed.value = HomeReleaseFeedUiState(HomeReleaseFeedUiStatus.Ready,
            listOf(item(1, ReleaseFeedState.REVIEW_REQUIRED, ReleaseFeedAction.REVIEW, WatchReason.LISTENING_HISTORY)))
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides configuration,
                LocalResources provides localized.resources, LocalDensity provides density) {
                Box(Modifier.width(320.dp)) { AutPlayTheme(AutPlayAppearance(mode)) { Screen(displayed.value) } }
            }
        }
        show(localized.getString(R.string.release_feed_history_reason)).assertIsDisplayed()
        show(localized.getString(R.string.release_feed_review_required)).assertIsDisplayed()
        val action = show(localized.getString(R.string.release_feed_open_review))
            .assertIsDisplayed().assertIsEnabled()
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Button))
        val bounds = action.fetchSemanticsNode().boundsInRoot
        assertTrue("Action target is at least48dp tall", bounds.height >= 48f * density.density - 1f)
        assertTrue("Action fits narrow viewport", bounds.width <= 320f * density.density + 1f)
        action.performClick()
        compose.runOnIdle { assertEquals(listOf(id(101)), discoveries) }
    }

    private fun render(state: HomeReleaseFeedState? = null) {
        compose.setContent {
            val scope = rememberCoroutineScope()
            LaunchedEffect(state) { state?.refresh() }
            AutPlayTheme {
                Screen(state?.toUi() ?: displayed.value,
                    retry = { if (state == null) retries++ else scope.launch { state.refresh() } },
                    more = { if (state != null) scope.launch { state.loadMore() } })
            }
        }
    }

    @androidx.compose.runtime.Composable
    private fun Screen(feed: HomeReleaseFeedUiState, retry: () -> Unit = { retries++ }, more: () -> Unit = {}) {
        HomeProductScreen(
            state = HomeScreenUiState(localMode = false, recommendationLoading = false, offlineFallback = false,
                releases = listOf(HomeReleaseUiItem("sentinel", "Local fallback sentinel", "Another source", null)),
                recommendations = emptyList(), releaseFeed = feed),
            contentPadding = PaddingValues(0.dp), onOpenListenTogether = {},
            onRecommendationVisible = { impressions++ }, onLike = { positiveFeedback++ }, onDislike = {},
            onReleasePlay = { played += it }, onReleaseDiscovery = { discoveries += it },
            onReleaseSync = { syncs++ }, onReleaseRetry = retry, onReleaseMore = more,
        )
    }

    private fun show(text: String): androidx.compose.ui.test.SemanticsNodeInteraction {
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasText(text))
        return compose.onNodeWithText(text)
    }

    private fun endpoint(): MockWebServer = MockWebServer().also { it.start(); server = it }

    private fun networkState(endpoint: MockWebServer): HomeReleaseFeedState = HomeReleaseFeedState(profile,
        OkHttpReleaseFeedPort(endpoint.url("/api/v1").toString().trimEnd('/'), FixedCredentials(),
            client = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build().also { clients += it }))

    private fun HomeReleaseFeedState.toUi() = HomeReleaseFeedUiState(
        status = status.uiStatus(),
        items = items.map { HomeReleaseFeedUiItem(it.candidateId, it.evidenceRunId, it.title, it.artistName,
            it.releasedAt, it.reason, it.state, it.availableAction, null) },
        hasMore = nextCursor != null, loadingMore = loadingMore, pageFailure = pageFailure?.uiStatus(),
    )

    private fun HomeReleaseFeedStatus.uiStatus(): HomeReleaseFeedUiStatus = when (this) {
        HomeReleaseFeedStatus.DISCONNECTED -> HomeReleaseFeedUiStatus.Disconnected
        HomeReleaseFeedStatus.LOADING -> HomeReleaseFeedUiStatus.Loading
        HomeReleaseFeedStatus.READY -> HomeReleaseFeedUiStatus.Ready
        HomeReleaseFeedStatus.OFFLINE -> HomeReleaseFeedUiStatus.Offline
        HomeReleaseFeedStatus.SESSION -> HomeReleaseFeedUiStatus.Session
        HomeReleaseFeedStatus.UNAVAILABLE -> HomeReleaseFeedUiStatus.Unavailable
        HomeReleaseFeedStatus.ERROR -> HomeReleaseFeedUiStatus.Error
    }

    private fun item(number: Int, state: ReleaseFeedState, action: ReleaseFeedAction,
        reason: WatchReason = WatchReason.MANUAL_FOLLOW, localId: String? = null) = HomeReleaseFeedUiItem(
        id(number), id(number + 100), "Release $number", "Artist $number", "2026-10-29T00:00:00+00:00",
        reason, state, action, localId,
    )

    private fun id(number: Int): String = "22222222-2222-4222-8222-${number.toString().padStart(12, '0')}"

    private fun wireItem(number: Int, state: String = "DISCOVERED", action: String = "OPEN_DISCOVERY"): String =
        """{"candidate_id":"${id(number)}","canonical_artist_id":"${id(500)}","artist_name":"Artist $number","title":"Release $number","released_at":"2026-10-29T00:00:00+00:00","reason":"MANUAL_FOLLOW","state":"$state","score_milli":100,"release_id":null,"recording_id":null,"user_track_ref_id":${if (state == "READY") "\"${id(600)}\"" else "null"},"provider_id":"${id(700)}","evidence_run_id":"${id(number + 100)}","evidence_adapter_version":"1.0.0","available_action":"$action"}"""

    private fun page(items: List<String>, next: String?): String =
        """{"contract_version":"release-feed-v1","schema_version":1,"ranking_version":"owner-local-v1","items":[${items.joinToString(",")}],"next_cursor":${next?.let { "\"$it\"" } ?: "null"}}"""

    private inner class FixedCredentials : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray? = if (profileId == profile) {
            SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("owner-token", "refresh-token", 0))
        } else null
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
}
