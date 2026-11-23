package app.autplay.ui.desktop

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
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
import androidx.compose.material3.ButtonDefaults
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
        IconButton(onClick = { send(DesktopTransportAction.PREVIOUS) }, enabled = controls.previous) {
            DesktopIcon(DesktopGlyph.Previous, DesktopText.Previous.inLanguage(language))
        }
        val playing = input.playback.isPlaying
        val colors = MaterialTheme.colorScheme
        Surface(onClick = { send(if (playing) DesktopTransportAction.PAUSE else DesktopTransportAction.PLAY) },
            enabled = controls.playPause, shape = CircleShape, modifier = Modifier.size(44.dp),
            color = colors.primaryFixedDim, contentColor = colors.onPrimaryFixed) {
            Box(contentAlignment = Alignment.Center) {
                DesktopIcon(if (playing) DesktopGlyph.Pause else DesktopGlyph.Play,
                    (if (playing) DesktopText.Pause else DesktopText.Play).inLanguage(language))
            }
        }
        IconButton(onClick = { send(DesktopTransportAction.NEXT) }, enabled = controls.next) {
            DesktopIcon(DesktopGlyph.Next, DesktopText.Next.inLanguage(language))
        }
    }
}

@Composable
internal fun DesktopLikeButton(track: DesktopTrack, language: DesktopLanguage, enabled: Boolean, change: (String) -> Unit) {
    val liked = track.preference == "LIKED"
    IconButton(enabled = enabled, onClick = { change(if (liked) "NEUTRAL" else "LIKED") }) {
        DesktopIcon(if (liked) DesktopGlyph.Liked else DesktopGlyph.Like,
            if (language == DesktopLanguage.Russian) (if (liked) "Убрать из избранного" else "В избранное")
            else (if (liked) "Remove from favorites" else "Add to favorites"))
    }
}

@Composable
internal fun Modifier.desktopSkipGesture(input: DesktopTransportInput, callbacks: DesktopTransportCallbacks,
    legacy: DesktopPlaybackCallbacks, allowLegacy: Boolean, navigation: () -> Boolean): Modifier {
    val latest by rememberUpdatedState(input)
    val latestNavigation by rememberUpdatedState(navigation)
    val latestCallbacks by rememberUpdatedState(callbacks)
    val latestLegacy by rememberUpdatedState(legacy)
    return pointerInput(input.playback.mediaId, input.target, input.navigationRevision) {
        var distance = 0f
        var captured = latest
        detectHorizontalDragGestures(onDragStart = { distance = 0f; captured = latest },
            onDragCancel = { distance = 0f }, onDragEnd = {
                val threshold = 48.dp.toPx()
                val action = when {
                    distance < -threshold -> DesktopTransportAction.NEXT
                    distance > threshold -> DesktopTransportAction.PREVIOUS
                    else -> null
                }
                if (action != null) dispatchDesktopTransport(captured, latest, action, null, latestNavigation(),
                    allowLegacy, latestCallbacks, latestLegacy)
            }) { change, dx -> change.consume(); distance += dx }
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
    modifier: Modifier = Modifier, smoothTrackTransitions: Boolean = false,
    libraryCallbacks: DesktopLibraryCallbacks = DesktopLibraryCallbacks(), download: (@Composable (String) -> Unit)? = null) {
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
    val ru = language == DesktopLanguage.Russian
    val track = current?.track
    val id = playback.localTrackRefId
    val bitmap = rememberDesktopArtworkBitmap(id, metadata.artworks[id])
    val base = MaterialTheme.colorScheme.background
    val tint by produceState(base, bitmap, base) {
        value = base
        if (bitmap != null) value = withContext(Dispatchers.Default) {
            val pixels = bitmap.toPixelMap()
            var red = 0f
            var green = 0f
            var blue = 0f
            var weight = 0f
            for (y in 0 until 16) for (x in 0 until 16) {
                val c = pixels[x * bitmap.width / 16, y * bitmap.height / 16]
                red += c.red * c.alpha
                green += c.green * c.alpha
                blue += c.blue * c.alpha
                weight += c.alpha
            }
            if (weight == 0f) base else lerp(base, Color(red / weight, green / weight, blue / weight), if (base.luminance() < .5f) .35f else .18f)
        }
    }
    var fullscreen by remember(playback.mediaId) { mutableStateOf(false) }
    val currentOwner by rememberUpdatedState(libraryCallbacks)
    fun changePreference(value: String) {
        if (track != null && currentView === prepared && libraryCallbacks === currentOwner &&
            input.commandsEnabled && desktopPlayerViewIsCurrent(currentPreparation, prepared, currentNavigation())) {
            currentOwner.onSetTrackPreference(track.id, value)
        }
    }
    if (fullscreen && playback.mediaId != null) {
        Dialog(onDismissRequest = { fullscreen = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
            BoxWithConstraints(Modifier.fillMaxSize().background(Color.Black).clickable(role = Role.Button,
                onClickLabel = if (ru) "Закрыть обложку" else "Close artwork") { fullscreen = false },
                contentAlignment = Alignment.Center) {
                DesktopArtworkImage(bitmap, language, minOf(maxWidth, maxHeight), id.orEmpty())
            }
        }
    }
    Column(modifier.background(Brush.verticalGradient(listOf(tint, base))).verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.fillMaxWidth().desktopSkipGesture(input, callbacks, legacy, false, isNavigationCurrent)
            .clickable(enabled = playback.mediaId != null, role = Role.Button,
                onClickLabel = if (ru) "Развернуть обложку" else "Expand artwork") { fullscreen = true }) {
            DesktopPlayerArtworkCarousel(input, prepared, fresh, metadata, language, isNavigationCurrent(),
                smoothTrackTransitions)
        }
        if (playback.mediaId == null) Text(DesktopText.PlayerEmpty.inLanguage(language))
        if (!input.controls(allowLegacy = false).playPause) Text(DesktopText.PlayerReadOnly.inLanguage(language))
        DesktopTransportSeek(input, language, callbacks, legacy, false, isNavigationCurrent)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            val queue = library?.queue
            val modeEnabled = fresh && queue?.id == input.target?.queueId && input.commandsEnabled && isNavigationCurrent()
            IconButton(enabled = modeEnabled && playback.shuffleEnabled, onClick = {
                if (currentView === prepared && desktopPlayerViewIsCurrent(currentPreparation, prepared, currentNavigation()) && libraryCallbacks === currentOwner)
                    currentOwner.onShuffle(queue?.shuffleEnabled != true)
            }) { DesktopIcon(DesktopGlyph.Shuffle, DesktopText.Shuffle.inLanguage(language)) }
            DesktopTransportButtons(input, language, callbacks, legacy, false, isNavigationCurrent, scrollable = false)
            IconButton(enabled = modeEnabled && playback.repeatEnabled, onClick = {
                if (currentView === prepared && desktopPlayerViewIsCurrent(currentPreparation, prepared, currentNavigation()) && libraryCallbacks === currentOwner)
                    currentOwner.onRepeat(when (queue?.repeatMode) { "OFF" -> "ALL"; "ALL" -> "ONE"; else -> "OFF" })
            }) { DesktopIcon(DesktopGlyph.Repeat, (if (ru) "Повтор" else "Repeat") + " · " + queue?.repeatMode.orEmpty()) }
            if (track != null) {
                DesktopLikeButton(track, language, input.commandsEnabled, ::changePreference)
                IconButton(enabled = input.commandsEnabled, onClick = { changePreference(if (track.preference == "DISLIKED") "NEUTRAL" else "DISLIKED") }) {
                    DesktopIcon(DesktopGlyph.Dislike, DesktopText.TrackPreferenceDisliked.inLanguage(language))
                }
            }
        }
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
        if (fresh && track != null && input.commandsEnabled) download?.invoke(track.id)
        if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language))
        DesktopSleepTimerControls(sleepTimer, !input.commandsEnabled, language, sleepTimerCallbacks)
        var tasteExpanded by remember { mutableStateOf(false) }
        TextButton(onClick = { tasteExpanded = !tasteExpanded }) { Text(if (ru) "Учёт прослушивания" else "Listening preferences") }
        if (tasteExpanded) DesktopTasteControls(tasteInput, isTasteNavigationCurrent, language, tasteCallbacks)
    }
}
