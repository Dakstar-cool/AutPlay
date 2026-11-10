package app.autplay.ui.desktop

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.DesktopTrack
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

internal enum class DesktopUpcomingText(val russian: String, val english: String) {
    Count("Выбрано следующих записей: {count} / 1000", "Upcoming entries selected: {count} / 1000"),
    Order("Повторы трека выбираются отдельно. Действия используют текущий порядок воспроизведения, включая перемешивание, а не порядок выбора. Выше и ниже сдвигают каждую выбранную группу через одну соседнюю невыбранную запись; группы у края остаются на месте. Следующими собирает выбранные записи сразу после текущей, сохраняя их порядок. Текущая и прошедшие записи сохраняются.", "Track occurrences are selected separately. Actions use the current playback order, including shuffle, rather than selection order. Up and down move each selected run across one adjacent unselected entry; runs at a boundary stay in place. Next gathers selected entries immediately after the current entry in their existing order. Current and past entries are retained."),
    SelectAll("Выбрать все следующие", "Select all upcoming"),
    Select("Выбрать запись", "Select entry"),
    Remove("Удалить выбранные из очереди", "Remove selected from queue"),
    Up("Выбранные выше", "Move selected up"),
    Down("Выбранные ниже", "Move selected down"),
    Next("Выбранные следующими", "Move selected next"),
    Submitted("Команда отправлена; результат не подтверждён. Выбор сохранён до изменения очереди. Проверьте очередь и сообщение об ошибке перед новой командой.", "Command sent; result is unconfirmed. Selection is retained until the queue changes. Review the queue and any error before another command."),
    ;

    fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.Russian -> russian
        DesktopLanguage.English -> english
    }
}

internal fun DesktopQueueGroupAction.desktopUpcomingLabel(): DesktopUpcomingText = when (this) {
    DesktopQueueGroupAction.REMOVE -> DesktopUpcomingText.Remove
    DesktopQueueGroupAction.MOVE_UP -> DesktopUpcomingText.Up
    DesktopQueueGroupAction.MOVE_DOWN -> DesktopUpcomingText.Down
    DesktopQueueGroupAction.MOVE_NEXT -> DesktopUpcomingText.Next
}

internal fun desktopUpcomingTrackTitle(track: DesktopTrack?, metadataUpdating: Boolean,
    language: DesktopLanguage): String = track?.title ?: (if (metadataUpdating) DesktopText.UpdatingLibrary
        else DesktopText.SourceMissing).inLanguage(language)

/** Rows belong to the coherent player projection; library lookup supplies metadata only. */
@Composable
internal fun DesktopUpcomingQueueEditor(
    editor: DesktopQueueEditorState,
    tracksById: Map<String, DesktopTrack>,
    callbacks: DesktopLibraryCallbacks,
    owner: DesktopLibraryCallbacks,
    language: DesktopLanguage,
    enabled: Boolean,
    reopenRequired: Boolean,
    navigationRevision: Long,
    isOwnerCurrent: () -> Boolean,
    legacyQueue: DesktopQueue?,
    legacyEnabled: Boolean,
    actOnLegacyView: (() -> Unit) -> Unit,
    modifier: Modifier = Modifier,
    metadataUpdating: Boolean = false,
) {
    val scope = DesktopUpcomingScope(editor.target, navigationRevision)
    val lifetime = remember(scope, DesktopUpcomingOwnerKey(owner)) { DesktopBrowseLifetime() }
    DisposableEffect(lifetime) { onDispose { lifetime.retire() } }
    var selection by remember(scope) { mutableStateOf(DesktopUpcomingSelection()) }
    var submitted by remember(scope) { mutableStateOf(false) }
    val currentLifetime by rememberDesktopBrowseReference(lifetime)
    val currentOwner by rememberDesktopBrowseReference(owner)
    val input = DesktopUpcomingInput(editor, selection, legacyQueue)
    val currentInput by rememberDesktopBrowseReference(input)
    val prepared by produceState(DesktopUpcomingView(), input) {
        value = withContext(Dispatchers.Default) {
            val context = currentCoroutineContext()
            prepareDesktopUpcomingView(input) { context.ensureActive() }
        }
    }
    val view = prepared
    val currentView by rememberDesktopBrowseReference(view)
    val fresh = view.input == input
    val currentEnabled by rememberUpdatedState(enabled)
    val currentReopenRequired by rememberUpdatedState(reopenRequired)
    val rowsCurrent = desktopUpcomingRowsGuard(lifetime, owner, { currentOwner }, input, view.input, { currentInput },
        isScopeCurrent = { currentLifetime === lifetime && isOwnerCurrent() },
        canEdit = { enabled && !reopenRequired && currentEnabled && !currentReopenRequired })
    val currentLegacyEnabled by rememberUpdatedState(legacyEnabled)
    val canUseLegacy: () -> Boolean = {
        rowsCurrent() && view.legacyAligned && view.input?.legacyQueue === currentInput.legacyQueue &&
            currentLegacyEnabled
    }
    val target = DesktopUpcomingActionTarget(input, view)
    val currentTarget by rememberDesktopBrowseReference(target)
    val commands = DesktopQueueGroupAction.entries.associateWith { action ->
        desktopUpcomingCommand(target, action, claim = {
            // Read mutable selection directly so rapid events cannot submit the previous selection.
            target.claim(DesktopUpcomingInput(currentInput.editor, selection, currentInput.legacyQueue),
                currentView, currentTarget, action, rowsCurrent())
        }, retire = {
            selection = selection.retireIntent()
            submitted = true
        }, callbacks = owner)
    }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(DesktopUpcomingText.Count.label(language).replace("{count}", selection.ids.size.toString()),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Text(DesktopUpcomingText.Order.label(language), style = MaterialTheme.typography.bodySmall)
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = desktopUpcomingSelectionAction(rowsCurrent) {
                selection = selection.selectAll(view.upcomingIds)
            }, enabled = fresh && enabled && !reopenRequired && view.upcomingIds.isNotEmpty()) {
                Text(DesktopUpcomingText.SelectAll.label(language))
            }
            OutlinedButton(onClick = desktopUpcomingSelectionAction(rowsCurrent) { selection = selection.clear() },
                enabled = enabled && !reopenRequired && selection.ids.isNotEmpty()) {
                Text(DesktopText.ClearSelection.inLanguage(language))
            }
        }
        if (submitted) Text(DesktopUpcomingText.Submitted.label(language),
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DesktopQueueGroupAction.entries.forEach { action ->
                OutlinedButton(onClick = commands.getValue(action),
                    enabled = fresh && enabled && !reopenRequired && action in view.allowed) {
                    Text(action.desktopUpcomingLabel().label(language))
                }
            }
        }
        val rows = if (view.input?.sameQueue(input) == true) view.rows else emptyList()
        if (!fresh) Text(DesktopText.UpdatingLibrary.inLanguage(language))
        else if (rows.isEmpty()) Text(DesktopText.QueueEditUnavailable.inLanguage(language))
        else if (view.upcomingIds.isEmpty()) Text(DesktopText.NoUpcoming.inLanguage(language))
        LazyColumn(Modifier.weight(1f)) {
            items(rows, key = { it.entry.id }) { row ->
                val entry = row.entry
                val track = tracksById[entry.trackId]
                val title = desktopUpcomingTrackTitle(track, metadataUpdating, language)
                val positionLabel = DesktopText.QueueEntryPosition.inLanguage(language)
                    .replace("{position}", (row.position + 1).toString())
                val label = "$title; $positionLabel"
                Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        if (row.upcoming) Checkbox(checked = entry.id in view.selectedIds,
                            onCheckedChange = desktopUpcomingCheckbox(entry.id, view.upcomingSet, rowsCurrent,
                                { selection }, { selection = it }),
                            enabled = enabled && !reopenRequired,
                            modifier = Modifier.semantics { contentDescription = "${DesktopUpcomingText.Select.label(language)}: $label" })
                        Column(Modifier.weight(1f).semantics { selected = entry.id == editor.currentEntryId }
                            .clickable(enabled = legacyEnabled && view.legacyAligned, role = Role.Button,
                                onClickLabel = DesktopText.Play.inLanguage(language),
                                onClick = desktopUpcomingSelectionAction(canUseLegacy) {
                                    actOnLegacyView { callbacks.onSelectEntry(entry.id) }
                                }).padding(vertical = 8.dp)) {
                            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(track?.artist.orEmpty(), maxLines = 1, overflow = TextOverflow.Ellipsis,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Text(positionLabel, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (row.upcoming) listOf(-1 to row.canMoveUp, 1 to row.canMoveDown).forEach { (delta, canMove) ->
                            val text = (if (delta < 0) DesktopText.MoveUp else DesktopText.MoveDown).inLanguage(language)
                            OutlinedButton(onClick = desktopUpcomingSelectionAction(canUseLegacy) {
                                actOnLegacyView { callbacks.onMoveUpcoming(entry.id, delta) }
                            }, enabled = legacyEnabled && view.legacyAligned && canMove,
                                modifier = Modifier.semantics { contentDescription = "$text: $label" }) { Text(text) }
                        }
                        OutlinedButton(onClick = desktopUpcomingSelectionAction(canUseLegacy) {
                            actOnLegacyView { callbacks.onRemoveEntry(entry.id) }
                        }, enabled = legacyEnabled && view.legacyAligned,
                            modifier = Modifier.semantics { contentDescription = "${DesktopText.Remove.inLanguage(language)}: $label" }) {
                            Text(DesktopText.Remove.inLanguage(language))
                        }
                    }
                }
                HorizontalDivider()
            }
        }
    }
}
