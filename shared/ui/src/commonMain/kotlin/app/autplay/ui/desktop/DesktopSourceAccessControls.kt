package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.sourceaccess.*

private class SourceControlsLifetime { var active: Boolean = true }
private class SourceControlsOwner(private val owner: SourceAccessActions) {
    override fun equals(other: Any?): Boolean = other is SourceControlsOwner && other.owner === owner
    override fun hashCode(): Int = 0
}

/** Settings host supplies the current source owner and navigation lifetime. No locator enters UI. */
@Composable
@NonSkippableComposable
public fun DesktopSourceAccessControls(
    state: SourceAccessState,
    actions: SourceAccessActions,
    language: DesktopLanguage,
    isNavigationCurrent: () -> Boolean,
) {
    val currentState by rememberUpdatedState(state)
    val currentOwner by rememberUpdatedState(actions)
    val currentNavigation by rememberUpdatedState(isNavigationCurrent)
    val lifetime = remember(SourceControlsOwner(actions), state.revision) { SourceControlsLifetime() }
    DisposableEffect(lifetime) { onDispose { lifetime.active = false } }
    var forget by remember(lifetime) { mutableStateOf<RememberedSource?>(null) }
    fun valid(): Boolean = lifetime.active && currentState === state && currentOwner === actions &&
        currentNavigation() && state.available && !state.busy && !state.fenced
    fun label(en: String, ru: String): String = if(language == DesktopLanguage.Russian) ru else en

    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.fillMaxWidth().heightIn(max = 440.dp).verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(label("Remembered original sources", "Запомненные источники оригиналов"), style = MaterialTheme.typography.titleMedium)
            Text(label("Remember a selected location for manual read-only rescans. OS access is checked each time. Rescan shows a preview; copying requires confirmation.",
                "Запомните выбранное место для ручного повторного сканирования. Доступ ОС проверяется каждый раз. Копирование требует подтверждения предпросмотра."),
                style = MaterialTheme.typography.bodySmall)
            Text(label("Locations are stored privately on this Mac without encryption, outside ordinary AutPlay settings and exports. OS or administrator copies can retain them. Originals are never changed.",
                "Местоположения хранятся приватно на этом Mac без шифрования, отдельно от настроек и обычного экспорта AutPlay. Копии ОС или администратора могут их сохранять. Оригиналы не изменяются."),
                style = MaterialTheme.typography.bodySmall)
            OutlinedButton(onClick = { if(valid()) actions.remember(state) }, enabled = valid()) {
                Text(label("Remember selected source…", "Запомнить выбранный источник…"))
            }
            if(!state.available) Text(label("Remembered sources are unavailable for this app installation. One-time import remains available.",
                "Запомненные источники недоступны в этой установке. Разовый импорт остаётся доступным."))
            if(state.busy || state.fenced) Text(label(if(state.busy) "Working; waiting for actual reads to settle." else "Source access is paused.",
                if(state.busy) "Выполняется операция; ожидаем завершения чтений." else "Доступ к источникам приостановлен."),
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            state.errorCode?.let { code ->
                Text(when(code) {
                    "source_index_full", "source_limit" -> label("The source or storage limit was reached. Existing records are retained.", "Достигнут лимит источника или хранилища. Существующие записи сохранены.")
                    "source_internal_rejected" -> label("Choose original music outside AutPlay storage and the app bundle.", "Выберите оригиналы вне хранилища AutPlay и пакета приложения.")
                    "source_drain_pending" -> label("Reads have not finished closing. Access remains fenced; retry the operation or Quit.", "Чтения ещё не закрыты. Новый доступ заблокирован; повторите действие или завершение приложения.")
                    "import_busy", "source_busy" -> label("Another operation is active. Try again after it settles.", "Другая операция ещё выполняется. Повторите после её завершения.")
                    else -> label("Access is unavailable. Reconnect the volume or explicitly repair the selection; a changed OS permission may require reselection.",
                        "Доступ недоступен. Подключите том или явно выберите источник заново. При изменении разрешения ОС может потребоваться новый выбор.")
                }, color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            }
            state.sources.forEach { row ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(row.label, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(when(row.availability) {
                        SourceAvailability.READY -> label("Ready for manual rescan", "Готов к ручному сканированию")
                        SourceAvailability.DISABLED -> label("Disabled", "Отключён")
                        SourceAvailability.DRAINING -> label("Access fenced; waiting for read settlement", "Новый доступ заблокирован; завершение чтений")
                        SourceAvailability.REPAIR_REQUIRED -> label("Repair or reselection required", "Нужно восстановить доступ или выбрать заново")
                        SourceAvailability.UNSUPPORTED -> label("Unsupported record retained", "Неподдерживаемая запись сохранена")
                    }, style = MaterialTheme.typography.bodySmall)
                    val allowed = valid() && row.availability != SourceAvailability.UNSUPPORTED
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { if(valid()) { if(row.enabled) actions.disable(state,row) else actions.enable(state,row) } }, enabled = allowed) {
                            Text(if(row.enabled) label("Disable", "Отключить") else label("Enable", "Включить"))
                        }
                        TextButton(onClick = { if(valid()) actions.rescan(state,row) },
                            enabled = allowed && row.enabled && row.availability != SourceAvailability.DRAINING) { Text(label("Rescan", "Сканировать")) }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { if(valid()) actions.repair(state,row) }, enabled = allowed) { Text(label("Repair…", "Восстановить…")) }
                        TextButton(onClick = { if(valid()) forget = row }, enabled = allowed) { Text(label("Forget…", "Забыть…")) }
                    }
                }
            }
        }
    }
    forget?.let { row ->
        AlertDialog(onDismissRequest = { forget = null },
            title = { Text(label("Forget this source?", "Забыть этот источник?")) },
            text = { Text(label("After active reads close, only this remembered location is removed. Originals, imported copies and account data remain.",
                "После закрытия активных чтений будет удалено только запомненное местоположение. Оригиналы, импортированные копии и данные аккаунта сохранятся.")) },
            confirmButton = { TextButton(onClick = { if(valid() && state.sources.any { it === row }) actions.forget(state,row); forget = null }, enabled = valid()) {
                Text(label("Forget source", "Забыть источник")) } },
            dismissButton = { TextButton(onClick = { forget = null }) { Text(label("Cancel", "Отмена")) } })
    }
}
