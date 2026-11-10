package app.autplay.desktop.playback

import app.autplay.data.desktop.DesktopStorageException

/** A refresh failure can be suppressed on the original transaction/cancellation failure. */
internal fun desktopSnapshotUnavailable(failure: Throwable): Boolean {
    val pending = ArrayDeque<Throwable>()
    pending.add(failure)
    var remaining = 16
    while (pending.isNotEmpty() && remaining-- > 0) {
        val current = pending.removeFirst()
        if (current is DesktopStorageException && current.code == "desktop_snapshot_unavailable") return true
        current.cause?.let { pending.addLast(it) }
        current.suppressed.take(16).forEach { pending.addLast(it) }
    }
    return false
}
