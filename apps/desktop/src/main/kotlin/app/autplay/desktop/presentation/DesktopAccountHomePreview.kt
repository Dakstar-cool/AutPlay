package app.autplay.desktop.presentation

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.size
import app.autplay.ui.AutPlayArtworkPlaceholder
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
            if (state.catalog.homeSections.isEmpty()) {
                Text(if (ru) "Подтверждённых превью пока нет. Откройте аккаунты для выбора и обновления каталога."
                    else "No confirmed previews yet. Open accounts to select and refresh the catalog.")
            }
            state.catalog.homeSections.forEach { section -> key(section.id) {
                Text(section.title, style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.semantics { heading() })
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    section.entries.take(6).forEach { entry -> key(entry.key, entry.occurrenceId) {
                        Column(Modifier.width(132.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            AutPlayArtworkPlaceholder(entry.key.id, Modifier.size(132.dp))
                            Text(entry.title ?: if (ru) "Название недоступно" else "Title unavailable",
                                maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                            entry.subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall,
                                maxLines = 1, overflow = TextOverflow.Ellipsis) }
                        }
                    } }
                }
            } }
            OutlinedButton(onClick = onOpenAccount) {
                Text(if (ru) "Открыть аккаунты и полный каталог" else "Open accounts and full catalog")
            }
        }
    }
}
