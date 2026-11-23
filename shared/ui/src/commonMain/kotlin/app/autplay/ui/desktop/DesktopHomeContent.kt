package app.autplay.ui.desktop

import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.geometry.Offset
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.IconButton
import androidx.compose.material3.TextButton
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Surface
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import app.autplay.application.desktop.DesktopMetadataState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackStatus
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

internal expect fun desktopWaveTracks(library: DesktopLibrarySnapshot): List<String>

internal class DesktopHomeInput(val library: DesktopLibrarySnapshot?) {
    override fun equals(other: Any?): Boolean = other is DesktopHomeInput && library === other.library
    override fun hashCode(): Int = 0
}

internal class DesktopHomeRecent(val event: DesktopListeningEvent, val track: DesktopTrack?)

internal class DesktopHomeView(
    val input: DesktopHomeInput? = null,
    val currentEntry: DesktopQueueEntry? = null,
    val currentTrack: DesktopTrack? = null,
    val hasActiveTracks: Boolean = false,
    val recent: List<DesktopHomeRecent> = emptyList(),
    val waveIds: List<String> = emptyList(),
)

/** No playback-position input: preparation runs only for a newly published snapshot reference. */
internal fun prepareDesktopHomeView(input: DesktopHomeInput, checkpoint: () -> Unit = {}): DesktopHomeView {
    val tracks = mutableMapOf<String, DesktopTrack>()
    var hasActive = false
    input.library?.tracks?.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        tracks[track.id] = track
        if (desktopTrackIsActive(track)) hasActive = true
    }
    val queue = input.library?.queue
    var entry: DesktopQueueEntry? = null
    queue?.entries?.forEachIndexed { index, candidate ->
        if (index % 256 == 0) checkpoint()
        if (candidate.id == queue.currentEntryId) entry = candidate
    }
    val recent = mutableListOf<DesktopHomeRecent>()
    val ids = mutableSetOf<String>()
    input.library?.history?.take(DesktopHistoryLimits.PREVIEW_SIZE)?.forEachIndexed { index, event ->
        if (index % 32 == 0) checkpoint()
        if (recent.size < 6 && ids.add(event.id)) recent += DesktopHomeRecent(event, tracks[event.trackId])
    }
    checkpoint()
    return DesktopHomeView(input, entry, entry?.trackId?.let { tracks[it] }, hasActive, recent, input.library?.let(::desktopWaveTracks).orEmpty())
}

/** Resume an existing retained queue, including removed tracks, without selecting a new queue. */
internal fun desktopHomeCanContinue(
    input: DesktopHomeInput,
    view: DesktopHomeView,
    playback: PlaybackPresentationState,
    adapterAvailable: Boolean,
    reopenRequired: Boolean,
): Boolean {
    val queue = input.library?.queue ?: return false
    val entry = view.currentEntry ?: return false
    val context = playback.context as? ActiveQueueContext.Loaded ?: return false
    return view.input == input && !reopenRequired && !playback.isPlaying && playback.playbackStatus != PlaybackStatus.Ended &&
        view.currentTrack != null && queue.currentEntryId == entry.id && playback.localTrackRefId == entry.trackId && playback.mediaId == entry.id &&
        context.snapshotId == queue.id && context.currentEntryId == entry.id && context.queueType == queue.queueType &&
        queue.queueType in setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST") && queue.repeatMode in setOf("OFF", "ONE", "ALL") &&
        DesktopPlaybackControls.from(playback, adapterAvailable).playPause
}

internal fun desktopHomeCanImport(library: DesktopLibrarySnapshot?, importing: Boolean, reopenRequired: Boolean): Boolean =
    library != null && !importing && !reopenRequired

/** Existing import cancellation remains available while the library requires restart. */
internal fun desktopHomeCanCancelImport(library: DesktopLibrarySnapshot?, importing: Boolean): Boolean =
    library != null && importing

/** A rendered Continue action retires after navigation, snapshot replacement or owner replacement. */
internal class DesktopHomeContinuation(
    val input: DesktopHomeInput,
    val view: DesktopHomeView,
    val playback: PlaybackPresentationState,
) {
    fun canAct(currentInput: DesktopHomeInput, currentView: DesktopHomeView, currentPlayback: PlaybackPresentationState,
        adapterAvailable: Boolean, reopenRequired: Boolean, navigationCurrent: Boolean): Boolean =
        navigationCurrent && input == currentInput && view === currentView &&
            playback.context == currentPlayback.context && playback.mediaId == currentPlayback.mediaId &&
            desktopHomeCanContinue(currentInput, currentView, currentPlayback, adapterAvailable, reopenRequired)

    /** The serial owner must revalidate these displayed identities after any already queued commands. */
    fun continueQueue(currentInput: DesktopHomeInput, currentView: DesktopHomeView, currentPlayback: PlaybackPresentationState,
        adapterAvailable: Boolean, reopenRequired: Boolean, navigationCurrent: Boolean, callbacks: DesktopLibraryCallbacks): Unit {
        if (!canAct(currentInput, currentView, currentPlayback, adapterAvailable, reopenRequired, navigationCurrent)) return
        val queue = input.library?.queue ?: return
        val entryId = queue.currentEntryId ?: return
        callbacks.onContinueQueue(queue.id, entryId, queue.sessionId)
    }
}

@Composable
internal fun DesktopHomeContent(
    library: DesktopLibrarySnapshot?,
    playback: PlaybackPresentationState,
    adapterAvailable: Boolean,
    language: DesktopLanguage,
    callbacks: DesktopLibraryCallbacks,
    importing: Boolean,
    importResult: DesktopImportResult?,
    errorCode: String?,
    reopenRequired: Boolean,
    isNavigationCurrent: () -> Boolean,
    onNavigate: (DesktopSection) -> Unit,
    onTrackDetails: (String) -> Unit,
    modifier: Modifier = Modifier,
    importActionLabel: String? = null,
    importCancelEnabled: Boolean = importing,
    accountContent: (@Composable () -> Unit)? = null,
    homeAlbums: (@Composable () -> Unit)? = null,
    homeWheel: (@Composable () -> Unit)? = null,
    playingTrack: DesktopTrack? = null,
    toggleEnabled: Boolean = false,
    togglePlayback: () -> Unit = {},
    preferTrack: (String, String) -> Unit = { _, _ -> },
    artworkGesture: Modifier = Modifier,
    metadata: DesktopMetadataState = DesktopMetadataState(),
) {
    val input = DesktopHomeInput(library)
    val currentInput by rememberUpdatedState(input)
    val currentPlayback by rememberUpdatedState(playback)
    val currentAdapter by rememberUpdatedState(adapterAvailable)
    val currentReopen by rememberUpdatedState(reopenRequired)
    val currentImporting by rememberUpdatedState(importing)
    val currentImportCancelEnabled by rememberUpdatedState(importCancelEnabled)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val view by produceState(DesktopHomeView(), input) {
        value = withContext(Dispatchers.Default) {
            val context = currentCoroutineContext()
            prepareDesktopHomeView(input) { context.ensureActive() }
        }
    }
    val prepared = view
    val currentPrepared by rememberUpdatedState(prepared)
    val fresh = prepared.input == input
    val isViewCurrent: () -> Boolean = {
        isNavigationCurrent() && currentPrepared === prepared && prepared.input == currentInput
    }
    val canContinue = desktopHomeCanContinue(input, prepared, playback, adapterAvailable, reopenRequired)
    val continuation = DesktopHomeContinuation(input, prepared, playback)
    Column(modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        val ru = language == DesktopLanguage.Russian
        val colors = MaterialTheme.colorScheme
        val enabled = fresh && prepared.waveIds.isNotEmpty() && !reopenRequired && adapterAvailable &&
            playback.connection == PlaybackConnectionState.Connected
        var played by remember(playback.mediaId) { mutableStateOf(false) }
        LaunchedEffect(playback.mediaId, playback.isPlaying) { if (playback.isPlaying) played = true }
        val showCover = playback.mediaId != null && (playback.isPlaying || played)
        val startWave = {
            if (isViewCurrent() && !currentReopen && currentAdapter &&
                currentPlayback.connection == PlaybackConnectionState.Connected && prepared.waveIds.isNotEmpty()) {
                currentCallbacks.onPlayTracks(prepared.waveIds, "USER")
            }
        }
        Column(Modifier.fillMaxWidth().waveGlow(
            if (showCover) colors.secondary else Color(0xFFD26539),
            if (showCover) colors.tertiary else Color(0xFFB85083), colors.background).padding(22.dp),
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(if (showCover) (if (ru) "Сейчас играет" else "Now playing")
                else (if (ru) "На устройстве" else "On this device"), style = MaterialTheme.typography.labelMedium)
            Text(if (showCover) playback.artist.orEmpty() else (if (ru) "Моя волна" else "My wave"),
                style = MaterialTheme.typography.displaySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (showCover) {
                Box(artworkGesture.clickable { if (isNavigationCurrent()) onNavigate(DesktopSection.Player) }) {
                    DesktopArtworkView(playback.localTrackRefId, metadata.artworks[playback.localTrackRefId], language, 164.dp)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    IconButton(enabled = toggleEnabled, onClick = togglePlayback) {
                        DesktopIcon(if (playback.isPlaying) DesktopGlyph.Pause else DesktopGlyph.Play,
                            (if (playback.isPlaying) DesktopText.Pause else DesktopText.Play).inLanguage(language))
                    }
                    Text(playback.title.orEmpty(), Modifier.widthIn(max = 360.dp), maxLines = 2,
                        overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleLarge)
                    if (playingTrack != null) DesktopLikeButton(playingTrack, language, !reopenRequired) {
                        if (isNavigationCurrent() && currentPlayback.localTrackRefId == playingTrack.id) preferTrack(playingTrack.id, it)
                    }
                }
                OutlinedButton(enabled = enabled, onClick = startWave) { Text(if (ru) "Включить мою волну" else "Start my wave") }
            } else {
                Surface(onClick = startWave, enabled = enabled, shape = CircleShape, color = colors.primary,
                    contentColor = colors.onPrimary, modifier = Modifier.size(84.dp)) {
                    Box(contentAlignment = Alignment.Center) { DesktopIcon(DesktopGlyph.Play, if (ru) "Включить мою волну" else "Start my wave") }
                }
                if (fresh && prepared.waveIds.isEmpty()) Text(if (ru) "Нет подходящих локальных треков" else "No eligible local tracks",
                    style = MaterialTheme.typography.bodySmall)
            }
        }
        homeWheel?.invoke()
        if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        if (fresh && prepared.currentTrack != null) {
            val track = prepared.currentTrack
            Text(DesktopText.ContinueListening.inLanguage(language), style = MaterialTheme.typography.titleLarge)
            Surface(shape = MaterialTheme.shapes.medium, color = colors.surfaceContainer) {
                Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    DesktopArtworkView(track.id, metadata.artworks[track.id], language, 64.dp)
                    Column(Modifier.weight(1f).clickable { if (isViewCurrent()) onTrackDetails(track.id) }) {
                        Text(track.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                        Text(track.artist, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    if (playback.isPlaying) {
                        OutlinedButton(onClick = { if (isViewCurrent()) onNavigate(DesktopSection.Player) }) { Text(DesktopText.OpenPlayer.inLanguage(language)) }
                    } else Button(onClick = {
                        continuation.continueQueue(currentInput, currentPrepared, currentPlayback, currentAdapter, currentReopen,
                            isNavigationCurrent(), currentCallbacks)
                    }, enabled = canContinue) { Text(DesktopText.Play.inLanguage(language)) }
                }
            }
        }
        HorizontalDivider()
        if (fresh && library != null && !prepared.hasActiveTracks) {
            Text(DesktopText.HomeWelcome.inLanguage(language), style = MaterialTheme.typography.titleLarge)
            Text(DesktopText.HomeOffline.inLanguage(language))
        }
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                if (isNavigationCurrent() && input == currentInput && desktopHomeCanImport(currentInput.library, currentImporting, currentReopen)) {
                    callbacks.onImport()
                }
            }, enabled = desktopHomeCanImport(library, importing, reopenRequired)) {
                Text(importActionLabel ?: (if (importing) DesktopText.Importing else DesktopText.Import).inLanguage(language))
            }
            if (importing) OutlinedButton(onClick = {
                if (isNavigationCurrent() && input == currentInput && currentImportCancelEnabled &&
                    desktopHomeCanCancelImport(currentInput.library, currentImporting)) {
                    callbacks.onCancelImport()
                }
            }, enabled = importCancelEnabled && desktopHomeCanCancelImport(library, importing)) { Text(DesktopText.CancelImport.inLanguage(language)) }
        }
        if (!reopenRequired) {
            errorCode?.let { Text(desktopErrorMessage(it, language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite }) }
        }
        if (!importing) importResult?.let { result ->
            if (!reopenRequired || desktopImportWasCancelled(result, errorCode)) {
                Text(desktopImportResultSummary(result, errorCode, language),
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                result.errorCode?.takeUnless { it == errorCode }?.let { Text(desktopErrorMessage(it, language)) }
            }
        }
        if (accountContent != null) accountContent()
        else Text(DesktopText.HomeServerUnavailable.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        homeAlbums?.invoke()
        if (fresh && prepared.recent.isNotEmpty()) {
            Text(DesktopText.HomeRecent.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            prepared.recent.forEach { row ->
                key(row.event.id) {
                    val track = row.track
                    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        DesktopArtworkView(track?.id, metadata.artworks[track?.id], language, 44.dp)
                        Column(Modifier.weight(1f).clickable { if (isViewCurrent()) onTrackDetails(row.event.trackId) }) {
                            Text(track?.title ?: DesktopText.TrackUnavailable.inLanguage(language), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(track?.artist.orEmpty(), maxLines = 1, style = MaterialTheme.typography.bodySmall)
                        }
                        IconButton(enabled = !reopenRequired && adapterAvailable &&
                            playback.connection == PlaybackConnectionState.Connected && desktopTrackIsActive(track),
                            onClick = {
                                if (isViewCurrent() && !currentReopen && currentAdapter && desktopTrackIsActive(track))
                                    currentCallbacks.onPlayTracks(listOf(row.event.trackId), "LIBRARY")
                            }) { DesktopIcon(DesktopGlyph.Play, DesktopText.Play.inLanguage(language)) }
                    }
                }
            }
        }
    }
}

private fun Modifier.waveGlow(accent: Color, secondary: Color, base: Color): Modifier = drawWithCache {
    val intensity = if (base.luminance() < .5f) .74f else .36f
    val warm = Brush.radialGradient(
        0f to accent.copy(alpha = intensity), .34f to accent.copy(alpha = intensity * .76f), 1f to accent.copy(alpha = 0f),
        center = Offset(size.width * .38f, size.height * .53f), radius = size.minDimension * .52f,
    )
    val halo = Brush.radialGradient(
        listOf(secondary.copy(alpha = intensity * .72f), secondary.copy(alpha = 0f)),
        center = Offset(size.width * .67f, size.height * .40f), radius = size.minDimension * .54f,
    )
    val edge = Brush.verticalGradient(0f to base, .16f to base.copy(alpha = 0f),
        .82f to base.copy(alpha = 0f), 1f to base)
    onDrawBehind {
        drawRect(base)
        drawRect(halo)
        drawRect(warm)
        drawRect(edge)
    }
}
