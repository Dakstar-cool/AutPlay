package app.autplay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onVisibilityChanged
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import androidx.media3.common.util.UnstableApi
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.work.WorkManager
import app.autplay.application.library.AddLocalTrackCommand
import app.autplay.application.library.LocalLibraryCommandRepository
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.importing.ImportResolverState
import app.autplay.application.importing.ImportReviewAction
import app.autplay.application.importing.LEGACY_PROFILE_ID
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.importing.RecordImportReviewCommand
import app.autplay.application.importing.RecordShadowEvaluationCommand
import app.autplay.application.importing.singleUriImportCommand
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncStatusRepository
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.recommendation.HomeFeed
import app.autplay.application.recommendation.HomeRecommendationItem
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.RecommendationPresentationResult
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.playback.PlaybackRuntimeState
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject
import app.autplay.work.SyncWorker
import app.autplay.work.WorkManagerDeferredWorkScheduler
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.flowOf

internal const val BOOTSTRAP_LABEL = "AutPlay"

@UnstableApi
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val database = AutPlayRuntime.database(applicationContext)
        val repository = LocalLibraryCommandRepository(database)
        val settingsStore = applicationNonSecretSettingsStore(applicationContext)
        val playbackRepository = PlaybackPersistenceRepository(database)
        val playbackOwner = ServicePlaybackSessionOwner(applicationContext)
        val downloadRepository = DownloadIntentRepository(applicationContext, database)
        val syncStatusRepository = SyncStatusRepository(database)
        val syncScheduler = WorkManagerDeferredWorkScheduler(WorkManager.getInstance(applicationContext), SyncWorker::class.java)
        val importRepository = LocalImportReviewRepository(database)
        val recommendationRepository = OfflineRecommendationRepository(database, syncScheduler = syncScheduler)
        setContent {
            AutPlayBootstrap(
                repository,
                settingsStore,
                LocalTrackSearchRepository(database),
                LibraryVerticalSliceRepository(database, syncScheduler = syncScheduler),
                playbackRepository,
                playbackOwner,
                downloadRepository,
                syncStatusRepository,
                syncScheduler,
                importRepository,
                recommendationRepository,
            )
        }
    }

}

@Composable
@UnstableApi
internal fun AutPlayBootstrap(
    repository: LocalLibraryCommandRepository? = null,
    settingsStore: NonSecretSettingsStore? = null,
    searchRepository: LocalTrackSearchRepository? = null,
    sliceRepository: LibraryVerticalSliceRepository? = null,
    playbackRepository: PlaybackPersistenceRepository? = null,
    playbackOwner: PlaybackSessionOwner? = null,
    downloadRepository: DownloadIntentRepository? = null,
    syncStatusRepository: SyncStatusRepository? = null,
    syncScheduler: DeferredWorkScheduler? = null,
    importRepository: LocalImportReviewRepository? = null,
    recommendationRepository: OfflineRecommendationRepository? = null,
) {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            if (repository == null || settingsStore == null || searchRepository == null || sliceRepository == null ||
                playbackRepository == null || playbackOwner == null || downloadRepository == null || syncStatusRepository == null || syncScheduler == null || importRepository == null || recommendationRepository == null
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text(text = BOOTSTRAP_LABEL)
                }
            } else {
                val settings by settingsStore.settings.collectAsState(initial = NonSecretSettings())
                val binding = settings.activeUserId?.let { userId ->
                    val deviceId = settings.deviceId ?: return@let null
                    val profileId = settings.activeServerProfileId ?: return@let null
                    ClientEventBinding(userId, deviceId, profileId)
                }
                OfflineLibraryScreen(
                    repository,
                    binding,
                    searchRepository,
                    sliceRepository,
                    playbackRepository,
                    playbackOwner,
                    downloadRepository,
                    syncStatusRepository,
                    syncScheduler,
                    importRepository,
                    recommendationRepository,
                )
            }
        }
    }
}

@Composable
@UnstableApi
private fun OfflineLibraryScreen(
    repository: LocalLibraryCommandRepository,
    binding: ClientEventBinding?,
    searchRepository: LocalTrackSearchRepository,
    sliceRepository: LibraryVerticalSliceRepository,
    playbackRepository: PlaybackPersistenceRepository,
    playbackOwner: PlaybackSessionOwner,
    downloadRepository: DownloadIntentRepository,
    syncStatusRepository: SyncStatusRepository,
    syncScheduler: DeferredWorkScheduler,
    importRepository: LocalImportReviewRepository,
    recommendationRepository: OfflineRecommendationRepository,
) {
    val entryCount by repository.activeEntryCount(binding?.serverProfileId?.value).collectAsState(initial = 0)
    val libraryEntries by repository.entries(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var view by remember { mutableStateOf("Library") }
    var searchText by remember { mutableStateOf("") }
    var searchResultCount by remember { mutableStateOf<Int?>(null) }
    var stableError by remember { mutableStateOf<String?>(null) }
    val playlists by sliceRepository.playlists(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val history by sliceRepository.history(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val downloads by downloadRepository.observeIntents().collectAsState(initial = emptyList())
    val playbackState by PlaybackRuntimeState.state.collectAsState()
    val importProfileId = binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID
    val importJob by importRepository.observeLatestJob(importProfileId).collectAsState(initial = null)
    val importItems by (importJob?.let { importRepository.observeReviewItems(it.importJobId) } ?: flowOf(emptyList()))
        .collectAsState(initial = emptyList())
    var selectedImportEntryId by remember { mutableStateOf<String?>(null) }
    var homeFeed by remember(
        binding?.serverProfileId?.value,
        binding?.userId?.value,
        binding?.deviceId?.value,
    ) { mutableStateOf<HomeFeed?>(null) }
    val presentationResults = remember(
        binding?.serverProfileId?.value,
        binding?.userId?.value,
        binding?.deviceId?.value,
    ) { mutableStateMapOf<String, RecommendationPresentationResult>() }
    val selectedImportItem = importItems.firstOrNull { it.entry.importEntryId == selectedImportEntryId }
    val importCandidates by (selectedImportItem?.latestEvaluation?.decisionId?.let(importRepository::observeCandidates) ?: flowOf(emptyList()))
        .collectAsState(initial = emptyList())
    val importLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            val inspector = ContentUriInspector(context.contentResolver)
            val permission = inspector.acquirePersistableReadPermission(uri.toString(), android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or android.content.Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            runCatching {
                val now = System.currentTimeMillis()
                val inspection = inspector.inspectWithDigest(uri.toString())
                val job = importRepository.createOrResume(
                    singleUriImportCommand(importProfileId, inspection, permission, now),
                )
                val entry = importRepository.entriesOnce(job.importJobId).single()
                importRepository.recordShadowEvaluation(
                    RecordShadowEvaluationCommand(
                        importEntryId = entry.importEntryId,
                        idempotencyKey = "local-uri-evidence-v1",
                        resolverState = ImportResolverState.DEFERRED_EVIDENCE,
                        evidenceMode = "AUDIO_AVAILABLE",
                        matcherVersion = "android-local-shadow/1",
                        explanationJson = "{\"schema_version\":1,\"reason_code\":\"FINGERPRINT_EVIDENCE_DEFERRED\"}",
                        candidates = emptyList(),
                        nowMs = now,
                    ),
                )
            }
                .onFailure { stableError = "IMPORT_UNAVAILABLE" }
        }
    }
    LaunchedEffect(view, binding) {
        if (view == "Home" && binding != null) {
            homeFeed = null
            runCatching { recommendationRepository.loadHomeFeed(binding, System.currentTimeMillis()) }
                .onSuccess { homeFeed = it }
                .onFailure {
                    homeFeed = null
                    stableError = "HOME_FEED_UNAVAILABLE"
                }
            // Refresh is optional and never blocks the first cached/local render.
            runCatching {
                AutPlayRuntime.refreshRecommendationPack(
                    context,
                    binding,
                    recommendationRepository,
                    System.currentTimeMillis(),
                )
            }.onSuccess {
                homeFeed = recommendationRepository.loadHomeFeed(binding, System.currentTimeMillis())
            }
        }
    }
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(text = "$BOOTSTRAP_LABEL offline")
            Text("Home | Library | Search | Playlists | History | Import | Player | Downloads | Sync")
            Row {
                Button(onClick = { view = "Home" }) { Text("Home") }
                Button(onClick = { view = "Library" }) { Text("Library") }
            }
            Button(onClick = { view = "Search" }) { Text("Search") }
            Button(onClick = { view = "Playlists" }) { Text("Playlists") }
            Button(onClick = { view = "History" }) { Text("History") }
            Button(onClick = { view = "Import" }) { Text("Import") }
            Button(onClick = { view = "Player" }) { Text("Player") }
            Button(onClick = { view = "Downloads" }) { Text("Downloads") }
            Button(onClick = { view = "Sync" }) { Text("Sync") }
            Text(text = "$entryCount local track(s)", modifier = Modifier.padding(vertical = 12.dp))
            if (binding == null) {
                Text("Standalone changes stay local until you choose a server profile")
            }
            Button(
                onClick = {
                    scope.launch {
                        repository.add(
                            AddLocalTrackCommand(
                                binding = binding,
                                trackRefId = LocalId.random(),
                                libraryEntryId = LocalId.random(),
                                localChangeId = LocalId.random(),
                                title = "Offline sample",
                                artist = "AutPlay",
                                occurredAtMs = System.currentTimeMillis(),
                            ),
                        )
                    }
                },
            ) {
                Text("Add offline sample")
            }
            libraryEntries.firstOrNull()?.let { entry ->
                Button(
                    onClick = {
                        scope.launch {
                            runCatching {
                                if (entry.removedAtMs == null) {
                                    sliceRepository.removeLibrary(
                                        binding,
                                        LocalId(entry.localLibraryEntryId),
                                        LocalId.random(),
                                        System.currentTimeMillis(),
                                    )
                                } else {
                                    sliceRepository.restoreLibrary(
                                        binding,
                                        LocalId(entry.localLibraryEntryId),
                                        LocalId.random(),
                                        System.currentTimeMillis(),
                                    )
                                }
                            }.onFailure { stableError = "LIBRARY_UPDATE_UNAVAILABLE" }
                        }
                    },
                ) { Text(if (entry.removedAtMs == null) "Remove first track" else "Restore first track") }
                Button(
                    onClick = {
                        scope.launch {
                            runCatching {
                                sliceRepository.setPreference(
                                    binding,
                                    LocalId(entry.localUserTrackRefId),
                                    LocalId.random(),
                                    "LIKED",
                                    false,
                                    null,
                                    System.currentTimeMillis(),
                                )
                            }.onFailure { stableError = "PREFERENCE_UNAVAILABLE" }
                        }
                    },
                ) { Text("Like first track") }
                Button(
                    onClick = {
                        scope.launch {
                            runCatching {
                                sliceRepository.recordListening(
                                    binding,
                                    LocalId.random(),
                                    LocalId(entry.localUserTrackRefId),
                                    1_000,
                                    null,
                                    false,
                                    "ORGANIC",
                                    now = System.currentTimeMillis(),
                                )
                            }.onFailure { stableError = "HISTORY_UNAVAILABLE" }
                        }
                    },
                ) { Text("Record logical listen") }
            }
            when (view) {
                "Home" -> {
                    Text("Home")
                    if (binding == null) {
                        Text("Recommendations need an owner-bound server profile; local library remains available.")
                    } else {
                        val feed = homeFeed?.takeIf {
                            it.ownerProfileId == binding.serverProfileId.value &&
                                it.ownerUserId == binding.userId.value &&
                                it.ownerDeviceId == binding.deviceId.value
                        }
                        if (feed == null) {
                            Text("Loading local Home feed")
                        } else {
                            if (feed.isStaleFallback) {
                                Text("Offline fallback: verified pack expired less than 24 hours ago; only locally available tracks are shown.")
                            }
                            Text("Home status: ${feed.statusCode}")
                            Text("New releases")
                            if (feed.recentRelevantReleases.isEmpty()) Text("No recent relevant releases")
                            feed.recentRelevantReleases.forEach { release ->
                                Text("${release.title} — ${release.artist}${release.releaseDateText?.let { " · $it" } ?: ""}")
                            }
                            Text("Recommendations")
                            if (feed.recommendationSections.isEmpty()) Text("No locally available recommendations")
                            feed.recommendationSections.forEach { (section, items) ->
                                Text(section.replace('_', ' '))
                                items.forEach { item ->
                                    val presentationId = checkNotNull(feed.presentationId)
                                    val key = "$presentationId:${item.recommendationRequestId}:${item.sourceRank}"
                                    VisibleRecommendation(
                                        item = item,
                                        onPresented = { complete ->
                                            if (presentationResults.containsKey(key)) {
                                                complete(true)
                                            } else {
                                                scope.launch {
                                                    runCatching {
                                                        recommendationRepository.recordPresentation(
                                                            binding,
                                                            LocalId(presentationId),
                                                            item,
                                                            System.currentTimeMillis(),
                                                        )
                                                    }.onSuccess {
                                                        presentationResults[key] = it
                                                        complete(true)
                                                    }.onFailure {
                                                        stableError = "IMPRESSION_UNAVAILABLE"
                                                        complete(false)
                                                    }
                                                }
                                            }
                                        },
                                    )
                                    val impression = presentationResults[key]
                                    Button(
                                        enabled = impression != null,
                                        onClick = {
                                            val recorded = impression ?: return@Button
                                            scope.launch {
                                                runCatching {
                                                    sliceRepository.setPreference(
                                                        binding,
                                                        LocalId(item.localUserTrackRefId),
                                                        LocalId.random(),
                                                        "LIKED",
                                                        false,
                                                        recommendationRepository.attributionJson(
                                                            LocalId(presentationId),
                                                            recorded.impressionEventId,
                                                            item,
                                                        ),
                                                        System.currentTimeMillis(),
                                                    )
                                                    homeFeed = recommendationRepository.loadHomeFeed(binding, System.currentTimeMillis())
                                                }.onFailure { stableError = "PREFERENCE_UNAVAILABLE" }
                                            }
                                        },
                                    ) { Text("Like ${item.title}") }
                                    Button(
                                        enabled = impression != null,
                                        onClick = {
                                            val recorded = impression ?: return@Button
                                            scope.launch {
                                                runCatching {
                                                    sliceRepository.setPreference(
                                                        binding,
                                                        LocalId(item.localUserTrackRefId),
                                                        LocalId.random(),
                                                        "DISLIKED",
                                                        false,
                                                        recommendationRepository.attributionJson(
                                                            LocalId(presentationId),
                                                            recorded.impressionEventId,
                                                            item,
                                                        ),
                                                        System.currentTimeMillis(),
                                                    )
                                                    homeFeed = recommendationRepository.loadHomeFeed(binding, System.currentTimeMillis())
                                                }.onFailure { stableError = "PREFERENCE_UNAVAILABLE" }
                                            }
                                        },
                                    ) { Text("Dislike ${item.title}") }
                                }
                            }
                        }
                    }
                }
                "Search" -> {
                    OutlinedTextField(value = searchText, onValueChange = { searchText = it }, label = { Text("Search library") })
                    Button(onClick = { scope.launch { runCatching { searchRepository.search(searchText, binding?.serverProfileId?.value).size }.onSuccess { searchResultCount = it }.onFailure { stableError = "SEARCH_UNAVAILABLE" } } }) { Text("Run search") }
                    searchResultCount?.let { Text("$it search result(s)") }
                }
                "Playlists" -> {
                    Text("${playlists.size} playlist(s)")
                    Button(
                        onClick = {
                            scope.launch {
                                runCatching {
                                    sliceRepository.createPlaylist(
                                        binding,
                                        LocalId.random(),
                                        LocalId.random(),
                                        "Offline playlist ${playlists.size + 1}",
                                        null,
                                        System.currentTimeMillis(),
                                    )
                                }.onFailure { stableError = "PLAYLIST_CREATE_UNAVAILABLE" }
                            }
                        },
                    ) { Text("Create playlist") }
                    val playlist = playlists.firstOrNull()
                    val entry = libraryEntries.firstOrNull { it.removedAtMs == null }
                    if (playlist != null && entry != null) {
                        Button(
                            onClick = {
                                scope.launch {
                                    runCatching {
                                        sliceRepository.addPlaylistEntry(
                                            binding,
                                            LocalId(playlist.localPlaylistId),
                                            LocalId.random(),
                                            LocalId(entry.localUserTrackRefId),
                                            LocalId.random(),
                                            null,
                                            null,
                                            System.currentTimeMillis(),
                                        )
                                    }.onFailure { stableError = "PLAYLIST_ENTRY_UNAVAILABLE" }
                                }
                            },
                        ) { Text("Add first track to playlist") }
                    }
                }
                "History" -> Text("${history.size} listening event(s)")
                "Import" -> {
                    Text("Choose a MediaStore or SAF audio URI to import; revoked or missing URIs remain repairable.")
                    Button(onClick = { importLauncher.launch(arrayOf("audio/*")) }) { Text("Choose audio") }
                    importJob?.let { job ->
                        Text("Import ${job.state}: ${job.totalEntries} row(s)")
                        Text("Review ${job.reviewRequiredCount} · Resolved ${job.resolvedCount} · No match ${job.noMatchCount} · Unresolved ${job.unresolvedCount} · Failed ${job.failedCount}")
                        importItems.forEach { item ->
                            val entry = item.entry
                            Text("${entry.rawTitle} — ${entry.rawArtist} · ${item.effectiveState}")
                            Text("Source: ${entry.sourceAvailability}${if (entry.persistedUriPermission) " · persistent access" else " · repair may be required"}")
                            if (item.effectiveState in setOf("REVIEW_REQUIRED", "INTEGRITY_CONFLICT", "DEFERRED_EVIDENCE", "NO_MATCH")) {
                                Button(onClick = { selectedImportEntryId = entry.importEntryId }) { Text("Review ${entry.rawTitle}") }
                            }
                        }
                    } ?: Text("No import job yet")
                    selectedImportItem?.let { item ->
                        val entry = item.entry
                        Text("Review: ${entry.rawTitle} — ${entry.rawArtist}")
                        if (item.effectiveState == "INTEGRITY_CONFLICT") Text("Hard conflict: resolution is blocked until evidence is cleared")
                        importCandidates.forEach { candidate ->
                            Text("Candidate ${candidate.rank}: ${candidate.titleSnapshot} — ${candidate.artistSnapshot}")
                            Text("Confidence ${candidate.confidence?.toString() ?: "deferred"} · ${candidate.evidenceTier}")
                            Text("Evidence: ${candidate.featureEvidenceJson}")
                            if (candidate.hardConflictsJson != "[]") Text("Hard-conflict warning: ${candidate.hardConflictsJson}")
                            if (item.effectiveState == "REVIEW_REQUIRED") {
                                Button(onClick = {
                                    scope.launch {
                                        runCatching {
                                            importRepository.recordReview(
                                                RecordImportReviewCommand(
                                                    entry.importEntryId,
                                                    LocalId.random().value,
                                                    LocalId.random().value,
                                                    ImportReviewAction.ACCEPT,
                                                    candidateId = candidate.candidateId,
                                                    predecessorDecisionId = item.latestEvaluation?.decisionId,
                                                    nowMs = System.currentTimeMillis(),
                                                ),
                                            )
                                        }.onFailure { stableError = "IMPORT_REVIEW_UNAVAILABLE" }
                                    }
                                }) { Text("Accept candidate ${candidate.rank}") }
                                Button(onClick = {
                                    scope.launch {
                                        runCatching {
                                            importRepository.recordReview(
                                                RecordImportReviewCommand(
                                                    entry.importEntryId,
                                                    LocalId.random().value,
                                                    LocalId.random().value,
                                                    ImportReviewAction.REJECT,
                                                    candidateId = candidate.candidateId,
                                                    predecessorDecisionId = item.latestEvaluation?.decisionId,
                                                    nowMs = System.currentTimeMillis(),
                                                ),
                                            )
                                        }.onFailure { stableError = "IMPORT_REVIEW_UNAVAILABLE" }
                                    }
                                }) { Text("Reject candidate ${candidate.rank}") }
                            }
                        }
                        Button(onClick = {
                            scope.launch {
                                runCatching {
                                    importRepository.recordReview(
                                        RecordImportReviewCommand(
                                            entry.importEntryId,
                                            LocalId.random().value,
                                            LocalId.random().value,
                                            ImportReviewAction.KEEP_UNRESOLVED,
                                            predecessorDecisionId = item.latestEvaluation?.decisionId,
                                            nowMs = System.currentTimeMillis(),
                                        ),
                                    )
                                }.onFailure { stableError = "IMPORT_REVIEW_UNAVAILABLE" }
                            }
                        }) { Text("Keep unresolved") }
                        if (item.effectiveState in setOf("REVIEW_REQUIRED", "NO_MATCH", "DEFERRED_EVIDENCE")) {
                            Button(onClick = {
                                scope.launch {
                                    runCatching {
                                        importRepository.recordReview(
                                            RecordImportReviewCommand(
                                                entry.importEntryId,
                                                LocalId.random().value,
                                                LocalId.random().value,
                                                ImportReviewAction.CREATE_RECORDING,
                                                createdRecordingId = LocalId.random().value,
                                                predecessorDecisionId = item.latestEvaluation?.decisionId,
                                                nowMs = System.currentTimeMillis(),
                                            ),
                                        )
                                    }.onFailure { stableError = "IMPORT_REVIEW_UNAVAILABLE" }
                                }
                            }) { Text("Create new Recording") }
                        }
                    }
                }
                "Player" -> {
                    Text("Mini player · ${playbackState.title ?: "Nothing playing"}")
                    Text("${playbackState.source ?: "NO_SOURCE"} · ${playbackState.positionMs} ms")
                    playbackState.unavailableReason?.let { Text("Unavailable: $it") }
                    val first = libraryEntries.firstOrNull { it.removedAtMs == null }
                    Button(
                        enabled = first != null,
                        onClick = {
                            val entry = first ?: return@Button
                            scope.launch {
                                runCatching {
                                    val snapshotId = LocalId.random()
                                    playbackRepository.activateQueue(
                                        snapshotId = snapshotId,
                                        entries = listOf(
                                            NewPlaybackQueueEntry(
                                                queueEntryId = LocalId.random(),
                                                trackRefId = LocalId(entry.localUserTrackRefId),
                                                sourceOrigin = "ORGANIC",
                                                sourceAudioPolicy = "LOCAL_THEN_VAULT",
                                            ),
                                        ),
                                        queueType = "USER",
                                        sourceContextId = null,
                                        serverProfileId = binding?.serverProfileId?.value,
                                        listeningContext = "GENERAL",
                                        nowMs = System.currentTimeMillis(),
                                    )
                                    playbackOwner.dispatch(PlaybackCommand.StartQueue(snapshotId))
                                }.onFailure { stableError = "PLAYBACK_UNAVAILABLE" }
                            }
                        },
                    ) { Text("Play first track") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Pause) } }) { Text("Pause playback") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Resume) } }) { Text("Resume playback") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Stop) } }) { Text("Stop and finalize") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SeekTo((playbackState.positionMs - 15_000).coerceAtLeast(0))) } }) { Text("Back 15s") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SeekTo(playbackState.positionMs + 15_000)) } }) { Text("Forward 15s") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SetShuffleEnabled(!playbackState.shuffleEnabled)) } }) { Text("Shuffle ${if (playbackState.shuffleEnabled) "on" else "off"}") }
                    val nextRepeat = when (playbackState.repeatMode) { "OFF" -> "ALL"; "ALL" -> "ONE"; else -> "OFF" }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SetRepeatMode(nextRepeat)) } }) { Text("Repeat ${playbackState.repeatMode}") }
                    Text("Full player · Queue ${if (first == null) 0 else 1} · ${if (playbackState.isPlaying) "Playing" else "Paused"}")
                }
                "Downloads" -> {
                    Text("${downloads.size} download intent(s); Media3 DownloadIndex owns progress")
                    downloads.firstOrNull()?.let { Text("Latest download: ${it.state}") }
                    val first = libraryEntries.firstOrNull { it.removedAtMs == null }
                    Button(
                        enabled = first != null && binding != null,
                        onClick = {
                            val entry = first ?: return@Button
                            val activeBinding = binding ?: return@Button
                            scope.launch {
                                runCatching {
                                    downloadRepository.requestPreferredVaultDownload(
                                        LocalId(entry.localUserTrackRefId),
                                        activeBinding.serverProfileId,
                                        DownloadStorageClass.USER_DOWNLOAD,
                                        System.currentTimeMillis(),
                                    )
                                }.onFailure { stableError = "DOWNLOAD_UNAVAILABLE" }
                            }
                        },
                    ) { Text("Download first track") }
                }
                "Sync" -> {
                    if (binding == null) {
                        Text("Sync is unavailable until a server profile is bound")
                    } else {
                        val status by syncStatusRepository.observe(binding).collectAsState(
                            initial = app.autplay.application.sync.SyncStatus(0, 0, 0, null, "LOADING"),
                        )
                        Text("Pending: ${status.pending} · Dead letters: ${status.deadLetters} · Conflicts: ${status.conflicts}")
                        Text("State: ${status.bootstrapState}")
                        Text("Last success: ${status.lastSuccessAtMs?.toString() ?: "Never"}")
                        status.lastErrorCode?.let { Text("Last error: $it") }
                        Button(onClick = {
                            syncScheduler.enqueue(
                                DeferredWorkRequest(
                                    DeferredWorkKind.SYNC,
                                    DeferredWorkSubject.Device(binding.deviceId),
                                    binding.serverProfileId,
                                ),
                            )
                        }) { Text("Retry sync") }
                    }
                }
            }
            stableError?.let { Text(it) }
        }
    }
}

@Composable
private fun VisibleRecommendation(
    item: HomeRecommendationItem,
    onPresented: (complete: (Boolean) -> Unit) -> Unit,
) {
    val attempt = remember(item.recommendationRequestId, item.sourceRank, item.displayPosition) {
        PresentationAttemptState()
    }
    Column(
        modifier = Modifier.onVisibilityChanged(minFractionVisible = 0.01f) { visible ->
            if (visible && attempt.begin()) {
                onPresented(attempt::complete)
            }
        },
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("${item.displayPosition}. ${item.title} — ${item.artist}")
        Text("${item.reasonCode} · original rank ${item.sourceRank}")
    }
}

internal class PresentationAttemptState {
    private var inFlight = false
    private var presented = false

    fun begin(): Boolean {
        if (inFlight || presented) return false
        inFlight = true
        return true
    }

    fun complete(success: Boolean) {
        inFlight = false
        presented = success
    }
}

@Preview(showBackground = true)
@Composable
@UnstableApi
private fun AutPlayBootstrapPreview() {
    AutPlayBootstrap()
}
