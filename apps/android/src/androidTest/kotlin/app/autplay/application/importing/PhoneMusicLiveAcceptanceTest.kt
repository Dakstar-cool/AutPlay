package app.autplay.application.importing

import android.content.ContentValues
import android.net.Uri
import android.provider.MediaStore
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.exoplayer.offline.Download
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.application.download.DownloadIntentRepository
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.download.DownloadStorageClass
import app.autplay.download.MediaDownloadComponents
import app.autplay.work.PhoneVaultUploadWork
import androidx.work.WorkManager
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import org.junit.Before
import org.junit.Assume.assumeTrue
import org.junit.runner.RunWith

/** Opt-in acceptance on the already paired device. Never clears application data. */
@UnstableApi
@RunWith(AndroidJUnit4::class)
class PhoneMusicLiveAcceptanceTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val preferences = context.getSharedPreferences("music-live-acceptance", 0)

    @Before fun requireExplicitLiveAcceptance() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("autplayLiveMusicAcceptance") == "true")
    }

    private suspend fun binding(): ClientEventBinding {
        val settings = applicationNonSecretSettingsStore(context).settings.first()
        val profile = checkNotNull(settings.activeServerProfileId)
        val cursor = checkNotNull(AutPlayRuntime.database(context).syncDao().cursor(profile.value))
        return ClientEventBinding(checkNotNull(settings.activeUserId), checkNotNull(settings.deviceId), profile, LocalId(cursor.journalEpoch))
    }

    @Test fun linkPreservesSourceWithoutCopyAndRepeatedImportRestoresOneEntry() = runBlocking {
        val owner = binding()
        val resolver = context.contentResolver
        val source = preferences.getString("source", null)?.let(Uri::parse) ?: run {
            val values = ContentValues().apply {
                put(MediaStore.Audio.Media.DISPLAY_NAME, "autplay-transfer-check.wav")
                put(MediaStore.Audio.Media.TITLE, "AutPlay transfer check")
                put(MediaStore.Audio.Media.ARTIST, "AutPlay verification")
                put(MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
                put(MediaStore.Audio.Media.RELATIVE_PATH, "Music/AutPlay Test Sources/")
                put(MediaStore.Audio.Media.IS_MUSIC, 1)
                put(MediaStore.Audio.Media.IS_PENDING, 1)
            }
            val created = checkNotNull(resolver.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values))
            resolver.openOutputStream(created)!!.use { it.write(wav()) }
            resolver.update(created, ContentValues().apply { put(MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
            preferences.edit().putString("source", created.toString()).commit()
            created
        }
        val scanner = PhoneMusicRepository(context)
        val track = scanner.scan().first { it.uri == source.toString() }
        val sourceDigest = ContentUriInspector(resolver).inspectWithDigest(source.toString()).contentSha256
        val database = AutPlayRuntime.database(context)
        val id = scanner.linkIntoLibrary(track, owner)
        preferences.edit().putString("audio", id).commit()
        val linked = checkNotNull(database.localAudioDao().state(id))
        assertEquals(source.toString(), linked.contentUri)
        assertFalse(linked.persistedUriPermission)
        assertEquals(sourceDigest, ContentUriInspector(resolver).inspectWithDigest(linked.contentUri).contentSha256)
        val count = database.libraryDao().trackRefCount()
        assertEquals(id, scanner.linkIntoLibrary(track, owner))
        assertEquals(count, database.libraryDao().trackRefCount())
        val entry = checkNotNull(database.libraryDao().entryForTrack(linked.localUserTrackRefId))
        val library = LibraryVerticalSliceRepository(database, syncScheduler = AutPlayRuntime.syncScheduler(context))
        library.removeLibrary(owner, LocalId(entry.localLibraryEntryId), LocalId.random(), System.currentTimeMillis())
        assertEquals(id, scanner.linkIntoLibrary(track, owner))
        assertNull(database.libraryDao().entry(entry.localLibraryEntryId)!!.removedAtMs)
        assertEquals(sourceDigest, ContentUriInspector(resolver).inspectWithDigest(source.toString()).contentSha256)
    }

    @Test fun uploadFromPhonePublishesPlayableVault() = runBlocking {
        val owner = binding()
        val audioId = checkNotNull(preferences.getString("audio", null))
        PhoneVaultUploadWork.enqueue(context, audioId, owner.serverProfileId.value)
        val manager = WorkManager.getInstance(context)
        var done = false
        repeat(120) {
            if (!done) {
                val work = manager.getWorkInfosForUniqueWork("phone-vault-${owner.serverProfileId.value}-$audioId").get()
                assertFalse("Phone upload failed", work.any { it.state == androidx.work.WorkInfo.State.FAILED })
                done = work.any { it.state == androidx.work.WorkInfo.State.SUCCEEDED }
                if (!done) delay(2000)
            }
        }
        assertTrue("Phone upload did not finish", done)
        val database = AutPlayRuntime.database(context)
        val audio = checkNotNull(database.localAudioDao().state(audioId))
        val ref = checkNotNull(database.libraryDao().trackRef(audio.localUserTrackRefId))
        val variant = AutPlayRuntime.serverFeatures(context, owner).playbackVariantId(checkNotNull(ref.serverUserTrackRefId))
        assertNotNull(variant)
        preferences.edit().putString("variant", variant).putString("track", ref.localUserTrackRefId).commit()
        Unit
    }

    @Test fun completedVaultDownloadReadsEveryByteWithoutNetwork() = runBlocking {
        val owner = binding()
        val database = AutPlayRuntime.database(context)
        val track = LocalId(checkNotNull(preferences.getString("track", null)))
        val intent = DownloadIntentRepository(context, database).requestPreferredVaultDownload(track, owner.serverProfileId,
            DownloadStorageClass.USER_DOWNLOAD, System.currentTimeMillis())
        val runtime = MediaDownloadComponents.get(context)
        var download: Download? = null
        repeat(90) {
            if (download?.state != Download.STATE_COMPLETED) {
                download = runtime.downloadManager.downloadIndex.getDownload(intent.media3DownloadId!!)
                assertTrue("Vault download failed", download?.state != Download.STATE_FAILED)
                if (download?.state != Download.STATE_COMPLETED) delay(1000)
            }
        }
        val completed = checkNotNull(download)
        assertEquals(Download.STATE_COMPLETED, completed.state)
        // No upstream factory: a single cache miss is an error, so this proves offline bytes.
        val offline = CacheDataSource.Factory().setCache(runtime.downloadCache).setUpstreamDataSourceFactory(null).createDataSource()
        val hash = MessageDigest.getInstance("SHA-256")
        var bytes = 0L
        try {
            offline.open(DataSpec.Builder().setUri(completed.request.uri).setKey(completed.request.customCacheKey).build())
            val buffer = ByteArray(65536)
            while (true) { val n = offline.read(buffer, 0, buffer.size); if (n < 0) break; bytes += n; hash.update(buffer, 0, n) }
        } finally { offline.close() }
        assertTrue(bytes > 0)
        val original = ContentUriInspector(context.contentResolver).inspectWithDigest(checkNotNull(preferences.getString("source", null)))
        assertEquals(original.contentSha256, hash.digest().joinToString("") { "%02x".format(it) })
    }

    private fun wav(): ByteArray {
        val sampleRate = 22050
        val samples = sampleRate * 20
        val result = ByteBuffer.allocate(44 + samples * 2).order(ByteOrder.LITTLE_ENDIAN)
        result.put("RIFF".toByteArray()).putInt(36 + samples * 2).put("WAVEfmt ".toByteArray()).putInt(16)
            .putShort(1).putShort(1).putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
            .put("data".toByteArray()).putInt(samples * 2)
        repeat(samples) { result.putShort((kotlin.math.sin(it * 2.0 * Math.PI * 440 / sampleRate) * 2000).toInt().toShort()) }
        return result.array()
    }

    @Test fun internetSelectionPublishesVaultAndDownloadsToPhone() = runBlocking {
        val owner = binding()
        val server = AutPlayRuntime.serverFeatures(context, owner)
        val search = server.searchInternetMusic("Kevin MacLeod Carefree", java.util.UUID.randomUUID().toString())
        assertEquals(5, search.candidates.size)
        val selected = search.candidates.first()
        assertTrue(selected.title.contains("Carefree", ignoreCase = true))
        app.autplay.work.InternetMusicWork.enqueue(context, owner.serverProfileId.value, search.id, selected.id, true)
        val manager = WorkManager.getInstance(context)
        var completed: androidx.work.WorkInfo? = null
        repeat(180) {
            if (completed == null) {
                val work = manager.getWorkInfosByTag(app.autplay.work.InternetMusicWork.tag(search.id, selected.id)).get()
                assertFalse("Internet acquisition failed", work.any { it.state == androidx.work.WorkInfo.State.FAILED })
                completed = work.firstOrNull { it.state == androidx.work.WorkInfo.State.SUCCEEDED }
                if (completed == null) delay(2000)
            }
        }
        val track = checkNotNull(completed) { "Internet selection timed out" }.outputData.getString("ref")!!
        preferences.edit().putString("internetTrack", track).commit()
        val database = AutPlayRuntime.database(context)
        var download: Download? = null
        val runtime = MediaDownloadComponents.get(context)
        repeat(90) {
            if (download?.state != Download.STATE_COMPLETED) {
                val intent = database.localAudioDao().downloadIntentsForTrack(track, 32).firstOrNull()
                download = intent?.media3DownloadId?.let { runtime.downloadManager.downloadIndex.getDownload(it) }
                assertTrue("Internet offline download failed", download?.state != Download.STATE_FAILED)
                if (download?.state != Download.STATE_COMPLETED) delay(1000)
            }
        }
        val ready = checkNotNull(download)
        assertEquals(Download.STATE_COMPLETED, ready.state)
        val cacheOnly = CacheDataSource.Factory().setCache(runtime.downloadCache).setUpstreamDataSourceFactory(null)
        var player: androidx.media3.exoplayer.ExoPlayer? = null
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync {
            player = androidx.media3.exoplayer.ExoPlayer.Builder(context).build().apply {
                volume = 0f
                val item = androidx.media3.common.MediaItem.Builder().setUri(ready.request.uri).setCustomCacheKey(ready.request.customCacheKey).build()
                setMediaSource(androidx.media3.exoplayer.source.ProgressiveMediaSource.Factory(cacheOnly).createMediaSource(item))
                prepare(); play()
            }
        }
        var position = 0L
        var error: androidx.media3.common.PlaybackException? = null
        try {
            repeat(30) {
                if (position < 1000 && error == null) {
                    delay(500)
                    instrumentation.runOnMainSync { position = player!!.currentPosition; error = player!!.playerError }
                }
            }
            assertNull("Cache-only playback error: $error", error)
            assertTrue("Cache-only playback did not advance", position >= 1000)
        } finally { instrumentation.runOnMainSync { player?.release() } }
    }
}
