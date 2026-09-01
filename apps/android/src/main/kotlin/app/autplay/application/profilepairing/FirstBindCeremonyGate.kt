package app.autplay.application.profilepairing

import java.util.concurrent.atomic.AtomicReference

enum class FirstBindCeremonyOwner { M5, PUBLIC_ACCESS }

/** Process-local reservation spanning the complete first-bind ceremony, including user decisions. */
class FirstBindCeremonyGate {
    private val owner = AtomicReference<FirstBindCeremonyOwner?>(null)

    fun reserve(candidate: FirstBindCeremonyOwner): Boolean {
        val current = owner.get()
        return current == candidate || (current == null && owner.compareAndSet(null, candidate))
    }

    fun isReservedBy(candidate: FirstBindCeremonyOwner): Boolean = owner.get() == candidate

    fun release(candidate: FirstBindCeremonyOwner) {
        owner.compareAndSet(candidate, null)
    }
}
