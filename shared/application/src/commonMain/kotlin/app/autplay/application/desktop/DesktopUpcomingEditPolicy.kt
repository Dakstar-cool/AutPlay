package app.autplay.application.desktop

/** Effective playback order after one edit; entry identities distinguish duplicate track references. */
data class DesktopUpcomingEditPlan(
    val activeEntryIds: List<String>,
    val removedEntryIds: Set<String>,
)

/**
 * Plans an all-or-nothing edit of upcoming occurrences in effective playback order.
 * Invalid selections and unchanged orders return null. Inputs and the prefix through current stay intact.
 */
fun planDesktopUpcomingEdit(
    entries: List<DesktopQueueEntry>,
    currentEntryId: String?,
    selectedEntryIds: List<String>,
    action: DesktopQueueGroupAction,
): DesktopUpcomingEditPlan? {
    // Refuse oversized input before allocating any collection derived from it.
    if (entries.size !in 1..DesktopLocalLimits.MAX_QUEUE_ENTRIES ||
        selectedEntryIds.size !in 1..DesktopLocalLimits.MAX_QUEUE_ENTRIES || currentEntryId == null) return null

    val activeEntryIds = entries.map { it.id }
    if (activeEntryIds.toSet().size != activeEntryIds.size) return null
    val currentIndex = activeEntryIds.indexOf(currentEntryId)
    if (currentIndex < 0) return null

    val selected = selectedEntryIds.toSet()
    if (selected.size != selectedEntryIds.size) return null
    val upcoming = activeEntryIds.drop(currentIndex + 1).toSet()
    if (selected.any { it !in upcoming }) return null

    val edited: List<String> = when (action) {
        DesktopQueueGroupAction.REMOVE -> activeEntryIds.filterNot { it in selected }
        DesktopQueueGroupAction.MOVE_UP -> activeEntryIds.toMutableList().apply {
            // Forward swaps carry each selected run across one preceding unselected upcoming row.
            for (index in currentIndex + 2 until size) {
                if (this[index] in selected && this[index - 1] !in selected) {
                    val preceding = this[index - 1]
                    this[index - 1] = this[index]
                    this[index] = preceding
                }
            }
        }
        DesktopQueueGroupAction.MOVE_DOWN -> activeEntryIds.toMutableList().apply {
            // Reverse traversal gives each run one following row without moving a boundary run.
            for (index in lastIndex - 1 downTo currentIndex + 1) {
                if (this[index] in selected && this[index + 1] !in selected) {
                    val following = this[index + 1]
                    this[index + 1] = this[index]
                    this[index] = following
                }
            }
        }
        DesktopQueueGroupAction.MOVE_NEXT -> buildList(activeEntryIds.size) {
            for (index in 0..currentIndex) add(activeEntryIds[index])
            // Selection click order never changes the existing order of selected occurrences.
            for (id in activeEntryIds) if (id in selected) add(id)
            for (index in currentIndex + 1 until activeEntryIds.size) {
                val id = activeEntryIds[index]
                if (id !in selected) add(id)
            }
        }
    }
    if (edited == activeEntryIds) return null
    return DesktopUpcomingEditPlan(
        activeEntryIds = edited,
        removedEntryIds = if (action == DesktopQueueGroupAction.REMOVE) selected else emptySet(),
    )
}
