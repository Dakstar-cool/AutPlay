package app.autplay.playback

import android.content.Context
import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.HttpUtil
import androidx.media3.datasource.TransferListener
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.datasource.cache.Cache
import androidx.media3.datasource.cache.CacheKeyFactory
import androidx.media3.datasource.cache.ContentMetadata
import androidx.media3.datasource.cache.ContentMetadataMutations
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.extractor.DefaultExtractorsFactory
import androidx.media3.extractor.mp3.Mp3Extractor
import app.autplay.download.MediaDownloadComponents
import java.io.IOException

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
    internal fun cachedVault(context: Context): DataSource.Factory {
        val components = MediaDownloadComponents.get(context.applicationContext)
        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(false)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(15_000)
        return cachedVaultDataSourceFactory(
            components.downloadCache, components.streamCache, VaultPlaybackDataSource.productionFactory(context, http),
        )
    }

    internal fun create(
        context: Context,
        onTimeline: ((androidx.media3.common.MediaItem, PlaybackSourceTimeline) -> Unit)? = null,
    ): androidx.media3.exoplayer.source.MediaSource.Factory {
        val cachedVault = cachedVault(context)
        val local = DefaultDataSource.Factory(context, DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(false).setConnectTimeoutMs(10_000).setReadTimeoutMs(15_000))
        val routing = DataSource.Factory {
            // Local content already has durable storage; copying it into both caches delays reads.
            RoutingPlaybackDataSource(local, cachedVault)
        }
        val delegate = DefaultMediaSourceFactory(routing, playbackExtractorsFactory())
            .setLoadErrorHandlingPolicy(PlaybackLoadErrorPolicy())
        val measured = object : androidx.media3.exoplayer.source.MediaSource.Factory by delegate {
            override fun createMediaSource(mediaItem: androidx.media3.common.MediaItem): androidx.media3.exoplayer.source.MediaSource {
                val loudness = mediaItem.localConfiguration?.tag as? ServerLoudness
                    ?: return delegate.createMediaSource(mediaItem)
                return DefaultMediaSourceFactory(routing, MeasuredAudioExtractors(loudness))
                    .setLoadErrorHandlingPolicy(PlaybackLoadErrorPolicy()).createMediaSource(mediaItem)
            }
        }
        val reporting = onTimeline?.let { PlaybackSourceTimelineFactory(measured, it) } ?: measured
        return ResolvingMediaSourceFactory(reporting)
    }
}

/** A short range uses the player's cache key and commits its span on close, even at EOF. */
@UnstableApi
internal suspend fun warmStreamPrefix(factory: DataSource.Factory, uri: Uri) =
    kotlinx.coroutines.runInterruptible(kotlinx.coroutines.Dispatchers.IO) {
        val source = factory.createDataSource()
        var remaining = 256 * 1024
        try {
            source.open(DataSpec.Builder().setUri(uri).setLength(remaining.toLong()).build())
            val buffer = ByteArray(16 * 1024)
            while (remaining > 0 && !Thread.currentThread().isInterrupted) {
                val count = source.read(buffer, 0, minOf(buffer.size, remaining))
                if (count <= 0) break
                remaining -= count
            }
        } finally {
            source.close()
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
    val downloads = CacheDataSource.Factory()
        .setCache(downloadCache)
        .setUpstreamDataSourceFactory(streaming)
        // Only DownloadManager may write durable bytes. Playback misses belong to the LRU.
        .setCacheWriteDataSinkFactory(null)
        .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
    return DataSource.Factory { VaultFileDataSource(downloads.createDataSource(), streamCache, upstream) }
}

/** Preserve the immutable file size when a speculative range is the first cached span. */
@UnstableApi
private class VaultFileDataSource(
    private val cached: CacheDataSource,
    private val streamCache: Cache,
    private val upstream: DataSource.Factory,
) : DataSource by cached {
    override fun open(dataSpec: DataSpec): Long {
        val length = cached.open(dataSpec)
        val key = CacheKeyFactory.DEFAULT.buildCacheKey(dataSpec)
        val range = cached.responseHeaders.entries.firstOrNull { it.key.equals("Content-Range", true) }
            ?.value?.firstOrNull()
        val total = try { HttpUtil.getDocumentSize(range) } catch (_: NumberFormatException) { C.LENGTH_UNSET.toLong() }
        if (total > 0) rememberLength(key, total)
        if (length != C.LENGTH_UNSET.toLong()) return length

        val saved = ContentMetadata.getContentLength(streamCache.getContentMetadata(key))
        if (saved >= dataSpec.position) return saved - dataSpec.position
        if (!streamCache.isCached(key, dataSpec.position, 1) && !cached.cache.isCached(key, dataSpec.position, 1)) return length

        // Older installations can have a prefix with no length. HEAD uses the same Vault
        // authorization as playback and fetches no audio. Cached bytes still play when offline.
        val probe = upstream.createDataSource()
        val size = try {
            try {
                probe.open(dataSpec.buildUpon().setHttpMethod(DataSpec.HTTP_METHOD_HEAD)
                    .setPosition(0).setLength(C.LENGTH_UNSET.toLong()).build())
            } finally { probe.close() }
        } catch (e: IOException) {
            if (Thread.currentThread().isInterrupted) throw e
            return length
        }
        if (size <= dataSpec.position) return length
        rememberLength(key, size)
        return size - dataSpec.position
    }

    private fun rememberLength(key: String, size: Long) {
        if (ContentMetadata.getContentLength(streamCache.getContentMetadata(key)) == size) return
        try {
            streamCache.applyContentMetadataMutations(key, ContentMetadataMutations.setContentLength(ContentMetadataMutations(), size))
        } catch (_: Cache.CacheException) {
            // Cache metadata is optional for the current play.
        }
    }
}

/** Keep native CBR/Xing/VBRI seeking; index only MP3 files whose seek table is missing. */
@UnstableApi
internal fun playbackExtractorsFactory(): DefaultExtractorsFactory = DefaultExtractorsFactory()
    .setMp3ExtractorFlags(Mp3Extractor.FLAG_ENABLE_INDEX_SEEKING)
