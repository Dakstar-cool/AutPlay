package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopSleepTimerLimits
import app.autplay.application.desktop.DesktopSleepTimerState

/** The playback owner revalidates all expected identities; defaults preserve existing hosts. */
public data class DesktopSleepTimerCallbacks(
    public val onSchedule: (String, Long) -> Unit = { _, _ -> },
    public val onStopAfterCurrent: (String, String, String) -> Unit = { _, _, _ -> },
    public val onCancel: (String) -> Unit = {},
)

/** Scalar keys exclude ticking remainingMs and avoid queue scans on the UI thread. */
internal data class DesktopSleepTimerTarget(
    val queueId: String?,
    val entryId: String?,
    val sessionId: String?,
    val planId: String?,
) {
    constructor(state: DesktopSleepTimerState) : this(state.queueId, state.currentEntryId, state.sessionId, state.planId)

    fun matches(state: DesktopSleepTimerState): Boolean = queueId == state.queueId &&
        entryId == state.currentEntryId && sessionId == state.sessionId && planId == state.planId

    fun canArm(state: DesktopSleepTimerState, reopenRequired: Boolean): Boolean =
        matches(state) && !reopenRequired && state.commandsEnabled &&
            queueId != null && entryId != null && sessionId != null

    fun canCancel(state: DesktopSleepTimerState): Boolean = planId != null && matches(state)
}

internal enum class DesktopSleepTimerMode { Duration, AfterCurrent }

/** Form values only; no deadline, persistence, optimistic timer or delayed job belongs here. */
internal class DesktopSleepTimerDraft(
    val target: DesktopSleepTimerTarget,
    val minutes: String = "30",
    val mode: DesktopSleepTimerMode = DesktopSleepTimerMode.Duration,
    val inputRejected: Boolean = false,
) {
    fun durationMs(): Long? = if (inputRejected) null else desktopSleepTimerDurationMs(minutes)
}

internal fun acceptsDesktopSleepTimerInput(value: String): Boolean =
    value.length <= 3 && value.all { it in '0'..'9' }

internal fun desktopSleepTimerDurationMs(value: String): Long? {
    if (!acceptsDesktopSleepTimerInput(value)) return null
    val minutes = value.toLongOrNull() ?: return null
    val duration = minutes * 60_000L
    return duration.takeIf { it in DesktopSleepTimerLimits.MIN_DURATION_MS..DesktopSleepTimerLimits.MAX_DURATION_MS }
}

internal fun desktopSleepTimerRemainingMinutes(remainingMs: Long): Long {
    val positive = remainingMs.coerceAtLeast(0)
    return positive / 60_000 + if (positive % 60_000 == 0L) 0 else 1
}

internal fun desktopSleepTimerStatus(state: DesktopSleepTimerState, language: DesktopLanguage): String {
    val remaining = state.remainingMs
    return when {
        state.planId == null -> DesktopText.SleepTimerOff.inLanguage(language)
        remaining != null -> DesktopText.SleepTimerRemaining.inLanguage(language)
            .replace("{minutes}", desktopSleepTimerRemainingMinutes(remaining).toString())
        state.stopAfterEntryId != null -> DesktopText.SleepTimerAfterCurrent.inLanguage(language)
        else -> DesktopText.SleepTimerOff.inLanguage(language)
    }
}

@Composable
internal fun DesktopSleepTimerControls(
    state: DesktopSleepTimerState,
    reopenRequired: Boolean,
    language: DesktopLanguage,
    callbacks: DesktopSleepTimerCallbacks,
) {
    val currentState by rememberUpdatedState(state)
    val currentReopenRequired by rememberUpdatedState(reopenRequired)
    val currentCallbacks by rememberUpdatedState(callbacks)
    var dialog by remember { mutableStateOf<DesktopSleepTimerDraft?>(null) }
    val target = DesktopSleepTimerTarget(state)
    val title = DesktopText.SleepTimer.inLanguage(language)
    val status = desktopSleepTimerStatus(state, language)
    OutlinedButton(
        onClick = {
            if (dialog == null && target.matches(currentState)) {
                dialog = DesktopSleepTimerDraft(target,
                    mode = if (state.stopAfterEntryId != null) DesktopSleepTimerMode.AfterCurrent else DesktopSleepTimerMode.Duration)
            }
        },
        modifier = Modifier.semantics { contentDescription = "$title: $status" },
    ) { Text("$title: $status") }
    if (target.planId != null) {
        TextButton(onClick = {
            if (target.canCancel(currentState)) currentCallbacks.onCancel(target.planId)
        }) { Text(DesktopText.CancelSleepTimer.inLanguage(language)) }
    }

    val draft = dialog ?: return
    val fresh = draft.target.matches(state)
    val armEnabled = draft.target.canArm(state, reopenRequired)
    val duration = draft.durationMs()
    val applyEnabled = armEnabled && (draft.mode == DesktopSleepTimerMode.AfterCurrent || duration != null)
    // Reference identity fences callbacks retained from an earlier form/edit or close/reopen.
    val canEdit: () -> Boolean = {
        dialog === draft && draft.target.canArm(currentState, currentReopenRequired)
    }
    val close: () -> Unit = { if (dialog === draft) dialog = null }
    AlertDialog(
        onDismissRequest = close,
        modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) },
        text = {
            Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(status)
                Text(DesktopText.SleepTimerLifetime.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                if (!fresh) {
                    Text(DesktopText.SleepTimerChanged.inLanguage(language), color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
                } else if (!armEnabled) {
                    Text(DesktopText.ControlsUnavailable.inLanguage(language))
                }
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).selectableGroup(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    DesktopSleepTimerMode.entries.forEach { mode ->
                        FilterChip(selected = draft.mode == mode, enabled = armEnabled, onClick = {
                            if (canEdit()) dialog = DesktopSleepTimerDraft(draft.target, draft.minutes, mode, draft.inputRejected)
                        }, label = {
                            Text((if (mode == DesktopSleepTimerMode.Duration) DesktopText.SleepTimerDuration
                                else DesktopText.SleepTimerAfterCurrent).inLanguage(language))
                        })
                    }
                }
                if (draft.mode == DesktopSleepTimerMode.Duration) {
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).selectableGroup(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("15", "30", "60").forEach { preset ->
                            FilterChip(selected = draft.minutes == preset && !draft.inputRejected, enabled = armEnabled,
                                onClick = { if (canEdit()) dialog = DesktopSleepTimerDraft(draft.target, preset) },
                                label = { Text(DesktopText.SleepTimerPreset.inLanguage(language).replace("{minutes}", preset)) })
                        }
                    }
                    OutlinedTextField(value = draft.minutes, onValueChange = { value ->
                        if (canEdit()) {
                            // Reject the entire oversized/non-ASCII edit; never truncate pasted input.
                            dialog = if (acceptsDesktopSleepTimerInput(value)) DesktopSleepTimerDraft(draft.target, value)
                                else DesktopSleepTimerDraft(draft.target, draft.minutes, inputRejected = true)
                        }
                    }, enabled = armEnabled, singleLine = true, isError = duration == null,
                        modifier = Modifier.fillMaxWidth(), label = { Text(DesktopText.SleepTimerMinutes.inLanguage(language)) },
                        supportingText = {
                            Text((if (draft.inputRejected) DesktopText.SleepTimerInputRejected
                                else DesktopText.SleepTimerMinutesHint).inLanguage(language))
                        })
                }
                if (draft.target.planId != null) {
                    TextButton(enabled = draft.target.canCancel(state), onClick = {
                        if (dialog === draft && draft.target.canCancel(currentState)) {
                            dialog = null
                            currentCallbacks.onCancel(draft.target.planId)
                        }
                    }) { Text(DesktopText.CancelSleepTimer.inLanguage(language)) }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = applyEnabled, onClick = {
                if (canEdit()) {
                    val queueId = draft.target.queueId
                    val entryId = draft.target.entryId
                    val sessionId = draft.target.sessionId
                    if (queueId != null && entryId != null && sessionId != null) {
                        when (draft.mode) {
                            DesktopSleepTimerMode.Duration -> draft.durationMs()?.let { milliseconds ->
                                dialog = null
                                currentCallbacks.onSchedule(queueId, milliseconds)
                            }
                            DesktopSleepTimerMode.AfterCurrent -> {
                                dialog = null
                                currentCallbacks.onStopAfterCurrent(queueId, entryId, sessionId)
                            }
                        }
                    }
                }
            }) { Text(DesktopText.ApplySleepTimer.inLanguage(language)) }
        },
        dismissButton = { TextButton(onClick = close) { Text(DesktopText.CancelSleepTimerDraft.inLanguage(language)) } },
    )
}
