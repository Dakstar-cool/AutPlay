package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopSettingsTransferState

public data class DesktopSettingsCallbacks(
    public val onLanguage: (String) -> Unit = {},
    public val onAppearance: (String) -> Unit = {},
    public val onAccent: (String) -> Unit = {},
    public val onImport: () -> Unit = {},
    public val onConfirmImport: (String) -> Unit = {},
    public val onDismissImport: (String) -> Unit = {},
    public val onExport: () -> Unit = {},
    public val onCancelTransfer: () -> Unit = {},
    public val onSmoothTrackTransitions: (Boolean) -> Unit = {},
)

/** Rendering fallbacks do not modify the source preference or normalize future values. */
public fun desktopPreferencesLanguage(value: String, fallback: DesktopLanguage = DesktopLanguage.English): DesktopLanguage =
    when (value) { "RU" -> DesktopLanguage.Russian; "EN" -> DesktopLanguage.English; else -> fallback }

public fun desktopPreferencesTheme(value: String, fallback: DesktopThemeMode = DesktopThemeMode.Dark): DesktopThemeMode =
    DesktopThemeMode.entries.firstOrNull { it.settingsValue == value } ?: fallback

public fun desktopPreferencesAccent(value: String, fallback: DesktopAccentPalette = DesktopAccentPalette.Coral): DesktopAccentPalette =
    DesktopAccentPalette.entries.firstOrNull { it.settingsValue == value } ?: fallback

internal fun desktopSettingsHaveUnsupportedValues(value: DesktopPreferences): Boolean =
    value.appLanguage !in setOf("EN", "RU") ||
        DesktopThemeMode.entries.none { it.settingsValue == value.appearanceMode } ||
        DesktopAccentPalette.entries.none { it.settingsValue == value.accentPalette }

internal fun desktopSettingsCanWrite(state: DesktopPreferencesState?, transfer: DesktopSettingsTransferState): Boolean =
    state?.writable != false && !transfer.busy

/** Scalar input changes create a new action lifetime, including a return to an earlier value. */
internal data class DesktopSmoothTransitionsScope(
    val value: Boolean?,
    val writable: Boolean,
    val busy: Boolean,
)

/** Callback owners must be compared by identity even if their data-class values compare equal. */
internal class DesktopSettingsOwnerKey(private val owner: DesktopSettingsCallbacks) {
    override fun equals(other: Any?): Boolean = other is DesktopSettingsOwnerKey && owner === other.owner
    override fun hashCode(): Int = 0
}

internal class DesktopSmoothTransitionsTarget(
    private val scope: DesktopSmoothTransitionsScope,
    private val owner: DesktopSettingsCallbacks,
) {
    private var active: Boolean = true

    internal fun retire(): Unit { active = false }

    internal fun canChange(
        next: Boolean,
        currentScope: DesktopSmoothTransitionsScope,
        currentOwner: DesktopSettingsCallbacks,
        currentTarget: DesktopSmoothTransitionsTarget,
    ): Boolean = active && currentTarget === this && currentOwner === owner && currentScope == scope &&
        scope.value != null && scope.writable && !scope.busy && next != scope.value

    /** A plain factory binds dispatch to this exact target and its owner outside Compose memoization. */
    internal fun changeHandler(
        latestScope: () -> DesktopSmoothTransitionsScope,
        latestOwner: () -> DesktopSettingsCallbacks,
        latestTarget: () -> DesktopSmoothTransitionsTarget,
    ): (Boolean) -> Unit = { next ->
        if (canChange(next, latestScope(), latestOwner(), latestTarget())) {
            owner.onSmoothTrackTransitions(next)
        }
    }
}

/** The ID and reference bind actions to the exact preview that was actually presented. */
internal class DesktopSettingsPreviewTarget(
    internal val id: String,
    internal val preview: DesktopPreferences,
) {
    internal fun matches(state: DesktopSettingsTransferState): Boolean =
        state.previewId == id && state.preview === preview

    internal fun canConfirm(preferences: DesktopPreferencesState?, state: DesktopSettingsTransferState): Boolean =
        preferences != null && matches(state) && desktopSettingsCanWrite(preferences, state)

    internal fun canDismiss(state: DesktopSettingsTransferState): Boolean = matches(state) && !state.busy
}

internal fun desktopSettingsErrorText(code: String): DesktopText = when (code) {
    "desktop_settings_unavailable", "desktop_settings_write_failed", "desktop_settings_save_failed", "desktop_settings_read_failed",
    "desktop_settings_file_unsafe", "desktop_settings_file_changed", "desktop_settings_atomic_unavailable",
    "desktop_settings_refresh_failed", "desktop_settings_close_failed" -> DesktopText.SettingsStorageFailed
    "desktop_settings_read_only", "desktop_settings_closed", "desktop_settings_in_use" -> DesktopText.SettingsReadOnly
    "desktop_settings_reopen_required" -> DesktopText.SettingsReopenRequired
    "desktop_settings_schema_unsupported" -> DesktopText.SettingsSchemaUnsupported
    "desktop_settings_too_large" -> DesktopText.SettingsImportTooLarge
    "desktop_settings_invalid", "desktop_settings_malformed", "desktop_settings_root_invalid",
    "desktop_settings_schema_invalid", "desktop_settings_structure_limit", "desktop_settings_value_invalid",
    "desktop_settings_language_invalid", "desktop_settings_appearance_invalid", "desktop_settings_palette_invalid",
    "desktop_settings_prefetch_invalid" -> DesktopText.SettingsImportInvalid
    "desktop_settings_export_invalid" -> DesktopText.SettingsExportInvalid
    "desktop_settings_export_target_exists" -> DesktopText.SettingsExportTargetExists
    "desktop_settings_export_failed" -> DesktopText.SettingsExportFailed
    "desktop_settings_import_failed", "desktop_settings_path_unsafe" -> DesktopText.SettingsTransferFailed
    "desktop_settings_cancelled" -> DesktopText.SettingsTransferCancelled
    "desktop_settings_command_busy" -> DesktopText.CommandBusy
    "desktop_settings_busy" -> DesktopText.SettingsTransferBusy
    else -> DesktopText.SettingsTransferFailed
}

internal fun desktopSettingsMessageText(code: String): DesktopText = when (code) {
    "desktop_settings_saved", "desktop_settings_imported" -> DesktopText.SettingsImported
    "desktop_settings_exported" -> DesktopText.SettingsExported
    "desktop_settings_cancelled" -> DesktopText.SettingsTransferCancelled
    else -> DesktopText.SettingsTransferFailed
}

internal fun DesktopThemeMode.settingsLabel(language: DesktopLanguage): String = when (this) {
    DesktopThemeMode.Light -> DesktopText.Light
    DesktopThemeMode.Dark -> DesktopText.Dark
    DesktopThemeMode.System -> DesktopText.SystemAppearance
}.inLanguage(language)

internal fun DesktopAccentPalette.settingsLabel(language: DesktopLanguage): String = when (this) {
    DesktopAccentPalette.Coral -> DesktopText.CoralAccent
    DesktopAccentPalette.Violet -> DesktopText.VioletAccent
    DesktopAccentPalette.Green -> DesktopText.GreenAccent
    DesktopAccentPalette.Blue -> DesktopText.BlueAccent
}.inLanguage(language)

// Uninterpreted input is never rendered as raw paths, control characters or external instructions.
internal fun desktopSettingsValueLabel(field: DesktopText, value: String, language: DesktopLanguage): String = when (field) {
    DesktopText.Language -> when (value) { "EN" -> "English"; "RU" -> "Русский"; else -> null }
    DesktopText.Appearance -> DesktopThemeMode.entries.firstOrNull { it.settingsValue == value }?.settingsLabel(language)
    DesktopText.AccentPalette -> DesktopAccentPalette.entries.firstOrNull { it.settingsValue == value }?.settingsLabel(language)
    DesktopText.WavePrefetch -> when (value) {
        "OFF" -> DesktopText.PrefetchOff
        "NEXT" -> DesktopText.PrefetchNext
        "NEXT_3" -> DesktopText.PrefetchNextThree
        "AGGRESSIVE_WIFI" -> DesktopText.PrefetchWifi
        else -> null
    }?.inLanguage(language)
    else -> null
} ?: DesktopText.SettingsOtherValue.inLanguage(language)

internal fun desktopSettingsBooleanLabel(value: Boolean, language: DesktopLanguage): String =
    (if (value) DesktopText.SettingsEnabled else DesktopText.SettingsDisabled).inLanguage(language)

internal fun desktopRetainedSettings(value: DesktopPreferences, language: DesktopLanguage): List<Pair<DesktopText, String>> = listOf(
    DesktopText.MeteredDownloads to desktopSettingsBooleanLabel(value.downloadOnMeteredNetwork, language),
    DesktopText.WavePrefetch to desktopSettingsValueLabel(DesktopText.WavePrefetch, value.wavePrefetchMode, language),
)

@Composable
internal fun DesktopSettingsControls(
    language: DesktopLanguage,
    themeMode: DesktopThemeMode,
    accentPalette: DesktopAccentPalette,
    preferences: DesktopPreferencesState?,
    transfer: DesktopSettingsTransferState,
    callbacks: DesktopSettingsCallbacks,
    onSessionLanguage: (DesktopLanguage) -> Unit,
    onSessionTheme: (DesktopThemeMode) -> Unit,
    modifier: Modifier = Modifier,
): Unit {
    val currentPreferences by rememberUpdatedState(preferences)
    val currentTransfer by rememberUpdatedState(transfer)
    val currentCallbacks by rememberDesktopBrowseReference(callbacks)
    val currentSessionLanguage by rememberUpdatedState(onSessionLanguage)
    val currentSessionTheme by rememberUpdatedState(onSessionTheme)
    val writeEnabled = desktopSettingsCanWrite(preferences, transfer)
    val canWrite: () -> Boolean = { desktopSettingsCanWrite(currentPreferences, currentTransfer) }
    val stored = preferences?.value
    val smoothScope = DesktopSmoothTransitionsScope(stored?.smoothTrackTransitions, preferences?.writable == true, transfer.busy)
    val currentSmoothScope by rememberUpdatedState(smoothScope)
    val smoothTarget = remember(smoothScope, DesktopSettingsOwnerKey(callbacks)) {
        DesktopSmoothTransitionsTarget(smoothScope, callbacks)
    }
    val currentSmoothTarget by rememberDesktopBrowseReference(smoothTarget)
    DisposableEffect(smoothTarget) { onDispose { smoothTarget.retire() } }
    val changeSmoothTransitions = smoothTarget.changeHandler(
        latestScope = { currentSmoothScope }, latestOwner = { currentCallbacks }, latestTarget = { currentSmoothTarget },
    )
    Surface(modifier, color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.large) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text((if (preferences == null) DesktopText.SessionSettings else DesktopText.SavedSettings).inLanguage(language))
            if (preferences?.writable == false) SettingsStatus(DesktopText.SettingsReadOnly, language, error = true)
            preferences?.errorCode?.let { SettingsStatus(desktopSettingsErrorText(it), language, error = true) }
            if (stored != null && desktopSettingsHaveUnsupportedValues(stored)) {
                SettingsStatus(DesktopText.SettingsUnsupportedValues, language, error = false)
            }
            Text(DesktopText.Appearance.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            SettingsOptions {
                DesktopThemeMode.entries.forEach { mode ->
                    FilterChip(selected = if (stored == null) themeMode == mode else stored.appearanceMode == mode.settingsValue,
                        enabled = writeEnabled, onClick = {
                            if (canWrite()) {
                                if (currentPreferences == null) currentSessionTheme(mode)
                                else currentCallbacks.onAppearance(mode.settingsValue)
                            }
                        }, label = { Text(mode.settingsLabel(language)) })
                }
            }
            Text(DesktopText.AccentPalette.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            SettingsOptions {
                DesktopAccentPalette.entries.forEach { accent ->
                    FilterChip(selected = if (stored == null) accentPalette == accent else stored.accentPalette == accent.settingsValue,
                        enabled = writeEnabled, onClick = { if (canWrite()) currentCallbacks.onAccent(accent.settingsValue) },
                        label = { Text(accent.settingsLabel(language)) })
                }
            }
            Text(DesktopText.Language.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            SettingsOptions {
                listOf("RU" to DesktopLanguage.Russian, "EN" to DesktopLanguage.English).forEach { (value, option) ->
                    FilterChip(selected = if (stored == null) language == option else stored.appLanguage == value,
                        enabled = writeEnabled, onClick = {
                            if (canWrite()) {
                                if (currentPreferences == null) currentSessionLanguage(option)
                                else currentCallbacks.onLanguage(value)
                            }
                        }, label = { Text(if (option == DesktopLanguage.Russian) "Русский" else "English") })
                }
            }
            if (stored != null) {
                Row(Modifier.fillMaxWidth().toggleable(value = stored.smoothTrackTransitions, enabled = writeEnabled,
                    role = Role.Switch, onValueChange = changeSmoothTransitions).padding(vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(DesktopText.SmoothTransitions.inLanguage(language), style = MaterialTheme.typography.titleMedium)
                        Text(DesktopText.SmoothTransitionsHint.inLanguage(language), style = MaterialTheme.typography.bodySmall)
                    }
                    Switch(checked = stored.smoothTrackTransitions, onCheckedChange = null, enabled = writeEnabled)
                }
                HorizontalDivider()
                Text(DesktopText.RetainedSettings.inLanguage(language))
                desktopRetainedSettings(stored, language).forEach { (field, value) -> Text("${field.inLanguage(language)}: $value") }
                SettingsOptions {
                    OutlinedButton(enabled = writeEnabled && transfer.previewId == null, onClick = {
                        if (canWrite() && currentTransfer.previewId == null && currentPreferences != null) currentCallbacks.onImport()
                    }) { Text(DesktopText.ImportSettings.inLanguage(language)) }
                    OutlinedButton(enabled = !transfer.busy && transfer.previewId == null, onClick = {
                        if (!currentTransfer.busy && currentTransfer.previewId == null && currentPreferences != null) currentCallbacks.onExport()
                    }) { Text(DesktopText.ExportSettings.inLanguage(language)) }
                }
                if (transfer.busy) {
                    SettingsStatus(DesktopText.SettingsTransferBusy, language, error = false)
                    TextButton(onClick = {
                        if (currentTransfer === transfer && currentTransfer.busy) currentCallbacks.onCancelTransfer()
                    }) {
                        Text(DesktopText.CancelSettingsTransfer.inLanguage(language))
                    }
                }
                transfer.errorCode?.let { SettingsStatus(desktopSettingsErrorText(it), language, error = true) }
                transfer.messageCode?.let { SettingsStatus(desktopSettingsMessageText(it), language, error = false) }
            }
        }
    }

    val preview = transfer.preview ?: return
    val id = transfer.previewId ?: return
    if (preferences == null) return
    val target = DesktopSettingsPreviewTarget(id, preview)
    val dismiss: () -> Unit = {
        if (target.canDismiss(currentTransfer)) {
            currentCallbacks.onDismissImport(id)
        }
    }
    val title = DesktopText.SettingsPreviewTitle.inLanguage(language)
    AlertDialog(onDismissRequest = dismiss, modifier = Modifier.semantics { paneTitle = title },
        title = { Text(title) }, text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(DesktopText.SettingsPreviewHint.inLanguage(language))
                listOf(Triple(DesktopText.Language, preferences.value.appLanguage, preview.appLanguage),
                    Triple(DesktopText.Appearance, preferences.value.appearanceMode, preview.appearanceMode),
                    Triple(DesktopText.AccentPalette, preferences.value.accentPalette, preview.accentPalette)).forEach { (field, before, after) ->
                    Text("${field.inLanguage(language)}: ${desktopSettingsValueLabel(field, before, language)} → ${desktopSettingsValueLabel(field, after, language)}")
                }
                if (desktopSettingsHaveUnsupportedValues(preview)) Text(DesktopText.SettingsUnsupportedValues.inLanguage(language))
                Text("${DesktopText.SmoothTransitions.inLanguage(language)}: " +
                    "${desktopSettingsBooleanLabel(preferences.value.smoothTrackTransitions, language)} → " +
                    desktopSettingsBooleanLabel(preview.smoothTrackTransitions, language))
                Text(DesktopText.RetainedSettings.inLanguage(language))
                val beforeRetained = desktopRetainedSettings(preferences.value, language)
                desktopRetainedSettings(preview, language).forEachIndexed { index, (field, after) ->
                    Text("${field.inLanguage(language)}: ${beforeRetained[index].second} → $after")
                }
                if (!preferences.writable) Text(DesktopText.SettingsReadOnly.inLanguage(language))
                preferences.errorCode?.let { SettingsStatus(desktopSettingsErrorText(it), language, error = true) }
                transfer.errorCode?.let { SettingsStatus(desktopSettingsErrorText(it), language, error = true) }
                if (transfer.busy) {
                    Text(DesktopText.SettingsTransferBusy.inLanguage(language))
                    TextButton(onClick = {
                        if (currentTransfer === transfer && target.matches(currentTransfer) && currentTransfer.busy) currentCallbacks.onCancelTransfer()
                    }) { Text(DesktopText.CancelSettingsTransfer.inLanguage(language)) }
                }
            }
        }, confirmButton = {
            Button(enabled = target.canConfirm(preferences, transfer), onClick = {
                if (target.canConfirm(currentPreferences, currentTransfer)) {
                    // Host admission owns double-submit fencing. A rejected/failed save keeps this preview usable.
                    currentCallbacks.onConfirmImport(id)
                }
            }) { Text(DesktopText.ApplySettingsImport.inLanguage(language)) }
        }, dismissButton = {
            TextButton(enabled = !transfer.busy, onClick = dismiss) { Text(DesktopText.DismissSettingsImport.inLanguage(language)) }
        })
}

@Composable
private fun SettingsOptions(content: @Composable () -> Unit): Unit {
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).selectableGroup(),
        horizontalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun SettingsStatus(text: DesktopText, language: DesktopLanguage, error: Boolean): Unit {
    Text(text.inLanguage(language), color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
}
