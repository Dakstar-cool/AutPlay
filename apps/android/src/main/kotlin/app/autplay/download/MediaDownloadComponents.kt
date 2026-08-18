package app.autplay.download

import android.content.Context
import androidx.media3.common.util.UnstableApi
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.NoOpCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.exoplayer.offline.DownloadManager
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import app.autplay.playback.VaultPlaybackDataSource

/** Process-singleton Media3 download execution state and physically separate cache policies. */
@UnstableApi
object MediaDownloadComponents {
    private val lock = Any()
    @Volatile private var state: State? = null

    fun get(context: Context): State = state ?: synchronized(lock) {
        state ?: create(context.applicationContext).also { state = it }
    }

    /** Instrumentation seam for exercising the real DownloadService with deterministic bytes. */
    internal fun replaceForTests(replacement: State?): State? = synchronized(lock) {
        state.also { state = replacement }
    }

    private fun create(context: Context): State {
        val provider = StandaloneDatabaseProvider(context)
        val downloadCache = SimpleCache(
            File(context.filesDir, "media3-download-cache"),
            NoOpCacheEvictor(),
            provider,
        )
        val streamCache = SimpleCache(
            File(context.cacheDir, "media3-stream-cache"),
            LeastRecentlyUsedCacheEvictor(STREAM_CACHE_BYTES),
            provider,
        )
        val http = DefaultHttpDataSource.Factory()
            .setAllowCrossProtocolRedirects(false)
            .setConnectTimeoutMs(10_000)
            .setReadTimeoutMs(15_000)
        val upstream = VaultPlaybackDataSource.productionFactory(context, http)
        val executor = Executors.newFixedThreadPool(MAX_PARALLEL_DOWNLOADS)
        val manager = DownloadManager(
            context,
            provider,
            downloadCache,
            upstream,
            executor,
        ).apply { maxParallelDownloads = MAX_PARALLEL_DOWNLOADS }
        return State(provider, downloadCache, streamCache, manager, executor)
    }

    data class State(
        val databaseProvider: StandaloneDatabaseProvider,
        val downloadCache: SimpleCache,
        val streamCache: SimpleCache,
        val downloadManager: DownloadManager,
        val downloadExecutor: ExecutorService,
    )

    private const val MAX_PARALLEL_DOWNLOADS = 2
    private const val STREAM_CACHE_BYTES = 128L * 1024 * 1024
}
