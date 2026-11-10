package app.autplay

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertHeightIsAtLeast
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasAnySibling
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.releasewatch.ArtistAutoDownloadReason
import app.autplay.application.releasewatch.ArtistAutoDownloadStatus
import app.autplay.application.releasewatch.OkHttpReleaseWatchPort
import app.autplay.application.releasewatch.WatchReason
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import app.autplay.ui.ArtistAutoDownloadMessage
import app.autplay.ui.ArtistFollowAction
import app.autplay.ui.ArtistFollowMenu
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.LibraryProductScreen
import app.autplay.ui.LibraryScreenUiState
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.OwnerAutoDownloadControls
import app.autplay.ui.OwnerAutoDownloadUiState
import app.autplay.ui.ReleaseWatchUiState
import app.autplay.ui.ReleaseWatchUiStatus
import app.autplay.ui.WatchedArtistUiItem
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Rendered component/HTTP qualification: production controls, state and transport.
 * The controlled loopback response model is not PostgreSQL, MainActivity wiring,
 * owner isolation, or durable policy qualification; those need the connected suite.
 */
@RunWith(AndroidJUnit4::class)
class ReleaseWatchScreenTest {
    @get:Rule val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private lateinit var server: MockWebServer
    private lateinit var fixture: WatchResponses
    private lateinit var client: OkHttpClient
    private lateinit var state: ReleaseWatchState

    @Before
    fun setUp() {
        fixture = WatchResponses()
        server = MockWebServer().apply {
            dispatcher = fixture
            start()
        }
        client = OkHttpClient.Builder()
            .callTimeout(15, TimeUnit.SECONDS)
            .readTimeout(12, TimeUnit.SECONDS)
            .build()
        val credentials = object : CredentialStore {
            override suspend fun read(profileId: ServerProfileId): ByteArray? =
                if (profileId == profile) SessionCredentialEnvelopeCodec.encode(
                    SessionCredentialEnvelope("watch-screen-owner", "unused-refresh", 0),
                ) else null

            override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
            override suspend fun clear(profileId: ServerProfileId) = Unit
        }
        state = ReleaseWatchState(
            profile,
            OkHttpReleaseWatchPort(server.url("/api/v1").toString().trimEnd('/'), credentials, client),
        )
    }

    @After
    fun tearDown() {
        fixture.releaseMutation?.countDown()
        server.shutdown()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }

    @Test
    fun expandedWatchedListRendersManualAndHistoryOriginsFromHttpResponse() {
        load()
        compose.setContent {
            val scope = rememberCoroutineScope()
            AutPlayTheme {
                LibraryProductScreen(
                    state = LibraryScreenUiState(
                        localMode = false,
                        tracks = emptyList(),
                        section = LibrarySection.Artists,
                        releaseWatch = watchUi(),
                    ),
                    contentPadding = PaddingValues(0.dp),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                    onSetArtistFollow = { id, follow -> scope.launch { state.setFollow(id, follow) } },
                    onSetOwnerAutoDownload = { enabled -> scope.launch { state.setAutoDownload(enabled) } },
                    onRetryReleaseWatch = { scope.launch { state.load() } },
                )
            }
        }
        val heading = text(R.string.release_watch_list_title) + " (2)"
        compose.onNodeWithTag("library-product-list").performScrollToNode(hasText(heading))
        compose.onNodeWithText(heading)
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, text(R.string.release_watch_collapsed)))
            .assertHeightIsAtLeast(48.dp)
            .performClick()
        compose.onNodeWithText(heading)
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, text(R.string.release_watch_expanded)))
        listText(R.string.release_watch_manual_reason)
        compose.onNode(hasText(text(R.string.release_watch_manual_reason)) and hasAnySibling(hasText("Manual Artist")))
            .assertIsDisplayed()
        listText(R.string.release_watch_history_reason)
        compose.onNode(hasText(text(R.string.release_watch_history_reason)) and hasAnySibling(hasText("History Artist")))
            .assertIsDisplayed()
        assertTrue(fixture.requests.all { it.method == "GET" })
        assertOwnerHeaders()
    }

    @Test
    fun historyUnfollowShowsExclusionAndExplicitRefollowUsesServerManualOrigin() {
        load()
        compose.setContent {
            val scope = rememberCoroutineScope()
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    ArtistFollowAction(
                        HISTORY,
                        watchUi(),
                        onSetFollow = { id, follow -> scope.launch { state.setFollow(id, follow) } },
                    )
                    ArtistFollowMenu(HISTORY, watchUi()) { id, follow ->
                        scope.launch { state.setFollow(id, follow) }
                    }
                }
            }
        }
        compose.onNodeWithText(text(R.string.release_watch_history_reason))
            .assertIsDisplayed()
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.LiveRegion, LiveRegionMode.Polite))
        compose.onNodeWithText(text(R.string.release_watch_unfollow)).performClick()
        compose.waitUntil(10_000) { state.status == ReleaseWatchStatus.READY && HISTORY in state.exclusions }
        compose.onNodeWithText(text(R.string.release_watch_excluded)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_actions)).performClick()
        // The same "Follow artist" exists in the detail and menu: use the popup node.
        compose.onNode(
            hasText(text(R.string.release_watch_follow)) and
                androidx.compose.ui.test.hasAnyAncestor(SemanticsMatcher.keyIsDefined(SemanticsProperties.IsPopup)),
        ).performClick()
        compose.waitUntil(10_000) {
            state.status == ReleaseWatchStatus.READY &&
                state.artists.any { it.artistId == HISTORY && it.reason == WatchReason.MANUAL_FOLLOW }
        }
        compose.onNodeWithText(text(R.string.release_watch_manual_reason)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_excluded)).assertDoesNotExist()
        assertEquals(
            listOf("DELETE", "PUT"),
            fixture.requests.filter { it.path == "/api/v1/release-watch/artists/$HISTORY" }.map { it.method },
        )
        assertTrue(fixture.requests.none { it.path.contains("auto-download") && it.method == "PUT" })
    }

    @Test
    fun confirmationCancelSendsNothingAndConfirmedEnableUsesFetchedRevisionThenPausedDisableWorks() {
        fixture.pauseAfterEnable = true
        load()
        showOwnerControls()
        compose.onNodeWithText(text(R.string.release_watch_auto_enable)).performClick()
        compose.onNodeWithText(text(R.string.release_watch_auto_consequence)).assertIsDisplayed()
        assertEquals(0, mutations().size)
        compose.onNodeWithText(text(R.string.action_cancel)).performClick()
        compose.onNodeWithText(text(R.string.release_watch_auto_confirm_title)).assertDoesNotExist()
        assertEquals(0, mutations().size)

        confirmEnable()
        compose.waitUntil(10_000) { state.autoSettings?.enabled == true && !state.autoBusy }
        compose.onNodeWithText(text(R.string.release_watch_auto_paused)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_auto_disable))
            .assertIsEnabled().assertHeightIsAtLeast(48.dp).performClick()
        compose.waitUntil(10_000) { state.autoSettings?.enabled == false && !state.autoBusy }
        compose.onNodeWithText(text(R.string.release_watch_auto_gate_off)).assertIsDisplayed()

        val writes = mutations().map { Json.parseToJsonElement(it.body).jsonObject }
        assertEquals(2, writes.size)
        assertTrue(writes[0].getValue("enabled").jsonPrimitive.boolean)
        assertEquals(7L, writes[0].getValue("expected_revision").jsonPrimitive.long)
        assertEquals("consequence-7", writes[0].getValue("consequence_confirmation").jsonPrimitive.content)
        assertFalse(writes[1].getValue("enabled").jsonPrimitive.boolean)
        assertEquals(8L, writes[1].getValue("expected_revision").jsonPrimitive.long)
        assertFalse(writes[1].containsKey("consequence_confirmation"))
        assertOwnerHeaders()
    }

    @Test
    fun gateOffAndMissingConfirmationNeverOfferAnEnabledEnableAction() {
        fixture.gate = false
        load()
        showOwnerControls()
        compose.onNodeWithText(text(R.string.release_watch_auto_gate_off)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_auto_enable)).assertIsNotEnabled()
        assertEquals(0, mutations().size)

        fixture.gate = true
        fixture.confirmation = null
        load()
        compose.onNodeWithText(text(R.string.release_watch_auto_confirmation_unavailable)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_auto_enable)).assertIsNotEnabled()
        assertEquals(0, mutations().size)
    }

    @Test
    fun rejectedRevisionAndGateCommandsShowFailureAndRefetchedServerTruth() {
        load()
        showOwnerControls()
        for (code in listOf(409, 403)) {
            fixture.mutationCode = code
            val before = mutations().size
            compose.onNodeWithText(text(R.string.release_watch_auto_enable)).performScrollTo().performClick()
            compose.onNodeWithText(text(R.string.release_watch_auto_consequence)).assertIsDisplayed()
            // Simulate an operator gate change after the screen fetched its revision.
            if (code == 403) fixture.gate = false
            compose.onNodeWithText(text(R.string.release_watch_auto_confirm)).performClick()
            compose.waitUntil(10_000) { mutations().size == before + 1 && !state.autoBusy && state.autoCommandFailure != null }
            compose.onNodeWithText(text(R.string.release_watch_auto_save_failed)).assertIsDisplayed()
            compose.onNodeWithText(text(if (code == 403) R.string.release_watch_auto_gate_off else R.string.release_watch_auto_off))
                .assertIsDisplayed()
            if (code == 403) {
                compose.onNodeWithText(text(R.string.release_watch_auto_enable)).assertIsNotEnabled()
            } else {
                compose.onNodeWithText(text(R.string.release_watch_auto_enable)).assertIsEnabled()
            }
            compose.onNodeWithText(text(R.string.release_watch_auto_disable)).assertDoesNotExist()
            assertEquals(false, state.autoSettings?.enabled)
            assertEquals(7L, state.autoSettings?.revision)
        }
        assertEquals(2, mutations().size)
    }

    @Test
    fun pendingDisableIsNotClickableTwiceAndDoesNotClaimSuccessBeforeResponse() {
        fixture.enabled = true
        fixture.releaseMutation = CountDownLatch(1)
        load()
        showOwnerControls()
        compose.onNodeWithText(text(R.string.release_watch_auto_disable)).performClick()
        compose.waitUntil(10_000) { mutations().size == 1 && state.autoBusy }
        compose.onNodeWithText(text(R.string.release_watch_saving)).assertIsNotEnabled()
        compose.onNodeWithText(text(R.string.release_watch_auto_enabled)).assertIsDisplayed()
        assertEquals(true, state.autoSettings?.enabled)
        assertEquals(1, mutations().size)
        fixture.releaseMutation!!.countDown()
        compose.waitUntil(10_000) { !state.autoBusy && state.autoSettings?.enabled == false }
        compose.onNodeWithText(text(R.string.release_watch_auto_off)).assertIsDisplayed()
    }

    @Test
    fun perArtistEffectiveLabelsDoNotTreatReasonAloneAsPermission() {
        var status by mutableStateOf<ArtistAutoDownloadStatus?>(null)
        compose.setContent {
            AutPlayTheme { ArtistAutoDownloadMessage(status) }
        }
        val cases = listOf(
            null to R.string.release_watch_artist_auto_unavailable,
            artistStatus(ArtistAutoDownloadReason.OWNER_CONSENT_OFF) to R.string.release_watch_artist_auto_owner_off,
            artistStatus(ArtistAutoDownloadReason.ARTIST_REVIEW_REQUIRED) to R.string.release_watch_artist_auto_review,
            artistStatus(ArtistAutoDownloadReason.WATCH_POLICY_PENDING) to R.string.release_watch_artist_auto_pending,
            artistStatus(ArtistAutoDownloadReason.STAGED_GATE_OFF) to R.string.release_watch_artist_auto_gate_off,
            artistStatus(ArtistAutoDownloadReason.READY) to R.string.release_watch_artist_auto_unavailable,
            artistStatus(ArtistAutoDownloadReason.READY, effective = true, gate = false) to R.string.release_watch_artist_auto_unavailable,
            artistStatus(ArtistAutoDownloadReason.READY, effective = true, gate = true) to R.string.release_watch_artist_auto_ready,
        )
        for ((value, label) in cases) {
            compose.runOnIdle { status = value }
            compose.onNodeWithText(text(label)).assertIsDisplayed()
        }
    }

    private fun showOwnerControls() {
        compose.setContent {
            val scope = rememberCoroutineScope()
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    OwnerAutoDownloadControls(
                        watchUi().autoDownload,
                        { enabled -> scope.launch { state.setAutoDownload(enabled) } },
                        { scope.launch { state.load() } },
                    )
                }
            }
        }
    }

    private fun confirmEnable() {
        compose.onNodeWithText(text(R.string.release_watch_auto_enable)).performScrollTo().performClick()
        compose.onNodeWithText(text(R.string.release_watch_auto_consequence)).assertIsDisplayed()
        compose.onNodeWithText(text(R.string.release_watch_auto_confirm)).performClick()
    }

    private fun listText(resource: Int) {
        compose.onNodeWithTag("library-product-list").performScrollToNode(hasText(text(resource)))
        compose.onNodeWithText(text(resource)).assertIsDisplayed()
    }

    private fun load() = runBlocking {
        state.load()
        assertEquals(ReleaseWatchStatus.READY, state.status)
        assertEquals(ReleaseWatchStatus.READY, state.autoStatus)
    }

    // Explicit presentation mapping for this component harness, not a replacement for
    // the separate MainActivity connected test and its owner/name mapping.
    private fun watchUi() = ReleaseWatchUiState(
        status = state.status.ui(),
        artists = state.artists.map {
            WatchedArtistUiItem(it.artistId, if (it.artistId == MANUAL) "Manual Artist" else "History Artist", it.reason, it.autoDownload)
        },
        excludedArtistIds = state.exclusions,
        busyArtistId = state.busyArtistId,
        autoDownload = OwnerAutoDownloadUiState(
            status = state.autoStatus.ui(),
            enabled = state.autoSettings?.enabled == true,
            executionEnabled = state.autoSettings?.executionEnabled == true,
            confirmationAvailable = !state.autoSettings?.consequenceConfirmation.isNullOrBlank(),
            busy = state.autoBusy,
            commandFailed = state.autoCommandFailure != null,
        ),
    )

    private fun ReleaseWatchStatus.ui(): ReleaseWatchUiStatus = when (this) {
        ReleaseWatchStatus.DISCONNECTED -> ReleaseWatchUiStatus.Disconnected
        ReleaseWatchStatus.UNAVAILABLE -> ReleaseWatchUiStatus.Unavailable
        ReleaseWatchStatus.LOADING -> ReleaseWatchUiStatus.Loading
        ReleaseWatchStatus.READY -> ReleaseWatchUiStatus.Ready
        ReleaseWatchStatus.OFFLINE -> ReleaseWatchUiStatus.Offline
        ReleaseWatchStatus.SESSION -> ReleaseWatchUiStatus.Session
        ReleaseWatchStatus.ERROR -> ReleaseWatchUiStatus.Error
    }

    private fun artistStatus(reason: ArtistAutoDownloadReason, effective: Boolean = false, gate: Boolean = true) =
        ArtistAutoDownloadStatus(null, null, null, effective, gate, reason)

    private fun text(resource: Int) = context.getString(resource)
    private fun mutations() = fixture.requests.filter { it.path == "/api/v1/release-watch/auto-download" && it.method == "PUT" }

    private fun assertOwnerHeaders() {
        assertTrue(fixture.requests.isNotEmpty())
        fixture.requests.forEach {
            assertEquals("Bearer watch-screen-owner", it.authorization)
            assertEquals("no-store", it.cacheControl)
        }
    }

    private data class RequestFacts(
        val method: String?,
        val path: String,
        val body: String,
        val authorization: String?,
        val cacheControl: String?,
    )

    private class WatchResponses : Dispatcher() {
        val requests = CopyOnWriteArrayList<RequestFacts>()
        @Volatile var gate = true
        @Volatile var enabled = false
        @Volatile var confirmation: String? = "consequence-7"
        @Volatile var revision = 7L
        @Volatile var mutationCode = 200
        @Volatile var pauseAfterEnable = false
        @Volatile var releaseMutation: CountDownLatch? = null
        @Volatile private var historyReason: String? = "LISTENING_HISTORY"
        @Volatile private var excluded = false

        override fun dispatch(request: RecordedRequest): MockResponse {
            val path = request.path.orEmpty().substringBefore('?')
            val body = request.body.readUtf8()
            requests += RequestFacts(request.method, path, body, request.getHeader("Authorization"), request.getHeader("Cache-Control"))
            return when {
                path == "/api/v1/release-watch/artists" && request.method == "GET" -> {
                    val history = historyReason?.let { ",{\"artist_id\":\"$HISTORY\",\"reason\":\"$it\",\"score_milli\":1,\"favorite_generation\":1}" }.orEmpty()
                    ok("\"artists\":[{\"artist_id\":\"$MANUAL\",\"reason\":\"MANUAL_FOLLOW\",\"score_milli\":0,\"favorite_generation\":null}$history]")
                }
                path == "/api/v1/release-watch/exclusions" && request.method == "GET" ->
                    ok("\"artist_ids\":[${if (excluded) "\"$HISTORY\"" else ""}]")
                path == "/api/v1/release-watch/artists/$HISTORY" && request.method in listOf("PUT", "DELETE") -> {
                    excluded = request.method == "DELETE"
                    historyReason = if (excluded) null else "MANUAL_FOLLOW"
                    ok("\"artist_id\":\"$HISTORY\",\"revision\":2")
                }
                path == "/api/v1/release-watch/auto-download" && request.method == "GET" -> {
                    val token = confirmation?.let { "\"$it\"" } ?: "null"
                    ok("\"enabled\":$enabled,\"revision\":$revision,\"execution_enabled\":$gate,\"consequence_confirmation\":$token")
                }
                path == "/api/v1/release-watch/auto-download" && request.method == "PUT" -> {
                    if (releaseMutation?.await(10, TimeUnit.SECONDS) == false) return MockResponse().setResponseCode(504)
                    if (mutationCode != 200) return MockResponse().setResponseCode(mutationCode).setBody("{}")
                    val value = Json.parseToJsonElement(body).jsonObject
                    if (value.getValue("expected_revision").jsonPrimitive.long != revision) return MockResponse().setResponseCode(409)
                    val requested = value.getValue("enabled").jsonPrimitive.boolean
                    if (requested && (!gate || value["consequence_confirmation"]?.jsonPrimitive?.content != confirmation)) {
                        return MockResponse().setResponseCode(403)
                    }
                    enabled = requested
                    revision += 1
                    if (requested && pauseAfterEnable) gate = false
                    ok("\"revision\":$revision")
                }
                else -> MockResponse().setResponseCode(404).setBody("{}")
            }
        }

        private fun ok(fields: String) = MockResponse().setHeader("Content-Type", "application/json")
            .setBody("{\"contract_version\":\"release-watch-v1\",\"schema_version\":1,$fields}")
    }

    private companion object {
        const val MANUAL = "22222222-2222-4222-8222-222222222222"
        const val HISTORY = "33333333-3333-4333-8333-333333333333"
    }
}
