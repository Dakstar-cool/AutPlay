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
    /** M5 non-secret checkpoint. A matching secret marker is required before remote use. */
    val m5Binding: M5BindingCheckpoint? = null,
    /** Signed public identity/capability evidence needed to fail closed across process restart. */
    val m5TrustEvidence: M5TrustEvidence? = null,
    /** Explicit M5 first-binding data decision; standalone local data itself is never rewritten. */
    val m5LocalDataDecision: String? = null,
    /** Versioned non-secret exchange replay checkpoint; encrypted companion holds bearers. */
    val m5PendingExchangeCheckpoint: String? = null,
    /** Durable cancellation tombstone that prevents a racing exchange from publishing a checkpoint. */
    val m5CancelledPairingGenerationId: String? = null,
)

/** Sensitive non-secret local evidence for a single M5 binding; it is never an authority token. */
data class M5BindingCheckpoint(
    val bindingCommitId: String,
    val serverInstanceId: String,
    val identityEpoch: Long,
    val identityThumbprintSha256: String,
    val deviceKeyAlias: String,
    val sessionId: String,
    val sessionFamilyId: String,
    val sessionGeneration: Long,
) {
    init {
        require(UUID_PATTERN.matches(bindingCommitId) && UUID_PATTERN.matches(serverInstanceId))
        require(UUID_PATTERN.matches(sessionId) && UUID_PATTERN.matches(sessionFamilyId))
        require(identityEpoch >= 1 && sessionGeneration >= 0)
        require(SHA256_PATTERN.matches(identityThumbprintSha256))
        require(deviceKeyAlias.isNotBlank() && deviceKeyAlias.length <= 160)
    }
    companion object {
        private val UUID_PATTERN = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        private val SHA256_PATTERN = Regex("^[0-9a-f]{64}$")
    }
}

data class M5TrustEvidence(
    val identityPublicKeySpkiB64: String,
    val serverLabelHint: String? = null,
    val capabilitySignedPayloadB64: String? = null,
    val capabilityPayloadSha256: String? = null,
    val capabilityRevisionHighWater: Long? = null,
) {
    init {
        require(identityPublicKeySpkiB64.length in 16..8_192)
        require(serverLabelHint == null || serverLabelHint.length in 1..80)
        val capability = listOf(capabilitySignedPayloadB64, capabilityPayloadSha256, capabilityRevisionHighWater)
        require(capability.all { it == null } || capability.all { it != null })
        require(capabilityPayloadSha256 == null || SHA256_PATTERN.matches(capabilityPayloadSha256))
        require(capabilityRevisionHighWater == null || capabilityRevisionHighWater >= 1)
    }
    companion object { private val SHA256_PATTERN = Regex("^[0-9a-f]{64}$") }
}

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
        settings.m5Binding?.let { binding ->
            preferences[M5_BINDING_COMMIT_ID] = binding.bindingCommitId
            preferences[M5_SERVER_INSTANCE_ID] = binding.serverInstanceId
            preferences[M5_IDENTITY_EPOCH] = binding.identityEpoch.toString()
            preferences[M5_IDENTITY_THUMBPRINT] = binding.identityThumbprintSha256
            preferences[M5_DEVICE_KEY_ALIAS] = binding.deviceKeyAlias
            preferences[M5_SESSION_ID] = binding.sessionId
            preferences[M5_SESSION_FAMILY_ID] = binding.sessionFamilyId
            preferences[M5_SESSION_GENERATION] = binding.sessionGeneration.toString()
        } ?: M5_KEYS.forEach(preferences::remove)
        settings.m5TrustEvidence?.let { evidence ->
            preferences[M5_IDENTITY_SPKI] = evidence.identityPublicKeySpkiB64
            evidence.serverLabelHint?.let { preferences[M5_SERVER_LABEL_HINT] = it }
                ?: preferences.remove(M5_SERVER_LABEL_HINT)
            evidence.capabilitySignedPayloadB64?.let { preferences[M5_CAPABILITY_PAYLOAD] = it }
                ?: preferences.remove(M5_CAPABILITY_PAYLOAD)
            evidence.capabilityPayloadSha256?.let { preferences[M5_CAPABILITY_HASH] = it }
                ?: preferences.remove(M5_CAPABILITY_HASH)
            evidence.capabilityRevisionHighWater?.let { preferences[M5_CAPABILITY_REVISION] = it.toString() }
                ?: preferences.remove(M5_CAPABILITY_REVISION)
        } ?: M5_TRUST_KEYS.forEach(preferences::remove)
        settings.m5LocalDataDecision?.let { preferences[M5_LOCAL_DATA_DECISION] = it }
            ?: preferences.remove(M5_LOCAL_DATA_DECISION)
        settings.m5PendingExchangeCheckpoint?.let { preferences[M5_PENDING_EXCHANGE] = it }
            ?: preferences.remove(M5_PENDING_EXCHANGE)
        settings.m5CancelledPairingGenerationId?.let { preferences[M5_CANCELLED_GENERATION] = it }
            ?: preferences.remove(M5_CANCELLED_GENERATION)
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
        m5Binding = m5Binding(preferences),
        m5TrustEvidence = m5TrustEvidence(preferences),
        m5LocalDataDecision = preferences[M5_LOCAL_DATA_DECISION],
        m5PendingExchangeCheckpoint = preferences[M5_PENDING_EXCHANGE],
        m5CancelledPairingGenerationId = preferences[M5_CANCELLED_GENERATION],
        )
    }

    private fun m5Binding(preferences: Preferences): M5BindingCheckpoint? {
        val values = listOf(M5_BINDING_COMMIT_ID, M5_SERVER_INSTANCE_ID, M5_IDENTITY_EPOCH, M5_IDENTITY_THUMBPRINT, M5_DEVICE_KEY_ALIAS, M5_SESSION_ID, M5_SESSION_FAMILY_ID, M5_SESSION_GENERATION)
        if (values.all { preferences[it] == null }) return null
        require(values.all { preferences[it] != null }) { "M5 binding checkpoint is incomplete." }
        return M5BindingCheckpoint(
            preferences[M5_BINDING_COMMIT_ID]!!, preferences[M5_SERVER_INSTANCE_ID]!!,
            preferences[M5_IDENTITY_EPOCH]!!.toLong(), preferences[M5_IDENTITY_THUMBPRINT]!!,
            preferences[M5_DEVICE_KEY_ALIAS]!!, preferences[M5_SESSION_ID]!!,
            preferences[M5_SESSION_FAMILY_ID]!!, preferences[M5_SESSION_GENERATION]!!.toLong(),
        )
    }

    private fun m5TrustEvidence(preferences: Preferences): M5TrustEvidence? {
        val spki = preferences[M5_IDENTITY_SPKI] ?: return null
        val capability = listOf(M5_CAPABILITY_PAYLOAD, M5_CAPABILITY_HASH, M5_CAPABILITY_REVISION)
        require(capability.all { preferences[it] == null } || capability.all { preferences[it] != null }) {
            "M5 capability evidence is incomplete."
        }
        return M5TrustEvidence(
            spki,
            preferences[M5_SERVER_LABEL_HINT],
            preferences[M5_CAPABILITY_PAYLOAD],
            preferences[M5_CAPABILITY_HASH],
            preferences[M5_CAPABILITY_REVISION]?.toLong(),
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
        settings.m5Binding?.let {
            require(bindingParts.all { part -> part != null }) { "M5 binding requires an active profile, account, and device." }
        }
        if (settings.m5TrustEvidence != null) {
            require(settings.m5Binding != null || settings.m5PendingExchangeCheckpoint != null) {
                "M5 trust evidence requires an active binding or pending exchange."
            }
        }
        require(settings.m5LocalDataDecision == null || settings.m5LocalDataDecision in setOf("KEEP_LOCAL", "REVIEW_SELECTED"))
        if (settings.m5LocalDataDecision != null) require(settings.m5Binding != null)
        require(settings.m5PendingExchangeCheckpoint == null || settings.m5PendingExchangeCheckpoint.length <= 4096)
        require(
            settings.m5CancelledPairingGenerationId == null ||
                runCatching {
                    java.util.UUID.fromString(settings.m5CancelledPairingGenerationId).toString() ==
                        settings.m5CancelledPairingGenerationId
                }.getOrDefault(false),
        )
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
        val M5_BINDING_COMMIT_ID = stringPreferencesKey("m5_binding_commit_id")
        val M5_SERVER_INSTANCE_ID = stringPreferencesKey("m5_server_instance_id")
        val M5_IDENTITY_EPOCH = stringPreferencesKey("m5_identity_epoch")
        val M5_IDENTITY_THUMBPRINT = stringPreferencesKey("m5_identity_thumbprint_sha256")
        val M5_DEVICE_KEY_ALIAS = stringPreferencesKey("m5_device_key_alias")
        val M5_SESSION_ID = stringPreferencesKey("m5_session_id")
        val M5_SESSION_FAMILY_ID = stringPreferencesKey("m5_session_family_id")
        val M5_SESSION_GENERATION = stringPreferencesKey("m5_session_generation")
        val M5_IDENTITY_SPKI = stringPreferencesKey("m5_identity_spki_b64")
        val M5_SERVER_LABEL_HINT = stringPreferencesKey("m5_server_label_hint")
        val M5_CAPABILITY_PAYLOAD = stringPreferencesKey("m5_capability_payload_b64")
        val M5_CAPABILITY_HASH = stringPreferencesKey("m5_capability_payload_sha256")
        val M5_CAPABILITY_REVISION = stringPreferencesKey("m5_capability_revision_high_water")
        val M5_LOCAL_DATA_DECISION = stringPreferencesKey("m5_local_data_decision")
        val M5_PENDING_EXCHANGE = stringPreferencesKey("m5_pending_exchange_checkpoint")
        val M5_CANCELLED_GENERATION = stringPreferencesKey("m5_cancelled_pairing_generation_id")
        val M5_KEYS = listOf(M5_BINDING_COMMIT_ID, M5_SERVER_INSTANCE_ID, M5_IDENTITY_EPOCH, M5_IDENTITY_THUMBPRINT, M5_DEVICE_KEY_ALIAS, M5_SESSION_ID, M5_SESSION_FAMILY_ID, M5_SESSION_GENERATION)
        val M5_TRUST_KEYS = listOf(M5_IDENTITY_SPKI, M5_SERVER_LABEL_HINT, M5_CAPABILITY_PAYLOAD, M5_CAPABILITY_HASH, M5_CAPABILITY_REVISION)
    }
}
