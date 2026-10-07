package app.autplay.ui.statistics

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.background
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.TextButton
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
import app.autplay.application.statistics.OwnerProfileStatisticsState
import app.autplay.ui.AutPlayTokens

@Composable
fun OwnerProfileStatisticsCard(
    state: OwnerProfileStatisticsState,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
) {
    StatisticsSurface(modifier) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(R.string.statistics_title),
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.weight(1f).semantics { heading() },
            )
            TextButton(onClick = onRefresh, enabled = !state.loading) {
                Text(stringResource(R.string.profile_stats_refresh))
            }
        }
        Text(stringResource(R.string.profile_stats_saved_history), color = AutPlayTokens.colors.mutedText,
            style = MaterialTheme.typography.bodySmall)
        if (state.loading) {
            LinearProgressIndicator(Modifier.fillMaxWidth())
            Text(stringResource(R.string.profile_stats_refreshing), color = AutPlayTokens.colors.mutedText)
        }
        if (state.refreshFailed) {
            Text(stringResource(if (state.snapshot == null) R.string.profile_stats_load_error else R.string.profile_stats_refresh_error),
                color = MaterialTheme.colorScheme.error)
        }
        val statistics = state.snapshot
        if (statistics != null) {
            Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(stringResource(R.string.profile_stats_listened_time), style = MaterialTheme.typography.labelLarge,
                    color = AutPlayTokens.colors.mutedText)
                Text(ownerDurationText(statistics.listenedMs), style = MaterialTheme.typography.displaySmall,
                    color = MaterialTheme.colorScheme.primary)
                Text(stringResource(R.string.profile_stats_time_note), style = MaterialTheme.typography.bodySmall,
                    color = AutPlayTokens.colors.mutedText)
            }
            if (statistics.listenedMs == 0L) Text(stringResource(R.string.statistics_empty))
            RankingSection(stringResource(R.string.profile_stats_top_genres), statistics.topGenres.isEmpty(),
                stringResource(R.string.profile_stats_genres_empty)) {
                Text(stringResource(R.string.profile_stats_genres_note), style = MaterialTheme.typography.bodySmall,
                    color = AutPlayTokens.colors.mutedText)
                statistics.topGenres.forEachIndexed { index, genre ->
                    RankingRow(index + 1, genre.genre, null, genre.listenedMs, statistics.topGenres.first().listenedMs)
                }
            }
            RankingSection(stringResource(R.string.profile_stats_top_tracks), statistics.topTracks.isEmpty(),
                stringResource(R.string.profile_stats_tracks_empty)) {
                statistics.topTracks.forEachIndexed { index, track ->
                    RankingRow(index + 1, track.title ?: stringResource(R.string.track_untitled),
                        track.artistName ?: stringResource(R.string.library_unknown_artist),
                        track.listenedMs, statistics.topTracks.first().listenedMs)
                }
            }
            RankingSection(stringResource(R.string.profile_stats_top_artists), statistics.topArtists.isEmpty(),
                stringResource(R.string.profile_stats_artists_empty)) {
                statistics.topArtists.forEachIndexed { index, artist ->
                    RankingRow(index + 1, artist.artistName ?: stringResource(R.string.library_unknown_artist),
                        null, artist.listenedMs, statistics.topArtists.first().listenedMs)
                }
            }
        } else if (!state.loading && !state.refreshFailed) {
            Text(stringResource(R.string.profile_stats_initial))
        }
    }
}

@Composable
private fun RankingSection(title: String, empty: Boolean, emptyText: String, content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = 12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() })
        if (empty) Text(emptyText, color = AutPlayTokens.colors.mutedText, style = MaterialTheme.typography.bodyMedium)
        else content()
    }
}

@Composable
private fun RankingRow(rank: Int, title: String, subtitle: String?, listenedMs: Long, maximumMs: Long) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.Top) {
        Text(rank.toString(), modifier = Modifier.widthIn(min = 20.dp), style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.primary)
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (subtitle != null) Text(subtitle, style = MaterialTheme.typography.bodySmall,
                color = AutPlayTokens.colors.mutedText, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(ownerDurationText(listenedMs), style = MaterialTheme.typography.labelMedium, color = AutPlayTokens.colors.mutedText)
            Box(Modifier.fillMaxWidth().height(3.dp).clip(MaterialTheme.shapes.small).background(AutPlayTokens.colors.border)) {
                Box(Modifier.fillMaxWidth((listenedMs.toDouble() / maximumMs.coerceAtLeast(1)).toFloat().coerceIn(0f, 1f))
                    .height(3.dp).background(MaterialTheme.colorScheme.primary))
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

private fun Long.pluralQuantity(): Int = coerceIn(0L, Int.MAX_VALUE.toLong()).toInt()

@Composable
private fun ownerDurationText(listenedMs: Long): String = when (listenedMs) {
    in 1L..999L -> stringResource(R.string.profile_stats_duration_subsecond)
    in 1_000L..59_999L -> stringResource(R.string.profile_stats_duration_seconds, listenedMs / 1_000L)
    else -> durationText(listenedMs)
}

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
            modifier = Modifier.fillMaxWidth().padding(AutPlayTokens.dimensions.screenPadding),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            content()
        }
    }
}
