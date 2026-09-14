package app.autplay.application.importing

import android.os.CancellationSignal
import java.io.Closeable
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.FutureTask
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull

/** Bounds stuck providers independently of service lifetime; cancellation also reaches Binder. */
internal object CancellableContentProbe {
    private val executor = ThreadPoolExecutor(2, 2, 0, TimeUnit.SECONDS, ArrayBlockingQueue(32))

    suspend fun inspect(read: (CancellationSignal, (Closeable?) -> Unit) -> ContentUriInspection): ContentUriInspection? =
        withTimeoutOrNull(2_000) {
            suspendCancellableCoroutine { continuation ->
                val signal = CancellationSignal()
                val opened = AtomicReference<Closeable?>()
                val task = FutureTask {
                    val outcome = runCatching {
                        signal.throwIfCanceled()
                        read(signal) { resource ->
                            opened.set(resource)
                            if (signal.isCanceled) opened.getAndSet(null)?.close()
                        }
                    }
                    opened.getAndSet(null)?.let { runCatching { it.close() } }
                    continuation.resumeWith(outcome)
                }
                continuation.invokeOnCancellation {
                    task.cancel(true)
                    executor.remove(task)
                    signal.cancel()
                    opened.getAndSet(null)?.let { runCatching { it.close() } }
                }
                try {
                    executor.execute(task)
                } catch (_: RejectedExecutionException) {
                    continuation.resumeWith(Result.success(null))
                }
            }
        }
}
