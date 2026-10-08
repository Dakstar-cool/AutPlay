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
import androidx.media3.datasource.cache.Cache
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.mp3.Mp3Extractor
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
    internal fun create(
        context: Context,
        onTimeline: ((androidx.media3.common.MediaItem, PlaybackSourceTimeline) -> Unit)? = null,
    ): androidx.media3.exoplayer.source.MediaSource.Factory {
        val components = MediaDownloadComponents.get(context.applicationContext)
        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(false)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(15_000)
        val cachedVault = cachedVaultDataSourceFactory(
            components.downloadCache, components.streamCache,
            VaultPlaybackDataSource.productionFactory(context, http),
        )
        val routing = DataSource.Factory {
            // Local content already has durable storage; copying it into both caches delays reads.
            RoutingPlaybackDataSource(DefaultDataSource.Factory(context, http), cachedVault)
        }
        val delegate = DefaultMediaSourceFactory(routing, playbackExtractorsFactory())
        val reporting = onTimeline?.let { PlaybackSourceTimelineFactory(delegate, it) } ?: delegate
        return ResolvingMediaSourceFactory(reporting)
    }
}

@UnstableApi
internal fun cachedVaultDataSourceFactory(
    downloadCache: Cache,
    streamCache: Cache,
    upstream: DataSource.Factory,
): DataSource.Factory {
    val streaming = CacheDataSource.Factory()
        .setCache(streamCache)
        .setUpstreamDataSourceFactory(upstream)
        .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
    return CacheDataSource.Factory()
        .setCache(downloadCache)
        .setUpstreamDataSourceFactory(streaming)
        // Only DownloadManager may write durable bytes. Playback misses belong to the LRU.
        .setCacheWriteDataSinkFactory(null)
        .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
}

/** Keep native CBR/Xing/VBRI seeking; index only MP3 files whose seek table is missing. */
@UnstableApi
internal fun playbackExtractorsFactory(): DefaultExtractorsFactory = DefaultExtractorsFactory()
    .setMp3ExtractorFlags(Mp3Extractor.FLAG_ENABLE_INDEX_SEEKING)
