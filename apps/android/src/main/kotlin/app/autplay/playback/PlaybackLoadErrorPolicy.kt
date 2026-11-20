package app.autplay.playback

import androidx.media3.common.C
import androidx.media3.common.PlaybackException
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.exoplayer.upstream.LoadErrorHandlingPolicy
import java.io.IOException

internal class VaultAuthorizationUnavailableException : IOException("VAULT_AUTHORIZATION_UNAVAILABLE")
internal class VaultAuthorizationTransportException(cause: IOException) : IOException("VAULT_AUTHORIZATION_TRANSPORT_UNAVAILABLE", cause)

@UnstableApi
internal fun isTransientPlaybackFailure(error: Throwable): Boolean {
    val causes = generateSequence(error) { it.cause }.take(16).toList()
    if (causes.any { it is VaultAuthorizationUnavailableException }) return false
    if (causes.any { it is VaultAuthorizationTransportException }) return true
    if (causes.any { it is HttpDataSource.InvalidContentTypeException ||
            it is HttpDataSource.CleartextNotPermittedException || it is java.io.FileNotFoundException }) return false
    val http = causes.filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()
    if (http != null) return http.responseCode in setOf(408, 429, 500, 502, 503, 504)
    val playback = error as? PlaybackException
    if (playback != null && playback.errorCode != PlaybackException.ERROR_CODE_IO_UNSPECIFIED) return playback.errorCode in setOf(
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
    )
    return causes.any { it is HttpDataSource.HttpDataSourceException && it.reason in setOf(
        PlaybackException.ERROR_CODE_IO_UNSPECIFIED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
    ) }
}

/** Small loader budget plus the service budget; permanent HTTP failures fail immediately. */
@UnstableApi
internal class PlaybackLoadErrorPolicy : DefaultLoadErrorHandlingPolicy(2) {
    override fun getRetryDelayMsFor(loadErrorInfo: LoadErrorHandlingPolicy.LoadErrorInfo): Long =
        if (loadErrorInfo.errorCount > 2 || !isTransientPlaybackFailure(loadErrorInfo.exception)) C.TIME_UNSET
        else loadErrorInfo.errorCount * 1_000L
}
