package app.autplay

import android.content.Context
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CoreProductRepository
import app.autplay.application.library.CoreTrackDetail
import app.autplay.application.library.CoreLibraryEntrySummary
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.recommendation.HomeFeed
import app.autplay.application.recommendation.HomeRecommendationItem
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.RecommendationPresentationResult
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.application.search.LocalTrackSearchResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.ui.core.CoreProductUiState
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.SearchGenerationGuard
import app.autplay.ui.core.SearchResultStore
import app.autplay.ui.core.SearchScope
import app.autplay.ui.core.SingleFlightActionGate
import androidx.media3.common.util.UnstableApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

internal data class CoreCommandActions(
    val recordRecommendationVisibility: (String) -> Unit,
    val recordHomeFeedback: (String, String) -> Unit,
    val startTrack: (String, String, String, String?) -> Unit,
    val startSearchTrack: (String) -> Unit,
    val startPlaylistEntry: (String) -> Unit,
    val updateLibraryMembership: (String) -> Unit,
    val likeTrack: (String) -> Unit,
    val downloadTrack: (String) -> Unit,
)

@UnstableApi
internal fun buildCoreCommandActions(
    scope: CoroutineScope,
    binding: () -> ClientEventBinding?,
    activeHomeFeed: () -> HomeFeed?,
    presentationResults: MutableMap<String, RecommendationPresentationResult>,
    actionGate: SingleFlightActionGate,
    recommendationRepository: OfflineRecommendationRepository,
    sliceRepository: LibraryVerticalSliceRepository,
    setHomeFeed: (HomeFeed?) -> Unit,
    reportError: (String) -> Unit,
    playbackRepository: PlaybackPersistenceRepository,
    playbackOwner: PlaybackSessionOwner,
    searchResultStore: SearchResultStore<LocalTrackSearchResult>,
    searchSessionId: () -> String?,
    setSearchSessionId: (String) -> Unit,
    playlistDetail: () -> CorePlaylistDetail?,
    libraryEntries: () -> List<CoreLibraryEntrySummary>,
    setLibraryError: (Boolean) -> Unit,
    selectedDetail: () -> DetailTarget?,
    closeCoreDetail: () -> Unit,
    coreProductRepository: CoreProductRepository,
    setTrackDetail: (CoreTrackDetail?) -> Unit,
    bindingContextKey: () -> String,
    setLoadedDetailContextKey: (String) -> Unit,
    downloadRepository: DownloadIntentRepository,
): CoreCommandActions {
    fun recommendationFor(key: String): Pair<String, HomeRecommendationItem>? {
        val feed = activeHomeFeed() ?: return null
        val presentationId = feed.presentationId ?: return null
        val item = feed.recommendationSections.values.flatten().firstOrNull {
            recommendationKey(presentationId, it) == key
        } ?: return null
        return presentationId to item
    }

    return CoreCommandActions(
        recordRecommendationVisibility = visibility@{ key ->
            if (presentationResults.containsKey(key)) return@visibility
            val activeBinding = binding() ?: return@visibility
            val (presentationId, item) = recommendationFor(key) ?: return@visibility
            val actionKey = "impression:$key"
            if (!actionGate.begin(actionKey)) return@visibility
            scope.launch {
                try {
                    runCatching {
                        recommendationRepository.recordPresentation(
                            activeBinding,
                            LocalId(presentationId),
                            item,
                            System.currentTimeMillis(),
                        )
                    }.onSuccess { presentationResults[key] = it }
                        .onFailure { reportError("IMPRESSION_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        recordHomeFeedback = feedback@{ key, preference ->
            val activeBinding = binding() ?: return@feedback
            val (presentationId, item) = recommendationFor(key) ?: return@feedback
            val impression = presentationResults[key] ?: return@feedback
            val actionKey = "home-feedback:$key"
            if (!actionGate.begin(actionKey)) return@feedback
            scope.launch {
                try {
                    runCatching {
                        sliceRepository.setPreference(
                            activeBinding,
                            LocalId(item.localUserTrackRefId),
                            LocalId.random(),
                            preference,
                            false,
                            recommendationRepository.attributionJson(
                                LocalId(presentationId),
                                impression.impressionEventId,
                                item,
                            ),
                            System.currentTimeMillis(),
                        )
                        setHomeFeed(recommendationRepository.loadHomeFeed(activeBinding, System.currentTimeMillis()))
                    }.onFailure { reportError("PREFERENCE_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        startTrack = { trackRefId, sourceOrigin, queueType, sourceContextId ->
            val actionKey = "play:$sourceOrigin:$trackRefId"
            if (actionGate.begin(actionKey)) scope.launch {
                try {
                    runCatching {
                        val snapshotId = LocalId.random()
                        playbackRepository.activateQueue(
                            snapshotId = snapshotId,
                            entries = listOf(
                                NewPlaybackQueueEntry(
                                    queueEntryId = LocalId.random(),
                                    trackRefId = LocalId(trackRefId),
                                    sourceOrigin = sourceOrigin,
                                    sourceAudioPolicy = "LOCAL_THEN_VAULT",
                                ),
                            ),
                            queueType = queueType,
                            sourceContextId = sourceContextId,
                            serverProfileId = binding()?.serverProfileId?.value,
                            listeningContext = "GENERAL",
                            nowMs = System.currentTimeMillis(),
                        )
                        playbackOwner.dispatch(PlaybackCommand.StartQueue(snapshotId))
                    }.onFailure { reportError("PLAYBACK_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        startSearchTrack = searchPlay@{ trackRefId ->
            val orderedResults = searchResultStore.results
            if (orderedResults.none { it.localUserTrackRefId == trackRefId }) return@searchPlay
            val sessionId = searchSessionId() ?: LocalId.random().value.also(setSearchSessionId)
            val actionKey = "search-play:$sessionId:$trackRefId"
            if (!actionGate.begin(actionKey)) return@searchPlay
            val entries = orderedResults.map { result ->
                result.localUserTrackRefId to NewPlaybackQueueEntry(
                    queueEntryId = LocalId.random(),
                    trackRefId = LocalId(result.localUserTrackRefId),
                    sourceOrigin = "SEARCH",
                    sourceAudioPolicy = "LOCAL_THEN_VAULT",
                )
            }
            val startEntryId = entries.first { it.first == trackRefId }.second.queueEntryId
            scope.launch {
                try {
                    runCatching {
                        val snapshotId = LocalId.random()
                        playbackRepository.activateQueue(
                            snapshotId = snapshotId,
                            entries = entries.map { it.second },
                            queueType = "SEARCH",
                            sourceContextId = sessionId,
                            serverProfileId = binding()?.serverProfileId?.value,
                            listeningContext = "GENERAL",
                            nowMs = System.currentTimeMillis(),
                            startEntryId = startEntryId,
                        )
                        playbackOwner.dispatch(PlaybackCommand.StartQueue(snapshotId))
                    }.onFailure { reportError("PLAYBACK_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        startPlaylistEntry = playlistPlay@{ localPlaylistEntryId ->
            val detail = playlistDetail() ?: return@playlistPlay
            val actionKey = "playlist:${detail.localPlaylistId}:$localPlaylistEntryId"
            if (!actionGate.begin(actionKey)) return@playlistPlay
            val generatedEntries = detail.entries.map { entry ->
                entry.localPlaylistEntryId to NewPlaybackQueueEntry(
                    queueEntryId = LocalId.random(),
                    trackRefId = LocalId(entry.localUserTrackRefId),
                    sourceOrigin = "PLAYLIST",
                    sourceAudioPolicy = "LOCAL_THEN_VAULT",
                )
            }
            val startEntryId = generatedEntries.firstOrNull {
                it.first == localPlaylistEntryId
            }?.second?.queueEntryId
            if (startEntryId == null) {
                actionGate.complete(actionKey)
                return@playlistPlay
            }
            scope.launch {
                try {
                    runCatching {
                        val snapshotId = LocalId.random()
                        playbackRepository.activateQueue(
                            snapshotId = snapshotId,
                            entries = generatedEntries.map { it.second },
                            queueType = "PLAYLIST",
                            sourceContextId = detail.localPlaylistId,
                            serverProfileId = binding()?.serverProfileId?.value,
                            listeningContext = "GENERAL",
                            nowMs = System.currentTimeMillis(),
                            startEntryId = startEntryId,
                        )
                        playbackOwner.dispatch(PlaybackCommand.StartQueue(snapshotId))
                    }.onFailure { reportError("PLAYBACK_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        updateLibraryMembership = membership@{ trackRefId ->
            val entry = libraryEntries().firstOrNull {
                it.localUserTrackRefId == trackRefId
            } ?: return@membership
            val actionKey = "library:$trackRefId"
            if (!actionGate.begin(actionKey)) return@membership
            scope.launch {
                try {
                    runCatching {
                        if (!entry.removed) {
                            sliceRepository.removeLibrary(
                                binding(),
                                LocalId(entry.localLibraryEntryId),
                                LocalId.random(),
                                System.currentTimeMillis(),
                            )
                        } else {
                            sliceRepository.restoreLibrary(
                                binding(),
                                LocalId(entry.localLibraryEntryId),
                                LocalId.random(),
                                System.currentTimeMillis(),
                            )
                        }
                    }.onSuccess {
                        setLibraryError(false)
                        if (selectedDetail() == DetailTarget(DetailKind.Track, trackRefId)) closeCoreDetail()
                    }.onFailure {
                        setLibraryError(true)
                        reportError("LIBRARY_UPDATE_UNAVAILABLE")
                    }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        likeTrack = { trackRefId ->
            val actionKey = "like:$trackRefId"
            if (actionGate.begin(actionKey)) scope.launch {
                try {
                    val result = runCatching {
                        sliceRepository.setPreference(
                            binding(),
                            LocalId(trackRefId),
                            LocalId.random(),
                            "LIKED",
                            false,
                            null,
                            System.currentTimeMillis(),
                        )
                    }
                    if (result.isSuccess) {
                        setLibraryError(false)
                        if (selectedDetail() == DetailTarget(DetailKind.Track, trackRefId)) {
                            setTrackDetail(coreProductRepository.trackDetail(trackRefId, binding()?.serverProfileId?.value))
                            setLoadedDetailContextKey(bindingContextKey())
                        }
                    } else {
                        setLibraryError(true)
                        reportError("PREFERENCE_UNAVAILABLE")
                    }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
        downloadTrack = download@{ trackRefId ->
            val activeBinding = binding() ?: return@download
            val actionKey = "download:$trackRefId"
            if (!actionGate.begin(actionKey)) return@download
            scope.launch {
                try {
                    runCatching {
                        downloadRepository.requestPreferredVaultDownload(
                            trackRefId = LocalId(trackRefId),
                            profileId = activeBinding.serverProfileId,
                            storageClass = DownloadStorageClass.USER_DOWNLOAD,
                            nowMs = System.currentTimeMillis(),
                        )
                    }.onFailure { reportError("DOWNLOAD_UNAVAILABLE") }
                } finally {
                    actionGate.complete(actionKey)
                }
            }
        },
    )
}

internal data class SearchActionState(
    val setCompleted: (Boolean) -> Unit,
    val setSessionId: (String?) -> Unit,
    val setResults: (List<LocalTrackSearchResult>) -> Unit,
    val setLoading: (Boolean) -> Unit,
    val setError: (Boolean) -> Unit,
    val setVaultLoading: (Boolean) -> Unit,
    val setVaultError: (Boolean) -> Unit,
    val setVaultResultCount: (Int?) -> Unit,
)

internal data class SearchCommandActions(
    val submit: () -> Unit,
    val reset: () -> Unit,
    val changeQuery: (String) -> Unit,
    val changeVaultScope: (Boolean) -> Unit,
)

internal fun buildSearchCommandActions(
    scope: CoroutineScope,
    context: Context,
    binding: () -> ClientEventBinding?,
    coreState: CoreProductUiState,
    generation: SearchGenerationGuard,
    resultStore: SearchResultStore<LocalTrackSearchResult>,
    searchRepository: LocalTrackSearchRepository,
    state: SearchActionState,
    reportError: (String) -> Unit,
): SearchCommandActions {
    fun reset() {
        generation.invalidate()
        resultStore.invalidate()
        state.setResults(emptyList())
        state.setCompleted(false)
        state.setSessionId(null)
        state.setLoading(false)
        state.setError(false)
        state.setVaultLoading(false)
        state.setVaultError(false)
        state.setVaultResultCount(null)
    }

    fun submit() {
        if (coreState.query.isBlank()) return
        val activeBinding = binding()
        val activeScopes = coreState.scopes + SearchScope.Local
        val request = generation.begin(coreState.query, activeScopes, activeBinding?.serverProfileId?.value)
        state.setSessionId(LocalId.random().value)
        resultStore.start(request)
        state.setResults(emptyList())
        state.setLoading(true)
        state.setError(false)
        state.setVaultLoading(SearchScope.Vault in activeScopes && activeBinding != null)
        state.setVaultError(false)
        state.setVaultResultCount(null)
        scope.launch {
            runCatching { searchRepository.search(request.normalizedQuery, activeBinding?.serverProfileId?.value) }
                .onSuccess {
                    if (generation.accepts(request) && resultStore.accept(request, it)) {
                        state.setResults(resultStore.results)
                        state.setCompleted(true)
                        state.setLoading(false)
                        state.setError(false)
                    }
                }
                .onFailure {
                    if (generation.accepts(request)) {
                        state.setCompleted(true)
                        state.setLoading(false)
                        state.setError(true)
                        reportError("SEARCH_UNAVAILABLE")
                    }
                }
        }
        if (SearchScope.Vault in activeScopes && activeBinding != null) {
            scope.launch {
                runCatching {
                    AutPlayRuntime.serverFeatures(context, activeBinding).searchLibrary(request.normalizedQuery)
                }.onSuccess { rows ->
                    if (generation.accepts(request)) {
                        state.setVaultResultCount(rows.size)
                        state.setVaultLoading(false)
                    }
                }.onFailure {
                    if (generation.accepts(request)) {
                        state.setVaultLoading(false)
                        state.setVaultError(true)
                    }
                }
            }
        }
    }

    return SearchCommandActions(
        submit = ::submit,
        reset = ::reset,
        changeQuery = { value ->
            coreState.query = value.take(200)
            reset()
        },
        changeVaultScope = { selected ->
            coreState.scopes = if (selected && binding() != null) {
                coreState.scopes + SearchScope.Vault + SearchScope.Local
            } else {
                setOf(SearchScope.Local)
            }
            reset()
        },
    )
}
