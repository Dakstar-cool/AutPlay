package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistCredit
import app.autplay.application.artist.ArtistDetail
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreTrackDetail
import app.autplay.application.library.CoreTrackDetailCapability
import app.autplay.application.library.CoreTrackAvailability
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.DetailKind
import app.autplay.ui.playlist.AddTrackToPlaylistDialog
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.ui.playlist.ManualPlaylistEditor
import app.autplay.ui.playlist.ManualPlaylistEntryUi
import app.autplay.ui.playlist.ManualPlaylistText
import app.autplay.ui.playlist.ManualPlaylistUi
import java.text.NumberFormat

public data class CoreProductDetailUiState(
    public val target: DetailTarget?,
    public val loading: Boolean = false,
    public val error: Boolean = false,
    public val track: CoreTrackDetail? = null,
    public val release: CoreReleaseDetail? = null,
    public val playlist: CorePlaylistDetail? = null,
    public val artist: ArtistDetail? = null,
    public val artistAppearances: List<ArtistAppearance> = emptyList(),
    public val subjectArtistCredits: List<ArtistCredit> = emptyList(),
)

@Composable
@OptIn(ExperimentalLayoutApi::class)
public fun CoreProductDetailScreen(
    state: CoreProductDetailUiState,
    contentPadding: PaddingValues = PaddingValues(0.dp),
    onPlayTrack: (String) -> Unit = {},
    onPlayPlaylistEntry: (String) -> Unit = {},
    onRemoveOrRestore: (String) -> Unit = {},
    onLike: (String) -> Unit = {},
    /** Opens an exact-target playlist chooser; the caller owns the local mutation. */
    onAddToPlaylist: (String) -> Unit = {},
    onPlayNext: (String) -> Unit = {},
    onAddToQueue: (String) -> Unit = {},
    manualPlaylists: List<ManualPlaylistUi> = emptyList(),
    manualPlaylistActions: ManualPlaylistActions = ManualPlaylistActions(),
    onDownload: (String) -> Unit = {},
    onRepairAccess: () -> Unit = {},
    onOpenReview: () -> Unit = {},
    onOpenDetail: (DetailTarget) -> Unit = {},
) {
    var addTrackToPlaylistId by remember { mutableStateOf<String?>(null) }
    val playlistText = ManualPlaylistText(
        create = stringResource(R.string.playlist_create),
        rename = stringResource(R.string.playlist_rename),
        delete = stringResource(R.string.playlist_delete),
        cancel = stringResource(R.string.action_cancel),
        save = stringResource(R.string.action_save),
        add = stringResource(R.string.playlist_add_selected),
        remove = stringResource(R.string.action_remove),
        moveUp = stringResource(R.string.playlist_move_up),
        moveDown = stringResource(R.string.playlist_move_down),
        name = stringResource(R.string.playlist_name),
        description = stringResource(R.string.playlist_description),
        selectPlaylist = stringResource(R.string.playlist_select_target),
        confirmDelete = stringResource(R.string.playlist_confirm_delete),
        empty = stringResource(R.string.playlist_empty),
        play = stringResource(R.string.action_play),
    )
    LazyColumn(
        modifier = Modifier.fillMaxWidth(),
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = contentPadding.calculateTopPadding() + 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = contentPadding.calculateBottomPadding() + 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when {
            state.loading -> item {
                AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.detail_loading))
            }
            state.error || (
                state.target != null && state.track == null && state.release == null &&
                    state.playlist == null && state.artist == null
                ) -> item {
                AutPlayStateSurface(AutPlayStateKind.Error, stringResource(R.string.detail_unavailable))
            }
            state.track != null -> {
                val detail = state.track
                item {
                    DetailHeading(
                        detail.title ?: stringResource(R.string.player_nothing_playing),
                        detail.artistName,
                    )
                }
                detail.albumName?.let { album -> item { DetailValue(stringResource(R.string.detail_album), album) } }
                detail.durationMs?.let { duration -> item { DetailValue(stringResource(R.string.detail_duration), formatDuration(duration)) } }
                artistCreditItems(state.subjectArtistCredits, onOpenDetail)
                item { DetailValue(stringResource(R.string.detail_availability), availabilityLabel(detail.availability)) }
                item {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (CoreTrackDetailCapability.PLAY in detail.capabilities) {
                            Button(
                                onClick = { onPlayTrack(detail.localUserTrackRefId) },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) { Text(stringResource(R.string.action_play)) }
                        }
                        if (CoreTrackDetailCapability.LIKE in detail.capabilities) {
                            OutlinedButton(
                                onClick = { onLike(detail.localUserTrackRefId) },
                                enabled = detail.preference.preference != "LIKED",
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) {
                                Text(
                                    stringResource(
                                        if (detail.preference.preference == "LIKED") R.string.action_liked else R.string.action_like,
                                    ),
                                )
                            }
                        }
                        OutlinedButton(
                            onClick = {
                                onAddToPlaylist(detail.localUserTrackRefId)
                                addTrackToPlaylistId = detail.localUserTrackRefId
                            },
                            enabled = manualPlaylists.isNotEmpty(),
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) { Text(stringResource(R.string.playlist_add_selected)) }
                        OutlinedButton(
                            onClick = { onPlayNext(detail.localUserTrackRefId) },
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) { Text(stringResource(R.string.queue_play_next)) }
                        OutlinedButton(
                            onClick = { onAddToQueue(detail.localUserTrackRefId) },
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) { Text(stringResource(R.string.queue_add_to_end)) }
                    }
                }
                if (CoreTrackDetailCapability.DOWNLOAD in detail.capabilities) {
                    item {
                        OutlinedButton(
                            onClick = { onDownload(detail.localUserTrackRefId) },
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) { Text(stringResource(R.string.action_download)) }
                    }
                }
                if (CoreTrackDetailCapability.REAUTHORIZE_LIBRARY_ROOT in detail.capabilities) {
                    item {
                        OutlinedButton(
                            onClick = onRepairAccess,
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) { Text(stringResource(R.string.action_choose_folder_again)) }
                    }
                }
                if (
                    CoreTrackDetailCapability.REMOVE_FROM_LIBRARY in detail.capabilities ||
                    CoreTrackDetailCapability.RESTORE_TO_LIBRARY in detail.capabilities
                ) {
                    item {
                        OutlinedButton(
                            onClick = { onRemoveOrRestore(detail.localUserTrackRefId) },
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) {
                            Text(
                                stringResource(
                                    if (CoreTrackDetailCapability.RESTORE_TO_LIBRARY in detail.capabilities) {
                                        R.string.action_restore
                                    } else {
                                        R.string.action_remove
                                    },
                                ),
                            )
                        }
                    }
                }
                if (CoreTrackDetailCapability.OPEN_IDENTITY_REVIEW in detail.capabilities) {
                    item {
                        OutlinedButton(onClick = onOpenReview, modifier = Modifier.heightIn(min = 48.dp)) {
                            Text(stringResource(R.string.nav_import_review))
                        }
                    }
                }
                item {
                    Text(
                        stringResource(R.string.detail_technical_title),
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.semantics { heading() },
                    )
                }
                item {
                    DetailValue(
                        stringResource(R.string.detail_resolution_status),
                        detail.technicalDetails.resolutionStatus.replace('_', ' ').lowercase()
                            .replaceFirstChar(Char::uppercase),
                    )
                }
                detail.technicalDetails.resolutionConfidence?.let { confidence ->
                    item {
                        DetailValue(
                            stringResource(R.string.detail_resolution_confidence),
                            NumberFormat.getPercentInstance().format(confidence.coerceIn(0.0, 1.0)),
                        )
                    }
                }
                detail.technicalDetails.recordingKind?.let { kind ->
                    item { DetailValue(stringResource(R.string.detail_recording_kind), kind.replace('_', ' ')) }
                }
                detail.technicalDetails.versionText?.let { version ->
                    item { DetailValue(stringResource(R.string.detail_recording_version), version) }
                }
            }
            state.release != null -> {
                val detail = state.release
                item { DetailHeading(detail.title, detail.artistName) }
                detail.releaseDateText?.let { date -> item { DetailValue(stringResource(R.string.detail_release_date), date) } }
                artistCreditItems(state.subjectArtistCredits, onOpenDetail)
                items(detail.tracks, key = { it.localReleaseTrackId }) { track ->
                    AutPlayCard(
                        onClick = track.localUserTrackRefId?.let { trackRefId -> { onPlayTrack(trackRefId) } },
                    ) {
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text(track.numberText ?: track.sequenceNo.toString())
                            Column {
                                Text(track.title, style = MaterialTheme.typography.titleMedium)
                                Text(track.artistName, color = AutPlayTokens.colors.mutedText)
                            }
                        }
                    }
                }
            }
            state.artist != null -> {
                val detail = state.artist
                item { DetailHeading(detail.summary.name, detail.summary.disambiguation) }
                detail.summary.artistType?.let { value ->
                    item { DetailValue(stringResource(R.string.detail_artist_type), humanize(value)) }
                }
                detail.summary.countryCode?.let { value ->
                    item { DetailValue(stringResource(R.string.detail_artist_country), value) }
                }
                detail.summary.identityStatus?.let { value ->
                    item { DetailValue(stringResource(R.string.detail_artist_identity_status), humanize(value)) }
                }
                if (detail.credits.isNotEmpty()) {
                    item {
                        DetailSectionHeading(stringResource(R.string.detail_artist_credits))
                    }
                    items(detail.credits, key = { "artist-credit:${it.id.value}" }) { credit ->
                        ArtistCreditCard(credit, onOpenDetail)
                    }
                }
                if (state.artistAppearances.isNotEmpty()) {
                    item {
                        DetailSectionHeading(stringResource(R.string.detail_artist_appearances))
                    }
                    items(
                        state.artistAppearances,
                        key = { "artist-appearance:${it.subjectType}:${it.subjectId.value}" },
                    ) { appearance ->
                        ArtistAppearanceCard(appearance, onOpenDetail)
                    }
                }
            }
            state.playlist != null -> {
                val detail = state.playlist
                item { DetailHeading(detail.name, detail.description) }
                item(key = "playlist-editor:${detail.localPlaylistId}") {
                    ManualPlaylistEditor(
                        playlist = ManualPlaylistUi(
                            playlistId = detail.localPlaylistId,
                            name = detail.name,
                            description = detail.description,
                            entries = detail.entries.map { entry ->
                                ManualPlaylistEntryUi(
                                    entryId = entry.localPlaylistEntryId,
                                    trackRefId = entry.localUserTrackRefId,
                                    title = entry.title ?: stringResource(R.string.player_nothing_playing),
                                    subtitle = entry.artistName ?: stringResource(R.string.library_unknown_artist),
                                    playable = !entry.unavailable,
                                )
                            },
                        ),
                        actions = manualPlaylistActions.copy(playEntry = onPlayPlaylistEntry),
                        text = playlistText,
                    )
                }
            }
        }
    }
    addTrackToPlaylistId?.let { trackRefId ->
        AddTrackToPlaylistDialog(
            trackRefId = trackRefId,
            playlists = manualPlaylists,
            actions = manualPlaylistActions,
            onDismiss = { addTrackToPlaylistId = null },
            text = playlistText,
        )
    }
}

private fun androidx.compose.foundation.lazy.LazyListScope.artistCreditItems(
    credits: List<ArtistCredit>,
    onOpenDetail: (DetailTarget) -> Unit,
) {
    if (credits.isEmpty()) return
    item { DetailSectionHeading(stringResource(R.string.detail_artist_credits)) }
    items(credits, key = { "subject-credit:${it.id.value}" }) { credit ->
        ArtistCreditCard(credit, onOpenDetail)
    }
}

@Composable
private fun DetailSectionHeading(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.semantics { heading() },
    )
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
private fun ArtistCreditCard(
    credit: ArtistCredit,
    onOpenDetail: (DetailTarget) -> Unit,
) {
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            credit.displayName?.takeIf(String::isNotBlank)?.let {
                Text(it, style = MaterialTheme.typography.titleMedium)
            }
            if (credit.members.isEmpty()) {
                Text(
                    stringResource(R.string.detail_artist_credit_unresolved),
                    color = AutPlayTokens.colors.mutedText,
                )
            } else {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    credit.members.sortedBy { it.position }.forEach { member ->
                        OutlinedButton(
                            onClick = {
                                onOpenDetail(DetailTarget(DetailKind.Artist, member.artistId.value))
                            },
                            modifier = Modifier.heightIn(min = 48.dp),
                        ) {
                            Text(member.creditedName)
                        }
                        member.joinPhrase.takeIf(String::isNotBlank)?.let { Text(it) }
                    }
                }
            }
        }
    }
}

@Composable
private fun ArtistAppearanceCard(
    appearance: ArtistAppearance,
    onOpenDetail: (DetailTarget) -> Unit,
) {
    val target = appearance.localTarget?.toDetailTarget()
    val typeLabel = when (appearance.subjectType) {
        "RECORDING" -> stringResource(R.string.detail_artist_recording)
        "RELEASE" -> stringResource(R.string.detail_artist_release)
        else -> stringResource(R.string.detail_artist_related_item)
    }
    AutPlayCard(onClick = target?.let { { onOpenDetail(it) } }) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(appearance.title ?: typeLabel, style = MaterialTheme.typography.titleMedium)
            Text(typeLabel, color = AutPlayTokens.colors.mutedText)
        }
    }
}

internal fun ArtistLocalTarget.toDetailTarget(): DetailTarget = when (this) {
    is ArtistLocalTarget.Track -> DetailTarget(DetailKind.Track, localUserTrackRefId)
    is ArtistLocalTarget.Release -> DetailTarget(DetailKind.Release, localReleaseId)
}

@Composable
private fun DetailHeading(title: String, subtitle: String?) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.semantics { heading() })
        subtitle?.let { Text(it, color = AutPlayTokens.colors.mutedText) }
    }
}

@Composable
private fun DetailValue(label: String, value: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.labelLarge)
        Text(value, color = AutPlayTokens.colors.mutedText)
    }
}

private fun formatDuration(durationMs: Long): String {
    val totalSeconds = durationMs.coerceAtLeast(0) / 1_000
    return "%d:%02d".format(totalSeconds / 60, totalSeconds % 60)
}

private fun humanize(value: String): String = value.replace('_', ' ').lowercase()
    .replaceFirstChar(Char::uppercase)

@Composable
private fun availabilityLabel(availability: CoreTrackAvailability): String = stringResource(
    when (availability) {
        CoreTrackAvailability.PLAYABLE_LOCAL -> R.string.detail_availability_local
        CoreTrackAvailability.PERMISSION_REVOKED -> R.string.detail_availability_permission
        CoreTrackAvailability.UNAVAILABLE -> R.string.detail_availability_unavailable
        CoreTrackAvailability.NO_LOCAL_SOURCE -> R.string.detail_availability_metadata
    },
)
