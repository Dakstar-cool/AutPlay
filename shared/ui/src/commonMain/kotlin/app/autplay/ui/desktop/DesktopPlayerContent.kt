package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.interaction.Interaction
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopSleepTimerState
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackPresentationState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext

/** Preparation keys deliberately exclude position and avoid structural comparison of large collections. */
internal class DesktopPlayerInput(
    val library: DesktopLibrarySnapshot?,
    playback: PlaybackPresentationState,
    val target: DesktopPlaybackTarget?,
    val navigationRevision: Long,
) {
    val mediaId: String? = playback.mediaId
    val trackId: String? = playback.localTrackRefId
    val previousId: String? = playback.previousMediaId
    val nextId: String? = playback.nextMediaId
    val context: ActiveQueueContext = playback.context

    override fun equals(other: Any?): Boolean = other is DesktopPlayerInput && library === other.library &&
        mediaId == other.mediaId && trackId == other.trackId && previousId == other.previousId &&
        nextId == other.nextId && context == other.context && target == other.target &&
        navigationRevision == other.navigationRevision

    override fun hashCode(): Int = 31 * (mediaId?.hashCode() ?: 0) + navigationRevision.hashCode()
}

internal class DesktopPlayerEntry(val entryId: String, val track: DesktopTrack?)

internal class DesktopPlayerView(
    val input: DesktopPlayerInput? = null,
    val previous: DesktopPlayerEntry? = null,
    val current: DesktopPlayerEntry? = null,
    val next: DesktopPlayerEntry? = null,
)

/** Actor-supplied neighbor IDs are authoritative, including repeat/shuffle and duplicate tracks. */
internal fun prepareDesktopPlayerView(input: DesktopPlayerInput, checkpoint: () -> Unit = {}): DesktopPlayerView {
    checkpoint()
    val library = input.library ?: return DesktopPlayerView(input)
    val queue = library.queue ?: return DesktopPlayerView(input)
    val context = input.context as? ActiveQueueContext.Loaded ?: return DesktopPlayerView(input)
    if (queue.entries.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES || queue.id != context.snapshotId ||
        queue.currentEntryId != input.mediaId || context.currentEntryId != input.mediaId || input.mediaId == null) {
        return DesktopPlayerView(input)
    }
    val wanted = setOfNotNull(input.previousId, input.mediaId, input.nextId)
    val entries = mutableMapOf<String, String>()
    queue.entries.forEachIndexed { index, entry ->
        if (index % 128 == 0) checkpoint()
        if (entry.id in wanted) {
            if (entries.put(entry.id, entry.trackId) != null) return DesktopPlayerView(input)
        }
    }
    val currentTrackId = entries[input.mediaId] ?: return DesktopPlayerView(input)
    if (currentTrackId != input.trackId) return DesktopPlayerView(input)
    val trackIds = entries.values.toSet()
    val tracks = mutableMapOf<String, DesktopTrack>()
    val collection = library.tracks
    for (index in collection.indices) {
        if (index % 256 == 0) checkpoint()
        val track = collection[index]
        if (track.id in trackIds) tracks[track.id] = track
        if (tracks.size == trackIds.size) break
    }
    checkpoint()
    fun entry(id: String?): DesktopPlayerEntry? = id?.let { entryId ->
        entries[entryId]?.let { DesktopPlayerEntry(entryId, tracks[it]) }
    }
    return DesktopPlayerView(input, entry(input.previousId), entry(input.mediaId), entry(input.nextId))
}

internal fun desktopPlayerViewIsCurrent(input: DesktopPlayerInput, view: DesktopPlayerView,
    navigationCurrent: Boolean): Boolean = navigationCurrent && view.input == input

@Composable
internal fun DesktopTransportButtons(input: DesktopTransportInput, language: DesktopLanguage,
    callbacks: DesktopTransportCallbacks, legacy: DesktopPlaybackCallbacks, allowLegacy: Boolean,
    isNavigationCurrent: () -> Boolean, scrollable: Boolean = true) {
    val currentInput by rememberUpdatedState(input)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val currentLegacy by rememberUpdatedState(legacy)
    val currentNavigation by rememberUpdatedState(isNavigationCurrent)
    val controls = input.controls(allowLegacy)
    val send: (DesktopTransportAction) -> Unit = { action ->
        dispatchDesktopTransport(input, currentInput, action, null, currentNavigation(), allowLegacy,
            currentCallbacks, currentLegacy)
    }
    val modifier = if (scrollable) Modifier.horizontalScroll(rememberScrollState()) else Modifier
    Row(modifier, verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedButton(onClick = { send(DesktopTransportAction.PREVIOUS) }, enabled = controls.previous) {
            Text(DesktopText.Previous.inLanguage(language))
        }
        val action = if (input.playback.isPlaying) DesktopTransportAction.PAUSE else DesktopTransportAction.PLAY
        Button(onClick = { send(action) }, enabled = controls.playPause) {
            Text((if (input.playback.isPlaying) DesktopText.Pause else DesktopText.Play).inLanguage(language))
        }
        OutlinedButton(onClick = { send(DesktopTransportAction.NEXT) }, enabled = controls.next) {
            Text(DesktopText.Next.inLanguage(language))
        }
    }
}

/** Cancel must retire the draft before buffered observers or Slider completion can run. */
internal class DesktopSeekInteractionSource(
    private val delegate: MutableInteractionSource,
    private val onCancel: () -> Unit,
) : MutableInteractionSource {
    override val interactions: Flow<Interaction> get() = delegate.interactions

    override suspend fun emit(interaction: Interaction): Unit {
        if (interaction is DragInteraction.Cancel) onCancel()
        delegate.emit(interaction)
    }

    override fun tryEmit(interaction: Interaction): Boolean {
        if (interaction is DragInteraction.Cancel) onCancel()
        return delegate.tryEmit(interaction)
    }
}

@Composable
internal fun DesktopTransportSeek(input: DesktopTransportInput, language: DesktopLanguage,
    callbacks: DesktopTransportCallbacks, legacy: DesktopPlaybackCallbacks, allowLegacy: Boolean,
    isNavigationCurrent: () -> Boolean) {
    val currentInput by rememberUpdatedState(input)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val currentLegacy by rememberUpdatedState(legacy)
    val currentNavigation by rememberUpdatedState(isNavigationCurrent)
    val duration = input.playback.durationMs?.takeIf { it > 0 }
    val position = input.playback.positionMs.coerceAtLeast(0).let { if (duration != null) it.coerceAtMost(duration) else it }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(formatPlaybackTime(position), modifier = Modifier.semantics {
            contentDescription = "${DesktopText.Elapsed.inLanguage(language)} ${formatPlaybackTime(position)}"
        }, style = MaterialTheme.typography.labelMedium)
        key(DesktopSeekLifetime(input, allowLegacy)) {
            val gesture = remember { DesktopSeekGesture() }
            var draft by remember { mutableStateOf<DesktopSeekDraft?>(null) }
            val interactions = remember {
                DesktopSeekInteractionSource(MutableInteractionSource()) {
                    gesture.cancel()
                    draft = null
                }
            }
            DisposableEffect(gesture) { onDispose { gesture.retire() } }
            // Stop precedes completion; press cancellation may start a drag. Neither cancels it.
            val visibleDraft = draft?.takeIf { it.matches(input, isNavigationCurrent(), allowLegacy) }
            Slider(value = visibleDraft?.fraction ?: if (duration != null) (position.toDouble() / duration).toFloat() else 0f,
                onValueChange = { fraction ->
                    draft = gesture.change(input, currentInput, fraction, currentNavigation(), allowLegacy)
                },
                onValueChangeFinished = {
                    val finished = gesture.finish(currentInput, currentNavigation(), allowLegacy)
                    draft = null
                    finished?.positionMs()?.let { positionMs ->
                        dispatchDesktopTransport(finished.input, currentInput, DesktopTransportAction.SEEK,
                            positionMs, currentNavigation(), allowLegacy, currentCallbacks, currentLegacy)
                    }
                }, enabled = input.controls(allowLegacy).seek, interactionSource = interactions,
                modifier = Modifier.weight(1f).semantics { contentDescription = DesktopText.Seek.inLanguage(language) })
        }
        val durationText = duration?.let(::formatPlaybackTime) ?: "--:--"
        Text(durationText, modifier = Modifier.semantics {
            contentDescription = "${DesktopText.Duration.inLanguage(language)} $durationText"
        }, style = MaterialTheme.typography.labelMedium)
    }
}

/** Entering this screen prepares immutable UI data only; every command requires an explicit gesture. */
@Composable
internal fun DesktopPlayerContent(library: DesktopLibrarySnapshot?, input: DesktopTransportInput,
    language: DesktopLanguage, callbacks: DesktopTransportCallbacks, legacy: DesktopPlaybackCallbacks,
    metadata: DesktopMetadataState, sleepTimer: DesktopSleepTimerState, sleepTimerCallbacks: DesktopSleepTimerCallbacks,
    tasteInput: DesktopTasteInput, tasteCallbacks: DesktopTasteCallbacks, isNavigationCurrent: () -> Boolean,
    isTasteNavigationCurrent: () -> Boolean, onTrackDetails: (String) -> Unit, onQueue: () -> Unit,
    modifier: Modifier = Modifier, smoothTrackTransitions: Boolean = false) {
    val preparation = DesktopPlayerInput(library, input.playback, input.target, input.navigationRevision)
    val currentPreparation by rememberUpdatedState(preparation)
    val currentNavigation by rememberUpdatedState(isNavigationCurrent)
    val currentDetails by rememberUpdatedState(onTrackDetails)
    val currentQueue by rememberUpdatedState(onQueue)
    val view by produceState(DesktopPlayerView(), preparation) {
        value = withContext(Dispatchers.Default) {
            val context = currentCoroutineContext()
            prepareDesktopPlayerView(preparation) { context.ensureActive() }
        }
    }
    val prepared = view
    val currentView by rememberUpdatedState(prepared)
    val fresh = desktopPlayerViewIsCurrent(preparation, prepared, isNavigationCurrent())
    val current = prepared.current.takeIf { fresh }
    val playback = input.playback
    Column(modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        DesktopPlayerArtworkCarousel(input, prepared, fresh, metadata, language, isNavigationCurrent(),
            smoothTrackTransitions)
        if (playback.mediaId == null) Text(DesktopText.PlayerEmpty.inLanguage(language))
        if (!input.controls(allowLegacy = false).playPause) Text(DesktopText.PlayerReadOnly.inLanguage(language))
        DesktopTransportButtons(input, language, callbacks, legacy, false, isNavigationCurrent)
        DesktopTransportSeek(input, language, callbacks, legacy, false, isNavigationCurrent)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = {
                if (currentView === prepared && desktopPlayerViewIsCurrent(currentPreparation, prepared, currentNavigation())) {
                    current?.track?.let { currentDetails(it.id) }
                }
            }, enabled = current?.track != null) { Text(DesktopText.TrackDetails.inLanguage(language)) }
            OutlinedButton(onClick = { if (currentNavigation()) currentQueue() }) {
                Text(DesktopText.OpenQueue.inLanguage(language))
            }
        }
        if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language))
        DesktopSleepTimerControls(sleepTimer, !input.commandsEnabled, language, sleepTimerCallbacks)
        DesktopTasteControls(tasteInput, isTasteNavigationCurrent, language, tasteCallbacks)
    }
}
