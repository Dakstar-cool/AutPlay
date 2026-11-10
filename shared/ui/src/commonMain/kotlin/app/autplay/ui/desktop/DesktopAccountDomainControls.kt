package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Inputs are drafts; opaque host targets bind the exact account, object and displayed revision. */
@Composable
internal fun DesktopAccountDomainControls(owner: DesktopAccountUiOwner, controls: List<DesktopDomainControl>,
    language: DesktopLanguage): Unit {
    fun text(ru: String, en: String): String = if (language == DesktopLanguage.Russian) ru else en
    controls.forEach { control -> key(control.targetId) {
        var open by remember { mutableStateOf(false) }
        var name by remember { mutableStateOf(control.name.orEmpty()) }
        var description by remember { mutableStateOf(control.description.orEmpty()) }
        val available = owner.available(DesktopAccountAction.DomainMutation, control.targetId)
        OutlinedButton(enabled = available, onClick = {
            if (control.kind == DesktopDomainEditKind.BeginAdd || control.kind == DesktopDomainEditKind.Retry)
                owner.send(DesktopAccountCommand.DomainEdit(control.targetId)) else open = true
        }, modifier = Modifier.fillMaxWidth()) { Text(control.label) }
        if (open) AlertDialog(onDismissRequest = { open = false },
            title = { Text(control.label) },
            text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text("Изменение относится к показанному аккаунту. Доставка на сервер может оставаться в ожидании; локальный результат подтверждается хранилищем.",
                    "This changes the displayed account. Server delivery may remain pending; storage confirms the local result."))
                if (control.kind == DesktopDomainEditKind.PlaylistMetadata) {
                    OutlinedTextField(name, { if (it.length <= 500) name = it },
                        label = { Text(text("Название", "Name")) }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(description, { if (it.length <= 2000) description = it },
                        label = { Text(text("Описание", "Description")) }, modifier = Modifier.fillMaxWidth())
                }
                if (control.kind == DesktopDomainEditKind.Delete) Text(text(
                    "Исходные файлы сохраняются. Подтвердите изменение выбранной ссылки или вхождения.",
                    "Original files are preserved. Confirm the change to the selected reference or occurrence."))
            } },
            confirmButton = { Button(enabled = available &&
                (control.kind != DesktopDomainEditKind.PlaylistMetadata || name.isNotBlank()), onClick = {
                open = false
                owner.send(if (control.kind == DesktopDomainEditKind.PlaylistMetadata)
                    DesktopAccountCommand.DomainEdit(control.targetId, name, description.takeIf { it.isNotEmpty() })
                    else DesktopAccountCommand.DomainEdit(control.targetId))
            }) { Text(text("Подтвердить", "Confirm")) } },
            dismissButton = { TextButton(onClick = { open = false }) { Text(text("Отмена", "Cancel")) } })
    } }
}
