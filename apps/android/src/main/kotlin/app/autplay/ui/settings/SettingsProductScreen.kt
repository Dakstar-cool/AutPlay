package app.autplay.ui.settings

import androidx.annotation.StringRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.AppLanguage
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.UiDestination

@Composable
internal fun SettingsProductScreen(
    settings: NonSecretSettings,
    onUpdate: ((NonSecretSettings) -> NonSecretSettings) -> Unit,
    onAppLanguageChange: (AppLanguage) -> Unit,
    onChooseLibraryRoot: () -> Unit,
    onRescanLibraryRoot: () -> Unit,
    onExportSettings: () -> Unit,
    onImportSettings: () -> Unit,
    onNavigate: (UiDestination) -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        SettingsSection(icon = AutPlayIcon.Settings, titleRes = R.string.settings_appearance) {
            Text(stringResource(R.string.settings_language), style = MaterialTheme.typography.labelLarge)
            ChoiceRow {
                AppLanguage.entries.forEach { language ->
                    FilterChip(
                        selected = AppLanguage.fromStoredValue(settings.appLanguage) == language,
                        onClick = { onAppLanguageChange(language) },
                        label = { Text(stringResource(language.labelRes)) },
                    )
                }
            }
            ChoiceRow {
                listOf("SYSTEM", "LIGHT", "DARK").forEach { mode ->
                    FilterChip(
                        selected = settings.appearanceMode == mode,
                        onClick = { onUpdate { current -> current.copy(appearanceMode = mode) } },
                        label = { Text(settingsAppearanceLabel(mode)) },
                    )
                }
            }
            ChoiceRow {
                listOf("CORAL", "VIOLET", "GREEN", "BLUE").forEach { palette ->
                    FilterChip(
                        selected = settings.accentPalette == palette,
                        onClick = { onUpdate { current -> current.copy(accentPalette = palette) } },
                        label = { Text(settingsAccentLabel(palette)) },
                    )
                }
            }
        }

        SettingsSection(icon = AutPlayIcon.Library, titleRes = R.string.settings_library_access) {
            Text(
                stringResource(
                    if (settings.libraryRootTreeUri == null) R.string.settings_folder_not_selected
                    else R.string.settings_folder_selected,
                ),
                style = MaterialTheme.typography.titleSmall,
            )
            Text(stringResource(R.string.settings_folder_privacy), color = AutPlayTokens.colors.mutedText)
            Button(onClick = onChooseLibraryRoot, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_choose_folder))
            }
            OutlinedButton(
                onClick = onRescanLibraryRoot,
                enabled = settings.libraryRootTreeUri != null,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(stringResource(R.string.settings_scan_folder)) }
        }

        SettingsSection(icon = AutPlayIcon.Server, titleRes = R.string.settings_network) {
            Text(
                stringResource(
                    if (settings.activeServerProfileId == null) R.string.profile_connection_local
                    else R.string.profile_connection_connected,
                ),
                style = MaterialTheme.typography.titleSmall,
                color = if (settings.activeServerProfileId == null) AutPlayTokens.colors.mutedText
                else MaterialTheme.colorScheme.primary,
            )
            Text(stringResource(R.string.settings_personal_server_body), color = AutPlayTokens.colors.mutedText)
            Button(
                onClick = { onNavigate(UiDestination.Profile) },
                modifier = Modifier.fillMaxWidth(),
            ) { Text(stringResource(R.string.settings_open_personal_server)) }
            SettingsSwitchRow(
                label = stringResource(R.string.settings_metered_sync),
                checked = settings.syncOnMeteredNetwork,
                onCheckedChange = { enabled ->
                    onUpdate { current -> current.copy(syncOnMeteredNetwork = enabled) }
                },
            )
        }

        SettingsSection(icon = AutPlayIcon.Wave, titleRes = R.string.nav_wave_rooms) {
            ChoiceColumn {
                listOf("OFF", "NEXT", "NEXT_3", "AGGRESSIVE_WIFI").forEach { mode ->
                    FilterChip(
                        selected = settings.wavePrefetchMode == mode,
                        onClick = { onUpdate { current -> current.copy(wavePrefetchMode = mode) } },
                        label = { Text(settingsWavePrefetchLabel(mode)) },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        SettingsSection(icon = AutPlayIcon.Privacy, titleRes = R.string.settings_transfer) {
            Text(stringResource(R.string.settings_export_privacy), color = AutPlayTokens.colors.mutedText)
            Button(onClick = onExportSettings, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_export))
            }
            OutlinedButton(onClick = onImportSettings, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_import))
            }
        }

        SettingsSection(icon = AutPlayIcon.Playlist, titleRes = R.string.settings_more) {
            UiDestination.secondaryNavigation.forEach { target ->
                Surface(
                    onClick = { onNavigate(target) },
                    modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                    shape = MaterialTheme.shapes.medium,
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AutPlayPlatformIcon(target.icon, contentDescription = null, modifier = Modifier.size(22.dp))
                        Text(stringResource(target.labelRes), style = MaterialTheme.typography.titleSmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun SettingsSection(
    icon: AutPlayIcon,
    @StringRes titleRes: Int,
    content: @Composable () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        color = AutPlayTokens.colors.glassSurface,
        border = BorderStroke(1.dp, AutPlayTokens.colors.glassBorder),
        tonalElevation = 1.dp,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                AutPlayPlatformIcon(
                    icon = icon,
                    contentDescription = null,
                    modifier = Modifier.size(24.dp),
                    tint = MaterialTheme.colorScheme.primary,
                )
                Text(stringResource(titleRes), style = MaterialTheme.typography.titleLarge)
            }
            content()
        }
    }
}

@Composable
private fun ChoiceRow(content: @Composable () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        content = { content() },
    )
}

@Composable
private fun ChoiceColumn(content: @Composable () -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        content = { content() },
    )
}

@Composable
private fun SettingsSwitchRow(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
private fun settingsAppearanceLabel(mode: String): String = stringResource(
    when (mode) {
        "LIGHT" -> R.string.settings_theme_light
        "DARK" -> R.string.settings_theme_dark
        else -> R.string.settings_theme_system
    },
)

@Composable
private fun settingsAccentLabel(palette: String): String = stringResource(
    when (palette) {
        "VIOLET" -> R.string.settings_accent_violet
        "GREEN" -> R.string.settings_accent_green
        "BLUE" -> R.string.settings_accent_blue
        else -> R.string.settings_accent_coral
    },
)

@Composable
private fun settingsWavePrefetchLabel(mode: String): String = stringResource(
    when (mode) {
        "OFF" -> R.string.settings_wave_prefetch_off
        "NEXT_3" -> R.string.settings_wave_prefetch_three
        "AGGRESSIVE_WIFI" -> R.string.settings_wave_prefetch_wifi
        else -> R.string.settings_wave_prefetch_next
    },
)
