package app.autplay.ui.statistics

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.social.FriendProfileStatisticsState
import app.autplay.application.social.SharedProfileStatisticsWindow
import app.autplay.application.social.SharedStatisticsWindowKind
import app.autplay.application.statistics.OwnerProfileStatistics
import app.autplay.application.statistics.OwnerStatisticsWindow
import app.autplay.ui.AutPlayTokens

@Composable
fun OwnerProfileStatisticsCard(
    statistics: OwnerProfileStatistics?,
    modifier: Modifier = Modifier,
) {
    StatisticsSurface(modifier) {
        Text(
            stringResource(R.string.statistics_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() },
        )
        Text(
            stringResource(R.string.statistics_owner_private_note),
            color = AutPlayTokens.colors.mutedText,
        )
        when {
            statistics == null -> Text(stringResource(R.string.statistics_loading))
            statistics.last365Days.playSessionCount == 0L -> Text(stringResource(R.string.statistics_empty))
            else -> {
                OwnerWindow(statistics.last7Days)
                OwnerWindow(statistics.last30Days)
                OwnerWindow(statistics.last365Days)
                if (statistics.topTracks30Days.isNotEmpty()) {
                    Text(stringResource(R.string.statistics_top_tracks), style = MaterialTheme.typography.titleMedium)
                    statistics.topTracks30Days.forEachIndexed { index, track ->
                        Text(
                            stringResource(
                                R.string.statistics_top_track_row,
                                index + 1,
                                track.title ?: stringResource(R.string.track_untitled),
                                track.artistName ?: stringResource(R.string.library_unknown_artist),
                                playCountText(track.playSessionCount),
                            ),
                        )
                    }
                }
                if (statistics.topArtists30Days.isNotEmpty()) {
                    Text(stringResource(R.string.statistics_top_artists), style = MaterialTheme.typography.titleMedium)
                    statistics.topArtists30Days.forEachIndexed { index, artist ->
                        Text(
                            stringResource(
                                R.string.statistics_top_artist_row,
                                index + 1,
                                artist.artistName ?: stringResource(R.string.library_unknown_artist),
                                playCountText(artist.playSessionCount),
                            ),
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun FriendProfileStatisticsCard(
    state: FriendProfileStatisticsState,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state == FriendProfileStatisticsState.Idle) return
    StatisticsSurface(modifier) {
        Text(
            stringResource(R.string.statistics_friend_title),
            style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.semantics { heading() },
        )
        when (state) {
            FriendProfileStatisticsState.Idle -> Unit
            is FriendProfileStatisticsState.Loading -> Text(stringResource(R.string.statistics_loading))
            is FriendProfileStatisticsState.Unavailable -> Text(stringResource(R.string.statistics_unavailable))
            is FriendProfileStatisticsState.Visible -> {
                Text(
                    stringResource(R.string.statistics_friend_through, state.statistics.throughUtcDate),
                    color = AutPlayTokens.colors.mutedText,
                )
                state.statistics.knownWindows().forEach { window -> SharedWindow(window) }
            }
        }
        OutlinedButton(
            onClick = onDismiss,
            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
        ) { Text(stringResource(R.string.statistics_close)) }
    }
}

@Composable
private fun OwnerWindow(window: OwnerStatisticsWindow) {
    Text(
        pluralStringResource(R.plurals.statistics_window_days, window.days, window.days),
        style = MaterialTheme.typography.titleMedium,
    )
    Text(
        stringResource(
            R.string.statistics_window_summary,
            sessionCountText(window.playSessionCount),
            durationText(window.listenedMs),
            trackCountText(window.uniqueTrackCount),
        ),
    )
}

@Composable
private fun SharedWindow(window: SharedProfileStatisticsWindow) {
    val days = when (window.kind) {
        SharedStatisticsWindowKind.Last7CompleteDays -> 7
        SharedStatisticsWindowKind.Last30CompleteDays -> 30
        SharedStatisticsWindowKind.Last365CompleteDays -> 365
        is SharedStatisticsWindowKind.Unknown -> return
    }
    Text(
        pluralStringResource(R.plurals.statistics_completed_window_days, days, days),
        style = MaterialTheme.typography.titleMedium,
    )
    Text(
        stringResource(
            R.string.statistics_window_summary,
            sessionCountText(window.playSessionCount),
            durationText(window.listenedMs),
            trackCountText(window.uniqueTrackCount),
        ),
    )
}

@Composable
private fun sessionCountText(count: Long): String = pluralStringResource(
    R.plurals.statistics_session_count,
    count.pluralQuantity(),
    count,
)

@Composable
private fun trackCountText(count: Long): String = pluralStringResource(
    R.plurals.statistics_track_count,
    count.pluralQuantity(),
    count,
)

@Composable
private fun playCountText(count: Long): String = pluralStringResource(
    R.plurals.statistics_play_count,
    count.pluralQuantity(),
    count,
)

private fun Long.pluralQuantity(): Int = coerceIn(0L, Int.MAX_VALUE.toLong()).toInt()

@Composable
private fun durationText(listenedMs: Long): String {
    val totalMinutes = listenedMs / 60_000L
    val hours = totalMinutes / 60L
    val minutes = totalMinutes % 60L
    return if (hours > 0) {
        stringResource(R.string.statistics_duration_hours_minutes, hours, minutes)
    } else {
        stringResource(R.string.statistics_duration_minutes, minutes)
    }
}

@Composable
private fun StatisticsSurface(
    modifier: Modifier,
    content: @Composable () -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        color = AutPlayTokens.colors.glassSurface,
        border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
        tonalElevation = 1.dp,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            content()
        }
    }
}
