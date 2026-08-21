package app.autplay

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.media3.common.util.UnstableApi
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.artist.ArtistCatalogPort
import app.autplay.application.artist.ArtistSummary
import app.autplay.application.importing.ImportReviewItem
import app.autplay.application.importing.LEGACY_PROFILE_ID
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.library.CoreHomePlaylistSummary
import app.autplay.application.library.CoreHomeTrackSummary
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.library.CoreLocalAudioSummary
import app.autplay.application.library.CoreProductRepository
import app.autplay.application.library.CoreReleaseSummary
import app.autplay.application.library.CoreResumeQueueSummary
import app.autplay.application.library.CoreTrackPreferenceSummary
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncStatus
import app.autplay.application.sync.SyncStatusRepository
import app.autplay.data.local.entity.LocalImportJobEntity
import app.autplay.data.local.entity.LocalMatchCandidateEntity
import kotlinx.coroutines.flow.flowOf

internal data class OfflineRepositorySnapshot(
    val artistBrowseStatus: OfflineArtistBrowseStatus,
    val artists: List<ArtistSummary>,
    val playlists: List<CoreHomePlaylistSummary>,
    val releases: List<CoreReleaseSummary>,
    val preferences: List<CoreTrackPreferenceSummary>,
    val historyCount: Int,
    val downloads: List<DownloadIntentPresentation>,
    val localAudio: List<CoreLocalAudioSummary>,
    val downloadedTrackIds: Set<String>,
    val recentlyAdded: List<CoreHomeTrackSummary>,
    val recentlyPlayed: List<CoreHomeTrackSummary>,
    val homePlaylists: List<CoreHomePlaylistSummary>,
    val activeQueue: CoreResumeQueueSummary?,
    val syncStatus: SyncStatus,
    val importProfileId: String,
    val importJob: LocalImportJobEntity?,
    val importItems: List<ImportReviewItem>,
    val selectedImportItem: ImportReviewItem?,
    val importCandidates: List<LocalMatchCandidateEntity>,
)

/** Observes bounded repository projections outside the root screen declaration. */
@Composable
@UnstableApi
internal fun rememberOfflineRepositorySnapshot(
    coreRepository: CoreProductRepository,
    artistCatalogPort: ArtistCatalogPort,
    downloadRepository: DownloadIntentRepository,
    syncStatusRepository: SyncStatusRepository,
    importRepository: LocalImportReviewRepository,
    binding: ClientEventBinding?,
    selectedImportEntryId: String?,
): OfflineRepositorySnapshot {
    val profileId = binding?.serverProfileId?.value
    val artistBrowse = rememberOfflineArtistBrowseSnapshot(artistCatalogPort, profileId)
    val playlists by remember(coreRepository, profileId) { coreRepository.playlists(profileId) }
        .collectAsState(initial = emptyList())
    val releases by remember(coreRepository, profileId) { coreRepository.releases(profileId) }
        .collectAsState(initial = emptyList())
    val preferences by remember(coreRepository, profileId) { coreRepository.preferences(profileId) }
        .collectAsState(initial = emptyList())
    val historyCount by remember(coreRepository, profileId) { coreRepository.historyCount(profileId) }
        .collectAsState(initial = 0)
    val downloads by downloadRepository.observePresentation(profileId).collectAsState(initial = emptyList())
    val localAudio by remember(coreRepository, profileId) { coreRepository.localAudio(profileId) }
        .collectAsState(initial = emptyList())
    val downloadedTrackIds by remember(coreRepository, profileId) {
        coreRepository.downloadedTrackIds(profileId)
    }.collectAsState(initial = emptySet())
    val recentlyAdded by remember(coreRepository, profileId) { coreRepository.recentlyAdded(profileId) }
        .collectAsState(initial = emptyList())
    val recentlyPlayed by remember(coreRepository, profileId) { coreRepository.recentlyPlayed(profileId) }
        .collectAsState(initial = emptyList())
    val homePlaylists by remember(coreRepository, profileId) { coreRepository.homePlaylists(profileId) }
        .collectAsState(initial = emptyList())
    val activeQueue by remember(coreRepository, profileId) { coreRepository.activeQueue(profileId) }
        .collectAsState(initial = null)
    val syncStatus by (binding?.let(syncStatusRepository::observe) ?: flowOf(
        SyncStatus(0, 0, 0, null, "STANDALONE"),
    )).collectAsState(initial = SyncStatus(0, 0, 0, null, "LOADING"))
    val importProfileId = profileId ?: LEGACY_PROFILE_ID
    val importJob by importRepository.observeLatestJob(importProfileId).collectAsState(initial = null)
    val importItems by (importJob?.let { importRepository.observeReviewItems(it.importJobId) }
        ?: flowOf(emptyList())).collectAsState(initial = emptyList())
    val selectedImportItem = importItems.firstOrNull { it.entry.importEntryId == selectedImportEntryId }
    val importCandidates by (selectedImportItem?.latestEvaluation?.decisionId
        ?.let(importRepository::observeCandidates) ?: flowOf(emptyList()))
        .collectAsState(initial = emptyList())
    return OfflineRepositorySnapshot(
        artistBrowse.status,
        artistBrowse.artists,
        playlists,
        releases,
        preferences,
        historyCount,
        downloads,
        localAudio,
        downloadedTrackIds,
        recentlyAdded,
        recentlyPlayed,
        homePlaylists,
        activeQueue,
        syncStatus,
        importProfileId,
        importJob,
        importItems,
        selectedImportItem,
        importCandidates,
    )
}
