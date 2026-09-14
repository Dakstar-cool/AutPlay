package app.autplay.ui.downloads

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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.application.download.DownloadPresentationAction
import app.autplay.application.download.allowedDownloadPresentationActions
import app.autplay.ui.AutPlayCard
import app.autplay.ui.AutPlayStateKind
import app.autplay.ui.AutPlayStateSurface
import app.autplay.ui.AutPlayTokens

data class DownloadsUiActions(
    val play: (DownloadIntentPresentation) -> Unit = {},
    val cancel: (DownloadIntentPresentation) -> Unit = {},
    val retry: (DownloadIntentPresentation) -> Unit = {},
    val downloadSelected: () -> Unit = {},
)

@Composable
fun DownloadsScreen(
    downloads: List<DownloadIntentPresentation>,
    pendingIntentId: String?,
    canDownloadSelected: Boolean,
    actions: DownloadsUiActions,
    contentPadding: PaddingValues,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().testTag("downloads-list"),
        contentPadding = PaddingValues(
            start = 16.dp,
            end = 16.dp,
            top = contentPadding.calculateTopPadding() + 16.dp,
            bottom = contentPadding.calculateBottomPadding() + 24.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item("downloads-heading") { Text(stringResource(R.string.nav_downloads), style = MaterialTheme.typography.headlineLarge) }
        item("downloads-selected") {
            Button(
                modifier = Modifier.fillMaxWidth().testTag("download-selected"),
                enabled = canDownloadSelected,
                onClick = actions.downloadSelected,
            ) { Text(stringResource(R.string.download_selected_track)) }
        }
        if (downloads.isEmpty()) {
            item("downloads-empty") { AutPlayStateSurface(AutPlayStateKind.Empty, stringResource(R.string.downloads_empty)) }
        }
        items(downloads, key = DownloadIntentPresentation::stableId) { download ->
            val allowed = allowedDownloadPresentationActions(download.state)
            val pending = pendingIntentId == download.stableId
            AutPlayCard(modifier = Modifier.testTag("download-${download.stableId}")) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(download.title ?: stringResource(R.string.track_untitled), style = MaterialTheme.typography.titleMedium)
                    Text(download.artist ?: stringResource(R.string.library_unknown_artist), color = AutPlayTokens.colors.mutedText)
                    Text(downloadStateText(download.state), style = MaterialTheme.typography.labelMedium)
                    download.failureCode?.let { Text(downloadFailureText(it), color = MaterialTheme.colorScheme.error) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (DownloadPresentationAction.PLAY in allowed) {
                            Button(
                                modifier = Modifier.testTag("download-play-${download.stableId}"),
                                enabled = !pending,
                                onClick = { actions.play(download) },
                            ) { Text(stringResource(R.string.action_play)) }
                        }
                        if (DownloadPresentationAction.RETRY in allowed) {
                            Button(
                                modifier = Modifier.testTag("download-retry-${download.stableId}"),
                                enabled = !pending,
                                onClick = { actions.retry(download) },
                            ) { Text(stringResource(R.string.action_retry)) }
                        }
                        if (DownloadPresentationAction.CANCEL in allowed) {
                            OutlinedButton(
                                modifier = Modifier.testTag("download-cancel-${download.stableId}"),
                                enabled = !pending,
                                onClick = { actions.cancel(download) },
                            ) { Text(stringResource(R.string.action_cancel)) }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun downloadStateText(state: String): String = stringResource(
    when (state) {
        "REQUESTED" -> R.string.download_state_requested
        "QUEUED" -> R.string.download_state_queued
        "DOWNLOADING" -> R.string.download_state_downloading
        "PAUSED" -> R.string.download_state_paused
        "COMPLETED" -> R.string.download_state_completed
        "FAILED" -> R.string.download_state_failed
        "CANCELLED", "CANCELED" -> R.string.download_state_cancelled
        else -> R.string.download_state_unknown
    },
)

@Composable
private fun downloadFailureText(code: String): String = stringResource(
    when (code) {
        "STORAGE_FULL" -> R.string.download_failure_storage
        "AUTH_EXPIRED", "AUTHORIZATION_DENIED" -> R.string.download_failure_authorization
        "NOT_FOUND" -> R.string.download_failure_not_found
        "NETWORK", "HTTP_SERVER" -> R.string.download_failure_network
        "MALFORMED_MEDIA" -> R.string.download_failure_media
        else -> R.string.download_failure_unknown
    },
)
