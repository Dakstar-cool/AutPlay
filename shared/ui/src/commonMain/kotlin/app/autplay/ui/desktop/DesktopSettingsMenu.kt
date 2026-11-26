package app.autplay.ui.desktop

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

internal enum class DesktopSettingsPage(val ru: String, val en: String) {
    Appearance("Оформление", "Appearance"),
    Music("Музыка и загрузки", "Music and downloads"),
    Application("Приложение", "Application");

    fun label(language: DesktopLanguage): String = if (language == DesktopLanguage.Russian) ru else en
}

@Composable
internal fun DesktopSettingsMenu(
    language: DesktopLanguage,
    theme: DesktopThemeMode,
    account: (() -> Unit)?,
    showMusic: Boolean,
    showApplication: Boolean,
    transferBusy: Boolean,
    modifier: Modifier = Modifier,
    content: @Composable (DesktopSettingsPage) -> Unit,
): Unit {
    val ru = language == DesktopLanguage.Russian
    var current by remember { mutableStateOf<DesktopSettingsPage?>(null) }
    val page = current
    key(page) {
        Column(modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (page == null) {
                Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow) {
                    Column {
                        if (account != null) {
                            SettingsRow(if (ru) "Аккаунт и сервер" else "Account and server",
                                if (ru) "Подключение и устройства" else "Connection and devices", account)
                            HorizontalDivider()
                        }
                        val languageName = if (ru) "Русский" else "English"
                        SettingsRow(DesktopSettingsPage.Appearance.label(language), theme.settingsLabel(language) + " · " + languageName) {
                            current = DesktopSettingsPage.Appearance
                        }
                        if (showMusic) {
                            HorizontalDivider()
                            SettingsRow(DesktopSettingsPage.Music.label(language),
                                if (ru) "Воспроизведение, папки и загрузки" else "Playback, folders and downloads") { current = DesktopSettingsPage.Music }
                        }
                        if (showApplication) {
                            HorizontalDivider()
                            SettingsRow(DesktopSettingsPage.Application.label(language),
                                if (transferBusy) DesktopText.SettingsTransferBusy.inLanguage(language)
                                else if (ru) "Окно, импорт и экспорт настроек" else "Window, import and export") { current = DesktopSettingsPage.Application }
                        }
                    }
                }
            } else {
                TextButton(onClick = { current = null }) { Text(if (ru) "Назад к настройкам" else "Back to settings") }
                Text(page.label(language), style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                content(page)
            }
        }
    }
}

@Composable
private fun SettingsRow(title: String, summary: String, open: () -> Unit): Unit {
    Row(Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = open).heightIn(min = 64.dp)
        .padding(horizontal = 18.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(summary, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        DesktopIcon(DesktopGlyph.Close, null, Modifier.size(18.dp).rotate(-90f))
    }
}
