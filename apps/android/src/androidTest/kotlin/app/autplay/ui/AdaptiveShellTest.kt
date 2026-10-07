package app.autplay.ui

import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.requiredWidth
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.player.NowPlayingScreen
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.swipeDown
import androidx.compose.ui.test.swipeUp
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistCredit
import app.autplay.application.artist.ArtistCreditId
import app.autplay.application.artist.ArtistCreditMember
import app.autplay.application.artist.ArtistDetail
import app.autplay.application.artist.ArtistId
import app.autplay.application.artist.ArtistKey
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.artist.ArtistSummary
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import app.autplay.R
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.ListAnchor

class AdaptiveShellTest {
    @get:Rule
    val composeRule = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun directNowPlayingWithoutBackStackDoesNotExposeADeadBackAction() {
        composeRule.setContent {
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = UiDestination.NowPlaying,
                    onDestinationSelected = {},
                    canNavigateBack = false,
                ) { _, _, _ -> Text("direct-now-playing") }
            }
        }

        composeRule.onNodeWithText("direct-now-playing").assertIsDisplayed()
        assertTrue(
            composeRule.onAllNodesWithContentDescription(context.getString(R.string.action_back))
                .fetchSemanticsNodes().isEmpty(),
        )
    }

    @Test
    fun compactNavigationRoutesEveryPrimaryActionToContent() {
        composeRule.setContent {
            var selected by remember { mutableStateOf<UiDestination>(UiDestination.Home) }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = selected,
                    onDestinationSelected = { selected = it },
                ) { destination, _, _ ->
                    Text("route:${destination.route}")
                }
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.nav_search)).performClick()
        val navigationBounds = composeRule.onNodeWithTag("compact-navigation").getUnclippedBoundsInRoot()
        assertEquals(61f, (navigationBounds.bottom - navigationBounds.top).value, 0.5f)
        composeRule.onNodeWithText("route:search").assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.nav_library)).performClick()
        composeRule.onNodeWithText("route:library").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_open_settings)).performClick()
        composeRule.onNodeWithText("route:settings").assertIsDisplayed()
    }

    @Test
    fun directNowPlayingCanCollapseToHomeWithoutABackStack() {
        val playback = PlaybackPresentationState(mediaId = "fixture", title = "Track", artist = "Artist", isPlaying = true)
        composeRule.setContent {
            var selected by remember { mutableStateOf<UiDestination>(UiDestination.NowPlaying) }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = selected,
                    onDestinationSelected = { selected = it },
                    nowPlayingBar = {
                        app.autplay.ui.player.PlaybackMiniPlayer(playback, {}, {}, {})
                    },
                ) { destination, _, _ ->
                    if (destination == UiDestination.NowPlaying) {
                        NowPlayingScreen(
                            state = playback,
                            onTogglePlayPause = {}, onToggleShuffle = {}, onCycleRepeat = {},
                            onSeekBegin = {}, onSeekUpdate = {}, onSeekCommit = {},
                            onLike = {}, onDislike = {}, feedbackEnabled = true, onObservingChanged = {},
                        )
                    } else Text("route:${destination.route}")
                }
            }
        }
        composeRule.onNodeWithTag("player-cover").performTouchInput { swipeDown() }
        composeRule.onNodeWithText("route:home").assertIsDisplayed()
        composeRule.onNodeWithTag("compact-navigation").assertIsDisplayed()
        composeRule.onNodeWithTag("persistent-mini-player").assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_pause)).assertIsDisplayed()
        val screenshot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        java.io.File(context.cacheDir, "player-layout-compact-navigation.png").outputStream().use {
            check(screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it))
        }
    }

    @Test
    fun currentTrackFeedbackRemainsReachableAt320Dp() {
        composeRule.setContent {
            Box(Modifier.width(320.dp)) {
                NowPlayingScreen(
                    state = PlaybackPresentationState(
                        mediaId = "queue-entry",
                        title = "Current track",
                        artist = "Current artist",
                    ),
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

        composeRule.onNodeWithContentDescription(context.getString(R.string.action_like)).performScrollTo().assertIsDisplayed()
        composeRule.onNodeWithContentDescription(context.getString(R.string.action_dislike)).performScrollTo().assertIsDisplayed()
    }

    @Test
    fun homeFailureIsTruthfulAndRetryable() {
        composeRule.setContent {
            var retried by remember { mutableStateOf(false) }
            AutPlayTheme {
                if (retried) {
                    Text("retry-requested")
                } else {
                    HomeProductScreen(
                        state = HomeScreenUiState(
                            false,
                            false,
                            false,
                            emptyList(),
                            emptyList(),
                            recommendationError = true,
                        ),
                        contentPadding = PaddingValues(),
                        onOpenListenTogether = {},
                        onRecommendationVisible = {},
                        onLike = {},
                        onDislike = {},
                        onRetry = { retried = true },
                    )
                }
            }
        }

        composeRule.onNodeWithTag("home-product-list")
            .performScrollToNode(hasText(context.getString(R.string.state_error_title)))
        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.action_retry)).performClick()
        composeRule.onNodeWithText("retry-requested").assertIsDisplayed()
    }

    @Test
    fun activeHomePlaybackRemainsReachableAfterHeroScrollsAway() {
        renderHomeWithPersistentPlayer(PlaybackPresentationState(mediaId = "active-entry", title = "Active track", artist = "Artist"))
        scrollHomeWithUserGestures()
        // The clickable player merges its text; inspect the child to verify its visible bounds.
        composeRule.onNode(hasText("Active track") and hasAnyAncestor(hasTestTag("persistent-mini-player")), useUnmergedTree = true).assertIsDisplayed()
    }

    @Test
    fun bottomHomePlaybackAppearsWhenPlaybackStartsAfterScrolling() {
        val startPlayback = renderHomeWithPersistentPlayer(PlaybackPresentationState())
        scrollHomeWithUserGestures()
        composeRule.runOnIdle { startPlayback(PlaybackPresentationState(mediaId = "late-entry", title = "Late playback", artist = "Artist")) }
        composeRule.onNodeWithTag("persistent-mini-player").assertIsDisplayed()
        composeRule.onNode(hasText("Late playback") and hasAnyAncestor(hasTestTag("persistent-mini-player")), useUnmergedTree = true).assertIsDisplayed()
    }

    private fun renderHomeWithPersistentPlayer(initial: PlaybackPresentationState): (PlaybackPresentationState) -> Unit {
        var update: (PlaybackPresentationState) -> Unit = {}
        composeRule.setContent {
            var player by remember { mutableStateOf(initial) }
            update = { player = it }
            AutPlayTheme {
                AutPlayAdaptiveShell(
                    selectedDestination = UiDestination.Home, onDestinationSelected = {},
                    nowPlayingBar = {
                        if (player.mediaId != null) app.autplay.ui.player.PlaybackMiniPlayer(player, {}, {}, {})
                    },
                ) { _, padding, _ ->
                    HomeProductScreen(
                        state = HomeScreenUiState(true, false, false, emptyList(), emptyList(),
                            recentlyPlayed = (1..30).map { HomeTrackUiItem("$it", "Recent $it", "Artist") }),
                        contentPadding = padding, onOpenListenTogether = {}, onRecommendationVisible = {},
                        onLike = {}, onDislike = {}, playerState = player,
                    )
                }
            }
        }
        return { update(it) }
    }

    private fun scrollHomeWithUserGestures() {
        repeat(3) {
            composeRule.onNodeWithTag("home-product-list").performTouchInput { swipeUp() }
            composeRule.waitForIdle()
        }
    }

    @Test
    fun searchFailureIsTruthfulAndRetryable() {
        composeRule.setContent {
            var retried by remember { mutableStateOf(false) }
            AutPlayTheme {
                if (retried) {
                    Text("search-retry-requested")
                } else {
                    SearchProductScreen(
                        state = SearchScreenUiState("query", emptyList(), searched = true, error = true),
                        contentPadding = PaddingValues(),
                        onQueryChange = {},
                        onSearch = {},
                        onPlay = {},
                        onRetry = { retried = true },
                    )
                }
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.action_retry)).performClick()
        composeRule.onNodeWithText("search-retry-requested").assertIsDisplayed()
    }

    @Test
    fun homeLocalSectionsRemainPresentWhenRecommendationsFail() {
        composeRule.setContent {
            AutPlayTheme {
                HomeProductScreen(
                    state = HomeScreenUiState(
                        localMode = false,
                        recommendationLoading = false,
                        offlineFallback = false,
                        releases = listOf(HomeReleaseUiItem("release", "Local release", "Local artist", null)),
                        recommendations = emptyList(),
                        recentlyPlayed = listOf(HomeTrackUiItem("track", "Local recent", "Local artist")),
                        playlists = listOf(CoreCollectionUiItem("playlist", "Local playlist", null)),
                        recommendationError = true,
                    ),
                    contentPadding = PaddingValues(),
                    onOpenListenTogether = {},
                    onRecommendationVisible = {},
                    onLike = {},
                    onDislike = {},
                )
            }
        }

        composeRule.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-recent"))
        composeRule.onNodeWithTag("home-recent")
            .assert(hasText("Local recent"))
            .assertIsDisplayed()
        composeRule.onNodeWithTag("home-product-list").performScrollToNode(hasText("Local release"))
        composeRule.onNodeWithText("Local release").assertIsDisplayed()
        composeRule.onNodeWithTag("home-product-list").performScrollToNode(hasText("Local playlist"))
        composeRule.onNodeWithText("Local playlist").assertIsDisplayed()
        composeRule.onNodeWithTag("home-product-list")
            .performScrollToNode(hasText(context.getString(R.string.state_error_title)))
        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
    }

    @Test
    fun vaultFailureDoesNotReplaceLocalSearchRows() {
        composeRule.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = SearchScreenUiState(
                        query = "local",
                        results = listOf(CoreTrackUiItem("track", "Local result", "Artist")),
                        searched = true,
                        vaultAvailable = true,
                        vaultSelected = true,
                        vaultError = true,
                    ),
                    contentPadding = PaddingValues(),
                    onQueryChange = {},
                    onSearch = {},
                    onPlay = {},
                )
            }
        }

        composeRule.onNodeWithText("Local result").assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.ui_library_refresh_vault_unavailable)).assertIsDisplayed()
    }

    @Test
    fun vaultRowsShowMetadataAndOnlyPlayableRowInvokesPlayback() {
        var played: String? = null
        composeRule.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = SearchScreenUiState(
                        query = "vault",
                        results = emptyList(),
                        searched = true,
                        vaultAvailable = true,
                        vaultSelected = true,
                        vaultSearched = true,
                        vaultResults = listOf(
                            VaultSearchUiItem("playable", "Vault track", "Artist", "UPLOAD", "AVAILABLE", true),
                            VaultSearchUiItem("remote-only", null, null, "IMPORT", "PENDING", false),
                        ),
                    ),
                    contentPadding = PaddingValues(),
                    onQueryChange = {},
                    onSearch = {},
                    onPlay = {},
                    onPlayVault = { played = it },
                )
            }
        }

        composeRule.onNodeWithTag("search-product-list")
            .performScrollToNode(hasTestTag("vault-result-playable"))
        composeRule.onNodeWithTag("vault-result-playable").performClick()
        composeRule.runOnIdle { assertEquals("playable", played) }
        composeRule.onNodeWithTag("search-product-list")
            .performScrollToNode(hasTestTag("vault-result-remote-only"))
        composeRule.onNodeWithTag("vault-result-remote-only").assertHasNoClickAction()
        composeRule.onNodeWithText(context.getString(R.string.search_vault_playback_unavailable)).assertIsDisplayed()
    }

    @Test
    fun completedEmptySearchHasOneCombinedEmptyState() {
        composeRule.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = SearchScreenUiState(
                        query = "nothing",
                        results = emptyList(),
                        searched = true,
                        vaultAvailable = true,
                        vaultSelected = true,
                        vaultSearched = true,
                    ),
                    contentPadding = PaddingValues(),
                    onQueryChange = {},
                    onSearch = {},
                    onPlay = {},
                )
            }
        }

        composeRule.onNodeWithTag("search-product-list")
            .performScrollToNode(hasText(context.getString(R.string.search_empty)))
        composeRule.onNodeWithText(context.getString(R.string.search_empty)).assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText(context.getString(R.string.search_vault_empty)).fetchSemanticsNodes().isEmpty())
    }

    @Test
    fun libraryFailureRemainsVisibleWithLocalContent() {
        composeRule.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = LibraryScreenUiState(
                        localMode = true,
                        tracks = listOf(CoreTrackUiItem("track", "Local fixture", "Artist")),
                        error = true,
                    ),
                    contentPadding = PaddingValues(),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                )
            }
        }

        composeRule.onNodeWithText(context.getString(R.string.state_error_title)).assertIsDisplayed()
        composeRule.onNodeWithTag("library-product-list").performScrollToNode(hasText("Local fixture"))
        composeRule.onNodeWithText("Local fixture").assertIsDisplayed()
    }

    @Test
    fun searchRestoresStableRowAnchorAfterRowsReload() {
        val rows = (0 until 30).map { index -> CoreTrackUiItem("track-$index", "Track $index", "Artist") }
        val searchState = mutableStateOf(SearchScreenUiState("query", emptyList(), searched = true))
        val restoredAnchor = java.util.concurrent.atomic.AtomicReference<ListAnchor?>()
        composeRule.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = searchState.value,
                    contentPadding = PaddingValues(),
                    onQueryChange = {},
                    onSearch = {},
                    onPlay = {},
                    listAnchor = ListAnchor("search:all:query:false", "search-result:track-20", 0),
                    onListAnchorChange = { restoredAnchor.set(it) },
                )
            }
        }

        composeRule.runOnIdle {
            assertTrue(restoredAnchor.get() == null)
            searchState.value = SearchScreenUiState("query", rows, searched = true)
        }
        composeRule.waitUntil(timeoutMillis = 5_000) { restoredAnchor.get() != null }
        composeRule.onNodeWithText("Track 20").assertIsDisplayed()
        composeRule.runOnIdle {
            assertEquals("search:all:query:false", restoredAnchor.get()?.contextKey)
            assertEquals("search-result:track-20", restoredAnchor.get()?.itemKey)
            assertEquals(0, restoredAnchor.get()?.scrollOffset)
        }
    }

    @Test
    fun searchRestoresStableRowAnchorWithNonzeroOffsetAfterRowsReload() {
        val rows = (0 until 30).map { index -> CoreTrackUiItem("track-$index", "Track $index", "Artist") }
        val searchState = mutableStateOf(SearchScreenUiState("query", emptyList(), searched = true))
        val restoredAnchor = java.util.concurrent.atomic.AtomicReference<ListAnchor?>()
        composeRule.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = searchState.value,
                    contentPadding = PaddingValues(),
                    onQueryChange = {},
                    onSearch = {},
                    onPlay = {},
                    listAnchor = ListAnchor("search:all:query:false", "search-result:track-20", 37),
                    onListAnchorChange = { restoredAnchor.set(it) },
                )
            }
        }

        composeRule.runOnIdle {
            assertTrue(restoredAnchor.get() == null)
            searchState.value = SearchScreenUiState("query", rows, searched = true)
        }
        composeRule.waitUntil(timeoutMillis = 5_000) { restoredAnchor.get() != null }
        composeRule.onNodeWithText("Track 20").assertIsDisplayed()
        composeRule.runOnIdle {
            assertEquals("search:all:query:false", restoredAnchor.get()?.contextKey)
            assertEquals("search-result:track-20", restoredAnchor.get()?.itemKey)
            assertEquals(37, restoredAnchor.get()?.scrollOffset)
        }
    }

    @Test
    fun libraryRestoresStableRowAnchorAfterRowsReload() {
        val rows = (0 until 30).map { index -> CoreTrackUiItem("track-$index", "Library track $index", "Artist") }
        val libraryState = mutableStateOf(LibraryScreenUiState(localMode = true, tracks = emptyList()))
        val restoredAnchor = java.util.concurrent.atomic.AtomicReference<ListAnchor?>()
        composeRule.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = libraryState.value,
                    contentPadding = PaddingValues(),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                    listAnchor = ListAnchor(
                        "library:Tracks:RecentlyAdded:All",
                        "library-track:track-20",
                        0,
                    ),
                    onListAnchorChange = { restoredAnchor.set(it) },
                )
            }
        }

        composeRule.runOnIdle {
            assertTrue(restoredAnchor.get() == null)
            libraryState.value = LibraryScreenUiState(localMode = true, tracks = rows)
        }
        composeRule.waitUntil(timeoutMillis = 5_000) { restoredAnchor.get() != null }
        composeRule.onNodeWithText("Library track 20").assertIsDisplayed()
        composeRule.runOnIdle {
            assertEquals("library:Tracks:RecentlyAdded:All", restoredAnchor.get()?.contextKey)
            assertEquals("library-track:track-20", restoredAnchor.get()?.itemKey)
            assertEquals(0, restoredAnchor.get()?.scrollOffset)
        }
    }

    @Test
    fun libraryRestoresStableRowAnchorWithNonzeroOffsetAfterRowsReload() {
        val rows = (0 until 30).map { index -> CoreTrackUiItem("track-$index", "Library track $index", "Artist") }
        val libraryState = mutableStateOf(LibraryScreenUiState(localMode = true, tracks = emptyList()))
        val restoredAnchor = java.util.concurrent.atomic.AtomicReference<ListAnchor?>()
        composeRule.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = libraryState.value,
                    contentPadding = PaddingValues(),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                    listAnchor = ListAnchor(
                        "library:Tracks:RecentlyAdded:All",
                        "library-track:track-20",
                        37,
                    ),
                    onListAnchorChange = { restoredAnchor.set(it) },
                )
            }
        }

        composeRule.runOnIdle {
            assertTrue(restoredAnchor.get() == null)
            libraryState.value = LibraryScreenUiState(localMode = true, tracks = rows)
        }
        composeRule.waitUntil(timeoutMillis = 5_000) { restoredAnchor.get() != null }
        composeRule.onNodeWithText("Library track 20").assertIsDisplayed()
        composeRule.runOnIdle {
            assertEquals("library:Tracks:RecentlyAdded:All", restoredAnchor.get()?.contextKey)
            assertEquals("library-track:track-20", restoredAnchor.get()?.itemKey)
            assertEquals(37, restoredAnchor.get()?.scrollOffset)
        }
    }

    @Test
    fun mediumLibraryTapPlaysWithoutOpeningDetail() {
        var observedWidthClass: UiWidthClass? = null
        var playedTrack: String? = null
        composeRule.setContent {
            var selectedDetail by remember { mutableStateOf<DetailTarget?>(null) }
            val actions = CoreProductRouteActions(
                openListenTogether = {},
                recommendationVisible = {},
                likeRecommendation = {},
                dislikeRecommendation = {},
                retryHome = {},
                resumeHomeQueue = {},
                openHomePlaylist = {},
                openOffline = {},
                openProblems = {},
                changeQuery = {},
                submitSearch = {},
                playSearchResult = {},
                changeVaultScope = {},
                changeSearchAnchor = {},
                addLocal = {},
                selectTrack = { selectedDetail = DetailTarget(DetailKind.Track, it) },
                removeOrRestore = {},
                likeTrack = {},
                changeLibrarySection = {},
                changeLibrarySort = {},
                changeLibraryFilter = {},
                openCollection = { _, _ -> },
                openDetail = { selectedDetail = it },
                openReview = {},
                changeLibraryAnchor = {},
                playTrack = { playedTrack = it },
                playPlaylistEntry = {},
                downloadTrack = {},
                repairAccess = {},
            )
            Box(Modifier.requiredWidth(700.dp)) {
                AutPlayTheme {
                    AutPlayAdaptiveShell(
                        selectedDestination = UiDestination.Library,
                        onDestinationSelected = {},
                        canNavigateBack = selectedDetail != null,
                        onNavigateBack = { selectedDetail = null },
                    ) { _, contentPadding, widthClass ->
                        observedWidthClass = widthClass
                        CoreProductRouteRenderer(
                            destination = UiDestination.Library,
                            widthClass = widthClass,
                            contentPadding = contentPadding,
                            homeState = HomeScreenUiState(false, false, false, emptyList(), emptyList()),
                            searchState = SearchScreenUiState("", emptyList(), false),
                            libraryState = LibraryScreenUiState(
                                localMode = true,
                                tracks = listOf(CoreTrackUiItem("medium-track", "Medium track", "Artist")),
                            ),
                            detailState = CoreProductDetailUiState(target = selectedDetail),
                            selectedDetail = selectedDetail,
                            searchListAnchor = null,
                            libraryListAnchor = null,
                            actions = actions,
                        )
                    }
                }
            }
        }

        composeRule.runOnIdle { assertEquals(UiWidthClass.Medium, observedWidthClass) }
        composeRule.onNodeWithTag("library-product-list")
            .performScrollToNode(hasText("Medium track"))
        composeRule.onNodeWithText("Medium track").performClick()
        composeRule.runOnIdle { assertEquals("medium-track", playedTrack) }
        composeRule.onNodeWithTag("library-product-list").assertIsDisplayed()
        assertTrue(composeRule.onAllNodesWithText(context.getString(R.string.detail_unavailable)).fetchSemanticsNodes().isEmpty())
        composeRule.onNodeWithText("Medium track").assertIsDisplayed()
    }

    @Test
    fun artistBrowseKeepsSameNamesAsSeparateCanonicalTargets() {
        var opened: String? = null
        composeRule.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = LibraryScreenUiState(
                        localMode = false,
                        tracks = emptyList(),
                        section = LibrarySection.Artists,
                        artists = listOf(
                            CoreArtistUiItem(ARTIST_ONE, "Same", "First"),
                            CoreArtistUiItem(ARTIST_TWO, "Same", "Second"),
                        ),
                        artistBrowseState = ArtistBrowseUiState.Ready,
                    ),
                    contentPadding = PaddingValues(),
                    onAddLocal = {},
                    onSelect = {},
                    onRemoveOrRestore = {},
                    onLike = {},
                    onOpenCollection = { section, id ->
                        if (section == LibrarySection.Artists) opened = id
                    },
                )
            }
        }

        composeRule.onNodeWithText("Second").performClick()
        composeRule.runOnIdle { assertEquals(ARTIST_TWO, opened) }
    }

    @Test
    fun artistDetailLinksCanonicalMemberAndOwnerVisibleRelease() {
        var opened: DetailTarget? = null
        val profile = ServerProfileId(PROFILE)
        val artist = ArtistDetail(
            ArtistSummary(ArtistKey(profile, ArtistId(ARTIST_ONE)), "Same", null, null, "First", null, null),
            listOf(
                ArtistCredit(
                    ArtistCreditId(CREDIT_ONE),
                    "Same feat. Guest",
                    listOf(ArtistCreditMember(ArtistId(ARTIST_TWO), 1, "Canonical member", "", "PRIMARY")),
                ),
                ArtistCredit(ArtistCreditId(CREDIT_TWO), "Legacy unresolved", emptyList()),
            ),
        )
        composeRule.setContent {
            AutPlayTheme {
                CoreProductDetailScreen(
                    state = CoreProductDetailUiState(
                        target = DetailTarget(DetailKind.Artist, ARTIST_ONE),
                        artist = artist,
                        artistAppearances = listOf(
                            ArtistAppearance(
                                ArtistCreditId(CREDIT_ONE),
                                "RELEASE",
                                ServerId(RELEASE_ONE),
                                "Owned release",
                                ArtistLocalTarget.Release("local-release"),
                            ),
                            ArtistAppearance(
                                ArtistCreditId(CREDIT_TWO),
                                "FUTURE_SUBJECT",
                                ServerId(RECORDING_UNKNOWN),
                                "Unknown relation",
                                null,
                            ),
                        ),
                    ),
                    onOpenDetail = { opened = it },
                )
            }
        }

        composeRule.onNodeWithText("Canonical member").performClick()
        composeRule.runOnIdle {
            assertEquals(DetailTarget(DetailKind.Artist, ARTIST_TWO), opened)
        }
        composeRule.onNodeWithText(context.getString(R.string.detail_artist_credit_unresolved))
            .performScrollTo().assertIsDisplayed().assertHasNoClickAction()
        composeRule.onNodeWithText("Owned release").performScrollTo().performClick()
        composeRule.runOnIdle {
            assertEquals(DetailTarget(DetailKind.Release, "local-release"), opened)
        }
        composeRule.onNodeWithText("Unknown relation").performScrollTo()
            .assertIsDisplayed().assertHasNoClickAction()
    }

    private companion object {
        const val PROFILE = "10000000-0000-4000-8000-000000000001"
        const val ARTIST_ONE = "20000000-0000-4000-8000-000000000001"
        const val ARTIST_TWO = "20000000-0000-4000-8000-000000000002"
        const val CREDIT_ONE = "30000000-0000-4000-8000-000000000001"
        const val CREDIT_TWO = "30000000-0000-4000-8000-000000000002"
        const val RELEASE_ONE = "50000000-0000-4000-8000-000000000001"
        const val RECORDING_UNKNOWN = "40000000-0000-4000-8000-000000000099"
    }
}
