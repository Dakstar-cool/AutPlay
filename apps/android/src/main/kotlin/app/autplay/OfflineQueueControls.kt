package app.autplay

import app.autplay.application.playback.NewPlaybackQueueEntry
import app.autplay.application.playback.OrdinaryQueueProjection
import app.autplay.application.playback.QueueEditFailure
import app.autplay.application.playback.QueueEditorRepository
import app.autplay.domain.LocalId
import app.autplay.playback.PlaybackCommand
import app.autplay.playback.PlaybackSessionOwner
import app.autplay.ui.queue.QueueEditorUiActions
import app.autplay.ui.queue.QueueEditorUiEntry
import app.autplay.ui.queue.QueueEditorUiState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

internal data class OfflineQueueControls(
    val state: QueueEditorUiState,
    val actions: QueueEditorUiActions,
    val addTrack: (String, Boolean) -> Unit,
)

/** Keeps queue command construction out of the screen's composition/data-flow graph. */
internal fun buildOfflineQueueControls(
    scope: CoroutineScope,
    queueProjection: () -> OrdinaryQueueProjection?,
    queueEditorRepository: QueueEditorRepository,
    playbackOwner: PlaybackSessionOwner,
    profileId: String?,
    untitledTrack: String,
    reportError: (String) -> Unit,
): OfflineQueueControls {
    val currentQueue = queueProjection()
    val queueEditorState = QueueEditorUiState(
        entries = currentQueue?.entries.orEmpty().map { entry ->
            QueueEditorUiEntry(
                queueEntryId = entry.queueEntryId.value,
                title = entry.title ?: untitledTrack,
                artist = entry.artist,
                isCurrent = entry.isCurrent,
                isUpcoming = entry.isUpcoming,
            )
        },
        editable = currentQueue?.queueType in setOf("USER", "SEARCH", "LIBRARY", "PLAYLIST"),
        canPrevious = currentQueue?.canPrevious == true,
        canNext = currentQueue?.canNext == true,
    )
    fun launchQueueEdit(operation: suspend () -> Unit) {
        scope.launch {
            runCatching { operation() }.onFailure { failure ->
                reportError((failure as? QueueEditFailure)?.code ?: "QUEUE_EDIT_UNAVAILABLE")
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
            val expectedSnapshotId = queueProjection()?.snapshotId
            val result = if (playNext) {
                queueEditorRepository.addNext(entry, profileId, expectedSnapshotId)
            } else {
                queueEditorRepository.addToEnd(entry, profileId, expectedSnapshotId)
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
            queueProjection()?.let { projection ->
                val upcoming = projection.entries.filter { it.isUpcoming }
                val index = upcoming.indexOfFirst { it.queueEntryId.value == entryId }
                val before = upcoming.getOrNull(index - 1)?.queueEntryId
                if (index > 0 && before != null) launchQueueEdit {
                    queueEditorRepository.moveUpcoming(
                        LocalId(entryId), before, profileId, projection.snapshotId,
                    )
                }
            }
        },
        moveDown = { entryId ->
            queueProjection()?.let { projection ->
                val upcoming = projection.entries.filter { it.isUpcoming }
                val index = upcoming.indexOfFirst { it.queueEntryId.value == entryId }
                if (index in 0 until upcoming.lastIndex) launchQueueEdit {
                    queueEditorRepository.moveUpcoming(
                        LocalId(entryId), upcoming.getOrNull(index + 2)?.queueEntryId,
                        profileId, projection.snapshotId,
                    )
                }
            }
        },
        remove = { entryId ->
            queueProjection()?.let { projection ->
                launchQueueEdit {
                    queueEditorRepository.removeUpcoming(
                        LocalId(entryId), profileId, projection.snapshotId,
                    )
                }
            }
        },
        clearUpcoming = {
            queueProjection()?.let { projection ->
                launchQueueEdit {
                    queueEditorRepository.clearUpcoming(profileId, projection.snapshotId)
                }
            }
        },
    )
    return OfflineQueueControls(queueEditorState, queueEditorActions, ::addTrackToQueue)
}
