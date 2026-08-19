package app.autplay.application.playback

import app.autplay.data.local.dao.QueueDao
import app.autplay.data.local.entity.QueueSnapshotEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

/**
 * Small presentation-facing view of the durable active queue.  It deliberately does not expose
 * queue contents: player presentation needs only the identity and mode needed to fail closed.
 */
class ActiveQueueContextRepository(
    queueDao: QueueDao,
    scope: CoroutineScope,
) {
    val context: StateFlow<ActiveQueueContext> = queueDao.activeSnapshot()
        .map(::toContext)
        .catch { emit(ActiveQueueContext.Unavailable) }
        .stateIn(scope, SharingStarted.WhileSubscribed(stopTimeoutMillis = 5_000), ActiveQueueContext.Loading)

    private fun toContext(snapshot: QueueSnapshotEntity?): ActiveQueueContext = snapshot?.let {
        ActiveQueueContext.Loaded(
            snapshotId = it.queueSnapshotId,
            currentEntryId = it.currentEntryId,
            queueType = it.queueType,
        )
    } ?: ActiveQueueContext.Absent
}

sealed interface ActiveQueueContext {
    data object Loading : ActiveQueueContext
    data object Absent : ActiveQueueContext
    data object Unavailable : ActiveQueueContext

    data class Loaded(
        val snapshotId: String,
        val currentEntryId: String?,
        val queueType: String,
    ) : ActiveQueueContext
}
