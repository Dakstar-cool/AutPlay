package app.autplay.ui.downloads

import android.net.Uri
import android.text.format.Formatter
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.media3.common.C
import androidx.media3.common.MimeTypes
import androidx.media3.common.util.UnstableApi
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.ByteArrayDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.datasource.cache.NoOpCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.exoplayer.offline.DefaultDownloadIndex
import androidx.media3.exoplayer.offline.DefaultDownloaderFactory
import androidx.media3.exoplayer.offline.DownloadManager
import androidx.media3.exoplayer.offline.DownloadRequest
import androidx.media3.exoplayer.scheduler.Requirements
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.download.MediaDownloadComponents
import app.autplay.saveUiScreenshot
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.CoreProductDetailScreen
import app.autplay.ui.CoreProductDetailUiState
import app.autplay.application.library.*
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.settings.*
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.*
import app.autplay.AutPlayRuntime
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.RecordedRequest
import java.io.File
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Rule
import org.junit.Test

@UnstableApi
class LiveDownloadProgressTest {
    @get:Rule val compose = createComposeRule()

    @Test fun fileRingFollowsReceivedBytesAcrossScreenReopen() = verifyTransfer(knownLength = true)
    @Test fun unknownLengthShowsBytesAsTheyArrive() = verifyTransfer(knownLength = false)
    @Test fun playlistDownloadsDistinctTracksAndShowsSavedAfterTheLastByte() = verifyTransfer(true, "playlist")
    @Test fun localAlbumDownloadsAllTracksUsingTheHeaderButton() = verifyTransfer(true, "library_release")
    @Test fun catalogAlbumShowsSavedOnlyAfterDeviceFilesAreAvailableOffline() = verifyTransfer(true, "release")

    private fun verifyTransfer(knownLength: Boolean, collection: String? = null) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val directory = File(context.cacheDir, "progress-${UUID.randomUUID()}")
        val provider = StandaloneDatabaseProvider(context)
        val cache = SimpleCache(directory, NoOpCacheEvictor(), provider)
        val executor = Executors.newSingleThreadExecutor()
        val payload = ByteArray(1024 * 1024)
        val gates = listOf(CountDownLatch(1), CountDownLatch(1))
        val stops = listOf(payload.size / 4, payload.size * 3 / 4)
        val upstream = DataSource.Factory {
            val delegate = ByteArrayDataSource(payload)
            object : DataSource by delegate {
                var position = 0
                override fun open(dataSpec: DataSpec): Long {
                    position = dataSpec.position.toInt()
                    val size = delegate.open(dataSpec)
                    return if (knownLength) size else C.LENGTH_UNSET.toLong()
                }

                override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                    val stop = stops.indexOf(position)
                    if (stop >= 0) check(gates[stop].await(30, TimeUnit.SECONDS))
                    val end = stops.firstOrNull { it > position } ?: payload.size
                    val count = delegate.read(buffer, offset, if (position == end) length else minOf(length, end - position))
                    if (count > 0) position += count
                    return count
                }
            }
        }
        val manager = compose.runOnIdle {
            val factory = CacheDataSource.Factory().setCache(cache).setUpstreamDataSourceFactory(upstream)
            DownloadManager(context, DefaultDownloadIndex(provider, "TestUiProgress"), DefaultDownloaderFactory(factory, executor))
                .apply { setRequirements(Requirements(0)) }
        }
        val previous = MediaDownloadComponents.replaceForTests(MediaDownloadComponents.State(provider, cache, cache, manager, executor))
        val visible = mutableStateOf(true)
        val id = UUID.randomUUID().toString()
        val row = DownloadIntentPresentation("local-intent", "track", "DOWNLOADING", "Overture", "Apashe", transferId = id)
        val store = applicationNonSecretSettingsStore(context)
        val originalSettings = runBlocking { store.settings.first() }
        val readyId = UUID.randomUUID().toString()
        val profile = ServerProfileId(UUID.randomUUID().toString())
        val db = AutPlayRuntime.database(context)
        val members = if (collection == "playlist") listOf(readyId, id, id) else listOf(readyId, id)
        fun title(ref: String) = if (ref == readyId) "Overture" else "Bad News"
        val server = if (collection == "release") MockWebServer().apply {
            dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    if (path.endsWith("/device-files")) {
                        val files = members.distinct().joinToString(",") { ref ->
                            """{"user_track_ref_id":"$ref","audio_variant_id":"$ref","row_version":1,"recording_id":"$ref","title":"Overture","artist":"Apashe","resolution_status":"RESOLVED"}"""
                        }
                        return MockResponse().setBody("""{"files":[$files],"unavailable":[],"next_offset":null}""")
                    }
                    if (!path.contains("/music/discovery/downloads/")) return MockResponse().setResponseCode(404)
                    val phase = if (path.endsWith("/preview")) "preview" else "complete"
                    return MockResponse().setBody("""{"phase":"$phase","total":2,"counts":{"vault":2}}""")
                }
            }
            start()
        } else null
        fun waitFor(matcher: SemanticsMatcher) = compose.waitUntil(12_000) {
            compose.onAllNodes(matcher, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty()
        }
        fun assertProgress(index: Int) {
            val bytes = stops[index].toLong()
            if (collection == null) waitFor(hasContentDescription(Formatter.formatShortFileSize(context, bytes), substring = true))
            val share = bytes.toFloat() / payload.size
            val expected = if (knownLength) ProgressBarRangeInfo(if (collection != null) (1f + share) / 2f else share, 0f..1f)
                else ProgressBarRangeInfo.Indeterminate
            waitFor(SemanticsMatcher.expectValue(SemanticsProperties.ProgressBarRangeInfo, expected))
        }
        try {
            if (collection != null) runBlocking {
                store.update(NonSecretSettings(activeServerProfileId = profile, activeUserId = UserId(UUID.randomUUID().toString()),
                    deviceId = DeviceId(UUID.randomUUID().toString()), serverBaseUrl = server?.url("/")?.toString()?.trimEnd('/')))
                if (server != null) AndroidKeystoreCredentialStore(context).write(profile,
                    SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("test-download", null, 0)))
                for (ref in members.distinct()) {
                    db.libraryDao().upsertTrackRef(UserTrackRefEntity(ref, ref, null, null, "RESOLVED", "Overture", "Apashe", null,
                        1000, 1.0, "CLEAN", 1, 0, 1, 1, null, profile.value))
                    db.localAudioDao().upsertState(LocalAudioStateEntity(UUID.randomUUID().toString(), ref, null, ref,
                        "content://media/test/$ref", true, null, null, null, null, null, null, null, null, null, 1000,
                        if (ref == readyId) "AVAILABLE" else "MISSING", "USER_IMPORT", payload.size.toLong(), null, null, 1, 1))
                }
                androidx.media3.exoplayer.offline.DownloadService.clearDownloadManagerHelpers()
            }
            compose.setContent { AutPlayTheme { Surface(Modifier.fillMaxSize()) {
                if (visible.value) when (collection) {
                    "playlist" -> CoreProductDetailScreen(CoreProductDetailUiState(target = null, playlist = CorePlaylistDetail(
                        id, "Вечером", null, "MANUAL", members.mapIndexed { i, ref -> CorePlaylistDetailEntry(
                            "entry-$i", ref, title(ref), "Apashe", null, false) })))
                    "library_release", "release" -> CoreProductDetailScreen(CoreProductDetailUiState(target = null, release = CoreReleaseDetail(
                        id, null, "Renaissance", "Apashe", "2020", null, null, members.mapIndexed { i, ref ->
                            CoreReleaseDetailTrack("entry-$i", null, 1, i+1, null, title(ref), "Apashe", null, ref) },
                        releaseMbid = id.takeIf { collection == "release" })))
                    else -> CompositionLocalProvider(LocalTrackDownloads provides mapOf("track" to row)) {
                        InlineTrackDownload("track", false) {}
                    }
                }
            } } }
            if (collection == null) compose.runOnIdle {
                manager.addDownload(DownloadRequest.Builder(id, Uri.parse("test://progress/$id"))
                    .setMimeType(MimeTypes.AUDIO_MPEG).build())
                manager.resumeDownloads()
            } else {
                val button = hasTestTag("download-$collection-$id")
                waitFor(button and SemanticsMatcher.keyNotDefined(SemanticsProperties.Disabled))
                compose.onNode(button).performClick()
            }
            assertProgress(0)
            compose.runOnIdle { visible.value = false }
            compose.waitForIdle()
            gates[0].countDown()
            compose.runOnIdle { visible.value = true }
            assertProgress(1)
            if (collection != null) {
                saveUiScreenshot("$collection-download-progress")
                gates[1].countDown()
                waitFor(hasTestTag("download-saved-$id"))
                saveUiScreenshot("$collection-download-saved")
                org.junit.Assert.assertEquals(1, runBlocking { db.localAudioDao().downloadIntentsForTrack(id, 10).size })
                org.junit.Assert.assertTrue(runBlocking { db.localAudioDao().downloadIntentsForTrack(readyId, 10).isEmpty() })
            }
        } finally {
            gates.forEach { it.countDown() }
            compose.runOnIdle { visible.value = false }
            compose.waitForIdle()
            if (collection != null) {
                context.stopService(android.content.Intent(context, app.autplay.download.AutPlayDownloadService::class.java))
                androidx.media3.exoplayer.offline.DownloadService.clearDownloadManagerHelpers()
                runBlocking { store.update(originalSettings) }
                if (server != null) runBlocking { AndroidKeystoreCredentialStore(context).clear(profile) }
            }
            server?.shutdown()
            MediaDownloadComponents.replaceForTests(previous)
            compose.runOnIdle { manager.removeDownload(id); manager.release() }
            executor.shutdownNow()
            cache.release()
            provider.close()
            directory.deleteRecursively()
        }
    }
}
