package app.autplay.ui

import android.content.Context
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.stringResource
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.library.CoreReleaseDetailTrack
import app.autplay.application.server.AlbumEditionChoice
import app.autplay.application.server.AlbumTracklist
import app.autplay.application.artist.musicSearchKey
import app.autplay.application.sync.ClientEventBinding
import kotlinx.coroutines.CancellationException

data class AlbumTracklistState(val result: AlbumTracklist? = null, val loading: Boolean = false,
    val failed: Boolean = false, val selected: AlbumEditionChoice? = null,
    val choose: (AlbumEditionChoice?) -> Unit = {})

@Composable
internal fun rememberAlbumTracklist(context: Context, binding: ClientEventBinding?, albumKey: String?,
    mbid: String?, title: String?, artist: String?, year: String?): AlbumTracklistState? {
    if (albumKey == null || binding == null) return null
    return key(binding, albumKey, mbid, title, artist, year) {
        var choice by remember { mutableStateOf<AlbumEditionChoice?>(null) }
        var attempt by remember { mutableIntStateOf(0) }
        var result by remember(choice, attempt) { mutableStateOf<AlbumTracklist?>(null) }
        var failed by remember(choice, attempt) { mutableStateOf(false) }
        LaunchedEffect(choice, attempt) {
            try {
                val client = AutPlayRuntime.serverFeatures(context, binding)
                result = client.albumTracklist(choice?.id?.takeIf { choice?.source == "musicbrainz" } ?: mbid,
                    choice?.id?.takeIf { choice?.source == "discogs" }, title, artist, year)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { failed = true }
        }
        AlbumTracklistState(result, result == null && !failed, failed, choice) { selected ->
            choice = selected
            attempt++
        }
    }
}

internal fun localEditionRows(result: AlbumTracklist?, local: List<CoreReleaseDetailTrack>): Map<Int, CoreReleaseDetailTrack> {
    if (result == null || local.isEmpty()) return emptyMap()
    val byId = local.groupBy { it.serverRecordingId }
    val byName = local.groupBy { musicSearchKey(it.title) to musicSearchKey(it.artistName) }
    return buildMap {
        result.tracks.forEachIndexed { index, row ->
            val identified = row.recording?.let { byId[it] }.orEmpty()
            val candidates = identified.ifEmpty {
                byName[musicSearchKey(row.title) to musicSearchKey(row.artist.orEmpty())].orEmpty().filter { track ->
                    val position = track.numberText ?: track.sequenceNo?.toString()
                    !position.isNullOrBlank() && position == row.position &&
                        (track.mediumPosition ?: 1) == (row.disc ?: 1) &&
                        (row.recording == null || track.serverRecordingId == null || row.recording == track.serverRecordingId) &&
                        (track.durationMs == null || row.durationMs?.let { kotlin.math.abs(it - track.durationMs) <= 3000L } != false)
                }
            }
            (candidates.singleOrNull() ?: candidates.singleOrNull { it.localUserTrackRefId != null })?.let { put(index, it) }
        }
    }
}

internal fun LazyListScope.albumTracklistItems(state: AlbumTracklistState, local: Map<Int, CoreReleaseDetailTrack>,
    play: (CoreReleaseDetailTrack) -> Unit, add: (String) -> Unit) {
    val result = state.result
    item(key = "online-tracklist-header") {
        Column {
            Text(stringResource(R.string.catalog_edition_tracks), style = MaterialTheme.typography.titleMedium)
            when {
                state.loading -> LinearProgressIndicator()
                state.failed -> {
                    CatalogueErrorText("music_catalogue_unavailable")
                    TextButton(onClick = { state.choose(state.selected) }) { Text(stringResource(R.string.music_retry)) }
                }
                result?.edition != null -> {
                    Text(stringResource(R.string.catalogue_vault_count, result.tracks.count { it.inVault == true }, result.tracks.size))
                    val edition = result.edition
                    if (edition?.source == "discogs") {
                        val uri = LocalUriHandler.current
                        TextButton(onClick = { uri.openUri("https://www.discogs.com/release/" + edition.id) }) {
                            Text(stringResource(R.string.album_discogs_credit))
                        }
                    }
                }
                result?.choices?.isNotEmpty() == true -> Text(stringResource(R.string.album_select_edition))
                else -> Text(stringResource(R.string.album_no_tracklist))
            }
            if (state.selected != null && !state.loading) {
                TextButton(onClick = { state.choose(null) }) { Text(stringResource(R.string.album_change_edition)) }
            }
            if (state.failed || result != null && result.edition == null) {
                var input by remember { mutableStateOf("") }
                OutlinedTextField(input, { input = it.take(120) }, singleLine = true,
                    label = { Text(stringResource(R.string.album_discogs_id)) })
                val id = app.autplay.application.server.discogsReleaseId(input)
                if (id != null) TextButton(onClick = { state.choose(AlbumEditionChoice("discogs", id, "")) }) {
                    Text(stringResource(R.string.catalog_edition_tracks))
                }
            }
        }
    }
    result ?: return
    itemsIndexed(result.choices) { _, edition ->
        TextButton(onClick = { state.choose(edition) }) {
            Text(edition.label + " · " + edition.source + " #" + edition.id)
        }
    }
    val multipleDiscs = result.tracks.any { (it.disc ?: 1) > 1 }
    itemsIndexed(result.tracks, key = { index, _ -> "edition-position-$index" }) { index, row ->
        val match = local[index]?.takeIf { it.localUserTrackRefId != null }
        val number = row.position.ifBlank { (index + 1).toString() }
        CollectionTrackRow(if (multipleDiscs && row.disc != null) "${row.disc}.$number" else number,
            row.title, row.artist, row.durationMs, match?.let { { play(it) } }, "edition-position-$index",
            onAdd = match?.localUserTrackRefId?.let { { add(it) } },
            status = stringResource(when (row.inVault) {
                true -> R.string.catalogue_in_vault
                false -> R.string.album_vault_absent
                null -> R.string.album_vault_unknown
            }))
    }
}
