package app.autplay.ui.statistics

import androidx.activity.ComponentActivity
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.statistics.OwnerProfileStatistics
import app.autplay.application.statistics.OwnerStatisticsWindow
import app.autplay.application.statistics.OwnerTopArtist
import app.autplay.application.statistics.OwnerTopTrack
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ProfileStatisticsCardTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun ownerCardRendersLocalStatisticsWithoutAnyServerState() {
        val statistics = OwnerProfileStatistics(
            throughMs = 1,
            last7Days = OwnerStatisticsWindow(7, 2, 3_600_000, 1),
            last30Days = OwnerStatisticsWindow(30, 4, 7_200_000, 2),
            last365Days = OwnerStatisticsWindow(365, 9, 10_800_000, 3),
            topTracks30Days = listOf(OwnerTopTrack("local-only", "Local song", "Local artist", 3, 1_000)),
            topArtists30Days = listOf(OwnerTopArtist("Local artist", 3, 1_000)),
        )
        compose.setContent { MaterialTheme { OwnerProfileStatisticsCard(statistics) } }

        compose.onNodeWithText(context.getString(R.string.statistics_title)).assertIsDisplayed()
        compose.onNodeWithText(
            context.resources.getQuantityString(R.plurals.statistics_window_days, 7, 7),
        ).assertIsDisplayed()
        val plays = context.resources.getQuantityString(R.plurals.statistics_play_count, 3, 3L)
        compose.onNodeWithText(
            context.getString(
                R.string.statistics_top_track_row,
                1,
                "Local song",
                "Local artist",
                plays,
            ),
        ).assertIsDisplayed()
    }
}
