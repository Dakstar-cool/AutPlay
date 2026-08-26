package app.autplay

import android.content.Context
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.importing.LocalImportReviewRepository
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.library.CoreHomePlaylistSummary
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.application.server.ServerFeatureStateRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.playback.presentation.PlaybackInteractionRouter
import app.autplay.playback.presentation.PlaybackPresentationAdapter
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.ui.AppLanguage
import app.autplay.ui.ServerFeaturesActions
import app.autplay.ui.ServerFeaturesUiState
import app.autplay.ui.UiDestination
import app.autplay.ui.profilepairing.ExistingLocalDataChoice
import app.autplay.ui.profilepairing.ProfilePairingActions
import app.autplay.ui.profilepairing.ProfileRemoteAction
import app.autplay.ui.social.SocialActions
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.profilepairing.RuntimeLifecycleAction
import app.autplay.ui.player.NowPlayingRouteActions
import app.autplay.ui.queue.QueueEditorUiActions
import androidx.media3.common.util.UnstableApi
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject
import app.autplay.work.RemoteImportWorkScheduler
import app.autplay.work.VaultUploadWorkScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

@UnstableApi
internal fun buildNowPlayingRouteActions(
    playbackActions: PlaybackInteractionRouter,
    playerAdapter: PlaybackPresentationAdapter,
    playbackOwner: PlaybackSessionOwner,
    currentTrackRefId: () -> String?,
    currentQueueEntryId: () -> String?,
    scope: CoroutineScope,
    sliceRepository: LibraryVerticalSliceRepository,
    binding: () -> ClientEventBinding?,
    reportError: (String) -> Unit,
    queueActions: QueueEditorUiActions,
): NowPlayingRouteActions = NowPlayingRouteActions(
    togglePlayPause = playbackActions::toggleDirectPlayPause,
    toggleShuffle = playbackActions::toggleDirectShuffle,
    cycleRepeat = playbackActions::cycleDirectRepeatMode,
    previous = {
        scope.launch {
            runCatching { playbackOwner.dispatch(PlaybackCommand.Previous) }
                .onFailure { reportError("QUEUE_NAVIGATION_UNAVAILABLE") }
        }
    },
    next = {
        scope.launch {
            runCatching { playbackOwner.dispatch(PlaybackCommand.Next) }
                .onFailure { reportError("QUEUE_NAVIGATION_UNAVAILABLE") }
        }
    },
    seekBegin = playerAdapter::beginSeek,
    seekUpdate = playerAdapter::updateSeek,
    seekCommit = playbackActions::commitDirectSeek,
    like = { recordPlaybackPreference(currentTrackRefId, scope, sliceRepository, binding, "LIKED", reportError) },
    dislike = { recordPlaybackPreference(currentTrackRefId, scope, sliceRepository, binding, "DISLIKED", reportError) },
    clearPreference = { recordPlaybackPreference(currentTrackRefId, scope, sliceRepository, binding, "NEUTRAL", reportError) },
    scheduleSleepTimer = { durationMs ->
        scope.launch {
            runCatching { playbackOwner.dispatch(PlaybackCommand.ScheduleSleepTimer(durationMs)) }
                .onFailure { reportError("SLEEP_TIMER_UNAVAILABLE") }
        }
    },
    stopAfterCurrentTrack = {
        val queueEntryId = currentQueueEntryId() ?: return@NowPlayingRouteActions
        scope.launch {
            runCatching { playbackOwner.dispatch(PlaybackCommand.StopAfterCurrentItem(LocalId(queueEntryId))) }
                .onFailure { reportError("SLEEP_TIMER_UNAVAILABLE") }
        }
    },
    cancelSleepTimer = {
        scope.launch {
            runCatching { playbackOwner.dispatch(PlaybackCommand.CancelSleepTimer) }
                .onFailure { reportError("SLEEP_TIMER_UNAVAILABLE") }
        }
    },
    observingChanged = { playerAdapter.setSurfaceObserving("full", it) },
    queue = queueActions,
)

private fun recordPlaybackPreference(
    currentTrackRefId: () -> String?,
    scope: CoroutineScope,
    repository: LibraryVerticalSliceRepository,
    binding: () -> ClientEventBinding?,
    preference: String,
    reportError: (String) -> Unit,
) {
    currentTrackRefId()?.let { trackRefId ->
        scope.launch {
            runCatching {
                repository.setPreference(
                    binding(),
                    LocalId(trackRefId),
                    LocalId.random(),
                    preference,
                    false,
                    null,
                    System.currentTimeMillis(),
                )
            }.onFailure { reportError("PREFERENCE_UNAVAILABLE") }
        }
    }
}

internal fun buildServerFeaturesActions(
    launchServerAction: (String, suspend (ServerFeatureRepository) -> Unit) -> Unit,
    binding: () -> ClientEventBinding?,
    state: () -> ServerFeaturesUiState,
    setState: (ServerFeaturesUiState) -> Unit,
    remoteImportJobId: () -> String?,
    setRemoteImportJobId: (String?) -> Unit,
    stateRepository: ServerFeatureStateRepository,
    context: Context,
    selectedUploadCandidate: () -> VaultUploadCandidate?,
    durableVaultUploads: () -> List<VaultUploadIntentEntity>,
    scope: CoroutineScope,
    chooseServerImport: () -> Unit,
): ServerFeaturesActions = ServerFeaturesActions(
    refreshHealth = {
        launchServerAction("SERVER_HEALTH") { server ->
            setState(state().copy(health = server.health()))
        }
    },
    refreshLibrary = {
        launchServerAction("SERVER_LIBRARY") { server ->
            setState(state().copy(library = server.librarySnapshot()))
        }
    },
    search = { query ->
        launchServerAction("SERVER_SEARCH") { server ->
            setState(state().copy(searchResults = server.searchLibrary(query)))
        }
    },
    chooseServerImport = chooseServerImport,
    refreshImport = {
        remoteImportJobId()?.let { jobId ->
            launchServerAction("SERVER_IMPORT_REFRESH") { server ->
                val report = server.importReport(jobId)
                stateRepository.recordImportReport(checkNotNull(binding()).serverProfileId, report, System.currentTimeMillis())
                setState(state().copy(importReport = report))
            }
        }
    },
    loadNextImport = {
        val current = state().importReport
        val cursor = current?.nextAfter
        if (current != null && cursor != null) {
            launchServerAction("SERVER_IMPORT_NEXT") { server ->
                val page = server.importReport(current.importJobId, cursor)
                setState(
                    state().copy(
                        importReport = page.copy(
                            entries = (current.entries + page.entries).distinctBy { it.importEntryId },
                        ),
                    ),
                )
            }
        }
    },
    cancelImport = {
        remoteImportJobId()?.let { jobId ->
            launchServerAction("SERVER_IMPORT_CANCEL") { server ->
                server.cancelImport(jobId)
                val report = server.importReport(jobId)
                stateRepository.recordImportReport(checkNotNull(binding()).serverProfileId, report, System.currentTimeMillis())
                setState(state().copy(importReport = report))
            }
        }
    },
    resumeImport = {
        remoteImportJobId()?.let { jobId ->
            launchServerAction("SERVER_IMPORT_RESUME") { server ->
                val activeBinding = checkNotNull(binding())
                val resumed = server.resumeImport(jobId)
                setRemoteImportJobId(resumed.importJobId)
                stateRepository.recordImportStart(activeBinding.serverProfileId, resumed, System.currentTimeMillis())
                val report = server.importReport(resumed.importJobId)
                stateRepository.recordImportReport(activeBinding.serverProfileId, report, System.currentTimeMillis())
                RemoteImportWorkScheduler.enqueue(context, resumed.importJobId)
                setState(state().copy(importReport = report))
            }
        }
    },
    reviewImport = { entry, action ->
        remoteImportJobId()?.let { jobId ->
            launchServerAction("SERVER_IMPORT_REVIEW") { server ->
                server.reviewImport(jobId, entry, action)
                val report = server.importReport(jobId)
                stateRepository.recordImportReport(checkNotNull(binding()).serverProfileId, report, System.currentTimeMillis())
                setState(state().copy(importReport = report))
            }
        }
    },
    uploadSelectedTrack = {
        val candidate = selectedUploadCandidate()
        val activeBinding = binding()
        if (candidate != null && activeBinding != null) scope.launch {
            val intent = stateRepository.enqueueVaultUpload(
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
    cancelUpload = {
        durableVaultUploads().firstOrNull {
            it.state !in setOf(
                "COMMITTED", "REUSED", "QUARANTINED", "FAILED", "CANCELLED", "EXPIRED",
                "INGEST_POLLING_PAUSED",
            )
        }?.let { upload ->
            scope.launch {
                stateRepository.cancelVaultUpload(upload.uploadIntentId, System.currentTimeMillis())
                VaultUploadWorkScheduler.enqueue(context, upload.uploadIntentId)
            }
        }
    },
    recommendations = { home ->
        launchServerAction("SERVER_RECOMMENDATIONS") { server ->
            val result = if (home) server.homeRecommendations() else server.recommendations()
            stateRepository.recordRecommendation(
                checkNotNull(binding()).serverProfileId,
                result,
                System.currentTimeMillis(),
            )
            setState(state().copy(recommendation = result))
        }
    },
    exactReplay = {
        state().recommendation?.requestId?.let { requestId ->
            launchServerAction("SERVER_REPLAY_EXACT") { server ->
                val result = server.exactRecommendationReplay(requestId)
                stateRepository.recordRecommendation(checkNotNull(binding()).serverProfileId, result, System.currentTimeMillis())
                setState(state().copy(recommendation = result))
            }
        }
    },
    algorithmicReplay = {
        state().recommendation?.requestId?.let { requestId ->
            launchServerAction("SERVER_REPLAY_ALGORITHMIC") { server ->
                val result = server.algorithmicRecommendationReplay(requestId)
                stateRepository.recordRecommendation(checkNotNull(binding()).serverProfileId, result, System.currentTimeMillis())
                setState(state().copy(recommendation = result))
            }
        }
    },
)

internal fun buildManualPlaylistActions(
    scope: CoroutineScope,
    binding: () -> ClientEventBinding?,
    sliceRepository: LibraryVerticalSliceRepository,
    reportError: (String) -> Unit,
    onChanged: () -> Unit = {},
    playEntry: (String) -> Unit = {},
): ManualPlaylistActions = ManualPlaylistActions(
    create = { name, description ->
        scope.launch {
            runCatching {
                sliceRepository.createPlaylist(
                    binding(), LocalId.random(), LocalId.random(), name, description, System.currentTimeMillis(),
                )
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_CREATE_UNAVAILABLE") }
        }
    },
    rename = { playlistId, name, description ->
        scope.launch {
            runCatching {
                sliceRepository.updatePlaylistMetadata(
                    binding(), LocalId(playlistId), LocalId.random(), name, description, System.currentTimeMillis(),
                )
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_UPDATE_UNAVAILABLE") }
        }
    },
    delete = { playlistId ->
        scope.launch {
            runCatching {
                sliceRepository.deletePlaylist(binding(), LocalId(playlistId), LocalId.random(), System.currentTimeMillis())
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_DELETE_UNAVAILABLE") }
        }
    },
    addTrack = { playlistId, trackRefId ->
        scope.launch {
            runCatching {
                sliceRepository.addPlaylistEntry(
                    binding(), LocalId(playlistId), LocalId.random(), LocalId(trackRefId), LocalId.random(), null, null,
                    System.currentTimeMillis(),
                )
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_ENTRY_UNAVAILABLE") }
        }
    },
    removeEntry = { entryId ->
        scope.launch {
            runCatching {
                sliceRepository.removePlaylistEntry(binding(), LocalId(entryId), LocalId.random(), System.currentTimeMillis())
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_ENTRY_UNAVAILABLE") }
        }
    },
    moveEntryBefore = { entryId, beforeEntryId ->
        scope.launch {
            runCatching {
                sliceRepository.reorderPlaylistEntry(
                    binding(), LocalId(entryId), beforeEntryId?.let(::LocalId), LocalId.random(), System.currentTimeMillis(),
                )
            }.onSuccess { onChanged() }.onFailure { reportError("PLAYLIST_ENTRY_UNAVAILABLE") }
        }
    },
    playEntry = playEntry,
)

@UnstableApi
internal fun buildLegacySecondaryRouteActions(
    scope: CoroutineScope,
    binding: () -> ClientEventBinding?,
    playlists: () -> List<CoreHomePlaylistSummary>,
    libraryEntries: () -> List<CoreLibraryEntrySummary>,
    selectedTrackRefId: () -> String?,
    sliceRepository: LibraryVerticalSliceRepository,
    downloadRepository: DownloadIntentRepository,
    syncScheduler: DeferredWorkScheduler,
    resolveServerRecordingId: suspend (String) -> String?,
    playbackActions: PlaybackInteractionRouter,
    reportError: (String) -> Unit,
    serverFeaturesActions: ServerFeaturesActions,
    navigate: (UiDestination) -> Unit,
    launchServerAction: (String, suspend (ServerFeatureRepository) -> Unit) -> Unit,
    settings: NonSecretSettings,
    settingsStore: NonSecretSettingsStore,
    context: Context,
    profilePairingRuntime: ProfilePairingRuntime,
    admissionRuntime: app.autplay.application.profilepairing.AdmissionRuntime,
    admissionSnapshot: app.autplay.application.profilepairing.PairingFlowSnapshot?,
    importProfileId: String,
    importRepository: LocalImportReviewRepository,
    importActions: app.autplay.ui.LegacyImportRouteActions,
    chooseLibraryRoot: () -> Unit,
    exportSettings: () -> Unit,
    importSettings: () -> Unit,
    social: SocialActions,
    manualPlaylists: ManualPlaylistActions,
    openPlaylist: (String) -> Unit,
): LegacySecondaryRouteActions = LegacySecondaryRouteActions(
    manualPlaylists = manualPlaylists,
    openPlaylist = openPlaylist,
    importActions = importActions,
    downloadSelectedTrack = {
        val entry = libraryEntries().firstOrNull { it.localUserTrackRefId == selectedTrackRefId() }
        val activeBinding = binding()
        if (entry != null && activeBinding != null) scope.launch {
            runCatching {
                downloadRepository.requestPreferredVaultDownload(
                    LocalId(entry.localUserTrackRefId),
                    activeBinding.serverProfileId,
                    DownloadStorageClass.USER_DOWNLOAD,
                    System.currentTimeMillis(),
                )
            }.onFailure { reportError("DOWNLOAD_UNAVAILABLE") }
        }
    },
    retrySync = {
        binding()?.let { activeBinding ->
            syncScheduler.enqueue(
                DeferredWorkRequest(
                    DeferredWorkKind.SYNC,
                    DeferredWorkSubject.Device(activeBinding.deviceId),
                    activeBinding.serverProfileId,
                ),
            )
        }
    },
    resolveServerRecordingId = resolveServerRecordingId,
    startWavePlayback = playbackActions::startWavePlayback,
    pauseWavePlayback = playbackActions::pauseWavePlayback,
    reportError = reportError,
    serverFeatures = serverFeaturesActions,
    openSettings = { navigate(UiDestination.Settings) },
    openSync = { navigate(UiDestination.SyncStatus) },
    logout = {
        launchServerAction("SERVER_LOGOUT") { server ->
            server.logout()
            settingsStore.mutate(::deactivateServerBinding)
        }
    },
    logoutAll = {
        launchServerAction("SERVER_LOGOUT_ALL") { server ->
            server.logoutAll()
            settingsStore.mutate(::deactivateServerBinding)
        }
    },
    revokeCurrentDevice = {
        settings.deviceId?.value?.let { deviceId ->
            launchServerAction("SERVER_DEVICE_REVOKE") { server ->
                server.revokeDevice(deviceId)
                settingsStore.mutate(::deactivateServerBinding)
            }
        }
    },
    disconnectLocally = {
        val profileId = settings.activeServerProfileId
        if (profileId != null) scope.launch {
            AndroidKeystoreCredentialStore(context.applicationContext).clear(profileId)
            settingsStore.mutate(::deactivateServerBinding)
        }
    },
    profilePairing = ProfilePairingActions(
        startDiscovery = profilePairingRuntime::startDiscovery,
        confirmTrust = profilePairingRuntime::confirmTrust,
        cancelPairing = {
            admissionRuntime.cancel()
            profilePairingRuntime.cancel()
        },
        exchangeInvitation = profilePairingRuntime::exchangeInvitation,
        chooseLocalData = { choice ->
            when (choice) {
                ExistingLocalDataChoice.KEEP_ON_PHONE -> profilePairingRuntime.chooseLocalData(false)
                ExistingLocalDataChoice.REVIEW_AND_CONNECT -> profilePairingRuntime.chooseLocalData(true)
                ExistingLocalDataChoice.CANCEL -> profilePairingRuntime.cancelFirstBinding()
            }
        },
        reviewLocalData = profilePairingRuntime::reviewLocalData,
        cancelLocalDataReview = profilePairingRuntime::cancelLocalDataReview,
        applyLocalDataSelection = profilePairingRuntime::applyLocalDataSelection,
        createInvitation = profilePairingRuntime::createInvitation,
        cancelCreatedInvitation = profilePairingRuntime::cancelCreatedInvitation,
        dismissCreatedInvitation = profilePairingRuntime::dismissCreatedInvitation,
        retry = profilePairingRuntime::retry,
        openSync = { navigate(UiDestination.SyncStatus) },
        performRemoteAction = { action -> profilePairingRuntime.performLifecycle(
            when (action) {
                ProfileRemoteAction.LOGOUT_CURRENT -> RuntimeLifecycleAction.LOGOUT_CURRENT
                ProfileRemoteAction.LOGOUT_ALL -> RuntimeLifecycleAction.LOGOUT_ALL
                ProfileRemoteAction.REVOKE_CURRENT_DEVICE -> RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE
                ProfileRemoteAction.DISCONNECT_LOCAL -> RuntimeLifecycleAction.DISCONNECT_LOCAL
            },
        ) },
        reenrollTrustedDevice = { admissionSnapshot?.let(admissionRuntime::reenrollTrusted) },
        admission = app.autplay.ui.profilepairing.AdmissionActions(
            request = { admissionSnapshot?.let(admissionRuntime::request) },
            confirmComparison = admissionRuntime::confirmComparison,
            poll = admissionRuntime::poll,
            confirmAccount = admissionRuntime::confirmAccount,
            cancel = {
                admissionRuntime.cancel()
                profilePairingRuntime.cancel()
            },
            retry = { admissionSnapshot?.let(admissionRuntime::retry) },
        ),
    ),
    updateSettings = { transform ->
        scope.launch {
            updateFrontendSettings(settingsStore, transform)?.let(reportError)
        }
    },
    changeAppLanguage = { language ->
        scope.launch {
            val error = updateFrontendSettings(settingsStore) { current ->
                current.copy(appLanguage = language.storedValue)
            }
            if (error == null) {
                synchronizeFrameworkAppLanguage(context, language)
            } else {
                reportError(error)
            }
        }
    },
    chooseLibraryRoot = chooseLibraryRoot,
    rescanLibraryRoot = {
        settings.libraryRootTreeUri?.let { treeUri ->
            scope.launch {
                runCatching {
                    scanLibraryRoot(context, treeUri, importProfileId, importRepository)
                }.onSuccess { outcome ->
                    navigate(UiDestination.ImportReview)
                    if (outcome.truncated) reportError("LIBRARY_ROOT_SCAN_LIMIT_REACHED")
                }.onFailure { reportError("LIBRARY_ROOT_SCAN_UNAVAILABLE") }
            }
        }
    },
    exportSettings = exportSettings,
    importSettings = importSettings,
    social = social,
    navigate = navigate,
)
