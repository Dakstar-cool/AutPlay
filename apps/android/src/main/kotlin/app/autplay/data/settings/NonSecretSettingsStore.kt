package app.autplay.data.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import app.autplay.domain.ServerProfileId
import app.autplay.domain.DeviceId
import app.autplay.domain.UserId
import java.io.IOException
import java.net.URI
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.first

/** Non-secret, device-local connection preferences. Credentials belong in [CredentialStore]. */
data class NonSecretSettings(
    val activeServerProfileId: ServerProfileId? = null,
    val activeUserId: UserId? = null,
    val deviceId: DeviceId? = null,
    /** API service origin. Fixed `/api/v1` paths are appended by transports. */
    val serverBaseUrl: String? = null,
    /** Stream service origin. It may differ from the API origin in local deployments. */
    val streamBaseUrl: String? = null,
    val syncOnMeteredNetwork: Boolean = false,
    val appearanceMode: String = "SYSTEM",
    val accentPalette: String = "CORAL",
    val libraryRootTreeUri: String? = null,
    val wavePrefetchMode: String = "NEXT",
)

/**
 * Port for non-secret preferences. Implementations must never use it for bearer or refresh material.
 */
interface NonSecretSettingsStore {
    val settings: Flow<NonSecretSettings>

    suspend fun update(settings: NonSecretSettings)

    /** Applies a field-level change against the latest stored value. */
    suspend fun mutate(transform: (NonSecretSettings) -> NonSecretSettings) {
        update(transform(settings.first()))
    }
}

/** DataStore Preferences adapter for [NonSecretSettingsStore]. */
class DataStoreNonSecretSettingsStore(
    private val dataStore: DataStore<Preferences>,
) : NonSecretSettingsStore {
    override val settings: Flow<NonSecretSettings> = dataStore.data
        .catch { error ->
            if (error is IOException) emit(emptyPreferences()) else throw error
        }
        .map(::toSettings)

    override suspend fun update(settings: NonSecretSettings) {
        validate(settings)
        dataStore.edit { preferences ->
            write(preferences, settings)
        }
    }

    override suspend fun mutate(transform: (NonSecretSettings) -> NonSecretSettings) {
        dataStore.edit { preferences ->
            val updated = transform(toSettings(preferences))
            validate(updated)
            write(preferences, updated)
        }
    }

    private fun write(preferences: androidx.datastore.preferences.core.MutablePreferences, settings: NonSecretSettings) {
        settings.activeServerProfileId?.let { preferences[ACTIVE_SERVER_PROFILE_ID] = it.value }
                ?: preferences.remove(ACTIVE_SERVER_PROFILE_ID)
        settings.activeUserId?.let { preferences[ACTIVE_USER_ID] = it.value }
                ?: preferences.remove(ACTIVE_USER_ID)
        settings.deviceId?.let { preferences[DEVICE_ID] = it.value }
                ?: preferences.remove(DEVICE_ID)
        settings.serverBaseUrl?.let { preferences[API_SERVICE_BASE_URL] = it }
                ?: preferences.remove(API_SERVICE_BASE_URL)
        settings.streamBaseUrl?.let { preferences[STREAM_SERVICE_BASE_URL] = it }
                ?: preferences.remove(STREAM_SERVICE_BASE_URL)
        preferences.remove(LEGACY_SERVER_BASE_URL)
        preferences[SYNC_ON_METERED_NETWORK] = settings.syncOnMeteredNetwork
        preferences[APPEARANCE_MODE] = settings.appearanceMode
        preferences[ACCENT_PALETTE] = settings.accentPalette
        settings.libraryRootTreeUri?.let { preferences[LIBRARY_ROOT_TREE_URI] = it }
                ?: preferences.remove(LIBRARY_ROOT_TREE_URI)
        preferences[WAVE_PREFETCH_MODE] = settings.wavePrefetchMode
    }

    private fun toSettings(preferences: Preferences): NonSecretSettings {
        val legacyOrigin = preferences[LEGACY_SERVER_BASE_URL]
        val apiOrigin = preferences[API_SERVICE_BASE_URL] ?: legacyOrigin
        return NonSecretSettings(
        activeServerProfileId = preferences[ACTIVE_SERVER_PROFILE_ID]?.let(::ServerProfileId),
        activeUserId = preferences[ACTIVE_USER_ID]?.let(::UserId),
        deviceId = preferences[DEVICE_ID]?.let(::DeviceId),
        serverBaseUrl = apiOrigin,
        streamBaseUrl = preferences[STREAM_SERVICE_BASE_URL] ?: legacyOrigin ?: apiOrigin,
        syncOnMeteredNetwork = preferences[SYNC_ON_METERED_NETWORK] ?: false,
        appearanceMode = preferences[APPEARANCE_MODE] ?: "SYSTEM",
        accentPalette = preferences[ACCENT_PALETTE] ?: "CORAL",
        libraryRootTreeUri = preferences[LIBRARY_ROOT_TREE_URI],
        wavePrefetchMode = preferences[WAVE_PREFETCH_MODE] ?: "NEXT",
        )
    }

    private fun validate(settings: NonSecretSettings) {
        val bindingParts = listOf(
            settings.activeServerProfileId,
            settings.activeUserId,
            settings.deviceId,
        )
        require(bindingParts.all { it == null } || bindingParts.all { it != null }) {
            "Server profile, authenticated user, and device binding must be stored together."
        }
        settings.libraryRootTreeUri?.let { treeUri ->
            val parsed = runCatching { URI(treeUri) }.getOrNull()
            require(parsed != null && parsed.scheme == "content" && parsed.authority != null) {
                "Library root must be a scoped Android content tree URI."
            }
        }
        require(settings.streamBaseUrl == null || settings.serverBaseUrl != null) {
            "A stream service origin requires an API service origin."
        }
        settings.serverBaseUrl?.let { validateServiceOrigin(it, "API") }
        settings.streamBaseUrl?.let { validateServiceOrigin(it, "stream") }
    }

    private fun validateServiceOrigin(value: String, label: String) {
        val uri = runCatching { URI(value) }.getOrNull()
        require(uri != null && uri.isAbsolute && uri.host != null && uri.scheme in setOf("http", "https")) {
            "$label service origin must be an absolute HTTP(S) URL with a host."
        }
        require(uri.userInfo == null && uri.query == null && uri.fragment == null && uri.path in setOf("", "/")) {
            "$label service origin must not contain credentials, a path, query, or fragment."
        }
    }

    private companion object {
        val ACTIVE_SERVER_PROFILE_ID = stringPreferencesKey("active_server_profile_id")
        val ACTIVE_USER_ID = stringPreferencesKey("active_user_id")
        val DEVICE_ID = stringPreferencesKey("device_id")
        val LEGACY_SERVER_BASE_URL = stringPreferencesKey("server_base_url")
        val API_SERVICE_BASE_URL = stringPreferencesKey("api_service_base_url")
        val STREAM_SERVICE_BASE_URL = stringPreferencesKey("stream_service_base_url")
        val SYNC_ON_METERED_NETWORK = booleanPreferencesKey("sync_on_metered_network")
        val APPEARANCE_MODE = stringPreferencesKey("appearance_mode")
        val ACCENT_PALETTE = stringPreferencesKey("accent_palette")
        val LIBRARY_ROOT_TREE_URI = stringPreferencesKey("library_root_tree_uri")
        val WAVE_PREFETCH_MODE = stringPreferencesKey("wave_prefetch_mode")
    }
}
