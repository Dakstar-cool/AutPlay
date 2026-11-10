package app.autplay.application.desktop

import kotlinx.coroutines.flow.StateFlow

/** Only portable non-secret preferences; paths, identities, credentials and playback are absent. */
data class DesktopPreferences(
    val appLanguage: String = "EN",
    val appearanceMode: String = "DARK",
    val accentPalette: String = "CORAL",
    val downloadOnMeteredNetwork: Boolean = false,
    val wavePrefetchMode: String = "NEXT",
    val smoothTrackTransitions: Boolean = false,
)

/** A failed/future settings file stays retained and read-only without disabling library playback. */
data class DesktopPreferencesState(
    val value: DesktopPreferences = DesktopPreferences(),
    val writable: Boolean = true,
    val errorCode: String? = null,
)

interface DesktopPreferencesStore {
    val state: StateFlow<DesktopPreferencesState>
    suspend fun setAppLanguage(value: String): Unit
    suspend fun setAppearanceMode(value: String): Unit
    suspend fun setAccentPalette(value: String): Unit
    suspend fun setSmoothTrackTransitions(value: Boolean): Unit
    /** Implementations must merge this single field under their actual write lock. */
    suspend fun setDownloadOnMeteredNetwork(value: Boolean): Unit =
        throw IllegalStateException("desktop_settings_metered_unsupported")
    /** Merges portable fields while preserving uninterpreted device-local extensions. */
    suspend fun applyPortable(value: DesktopPreferences): Unit
    suspend fun close(): Unit
}

/** Frozen import preview. UI receives no source/target path or external file capability. */
data class DesktopSettingsTransferState(
    val busy: Boolean = false,
    val previewId: String? = null,
    val preview: DesktopPreferences? = null,
    val errorCode: String? = null,
    val messageCode: String? = null,
)
