package app.autplay.desktop.presentation

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.ui.desktop.DesktopAccountWorkspaceState
import app.autplay.ui.desktop.DesktopLanguage

/** Passive issued-owner previews. Full account actions require explicit navigation and new review. */
@Composable
internal fun DesktopAccountHomePreview(
    state: DesktopAccountWorkspaceState,
    language: DesktopLanguage,
    onOpenAccount: () -> Unit,
): Unit {
    val ru = language == DesktopLanguage.Russian
    key(state.scope.lifetime, state.scope.revision) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(if (ru) "Каталог текущего аккаунта" else "Current account catalog",
                style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
            val account = state.connection.accountPublicId ?: state.portable.publicId
            account?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
            Text(if (ru) "Ограниченные превью текущего каталога; порядок по ID, не по дате релиза."
                else "Bounded current catalog previews; ordered by ID, not release date.",
                style = MaterialTheme.typography.bodySmall)
            if (state.catalog.homeSections.isEmpty()) {
                Text(if (ru) "Подтверждённых превью пока нет. Откройте аккаунты для выбора и обновления каталога."
                    else "No confirmed previews yet. Open accounts to select and refresh the catalog.")
            }
            state.catalog.homeSections.forEach { section -> key(section.id) {
                Text(section.title, style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() })
                section.entries.take(6).forEach { entry -> key(entry.key, entry.occurrenceId) {
                    Text(entry.title ?: if (ru) "Название недоступно" else "Title unavailable",
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                    entry.subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall,
                        maxLines = 2, overflow = TextOverflow.Ellipsis) }
                } }
            } }
            OutlinedButton(onClick = onOpenAccount) {
                Text(if (ru) "Открыть аккаунты и полный каталог" else "Open accounts and full catalog")
            }
        }
    }
}
