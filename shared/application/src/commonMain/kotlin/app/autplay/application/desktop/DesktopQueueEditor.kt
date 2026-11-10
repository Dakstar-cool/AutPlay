package app.autplay.application.desktop

/** Process-local identity of one exact editable queue; never persisted or sent to native code. */
data class DesktopQueueEditTarget(
    val queueId: String,
    val currentEntryId: String,
    val sessionId: String?,
    val generation: Long,
    val revision: Long,
)

enum class DesktopQueueGroupAction { REMOVE, MOVE_UP, MOVE_DOWN, MOVE_NEXT }

/** Entries are a bounded copy in effective playback order, published with their matching target.
 * Position/label-only publications retain the same target instance; retired revisions never return.
 */
data class DesktopQueueEditorState(
    val target: DesktopQueueEditTarget? = null,
    val entries: List<DesktopQueueEntry> = emptyList(),
    val currentEntryId: String? = null,
    val shuffleEnabled: Boolean = false,
)
