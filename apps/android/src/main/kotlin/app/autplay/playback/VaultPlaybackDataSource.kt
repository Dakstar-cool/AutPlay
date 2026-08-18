package app.autplay.playback

import android.content.Context
import android.net.Uri
import androidx.core.net.toUri
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.CredentialStore
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

data class SessionCredentialEnvelope(
    val accessToken: String,
    val refreshToken: String?,
    val generation: Long,
) {
    init {
        require(accessToken.isNotBlank() && accessToken.length <= MAX_TOKEN_CHARS)
        require(refreshToken == null || (refreshToken.isNotBlank() && refreshToken.length <= MAX_TOKEN_CHARS))
        require(generation >= 0)
    }

    private companion object { const val MAX_TOKEN_CHARS = 4_096 }
}

object SessionCredentialEnvelopeCodec {
    fun decode(material: ByteArray): SessionCredentialEnvelope {
        val raw = material.toString(StandardCharsets.UTF_8)
        if (!raw.trimStart().startsWith("{")) return SessionCredentialEnvelope(raw, null, 0)
        val value = Json.parseToJsonElement(raw).jsonObject
        return SessionCredentialEnvelope(
            accessToken = value.requiredString("access_token"),
            refreshToken = value["refresh_token"]?.let { element ->
                (element as? JsonPrimitive)?.takeUnless { it.content == "null" }?.content
            },
            generation = value["generation"]?.jsonPrimitive?.content?.toLongOrNull() ?: 0,
        )
    }

    fun encode(value: SessionCredentialEnvelope): ByteArray = buildJsonObject {
        put("access_token", value.accessToken)
        value.refreshToken?.let { put("refresh_token", it) }
        put("generation", value.generation)
    }.toString().toByteArray(StandardCharsets.UTF_8)

    private fun JsonObject.requiredString(name: String): String =
        requireNotNull(this[name]) { "CREDENTIAL_ENVELOPE_INVALID" }.jsonPrimitive.content
}

data class VaultAuthorization(
    val streamUri: Uri,
    val accessToken: String,
    val generation: Long,
)

interface VaultAuthorizationProvider {
    /** A rejected generation triggers at most one single-flight refresh before returning. */
    fun authorize(profileId: ServerProfileId, audioVariantId: String, rejectedGeneration: Long? = null): VaultAuthorization?
}

/** Reads encrypted credentials and rotates refresh tokens without exposing them to logs or Room. */
class RefreshingVaultAuthorizationProvider(
    private val settings: NonSecretSettingsStore,
    private val credentials: CredentialStore,
) : VaultAuthorizationProvider {
    private val refreshMutex = Mutex()

    override fun authorize(
        profileId: ServerProfileId,
        audioVariantId: String,
        rejectedGeneration: Long?,
    ): VaultAuthorization? = runBlocking(Dispatchers.IO) {
        val configured = settings.settings.first()
        if (configured.activeServerProfileId != profileId) return@runBlocking null
        val baseUrl = configured.serverBaseUrl ?: return@runBlocking null
        var envelope = readEnvelope(profileId) ?: return@runBlocking null
        if (rejectedGeneration != null && rejectedGeneration == envelope.generation) {
            envelope = refreshMutex.withLock {
                val current = readEnvelope(profileId) ?: return@withLock null
                if (current.generation != rejectedGeneration) current
                else refresh(baseUrl, current)?.also { credentials.write(profileId, SessionCredentialEnvelopeCodec.encode(it)) }
            } ?: return@runBlocking null
        }
        VaultAuthorization(
            streamUri = (baseUrl.trimEnd('/') + "/api/v1/stream/audio-variants/" + audioVariantId).toUri(),
            accessToken = envelope.accessToken,
            generation = envelope.generation,
        )
    }

    private suspend fun readEnvelope(profileId: ServerProfileId): SessionCredentialEnvelope? =
        credentials.read(profileId)?.let(SessionCredentialEnvelopeCodec::decode)

    private suspend fun refresh(baseUrl: String, current: SessionCredentialEnvelope): SessionCredentialEnvelope? =
        withContext(Dispatchers.IO) {
            val refreshToken = current.refreshToken ?: return@withContext null
            val endpoint = URI(baseUrl.trimEnd('/') + "/api/v1/auth/refresh").toURL()
            val connection = endpoint.openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = 10_000
                connection.readTimeout = 10_000
                connection.instanceFollowRedirects = false
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("Accept", "application/json")
                val body = buildJsonObject { put("refresh_token", refreshToken) }
                    .toString().toByteArray(StandardCharsets.UTF_8)
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
                if (connection.responseCode != 200) return@withContext null
                val response = connection.inputStream.bufferedReader(StandardCharsets.UTF_8).use { reader ->
                    val text = reader.readText()
                    require(text.toByteArray(StandardCharsets.UTF_8).size <= MAX_REFRESH_RESPONSE_BYTES)
                    Json.parseToJsonElement(text).jsonObject
                }
                SessionCredentialEnvelope(
                    accessToken = response.requiredString("access_token"),
                    refreshToken = response.requiredString("refresh_token"),
                    generation = current.generation + 1,
                )
            } catch (_: IOException) {
                null
            } finally {
                connection.disconnect()
            }
        }

    private fun JsonObject.requiredString(name: String): String =
        requireNotNull(this[name]) { "AUTH_REFRESH_RESPONSE_INVALID" }.jsonPrimitive.content

    private companion object { const val MAX_REFRESH_RESPONSE_BYTES = 64 * 1024 }
}

/** Resolves the synthetic stable Vault URI at every open and retries one rejected generation. */
@UnstableApi
class VaultPlaybackDataSource(
    private val upstreamFactory: HttpDataSource.Factory,
    private val authorizationProvider: VaultAuthorizationProvider,
) : DataSource {
    private val transferListeners = mutableListOf<TransferListener>()
    private var delegate: HttpDataSource? = null
    private var openedUri: Uri? = null

    override fun addTransferListener(transferListener: TransferListener) {
        transferListeners += transferListener
        delegate?.addTransferListener(transferListener)
    }

    override fun open(dataSpec: DataSpec): Long {
        val stable = parseStableUri(dataSpec.uri)
        val first = authorizationProvider.authorize(stable.profileId, stable.audioVariantId)
            ?: throw IOException("VAULT_AUTHORIZATION_UNAVAILABLE")
        return tryOpen(dataSpec, first, allowRefresh = true, stable = stable)
    }

    private fun tryOpen(
        original: DataSpec,
        authorization: VaultAuthorization,
        allowRefresh: Boolean,
        stable: StableVaultReference,
    ): Long {
        val source = upstreamFactory.createDataSource().also { created ->
            transferListeners.forEach(created::addTransferListener)
            delegate = created
        }
        val request = original.buildUpon()
            .setUri(authorization.streamUri)
            .setHttpRequestHeaders(original.httpRequestHeaders +
                mapOf("Authorization" to "Bearer ${authorization.accessToken}"))
            .build()
        return try {
            source.open(request).also { openedUri = authorization.streamUri }
        } catch (error: HttpDataSource.InvalidResponseCodeException) {
            source.close()
            delegate = null
            if (!allowRefresh || error.responseCode != 401) throw error
            val refreshed = authorizationProvider.authorize(
                stable.profileId,
                stable.audioVariantId,
                authorization.generation,
            ) ?: throw error
            tryOpen(original, refreshed, allowRefresh = false, stable = stable)
        }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
        requireNotNull(delegate) { "Vault data source is not open." }.read(buffer, offset, length)

    override fun getUri(): Uri? = openedUri

    override fun getResponseHeaders(): Map<String, List<String>> =
        delegate?.responseHeaders ?: emptyMap()

    override fun close() {
        try {
            delegate?.close()
        } finally {
            delegate = null
            openedUri = null
        }
    }

    private fun parseStableUri(uri: Uri): StableVaultReference {
        require(uri.scheme == AndroidPlaybackSourceResolver.VAULT_SCHEME) { "VAULT_URI_REQUIRED" }
        val profile = ServerProfileId(requireNotNull(uri.authority) { "VAULT_PROFILE_REQUIRED" })
        require(uri.pathSegments.size == 2 && uri.pathSegments[0] == "audio-variants") {
            "VAULT_VARIANT_REQUIRED"
        }
        return StableVaultReference(profile, uri.pathSegments[1])
    }

    private data class StableVaultReference(
        val profileId: ServerProfileId,
        val audioVariantId: String,
    )

    class Factory(
        upstreamFactory: HttpDataSource.Factory,
        authorizationProvider: VaultAuthorizationProvider,
    ) : DataSource.Factory {
        private val upstream = upstreamFactory
        private val provider = authorizationProvider
        override fun createDataSource(): DataSource = VaultPlaybackDataSource(upstream, provider)
    }

    companion object {
        fun productionFactory(context: Context, upstreamFactory: HttpDataSource.Factory): Factory = Factory(
            upstreamFactory,
            RefreshingVaultAuthorizationProvider(
                applicationNonSecretSettingsStore(context.applicationContext),
                AndroidKeystoreCredentialStore(context.applicationContext),
            ),
        )
    }
}
