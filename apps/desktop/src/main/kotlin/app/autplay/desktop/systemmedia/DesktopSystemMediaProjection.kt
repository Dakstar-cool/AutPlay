package app.autplay.desktop.systemmedia

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.validDesktopEmbeddedText
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.PlaybackStatus
import app.autplay.playback.presentation.canSeek
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Pure display join. The controller's coherent entry map, never the persisted queue, owns identity. */
internal fun projectDesktopSystemMedia(
    player: DesktopPlayerState,
    library: DesktopLibrarySnapshot,
    metadata: DesktopMetadataState,
    playbackError: String?,
    hostError: String?,
): DesktopSystemMediaSnapshot? {
    if (hostError == "desktop_snapshot_unavailable" || playbackError == "desktop_snapshot_unavailable" ||
        playbackError?.startsWith("AUDIO_") == true ||
        playbackError in setOf("SOURCE_UNAVAILABLE", "PLAYBACK_OPERATION_FAILED")) return null
    val target = player.target ?: return null
    val playback = player.playback
    val editor = player.queueEditor
    val editorTarget = editor.target ?: return null
    val context = playback.context as? ActiveQueueContext.Loaded ?: return null
    if (target.generation <= 0 || context.snapshotId != target.queueId || context.currentEntryId != target.entryId ||
        playback.mediaId != target.entryId || editor.currentEntryId != target.entryId ||
        editorTarget.queueId != target.queueId || editorTarget.currentEntryId != target.entryId ||
        editorTarget.sessionId != target.sessionId || editorTarget.generation != target.generation ||
        playback.connection != PlaybackConnectionState.Connected || playback.source != PlaybackSourcePresentation.Local ||
        playback.playbackStatus !in setOf(PlaybackStatus.Ready, PlaybackStatus.Ended)) return null
    val entry = editor.entries.singleOrNull { it.id == target.entryId } ?: return null
    if (entry.trackId != playback.localTrackRefId) return null
    val track = library.tracks.singleOrNull { it.id == entry.trackId } ?: return null
    if (!validDesktopEmbeddedText(track.title, allowBlank = false) ||
        !validDesktopEmbeddedText(track.artist, allowBlank = true)) return null
    // isSeekable is derived by the controller from current native duration. The library fallback
    // duration alone (including imported/header metadata) cannot establish a current execution fact.
    val duration = playback.durationMs?.takeIf { playback.isSeekable && it > 0 }
    if (playback.positionMs !in 0..DesktopSystemMediaContract.MAX_POSITION_MS ||
        (duration != null && (duration > DesktopSystemMediaContract.MAX_POSITION_MS || playback.positionMs > duration))) return null
    var commands = 0
    if (playback.controls == PlaybackControlGate.Allowed) {
        commands = DesktopSystemMediaContract.PLAY or DesktopSystemMediaContract.PAUSE
        if (playback.previousMediaId != null ||
            (playback.canSeek && playback.positionMs > 3_000 && playback.playbackStatus != PlaybackStatus.Ended)) {
            commands = commands or DesktopSystemMediaContract.PREVIOUS
        }
        if (playback.nextMediaId != null) commands = commands or DesktopSystemMediaContract.NEXT
        if (playback.canSeek && duration != null) commands = commands or DesktopSystemMediaContract.SEEK
    }
    return DesktopSystemMediaSnapshot(target, track.title, track.artist, playback.positionMs, duration,
        playback.isPlaying, commands, metadata.artworks[entry.trackId])
}

/** One retained host publisher. No media reads, audio owner, AWT calls or unbounded command queue. */
public class DesktopSystemMediaProjection(
    api: DesktopSystemMediaApi?,
    scope: CoroutineScope,
    private val player: StateFlow<DesktopPlayerState>,
    private val library: StateFlow<DesktopLibrarySnapshot>,
    private val metadata: StateFlow<DesktopMetadataState>,
    private val playbackError: StateFlow<String?>,
    private val hostError: StateFlow<String?>,
    private val canPublish: () -> Boolean,
    onCommand: (DesktopSystemMediaCommand) -> Boolean,
) {
    private val publicationLock: Any = Any()
    private val bridge: DesktopSystemMediaOwner = DesktopSystemMediaOwner(api, onCommand)
    private var retired: Boolean = false
    private var refused: Boolean = false
    public val state: DesktopSystemMediaState get() = bridge.state
    // UNDISPATCHED enters try/finally even for an already cancelled startup scope. The actual
    // collector switches to IO; acquisition happens only after the application's window exists.
    private val publisher: Job = scope.launch(start = CoroutineStart.UNDISPATCHED) {
        try {
            withContext(Dispatchers.IO) {
                combine(player, library, metadata, playbackError, hostError) { _, _, _, _, _ -> Unit }
                    .collect { publishLatest() }
            }
        } catch (failure: CancellationException) {
            throw failure
        } catch (_: Exception) {
            // Optional integration failure never fails the host's playback/controller scope.
        } finally {
            retire()
            bridge.close()
        }
    }

    private fun publishLatest(): Unit {
        // Ignore combine's carried values: they may have waited behind a newer player update.
        val capturedPlayer = player.value
        val capturedLibrary = library.value
        val capturedMetadata = metadata.value
        val capturedPlaybackError = playbackError.value
        val capturedHostError = hostError.value
        val snapshot = projectDesktopSystemMedia(capturedPlayer, capturedLibrary, capturedMetadata,
            capturedPlaybackError, capturedHostError)
        synchronized(publicationLock) {
            if (retired) return
            if (refused || !canPublish()) { bridge.publish(null); return }
            // Only this collector publishes non-null values. Reference rechecks fence metadata
            // work across target/entry/session/generation replacement, including A-B-A changes.
            if (player.value !== capturedPlayer || library.value !== capturedLibrary || metadata.value !== capturedMetadata ||
                playbackError.value != capturedPlaybackError || hostError.value != capturedHostError) return
            bridge.publish(snapshot)
        }
    }

    /** Sticky storage refusal clears synchronously, even while the collector is sampling inputs. */
    public fun refuse(): Unit = synchronized(publicationLock) {
        refused = true
        if (!retired) bridge.publish(null)
        Unit
    }

    /** Must run synchronously from host beginClose, before cancelling any host-owned jobs. */
    public fun retire(): Unit = synchronized(publicationLock) {
        retired = true
        bridge.retire()
    }

    /** The collector's finally owns bounded, cancellation-resistant native disposal on IO. */
    public suspend fun close(): Unit = withContext(kotlinx.coroutines.NonCancellable + Dispatchers.IO) {
        retire()
        publisher.cancelAndJoin()
        check(bridge.state.closed) { "DESKTOP_SYSTEM_MEDIA_CLOSE_FAILED" }
    }
}
