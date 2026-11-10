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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopTastePolicy
import app.autplay.application.desktop.DesktopTasteState

/** The existing queue owner revalidates expected IDs and persists explicit choices. */
public data class DesktopTasteCallbacks(
    public val onSetCurrentListenExcluded: (expectedQueueId: String, expectedEntryId: String,
        expectedListeningEventId: String, excluded: Boolean) -> Unit = { _, _, _, _ -> },
    public val onSetQueueSessionExcluded: (expectedQueueId: String, excluded: Boolean) -> Unit = { _, _ -> },
)

/** Reference keys retire even structurally equal projections without scanning snapshot lists. */
internal class DesktopTasteInput(
    val state: DesktopTasteState,
    val library: DesktopLibrarySnapshot?,
    val section: DesktopSection,
    val navigationRevision: Long,
    val selectionRevision: Long,
    val reopenRequired: Boolean,
) {
    override fun equals(other: Any?): Boolean = other is DesktopTasteInput &&
        state === other.state && library === other.library && section == other.section &&
        navigationRevision == other.navigationRevision && selectionRevision == other.selectionRevision &&
        reopenRequired == other.reopenRequired

    // Equal keys share these scalars; collisions for different references are intentional.
    override fun hashCode(): Int = 31 * navigationRevision.hashCode() + selectionRevision.hashCode()
}

/** Each remembered target is one rendering lifetime, including state/snapshot A/B/A changes. */
internal class DesktopTasteTarget(val input: DesktopTasteInput) {
    val queueId: String? = input.state.queueId
    val entryId: String? = input.state.currentEntryId
    val listeningEventId: String? = input.state.listeningEventId

    private fun canWrite(current: DesktopTasteTarget, reopenRequired: Boolean, navigationCurrent: Boolean): Boolean =
        this === current && input == current.input && navigationCurrent &&
            !input.reopenRequired && !reopenRequired && !queueId.isNullOrBlank()

    fun canSetListen(current: DesktopTasteTarget, reopenRequired: Boolean, navigationCurrent: Boolean): Boolean =
        canWrite(current, reopenRequired, navigationCurrent) && input.state.listenCommandsEnabled &&
            !entryId.isNullOrBlank() && !listeningEventId.isNullOrBlank()

    // Queue-wide scope is queueId, never DesktopQueue.sessionId (a logical listen ID).
    fun canSetQueue(current: DesktopTasteTarget, reopenRequired: Boolean, navigationCurrent: Boolean): Boolean =
        canWrite(current, reopenRequired, navigationCurrent) && input.state.sessionCommandsEnabled

    fun setListen(current: DesktopTasteTarget, reopenRequired: Boolean, navigationCurrent: Boolean,
        excluded: Boolean, callbacks: DesktopTasteCallbacks): Unit {
        if (canSetListen(current, reopenRequired, navigationCurrent)) {
            val queue = queueId ?: return
            val entry = entryId ?: return
            val event = listeningEventId ?: return
            callbacks.onSetCurrentListenExcluded(queue, entry, event, excluded)
        }
    }

    fun setQueue(current: DesktopTasteTarget, reopenRequired: Boolean, navigationCurrent: Boolean,
        excluded: Boolean, callbacks: DesktopTasteCallbacks): Unit {
        if (canSetQueue(current, reopenRequired, navigationCurrent)) {
            callbacks.onSetQueueSessionExcluded(queueId ?: return, excluded)
        }
    }
}

/** Unknown flags may coexist with a known effective exclusion; retain a separate warning. */
internal enum class DesktopTasteMark { INCLUDED, EXCLUDED, UNSUPPORTED }

internal fun desktopTasteChoice(value: String): Boolean? = when (value) {
    DesktopTastePolicy.INCLUDED -> false
    DesktopTastePolicy.EXCLUDED -> true
    else -> null
}

internal fun desktopTasteMark(session: String, listen: String): DesktopTasteMark =
    when (DesktopTastePolicy.isExcluded(session, listen)) {
        true -> DesktopTasteMark.EXCLUDED
        false -> DesktopTasteMark.INCLUDED
        null -> DesktopTasteMark.UNSUPPORTED
    }

internal fun desktopTasteHasUnsupported(session: String, listen: String): Boolean =
    !DesktopTastePolicy.isKnown(session) || !DesktopTastePolicy.isKnown(listen)

internal fun desktopTasteStatus(session: String, listen: String, language: DesktopLanguage): String = when (desktopTasteMark(session, listen)) {
    DesktopTasteMark.INCLUDED -> DesktopText.TasteIncluded
    DesktopTasteMark.EXCLUDED -> DesktopText.TasteExcluded
    DesktopTasteMark.UNSUPPORTED -> DesktopText.TasteUnsupported
}.inLanguage(language)

/** History uses each event's captured flags, independently of current queue/track state. */
internal fun desktopHistoryTasteAnnotation(event: DesktopListeningEvent, language: DesktopLanguage): String {
    val status = desktopTasteStatus(event.sessionTasteState, event.listenTasteState, language)
    val warning = if (desktopTasteHasUnsupported(event.sessionTasteState, event.listenTasteState))
        " · ${DesktopText.TasteUnsupportedWarning.inLanguage(language)}" else ""
    return "${DesktopText.TasteHistoryMark.inLanguage(language)}: $status$warning"
}

internal fun desktopTasteRequiresReopen(code: String?): Boolean =
    code == "desktop_snapshot_unavailable" || code == "import_library_reopen_required"

/** Separate from native/library errors; never interpolate a raw code or claim rollback. */
internal fun desktopTasteErrorText(code: String?, reopenRequired: Boolean): DesktopText? = when {
    reopenRequired || desktopTasteRequiresReopen(code) -> DesktopText.LibraryReopenRequired
    code == null -> null
    code == "TASTE_LISTEN_STALE" -> DesktopText.TasteListenStale
    code == "TASTE_SESSION_STALE" -> DesktopText.TasteQueueStale
    code == "TASTE_EXCLUSION_UNAVAILABLE" -> DesktopText.TasteUnavailable
    else -> DesktopText.TasteSaveUnconfirmed
}

@Composable
internal fun DesktopTasteControls(
    input: DesktopTasteInput,
    isNavigationCurrent: () -> Boolean,
    language: DesktopLanguage,
    callbacks: DesktopTasteCallbacks,
) {
    val target = remember(input) { DesktopTasteTarget(input) }
    val currentTarget by rememberUpdatedState(target)
    val currentReopenRequired by rememberUpdatedState(input.reopenRequired)
    val currentCallbacks by rememberUpdatedState(callbacks)
    val state = input.state
    val hasListen = !state.queueId.isNullOrBlank() && !state.currentEntryId.isNullOrBlank() && !state.listeningEventId.isNullOrBlank()
    val effective = if (hasListen) desktopTasteStatus(state.sessionTasteState, state.listenTasteState, language)
        else DesktopText.TasteNoCurrentListen.inLanguage(language)
    Column(Modifier.fillMaxWidth().heightIn(max = 200.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("${DesktopText.TasteEffective.inLanguage(language)}: $effective",
            style = MaterialTheme.typography.labelLarge,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        desktopTasteErrorText(state.errorCode, input.reopenRequired)?.let { text ->
            Text(text.inLanguage(language), color = MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        DesktopTasteChoices(DesktopText.TasteCurrentListen, state.listenTasteState,
            target.canSetListen(target, input.reopenRequired, isNavigationCurrent()), language) { excluded ->
            target.setListen(currentTarget, currentReopenRequired, isNavigationCurrent(), excluded, currentCallbacks)
        }
        DesktopTasteChoices(DesktopText.TasteCurrentQueue, state.sessionTasteState,
            target.canSetQueue(target, input.reopenRequired, isNavigationCurrent()), language) { excluded ->
            target.setQueue(currentTarget, currentReopenRequired, isNavigationCurrent(), excluded, currentCallbacks)
        }
        Text(DesktopText.TasteLocalOnly.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        Text(DesktopText.TasteListenScope.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        Text(DesktopText.TasteQueueScope.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        if (desktopTasteHasUnsupported(state.sessionTasteState, state.listenTasteState)) {
            Text(DesktopText.TasteUnsupportedHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
        }
        Text(DesktopText.TasteHistoryRetained.inLanguage(language), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun DesktopTasteChoices(scope: DesktopText, value: String, enabled: Boolean,
    language: DesktopLanguage, onChoose: (Boolean) -> Unit) {
    val scopeLabel = scope.inLanguage(language)
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).selectableGroup(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(scopeLabel, style = MaterialTheme.typography.labelLarge)
        listOf(false, true).forEach { excluded ->
            val label = (if (excluded) DesktopText.TasteChooseExclude else DesktopText.TasteChooseInclude).inLanguage(language)
            FilterChip(selected = desktopTasteChoice(value) == excluded, enabled = enabled,
                onClick = { onChoose(excluded) }, label = { Text(label) },
                modifier = Modifier.semantics { contentDescription = "$scopeLabel: $label" })
        }
        if (!DesktopTastePolicy.isKnown(value)) Text(DesktopText.TasteUnsupportedWarning.inLanguage(language))
    }
}
