package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LibrarySearchRefreshTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

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
        retryHome = {}, resumeHomeQueue = {}, openHomePlaylist = {}, openOffline = {}, openProblems = {},
        changeQuery = {}, submitSearch = {}, playSearchResult = {}, changeVaultScope = {}, changeSearchAnchor = {},
        addLocal = {}, selectTrack = select, removeOrRestore = {}, likeTrack = {}, changeLibrarySection = {},
        changeLibrarySort = {}, changeLibraryFilter = {}, openCollection = { _, _ -> }, openDetail = {},
        openReview = {}, changeLibraryAnchor = {}, playTrack = play, playPlaylistEntry = {}, downloadTrack = {},
        repairAccess = {},
    )
}
