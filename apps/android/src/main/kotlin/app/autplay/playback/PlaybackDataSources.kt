package app.autplay.playback

import android.content.Context
import android.net.Uri
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.TransferListener
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import app.autplay.download.MediaDownloadComponents

/** Delegates only stable Vault URIs to auth-aware HTTP; local content stays in Android providers. */
@UnstableApi
class RoutingPlaybackDataSource(
    private val localFactory: DataSource.Factory,
    private val vaultFactory: DataSource.Factory,
) : DataSource {
    private val listeners = mutableListOf<TransferListener>()
    private var delegate: DataSource? = null

    override fun addTransferListener(transferListener: TransferListener) {
        listeners += transferListener
        delegate?.addTransferListener(transferListener)
    }

    override fun open(dataSpec: DataSpec): Long {
        check(delegate == null) { "Playback data source is already open." }
        val source = if (dataSpec.uri.scheme == AndroidPlaybackSourceResolver.VAULT_SCHEME) {
            vaultFactory.createDataSource()
        } else {
            localFactory.createDataSource()
        }
        listeners.forEach(source::addTransferListener)
        delegate = source
        return source.open(dataSpec)
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
        requireNotNull(delegate).read(buffer, offset, length)

    override fun getUri(): Uri? = delegate?.uri

    override fun getResponseHeaders(): Map<String, List<String>> =
        delegate?.responseHeaders ?: emptyMap()

    override fun close() {
        try {
            delegate?.close()
        } finally {
            delegate = null
        }
    }

    class Factory(context: Context) : DataSource.Factory {
        private val applicationContext = context.applicationContext
        private val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(false)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(15_000)
        private val local = DefaultDataSource.Factory(applicationContext, http)
        private val vault = VaultPlaybackDataSource.productionFactory(applicationContext, http)

        override fun createDataSource(): DataSource = RoutingPlaybackDataSource(local, vault)
    }
}

@UnstableApi
object PlaybackMediaSourceFactory {
    fun create(context: Context): DefaultMediaSourceFactory {
        val components = MediaDownloadComponents.get(context.applicationContext)
        val routing = RoutingPlaybackDataSource.Factory(context)
        val streamCache = CacheDataSource.Factory()
            .setCache(components.streamCache)
            .setUpstreamDataSourceFactory(routing)
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
        val downloadCache = CacheDataSource.Factory()
            .setCache(components.downloadCache)
            .setUpstreamDataSourceFactory(streamCache)
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
        return DefaultMediaSourceFactory(downloadCache)
    }
}
