package app.autplay.ui.history

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.history.HistoryCursor
import app.autplay.application.history.HistoryItem
import app.autplay.ui.AutPlayCard
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens
import java.text.DateFormat
import java.util.Date

data class HistoryUiState(
    val items: List<HistoryItem> = emptyList(),
    val loading: Boolean = true,
    val loadingMore: Boolean = false,
    val error: Boolean = false,
    val nextCursor: HistoryCursor? = null,
)

data class HistoryUiActions(
    val retry: () -> Unit = {},
    val loadMore: () -> Unit = {},
    val play: (HistoryItem) -> Unit = {},
    val open: (HistoryItem) -> Unit = {},
    val openLibrary: () -> Unit = {},
)

@Composable
fun HistoryScreen(state: HistoryUiState, actions: HistoryUiActions, contentPadding: PaddingValues) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().testTag("history-list"),
        contentPadding = PaddingValues(
            start = 16.dp,
            end = 16.dp,
            top = contentPadding.calculateTopPadding() + 16.dp,
            bottom = contentPadding.calculateBottomPadding() + 24.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item("history-heading") { Text(stringResource(R.string.nav_history), style = MaterialTheme.typography.headlineLarge) }
        if (state.loading && state.items.isEmpty()) {
            item("history-loading") { AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.history_loading)) }
        } else if (state.error && state.items.isEmpty()) {
            item("history-error") {
                AutPlayStateSurface(
                    AutPlayStateKind.Error,
                    stringResource(R.string.history_error),
                    actionLabel = stringResource(R.string.action_retry),
                    onAction = actions.retry,
                )
            }
        } else if (state.items.isEmpty()) {
            item("history-empty") {
                AutPlayStateSurface(
                    AutPlayStateKind.Empty,
                    stringResource(R.string.history_empty),
                    actionLabel = stringResource(R.string.history_open_library),
                    onAction = actions.openLibrary,
                )
            }
        }
        items(state.items, key = HistoryItem::listeningEventId) { event ->
            AutPlayCard(modifier = Modifier.testTag("history-${event.listeningEventId}")) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(event.title ?: stringResource(R.string.track_untitled), style = MaterialTheme.typography.titleMedium)
                    Text(event.artist ?: stringResource(R.string.library_unknown_artist), color = AutPlayTokens.colors.mutedText)
                    Text(
                        stringResource(
                            R.string.history_listened_at,
                            DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(event.startedAtMs)),
                        ),
                        style = MaterialTheme.typography.bodySmall,
                    )
                    Text(stringResource(R.string.history_played_duration, formatPlayedDuration(event.playedMs)))
                    if (event.excludedFromTaste) Text(stringResource(R.string.history_taste_excluded), color = AutPlayTokens.colors.mutedText)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Button(
                            modifier = Modifier.testTag("history-play-${event.listeningEventId}"),
                            onClick = { actions.play(event) },
                        ) { Text(stringResource(R.string.action_play)) }
                        Button(
                            modifier = Modifier.testTag("history-open-${event.listeningEventId}"),
                            onClick = { actions.open(event) },
                        ) { Text(stringResource(R.string.action_open)) }
                    }
                }
            }
        }
        if (state.nextCursor != null) {
            item("history-more") {
                Button(
                    modifier = Modifier.fillMaxWidth().testTag("history-load-more"),
                    enabled = !state.loadingMore,
                    onClick = actions.loadMore,
                ) { Text(stringResource(if (state.loadingMore) R.string.history_loading_more else R.string.history_load_more)) }
            }
        }
        if (state.error && state.items.isNotEmpty()) {
            item("history-more-error") { Text(stringResource(R.string.history_error), color = MaterialTheme.colorScheme.error) }
        }
    }
}

private fun formatPlayedDuration(playedMs: Long): String {
    val totalSeconds = (playedMs.coerceAtLeast(0) / 1_000)
    return "%d:%02d".format(totalSeconds / 60, totalSeconds % 60)
}
