package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.planDesktopUpcomingEdit

/** Only the exact actor target and navigation own selection, independently of publication DTOs. */
internal class DesktopUpcomingScope(
    val target: DesktopQueueEditTarget?,
    private val navigationRevision: Long,
) {
    override fun equals(other: Any?): Boolean = other is DesktopUpcomingScope && target === other.target &&
        navigationRevision == other.navigationRevision

    override fun hashCode(): Int = 31 * (target?.hashCode() ?: 0) + navigationRevision.hashCode()
}

/** Owner replacement retires callbacks without clearing the actor-target selection. */
internal class DesktopUpcomingOwnerKey(private val owner: DesktopLibraryCallbacks) {
    override fun equals(other: Any?): Boolean = other is DesktopUpcomingOwnerKey && owner === other.owner
    override fun hashCode(): Int = 0
}

/** Entry occurrences remain distinct even when they refer to the same track. */
internal class DesktopUpcomingSelection private constructor(val ids: List<String>) {
    constructor() : this(emptyList())

    fun setSelected(id: String, selected: Boolean, upcomingIds: Set<String>): DesktopUpcomingSelection {
        if (!selected) return if (id in ids) DesktopUpcomingSelection(ids.filterNot { it == id }) else this
        if (id !in upcomingIds || id in ids || ids.size >= DesktopLocalLimits.MAX_QUEUE_ENTRIES) return this
        return DesktopUpcomingSelection(ids + id)
    }

    fun selectAll(upcomingIds: List<String>): DesktopUpcomingSelection {
        if (upcomingIds.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES || upcomingIds.toSet().size != upcomingIds.size) return this
        return DesktopUpcomingSelection(upcomingIds.toList())
    }

    fun clear(): DesktopUpcomingSelection = DesktopUpcomingSelection()
    fun retireIntent(): DesktopUpcomingSelection = DesktopUpcomingSelection(ids.toList())
}

/** The actor retains this exact target only while its frozen queue shape remains unchanged. */
internal class DesktopUpcomingInput(val editor: DesktopQueueEditorState, val selection: DesktopUpcomingSelection,
    val legacyQueue: DesktopQueue? = null) {
    fun sameQueue(other: DesktopUpcomingInput): Boolean = editor.target === other.editor.target &&
        editor.currentEntryId == other.editor.currentEntryId &&
        editor.shuffleEnabled == other.editor.shuffleEnabled

    override fun equals(other: Any?): Boolean = other is DesktopUpcomingInput && sameQueue(other) &&
        selection === other.selection && legacyQueue === other.legacyQueue
    override fun hashCode(): Int = editor.target?.hashCode() ?: 0
}

internal class DesktopUpcomingRow(val entry: DesktopQueueEntry, val position: Int, val upcoming: Boolean,
    val canMoveUp: Boolean, val canMoveDown: Boolean)

internal class DesktopUpcomingView(
    val input: DesktopUpcomingInput? = null,
    val rows: List<DesktopUpcomingRow> = emptyList(),
    val upcomingIds: List<String> = emptyList(),
    val upcomingSet: Set<String> = emptySet(),
    val selectedIds: Set<String> = emptySet(),
    val allowed: Set<DesktopQueueGroupAction> = emptySet(),
    val legacyAligned: Boolean = false,
)

/** Single-row legacy callbacks may act only on the same bounded effective queue shape. */
internal fun desktopUpcomingLegacyAligned(editor: DesktopQueueEditorState, queue: DesktopQueue?): Boolean {
    val target = editor.target ?: return false
    if (queue == null || queue.id != target.queueId || queue.currentEntryId != target.currentEntryId ||
        queue.sessionId != target.sessionId || queue.shuffleEnabled != editor.shuffleEnabled ||
        queue.repeatMode !in setOf("OFF", "ONE", "ALL") || queue.entries.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES ||
        queue.shuffleOrder.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES ||
        editor.entries.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES) return false
    val byId = queue.entries.associateBy { it.id }
    if (byId.size != queue.entries.size) return false
    val active = if (queue.shuffleEnabled) {
        if (queue.shuffleOrder.size != byId.size || queue.shuffleOrder.toSet() != byId.keys) return false
        queue.shuffleOrder.map { byId.getValue(it) }
    } else queue.entries
    return active == editor.entries
}

/** Background-only row preparation and preview admission use the application's single edit policy. */
internal fun prepareDesktopUpcomingView(input: DesktopUpcomingInput, checkpoint: () -> Unit = {}): DesktopUpcomingView {
    val editor = input.editor
    val target = editor.target ?: return DesktopUpcomingView(input)
    if (editor.entries.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES || editor.currentEntryId != target.currentEntryId ||
        editor.entries.map { it.id }.toSet().size != editor.entries.size) return DesktopUpcomingView(input)
    val current = editor.entries.indexOfFirst { it.id == editor.currentEntryId }
    if (current < 0) return DesktopUpcomingView(input)
    val upcomingIds = editor.entries.drop(current + 1).map { it.id }
    val rows = editor.entries.mapIndexed { index, entry ->
        if (index % 256 == 0) checkpoint()
        DesktopUpcomingRow(entry, index, index > current, index > current + 1, index > current && index < editor.entries.lastIndex)
    }
    val allowed = DesktopQueueGroupAction.entries.filter { action ->
        checkpoint()
        planDesktopUpcomingEdit(editor.entries, editor.currentEntryId, input.selection.ids, action) != null
    }.toSet()
    checkpoint()
    return DesktopUpcomingView(input, rows, upcomingIds, upcomingIds.toSet(), input.selection.ids.toSet(), allowed,
        desktopUpcomingLegacyAligned(editor, input.legacyQueue))
}

/** Plain factories keep strong skipping from memoizing closures with equal retired captures. */
internal fun desktopUpcomingRowsGuard(lifetime: DesktopBrowseLifetime, owner: DesktopLibraryCallbacks,
    latestOwner: () -> DesktopLibraryCallbacks, input: DesktopUpcomingInput, preparedInput: DesktopUpcomingInput?,
    latestInput: () -> DesktopUpcomingInput, isScopeCurrent: () -> Boolean, canEdit: () -> Boolean): () -> Boolean =
    desktopBrowseIdentityGuard(lifetime, owner, latestOwner) {
        isScopeCurrent() && canEdit() && input.sameQueue(latestInput()) && preparedInput?.sameQueue(latestInput()) == true
    }

internal fun desktopUpcomingCheckbox(id: String, upcomingIds: Set<String>, isCurrent: () -> Boolean,
    latest: () -> DesktopUpcomingSelection, update: (DesktopUpcomingSelection) -> Unit): (Boolean) -> Unit = { selected ->
    if (isCurrent()) update(latest().setSelected(id, selected, upcomingIds))
}

internal fun desktopUpcomingSelectionAction(isCurrent: () -> Boolean, action: () -> Unit): () -> Unit = {
    if (isCurrent()) action()
}

/** One rendering can submit at most once; it never acknowledges a Unit callback as success. */
internal class DesktopUpcomingActionTarget(val input: DesktopUpcomingInput, private val view: DesktopUpcomingView) {
    private var retired: Boolean = false

    fun claim(currentInput: DesktopUpcomingInput, currentView: DesktopUpcomingView,
        currentTarget: DesktopUpcomingActionTarget, action: DesktopQueueGroupAction, isCurrent: Boolean): Boolean {
        if (retired || currentTarget !== this || currentView !== view || input != currentInput ||
            view.input != input || action !in view.allowed || !isCurrent) return false
        retired = true
        return true
    }
}

internal fun desktopUpcomingCommand(target: DesktopUpcomingActionTarget, action: DesktopQueueGroupAction,
    claim: () -> Boolean, retire: () -> Unit, callbacks: DesktopLibraryCallbacks): () -> Unit = {
    val editorTarget = target.input.editor.target
    if (editorTarget != null && claim()) {
        val ids = target.input.selection.ids.toList()
        retire()
        callbacks.onEditUpcoming(editorTarget, action, ids)
    }
}
