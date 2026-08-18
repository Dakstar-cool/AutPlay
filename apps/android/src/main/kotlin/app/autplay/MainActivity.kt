package app.autplay

import android.os.Bundle
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.DisposableEffect
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
import app.autplay.application.settings.SettingsTransferCodec
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.importing.ContentTreeAudioScanner
import app.autplay.application.importing.ImportResolverState
import app.autplay.application.importing.ImportReviewAction
import app.autplay.application.importing.LEGACY_PROFILE_ID
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.importing.RecordImportReviewCommand
import app.autplay.application.importing.RecordShadowEvaluationCommand
import app.autplay.application.importing.singleUriImportCommand
import app.autplay.application.importing.treeUriImportCommand
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.SyncStatusRepository
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.recommendation.HomeFeed
import app.autplay.application.recommendation.HomeRecommendationItem
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.RecommendationPresentationResult
import app.autplay.application.server.RemoteImportEntry
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.application.server.ServerFeatureStateRepository
import app.autplay.application.wave.WaveCoordinator
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.local.entity.RemoteImportJobProjectionEntity
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.playback.PlaybackRuntimeState
import app.autplay.playback.PlaybackUiState
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject
import app.autplay.work.SyncWorker
import app.autplay.work.RecommendationPackWorkScheduler
import app.autplay.work.RemoteImportWorkScheduler
import app.autplay.work.VaultUploadWorkScheduler
import app.autplay.work.WorkManagerDeferredWorkScheduler
import app.autplay.work.shouldScheduleRemoteImport
import app.autplay.ui.AutPlayAccent
import app.autplay.ui.AutPlayAdaptiveShell
import app.autplay.ui.AutPlayAppearance
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.AutPlayThemeMode
import app.autplay.ui.UiDestination
import app.autplay.ui.ServerFeaturesScreen
import app.autplay.ui.ServerFeaturesUiState
import java.io.ByteArrayOutputStream
import java.io.InputStream
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
    if (repository == null || settingsStore == null || searchRepository == null || sliceRepository == null ||
        playbackRepository == null || playbackOwner == null || downloadRepository == null || syncStatusRepository == null || syncScheduler == null || importRepository == null || recommendationRepository == null
    ) {
        MaterialTheme {
            Surface(modifier = Modifier.fillMaxSize()) {
                Box(contentAlignment = Alignment.Center) {
                    Text(text = BOOTSTRAP_LABEL)
                }
            }
        }
    } else {
        val settings by settingsStore.settings.collectAsState(initial = NonSecretSettings())
        val binding = settings.activeUserId?.let { userId ->
            val deviceId = settings.deviceId ?: return@let null
            val profileId = settings.activeServerProfileId ?: return@let null
            ClientEventBinding(userId, deviceId, profileId)
        }
        val context = LocalContext.current
        var waveCoordinator by remember { mutableStateOf<WaveCoordinator?>(null) }
        LaunchedEffect(binding) {
            waveCoordinator?.close()
            waveCoordinator = if (binding == null) null else runCatching {
                AutPlayRuntime.waveCoordinator(context, binding)
            }.getOrNull()
        }
        DisposableEffect(Unit) {
            onDispose { waveCoordinator?.close() }
        }
        AutPlayTheme(appearanceFrom(settings)) {
            Surface(modifier = Modifier.fillMaxSize()) {
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
                    settings,
                    settingsStore,
                    waveCoordinator,
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
    settings: NonSecretSettings,
    settingsStore: NonSecretSettingsStore,
    waveCoordinator: WaveCoordinator?,
) {
    val entryCount by repository.activeEntryCount(binding?.serverProfileId?.value).collectAsState(initial = 0)
    val libraryEntries by repository.entries(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var destination by remember { mutableStateOf<UiDestination>(UiDestination.Home) }
    val view = legacyView(destination)
    var searchText by remember { mutableStateOf("") }
    var searchResults by remember { mutableStateOf<List<UserTrackRefEntity>>(emptyList()) }
    var stableError by remember { mutableStateOf<String?>(null) }
    val playlists by sliceRepository.playlists(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val history by sliceRepository.history(binding?.serverProfileId?.value).collectAsState(initial = emptyList())
    val downloads by downloadRepository.observeIntents().collectAsState(initial = emptyList())
    val playbackState by PlaybackRuntimeState.state.collectAsState()
    val syncStatus by (binding?.let(syncStatusRepository::observe) ?: flowOf(
        app.autplay.application.sync.SyncStatus(0, 0, 0, null, "STANDALONE"),
    )).collectAsState(initial = app.autplay.application.sync.SyncStatus(0, 0, 0, null, "LOADING"))
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
    var selectedTrackRefId by remember { mutableStateOf<String?>(null) }
    var selectedUploadCandidate by remember { mutableStateOf<VaultUploadCandidate?>(null) }
    var remoteImportJobId by remember(binding?.serverProfileId?.value) { mutableStateOf<String?>(null) }
    var serverUiState by remember(binding?.serverProfileId?.value) { mutableStateOf(ServerFeaturesUiState()) }
    val serverStateRepository = remember { ServerFeatureStateRepository(AutPlayRuntime.database(context)) }
    val durableRemoteImports by (
        binding?.let { serverStateRepository.observeRemoteImports(it.serverProfileId) }
            ?: flowOf(emptyList<RemoteImportJobProjectionEntity>())
    ).collectAsState(initial = emptyList())
    val durableVaultUploads by (
        binding?.let { serverStateRepository.observeVaultUploads(it.serverProfileId) }
            ?: flowOf(emptyList<VaultUploadIntentEntity>())
    ).collectAsState(initial = emptyList())

    LaunchedEffect(durableRemoteImports) {
        if (remoteImportJobId == null) remoteImportJobId = durableRemoteImports.firstOrNull()?.importJobId
        durableRemoteImports
            .filter { shouldScheduleRemoteImport(it.state, it.lastErrorCode) }
            .take(10)
            .forEach { RemoteImportWorkScheduler.enqueue(context, it.importJobId) }
    }
    LaunchedEffect(durableVaultUploads) {
        serverUiState = serverUiState.copy(
            uploadStatus = durableVaultUploads.firstOrNull()?.let { upload ->
                "${upload.state} · ${upload.remoteOffset}/${upload.expectedSize} bytes" +
                    (upload.lastErrorCode?.let { " · $it" } ?: "")
            },
        )
    }

    LaunchedEffect(libraryEntries, selectedTrackRefId) {
        val activeIds = libraryEntries.filter { it.removedAtMs == null }.map { it.localUserTrackRefId }
        if (selectedTrackRefId !in activeIds) selectedTrackRefId = activeIds.firstOrNull()
    }
    LaunchedEffect(selectedTrackRefId, binding?.serverProfileId?.value) {
        val trackRefId = selectedTrackRefId
        selectedUploadCandidate = if (trackRefId == null || binding == null) {
            null
        } else {
            val track = repository.trackRef(trackRefId)
            val local = AutPlayRuntime.database(context).localAudioDao()
                .statesForPlayback(trackRefId, 10)
                .firstOrNull { it.status == "AVAILABLE" }
            if (track?.serverRecordingId != null && local != null) {
                VaultUploadCandidate(
                    trackRefId,
                    track.serverRecordingId,
                    local.localAudioStateId,
                    local.localSha256,
                    local.byteSize,
                )
            } else {
                null
            }
        }
    }

    fun launchServerAction(
        action: String,
        operation: suspend (ServerFeatureRepository) -> Unit,
    ) {
        val activeBinding = binding
        if (activeBinding == null) {
            serverUiState = serverUiState.copy(stableMessage = "SERVER_PROFILE_NOT_ACTIVE")
            return
        }
        scope.launch {
            serverUiState = serverUiState.copy(busyAction = action, stableMessage = null)
            runCatching {
                operation(AutPlayRuntime.serverFeatures(context, activeBinding))
            }.onFailure {
                serverUiState = serverUiState.copy(stableMessage = "${action}_UNAVAILABLE")
            }
            serverUiState = serverUiState.copy(busyAction = null)
        }
    }
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
            // Refresh is durable and never blocks the first cached/local render.
            RecommendationPackWorkScheduler.enqueue(context, binding.serverProfileId.value)
        }
    }
    LaunchedEffect(view, remoteImportJobId, binding?.serverProfileId?.value) {
        val jobId = remoteImportJobId
        if (view == "Server" && jobId != null && serverUiState.importReport?.importJobId != jobId) {
            launchServerAction("SERVER_IMPORT_REFRESH") { server ->
                val report = server.importReport(jobId)
                serverStateRepository.recordImportReport(checkNotNull(binding).serverProfileId, report, System.currentTimeMillis())
                serverUiState = serverUiState.copy(importReport = report)
            }
        }
    }
    val libraryRootLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
                settingsStore.mutate { current -> current.copy(libraryRootTreeUri = uri.toString()) }
                val outcome = scanLibraryRoot(
                    context = context,
                    treeUri = uri.toString(),
                    importProfileId = importProfileId,
                    importRepository = importRepository,
                )
                destination = UiDestination.ImportReview
                if (outcome.truncated) stableError = "LIBRARY_ROOT_SCAN_LIMIT_REACHED"
            }.onFailure { stableError = "LIBRARY_ROOT_PERMISSION_UNAVAILABLE" }
        }
    }
    val settingsExportLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/json"),
    ) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                context.contentResolver.openOutputStream(uri, "wt")?.use { output ->
                    output.write(SettingsTransferCodec.encode(settings))
                } ?: error("SETTINGS_EXPORT_TARGET_UNAVAILABLE")
            }.onFailure { stableError = "SETTINGS_EXPORT_UNAVAILABLE" }
        }
    }
    val settingsImportLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.use(::readBoundedSettingsBytes)
                    ?: error("SETTINGS_IMPORT_SOURCE_UNAVAILABLE")
                settingsStore.mutate { current -> SettingsTransferCodec.decode(bytes, current) }
            }.onFailure { stableError = "SETTINGS_IMPORT_UNAVAILABLE" }
        }
    }
    val serverImportLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            launchServerAction("SERVER_IMPORT_START") { server ->
                val format = serverImportFormat(context, uri)
                val payload = context.contentResolver.openInputStream(uri)?.use(::readBoundedServerImportBytes)
                    ?: error("SERVER_IMPORT_SOURCE_UNAVAILABLE")
                val started = server.startImport(payload, format, materialize = true)
                val activeBinding = checkNotNull(binding)
                serverStateRepository.recordImportStart(
                    activeBinding.serverProfileId,
                    started,
                    System.currentTimeMillis(),
                )
                remoteImportJobId = started.importJobId
                val report = server.importReport(started.importJobId)
                serverStateRepository.recordImportReport(
                    activeBinding.serverProfileId,
                    report,
                    System.currentTimeMillis(),
                )
                RemoteImportWorkScheduler.enqueue(context, started.importJobId)
                serverUiState = serverUiState.copy(importReport = report, stableMessage = "SERVER_IMPORT_STARTED")
            }
        }
    }
    AutPlayAdaptiveShell(
        selectedDestination = destination,
        onDestinationSelected = { destination = it },
        unreadSyncConflicts = syncStatus.deadLetters + syncStatus.conflicts,
        onProfileClick = { destination = UiDestination.Profile },
        onNowPlayingClick = { destination = UiDestination.NowPlaying },
        nowPlayingBar = {
            PlaybackMiniBar(
                playbackState = playbackState,
                onOpen = { destination = UiDestination.NowPlaying },
                onPlayPause = {
                    scope.launch {
                        playbackOwner.dispatch(
                            if (playbackState.isPlaying) PlaybackCommand.Pause else PlaybackCommand.Resume,
                        )
                    }
                },
            )
        },
    ) { _, contentPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(contentPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(text = destination.label, style = MaterialTheme.typography.headlineSmall)
            if (destination == UiDestination.Library) {
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
            libraryEntries.filter { it.removedAtMs == null }.forEach { item ->
                OutlinedButton(onClick = { selectedTrackRefId = item.localUserTrackRefId }) {
                    val selected = item.localUserTrackRefId == selectedTrackRefId
                    Text("${if (selected) "✓ " else ""}${item.localUserTrackRefId.take(12)}…")
                }
            }
            libraryEntries.firstOrNull { it.localUserTrackRefId == selectedTrackRefId }?.let { entry ->
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
                ) { Text(if (entry.removedAtMs == null) "Remove selected track" else "Restore selected track") }
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
                ) { Text("Like selected track") }
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
                    Button(onClick = {
                        scope.launch {
                            runCatching { searchRepository.search(searchText, binding?.serverProfileId?.value) }
                                .onSuccess { searchResults = it }
                                .onFailure { stableError = "SEARCH_UNAVAILABLE" }
                        }
                    }) { Text("Run search") }
                    Text("${searchResults.size} search result(s)")
                    searchResults.forEach { result ->
                        Text("${result.rawTitle} — ${result.rawArtist ?: "Unknown artist"}")
                        Button(onClick = {
                            scope.launch {
                                runCatching {
                                    val snapshotId = LocalId.random()
                                    playbackRepository.activateQueue(
                                        snapshotId = snapshotId,
                                        entries = listOf(
                                            NewPlaybackQueueEntry(
                                                queueEntryId = LocalId.random(),
                                                trackRefId = LocalId(result.localUserTrackRefId),
                                                sourceOrigin = "SEARCH",
                                                sourceAudioPolicy = "LOCAL_THEN_VAULT",
                                            ),
                                        ),
                                        queueType = "SEARCH",
                                        sourceContextId = null,
                                        serverProfileId = binding?.serverProfileId?.value,
                                        listeningContext = "GENERAL",
                                        nowMs = System.currentTimeMillis(),
                                    )
                                    playbackOwner.dispatch(PlaybackCommand.StartQueue(snapshotId))
                                }.onFailure { stableError = "PLAYBACK_UNAVAILABLE" }
                            }
                        }) { Text("Play ${result.rawTitle}") }
                    }
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
                    val entry = libraryEntries.firstOrNull { it.localUserTrackRefId == selectedTrackRefId }
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
                        ) { Text("Add selected track to playlist") }
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
                    val first = libraryEntries.firstOrNull { it.localUserTrackRefId == selectedTrackRefId }
                    val currentTrackRefId = playbackState.localUserTrackRefId
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
                    ) { Text("Play selected track") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Pause) } }) { Text("Pause playback") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Resume) } }) { Text("Resume playback") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.Stop) } }) { Text("Stop and finalize") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SeekTo((playbackState.positionMs - 15_000).coerceAtLeast(0))) } }) { Text("Back 15s") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SeekTo(playbackState.positionMs + 15_000)) } }) { Text("Forward 15s") }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SetShuffleEnabled(!playbackState.shuffleEnabled)) } }) { Text("Shuffle ${if (playbackState.shuffleEnabled) "on" else "off"}") }
                    val nextRepeat = when (playbackState.repeatMode) { "OFF" -> "ALL"; "ALL" -> "ONE"; else -> "OFF" }
                    Button(onClick = { scope.launch { playbackOwner.dispatch(PlaybackCommand.SetRepeatMode(nextRepeat)) } }) { Text("Repeat ${playbackState.repeatMode}") }
                    TrackPreferenceActions(
                        enabled = currentTrackRefId != null,
                        onLike = {
                            val trackRefId = currentTrackRefId ?: return@TrackPreferenceActions
                            scope.launch {
                                runCatching {
                                    sliceRepository.setPreference(
                                        binding, LocalId(trackRefId), LocalId.random(), "LIKED",
                                        false, null, System.currentTimeMillis(),
                                    )
                                }.onFailure { stableError = "PREFERENCE_UNAVAILABLE" }
                            }
                        },
                        onDislike = {
                            val trackRefId = currentTrackRefId ?: return@TrackPreferenceActions
                            scope.launch {
                                runCatching {
                                    sliceRepository.setPreference(
                                        binding, LocalId(trackRefId), LocalId.random(), "DISLIKED",
                                        false, null, System.currentTimeMillis(),
                                    )
                                }.onFailure { stableError = "PREFERENCE_UNAVAILABLE" }
                            }
                        },
                    )
                    Text("Full player · ${if (playbackState.isPlaying) "Playing" else "Paused"}")
                }
                "Downloads" -> {
                    Text("${downloads.size} download intent(s); Media3 DownloadIndex owns progress")
                    downloads.firstOrNull()?.let { Text("Latest download: ${it.state}") }
                    val first = libraryEntries.firstOrNull { it.localUserTrackRefId == selectedTrackRefId }
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
                    ) { Text("Download selected track") }
                }
                "Sync" -> {
                    if (binding == null) {
                        Text("Sync is unavailable until a server profile is bound")
                    } else {
                        Text("Pending: ${syncStatus.pending} · Dead letters: ${syncStatus.deadLetters} · Conflicts: ${syncStatus.conflicts}")
                        Text("State: ${syncStatus.bootstrapState}")
                        Text("Last success: ${syncStatus.lastSuccessAtMs?.toString() ?: "Never"}")
                        syncStatus.lastErrorCode?.let { Text("Last error: $it") }
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
                "Wave" -> WaveFrontendScreen(
                    coordinator = waveCoordinator,
                    isProfileBound = binding != null,
                    localTrackRefId = selectedTrackRefId,
                    resolveServerRecordingId = { trackRefId ->
                        repository.trackRef(trackRefId)?.serverRecordingId
                    },
                    onError = { stableError = it },
                )
                "Server" -> ServerFeaturesScreen(
                    isBound = binding != null,
                    selectedTrackLabel = selectedTrackRefId?.let { "Selected track ${it.take(12)}…" },
                    selectedTrackUploadEligible = selectedUploadCandidate != null,
                    state = serverUiState,
                    onRefreshHealth = {
                        launchServerAction("SERVER_HEALTH") { server ->
                            serverUiState = serverUiState.copy(health = server.health())
                        }
                    },
                    onRefreshLibrary = {
                        launchServerAction("SERVER_LIBRARY") { server ->
                            serverUiState = serverUiState.copy(library = server.librarySnapshot())
                        }
                    },
                    onSearch = { query ->
                        launchServerAction("SERVER_SEARCH") { server ->
                            serverUiState = serverUiState.copy(searchResults = server.searchLibrary(query))
                        }
                    },
                    onChooseServerImport = {
                        serverImportLauncher.launch(arrayOf("text/csv", "application/json", "text/html"))
                    },
                    onRefreshImport = {
                        remoteImportJobId?.let { jobId ->
                            launchServerAction("SERVER_IMPORT_REFRESH") { server ->
                                val report = server.importReport(jobId)
                                serverStateRepository.recordImportReport(checkNotNull(binding).serverProfileId, report, System.currentTimeMillis())
                                serverUiState = serverUiState.copy(importReport = report)
                            }
                        }
                    },
                    onLoadNextImport = {
                        val current = serverUiState.importReport
                        val cursor = current?.nextAfter
                        if (current != null && cursor != null) {
                            launchServerAction("SERVER_IMPORT_NEXT") { server ->
                                val page = server.importReport(current.importJobId, cursor)
                                serverUiState = serverUiState.copy(
                                    importReport = page.copy(
                                        entries = (current.entries + page.entries).distinctBy { it.importEntryId },
                                    ),
                                )
                            }
                        }
                    },
                    onCancelImport = {
                        remoteImportJobId?.let { jobId ->
                            launchServerAction("SERVER_IMPORT_CANCEL") { server ->
                                server.cancelImport(jobId)
                                val report = server.importReport(jobId)
                                serverStateRepository.recordImportReport(checkNotNull(binding).serverProfileId, report, System.currentTimeMillis())
                                serverUiState = serverUiState.copy(importReport = report)
                            }
                        }
                    },
                    onResumeImport = {
                        remoteImportJobId?.let { jobId ->
                            launchServerAction("SERVER_IMPORT_RESUME") { server ->
                                val resumed = server.resumeImport(jobId)
                                remoteImportJobId = resumed.importJobId
                                serverStateRepository.recordImportStart(checkNotNull(binding).serverProfileId, resumed, System.currentTimeMillis())
                                val report = server.importReport(resumed.importJobId)
                                serverStateRepository.recordImportReport(binding.serverProfileId, report, System.currentTimeMillis())
                                RemoteImportWorkScheduler.enqueue(context, resumed.importJobId)
                                serverUiState = serverUiState.copy(importReport = report)
                            }
                        }
                    },
                    onReviewImport = { entry, action ->
                        remoteImportJobId?.let { jobId ->
                            launchServerAction("SERVER_IMPORT_REVIEW") { server ->
                                server.reviewImport(jobId, entry, action)
                                val report = server.importReport(jobId)
                                serverStateRepository.recordImportReport(checkNotNull(binding).serverProfileId, report, System.currentTimeMillis())
                                serverUiState = serverUiState.copy(importReport = report)
                            }
                        }
                    },
                    onUploadSelectedTrack = {
                        val candidate = selectedUploadCandidate
                        val activeBinding = binding
                        if (candidate != null && activeBinding != null) scope.launch {
                            val intent = serverStateRepository.enqueueVaultUpload(
                                activeBinding.serverProfileId,
                                candidate.localAudioStateId,
                                candidate.serverRecordingId,
                                candidate.knownSha256,
                                candidate.knownSize,
                                System.currentTimeMillis(),
                            )
                            VaultUploadWorkScheduler.enqueue(context, intent.uploadIntentId)
                        }
                    },
                    onCancelUpload = {
                        durableVaultUploads.firstOrNull {
                            it.state !in setOf(
                                "COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED", "EXPIRED",
                                "INGEST_POLLING_PAUSED",
                            )
                        }
                            ?.let { upload ->
                                scope.launch {
                                    serverStateRepository.cancelVaultUpload(upload.uploadIntentId, System.currentTimeMillis())
                                    VaultUploadWorkScheduler.enqueue(context, upload.uploadIntentId)
                                }
                            }
                    },
                    onRecommendations = { home ->
                        launchServerAction("SERVER_RECOMMENDATIONS") { server ->
                            val result = if (home) server.homeRecommendations() else server.recommendations()
                            serverStateRepository.recordRecommendation(
                                checkNotNull(binding).serverProfileId,
                                result,
                                System.currentTimeMillis(),
                            )
                            serverUiState = serverUiState.copy(
                                recommendation = result,
                            )
                        }
                    },
                    onExactReplay = {
                        serverUiState.recommendation?.requestId?.let { requestId ->
                            launchServerAction("SERVER_REPLAY_EXACT") { server ->
                                val result = server.exactRecommendationReplay(requestId)
                                serverStateRepository.recordRecommendation(checkNotNull(binding).serverProfileId, result, System.currentTimeMillis())
                                serverUiState = serverUiState.copy(recommendation = result)
                            }
                        }
                    },
                    onAlgorithmicReplay = {
                        serverUiState.recommendation?.requestId?.let { requestId ->
                            launchServerAction("SERVER_REPLAY_ALGORITHMIC") { server ->
                                val result = server.algorithmicRecommendationReplay(requestId)
                                serverStateRepository.recordRecommendation(checkNotNull(binding).serverProfileId, result, System.currentTimeMillis())
                                serverUiState = serverUiState.copy(recommendation = result)
                            }
                        }
                    },
                )
                "Profile" -> ProfileFrontendScreen(
                    settings = settings,
                    onOpenSettings = { destination = UiDestination.Settings },
                    onOpenSync = { destination = UiDestination.SyncStatus },
                    onLogout = {
                        launchServerAction("SERVER_LOGOUT") { server ->
                            server.logout()
                            settingsStore.mutate(::deactivateServerBinding)
                        }
                    },
                    onLogoutAll = {
                        launchServerAction("SERVER_LOGOUT_ALL") { server ->
                            server.logoutAll()
                            settingsStore.mutate(::deactivateServerBinding)
                        }
                    },
                    onRevokeCurrentDevice = {
                        settings.deviceId?.value?.let { deviceId ->
                            launchServerAction("SERVER_DEVICE_REVOKE") { server ->
                                server.revokeDevice(deviceId)
                                settingsStore.mutate(::deactivateServerBinding)
                            }
                        }
                    },
                    onDisconnectLocally = {
                        val profileId = settings.activeServerProfileId
                        if (profileId != null) scope.launch {
                            AndroidKeystoreCredentialStore(context.applicationContext).clear(profileId)
                            settingsStore.mutate(::deactivateServerBinding)
                        }
                    },
                )
                "Settings" -> SettingsFrontendScreen(
                    settings = settings,
                    onUpdate = { transform ->
                        scope.launch {
                            updateFrontendSettings(settingsStore, transform)?.let { stableError = it }
                        }
                    },
                    onChooseLibraryRoot = { libraryRootLauncher.launch(null) },
                    onRescanLibraryRoot = {
                        settings.libraryRootTreeUri?.let { treeUri ->
                            scope.launch {
                                runCatching {
                                    scanLibraryRoot(context, treeUri, importProfileId, importRepository)
                                }.onSuccess { outcome ->
                                    destination = UiDestination.ImportReview
                                    if (outcome.truncated) stableError = "LIBRARY_ROOT_SCAN_LIMIT_REACHED"
                                }.onFailure { stableError = "LIBRARY_ROOT_SCAN_UNAVAILABLE" }
                            }
                        }
                    },
                    onExportSettings = { settingsExportLauncher.launch("autplay-settings.json") },
                    onImportSettings = { settingsImportLauncher.launch(arrayOf("application/json", "text/json")) },
                    onNavigate = { destination = it },
                )
            }
            stableError?.let { Text(it) }
        }
    }
}

@Composable
internal fun TrackPreferenceActions(
    enabled: Boolean,
    onLike: () -> Unit,
    onDislike: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Button(enabled = enabled, onClick = onLike) { Text("Like current track") }
        OutlinedButton(enabled = enabled, onClick = onDislike) { Text("Dislike current track") }
    }
}

@Composable
private fun PlaybackMiniBar(
    playbackState: PlaybackUiState,
    onOpen: () -> Unit,
    onPlayPause: () -> Unit,
) {
    Surface(tonalElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedButton(onClick = onOpen) {
                Text((playbackState.title ?: "Nothing playing").take(28))
            }
            Button(onClick = onPlayPause, enabled = playbackState.title != null) {
                Text(if (playbackState.isPlaying) "Pause" else "Play")
            }
        }
    }
}

@Composable
private fun WaveFrontendScreen(
    coordinator: WaveCoordinator?,
    isProfileBound: Boolean,
    localTrackRefId: String?,
    resolveServerRecordingId: suspend (String) -> String?,
    onError: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val state by (coordinator?.uiState ?: flowOf(app.autplay.application.wave.WaveUiState()))
        .collectAsState(initial = app.autplay.application.wave.WaveUiState())
    var roomCode by remember { mutableStateOf("") }
    var createdCode by remember { mutableStateOf<String?>(null) }
    var inviteUserIds by remember { mutableStateOf("") }
    var waveActionMessage by remember { mutableStateOf<String?>(null) }
    val inviteList = inviteUserIds.split(Regex("[,;\\s]+"))
        .map(String::trim)
        .filter(String::isNotEmpty)
    val inviteIsValid = inviteList.size <= 7 && inviteList.distinct().size == inviteList.size &&
        inviteList.all { value -> runCatching { java.util.UUID.fromString(value) }.isSuccess }

    Text("Listen together with up to eight authenticated devices.")
    if (!isProfileBound || coordinator == null) {
        Text("Wave needs an active personal-server profile. Local playback stays available.")
        return
    }
    if (state.roomId == null) {
        OutlinedTextField(
            value = inviteUserIds,
            onValueChange = { inviteUserIds = it.take(300) },
            label = { Text("Invite user UUIDs (up to 7)") },
            isError = !inviteIsValid,
            minLines = 2,
        )
        Button(enabled = inviteIsValid, onClick = {
            scope.launch {
                runCatching { coordinator.create(inviteList) }
                    .onSuccess { createdCode = it }
                    .onFailure { onError("WAVE_CREATE_UNAVAILABLE") }
            }
        }) { Text("Create listening room") }
        OutlinedTextField(
            value = roomCode,
            onValueChange = { roomCode = it.uppercase().filter(Char::isLetterOrDigit).take(10) },
            label = { Text("10-character room code") },
        )
        Button(
            enabled = roomCode.length == 10,
            onClick = {
                scope.launch {
                    runCatching { coordinator.joinByCode(roomCode) }
                        .onFailure { onError("WAVE_JOIN_UNAVAILABLE") }
                }
            },
        ) { Text("Join room") }
    } else {
        createdCode?.let { code ->
            Text("Room code: $code")
            OutlinedButton(onClick = {
                val share = Intent(Intent.ACTION_SEND)
                    .setType("text/plain")
                    .putExtra(Intent.EXTRA_TEXT, "AutPlay Wave room: $code")
                context.startActivity(Intent.createChooser(share, "Share Wave room"))
            }) { Text("Share room code") }
        }
        Text("State: ${state.state}")
        Text(if (state.isHost) "You control this room" else "The host controls playback")
        state.message?.let { Text(it) }
        if (state.isHost) {
            Column {
                Button(
                    enabled = localTrackRefId != null,
                    onClick = {
                        val trackRefId = localTrackRefId ?: return@Button
                        scope.launch {
                            val recordingId = runCatching { resolveServerRecordingId(trackRefId) }.getOrNull()
                            if (recordingId == null) {
                                onError("WAVE_RECORDING_NOT_SYNCED")
                            } else {
                                runCatching { coordinator.enqueueRecording(recordingId) }
                                    .onFailure { onError("WAVE_QUEUE_UNAVAILABLE") }
                            }
                        }
                    },
                ) { Text("Add first library track to room") }
                Button(onClick = {
                    scope.launch {
                        runCatching { coordinator.startFirstQueued() }
                            .onSuccess { started ->
                                waveActionMessage = if (started) {
                                    "Synchronized start scheduled."
                                } else {
                                    "Start gate is waiting for every present device to become ready."
                                }
                            }
                            .onFailure { onError("WAVE_START_UNAVAILABLE") }
                    }
                }) { Text("Start synchronized playback") }
                Button(onClick = {
                    scope.launch {
                        runCatching { coordinator.pauseRoom() }
                            .onFailure { onError("WAVE_PAUSE_UNAVAILABLE") }
                    }
                }) { Text("Pause room") }
            }
            waveActionMessage?.let { Text(it) }
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { coordinator.closeRoom() }
                        .onFailure { onError("WAVE_CLOSE_UNAVAILABLE") }
                }
            }) { Text("Close room") }
        } else {
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { coordinator.leave() }
                        .onFailure { onError("WAVE_LEAVE_UNAVAILABLE") }
                }
            }) { Text("Leave room") }
        }
    }
}

@Composable
private fun ProfileFrontendScreen(
    settings: NonSecretSettings,
    onOpenSettings: () -> Unit,
    onOpenSync: () -> Unit,
    onLogout: () -> Unit,
    onLogoutAll: () -> Unit,
    onRevokeCurrentDevice: () -> Unit,
    onDisconnectLocally: () -> Unit,
) {
    var capabilityMessage by remember { mutableStateOf<String?>(null) }
    Text(if (settings.activeUserId == null) "Standalone profile" else "Personal server profile")
    Text(if (settings.activeUserId == null) "No account is required for the local library." else "Authenticated device profile is active.")
    Button(onClick = onOpenSettings) { Text("Profile and app settings") }
    Button(onClick = onOpenSync, enabled = settings.activeUserId != null) { Text("Sync status") }
    OutlinedButton(onClick = {
        capabilityMessage = "Password change is not exposed by the current authenticated server contract."
    }) { Text("Change password") }
    Text("The server has no device-list endpoint; only this known device can be revoked safely.")
    OutlinedButton(enabled = settings.activeUserId != null, onClick = onLogout) { Text("Log out this session") }
    OutlinedButton(enabled = settings.activeUserId != null, onClick = onLogoutAll) { Text("Log out all devices") }
    OutlinedButton(enabled = settings.deviceId != null, onClick = onRevokeCurrentDevice) { Text("Revoke this device") }
    OutlinedButton(enabled = settings.activeServerProfileId != null, onClick = onDisconnectLocally) {
        Text("Disconnect locally now")
    }
    Text("Local disconnect does not claim that the server session was revoked.")
    capabilityMessage?.let { Text(it) }
}

@Composable
private fun SettingsFrontendScreen(
    settings: NonSecretSettings,
    onUpdate: ((NonSecretSettings) -> NonSecretSettings) -> Unit,
    onChooseLibraryRoot: () -> Unit,
    onRescanLibraryRoot: () -> Unit,
    onExportSettings: () -> Unit,
    onImportSettings: () -> Unit,
    onNavigate: (UiDestination) -> Unit,
) {
    var apiOrigin by remember(settings.serverBaseUrl) { mutableStateOf(settings.serverBaseUrl.orEmpty()) }
    var streamOrigin by remember(settings.streamBaseUrl) { mutableStateOf(settings.streamBaseUrl.orEmpty()) }
    Text("Appearance", style = MaterialTheme.typography.titleMedium)
    listOf("SYSTEM", "LIGHT", "DARK").forEach { mode ->
        OutlinedButton(onClick = { onUpdate { current -> current.copy(appearanceMode = mode) } }) {
            Text(if (settings.appearanceMode == mode) "✓ $mode" else mode)
        }
    }
    listOf("CORAL", "VIOLET", "GREEN", "BLUE").forEach { palette ->
        OutlinedButton(onClick = { onUpdate { current -> current.copy(accentPalette = palette) } }) {
            Text(if (settings.accentPalette == palette) "✓ $palette" else palette)
        }
    }

    Text("Library access", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
    Text(if (settings.libraryRootTreeUri == null) "No root folder selected" else "Scoped music folder selected")
    Button(onClick = onChooseLibraryRoot) { Text("Choose music folder") }
    OutlinedButton(
        onClick = onRescanLibraryRoot,
        enabled = settings.libraryRootTreeUri != null,
    ) { Text("Scan selected folder") }
    Text("Android stores a revocable SAF permission, never a raw filesystem path.")

    Text("Network and Wave", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
    OutlinedTextField(
        value = apiOrigin,
        onValueChange = { apiOrigin = it.take(2_048) },
        label = { Text("API service origin") },
        placeholder = { Text("https://server.example") },
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = streamOrigin,
        onValueChange = { streamOrigin = it.take(2_048) },
        label = { Text("Stream service origin") },
        placeholder = { Text("https://stream.example") },
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedButton(
        enabled = apiOrigin.isNotBlank() && streamOrigin.isNotBlank(),
        onClick = {
            onUpdate { current ->
                current.copy(
                    serverBaseUrl = apiOrigin.trim().trimEnd('/'),
                    streamBaseUrl = streamOrigin.trim().trimEnd('/'),
                )
            }
        },
    ) { Text("Save service origins") }
    Text("Addresses are device-local and excluded from settings export.")
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text("Allow sync on metered network")
        Switch(
            checked = settings.syncOnMeteredNetwork,
            onCheckedChange = { enabled -> onUpdate { current -> current.copy(syncOnMeteredNetwork = enabled) } },
        )
    }
    listOf("OFF", "NEXT", "NEXT_3", "AGGRESSIVE_WIFI").forEach { mode ->
        OutlinedButton(onClick = { onUpdate { current -> current.copy(wavePrefetchMode = mode) } }) {
            Text(if (settings.wavePrefetchMode == mode) "✓ Wave prefetch $mode" else "Wave prefetch $mode")
        }
    }

    Text("Import and export", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
    Button(onClick = onExportSettings) { Text("Export settings") }
    OutlinedButton(onClick = onImportSettings) { Text("Import settings") }
    Text("Credentials, server addresses and device bindings are never exported.")

    Text("Features", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 16.dp))
    listOf(
        UiDestination.Playlists,
        UiDestination.Downloads,
        UiDestination.History,
        UiDestination.ImportReview,
        UiDestination.SyncStatus,
        UiDestination.ServerFeatures,
        UiDestination.PrivacyAndData,
    ).forEach { target ->
        Button(onClick = { onNavigate(target) }) { Text(target.label) }
    }
}

private fun legacyView(destination: UiDestination): String = when (destination) {
    UiDestination.Home -> "Home"
    UiDestination.Library -> "Library"
    UiDestination.Search -> "Search"
    UiDestination.Playlists -> "Playlists"
    UiDestination.History -> "History"
    UiDestination.Downloads -> "Downloads"
    UiDestination.ImportReview -> "Import"
        UiDestination.NowPlaying -> "Player"
    UiDestination.WaveRooms -> "Wave"
    UiDestination.SyncStatus -> "Sync"
    UiDestination.ServerFeatures -> "Server"
    UiDestination.Profile -> "Profile"
    UiDestination.Settings, UiDestination.PrivacyAndData -> "Settings"
}

private fun appearanceFrom(settings: NonSecretSettings): AutPlayAppearance = AutPlayAppearance(
    mode = runCatching { AutPlayThemeMode.valueOf(settings.appearanceMode.lowercase().replaceFirstChar(Char::uppercase)) }
        .getOrDefault(AutPlayThemeMode.System),
    accent = runCatching { AutPlayAccent.valueOf(settings.accentPalette.lowercase().replaceFirstChar(Char::uppercase)) }
        .getOrDefault(AutPlayAccent.Coral),
)

internal suspend fun updateFrontendSettings(
    settingsStore: NonSecretSettingsStore,
    transform: (NonSecretSettings) -> NonSecretSettings,
): String? = runCatching { settingsStore.mutate(transform) }
    .exceptionOrNull()
    ?.let { "SETTINGS_UPDATE_UNAVAILABLE" }

private fun readBoundedSettingsBytes(input: InputStream): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(8 * 1024)
    while (true) {
        val read = input.read(buffer)
        if (read < 0) break
        require(output.size() + read <= 64 * 1024) { "SETTINGS_IMPORT_TOO_LARGE" }
        output.write(buffer, 0, read)
    }
    return output.toByteArray()
}

private fun readBoundedServerImportBytes(input: InputStream): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(8_192)
    while (true) {
        val count = input.read(buffer)
        if (count < 0) break
        if (output.size() + count > 2 * 1_024 * 1_024) error("SERVER_IMPORT_TOO_LARGE")
        output.write(buffer, 0, count)
    }
    return output.toByteArray().also { require(it.isNotEmpty()) { "SERVER_IMPORT_EMPTY" } }
}

private fun serverImportFormat(context: android.content.Context, uri: Uri): String {
    val mime = context.contentResolver.getType(uri)?.lowercase()
    val name = context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
        ?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0)?.lowercase() else null
        }
    return when {
        mime == "text/csv" || name?.endsWith(".csv") == true -> "CSV"
        mime == "text/html" || name?.endsWith(".html") == true || name?.endsWith(".htm") == true -> "HTML"
        mime == "application/json" || mime == "text/json" || name?.endsWith(".json") == true -> "JSON"
        else -> error("SERVER_IMPORT_FORMAT_UNSUPPORTED")
    }
}

private fun deactivateServerBinding(settings: NonSecretSettings): NonSecretSettings = settings.copy(
    activeServerProfileId = null,
    activeUserId = null,
    deviceId = null,
)

private data class VaultUploadCandidate(
    val localTrackRefId: String,
    val serverRecordingId: String,
    val localAudioStateId: String,
    val knownSha256: ByteArray?,
    val knownSize: Long?,
)

private data class LibraryRootImportOutcome(val importedCount: Int, val truncated: Boolean)

private suspend fun scanLibraryRoot(
    context: android.content.Context,
    treeUri: String,
    importProfileId: String,
    importRepository: LocalImportReviewRepository,
): LibraryRootImportOutcome = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
    val scan = ContentTreeAudioScanner(context.contentResolver).scan(treeUri, maxAudioFiles = 200)
    require(scan.documentUris.isNotEmpty()) { "LIBRARY_ROOT_EMPTY" }
    val inspector = ContentUriInspector(context.contentResolver)
    val inspections = scan.documentUris.map(inspector::inspect)
        .filter { it.status == app.autplay.application.importing.ContentUriStatus.AVAILABLE }
    require(inspections.isNotEmpty()) { "LIBRARY_ROOT_UNREADABLE" }
    val now = System.currentTimeMillis()
    val job = importRepository.createOrResume(
        treeUriImportCommand(importProfileId, treeUri, inspections, persistedPermission = true, nowMs = now),
    )
    importRepository.entriesOnce(job.importJobId).forEach { entry ->
        importRepository.recordShadowEvaluation(
            RecordShadowEvaluationCommand(
                importEntryId = entry.importEntryId,
                idempotencyKey = "document-tree-evidence-v1",
                resolverState = ImportResolverState.DEFERRED_EVIDENCE,
                evidenceMode = "AUDIO_AVAILABLE",
                matcherVersion = "android-local-shadow/1",
                explanationJson = "{\"schema_version\":1,\"reason_code\":\"FINGERPRINT_EVIDENCE_DEFERRED\"}",
                candidates = emptyList(),
                nowMs = now,
            ),
        )
    }
    LibraryRootImportOutcome(inspections.size, scan.truncated)
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
