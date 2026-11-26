package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.performTextReplacement
import app.autplay.ui.core.LibrarySection
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.assertHasNoClickAction
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LibrarySearchRefreshTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun personalSearchKeepsEachTabScopedAndOpensMatchingPlaylist() {
        val state = mutableStateOf(LibraryScreenUiState(false, listOf(CoreTrackUiItem("s", "Solo", "25/17")),
            section = LibrarySection.Artists, artists = listOf(CoreArtistUiItem("a", "25/17", null)),
            albums = listOf(CoreCollectionUiItem("r", "Альбом 25/17", null)),
            playlists = listOf(CoreCollectionUiItem("p", "Для дороги", null), CoreCollectionUiItem("hidden", "Дома", null))))
        var opened: String? = null
        compose.setContent { AutPlayTheme {
            LibraryProductScreen(state.value, PaddingValues(), {}, {}, {}, {},
                onSectionChange = { state.value = state.value.copy(section = it) },
                onOpenCollection = { _, id -> opened = id })
        } }
        val input = compose.onNodeWithTag("personal-library-search")
        input.performTextReplacement("25-17")
        compose.onNodeWithText("25/17").assertIsDisplayed()
        compose.onNodeWithText("Solo").assertDoesNotExist()
        compose.runOnIdle { state.value = state.value.copy(section = LibrarySection.Albums) }
        input.performTextReplacement("Альбом")
        compose.onNodeWithText("Альбом 25/17").assertIsDisplayed()
        compose.runOnIdle { state.value = state.value.copy(section = LibrarySection.Tracks) }
        input.performTextReplacement("Sol")
        compose.onNodeWithText("Solo").assertIsDisplayed()
        compose.runOnIdle { state.value = state.value.copy(section = LibrarySection.Playlists) }
        input.performTextReplacement("дороги")
        compose.onNodeWithText("Дома").assertDoesNotExist()
        compose.onNodeWithText("Для дороги").performClick()
        compose.runOnIdle { assertEquals("p", opened) }
    }

    @Test
    fun addActionRemainsAccessibleAtNarrowWidthAndDisablesDuringImport() {
        var imports = 0
        val importing = mutableStateOf(false)
        compose.setContent {
            AutPlayTheme {
                Box(Modifier.width(320.dp)) {
                    LibraryProductScreen(LibraryScreenUiState(true, emptyList(), importingLocalTrack = importing.value),
                        PaddingValues(), onAddLocal = { imports++ }, onSelect = {}, onRemoveOrRestore = {}, onLike = {})
                }
            }
        }
        compose.onNodeWithTag("library-add").assertIsDisplayed().performClick()
        val toolbar = listOf("library-favorites", "library-device", "library-downloads", "library-sort", "library-add")
            .map { compose.onNodeWithTag(it).assertIsDisplayed().getUnclippedBoundsInRoot() }
        assertTrue(toolbar.zipWithNext().all { (left, right) -> left.right <= right.left && left.top == right.top })
        val search = compose.onNodeWithTag("personal-library-search").getUnclippedBoundsInRoot()
        val tabs = compose.onNodeWithTag("library-sections").getUnclippedBoundsInRoot()
        assertTrue(search.bottom <= tabs.top && tabs.bottom < toolbar.first().top)
        compose.onNodeWithText(context.getString(R.string.library_favorites_shortcut)).assertDoesNotExist()
        compose.runOnIdle { assertEquals(1, imports); importing.value = true }
        compose.onNodeWithTag("library-add").assertIsNotEnabled()
    }

    @Test
    fun shortTrackTitleFitsAt375DpAndTappingTitleStartsThatTrack() {
        val played = mutableListOf<String>()
        val title = "First Light.wav"
        compose.setContent {
            AutPlayTheme {
                Box(Modifier.width(375.dp)) {
                    LibraryProductScreen(
                        state = LibraryScreenUiState(true, listOf(CoreTrackUiItem("first", title, "Unknown artist"))),
                        contentPadding = PaddingValues(), onAddLocal = {}, onSelect = { played += it },
                        onRemoveOrRestore = {}, onLike = {},
                    )
                }
            }
        }
        compose.onNodeWithTag("library-product-list").performScrollToNode(hasTestTag("library-track-first"))
        compose.onNodeWithText(title, useUnmergedTree = true)
            .performSemanticsAction(SemanticsActions.GetTextLayoutResult) { action ->
                val layouts = mutableListOf<TextLayoutResult>()
                assertTrue(action(layouts))
                val layout = layouts.single()
                val detail = "Title layout: ${layout.size}, paragraph ${layout.multiParagraph.width} x " +
                    "${layout.multiParagraph.height}, ${layout.lineCount} lines, " +
                    "widthOverflow=${layout.didOverflowWidth}, heightOverflow=${layout.didOverflowHeight}"
                android.util.Log.i("FirstUsersLayout", detail)
                assertTrue(detail, layout.lineCount > 0)
                assertEquals(detail, title.length, layout.getLineEnd(layout.lineCount - 1, visibleEnd = true))
                for (line in 0 until layout.lineCount) assertFalse(detail, layout.isLineEllipsized(line))
            }
        compose.onNodeWithText(title).performClick()
        compose.runOnIdle { assertEquals(listOf("first"), played) }
    }

    @Test
    fun reachingEndOfVaultLoadsMoreAndKeepsEarlierMatches() {
        val initial = (1..100).map { VaultSearchUiItem("id-$it", "Track $it", "Artist", "VAULT", "VAULT", true) }
        val state = mutableStateOf(SearchScreenUiState("Track", emptyList(), searched = true, vaultAvailable = true,
            vaultSearched = true, vaultResults = initial, vaultNextCursor = "next"))
        compose.setContent {
            AutPlayTheme {
                SearchProductScreen(state.value, PaddingValues(), {}, {}, {}, onMoreVault = {
                    state.value = state.value.copy(vaultNextCursor = null, vaultResults = initial +
                        VaultSearchUiItem("last", "Last track", "Artist", "VAULT", "VAULT", true))
                })
            }
        }
        val list = compose.onNodeWithTag("search-product-list")
        list.performScrollToNode(hasTestTag("vault-result-id-100"))
        list.performTouchInput { swipeUp() }
        compose.waitUntil(8000) { state.value.vaultNextCursor == null }
        list.performScrollToNode(hasTestTag("vault-result-last"))
        compose.onNodeWithTag("vault-result-last").assertIsDisplayed()
        list.performScrollToNode(hasTestTag("vault-result-id-1"))
        compose.onNodeWithTag("vault-result-id-1").assertIsDisplayed()
    }

    @Test
    fun connectedSearchDisplaysVaultEvenWhenOldSavedScopeWasOff() {
        compose.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = SearchScreenUiState(
                        query = "music",
                        results = listOf(CoreTrackUiItem("local-a", "Local match", null)),
                        searched = true,
                        vaultAvailable = true,
                        vaultSelected = false,
                        vaultSearched = true,
                        vaultResults = listOf(VaultSearchUiItem("vault-a", "Vault match", null, "VAULT", "AVAILABLE", true)),
                    ),
                    contentPadding = PaddingValues(),
                    onQueryChange = {}, onSearch = {}, onPlay = {},
                )
            }
        }
        assertTrue(compose.onAllNodesWithText(context.getString(R.string.search_local_scope)).fetchSemanticsNodes().isEmpty())
        assertTrue(compose.onAllNodesWithText(context.getString(R.string.search_vault_scope)).fetchSemanticsNodes().isEmpty())
        compose.onNodeWithTag("search-product-list").performScrollToNode(hasTestTag("vault-result-vault-a"))
        compose.onNodeWithTag("vault-result-vault-a").assertIsDisplayed()
    }

    @Test
    fun mixedSearchShowsSourceOfflineStatusAndOneRowPerLinkedRecording() {
        val localPlays = mutableListOf<String>()
        val serverPlays = mutableListOf<String>()
        val localized = app.autplay.localizedAppContext(context, AppLanguage.Russian)
        val state = SearchScreenUiState(
            query = "Тихий свет",
            results = listOf(
                CoreTrackUiItem("shared", "Тихий свет", "Маяк"),
                CoreTrackUiItem("phone", "Тихий свет (демо)", "Маяк"),
            ),
            searched = true, vaultAvailable = true, vaultSearched = true,
            offlineTrackIds = setOf("shared", "phone"),
            vaultResults = listOf(
                VaultSearchUiItem("overlap", "Тихий свет", "Маяк", "VAULT", "VAULT", true, "shared"),
                VaultSearchUiItem("live", "Тихий свет", "Маяк", "VAULT", "VAULT", true, "other-recording"),
                VaultSearchUiItem("archive", "Тихий свет (архив)", "Маяк", "IMPORT", "PENDING", false),
            ),
        )
        compose.setContent {
            androidx.compose.runtime.CompositionLocalProvider(
                androidx.compose.ui.platform.LocalContext provides localized,
                androidx.compose.ui.platform.LocalResources provides localized.resources,
                androidx.compose.ui.platform.LocalConfiguration provides localized.resources.configuration,
            ) {
                AutPlayTheme {
                    AutPlayAdaptiveShell(selectedDestination = UiDestination.Search, onDestinationSelected = {}) { _, padding, _ ->
                        SearchProductScreen(state, padding, {}, {}, { localPlays += it }, { serverPlays += it })
                    }
                }
            }
        }
        val list = compose.onNodeWithTag("search-product-list")
        fun row(tag: String) = compose.onNodeWithTag(tag).also { list.performScrollToNode(hasTestTag(tag)) }
        compose.waitForIdle()
        app.autplay.saveUiScreenshot("search-mixed")
        row("local-result-shared").assertTextContains(localized.getString(R.string.search_source_both_offline)).performClick()
        row("local-result-phone").assertTextContains(localized.getString(R.string.search_source_phone_offline))
        row("vault-result-live").assertTextContains(localized.getString(R.string.search_source_server_online)).performClick()
        row("vault-result-archive").assertHasNoClickAction()
        compose.onNodeWithText(localized.getString(R.string.search_source_server_unavailable), useUnmergedTree = true).assertIsDisplayed()
        compose.onNodeWithTag("vault-result-overlap").assertDoesNotExist()
        val count = localized.resources.getQuantityString(R.plurals.ui_library_refresh_search_count, 4, 4)
        list.performScrollToNode(androidx.compose.ui.test.hasText(count))
        compose.onAllNodesWithText(count).assertCountEquals(1)
        compose.runOnIdle {
            assertEquals(listOf("shared"), localPlays)
            assertEquals(listOf("live"), serverPlays)
        }
    }

    @Test
    fun serverErrorKeepsLocalResultPlayable() {
        val played = mutableListOf<String>()
        compose.setContent {
            AutPlayTheme {
                SearchProductScreen(
                    state = SearchScreenUiState(
                        query = "music",
                        results = listOf(CoreTrackUiItem("local-a", "Local match", null)),
                        searched = true,
                        vaultAvailable = true,
                        vaultSelected = true,
                        vaultSearched = true,
                        vaultError = true,
                    ),
                    contentPadding = PaddingValues(),
                    onQueryChange = {}, onSearch = {}, onPlay = { played += it },
                )
            }
        }
        compose.onNodeWithTag("search-product-list").performScrollToNode(hasTestTag("local-result-local-a"))
        compose.onNodeWithTag("local-result-local-a").performClick()
        compose.runOnIdle { assertEquals(listOf("local-a"), played) }
    }

    @Test
    fun trackTapPlaysAndKeepsLibraryForEveryWidthIncludingOldTrackDetail() {
        val played = mutableListOf<String>()
        var selections = 0
        val width = mutableStateOf(UiWidthClass.Compact)
        compose.setContent {
            AutPlayTheme {
                CoreProductRouteRenderer(
                    destination = UiDestination.Library,
                    widthClass = width.value,
                    contentPadding = PaddingValues(),
                    homeState = HomeScreenUiState(false, false, false, emptyList(), emptyList()),
                    searchState = SearchScreenUiState("", emptyList(), false),
                    libraryState = LibraryScreenUiState(true, listOf(CoreTrackUiItem("track-a", "Library song", null))),
                    detailState = CoreProductDetailUiState(target = DetailTarget(DetailKind.Track, "track-a")),
                    selectedDetail = DetailTarget(DetailKind.Track, "track-a"),
                    searchListAnchor = null, libraryListAnchor = null,
                    actions = actions(play = { played += it }, select = { selections++ }),
                )
            }
        }
        for (size in listOf(UiWidthClass.Compact, UiWidthClass.Medium, UiWidthClass.Expanded)) {
            compose.runOnIdle { width.value = size }
            compose.onNodeWithTag("library-product-list").performScrollToNode(hasTestTag("library-track-track-a"))
            compose.onNodeWithTag("library-track-track-a").performClick()
            compose.onNodeWithTag("library-product-list").assertIsDisplayed()
        }
        compose.runOnIdle {
            assertEquals(listOf("track-a", "track-a", "track-a"), played)
            assertEquals(0, selections)
        }
    }

    @Test
    fun rowLikeAndPlayNextUseExactTrackWithoutStartingPlayback() {
        val liked = mutableListOf<String>()
        val next = mutableListOf<String>()
        val played = mutableListOf<String>()
        compose.setContent {
            AutPlayTheme {
                LibraryProductScreen(
                    state = LibraryScreenUiState(true, listOf(CoreTrackUiItem("track-a", "Same title", null))),
                    contentPadding = PaddingValues(),
                    onAddLocal = {}, onSelect = { played += it }, onRemoveOrRestore = {},
                    onLike = { liked += it }, onPlayNext = { next += it },
                )
            }
        }
        compose.onNodeWithTag("library-product-list").performScrollToNode(hasTestTag("library-track-track-a"))
        compose.onNodeWithTag("library-like-track-a").performClick()
        compose.onNodeWithTag("library-actions-track-a").performClick()
        compose.onNodeWithText(context.getString(R.string.queue_play_next)).performClick()
        compose.runOnIdle {
            assertEquals(listOf("track-a"), liked)
            assertEquals(listOf("track-a"), next)
            assertTrue(played.isEmpty())
        }
    }

    private fun actions(play: (String) -> Unit, select: (String) -> Unit) = CoreProductRouteActions(
        openListenTogether = {}, recommendationVisible = {}, likeRecommendation = {}, dislikeRecommendation = {},
        retryHome = {}, resumeHomeQueue = {}, openOffline = {}, openProblems = {},
        changeQuery = {}, submitSearch = {}, playSearchResult = {}, changeVaultScope = {}, changeSearchAnchor = {},
        addLocal = {}, selectTrack = select, removeOrRestore = {}, likeTrack = {}, changeLibrarySection = {},
        changeLibrarySort = {}, changeLibraryFilter = {}, openCollection = { _, _ -> }, openDetail = {},
        openReview = {}, changeLibraryAnchor = {}, playTrack = play, playPlaylistEntry = {}, downloadTrack = {},
        repairAccess = {},
    )
}
