package app.autplay.download

import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.TransferListener
import androidx.media3.datasource.cache.NoOpCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.exoplayer.offline.Download
import androidx.media3.exoplayer.offline.DownloadManager
import androidx.media3.exoplayer.offline.DownloadRequest
import androidx.media3.exoplayer.offline.DownloadService
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.data.local.entity.DownloadIntentEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import java.io.File
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class Media3DownloadRuntimeTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun interruptedProgressiveDownloadResumesFromCachedRangeAndCompletes() {
        val directory = File(context.cacheDir, "p08-download-${System.nanoTime()}").apply { mkdirs() }
        val databaseProvider = StandaloneDatabaseProvider(context)
        val cache = SimpleCache(directory, NoOpCacheEvictor(), databaseProvider)
        val sourceFactory = InterruptingDataSourceFactory(ByteArray(64 * 1024) { (it % 251).toByte() })
        val executor = Executors.newFixedThreadPool(2)
        val manager = DownloadManager(context, databaseProvider, cache, sourceFactory, executor)
        val cacheKey = "range-resume-${UUID.randomUUID()}"
        val terminal = CountDownLatch(1)
        val result = AtomicReference<Download>()
        manager.addListener(object : DownloadManager.Listener {
            override fun onDownloadChanged(manager: DownloadManager, download: Download, finalException: Exception?) {
                if (download.state == Download.STATE_COMPLETED || download.state == Download.STATE_FAILED) {
                    result.set(download)
                    terminal.countDown()
                }
            }
        })
        try {
            manager.addDownload(
                DownloadRequest.Builder(cacheKey, Uri.parse("test://audio/range-resume"))
                    .setMimeType(MimeTypes.AUDIO_MPEG)
                    .setCustomCacheKey(cacheKey)
                    .build(),
            )
            manager.resumeDownloads()
            assertTrue("Media3 download did not terminate", terminal.await(30, TimeUnit.SECONDS))
            assertEquals(Download.STATE_COMPLETED, result.get().state)
            assertTrue("Expected a resumed non-zero range", sourceFactory.openPositions.any { it > 0 })
            assertEquals(sourceFactory.payloadSize.toLong(), cache.getCachedBytes(cacheKey, 0, sourceFactory.payloadSize.toLong()))
        } finally {
            manager.release()
            executor.shutdownNow()
            cache.release()
            databaseProvider.close()
            directory.deleteRecursively()
        }
    }

    @Test fun productionDownloadRuntimeIsSingletonAndBackgroundServiceIsRegistered() {
        val first = MediaDownloadComponents.get(context)
        val second = MediaDownloadComponents.get(context)
        assertSame(first.downloadManager, second.downloadManager)
        assertSame(first.downloadCache, second.downloadCache)
        val service = context.packageManager.getServiceInfo(
            ComponentName(context, AutPlayDownloadService::class.java),
            0,
        )
        assertNotNull(service)
        assertTrue(service.enabled)
    }

    @Test fun realDownloadServiceReconcilesCompletionAfterServiceRecreation() = runBlocking {
        context.stopService(Intent(context, AutPlayDownloadService::class.java))
        val directory = File(context.cacheDir, "p08-service-download-${System.nanoTime()}").apply { mkdirs() }
        val databaseProvider = StandaloneDatabaseProvider(context)
        val cache = SimpleCache(directory, NoOpCacheEvictor(), databaseProvider)
        val sourceFactory = InterruptingDataSourceFactory(ByteArray(64 * 1024) { (it % 239).toByte() })
        val executor = Executors.newFixedThreadPool(2)
        val manager = DownloadManager(context, databaseProvider, cache, sourceFactory, executor).apply {
            maxParallelDownloads = 1
        }
        val streamDirectory = File(context.cacheDir, "p08-service-stream-${System.nanoTime()}").apply { mkdirs() }
        val streamCache = SimpleCache(streamDirectory, NoOpCacheEvictor(), databaseProvider)
        val replacement = MediaDownloadComponents.State(databaseProvider, cache, streamCache, manager, executor)
        val previous = MediaDownloadComponents.replaceForTests(replacement)
        val database = AutPlayRuntime.database(context)
        val trackId = UUID(0, 81).toString()
        val intentId = UUID(0, 82).toString()
        database.libraryDao().upsertTrackRef(track(trackId))
        database.localAudioDao().upsertDownloadIntent(
            DownloadIntentEntity(
                downloadIntentId = intentId,
                localUserTrackRefId = trackId,
                serverAudioVariantId = UUID(0, 83).toString(),
                media3DownloadId = intentId,
                desiredStorageClass = "PROACTIVE_CACHE",
                qualityPolicy = "DIRECT_ORIGINAL",
                sourcePolicy = "VAULT_ONLY",
                state = "REQUESTED",
                failureCode = null,
                createdAtMs = 1,
                updatedAtMs = 1,
                completedAtMs = null,
                serverProfileId = UUID(0, 84).toString(),
                lastAccessedAtMs = null,
            ),
        )
        val terminal = CountDownLatch(1)
        manager.addListener(object : DownloadManager.Listener {
            override fun onDownloadChanged(manager: DownloadManager, download: Download, finalException: Exception?) {
                if (download.state == Download.STATE_COMPLETED) terminal.countDown()
            }
        })
        try {
            val request = DownloadRequest.Builder(intentId, Uri.parse("test://audio/service"))
                .setMimeType(MimeTypes.AUDIO_MPEG)
                .setCustomCacheKey(intentId)
                .build()
            DownloadService.sendAddDownload(context, AutPlayDownloadService::class.java, request, false)
            assertTrue("expected injected interruption", sourceFactory.interrupted.await(10, TimeUnit.SECONDS))
            context.stopService(Intent(context, AutPlayDownloadService::class.java))
            // This is the public Media3 service command path used after a service/process restart.
            // The test never resumes the DownloadManager directly or completes the download itself.
            DownloadService.sendResumeDownloads(context, AutPlayDownloadService::class.java, false)
            assertTrue("download did not resume through DownloadService", terminal.await(30, TimeUnit.SECONDS))
            await("Room completion reconciliation") {
                runBlocking { database.localAudioDao().downloadIntent(intentId)?.state == "COMPLETED" }
            }
            assertEquals("COMPLETED", database.localAudioDao().downloadIntent(intentId)?.state)
        } finally {
            context.stopService(Intent(context, AutPlayDownloadService::class.java))
            MediaDownloadComponents.replaceForTests(previous)
            manager.release()
            executor.shutdownNow()
            streamCache.release()
            cache.release()
            databaseProvider.close()
            streamDirectory.deleteRecursively()
            directory.deleteRecursively()
        }
    }

    private fun await(label: String, timeoutMs: Long = 10_000, condition: () -> Boolean) {
        val deadline = android.os.SystemClock.elapsedRealtime() + timeoutMs
        while (!condition() && android.os.SystemClock.elapsedRealtime() < deadline) {
            android.os.SystemClock.sleep(50)
        }
        assertTrue("Timed out waiting for $label", condition())
    }

    private fun track(id: String) = UserTrackRefEntity(
        localUserTrackRefId = id,
        serverUserTrackRefId = null,
        localRecordingId = null,
        serverRecordingId = null,
        resolutionStatus = "UNRESOLVED",
        rawTitle = "Download service",
        rawArtist = "AutPlay",
        rawAlbum = null,
        rawDurationMs = null,
        resolutionConfidence = null,
        syncState = "LOCAL_ONLY",
        serverRowVersion = null,
        lastLocalSequence = 0,
        createdAtMs = 1,
        updatedAtMs = 1,
        deletedAtMs = null,
    )

    private class InterruptingDataSourceFactory(private val payload: ByteArray) : DataSource.Factory {
        val payloadSize: Int get() = payload.size
        val openPositions = mutableListOf<Long>()
        val interrupted = CountDownLatch(1)
        private val failurePending = AtomicBoolean(true)

        override fun createDataSource(): DataSource = object : DataSource {
            private var position = 0
            private var openedUri: Uri? = null

            override fun open(dataSpec: DataSpec): Long {
                synchronized(openPositions) { openPositions += dataSpec.position }
                position = dataSpec.position.toInt()
                openedUri = dataSpec.uri
                return (payload.size - position).toLong()
            }

            override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                if (position >= payload.size) return -1
                if (position >= FAILURE_AFTER_BYTES && failurePending.compareAndSet(true, false)) {
                    interrupted.countDown()
                    throw IOException("TEST_NETWORK_INTERRUPTION")
                }
                val untilFailure = if (failurePending.get()) {
                    (FAILURE_AFTER_BYTES - position).coerceAtLeast(1)
                } else {
                    length
                }
                val count = minOf(length, payload.size - position, untilFailure)
                payload.copyInto(buffer, offset, position, position + count)
                position += count
                return count
            }

            override fun getUri(): Uri? = openedUri
            override fun close() { openedUri = null }
            override fun addTransferListener(transferListener: TransferListener) = Unit
        }

        private companion object { const val FAILURE_AFTER_BYTES = 8 * 1024 }
    }
}
