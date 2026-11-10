package app.autplay.desktop.lifecycle

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** One process-owned close attempt, independent of window/composition cancellation. */
public class DesktopShutdownOwner(
    private val closeAdmission: () -> Unit,
    closeRuntime: suspend () -> Unit,
    reportFailure: () -> Unit,
) {
    private val lock: Any = Any()
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutableRequested: MutableStateFlow<Boolean> = MutableStateFlow(false)
    public val requested: StateFlow<Boolean> = mutableRequested
    private var admissionFailure: Throwable? = null
    private val attempt: Deferred<Result<Unit>> = scope.async(start = CoroutineStart.LAZY) {
        // Observation deadlines belong to callers; retain the actual close result to completion.
        val closed = runCatching { closeRuntime() }
        // An admission failure must not skip cleanup or be reported as successful cleanup.
        val result = admissionFailure?.let { Result.failure<Unit>(it) } ?: closed
        if (result.isFailure) reportFailure()
        result
    }.also { deferred -> deferred.invokeOnCompletion { scope.cancel() } }

    /** The barrier runs synchronously, before scheduling IO or returning to a native/UI caller. */
    public fun request(): Deferred<Result<Unit>> = synchronized(lock) {
        if (!mutableRequested.value) {
            admissionFailure = runCatching { closeAdmission() }.exceptionOrNull()
            mutableRequested.value = true
            attempt.start()
        }
        attempt
    }
}
