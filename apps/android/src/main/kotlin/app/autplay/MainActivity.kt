package app.autplay

import android.app.LocaleManager
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.LocaleList
import android.os.SystemClock
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.key
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onVisibilityChanged
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.util.UnstableApi
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.ManagedActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.work.WorkManager
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.library.CorePlaylistDetail
import app.autplay.application.library.CoreProductRepository
import app.autplay.application.library.CoreReleaseSummary
import app.autplay.application.library.CoreReleaseDetail
import app.autplay.application.library.CoreTrackDetail
import app.autplay.application.artist.RoomArtistCatalogPort
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.application.search.LocalTrackSearchResult
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
import app.autplay.application.playback.ActiveQueueContextRepository
import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.PlaybackPersistenceRepository
import app.autplay.application.playback.QueueEditFailure
import app.autplay.application.playback.QueueEditorRepository
import app.autplay.application.recommendation.HomeFeed
import app.autplay.application.recommendation.HomeRecommendationItem
import app.autplay.application.recommendation.OfflineRecommendationRepository
import app.autplay.application.recommendation.RecommendationPresentationResult
import app.autplay.application.server.ServerFeatureRepository
import app.autplay.application.server.ServerFeatureStateRepository
import app.autplay.application.wave.WaveCoordinator
import app.autplay.application.profilepairing.PairingFailure
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.profilepairing.OkHttpProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.social.ContactCard
import app.autplay.application.social.SocialRuntime
import app.autplay.application.social.SocialRuntimeState
import app.autplay.application.statistics.OwnerProfileStatistics
import app.autplay.application.statistics.ProfileStatisticsRepository
import app.autplay.data.local.RoomM5LocalIntentMaterializer
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RemoteImportJobProjectionEntity
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.CURRENT_ONBOARDING_REVISION
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackRuntimeState
import app.autplay.playback.ServicePlaybackSessionOwner
import app.autplay.playback.presentation.PlaybackInteractionRouter
import app.autplay.playback.presentation.PlaybackPresentationActionPort
import app.autplay.playback.presentation.PlaybackPresentationAdapter
import app.autplay.playback.presentation.WaveCoordinatorHostPlaybackCommandPort
import app.autplay.playback.presentation.WavePlaybackCommandOutcome
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.SyncWorker
import app.autplay.work.RecommendationPackWorkScheduler
import app.autplay.work.RemoteImportWorkScheduler
import app.autplay.work.WorkManagerDeferredWorkScheduler
import app.autplay.work.shouldScheduleRemoteImport
import app.autplay.ui.AutPlayAccent
import app.autplay.ui.AutPlayAppearance
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.AutPlayThemeMode
import app.autplay.ui.AppLanguage
import app.autplay.ui.UiDestination
import app.autplay.ui.WelcomeOnboardingScreen
import app.autplay.ui.player.PlaybackPreferenceUiState
import app.autplay.ui.playlist.ManualPlaylistUi
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiEntry
import app.autplay.ui.queue.QueueEditorUiState
import app.autplay.ui.settings.SettingsProductScreen
import app.autplay.ui.core.SearchGenerationGuard
import app.autplay.ui.core.SearchResultStore
import app.autplay.ui.core.SearchScope
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import app.autplay.ui.core.CoreTrackSummary
import app.autplay.ui.core.LibraryFilter
import app.autplay.ui.core.LibrarySection
import app.autplay.ui.core.TrackAvailability
import app.autplay.ui.core.buildLibraryTrackSummaries
import app.autplay.ui.core.buildHomeScreenUiState
import app.autplay.ui.core.buildLibraryScreenUiState
import app.autplay.ui.core.countHomeProblems
import app.autplay.ui.core.SingleFlightActionGate
import app.autplay.ui.core.rememberCoreProductUiState
import app.autplay.ui.core.CoreProductUiState
import app.autplay.ui.ServerFeaturesUiState
import app.autplay.ui.CoreTrackUiItem
import app.autplay.ui.ArtistBrowseUiState
import app.autplay.ui.CoreProductDetailUiState
import app.autplay.ui.CoreProductRouteActions
import app.autplay.ui.HomeRecommendationUiItem
import app.autplay.ui.HomeProblemUiItem
import app.autplay.ui.LegacyImportRouteActions
import app.autplay.ui.LegacyImportRouteState
import app.autplay.ui.profilepairing.ProfilePairingUiState
import app.autplay.ui.social.SocialActions
import app.autplay.ui.SearchScreenUiState
import app.autplay.ui.rememberAutPlayNavigationState
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.util.Locale
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlin.math.ceil
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import java.util.concurrent.ConcurrentHashMap

internal const val BOOTSTRAP_LABEL = "AutPlay"
internal const val ONBOARDING_REVISION = CURRENT_ONBOARDING_REVISION

internal suspend fun completeOnboarding(
    settingsStore: NonSecretSettingsStore,
): Boolean = runCatching {
    settingsStore.mutate {
        it.copy(onboardingRevision = ONBOARDING_REVISION)
    }
}.isSuccess

@UnstableApi
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val database = AutPlayRuntime.database(applicationContext)
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
    if (settingsStore == null || searchRepository == null || sliceRepository == null ||
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
        val loadedSettings by settingsStore.settings.collectAsState(initial = null as NonSecretSettings?)
        if (loadedSettings == null) {
            AutPlayTheme(appearanceFrom(NonSecretSettings())) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Box(contentAlignment = Alignment.Center) { Text(text = BOOTSTRAP_LABEL) }
                }
            }
            return
        }
        val settings = checkNotNull(loadedSettings)
        AppLanguageProvider(settings.appLanguage) {
            val binding = settings.activeUserId?.let { userId ->
                val deviceId = settings.deviceId ?: return@let null
                val profileId = settings.activeServerProfileId ?: return@let null
                ClientEventBinding(userId, deviceId, profileId)
            }
            val context = LocalContext.current
            val pairingScope = rememberCoroutineScope()
            var pairingSafeError by remember { mutableStateOf<String?>(null) }
            val pairingOrigins = remember { ConcurrentHashMap<String, String>() }
            val allowUnsafePairingHttp =
                (context.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
            val pairingRuntime = remember(context, settingsStore, sliceRepository, syncScheduler) {
                val credentialStore = AndroidKeystoreCredentialStore(context.applicationContext)
                val deviceKeys = AndroidM5DeviceKeyStore()
                val port = OkHttpProfilePairingPort(
                    originForProfile = { profile -> pairingOrigins[profile.value] },
                    credentials = credentialStore,
                    deviceKeys = deviceKeys,
                    allowUnsafeDevelopmentHttp = allowUnsafePairingHttp,
                )
                ProfilePairingRuntime(
                    scope = pairingScope,
                    settings = settingsStore,
                    credentials = credentialStore,
                    deviceKeys = deviceKeys,
                    port = port,
                    materialization = M5BindingMaterializationCoordinator(
                        settingsStore,
                        credentialStore,
                        deviceKeys,
                        RoomM5LocalIntentMaterializer(
                            AutPlayRuntime.database(context),
                            sliceRepository,
                            syncScheduler,
                        ),
                    ),
                    deviceName = android.os.Build.MODEL ?: "Android device",
                    reportSafeError = { pairingSafeError = it },
                    registerOrigin = { profile, origin -> pairingOrigins[profile.value] = origin },
                    allowUnsafeDevelopmentHttp = allowUnsafePairingHttp,
                )
            }
            val pairingRuntimeState by pairingRuntime.state.collectAsState()
            val admissionRuntime = remember(context, settingsStore, pairingOrigins) {
                app.autplay.application.profilepairing.AdmissionRuntime(
                    scope = pairingScope,
                    keys = AndroidM5DeviceKeyStore(),
                    port = app.autplay.application.profilepairing.OkHttpAdmissionPort(originForProfile = { profile: app.autplay.domain.ServerProfileId ->
                        pairingOrigins[profile.value] ?: settings.serverBaseUrl
                    }),
                    persistCheckpoint = { checkpoint -> settingsStore.mutate { current ->
                        current.copy(m5AdmissionCheckpoint = checkpoint?.let(app.autplay.application.profilepairing.AdmissionCheckpointCodec::encode))
                    } },
                    persistEnrollment = { checkpoint, account, bindingCommitId, session ->
                        pairingRuntime.completeAdmissionEnrollment(checkpoint, account, bindingCommitId, session)
                    },
                    persistTrustedReenrollment = { checkpoint, account, bindingCommitId, session ->
                        pairingRuntime.completeTrustedReenrollment(checkpoint, account, bindingCommitId, session)
                    },
                )
            }
            val admissionState by admissionRuntime.state.collectAsState()
            val admissionRecoveryBootstrap = remember(admissionRuntime) {
                app.autplay.application.profilepairing.AdmissionRecoveryBootstrap(
                    settings.m5AdmissionCheckpoint,
                    preferExistingBinding = settings.m5Binding != null,
                )
            }
            val onboardingComplete = settings.onboardingRevision >= ONBOARDING_REVISION
            LaunchedEffect(
                admissionRuntime,
                pairingRuntime,
                admissionRecoveryBootstrap,
                onboardingComplete,
            ) {
                if (!onboardingComplete) return@LaunchedEffect
                val restoredBinding = admissionRecoveryBootstrap.restoreExistingBindingIfPresent(
                    pairingRuntime,
                ) {
                    settingsStore.mutate { current ->
                        if (current.m5Binding == null) current else {
                            current.copy(m5AdmissionCheckpoint = null)
                        }
                    }
                }
                if (!restoredBinding && admissionRecoveryBootstrap.checkpoint == null) {
                    pairingRuntime.recoverAndRefresh()
                } else if (!restoredBinding) {
                    admissionRecoveryBootstrap.checkpoint?.let { checkpoint ->
                        if (pairingRuntime.recoverAdmissionTrust(checkpoint)) {
                            admissionRuntime.recover(checkpoint).join()
                        }
                    }
                }
            }
            var waveCoordinator by remember { mutableStateOf<WaveCoordinator?>(null) }
            LaunchedEffect(binding, onboardingComplete) {
                waveCoordinator?.close()
                waveCoordinator = if (binding == null || !onboardingComplete) null else runCatching {
                    AutPlayRuntime.waveCoordinator(context, binding)
                }.getOrNull()
            }
            DisposableEffect(Unit) {
                onDispose { waveCoordinator?.close() }
            }
            var onboardingCompletionRoute by rememberSaveable { mutableStateOf<String?>(null) }
            AutPlayTheme(appearanceFrom(settings)) {
                Surface(modifier = Modifier.fillMaxSize()) {
                    if (settings.onboardingRevision < ONBOARDING_REVISION) {
                        WelcomeOnboardingScreen(
                            onComplete = { destination ->
                                completeOnboarding(settingsStore).also { completed ->
                                    if (completed) onboardingCompletionRoute = destination.route
                                }
                            },
                        )
                    } else {
                        val initialDestination = onboardingCompletionRoute
                            ?.let(UiDestination::fromRoute)
                            ?: UiDestination.Home
                        key(binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID) {
                            OfflineLibraryScreen(
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
                                pairingRuntime,
                                pairingRuntimeState,
                                admissionRuntime,
                                admissionState,
                                pairingSafeError,
                                initialDestination = initialDestination,
                                clearPairingSafeError = { pairingSafeError = null },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AppLanguageProvider(
    storedLanguage: String,
    content: @Composable () -> Unit,
) {
    val baseContext = LocalContext.current
    val baseConfiguration = LocalConfiguration.current
    val knownLanguage = AppLanguage.knownFromStoredValue(storedLanguage)
    val language = knownLanguage ?: AppLanguage.System
    LaunchedEffect(baseContext, knownLanguage) {
        if (knownLanguage != null && knownLanguage != AppLanguage.System) {
            synchronizeFrameworkAppLanguage(baseContext, knownLanguage)
        }
    }
    val localizedContext = remember(baseContext, baseConfiguration, language) {
        localizedAppContext(baseContext, language)
    }
    if (localizedContext === baseContext) {
        content()
    } else {
        CompositionLocalProvider(
            LocalContext provides localizedContext,
            LocalConfiguration provides localizedContext.resources.configuration,
            LocalResources provides localizedContext.resources,
            content = content,
        )
    }
}

internal fun synchronizeFrameworkAppLanguage(context: Context, language: AppLanguage) {
    if (Build.VERSION.SDK_INT < 33) return
    val desiredLocales = language.languageTag
        ?.let(LocaleList::forLanguageTags)
        ?: LocaleList.getEmptyLocaleList()
    val localeManager = context.getSystemService(LocaleManager::class.java)
    if (localeManager.applicationLocales.toLanguageTags() != desiredLocales.toLanguageTags()) {
        localeManager.applicationLocales = desiredLocales
    }
}

internal fun localizedAppContext(baseContext: Context, language: AppLanguage): Context {
    val languageTag = language.languageTag ?: return baseContext
    val locale = Locale.forLanguageTag(languageTag)
    val configuration = Configuration(baseContext.resources.configuration).apply {
        setLocales(LocaleList(locale))
    }
    return baseContext.createConfigurationContext(configuration)
}

@Composable
@UnstableApi
private fun OfflineLibraryScreen(
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
    pairingRuntime: ProfilePairingRuntime,
    pairingRuntimeState: app.autplay.application.profilepairing.ProfilePairingRuntimeState,
    admissionRuntime: app.autplay.application.profilepairing.AdmissionRuntime,
    admissionState: app.autplay.application.profilepairing.AdmissionState,
    pairingSafeError: String?,
    initialDestination: UiDestination = UiDestination.Home,
    clearPairingSafeError: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var socialRuntime by remember(binding?.serverProfileId?.value, settings.serverBaseUrl, waveCoordinator) {
        mutableStateOf<SocialRuntime?>(null)
    }
    LaunchedEffect(binding, settings.serverBaseUrl, waveCoordinator) {
        socialRuntime = if (binding == null || settings.serverBaseUrl == null) null else runCatching {
            AutPlayRuntime.socialRuntime(context, binding, scope) { roomId ->
                scope.launch { waveCoordinator?.join(roomId) }
            }
        }.getOrNull()
    }
    val emptySocialState = remember { flowOf(SocialRuntimeState()) }
    val socialState by (socialRuntime?.state ?: emptySocialState).collectAsState(
        initial = SocialRuntimeState(),
    )
    LaunchedEffect(socialRuntime, lifecycleOwner) {
        val runtime = socialRuntime ?: return@LaunchedEffect
        runtime.load()
        runtime.loadProfileStatisticsSettings()
        lifecycleOwner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                runtime.heartbeatWhileActive()
                delay(60_000L)
            }
        }
    }
    val resources = LocalResources.current
    val coreProductRepository = remember(context) { CoreProductRepository(AutPlayRuntime.database(context)) }
    val profileStatisticsRepository = remember(context) {
        ProfileStatisticsRepository(AutPlayRuntime.database(context))
    }
    val ownerStatistics by remember(profileStatisticsRepository, binding?.serverProfileId?.value) {
        profileStatisticsRepository.observe(binding?.serverProfileId?.value)
            .map<OwnerProfileStatistics, OwnerProfileStatistics?> { it }
    }.collectAsState(initial = null)
    val artistCatalogPort = remember(context) { RoomArtistCatalogPort(AutPlayRuntime.database(context)) }
    val libraryEntries by remember(coreProductRepository, binding?.serverProfileId?.value) {
        coreProductRepository.libraryEntries(binding?.serverProfileId?.value)
    }.collectAsState(initial = emptyList())
    val activeQueueContextRepository = remember(context, scope) {
        ActiveQueueContextRepository.fromDatabase(AutPlayRuntime.database(context), scope)
    }
    val playerAdapter = remember(context, lifecycleOwner, activeQueueContextRepository) {
        PlaybackPresentationAdapter(context, lifecycleOwner.lifecycle, activeQueueContextRepository)
    }
    val playbackActions = remember(playerAdapter, waveCoordinator) {
        PlaybackInteractionRouter(
            direct = PlaybackPresentationActionPort(playerAdapter),
            wave = waveCoordinator?.let(::WaveCoordinatorHostPlaybackCommandPort),
        )
    }
    DisposableEffect(playerAdapter) {
        onDispose { playerAdapter.close() }
    }
    val playerState by playerAdapter.state.collectAsState()
    val queueEditorRepository = remember(context, playbackOwner) {
        QueueEditorRepository(AutPlayRuntime.database(context), playbackOwner)
    }
    val queueProjection by remember(queueEditorRepository, binding?.serverProfileId?.value) {
        queueEditorRepository.observeActive(binding?.serverProfileId?.value)
    }.collectAsState(initial = null)
    val navigation = rememberAutPlayNavigationState(initialDestination)
    val destination = navigation.current
    LaunchedEffect(destination, socialRuntime) {
        if (destination != UiDestination.Profile) socialRuntime?.clearFriendProfileStatistics()
    }
    val view = legacyView(destination)
    val coreProductState = rememberCoreProductUiState(binding?.serverProfileId?.value)
    val coreActionGate = remember { SingleFlightActionGate() }
    var searchCompleted by rememberSaveable { mutableStateOf(false) }
    var searchSessionId by rememberSaveable { mutableStateOf<String?>(null) }
    var searchResults by remember { mutableStateOf<List<LocalTrackSearchResult>>(emptyList()) }
    var searchLoading by remember { mutableStateOf(false) }
    var vaultSearchLoading by remember { mutableStateOf(false) }
    var vaultSearchError by remember { mutableStateOf(false) }
    var vaultSearchResultCount by remember { mutableStateOf<Int?>(null) }
    val searchGeneration = remember { SearchGenerationGuard() }
    val searchResultStore = remember { SearchResultStore<LocalTrackSearchResult>() }
    var stableError by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(pairingSafeError) {
        pairingSafeError?.let {
            stableError = it
            clearPairingSafeError()
        }
    }
    var homeError by remember(binding?.serverProfileId?.value) { mutableStateOf(false) }
    var searchError by remember(binding?.serverProfileId?.value) { mutableStateOf(false) }
    var libraryError by remember(binding?.serverProfileId?.value) { mutableStateOf(false) }
    var libraryImportInProgress by remember(binding?.serverProfileId?.value) { mutableStateOf(false) }
    var lastImportedTitle by remember(binding?.serverProfileId?.value) { mutableStateOf<String?>(null) }
    var homeRetryNonce by remember { mutableStateOf(false) }
    val playbackState by PlaybackRuntimeState.state.collectAsState()
    var selectedImportEntryId by rememberSaveable { mutableStateOf<String?>(null) }
    val repositorySnapshot = rememberOfflineRepositorySnapshot(
        coreProductRepository,
        artistCatalogPort,
        downloadRepository,
        syncStatusRepository,
        importRepository,
        binding,
        selectedImportEntryId,
    )
    val playlists = repositorySnapshot.playlists
    val libraryReleases = repositorySnapshot.releases
    val libraryPreferences = repositorySnapshot.preferences
    val historyCount = repositorySnapshot.historyCount
    val downloads = repositorySnapshot.downloads
    val localAudioStates = repositorySnapshot.localAudio
    val downloadedTrackIds = repositorySnapshot.downloadedTrackIds
    val homeRecentlyAdded = repositorySnapshot.recentlyAdded
    val homeRecentlyPlayed = repositorySnapshot.recentlyPlayed
    val homePlaylists = repositorySnapshot.homePlaylists
    val homeActiveQueue = repositorySnapshot.activeQueue
    val syncStatus = repositorySnapshot.syncStatus
    val importProfileId = repositorySnapshot.importProfileId
    val importJob = repositorySnapshot.importJob
    val importItems = repositorySnapshot.importItems
    val selectedImportItem = repositorySnapshot.selectedImportItem
    val importCandidates = repositorySnapshot.importCandidates
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
    val coreBindingKey = binding?.serverProfileId?.value ?: LEGACY_PROFILE_ID
    var playlistMutationNonce by remember(coreBindingKey) { mutableIntStateOf(0) }
    val detailContextKey = "$coreBindingKey:$playlistMutationNonce"
    val playlistMutationActions = remember(scope, sliceRepository, coreBindingKey) {
        buildManualPlaylistActions(
            scope = scope,
            binding = { binding },
            sliceRepository = sliceRepository,
            reportError = { stableError = it },
            onChanged = { playlistMutationNonce += 1 },
        )
    }
    var selectedTrackRefId by remember(coreBindingKey) {
        mutableStateOf(
            coreProductState.selectedDetail
                ?.takeIf { it.kind == DetailKind.Track }
                ?.stableId,
        )
    }
    val coreDetailState = rememberOfflineCoreDetailState(
        repository = coreProductRepository,
        artistCatalogPort = artistCatalogPort,
        target = coreProductState.selectedDetail,
        profileId = binding?.serverProfileId?.value,
        contextKey = detailContextKey,
        reportError = { stableError = it },
    )
    fun closeCoreDetail() {
        selectedTrackRefId = null
        coreProductState.clearDetail()
    }
    val hasVisibleCoreDetail = destination == UiDestination.Library && coreProductState.selectedDetail != null
    BackHandler(enabled = hasVisibleCoreDetail || navigation.canNavigateBack) {
        if (hasVisibleCoreDetail) closeCoreDetail() else navigation.navigateBack()
    }
    var selectedUploadCandidate by remember { mutableStateOf<VaultUploadCandidate?>(null) }
    var remoteImportJobId by rememberSaveable(binding?.serverProfileId?.value) { mutableStateOf<String?>(null) }
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
        val activeIds = libraryEntries.filterNot { it.removed }.map { it.localUserTrackRefId }
        if (
            selectedTrackRefId != null &&
            coreProductState.selectedDetail?.kind in setOf(null, DetailKind.Track) &&
            selectedTrackRefId !in activeIds
        ) {
            selectedTrackRefId = null
        }
    }
    LaunchedEffect(selectedTrackRefId) {
        selectedTrackRefId?.let { coreProductState.selectDetail(DetailTarget(DetailKind.Track, it)) }
            ?: if (coreProductState.selectedDetail?.kind == DetailKind.Track) coreProductState.clearDetail() else Unit
    }
    RefreshSearchOnBindingEffect(
        binding = binding,
        context = context,
        completed = searchCompleted,
        coreState = coreProductState,
        generation = searchGeneration,
        resultStore = searchResultStore,
        repository = searchRepository,
        setResults = { searchResults = it },
        setLoading = { searchLoading = it },
        setError = { searchError = it },
        setVaultLoading = { vaultSearchLoading = it },
        setVaultError = { vaultSearchError = it },
        setVaultResultCount = { vaultSearchResultCount = it },
        reportError = { stableError = it },
    )
    LaunchedEffect(selectedTrackRefId, binding?.serverProfileId?.value) {
        val trackRefId = selectedTrackRefId
        selectedUploadCandidate = if (trackRefId == null || binding == null) {
            null
        } else {
            val available = coreProductRepository.availableAudioForTrack(
                trackRefId,
                binding.serverProfileId.value,
            )
            if (available != null) {
                VaultUploadCandidate(
                    trackRefId,
                    available.serverRecordingId,
                    available.localAudioStateId,
                    available.localSha256,
                    available.byteSize,
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
    val activityLaunchers = rememberOfflineActivityLaunchers(
        binding = binding,
        scope = scope,
        context = context,
        sliceRepository = sliceRepository,
        importProfileId = importProfileId,
        importRepository = importRepository,
        coreProductState = coreProductState,
        settings = settings,
        settingsStore = settingsStore,
        serverStateRepository = serverStateRepository,
        launchServerAction = ::launchServerAction,
        navigate = navigation::navigate,
        setRemoteImportJobId = { remoteImportJobId = it },
        setServerUiState = { serverUiState = it },
        serverUiState = { serverUiState },
        setLibraryImportInProgress = { libraryImportInProgress = it },
        setLibraryError = { libraryError = it },
        setLastImportedTitle = { lastImportedTitle = it },
        reportError = { stableError = it },
    )
    LaunchedEffect(view, binding, homeRetryNonce) {
        if (view == "Home" && binding != null) {
            homeFeed = null
            homeError = false
            runCatching { recommendationRepository.loadHomeFeed(binding, System.currentTimeMillis()) }
                .onSuccess {
                    homeFeed = it
                    homeError = false
                }
                .onFailure {
                    homeFeed = null
                    homeError = true
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
    val activeHomeFeed = homeFeed?.takeIf { feed ->
        binding != null &&
            feed.ownerProfileId == binding.serverProfileId.value &&
            feed.ownerUserId == binding.userId.value &&
            feed.ownerDeviceId == binding.deviceId.value
    }
    val homeRecommendationSectionLabel = stringResource(R.string.home_recommendations)
    val homeRecommendations = activeHomeFeed?.recommendationSections.orEmpty().flatMap { (section, items) ->
        items.map { item ->
            val presentationId = activeHomeFeed?.presentationId.orEmpty()
            val key = recommendationKey(presentationId, item)
            HomeRecommendationUiItem(
                id = key,
                title = item.title,
                artist = item.artist,
                section = if (section.equals("FOR_YOU", ignoreCase = true)) {
                    homeRecommendationSectionLabel
                } else {
                    section.replace('_', ' ')
                },
                feedbackEnabled = presentationResults.containsKey(key),
            )
        }
    }
    val homeReleases = (
        libraryReleases + activeHomeFeed?.recentRelevantReleases.orEmpty().map { release ->
            CoreReleaseSummary(release.localReleaseId, release.title, release.artist)
        }
    ).distinctBy(CoreReleaseSummary::stableId)
    val coreCommandActions = buildCoreCommandActions(
        scope = scope,
        binding = { binding },
        activeHomeFeed = { activeHomeFeed },
        presentationResults = presentationResults,
        actionGate = coreActionGate,
        recommendationRepository = recommendationRepository,
        sliceRepository = sliceRepository,
        setHomeFeed = { homeFeed = it },
        reportError = { stableError = it },
        playbackRepository = playbackRepository,
        playbackOwner = playbackOwner,
        searchResultStore = searchResultStore,
        searchSessionId = { searchSessionId },
        setSearchSessionId = { searchSessionId = it },
        playlistDetail = { coreDetailState.playlist },
        libraryEntries = { libraryEntries },
        setLibraryError = { libraryError = it },
        selectedDetail = { coreProductState.selectedDetail },
        closeCoreDetail = ::closeCoreDetail,
        coreProductRepository = coreProductRepository,
        setTrackDetail = { coreDetailState.track = it },
        bindingContextKey = { detailContextKey },
        setLoadedDetailContextKey = { coreDetailState.loadedContextKey = it },
        downloadRepository = downloadRepository,
    )
    val manualPlaylistActions = playlistMutationActions.copy(
        playEntry = coreCommandActions.startPlaylistEntry,
    )
    val searchCommandActions = buildSearchCommandActions(
        scope = scope,
        context = context,
        binding = { binding },
        coreState = coreProductState,
        generation = searchGeneration,
        resultStore = searchResultStore,
        searchRepository = searchRepository,
        state = SearchActionState(
            setCompleted = { searchCompleted = it },
            setSessionId = { searchSessionId = it },
            setResults = { searchResults = it },
            setLoading = { searchLoading = it },
            setError = { searchError = it },
            setVaultLoading = { vaultSearchLoading = it },
            setVaultError = { vaultSearchError = it },
            setVaultResultCount = { vaultSearchResultCount = it },
        ),
        reportError = { stableError = it },
    )
    fun resumeHomeQueue(trackRefId: String) {
        if (playbackState.localUserTrackRefId == trackRefId && playerState.mediaId != null) {
            playbackActions.toggleDirectPlayPause()
        } else {
            coreCommandActions.startTrack(trackRefId, "HOME", "USER", null)
        }
        navigation.navigate(UiDestination.NowPlaying)
    }
    fun openHomePlaylist(stableId: String) {
        coreProductState.selectDetail(DetailTarget(DetailKind.Playlist, stableId))
        navigation.navigate(UiDestination.Library)
    }
    fun openOfflineLibrary() {
        coreProductState.librarySection = LibrarySection.Offline
        navigation.navigate(UiDestination.Library)
    }
    fun openCoreDetail(target: DetailTarget) {
        selectedTrackRefId = target.takeIf { it.kind == DetailKind.Track }?.stableId
        coreProductState.selectDetail(target)
        navigation.navigate(UiDestination.Library)
    }
    fun openCoreCollection(section: LibrarySection, stableId: String) {
        when (section) {
            LibrarySection.Albums -> openCoreDetail(DetailTarget(DetailKind.Release, stableId))
            LibrarySection.Artists -> openCoreDetail(DetailTarget(DetailKind.Artist, stableId))
            LibrarySection.Playlists -> openCoreDetail(DetailTarget(DetailKind.Playlist, stableId))
            else -> Unit
        }
    }
    val visibleSearchResults = searchResultStore.visibleFor(
        coreProductState.query,
        coreProductState.scopes + SearchScope.Local,
        binding?.serverProfileId?.value,
    )
    val searchContextIsCurrent = searchResultStore.matchesContext(
        coreProductState.query,
        coreProductState.scopes + SearchScope.Local,
        binding?.serverProfileId?.value,
    )
    val untitledTrack = stringResource(R.string.player_nothing_playing)
    val queueEditorState = QueueEditorUiState(
        entries = queueProjection?.entries.orEmpty().map { entry ->
            QueueEditorUiEntry(
                queueEntryId = entry.queueEntryId.value,
                title = entry.title ?: untitledTrack,
                artist = entry.artist,
                isCurrent = entry.isCurrent,
                isUpcoming = entry.isUpcoming,
            )
        },
        editable = queueProjection?.queueType in setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST"),
        canPrevious = queueProjection?.canPrevious == true,
        canNext = queueProjection?.canNext == true,
    )
    fun launchQueueEdit(operation: suspend () -> Unit) {
        scope.launch {
            runCatching { operation() }.onFailure { failure ->
                stableError = (failure as? QueueEditFailure)?.code ?: "QUEUE_EDIT_UNAVAILABLE"
            }
        }
    }
    fun addTrackToQueue(trackRefId: String, playNext: Boolean) {
        launchQueueEdit {
            val entry = NewPlaybackQueueEntry(
                queueEntryId = LocalId.random(),
                trackRefId = LocalId(trackRefId),
                sourceOrigin = "ORGANIC",
                sourceAudioPolicy = "LOCAL_THEN_VAULT",
            )
            val expectedSnapshotId = queueProjection?.snapshotId
            val result = if (playNext) {
                queueEditorRepository.addNext(entry, binding?.serverProfileId?.value, expectedSnapshotId)
            } else {
                queueEditorRepository.addToEnd(entry, binding?.serverProfileId?.value, expectedSnapshotId)
            }
            if (result.created) {
                playbackOwner.dispatch(
                    if (playNext) PlaybackCommand.StartQueue(result.snapshotId)
                    else PlaybackCommand.PrepareQueue(result.snapshotId),
                )
            }
        }
    }
    val queueEditorActions = QueueEditorUiActions(
        moveUp = { entryId ->
            queueProjection?.let { projection ->
                val upcoming = projection.entries.filter { it.isUpcoming }
                val index = upcoming.indexOfFirst { it.queueEntryId.value == entryId }
                val before = upcoming.getOrNull(index - 1)?.queueEntryId
                if (index > 0 && before != null) launchQueueEdit {
                    queueEditorRepository.moveUpcoming(
                        LocalId(entryId), before, binding?.serverProfileId?.value, projection.snapshotId,
                    )
                }
            }
        },
        moveDown = { entryId ->
            queueProjection?.let { projection ->
                val upcoming = projection.entries.filter { it.isUpcoming }
                val index = upcoming.indexOfFirst { it.queueEntryId.value == entryId }
                if (index in 0 until upcoming.lastIndex) launchQueueEdit {
                    queueEditorRepository.moveUpcoming(
                        LocalId(entryId), upcoming.getOrNull(index + 2)?.queueEntryId,
                        binding?.serverProfileId?.value, projection.snapshotId,
                    )
                }
            }
        },
        remove = { entryId ->
            queueProjection?.let { projection ->
                launchQueueEdit {
                    queueEditorRepository.removeUpcoming(
                        LocalId(entryId), binding?.serverProfileId?.value, projection.snapshotId,
                    )
                }
            }
        },
        clearUpcoming = {
            queueProjection?.let { projection ->
                launchQueueEdit {
                    queueEditorRepository.clearUpcoming(binding?.serverProfileId?.value, projection.snapshotId)
                }
            }
        },
    )
    val allLibraryTrackSummaries = buildLibraryTrackSummaries(
        entries = libraryEntries,
        audioStates = localAudioStates,
        preferences = libraryPreferences,
        downloadedTrackIds = downloadedTrackIds,
        untitledTrack = untitledTrack,
    )
    val homeProblemCounts = countHomeProblems(
        tracks = allLibraryTrackSummaries,
        reviewCount = importJob?.reviewRequiredCount ?: 0,
        downloads = downloads,
    )
    fun openHomeProblems() {
        coreProductState.librarySection = when {
            homeProblemCounts.reviewRequired > 0 -> LibrarySection.Review
            homeProblemCounts.permissionRevoked > 0 -> LibrarySection.Unavailable
            else -> LibrarySection.Offline
        }
        navigation.navigate(UiDestination.Library)
    }
    val totalHomeProblems = homeProblemCounts.permissionRevoked +
        homeProblemCounts.reviewRequired + homeProblemCounts.failedDownloads
    val homeProblems = if (totalHomeProblems == 0) emptyList() else listOf(
        HomeProblemUiItem(
            "attention-summary",
            resources.getQuantityString(
                R.plurals.home_problem_total_count,
                totalHomeProblems,
                totalHomeProblems,
            ),
        ),
    )
    val homeScreenState = buildHomeScreenUiState(
        localMode = binding == null,
        recommendationLoading = binding != null && activeHomeFeed == null,
        offlineFallback = activeHomeFeed?.isStaleFallback == true,
        releases = homeReleases,
        recommendations = homeRecommendations,
        continueListening = homeActiveQueue,
        recentlyPlayed = homeRecentlyPlayed,
        recentlyAdded = homeRecentlyAdded,
        playlists = homePlaylists,
        libraryTracks = allLibraryTrackSummaries,
        problems = homeProblems,
        recommendationError = homeError,
        untitledTrack = untitledTrack,
    )
    val libraryScreenState = buildLibraryScreenUiState(
        localMode = binding == null,
        tracks = allLibraryTrackSummaries,
        section = coreProductState.librarySection,
        sort = coreProductState.librarySort,
        filter = coreProductState.libraryFilter,
        selectedTrackRefId = selectedTrackRefId,
        playlists = playlists,
        releases = libraryReleases,
        artists = repositorySnapshot.artists,
        artistBrowseState = when (repositorySnapshot.artistBrowseStatus) {
            OfflineArtistBrowseStatus.Unavailable -> ArtistBrowseUiState.Unavailable
            OfflineArtistBrowseStatus.Loading -> ArtistBrowseUiState.Loading
            OfflineArtistBrowseStatus.Ready -> ArtistBrowseUiState.Ready
            OfflineArtistBrowseStatus.Error -> ArtistBrowseUiState.Error
        },
        reviewCount = importJob?.reviewRequiredCount ?: 0,
        importingLocalTrack = libraryImportInProgress,
        lastImportedTitle = lastImportedTitle,
        error = libraryError,
    )
    val detailRequestIsCurrent = coreDetailState.matches(detailContextKey, coreProductState.selectedDetail)
    val presentedTrackDetail = coreDetailState.track.takeIf { detailRequestIsCurrent }
    val presentedReleaseDetail = coreDetailState.release.takeIf { detailRequestIsCurrent }
    val presentedPlaylistDetail = coreDetailState.playlist.takeIf { detailRequestIsCurrent }
    val presentedArtistDetail = coreDetailState.artist.takeIf { detailRequestIsCurrent }
    val presentedArtistAppearances = coreDetailState.artistAppearances.takeIf { detailRequestIsCurrent }.orEmpty()
    val presentedSubjectArtistCredits = coreDetailState.subjectArtistCredits
        .takeIf { detailRequestIsCurrent }
        .orEmpty()
    val presentedDetailError = coreDetailState.error && detailRequestIsCurrent
    val presentedDetailLoading = coreProductState.selectedDetail != null &&
        (coreDetailState.loading || !detailRequestIsCurrent) && !presentedDetailError
    val searchScreenState = SearchScreenUiState(
        query = coreProductState.query,
        results = visibleSearchResults.map { result ->
            CoreTrackUiItem(
                result.localUserTrackRefId,
                result.rawTitle ?: untitledTrack,
                result.rawArtist,
            )
        },
        searched = searchCompleted && searchContextIsCurrent,
        loading = searchLoading && searchContextIsCurrent,
        error = searchError,
        vaultAvailable = binding != null,
        vaultSelected = SearchScope.Vault in coreProductState.scopes,
        vaultLoading = vaultSearchLoading && searchContextIsCurrent,
        vaultResultCount = vaultSearchResultCount.takeIf { searchContextIsCurrent },
        vaultError = vaultSearchError && searchContextIsCurrent,
    )
    val coreDetailUiState = CoreProductDetailUiState(
        target = coreProductState.selectedDetail,
        loading = presentedDetailLoading,
        error = presentedDetailError,
        track = presentedTrackDetail,
        release = presentedReleaseDetail,
        playlist = presentedPlaylistDetail,
        artist = presentedArtistDetail,
        artistAppearances = presentedArtistAppearances,
        subjectArtistCredits = presentedSubjectArtistCredits,
    )
    val coreRouteActions = CoreProductRouteActions(
        openListenTogether = { navigation.navigate(UiDestination.WaveRooms) },
        recommendationVisible = coreCommandActions.recordRecommendationVisibility,
        likeRecommendation = { coreCommandActions.recordHomeFeedback(it, "LIKED") },
        dislikeRecommendation = { coreCommandActions.recordHomeFeedback(it, "DISLIKED") },
        retryHome = { homeRetryNonce = !homeRetryNonce },
        resumeHomeQueue = ::resumeHomeQueue,
        openHomePlaylist = ::openHomePlaylist,
        openOffline = ::openOfflineLibrary,
        openProblems = ::openHomeProblems,
        changeQuery = searchCommandActions.changeQuery,
        submitSearch = searchCommandActions.submit,
        playSearchResult = coreCommandActions.startSearchTrack,
        changeVaultScope = searchCommandActions.changeVaultScope,
        changeSearchAnchor = { coreProductState.searchListAnchor = it },
        addLocal = { activityLaunchers.addLocalAudio.launch(arrayOf("audio/*")) },
        selectTrack = { selectedTrackRefId = it },
        removeOrRestore = coreCommandActions.updateLibraryMembership,
        likeTrack = coreCommandActions.likeTrack,
        changeLibrarySection = { coreProductState.librarySection = it },
        changeLibrarySort = { coreProductState.librarySort = it },
        changeLibraryFilter = { coreProductState.libraryFilter = it },
        openCollection = ::openCoreCollection,
        openDetail = ::openCoreDetail,
        openReview = { navigation.navigate(UiDestination.ImportReview) },
        changeLibraryAnchor = { coreProductState.libraryListAnchor = it },
        playTrack = { coreCommandActions.startTrack(it, "LIBRARY", "LIBRARY", null) },
        playPlaylistEntry = coreCommandActions.startPlaylistEntry,
        downloadTrack = coreCommandActions.downloadTrack,
        repairAccess = { activityLaunchers.chooseLibraryRoot.launch(null) },
        playNext = { addTrackToQueue(it, true) },
        addToQueue = { addTrackToQueue(it, false) },
        manualPlaylists = playlists.map { ManualPlaylistUi(it.stableId, it.title, it.description) },
        manualPlaylistActions = manualPlaylistActions,
    )
    val nowPlayingRouteActions = buildNowPlayingRouteActions(
        playbackActions = playbackActions,
        playerAdapter = playerAdapter,
        playbackOwner = playbackOwner,
        currentTrackRefId = { playbackState.localUserTrackRefId },
        currentQueueEntryId = { playbackState.queueEntryId },
        scope = scope,
        sliceRepository = sliceRepository,
        binding = { binding },
        reportError = { stableError = it },
        queueActions = queueEditorActions,
    )
    val currentTrackPreference = playbackState.localUserTrackRefId?.let { trackRefId ->
        libraryPreferences.firstOrNull { it.stableId == trackRefId }
    }
    val sleepTimerRemainingMinutes = rememberSleepTimerRemainingMinutes(
        playbackState.sleepTimerDeadlineElapsedRealtimeMs,
    )
    val serverFeaturesActions = buildServerFeaturesActions(
        launchServerAction = ::launchServerAction,
        binding = { binding },
        state = { serverUiState },
        setState = { serverUiState = it },
        remoteImportJobId = { remoteImportJobId },
        setRemoteImportJobId = { remoteImportJobId = it },
        stateRepository = serverStateRepository,
        context = context,
        selectedUploadCandidate = { selectedUploadCandidate },
        durableVaultUploads = { durableVaultUploads },
        scope = scope,
        chooseServerImport = {
            activityLaunchers.startServerImport.launch(arrayOf("text/csv", "application/json", "text/html"))
        },
    )
    val legacyImportActions = LegacyImportRouteActions(
        chooseAudio = { activityLaunchers.addReviewAudio.launch(arrayOf("audio/*")) },
        selectEntry = { selectedImportEntryId = it },
        review = { action, candidateId ->
            selectedImportItem?.let { item ->
                scope.launch {
                    runCatching {
                        importRepository.recordReview(
                            RecordImportReviewCommand(
                                importEntryId = item.entry.importEntryId,
                                localChangeId = LocalId.random().value,
                                idempotencyKey = LocalId.random().value,
                                action = action,
                                candidateId = candidateId,
                                createdRecordingId = if (action == ImportReviewAction.CREATE_RECORDING) LocalId.random().value else null,
                                predecessorDecisionId = item.latestEvaluation?.decisionId,
                                nowMs = System.currentTimeMillis(),
                            ),
                        )
                    }.onFailure { stableError = "IMPORT_REVIEW_UNAVAILABLE" }
                }
            }
        },
    )
    val socialActions = SocialActions(
        refresh = { socialRuntime?.load() },
        createContactCard = { socialRuntime?.loadContactCard() },
        shareContactCard = { card: ContactCard ->
            val share = Intent(Intent.ACTION_SEND).apply {
                type = "application/json"
                putExtra(Intent.EXTRA_TEXT, card.asJson().toString())
            }
            context.startActivity(Intent.createChooser(share, "Share AutPlay contact card"))
        },
        importContactCard = { socialRuntime?.importContactCard(it) },
        acceptFriend = { socialRuntime?.acceptFriend(it) },
        declineFriend = { socialRuntime?.declineFriend(it) },
        cancelFriendRequest = { socialRuntime?.cancelFriendRequest(it) },
        removeFriend = { socialRuntime?.removeFriend(it) },
        block = { socialRuntime?.block(it) },
        unblock = { socialRuntime?.unblock(it) },
        setPresence = { socialRuntime?.setPresence(it) },
        setProfileStatisticsVisibility = { socialRuntime?.setProfileStatisticsVisibility(it) },
        viewFriendStatistics = { socialRuntime?.loadFriendProfileStatistics(it) },
        closeFriendStatistics = { socialRuntime?.clearFriendProfileStatistics() },
        inviteFriend = { accountId ->
            val roomId = waveCoordinator?.uiState?.value?.roomId
            if (roomId == null) stableError = "WAVE_ROOM_REQUIRED"
            else socialRuntime?.createRoomInvitation(roomId, accountId)
        },
        acceptInvitation = { socialRuntime?.acceptRoomInvitation(it) },
        cancelInvitation = { socialRuntime?.cancelRoomInvitation(it) },
    )
    val legacySecondaryState = LegacySecondaryRouteState(
        destination = destination,
        view = view,
        playlists = playlists,
        libraryEntries = libraryEntries,
        selectedTrackRefId = selectedTrackRefId,
        historyCount = historyCount,
        importState = LegacyImportRouteState(
            job = importJob,
            items = importItems,
            selectedItem = selectedImportItem,
            candidates = importCandidates,
        ),
        downloads = downloads,
        isProfileBound = binding != null,
        syncStatus = syncStatus,
        waveCoordinator = waveCoordinator,
        serverUiState = serverUiState,
        selectedTrackLabel = libraryEntries.firstOrNull { it.localUserTrackRefId == selectedTrackRefId }?.let { entry ->
            stringResource(
                R.string.server_selected_track,
                entry.title ?: stringResource(R.string.track_untitled),
                entry.artistName ?: stringResource(R.string.library_unknown_artist),
            )
        },
        selectedTrackUploadEligible = selectedUploadCandidate != null,
        settings = settings,
        profilePairing = ProfilePairingUiState(
            pairing = pairingRuntimeState.pairing,
            serverLabel = pairingRuntimeState.serverLabel,
            trustConfirmed = pairingRuntimeState.trustConfirmed,
            accountLabel = pairingRuntimeState.accountLabel,
            deviceLabel = pairingRuntimeState.deviceLabel,
            pendingSyncCount = syncStatus.pending,
            devices = pairingRuntimeState.devices,
            sessions = pairingRuntimeState.sessions,
            localDataChoiceRequired = pairingRuntimeState.localDataChoiceRequired,
            localDataReview = pairingRuntimeState.localReview?.let { review ->
                app.autplay.ui.profilepairing.LocalDataReviewUiState(
                    review.map { app.autplay.ui.profilepairing.PendingLocalDataUiSummary(it.localChangeId, it.eventType, it.occurredAtMs) },
                    applying = pairingRuntimeState.applyingLocalReview,
                )
            },
            invitationManagement = (pairingRuntimeState.pairing as? PairingState.Connected)?.let {
                app.autplay.ui.profilepairing.InvitationManagementUiState(
                    canCreate = pairingRuntimeState.canCreateInvitation,
                    minExpiryMinutes = 1,
                    maxExpiryMinutes = 30,
                    creating = pairingRuntimeState.invitationPending,
                    createdSecret = pairingRuntimeState.createdInvitationEnvelope,
                    cancelling = pairingRuntimeState.invitationPending && pairingRuntimeState.createdInvitationId != null,
                )
            },
            pendingRemoteAction = pairingRuntimeState.pendingLifecycle?.let {
                when (it) {
                    app.autplay.application.profilepairing.RuntimeLifecycleAction.LOGOUT_CURRENT -> app.autplay.ui.profilepairing.ProfileRemoteAction.LOGOUT_CURRENT
                    app.autplay.application.profilepairing.RuntimeLifecycleAction.LOGOUT_ALL -> app.autplay.ui.profilepairing.ProfileRemoteAction.LOGOUT_ALL
                    app.autplay.application.profilepairing.RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE -> app.autplay.ui.profilepairing.ProfileRemoteAction.REVOKE_CURRENT_DEVICE
                    app.autplay.application.profilepairing.RuntimeLifecycleAction.DISCONNECT_LOCAL -> app.autplay.ui.profilepairing.ProfileRemoteAction.DISCONNECT_LOCAL
                }
            },
            admission = if (pairingRuntimeState.pairing is PairingState.AwaitingTrust && pairingRuntimeState.trustConfirmed || admissionState !is app.autplay.application.profilepairing.AdmissionState.RequestReady) {
                app.autplay.ui.profilepairing.AdmissionUiState(admissionState)
            } else null,
        ),
        ownerStatistics = ownerStatistics,
        social = socialState,
        socialAvailable = socialRuntime != null,
        stableError = stableError,
    )
    val legacySecondaryActions = buildLegacySecondaryRouteActions(
        scope = scope,
        binding = { binding },
        playlists = { playlists },
        libraryEntries = { libraryEntries },
        selectedTrackRefId = { selectedTrackRefId },
        sliceRepository = sliceRepository,
        downloadRepository = downloadRepository,
        syncScheduler = syncScheduler,
        resolveServerRecordingId = { trackRefId ->
            coreProductRepository.serverRecordingId(trackRefId, binding?.serverProfileId?.value)
        },
        playbackActions = playbackActions,
        reportError = { stableError = it },
        serverFeaturesActions = serverFeaturesActions,
        navigate = navigation::navigate,
        launchServerAction = ::launchServerAction,
        settings = settings,
        settingsStore = settingsStore,
        context = context,
        profilePairingRuntime = pairingRuntime,
        admissionRuntime = admissionRuntime,
        admissionSnapshot = when (val pairing = pairingRuntimeState.pairing) {
            is PairingState.AwaitingTrust -> pairing.snapshot
            is PairingState.Connected -> pairing.snapshot
            else -> null
        },
        importProfileId = importProfileId,
        importRepository = importRepository,
        importActions = legacyImportActions,
        chooseLibraryRoot = { activityLaunchers.chooseLibraryRoot.launch(null) },
        exportSettings = { activityLaunchers.exportSettings.launch("autplay-settings.json") },
        importSettings = { activityLaunchers.importSettings.launch(arrayOf("application/json", "text/json")) },
        social = socialActions,
        manualPlaylists = manualPlaylistActions,
        openPlaylist = { openCoreDetail(DetailTarget(DetailKind.Playlist, it)) },
    )
    MainAdaptiveShell(
        state = MainAdaptiveShellState(
            destination = destination,
            unreadSyncConflicts = syncStatus.deadLetters + syncStatus.conflicts,
            navigationCanGoBack = navigation.canNavigateBack,
            hasVisibleCoreDetail = hasVisibleCoreDetail,
            playerState = playerState,
            currentTrackRefId = playbackState.localUserTrackRefId,
            currentTrackLiked = playbackState.localUserTrackRefId?.let { trackRefId ->
                libraryPreferences.any { it.stableId == trackRefId && it.loved }
            } == true,
            coreDetailState = coreDetailUiState,
            selectedDetail = coreProductState.selectedDetail,
            homeState = homeScreenState,
            searchState = searchScreenState,
            libraryState = libraryScreenState,
            searchListAnchor = coreProductState.searchListAnchor,
            libraryListAnchor = coreProductState.libraryListAnchor,
            coreRouteActions = coreRouteActions,
            queueState = queueEditorState,
            nowPlayingFeedbackEnabled = playbackState.localUserTrackRefId != null,
            nowPlayingPreference = when {
                currentTrackPreference?.loved == true -> PlaybackPreferenceUiState.Liked
                currentTrackPreference?.disliked == true -> PlaybackPreferenceUiState.Disliked
                else -> PlaybackPreferenceUiState.Neutral
            },
            sleepTimerRemainingMinutes = sleepTimerRemainingMinutes,
            stopAfterCurrentTrackActive = playbackState.stopAfterQueueEntryId == playbackState.queueEntryId,
            nowPlayingActions = nowPlayingRouteActions,
            legacyState = legacySecondaryState,
            legacyActions = legacySecondaryActions,
        ),
        actions = MainAdaptiveShellActions(
            navigate = navigation::navigate,
            navigateBack = navigation::navigateBack,
            closeCoreDetail = ::closeCoreDetail,
            togglePlayPause = playbackActions::toggleDirectPlayPause,
            setMiniPlayerObserving = { playerAdapter.setSurfaceObserving("mini", it) },
            playTrack = { coreCommandActions.startTrack(it, "LIBRARY", "LIBRARY", null) },
            playPlaylistEntry = coreCommandActions.startPlaylistEntry,
            removeOrRestore = coreCommandActions.updateLibraryMembership,
            likeTrack = coreCommandActions.likeTrack,
            downloadTrack = coreCommandActions.downloadTrack,
            repairAccess = { activityLaunchers.chooseLibraryRoot.launch(null) },
            openReview = { navigation.navigate(UiDestination.ImportReview) },
            openDetail = ::openCoreDetail,
        ),
    )
}

private data class OfflineActivityLaunchers(
    val addLocalAudio: ManagedActivityResultLauncher<Array<String>, Uri?>,
    val addReviewAudio: ManagedActivityResultLauncher<Array<String>, Uri?>,
    val chooseLibraryRoot: ManagedActivityResultLauncher<Uri?, Uri?>,
    val exportSettings: ManagedActivityResultLauncher<String, Uri?>,
    val importSettings: ManagedActivityResultLauncher<Array<String>, Uri?>,
    val startServerImport: ManagedActivityResultLauncher<Array<String>, Uri?>,
)

@Composable
private fun rememberOfflineActivityLaunchers(
    binding: ClientEventBinding?,
    scope: CoroutineScope,
    context: android.content.Context,
    sliceRepository: LibraryVerticalSliceRepository,
    importProfileId: String,
    importRepository: LocalImportReviewRepository,
    coreProductState: CoreProductUiState,
    settings: NonSecretSettings,
    settingsStore: NonSecretSettingsStore,
    serverStateRepository: ServerFeatureStateRepository,
    launchServerAction: (String, suspend (ServerFeatureRepository) -> Unit) -> Unit,
    navigate: (UiDestination) -> Unit,
    setRemoteImportJobId: (String) -> Unit,
    setServerUiState: (ServerFeaturesUiState) -> Unit,
    serverUiState: () -> ServerFeaturesUiState,
    setLibraryImportInProgress: (Boolean) -> Unit,
    setLibraryError: (Boolean) -> Unit,
    setLastImportedTitle: (String?) -> Unit,
    reportError: (String) -> Unit,
): OfflineActivityLaunchers {
    val addLocalAudio = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            setLibraryImportInProgress(true)
            setLibraryError(false)
            setLastImportedTitle(null)
            val inspector = ContentUriInspector(context.contentResolver)
            val permission = inspector.acquirePersistableReadPermission(
                uri.toString(),
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            )
            val trackRefId = LocalId.random()
            runCatching {
                val now = System.currentTimeMillis()
                val inspection = inspector.inspectWithDigest(uri.toString())
                val title = inspection.displayName?.takeIf(String::isNotBlank) ?: "Imported track"
                sliceRepository.importUri(
                    binding = binding,
                    trackRefId = trackRefId,
                    libraryEntryId = LocalId.random(),
                    audioStateId = LocalId.random(),
                    changeId = LocalId.random(),
                    title = title,
                    artist = context.getString(R.string.library_unknown_artist),
                    inspection = inspection,
                    persistedPermission = permission,
                    now = now,
                )
                title
            }.onSuccess { title ->
                setLastImportedTitle(title)
                coreProductState.librarySection = LibrarySection.Tracks
                coreProductState.libraryFilter = LibraryFilter.All
            }.onFailure {
                setLibraryError(true)
                reportError("LOCAL_TRACK_IMPORT_UNAVAILABLE")
            }
            setLibraryImportInProgress(false)
        }
    }
    val addReviewAudio = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            val inspector = ContentUriInspector(context.contentResolver)
            val permission = inspector.acquirePersistableReadPermission(
                uri.toString(),
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION,
            )
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
            }.onFailure { reportError("IMPORT_UNAVAILABLE") }
        }
    }
    val chooseLibraryRoot = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                settingsStore.mutate { current -> current.copy(libraryRootTreeUri = uri.toString()) }
                val outcome = scanLibraryRoot(context, uri.toString(), importProfileId, importRepository)
                navigate(UiDestination.ImportReview)
                if (outcome.truncated) reportError("LIBRARY_ROOT_SCAN_LIMIT_REACHED")
            }.onFailure { reportError("LIBRARY_ROOT_PERMISSION_UNAVAILABLE") }
        }
    }
    val exportSettings = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/json"),
    ) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                context.contentResolver.openOutputStream(uri, "wt")?.use { output ->
                    output.write(SettingsTransferCodec.encode(settings))
                } ?: error("SETTINGS_EXPORT_TARGET_UNAVAILABLE")
            }.onFailure { reportError("SETTINGS_EXPORT_UNAVAILABLE") }
        }
    }
    val importSettings = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            runCatching {
                val bytes = context.contentResolver.openInputStream(uri)?.use(::readBoundedSettingsBytes)
                    ?: error("SETTINGS_IMPORT_SOURCE_UNAVAILABLE")
                var importedLanguage: AppLanguage? = null
                settingsStore.mutate { current ->
                    SettingsTransferCodec.decode(bytes, current).also { imported ->
                        importedLanguage = AppLanguage.knownFromStoredValue(imported.appLanguage)
                    }
                }
                importedLanguage?.let { synchronizeFrameworkAppLanguage(context, it) }
            }.onFailure { reportError("SETTINGS_IMPORT_UNAVAILABLE") }
        }
    }
    val startServerImport = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) launchServerAction("SERVER_IMPORT_START") { server ->
            val format = serverImportFormat(context, uri)
            val payload = context.contentResolver.openInputStream(uri)?.use(::readBoundedServerImportBytes)
                ?: error("SERVER_IMPORT_SOURCE_UNAVAILABLE")
            val started = server.startImport(payload, format, materialize = true)
            val activeBinding = checkNotNull(binding)
            serverStateRepository.recordImportStart(activeBinding.serverProfileId, started, System.currentTimeMillis())
            setRemoteImportJobId(started.importJobId)
            val report = server.importReport(started.importJobId)
            serverStateRepository.recordImportReport(activeBinding.serverProfileId, report, System.currentTimeMillis())
            RemoteImportWorkScheduler.enqueue(context, started.importJobId)
            setServerUiState(serverUiState().copy(importReport = report, stableMessage = "SERVER_IMPORT_STARTED"))
        }
    }
    return OfflineActivityLaunchers(
        addLocalAudio,
        addReviewAudio,
        chooseLibraryRoot,
        exportSettings,
        importSettings,
        startServerImport,
    )
}

@Composable
internal fun WaveFrontendScreen(
    coordinator: WaveCoordinator?,
    isProfileBound: Boolean,
    localTrackRefId: String?,
    resolveServerRecordingId: suspend (String) -> String?,
    onStartPlayback: suspend () -> WavePlaybackCommandOutcome,
    onPausePlayback: suspend () -> WavePlaybackCommandOutcome,
    onError: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val state by (coordinator?.uiState ?: flowOf(app.autplay.application.wave.WaveUiState()))
        .collectAsState(initial = app.autplay.application.wave.WaveUiState())
    var roomCode by remember { mutableStateOf("") }
    var createdCode by remember { mutableStateOf<String?>(null) }
    var inviteUserIds by remember { mutableStateOf("") }
    var waveActionMessage by remember { mutableStateOf<Int?>(null) }
    val inviteList = inviteUserIds.split(Regex("[,;\\s]+"))
        .map(String::trim)
        .filter(String::isNotEmpty)
    val inviteIsValid = inviteList.size <= 7 && inviteList.distinct().size == inviteList.size &&
        inviteList.all { value -> runCatching { java.util.UUID.fromString(value) }.isSuccess }

    Text(stringResource(R.string.wave_intro))
    if (!isProfileBound || coordinator == null) {
        Text(stringResource(R.string.wave_requires_server))
        return
    }
    if (state.roomId == null) {
        OutlinedTextField(
            value = inviteUserIds,
            onValueChange = { inviteUserIds = it.take(300) },
            label = { Text(stringResource(R.string.wave_invite_codes)) },
            isError = !inviteIsValid,
            minLines = 2,
        )
        Button(enabled = inviteIsValid, onClick = {
            scope.launch {
                runCatching { coordinator.create(inviteList) }
                    .onSuccess { createdCode = it }
                    .onFailure { onError("WAVE_CREATE_UNAVAILABLE") }
            }
        }) { Text(stringResource(R.string.wave_create_room)) }
        OutlinedTextField(
            value = roomCode,
            onValueChange = { roomCode = it.uppercase().filter(Char::isLetterOrDigit).take(10) },
            label = { Text(stringResource(R.string.wave_room_code_label)) },
        )
        Button(
            enabled = roomCode.length == 10,
            onClick = {
                scope.launch {
                    runCatching { coordinator.joinByCode(roomCode) }
                        .onFailure { onError("WAVE_JOIN_UNAVAILABLE") }
                }
            },
        ) { Text(stringResource(R.string.wave_join_room)) }
    } else {
        createdCode?.let { code ->
            Text(stringResource(R.string.wave_room_code, code))
            val shareText = stringResource(R.string.wave_share_text, code)
            val shareTitle = stringResource(R.string.wave_share_title)
            OutlinedButton(onClick = {
                val share = Intent(Intent.ACTION_SEND)
                    .setType("text/plain")
                    .putExtra(Intent.EXTRA_TEXT, shareText)
                context.startActivity(Intent.createChooser(share, shareTitle))
            }) { Text(stringResource(R.string.wave_share_code)) }
        }
        Text(waveStateLabel(state.state))
        Text(stringResource(if (state.isHost) R.string.wave_you_control else R.string.wave_host_controls))
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
                ) { Text(stringResource(R.string.wave_add_selected_track)) }
                Button(onClick = {
                    scope.launch {
                        when (onStartPlayback()) {
                            WavePlaybackCommandOutcome.Started -> {
                                waveActionMessage = R.string.wave_start_scheduled
                            }
                            WavePlaybackCommandOutcome.WaitingForReadyDevices -> {
                                waveActionMessage = R.string.wave_waiting_devices
                            }
                            WavePlaybackCommandOutcome.RoleRejected,
                            WavePlaybackCommandOutcome.CommandFailed,
                            -> onError("WAVE_START_UNAVAILABLE")
                            WavePlaybackCommandOutcome.Paused -> Unit
                        }
                    }
                }) { Text(stringResource(R.string.wave_start_playback)) }
                Button(onClick = {
                    scope.launch {
                        when (onPausePlayback()) {
                            WavePlaybackCommandOutcome.Paused -> Unit
                            WavePlaybackCommandOutcome.RoleRejected,
                            WavePlaybackCommandOutcome.CommandFailed,
                            -> onError("WAVE_PAUSE_UNAVAILABLE")
                            WavePlaybackCommandOutcome.Started,
                            WavePlaybackCommandOutcome.WaitingForReadyDevices,
                            -> Unit
                        }
                    }
                }) { Text(stringResource(R.string.wave_pause_playback)) }
            }
            waveActionMessage?.let { Text(stringResource(it)) }
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { coordinator.closeRoom() }
                        .onFailure { onError("WAVE_CLOSE_UNAVAILABLE") }
                }
            }) { Text(stringResource(R.string.wave_close_room)) }
        } else {
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching { coordinator.leave() }
                        .onFailure { onError("WAVE_LEAVE_UNAVAILABLE") }
                }
            }) { Text(stringResource(R.string.wave_leave_room)) }
        }
    }
}

@Composable
internal fun ProfileFrontendScreen(
    state: app.autplay.ui.profilepairing.ProfilePairingUiState,
    actions: app.autplay.ui.profilepairing.ProfilePairingActions,
) = app.autplay.ui.profilepairing.ProfilePairingScreen(state, actions)

@Composable
internal fun SettingsFrontendScreen(
    settings: NonSecretSettings,
    onUpdate: ((NonSecretSettings) -> NonSecretSettings) -> Unit,
    onAppLanguageChange: (AppLanguage) -> Unit,
    onChooseLibraryRoot: () -> Unit,
    onRescanLibraryRoot: () -> Unit,
    onExportSettings: () -> Unit,
    onImportSettings: () -> Unit,
    statisticsSettings: app.autplay.application.social.ProfileStatisticsSettingsState,
    statisticsSettingsErrorCode: String?,
    onStatisticsVisibilityChange: (Boolean) -> Unit,
    onNavigate: (UiDestination) -> Unit,
) {
    SettingsProductScreen(
        settings = settings,
        onUpdate = onUpdate,
        onAppLanguageChange = onAppLanguageChange,
        onChooseLibraryRoot = onChooseLibraryRoot,
        onRescanLibraryRoot = onRescanLibraryRoot,
        onExportSettings = onExportSettings,
        onImportSettings = onImportSettings,
        statisticsSettings = statisticsSettings,
        statisticsSettingsErrorCode = statisticsSettingsErrorCode,
        onStatisticsVisibilityChange = onStatisticsVisibilityChange,
        onNavigate = onNavigate,
    )
}

@Composable
private fun waveStateLabel(state: app.autplay.domain.wave.WaveRuntimeState): String = stringResource(
    when (state) {
        app.autplay.domain.wave.WaveRuntimeState.IDLE -> R.string.wave_state_ready
        app.autplay.domain.wave.WaveRuntimeState.PREFLIGHT -> R.string.wave_state_preparing
        app.autplay.domain.wave.WaveRuntimeState.SCHEDULED -> R.string.wave_state_scheduled
        app.autplay.domain.wave.WaveRuntimeState.PLAYING -> R.string.wave_state_playing
        app.autplay.domain.wave.WaveRuntimeState.DEGRADED -> R.string.wave_state_connection_problem
        app.autplay.domain.wave.WaveRuntimeState.REJOINING -> R.string.wave_state_reconnecting
        app.autplay.domain.wave.WaveRuntimeState.CLOSED -> R.string.wave_state_closed
    },
)

@Composable
private fun rememberSleepTimerRemainingMinutes(deadlineElapsedRealtimeMs: Long?): Int? {
    val remaining by produceState<Int?>(initialValue = null, key1 = deadlineElapsedRealtimeMs) {
        while (deadlineElapsedRealtimeMs != null) {
            val remainingMs = deadlineElapsedRealtimeMs - SystemClock.elapsedRealtime()
            if (remainingMs <= 0L) {
                value = null
                break
            }
            value = ceil(remainingMs / 60_000.0).toInt().coerceAtLeast(1)
            delay(30_000L.coerceAtMost(remainingMs))
        }
    }
    return remaining
}

internal fun recommendationKey(presentationId: String, item: HomeRecommendationItem): String =
    "$presentationId:${item.recommendationRequestId}:${item.sourceRank}"

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

internal fun deactivateServerBinding(settings: NonSecretSettings): NonSecretSettings = settings.copy(
    activeServerProfileId = null,
    activeUserId = null,
    deviceId = null,
    m5Binding = null,
    m5TrustEvidence = null,
)

internal data class VaultUploadCandidate(
    val localTrackRefId: String,
    val serverRecordingId: String,
    val localAudioStateId: String,
    val knownSha256: ByteArray?,
    val knownSize: Long?,
)

internal data class LibraryRootImportOutcome(val importedCount: Int, val truncated: Boolean)

internal suspend fun scanLibraryRoot(
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
        Text(stringResource(R.string.recommendation_for_you))
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
