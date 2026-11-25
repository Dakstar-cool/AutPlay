package app.autplay.ui.player

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import app.autplay.ui.playlist.AddTrackToPlaylistDialog
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.ui.playlist.ManualPlaylistUi
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.ui.HomeSwipeTargets
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiState

internal data class NowPlayingRouteActions(
    val togglePlayPause: () -> Unit,
    val phoneAudioRetryEntry: String? = null,
    val retryPhoneAudio: (String) -> Unit = {},
    val toggleShuffle: () -> Unit,
    val cycleRepeat: () -> Unit,
    val previous: () -> Unit = {},
    val next: () -> Unit = {},
    val seekBegin: (Long) -> Unit,
    val seekUpdate: (Long) -> Unit,
    val seekCommit: () -> Unit,
    val like: () -> Unit,
    val dislike: () -> Unit,
    val clearPreference: () -> Unit,
    val setCurrentListenTasteExcluded: (Boolean) -> Unit = {},
    val setSessionTasteExcluded: (Boolean) -> Unit = {},
    val scheduleSleepTimer: (Long) -> Unit,
    val stopAfterCurrentTrack: () -> Unit,
    val cancelSleepTimer: () -> Unit,
    val observingChanged: (Boolean) -> Unit,
    val queue: QueueEditorUiActions = QueueEditorUiActions(),
    val download: () -> Unit = {},
    val artists: List<app.autplay.application.artist.ArtistSummary> = emptyList(),
    val playlists: List<ManualPlaylistUi> = emptyList(),
    val playlistActions: ManualPlaylistActions = ManualPlaylistActions(),
    val openArtist: (String) -> Unit = {},
    val chooseArtists: (() -> Unit)? = null,
)

internal data class NowPlayingTasteUiState(
    val listenExcluded: Boolean = false,
    val sessionExcluded: Boolean = false,
    val listenActionAvailable: Boolean = false,
    val sessionActionAvailable: Boolean = false,
    val errorCode: String? = null,
)

@Composable
internal fun NowPlayingRouteRenderer(
    state: PlaybackPresentationState,
    feedbackEnabled: Boolean,
    preference: PlaybackPreferenceUiState,
    taste: NowPlayingTasteUiState,
    sleepTimerRemainingMinutes: Int?,
    stopAfterCurrentTrackActive: Boolean,
    queueState: QueueEditorUiState,
    actions: NowPlayingRouteActions,
    modifier: Modifier = Modifier,
    downloadState: String? = null,
    swipeTargets: HomeSwipeTargets = HomeSwipeTargets(),
    onPrevious: () -> Unit = actions.previous,
    onNext: () -> Unit = actions.next,
    serverConnection: PlayerServerConnection = PlayerServerConnection.Offline,
) {
    val network = LocalContext.current.getSystemService(android.net.ConnectivityManager::class.java)
    val online by remember(network) { app.autplay.networkAvailability(network) }
        .collectAsState(initial = network.activeNetwork != null)
    var playlistTrack by rememberSaveable { mutableStateOf<String?>(null) }
    playlistTrack?.let { AddTrackToPlaylistDialog(it, actions.playlists, actions.playlistActions, { playlistTrack = null }) }
    val context = LocalContext.current
    val permission = app.autplay.playback.phoneAudioReadPermission
    var requestedEntry by rememberSaveable { mutableStateOf<String?>(null) }
    val phoneAccess = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val entry = requestedEntry
        requestedEntry = null
        if (granted && entry != null && entry == actions.phoneAudioRetryEntry) actions.retryPhoneAudio(entry)
    }
    val entry = actions.phoneAudioRetryEntry
    NowPlayingScreen(
        state = state,
        requestPhoneAudio = if (entry != null && entry == state.mediaId &&
            context.checkSelfPermission(permission) != android.content.pm.PackageManager.PERMISSION_GRANTED) ({
            requestedEntry = entry
            phoneAccess.launch(permission)
        }) else null,
        artists = actions.artists,
        onAddToPlaylist = state.localTrackRefId?.let { { playlistTrack = it } },
        onOpenArtist = actions.openArtist,
        onChooseArtists = actions.chooseArtists,
        onTogglePlayPause = actions.togglePlayPause,
        onToggleShuffle = actions.toggleShuffle,
        onCycleRepeat = actions.cycleRepeat,
        onPrevious = onPrevious,
        onNext = onNext,
        swipeTargets = swipeTargets,
        onSeekBegin = actions.seekBegin,
        onSeekUpdate = actions.seekUpdate,
        onSeekCommit = actions.seekCommit,
        onLike = actions.like,
        onDislike = actions.dislike,
        preference = preference,
        onClearPreference = actions.clearPreference,
        feedbackEnabled = feedbackEnabled,
        listenExcludedFromTaste = taste.listenExcluded,
        sessionExcludedFromTaste = taste.sessionExcluded,
        listenTasteActionAvailable = taste.listenActionAvailable,
        sessionTasteActionAvailable = taste.sessionActionAvailable,
        tasteExclusionError = taste.errorCode,
        onSetCurrentListenTasteExcluded = actions.setCurrentListenTasteExcluded,
        onSetSessionTasteExcluded = actions.setSessionTasteExcluded,
        sleepTimerRemainingMinutes = sleepTimerRemainingMinutes,
        stopAfterCurrentTrackActive = stopAfterCurrentTrackActive,
        onSetSleepTimer = actions.scheduleSleepTimer,
        onStopAfterCurrentTrack = actions.stopAfterCurrentTrack,
        onCancelSleepTimer = actions.cancelSleepTimer,
        onObservingChanged = actions.observingChanged,
        queueState = queueState,
        queueActions = actions.queue,
        onDownload = actions.download,
        downloadState = downloadState,
        serverConnection = if (online) serverConnection else PlayerServerConnection.Offline,
        modifier = modifier,
    )
}
