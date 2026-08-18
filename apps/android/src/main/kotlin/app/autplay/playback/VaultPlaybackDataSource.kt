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
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking

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
    override fun authorize(
        profileId: ServerProfileId,
        audioVariantId: String,
        rejectedGeneration: Long?,
    ): VaultAuthorization? = runBlocking(Dispatchers.IO) {
        val configured = settings.settings.first()
        if (configured.activeServerProfileId != profileId) return@runBlocking null
        val apiBaseUrl = configured.serverBaseUrl ?: return@runBlocking null
        val streamBaseUrl = configured.streamBaseUrl ?: return@runBlocking null
        val session = RefreshingSessionCredentials(apiBaseUrl.trimEnd('/') + "/api/v1", credentials)
        val access = runCatching {
            if (rejectedGeneration == null) session.access(profileId)
            else session.refreshAfterRejection(profileId, rejectedGeneration)
        }.getOrNull() ?: return@runBlocking null
        VaultAuthorization(
            streamUri = (streamBaseUrl.trimEnd('/') + "/api/v1/stream/audio-variants/" + audioVariantId).toUri(),
            accessToken = access.token.toString(StandardCharsets.UTF_8),
            generation = access.generation,
        ).also {
            access.close()
        }
    }
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
