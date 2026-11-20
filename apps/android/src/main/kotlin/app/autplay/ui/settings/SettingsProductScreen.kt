package app.autplay.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.social.ProfileStatisticsSettingsState
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
    statisticsSettings: ProfileStatisticsSettingsState = ProfileStatisticsSettingsState.Unavailable,
    statisticsSettingsErrorCode: String? = null,
    onStatisticsVisibilityChange: (Boolean) -> Unit = {},
    onRefreshDeveloperMode: () -> Unit = {},
    onNavigate: (UiDestination) -> Unit,
    trainingConsent: app.autplay.TrainingConsentUi = app.autplay.TrainingConsentUi(),
    page: UiDestination = UiDestination.Settings,
    onChooseAudio: () -> Unit = {},
) {
    when (page) {
        UiDestination.Settings -> Surface(
            color = AutPlayTokens.colors.glassSurface,
            shape = MaterialTheme.shapes.extraLarge,
        ) {
            Column(Modifier.fillMaxWidth()) {
                UiDestination.settingsNavigation.forEachIndexed { index, destination ->
                    SettingsNavigationRow(destination, settingsSummary(destination, settings)) { onNavigate(destination) }
                    if (index < UiDestination.settingsNavigation.lastIndex) {
                        HorizontalDivider(Modifier.padding(start = 52.dp), color = AutPlayTokens.colors.border)
                    }
                }
            }
        }
        UiDestination.SettingsAppearance -> SettingsPageSurface {
            val dark = settings.appearanceMode != "LIGHT"
            val currentTheme = stringResource(if (dark) R.string.settings_theme_dark else R.string.settings_theme_light)
            val switchTheme = stringResource(if (dark) R.string.settings_switch_to_light else R.string.settings_switch_to_dark)
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(stringResource(R.string.settings_theme), style = MaterialTheme.typography.labelLarge)
                    Text(currentTheme, style = MaterialTheme.typography.bodyMedium, color = AutPlayTokens.colors.mutedText)
                }
                FilledTonalIconButton(
                    onClick = { onUpdate { it.copy(appearanceMode = if (it.appearanceMode == "LIGHT") "DARK" else "LIGHT") } },
                    modifier = Modifier.size(56.dp).testTag("settings-theme-toggle").semantics {
                        contentDescription = switchTheme
                        stateDescription = currentTheme
                    },
                ) {
                    AutPlayPlatformIcon(if (dark) AutPlayIcon.Sun else AutPlayIcon.Moon, null, Modifier.size(26.dp))
                }
            }
            HorizontalDivider(color = AutPlayTokens.colors.border)
            Text(stringResource(R.string.settings_language), style = MaterialTheme.typography.labelLarge)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppLanguage.entries.forEach { language ->
                    FilterChip(
                        selected = AppLanguage.fromStoredValue(settings.appLanguage) == language,
                        onClick = { onAppLanguageChange(language) },
                        label = { Text(stringResource(language.labelRes)) },
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp).testTag("settings-language-${language.storedValue}"),
                    )
                }
            }
        }
        UiDestination.SettingsPlayback -> SettingsPageSurface {
            SettingsSwitchRow(
                label = stringResource(R.string.settings_smooth_track_transitions),
                checked = settings.smoothTrackTransitions,
                onCheckedChange = { enabled ->
                    onUpdate { current -> current.copy(smoothTrackTransitions = enabled) }
                },
            )
            Text(stringResource(R.string.settings_complete_tracks), color = AutPlayTokens.colors.mutedText)
            PlaybackAudioSettingsPanel(settings.playbackAudio) { transform ->
                onUpdate { current -> current.copy(playbackAudio = transform(current.playbackAudio)) }
            }
        }
        UiDestination.SettingsMusic -> SettingsPageSurface {
            Button(onClick = onChooseAudio, modifier = Modifier.fillMaxWidth().testTag("settings-add-audio")) {
                Text(stringResource(R.string.library_add_local))
            }
            PhoneMusicTools(settings)
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
        UiDestination.SettingsDownloads -> SettingsPageSurface {
            SettingsSwitchRow(
                label = stringResource(R.string.settings_metered_downloads),
                checked = settings.downloadOnMeteredNetwork,
                onCheckedChange = { enabled ->
                    onUpdate { current -> current.copy(downloadOnMeteredNetwork = enabled) }
                },
            )
            HorizontalDivider()
            Text(stringResource(R.string.settings_room_downloads), style = MaterialTheme.typography.titleSmall)
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
        UiDestination.PrivacyAndData -> Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
            SettingsPageSurface {
                Text(stringResource(R.string.settings_statistics_privacy), style = MaterialTheme.typography.titleMedium)
                Text(
                    stringResource(R.string.settings_statistics_share_explanation),
                    color = AutPlayTokens.colors.mutedText,
                )
                val confirmed = statisticsSettings as? ProfileStatisticsSettingsState.Confirmed
                val checked = when (statisticsSettings) {
                    is ProfileStatisticsSettingsState.Confirmed -> statisticsSettings.enabled
                    is ProfileStatisticsSettingsState.Updating -> statisticsSettings.confirmed.enabled
                    ProfileStatisticsSettingsState.Loading,
                    ProfileStatisticsSettingsState.Unavailable,
                    -> false
                }
                val switchStateDescription = stringResource(
                    when (statisticsSettings) {
                        ProfileStatisticsSettingsState.Loading -> R.string.settings_statistics_state_loading
                        is ProfileStatisticsSettingsState.Updating -> R.string.settings_statistics_state_updating
                        ProfileStatisticsSettingsState.Unavailable -> R.string.settings_statistics_state_unavailable
                        is ProfileStatisticsSettingsState.Confirmed -> if (statisticsSettings.enabled) {
                            R.string.settings_statistics_state_shared
                        } else {
                            R.string.settings_statistics_state_private
                        }
                    },
                )
                SettingsSwitchRow(
                    label = stringResource(R.string.settings_statistics_share),
                    checked = checked,
                    enabled = confirmed != null,
                    stateDescriptionText = switchStateDescription,
                    onCheckedChange = onStatisticsVisibilityChange,
                )
                Text(
                    stringResource(
                        when (statisticsSettings) {
                            ProfileStatisticsSettingsState.Loading -> R.string.settings_statistics_loading
                            is ProfileStatisticsSettingsState.Updating -> R.string.settings_statistics_updating
                            ProfileStatisticsSettingsState.Unavailable -> if (settings.activeServerProfileId == null) {
                                R.string.settings_statistics_unavailable_local
                            } else {
                                R.string.settings_statistics_unavailable_server
                            }
                            is ProfileStatisticsSettingsState.Confirmed -> if (statisticsSettings.enabled) {
                                R.string.settings_statistics_state_shared
                            } else {
                                R.string.settings_statistics_state_private
                            }
                        },
                    ),
                    color = AutPlayTokens.colors.mutedText,
                )
                if (statisticsSettingsErrorCode != null && confirmed != null) {
                    Text(
                        stringResource(R.string.settings_statistics_update_failed),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            SettingsPageSurface {
                Text(stringResource(R.string.training_consent_summary), style = MaterialTheme.typography.titleMedium)
                TrainingConsentCard(trainingConsent, expanded = true)
            }
        }
        UiDestination.SettingsAbout -> SettingsPageSurface {
            Text(stringResource(R.string.settings_app_version, app.autplay.BuildConfig.VERSION_NAME))
            HorizontalDivider()
            Text(stringResource(R.string.settings_transfer), style = MaterialTheme.typography.titleMedium)
            Text(stringResource(R.string.settings_transfer_scope), color = AutPlayTokens.colors.mutedText)
            Text(stringResource(R.string.settings_export_privacy), color = AutPlayTokens.colors.mutedText)
            Button(onClick = onExportSettings, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_export))
            }
            OutlinedButton(onClick = onImportSettings, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_import))
            }
            HorizontalDivider()
            SettingsNavigationRow(UiDestination.SettingsDiagnostics, stringResource(R.string.settings_diagnostics_summary)) {
                onNavigate(UiDestination.SettingsDiagnostics)
            }
        }
        UiDestination.SettingsDiagnostics -> SettingsPageSurface {
            Text(stringResource(R.string.settings_developer_mode), style = MaterialTheme.typography.titleSmall)
            Text(stringResource(R.string.settings_developer_description), color = AutPlayTokens.colors.mutedText)
            OutlinedButton(onClick = onRefreshDeveloperMode, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.settings_developer_refresh))
            }
            if (settings.developerMode) {
                Text(stringResource(R.string.settings_developer_enabled), color = MaterialTheme.colorScheme.primary)
                Text("${app.autplay.BuildConfig.APPLICATION_ID} · ${app.autplay.BuildConfig.VERSION_CODE}")
            } else {
                Text(stringResource(R.string.settings_developer_disabled), color = AutPlayTokens.colors.mutedText)
            }
            OutlinedButton(onClick = { onNavigate(UiDestination.ServerFeatures) }, modifier = Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.nav_server_features))
            }
        }
        else -> Unit
    }
}

@Composable
private fun SettingsPageSurface(content: @Composable () -> Unit) {
    app.autplay.ui.PreferenceSurface {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            content()
        }
    }
}

@Composable
private fun SettingsNavigationRow(destination: UiDestination, summary: String, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp).testTag("settings-row-${destination.route}"),
        color = androidx.compose.ui.graphics.Color.Transparent,
    ) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            AutPlayPlatformIcon(destination.icon, null, Modifier.size(24.dp), MaterialTheme.colorScheme.primary)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(stringResource(destination.labelRes), style = MaterialTheme.typography.titleSmall)
                Text(summary, style = MaterialTheme.typography.bodyMedium, color = AutPlayTokens.colors.mutedText)
            }
            AutPlayPlatformIcon(AutPlayIcon.ChevronDown, null, Modifier.size(18.dp).rotate(-90f), AutPlayTokens.colors.mutedText)
        }
    }
}

@Composable
private fun settingsSummary(destination: UiDestination, settings: NonSecretSettings): String = when (destination) {
    UiDestination.SettingsAccount -> stringResource(if (settings.activeServerProfileId == null)
        R.string.settings_account_local_summary else R.string.settings_account_connected_summary)
    UiDestination.SettingsPlayback -> stringResource(R.string.settings_sound_summary)
    UiDestination.SettingsMusic -> stringResource(R.string.settings_music_summary)
    UiDestination.SettingsDownloads -> stringResource(R.string.settings_downloads_summary)
    UiDestination.SettingsAppearance -> stringResource(R.string.settings_appearance_summary,
        stringResource(if (settings.appearanceMode == "LIGHT") R.string.settings_theme_light else R.string.settings_theme_dark),
        stringResource(AppLanguage.fromStoredValue(settings.appLanguage).labelRes))
    UiDestination.PrivacyAndData -> stringResource(R.string.settings_data_summary)
    else -> stringResource(R.string.settings_about_summary)
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
private fun SettingsSwitchRow(
    label: String,
    checked: Boolean,
    enabled: Boolean = true,
    stateDescriptionText: String? = null,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        Switch(
            checked = checked,
            enabled = enabled,
            onCheckedChange = onCheckedChange,
            modifier = Modifier.heightIn(min = 48.dp).semantics {
                contentDescription = label
                stateDescriptionText?.let { stateDescription = it }
            },
        )
    }
}

@Composable
private fun settingsWavePrefetchLabel(mode: String): String = stringResource(
    when (mode) {
        "OFF" -> R.string.settings_wave_prefetch_off
        "NEXT_3" -> R.string.settings_wave_prefetch_three
        "AGGRESSIVE_WIFI" -> R.string.settings_wave_prefetch_wifi
        else -> R.string.settings_wave_prefetch_next
    },
)
