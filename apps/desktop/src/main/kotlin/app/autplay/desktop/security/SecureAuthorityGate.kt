package app.autplay.desktop.security

import app.autplay.application.securestate.SecureStateStatus
import app.autplay.application.securestate.secureRequire

/**
 * One non-thread-affine admission barrier used by K and the approved concrete J
 * capture. Acquisition never waits; a capture keeps its permit through actual settlement
 * and native descriptors borrow it. This primitive establishes no store association or
 * witness by itself. Cancellation cannot release a running borrow.
 */
internal class SecureAuthorityGate {
    private val monitor: Any = Any()
    private val submitted: ThreadLocal<Permit?> = ThreadLocal()
    private var current: Permit? = null
    private var retired: Boolean = false
    private var idle: (() -> Unit)? = null

    internal class Permit internal constructor(internal val gate: SecureAuthorityGate) : AutoCloseable {
        internal var references: Int = 1
        internal var released: Boolean = false
        override fun close(): Unit = gate.release(this)
    }

    fun tryAcquire(): Permit? = synchronized(monitor) {
        if (retired || current != null) null else Permit(this).also { current = it }
    }

    /** Called at submission, before the descriptor can reach the native executor. */
    fun admit(): AutoCloseable? = synchronized(monitor) {
        if (retired) return@synchronized null
        val borrowed = submitted.get()
        if (borrowed != null) {
            requireCurrent(borrowed)
            borrowed.references++
            AutoCloseable { releaseReference(borrowed) }
        } else if (current == null) {
            val permit = Permit(this).also { current = it }
            AutoCloseable { permit.close() }
        } else null
    }

    fun <T> submit(permit: Permit, block: () -> T): T {
        synchronized(monitor) { requireCurrent(permit) }
        val previous = submitted.get()
        secureRequire(previous == null || previous === permit, SecureStateStatus.CONTEXT_CHANGED)
        submitted.set(permit)
        try { return block() } finally { if (previous == null) submitted.remove() else submitted.set(previous) }
    }

    fun requireCurrent(permit: Permit): Unit = synchronized(monitor) {
        secureRequire(!retired && current === permit && !permit.released, SecureStateStatus.CONTEXT_CHANGED)
    }

    /** Actual native close/lock release is queued only after ALL captures and borrows settle. */
    fun retire(onIdle: () -> Unit): Unit {
        val invoke = synchronized(monitor) {
            retired = true
            if (current == null) true else { idle = onIdle; false }
        }
        if (invoke) onIdle()
    }

    private fun release(permit: Permit): Unit {
        synchronized(monitor) {
            if (permit.released) return
            secureRequire(current === permit, SecureStateStatus.CONTEXT_CHANGED)
            permit.released = true
        }
        releaseReference(permit)
    }

    private fun releaseReference(permit: Permit): Unit {
        val callback = synchronized(monitor) {
            secureRequire(current === permit && permit.references > 0, SecureStateStatus.CONTEXT_CHANGED)
            permit.references--
            if (permit.references == 0) {
                current = null
                idle.also { idle = null }
            } else null
        }
        callback?.invoke()
    }
}
