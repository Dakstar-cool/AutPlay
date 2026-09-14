package app.autplay.playback

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Main publishes teardown work; a successor service waits asynchronously before restoring Room. */
internal object PlaybackShutdownPersistence {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var pending: Job? = null

    fun enqueue(cancelledService: Job, persist: suspend () -> Unit) {
        val previous = pending
        pending = scope.launch {
            previous?.join()
            cancelledService.join()
            runCatching { persist() }
        }
    }

    suspend fun awaitPending() { pending?.join() }
}
