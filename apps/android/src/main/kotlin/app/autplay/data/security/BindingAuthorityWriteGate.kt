package app.autplay.data.security

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Process-wide fence for writes that can create, rotate or erase active server authority.
 * Recommendation pack commits share it so no pre-unbinding response can resurrect local context.
 */
object BindingAuthorityWriteGate {
    private val mutex = Mutex()

    suspend fun <T> serialized(block: suspend () -> T): T = mutex.withLock { block() }
}
