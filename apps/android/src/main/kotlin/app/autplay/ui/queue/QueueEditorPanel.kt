package app.autplay.ui.queue

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.ui.AutPlayTokens

public data class QueueEditorUiEntry(
    val queueEntryId: String,
    val title: String,
    val artist: String?,
    val isCurrent: Boolean,
    val isUpcoming: Boolean,
)

public data class QueueEditorUiState(
    val entries: List<QueueEditorUiEntry> = emptyList(),
    val editable: Boolean = false,
    val canPrevious: Boolean = false,
    val canNext: Boolean = false,
    val unavailable: Boolean = false,
)

public data class QueueEditorUiActions(
    val moveUp: (String) -> Unit = {},
    val moveDown: (String) -> Unit = {},
    val remove: (String) -> Unit = {},
    val clearUpcoming: () -> Unit = {},
)

@Composable
@OptIn(ExperimentalLayoutApi::class)
internal fun QueueEditorPanel(
    state: QueueEditorUiState,
    actions: QueueEditorUiActions,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth().testTag("queue-editor"),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(stringResource(R.string.queue_editor_title), style = MaterialTheme.typography.titleLarge)
            OutlinedButton(
                onClick = actions.clearUpcoming,
                enabled = state.editable && state.entries.any(QueueEditorUiEntry::isUpcoming),
            ) {
                Text(stringResource(R.string.queue_clear_upcoming))
            }
        }
        when {
            state.unavailable -> Text(
                stringResource(R.string.queue_editor_unavailable),
                color = MaterialTheme.colorScheme.error,
            )
            state.entries.isEmpty() -> Text(
                stringResource(R.string.queue_editor_empty),
                color = AutPlayTokens.colors.mutedText,
            )
            else -> state.entries.take(MAX_VISIBLE_ENTRIES).forEachIndexed { index, entry ->
                QueueEntryRow(
                    entry = entry,
                    canMoveUp = entry.isUpcoming && index > 0 && state.entries[index - 1].isUpcoming,
                    canMoveDown = entry.isUpcoming && state.entries.getOrNull(index + 1)?.isUpcoming == true,
                    editable = state.editable,
                    actions = actions,
                )
            }
        }
        if (state.entries.size > MAX_VISIBLE_ENTRIES) {
            Text(
                pluralStringResource(
                    R.plurals.queue_editor_more,
                    state.entries.size - MAX_VISIBLE_ENTRIES,
                    state.entries.size - MAX_VISIBLE_ENTRIES,
                ),
                color = AutPlayTokens.colors.mutedText,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        if (state.entries.any(QueueEditorUiEntry::isCurrent)) {
            Text(
                stringResource(R.string.queue_current_locked),
                color = AutPlayTokens.colors.mutedText,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
private fun QueueEntryRow(
    entry: QueueEditorUiEntry,
    canMoveUp: Boolean,
    canMoveDown: Boolean,
    editable: Boolean,
    actions: QueueEditorUiActions,
) {
    Surface(
        modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp).testTag("queue-entry-${entry.queueEntryId}"),
        shape = MaterialTheme.shapes.large,
        color = if (entry.isCurrent) {
            MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.62f)
        } else {
            AutPlayTokens.colors.glassSurface
        },
        tonalElevation = if (entry.isCurrent) 2.dp else 0.dp,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(entry.title, style = MaterialTheme.typography.titleMedium)
            entry.artist?.takeIf(String::isNotBlank)?.let {
                Text(it, color = AutPlayTokens.colors.mutedText, style = MaterialTheme.typography.bodySmall)
            }
            if (entry.isCurrent) {
                Text(stringResource(R.string.queue_current), color = MaterialTheme.colorScheme.primary)
            } else if (entry.isUpcoming) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { actions.moveUp(entry.queueEntryId) }, enabled = editable && canMoveUp) {
                        Text(stringResource(R.string.queue_move_up))
                    }
                    OutlinedButton(onClick = { actions.moveDown(entry.queueEntryId) }, enabled = editable && canMoveDown) {
                        Text(stringResource(R.string.queue_move_down))
                    }
                    OutlinedButton(onClick = { actions.remove(entry.queueEntryId) }, enabled = editable) {
                        Text(stringResource(R.string.queue_remove))
                    }
                }
            }
        }
    }
}

private const val MAX_VISIBLE_ENTRIES = 100
