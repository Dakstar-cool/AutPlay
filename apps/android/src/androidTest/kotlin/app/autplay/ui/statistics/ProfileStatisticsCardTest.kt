package app.autplay.ui.statistics

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.statistics.OwnerProfileStatistics
import app.autplay.application.statistics.OwnerProfileStatisticsState
import app.autplay.application.statistics.OwnerTopArtist
import app.autplay.application.statistics.OwnerTopGenre
import app.autplay.application.statistics.OwnerTopTrack
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ProfileStatisticsCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val snapshot = OwnerProfileStatistics(
        throughMs = 1,
        listenedMs = 3_600_000,
        topGenres = listOf(OwnerTopGenre("Jazz", 3_600_000)),
        topTracks = listOf(OwnerTopTrack("local-only", "Local song", "Local artist", 3, 3_600_000)),
        topArtists = listOf(OwnerTopArtist("Local artist", 3, 3_600_000)),
    )

    @Test
    fun ownerCardShowsOnlySavedTimeAndThreeTopsWithWorkingRefresh() {
        var refreshes = 0
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    OwnerProfileStatisticsCard(OwnerProfileStatisticsState(snapshot), { refreshes++ })
                }
            }
        }
        compose.onNodeWithText(context.getString(R.string.profile_stats_refresh)).performClick()
        assertEquals(1, refreshes)
        compose.onNodeWithText(context.getString(R.string.profile_stats_listened_time)).assertIsDisplayed()
        compose.onNodeWithText("Jazz").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Local song").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_stats_top_artists)).performScrollTo().assertIsDisplayed()
        listOf(7, 30, 365).forEach { days ->
            compose.onNodeWithText(context.resources.getQuantityString(R.plurals.statistics_window_days, days, days)).assertDoesNotExist()
        }
    }

    @Test
    fun refreshingKeepsSavedDataAndDisablesRepeatedTaps() {
        compose.setContent {
            AutPlayTheme { OwnerProfileStatisticsCard(OwnerProfileStatisticsState(snapshot, loading = true), {}) }
        }
        compose.onNodeWithText(context.getString(R.string.profile_stats_refresh)).assertIsNotEnabled()
        compose.onNodeWithText(context.getString(R.string.profile_stats_refreshing)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_stats_listened_time)).assertIsDisplayed()
    }

    @Test
    fun emptyGenresAndErrorAreExplicitWhileOldSnapshotRemainsVisible() {
        compose.setContent {
            AutPlayTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    OwnerProfileStatisticsCard(OwnerProfileStatisticsState(snapshot.copy(topGenres = emptyList()), refreshFailed = true), {})
                }
            }
        }
        compose.onNodeWithText(context.getString(R.string.profile_stats_refresh_error)).assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.profile_stats_genres_empty)).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Local song").performScrollTo().assertIsDisplayed()
    }
}
