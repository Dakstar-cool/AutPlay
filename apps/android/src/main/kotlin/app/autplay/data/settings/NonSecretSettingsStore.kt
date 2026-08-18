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

/** Non-secret, device-local connection preferences. Credentials belong in [CredentialStore]. */
data class NonSecretSettings(
    val activeServerProfileId: ServerProfileId? = null,
    val activeUserId: UserId? = null,
    val deviceId: DeviceId? = null,
    val serverBaseUrl: String? = null,
    val syncOnMeteredNetwork: Boolean = false,
)

/**
 * Port for non-secret preferences. Implementations must never use it for bearer or refresh material.
 */
interface NonSecretSettingsStore {
    val settings: Flow<NonSecretSettings>

    suspend fun update(settings: NonSecretSettings)
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
            settings.activeServerProfileId?.let { preferences[ACTIVE_SERVER_PROFILE_ID] = it.value }
                ?: preferences.remove(ACTIVE_SERVER_PROFILE_ID)
            settings.activeUserId?.let { preferences[ACTIVE_USER_ID] = it.value }
                ?: preferences.remove(ACTIVE_USER_ID)
            settings.deviceId?.let { preferences[DEVICE_ID] = it.value }
                ?: preferences.remove(DEVICE_ID)
            settings.serverBaseUrl?.let { preferences[SERVER_BASE_URL] = it }
                ?: preferences.remove(SERVER_BASE_URL)
            preferences[SYNC_ON_METERED_NETWORK] = settings.syncOnMeteredNetwork
        }
    }

    private fun toSettings(preferences: Preferences): NonSecretSettings = NonSecretSettings(
        activeServerProfileId = preferences[ACTIVE_SERVER_PROFILE_ID]?.let(::ServerProfileId),
        activeUserId = preferences[ACTIVE_USER_ID]?.let(::UserId),
        deviceId = preferences[DEVICE_ID]?.let(::DeviceId),
        serverBaseUrl = preferences[SERVER_BASE_URL],
        syncOnMeteredNetwork = preferences[SYNC_ON_METERED_NETWORK] ?: false,
    )

    private fun validate(settings: NonSecretSettings) {
        val bindingParts = listOf(
            settings.activeServerProfileId,
            settings.activeUserId,
            settings.deviceId,
        )
        require(bindingParts.all { it == null } || bindingParts.all { it != null }) {
            "Server profile, authenticated user, and device binding must be stored together."
        }
        val baseUrl = settings.serverBaseUrl ?: return
        val uri = runCatching { URI(baseUrl) }.getOrNull()
        require(uri != null && uri.isAbsolute && uri.host != null && uri.scheme in setOf("http", "https")) {
            "Server base URL must be an absolute HTTP(S) URL with a host."
        }
        require(uri.userInfo == null && uri.query == null && uri.fragment == null) {
            "Server base URL must not contain credentials, a query, or a fragment."
        }
    }

    private companion object {
        val ACTIVE_SERVER_PROFILE_ID = stringPreferencesKey("active_server_profile_id")
        val ACTIVE_USER_ID = stringPreferencesKey("active_user_id")
        val DEVICE_ID = stringPreferencesKey("device_id")
        val SERVER_BASE_URL = stringPreferencesKey("server_base_url")
        val SYNC_ON_METERED_NETWORK = booleanPreferencesKey("sync_on_metered_network")
    }
}
