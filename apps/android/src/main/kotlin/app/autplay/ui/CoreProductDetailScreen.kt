package app.autplay.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.testTag
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
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.server.InternetMetadataBrowseTarget
import app.autplay.application.server.InternetMetadataDiscoveryEntity
import app.autplay.application.server.InternetMetadataDiscoveryCard
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
import app.autplay.ui.playlist.localizedPlaylistText
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
    public val artistMbid: String? = null,
    public val publicEditions: List<InternetMetadataDiscoveryCard> = emptyList(),
    public val publicRecordings: List<InternetMetadataDiscoveryCard> = emptyList(),
    public val publicCounts: Map<String, Long> = emptyMap(),
    public val libraryRecordings: Map<String, String> = emptyMap(),
    public val recordingsIncomplete: Boolean = false,
    public val recordingsLoading: Boolean = false,
    public val retryRecordings: () -> Unit = {},
    public val ranking: Map<String, Long> = emptyMap(),
    public val artistAppearances: List<ArtistAppearance> = emptyList(),
    public val subjectArtistCredits: List<ArtistCredit> = emptyList(),
    public val albumArtwork: Map<String, String?> = emptyMap(),
)

@Composable
@OptIn(ExperimentalLayoutApi::class)
public fun CoreProductDetailScreen(
    state: CoreProductDetailUiState,
    contentPadding: PaddingValues = PaddingValues(0.dp),
    onPlayTrack: (String) -> Unit = {},
    onPlayPlaylistEntry: (String) -> Unit = {},
    onPlayReleaseEntry: ((String) -> Unit)? = null,
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
    var trackLimit by rememberSaveable(state.target?.stableId) { androidx.compose.runtime.mutableIntStateOf(25) }
    var selectedCard by remember(state.target) { mutableStateOf<InternetMetadataDiscoveryCard?>(null) }
    selectedCard?.let { card ->
        AutPlayDialog(onDismissRequest = { selectedCard = null }, title = { Text(card.title) }, text = {
            androidx.compose.foundation.lazy.LazyColumn {
                item { InternetMetadataDiscoverySection(card.title, LibrarySearchKind.Artist, 1,
                    onPlaylistTrack = { selectedCard = null; addTrackToPlaylistId = it }, initialCard = card) }
            }
        }, confirmButton = {
            androidx.compose.material3.TextButton(onClick = { selectedCard = null }) { Text(stringResource(android.R.string.cancel)) }
        })
    }
    var showArtistInfo by rememberSaveable(state.target?.stableId) { mutableStateOf(false) }
    var editing by rememberSaveable(state.target?.stableId) { mutableStateOf(false) }
    androidx.activity.compose.BackHandler(enabled = editing) { editing = false }
    val playlistText = localizedPlaylistText()
    val viewStates = androidx.compose.runtime.saveable.rememberSaveableStateHolder()
    viewStates.SaveableStateProvider("${state.target?.kind}:${state.target?.stableId}") {
    LazyColumn(
        modifier = Modifier.padding(
            top = contentPadding.calculateTopPadding(),
            bottom = contentPadding.calculateBottomPadding(),
        ).fillMaxWidth().testTag("collection-details"),
        contentPadding = PaddingValues(
            start = AutPlayTokens.dimensions.screenPadding,
            top = 12.dp,
            end = AutPlayTokens.dimensions.screenPadding,
            bottom = 28.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when {
            state.target?.kind == DetailKind.CatalogRelease || state.target?.kind == DetailKind.CatalogArtistTracks -> item {
                val target = requireNotNull(state.target)
                val release = target.kind == DetailKind.CatalogRelease
                InternetMetadataDiscoverySection(target.stableId,
                    if (release) LibrarySearchKind.Album else LibrarySearchKind.Artist, 1,
                    onPlaylistTrack = { addTrackToPlaylistId = it },
                    initialTarget = InternetMetadataBrowseTarget(
                        if (release) InternetMetadataDiscoveryEntity.RELEASE else InternetMetadataDiscoveryEntity.ARTIST, target.stableId))
            }
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
                item { AutPlayArtwork(detail.title.orEmpty(), size = 220.dp, trackId = detail.localUserTrackRefId) }
                item {
                    DetailHeading(
                        detail.title ?: stringResource(R.string.player_nothing_playing),
                        detail.artistName,
                    )
                }
                detail.albumName?.let { album -> item { DetailValue(stringResource(R.string.detail_album), album) } }
                detail.metadata?.text("mb_recording_id")?.let { id ->
                    if (runCatching { java.util.UUID.fromString(id).toString() }.getOrNull() == id)
                        item { CatalogDownloadAction("recording", id) }
                }
                item(key = "track-metadata") { TrackMetadataPanel(detail) }
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
                val first = detail.tracks.firstOrNull { it.localUserTrackRefId != null }
                fun play(row: app.autplay.application.library.CoreReleaseDetailTrack) {
                    if (onPlayReleaseEntry != null) onPlayReleaseEntry(row.localReleaseTrackId)
                    else row.localUserTrackRefId?.let(onPlayTrack)
                }
                val artist = state.subjectArtistCredits.singleOrNull()?.members?.singleOrNull()
                item(key = "release-header") {
                    CollectionDetailHeader(detail.title, detail.artistName, detail.releaseDateText, detail.tracks.size,
                        detail.artworkTrackId, onListen = first?.let { { play(it) } }, catalogCoverId = detail.releaseMbid,
                        onOpenArtist = artist?.let { { onOpenDetail(DetailTarget(DetailKind.Artist, it.artistId.value)) } })
                }
                detail.releaseMbid?.let { mbid ->
                    item {
                        OutlinedButton(onClick = { onOpenDetail(DetailTarget(DetailKind.CatalogRelease, mbid)) }) {
                            Text(stringResource(R.string.catalog_edition_tracks))
                        }
                    }
                    item { CatalogDownloadAction("release", mbid) }
                }
                itemsIndexed(detail.tracks, key = { _, track -> track.localReleaseTrackId }) { index, track ->
                    CollectionTrackRow(track.numberText ?: (track.sequenceNo ?: index + 1).toString(), track.title,
                        track.artistName, track.durationMs, track.localUserTrackRefId?.let { { play(track) } },
                        "album-track-${track.localReleaseTrackId}",
                        onAdd = track.localUserTrackRefId?.let { { addTrackToPlaylistId = it } })
                }
                if (artist == null || detail.artistName.isNullOrBlank()) artistCreditItems(state.subjectArtistCredits, onOpenDetail)
            }
            state.artist != null -> {
                val detail = state.artist
                val appearances = state.artistAppearances.distinctBy { it.subjectType to it.displayKey }
                val tracks = artistRecordings(state)
                val albums = artistAlbums(appearances, state.publicEditions)
                val kind = state.target?.kind
                val allTracks = kind == DetailKind.ArtistTracks
                val allAlbums = kind == DetailKind.ArtistAlbums
                fun openList(list: DetailKind) = onOpenDetail(DetailTarget(list, detail.summary.key.artistId.value))
                @Composable fun trackRow(row: ArtistRecording, modifier: Modifier = Modifier, section: String = "artist") {
                    val track = row.local?.localTarget as? ArtistLocalTarget.Track
                    AutPlayTrackRow(row.title ?: stringResource(R.string.detail_artist_recording), row.card?.artist ?: detail.summary.name,
                        track?.localUserTrackRefId, track?.let { { onPlayTrack(it.localUserTrackRefId) } },
                        modifier = modifier.testTag("$section-track-${row.displayKey}"),
                        actions = row.card?.takeIf { track == null }?.let { card -> {
                            androidx.compose.material3.TextButton(onClick = { selectedCard = card }) {
                                Text(stringResource(R.string.ui_library_refresh_track_actions))
                            }
                        } })
                }
                @Composable fun albumTile(album: ArtistAlbumTile, modifier: Modifier, section: String = "artist") {
                    CollectionCoverTile(album.title ?: stringResource(R.string.detail_artist_release),
                        state.albumArtwork[album.localId], album.date, modifier.testTag("$section-album-${album.key}"),
                        enabled = album.target != null, catalogCoverId = album.mbid) {
                        album.target?.let(onOpenDetail)
                    }
                }
                if (allTracks || allAlbums) {
                    item { DetailHeading(stringResource(if (allTracks) R.string.library_section_tracks else R.string.library_section_albums), detail.summary.name) }
                    if (allTracks && state.recordingsLoading) {
                        item { AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.detail_loading)) }
                        return@LazyColumn
                    }
                    if (allTracks) items(tracks.take(trackLimit), key = { it.displayKey }) { trackRow(it) }
                    else items(albums, key = { it.key }) { albumTile(it, Modifier.width(180.dp)) }
                    if (allTracks && tracks.size > trackLimit) item {
                        OutlinedButton(onClick = { trackLimit += 25 }, modifier = Modifier.testTag("artist-tracks-more")) {
                            Text(stringResource(R.string.music_catalogue_more))
                        }
                    }
                    if (allTracks && state.recordingsIncomplete) item {
                        CatalogueErrorText("music_catalogue_unavailable")
                        OutlinedButton(onClick = state.retryRecordings) { Text(stringResource(R.string.music_retry)) }
                    }
                    return@LazyColumn
                }
                val first = tracks.firstNotNullOfOrNull { (it.local?.localTarget as? ArtistLocalTarget.Track)?.localUserTrackRefId }
                item {
                    CollectionDetailHeader(detail.summary.name, detail.summary.disambiguation, null, tracks.size, null,
                        onListen = first?.let { { onPlayTrack(it) } })
                }
                state.artistMbid?.let { id -> item { CatalogDownloadAction("artist", id) } }
                val today = java.time.LocalDate.now().toString()
                albums.firstOrNull { it.official && it.date != null && it.date <= today }?.let { latest ->
                    item { DetailSectionHeading(stringResource(R.string.catalog_latest_release)) }
                    item { albumTile(latest, Modifier.width(180.dp), "latest") }
                }
                val personal = tracks.filter { (it.local?.listenCount ?: 0) > 0 }.sortedByDescending { it.local?.listenCount }.take(5)
                if (personal.isNotEmpty()) {
                    item { DetailSectionHeading(stringResource(R.string.detail_artist_popular)) }
                    items(personal, key = { "personal:${it.displayKey}" }) { trackRow(it, section = "personal") }
                }
                if (state.recordingsLoading) {
                    item { AutPlayStateSurface(AutPlayStateKind.Loading, stringResource(R.string.detail_loading)) }
                } else if (tracks.isNotEmpty() || state.recordingsIncomplete) {
                    item { CollectionListHeading(stringResource(if (tracks.none { it.count != null }) R.string.library_section_tracks else R.string.catalog_popular_tracks)) { openList(DetailKind.ArtistTracks) } }
                    item {
                        LazyRow(modifier = Modifier.testTag("artist-track-preview"), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            items(tracks.take(12).chunked(3), key = { it.first().displayKey }) { column ->
                                Column(Modifier.width(248.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                    column.forEach { trackRow(it) }
                                }
                            }
                        }
                    }
                }
                val guests = tracks.filter { it.guest }
                if (guests.isNotEmpty()) {
                    item { CollectionListHeading(stringResource(R.string.catalog_guest_tracks)) { openList(DetailKind.ArtistTracks) } }
                    items(guests.take(3), key = { "guest:${it.displayKey}" }) { trackRow(it, section = "guest") }
                }
                if (albums.isNotEmpty()) {
                    item { CollectionListHeading(stringResource(R.string.library_section_albums)) { openList(DetailKind.ArtistAlbums) } }
                    item {
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            items(albums.take(4), key = { it.key }) { albumTile(it, Modifier.width(148.dp)) }
                        }
                    }
                }
                val hasArtistInfo = detail.credits.isNotEmpty() || listOf(detail.summary.artistType,
                    detail.summary.countryCode, detail.summary.identityStatus).any { it != null }
                if (hasArtistInfo && appearances.isNotEmpty()) item {
                    androidx.compose.material3.TextButton(onClick = { showArtistInfo = !showArtistInfo }) {
                        Text(stringResource(if (showArtistInfo) R.string.detail_hide_artist_info else R.string.detail_artist_info))
                    }
                }
                if (showArtistInfo || appearances.isEmpty()) {
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
                }
                val other = appearances.filter { it.subjectType != "RECORDING" && it.subjectType != "RELEASE" }
                if (other.isNotEmpty()) {
                    item {
                        DetailSectionHeading(stringResource(R.string.detail_artist_appearances))
                    }
                    items(
                        other,
                        key = { "artist-appearance:${it.subjectType}:${it.displayKey}" },
                    ) { appearance ->
                        ArtistAppearanceCard(appearance, onOpenDetail)
                    }
                }
            }
            state.playlist != null -> {
                val detail = state.playlist
                val first = detail.entries.firstOrNull { !it.unavailable }
                item(key = "playlist-header") {
                    CollectionDetailHeader(detail.name, detail.description, null, detail.entries.size, detail.artworkTrackId,
                        onListen = first?.let { { onPlayPlaylistEntry(it.localPlaylistEntryId) } }) {
                        if (detail.playlistType == "MANUAL") {
                            OutlinedButton(onClick = { editing = !editing }, modifier = Modifier.testTag("playlist-toggle-edit")) {
                                Text(stringResource(if (editing) R.string.detail_finish_edit else R.string.action_edit))
                            }
                        }
                    }
                }
                if (detail.playlistType == "MANUAL") item(key = "playlist-share") { SharePlaylistAction(detail.localPlaylistId) }
                if (!editing) {
                    if (detail.entries.isEmpty()) item { Text(playlistText.empty, color = AutPlayTokens.colors.mutedText) }
                    itemsIndexed(detail.entries, key = { _, entry -> entry.localPlaylistEntryId }) { index, entry ->
                        CollectionTrackRow((index + 1).toString(), entry.title ?: stringResource(R.string.player_nothing_playing),
                            if (entry.unavailable) listOfNotNull(entry.artistName, stringResource(R.string.playlist_copy_no_audio)).joinToString(" · ")
                            else entry.artistName, entry.durationMs, if (entry.unavailable) null else ({ onPlayPlaylistEntry(entry.localPlaylistEntryId) }),
                            "playlist-entry-${entry.localPlaylistEntryId}")
                    }
                } else item(key = "playlist-editor:${detail.localPlaylistId}") {
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
private fun CollectionListHeading(title: String, open: () -> Unit) {
    Text(title, style = MaterialTheme.typography.titleMedium,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(onClick = open).semantics { heading() })
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

internal fun formatDuration(durationMs: Long): String {
    val totalSeconds = durationMs.coerceAtLeast(0) / 1_000
    return "%d:%02d".format(totalSeconds / 60, totalSeconds % 60)
}

private fun humanize(value: String): String = value.replace('_', ' ').lowercase()
    .replaceFirstChar(Char::uppercase)

@Composable
private fun availabilityLabel(availability: CoreTrackAvailability): String = stringResource(
    when (availability) {
        CoreTrackAvailability.PLAYABLE_LOCAL -> R.string.detail_availability_local
        CoreTrackAvailability.SERVER_CANDIDATE -> R.string.detail_availability_server
        CoreTrackAvailability.PERMISSION_REVOKED -> R.string.detail_availability_permission
        CoreTrackAvailability.UNAVAILABLE -> R.string.detail_availability_unavailable
        CoreTrackAvailability.NO_LOCAL_SOURCE -> R.string.detail_availability_metadata
    },
)
