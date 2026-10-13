package app.autplay.playback

import android.net.Uri
import android.os.Bundle
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.Timeline
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.ByteArrayDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.NoOpCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.extractor.DefaultExtractorInput
import androidx.media3.extractor.DiscardingTrackOutput
import androidx.media3.extractor.Extractor
import androidx.media3.extractor.ExtractorOutput
import androidx.media3.extractor.PositionHolder
import androidx.media3.extractor.SeekMap
import androidx.media3.extractor.TrackOutput
import androidx.media3.extractor.mp3.Mp3Extractor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.testing.XingAudioProvider
import java.io.File
import java.nio.ByteBuffer
import java.util.UUID
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class PlaybackLoadingRegressionTest {
    private data class CompletedRead(val reset: Int, val count: Int, val descriptor: Int)

    @Test fun xingWithoutTocDurationCorrectionDoesNotReprepareSmoothQueueForever() = runBlocking {
        // Same end-to-end regression on both sides of the short-source eligibility boundary.
        for (audioFrames in listOf(XingAudioProvider.AUDIO_FRAMES, 230)) {
            verifyXingQueue(audioFrames)
        }
    }

    private suspend fun verifyXingQueue(audioFrames: Int) {
        val shortSource = audioFrames == 230
        val expectedHeadMs = if (shortSource) 0L else 3_000L
        // First prove this exact decoder fixture produces distinct header/EOF durations
        // through the production extractor, rather than relying on inaccurate file tags.
        val bytes = XingAudioProvider.bytes(audioFrames)
        val extractor = playbackExtractorsFactory().createExtractors().filterIsInstance<Mp3Extractor>().single()
        val durationsUs = mutableListOf<Long>()
        val source = ByteArrayDataSource(bytes)
        try {
            val input = DefaultExtractorInput(source, 0, source.open(DataSpec(Uri.parse("memory://xing.mp3"))))
            extractor.init(object : ExtractorOutput {
                override fun track(id: Int, type: Int): TrackOutput = DiscardingTrackOutput()
                override fun endTracks() = Unit
                override fun seekMap(seekMap: SeekMap) {
                    assertTrue(seekMap.isSeekable)
                    durationsUs += seekMap.durationUs // Snapshot: IndexSeeker is mutable.
                }
            })
            var result = Extractor.RESULT_CONTINUE
            val seekPosition = PositionHolder()
            // DiscardingTrackOutput can consume only the peek-buffered header, then
            // finish the frame on the next read. Bound by bytes, not frame count:
            // each continuing read must consume at least one byte, plus one for EOF.
            val maxReads = bytes.size + 1
            var readCalls = 0
            while (result != Extractor.RESULT_END_OF_INPUT && readCalls < maxReads) {
                val previousPosition = input.position
                result = extractor.read(input, seekPosition)
                readCalls++
                assertTrue("Unexpected sequential MP3 read result: $result at ${input.position}",
                    result == Extractor.RESULT_CONTINUE || result == Extractor.RESULT_END_OF_INPUT)
                if (result == Extractor.RESULT_CONTINUE) {
                    assertTrue("MP3 extraction stalled at $previousPosition after $readCalls reads",
                        input.position > previousPosition)
                }
            }
            assertEquals("MP3 EOF after $readCalls reads at ${input.position}/${bytes.size}",
                Extractor.RESULT_END_OF_INPUT, result)
            assertEquals(bytes.size.toLong(), input.position)
        } finally { source.close(); extractor.release() }
        val exactDurationMs = audioFrames * 1_152_000L / 44_100
        assertEquals(exactDurationMs, durationsUs.last() / 1_000)
        assertTrue("Fixture must exercise EOF correction", durationsUs.last() - durationsUs.first() in 26_000L..27_000L)
        if (shortSource) {
            assertTrue(durationsUs.first() / 1_000 <= 6_000)
            assertTrue(exactDurationMs > 6_000)
        }
        val expectedEndMs = if (shortSource) C.TIME_END_OF_SOURCE else durationsUs.first() / 1_000 - 3_000

        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val provider = Uri.parse("content://${instrumentation.context.packageName}.xing")
        val fixture = PlaybackServiceLifecycleTest.Fixture()
        val reset = checkNotNull(instrumentation.targetContext.contentResolver.call(provider, "reset", null,
            Bundle().apply { putStringArrayList("fixtureIds", ArrayList(fixture.audioIds)) })).getInt("resetGeneration")
        fun providerStatus(index: Int? = null): Bundle = checkNotNull(instrumentation.targetContext.contentResolver
            .call(provider, "status", index?.let { fixture.audioIds[it] }, null))
        fun status(key: String): Int = providerStatus().let {
            assertFalse("Bounded Xing evidence overflow", it.getBoolean("evidenceOverflow"))
            assertEquals("Fixture reset must not change", reset, it.getInt("resetGeneration"))
            it.getInt(key)
        }
        fun completedRead(index: Int): CompletedRead? = providerStatus(index).let {
            // ContentDataSource knows the byte length and can close without requesting
            // a read beyond EOF. Tail delivery plus release proves this actual load ended.
            if (it.getInt("resetGeneration") != reset || it.getInt("sourceReads") == 0 ||
                !it.getBoolean("tailDelivered") || !it.getBoolean("released")) null
            else CompletedRead(it.getInt("resetGeneration"), it.getInt("sourceReads"), it.getInt("latestDescriptor"))
        }
        val frozenReads = mutableMapOf<Int, CompletedRead>()
        val preparationFailures = mutableListOf<String>()
        fun preparationFailure(message: String) {
            if (preparationFailures.size < 32) preparationFailures += message
        }
        // Called only on main: provider descriptor IDs identify actual source reads;
        // MediaController's remote timeline does not expose source/period UIDs.
        fun observeFrozenReads(player: Player) {
            for ((index, completed) in frozenReads) {
                val reads = providerStatus(index)
                if (reads.getBoolean("evidenceOverflow") || reads.getInt("resetGeneration") != completed.reset ||
                    completed.count != reads.getInt("sourceReads") || completed.descriptor != reads.getInt("latestDescriptor")) {
                    preparationFailure("Source ${index + 1} reopened/reset: expected count=${completed.count} descriptor=${completed.descriptor}, " +
                        "actual count=${reads.getInt("sourceReads")} descriptor=${reads.getInt("latestDescriptor")}")
                }
                // The service may update a departed item's preservation policy.
                // Its retained read count must still stay fixed, but its old cut
                // no longer describes the current/future playback occurrence.
                if (index < player.currentMediaItemIndex) continue
                if (player.currentTimeline.windowCount != 3) {
                    preparationFailure("Prepared queue lost its windows")
                    continue
                }
                val item = player.getMediaItemAt(index)
                val window = player.currentTimeline.getWindow(index, Timeline.Window())
                val start = if (index == 0) 0L else expectedHeadMs
                val end = if (index == 2) C.TIME_END_OF_SOURCE else expectedEndMs
                if (window.isPlaceholder || item.clippingConfiguration.startPositionMs != start ||
                    item.clippingConfiguration.endPositionMs != end ||
                    item.mediaMetadata.extras?.getLong(CUT_SOURCE_DURATION_MS) != durationsUs.first() / 1_000) {
                    preparationFailure("Source ${index + 1} changed final timeline: placeholder=${window.isPlaceholder} " +
                        "start=${item.clippingConfiguration.startPositionMs} end=${item.clippingConfiguration.endPositionMs} " +
                        "anchored=${item.mediaMetadata.extras?.getLong(CUT_SOURCE_DURATION_MS)} currentSource=${player.currentMediaItemIndex + 1}")
                }
            }
        }
        fun assertFrozenReads(player: Player) {
            observeFrozenReads(player)
            assertTrue("Prepared sources must remain stable: $preparationFailures", preparationFailures.isEmpty())
        }
        var observeFinalSource: ((Player) -> Unit)? = null
        val preparationListener = object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                observeFrozenReads(player)
                observeFinalSource?.invoke(player)
            }
        }
        try {
            // Real service-owned ExoPlayer, source factory, resolver, settings and queue.
            val sourceName = if (shortSource) "xing-short" else "xing"
            fixture.start(listOf(sourceName, sourceName, sourceName), smooth = true)
            fixture.main { fixture.controller!!.addListener(preparationListener) }
            fixture.await("first MP3 ready with stable eligibility ($audioFrames frames)") { fixture.main {
                val player = checkNotNull(fixture.controller)
                assertEquals(null, player.playerError)
                assertTrue("Repeated source preparation", status("readOpens") <= 18)
                player.isPlaying && player.currentMediaItemIndex == 0 &&
                    player.currentMediaItem?.clippingConfiguration?.endPositionMs == expectedEndMs &&
                    player.currentMediaItem?.mediaMetadata?.extras?.getLong(CUT_SOURCE_DURATION_MS) == durationsUs.first() / 1_000
            } }
            fixture.main { fixture.controller!!.pause(); fixture.controller!!.volume = 0.35f }
            assertEquals(0L, fixture.main { fixture.controller!!.currentMediaItem!!.clippingConfiguration.startPositionMs })
            val expectedMiddleDurationMs = if (shortSource) exactDurationMs else expectedEndMs - expectedHeadMs
            fixture.await("preloaded middle MP3 has its final bounds before it starts") { fixture.main {
                val player = checkNotNull(fixture.controller)
                val timeline = player.currentTimeline
                val middle = player.getMediaItemAt(1)
                status("tailReads") >= 2 && timeline.windowCount == 3 &&
                    player.getMediaItemAt(2).localConfiguration?.uri?.scheme == "content" &&
                    middle.clippingConfiguration.startPositionMs == expectedHeadMs &&
                    middle.clippingConfiguration.endPositionMs == expectedEndMs &&
                    timeline.getWindow(1, Timeline.Window()).durationMs == expectedMiddleDurationMs &&
                    timeline.getWindow(1, Timeline.Window()).defaultPositionMs == 0L &&
                    (!shortSource || fixture.controller!!.duration == exactDurationMs) &&
                    fixture.controller!!.playbackState == Player.STATE_READY
            } }
            // A visible clipping bound is not a completed load. Freeze only the
            // first/middle descriptors that actually delivered their tail and closed.
            // The third source may begin its first preparation on a later Media3 tick.
            fixture.await("first and middle final read generations complete") { fixture.main {
                assertTrue("Repeated source preparation", status("readOpens") <= 18)
                val player = checkNotNull(fixture.controller)
                val first = completedRead(0)
                val middle = completedRead(1)
                if (first != null && middle != null && player.currentTimeline.windowCount == 3 &&
                    !(0..1).any { player.currentTimeline.getWindow(it, Timeline.Window()).isPlaceholder } &&
                    player.getMediaItemAt(0).clippingConfiguration.endPositionMs == expectedEndMs &&
                    player.getMediaItemAt(1).clippingConfiguration.startPositionMs == expectedHeadMs &&
                    player.getMediaItemAt(1).clippingConfiguration.endPositionMs == expectedEndMs &&
                    player.playbackState == Player.STATE_READY) {
                    frozenReads[0] = first
                    frozenReads[1] = middle
                    assertFrozenReads(player)
                    true
                } else false
            } }
            // Freeze the third source independently as soon as its final load exists.
            // It receives the same exact no-reopen assertion, never a blanket extra open.
            fun freezeFinalSource(player: Player): Boolean {
                observeFrozenReads(player)
                if (2 in frozenReads) return true
                val completed = completedRead(2) ?: return false
                if (player.currentTimeline.windowCount != 3) return false
                val finalWindow = player.currentTimeline.getWindow(2, Timeline.Window())
                val finalItem = player.getMediaItemAt(2)
                if (finalWindow.isPlaceholder || finalWindow.durationMs != exactDurationMs - expectedHeadMs ||
                    finalItem.clippingConfiguration.startPositionMs != expectedHeadMs ||
                    finalItem.clippingConfiguration.endPositionMs != C.TIME_END_OF_SOURCE) return false
                frozenReads[2] = completed
                observeFrozenReads(player)
                return true
            }
            fixture.main {
                observeFinalSource = { freezeFinalSource(it) }
                freezeFinalSource(fixture.controller!!)
            }
            val installedEnd = fixture.main { fixture.controller!!.currentMediaItem!!.clippingConfiguration.endPositionMs }
            // Several possible header/EOF cycles fit in this interval. A paused player
            // must remain prepared, with the same cut and no new source reads.
            repeat(20) {
                delay(50)
                fixture.main {
                    freezeFinalSource(fixture.controller!!)
                    assertFrozenReads(fixture.controller!!)
                    assertEquals(Player.STATE_READY, fixture.controller!!.playbackState)
                    assertEquals(installedEnd, fixture.controller!!.currentMediaItem!!.clippingConfiguration.endPositionMs)
                    assertEquals(0L, fixture.controller!!.currentMediaItem!!.clippingConfiguration.startPositionMs)
                }
            }
            fixture.main { assertFrozenReads(fixture.controller!!) }
            fixture.await("initial listening event") {
                fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId != null
            }
            val listen = checkNotNull(fixture.database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            // Observe callbacks, not polling alone: a short reprepare can fit entirely
            // between await samples. All reads/writes of these observations use main.
            val middleFailures = mutableListOf<String>()
            var middleAutoTransitions = 0
            fixture.main {
                val player = checkNotNull(fixture.controller)
                player.addListener(object : Player.Listener {
                    override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                        if (mediaItem?.mediaId != fixture.entries[1].value) return
                        if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) middleAutoTransitions++
                        else middleFailures += "Unexpected middle transition: $reason"
                    }

                    override fun onPlaybackStateChanged(playbackState: Int) {
                        if (player.currentMediaItem?.mediaId == fixture.entries[1].value &&
                            playbackState == Player.STATE_BUFFERING) middleFailures += "Middle entry rebuffered"
                    }

                    override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo,
                        newPosition: Player.PositionInfo, reason: Int) {
                        if (newPosition.mediaItem?.mediaId == fixture.entries[1].value &&
                            reason != Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                            middleFailures += "Middle entry repositioned: $reason"
                        }
                    }

                    override fun onEvents(player: Player, events: Player.Events) {
                        if (player.mediaItemCount < 2) return
                        val middle = player.getMediaItemAt(1)
                        if (middle.clippingConfiguration.startPositionMs != expectedHeadMs ||
                            middle.clippingConfiguration.endPositionMs != expectedEndMs) {
                            middleFailures += "Prepared middle clip changed"
                        }
                    }
                })
                player.play()
            }
            // No seek/next command: advancing proves the actual clipped period ends.
            fixture.await("natural transition to second MP3") { fixture.main {
                val player = checkNotNull(fixture.controller)
                assertEquals(null, player.playerError)
                assertTrue("Repeated source preparation", status("readOpens") <= 18)
                player.currentMediaItem?.mediaId == fixture.entries[1].value && player.isPlaying
            } }
            fixture.main { fixture.controller!!.pause() }
            fixture.await("middle entry keeps its prepared head and tail") { fixture.main {
                fixture.controller!!.currentMediaItem!!.clippingConfiguration.startPositionMs == expectedHeadMs &&
                    fixture.controller!!.duration == expectedMiddleDurationMs
            } }
            fixture.main {
                assertEquals(expectedEndMs, fixture.controller!!.currentMediaItem!!.clippingConfiguration.endPositionMs)
                assertEquals(0.35f, fixture.controller!!.volume, 0.001f)
            }
            fixture.await("source coordinates persisted") {
                fixture.database.queueDao().activeSnapshotOnce()?.let {
                    it.currentEntryId == fixture.entries[1].value &&
                        it.currentPositionMs in expectedHeadMs..(expectedHeadMs + 2_000)
                } == true
            }
            val expectedSessionEnd = if (shortSource) exactDurationMs else installedEnd
            fixture.await("first listen finalized") {
                fixture.database.historyDao().event(listen)?.sessionEndPositionMs == expectedSessionEnd
            }
            assertEquals(exactDurationMs, checkNotNull(fixture.database.historyDao().event(listen)).trackDurationMs)
            fixture.main { fixture.controller!!.play() }
            fixture.await("natural transition through the middle MP3") { fixture.main {
                assertEquals(null, fixture.controller!!.playerError)
                fixture.controller!!.currentMediaItem?.mediaId == fixture.entries[2].value &&
                    fixture.controller!!.isPlaying
            } }
            fixture.main {
                fixture.controller!!.pause()
                assertEquals("Exactly one natural middle transition", 1, middleAutoTransitions)
                assertTrue("No reprepare after preloading: $middleFailures", middleFailures.isEmpty())
            }
            fixture.await("final entry keeps original tail") { fixture.main {
                fixture.controller!!.currentMediaItem!!.clippingConfiguration.startPositionMs == expectedHeadMs &&
                    fixture.controller!!.duration == exactDurationMs - expectedHeadMs &&
                    freezeFinalSource(fixture.controller!!)
            } }
            fixture.main {
                assertEquals(C.TIME_END_OF_SOURCE, fixture.controller!!.currentMediaItem!!.clippingConfiguration.endPositionMs)
                assertEquals(0.35f, fixture.controller!!.volume, 0.001f)
                fixture.controller!!.play()
            }
            fixture.await("final MP3 finishes without buffering loop") { fixture.main {
                assertEquals(null, fixture.controller!!.playerError)
                fixture.controller!!.playbackState == Player.STATE_ENDED
            } }
            assertTrue("Bounded source reads for the whole queue", status("readOpens") <= 18)
            fixture.main { assertFrozenReads(fixture.controller!!) }
        } catch (failure: AssertionError) {
            val evidence = checkNotNull(instrumentation.targetContext.contentResolver.call(provider, "status", null, null))
            throw AssertionError("Xing frames=$audioFrames: ${failure.message}; events=${evidence.getStringArrayList("events")}", failure)
        } finally {
            fixture.main { fixture.controller?.removeListener(preparationListener) }
            fixture.close()
        }
    }

    @Test fun playbackMissWritesOnlyStreamCacheAndReplaysWithoutUpstream() {
        val root = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir,
            "playback-cache-test-${UUID.randomUUID()}")
        val downloads = SimpleCache(File(root, "downloads"), NoOpCacheEvictor())
        val streams = SimpleCache(File(root, "streams"), LeastRecentlyUsedCacheEvictor(1_048_576))
        val payload = ByteArray(65_536) { (it % 251).toByte() }
        var upstreamOpens = 0
        val upstream = DataSource.Factory {
            val bytes = ByteArrayDataSource(payload)
            object : DataSource by bytes {
                override fun open(dataSpec: DataSpec): Long {
                    upstreamOpens++
                    return bytes.open(dataSpec)
                }
            }
        }
        val factory = cachedVaultDataSourceFactory(downloads, streams, upstream)
        val uri = Uri.parse("autplay-vault://test-profile/audio-variants/test-variant")
        try {
            repeat(2) {
                val source = factory.createDataSource()
                try {
                    assertEquals(payload.size.toLong(), source.open(DataSpec(uri)))
                    val actual = java.io.ByteArrayOutputStream()
                    val buffer = ByteArray(4_096)
                    while (true) {
                        val count = source.read(buffer, 0, buffer.size)
                        if (count == C.RESULT_END_OF_INPUT) break
                        actual.write(buffer, 0, count)
                    }
                    assertTrue(payload.contentEquals(actual.toByteArray()))
                } finally { source.close() }
            }
            assertEquals(1, upstreamOpens)
            assertEquals(0L, downloads.cacheSpace)
            assertEquals(payload.size.toLong(), streams.cacheSpace)
        } finally {
            streams.release()
            downloads.release()
            root.deleteRecursively()
        }
    }

    @Test fun xingDurationWithoutSeekTableSupportsCutsBeforeDownloadingTheWholeFile() {
        // MPEG1 Layer III, 128 kbps, 44.1 kHz stereo. Xing gives duration but no TOC.
        // The payload exercises extraction/seek-map selection, not an audio decoder.
        val frameSize = 417
        val audioFrames = 1_532
        val bytes = ByteArray(frameSize * (audioFrames + 1))
        for (offset in bytes.indices step frameSize) {
            ByteBuffer.wrap(bytes, offset, 4).putInt(0xfffb9000.toInt())
        }
        ByteBuffer.wrap(bytes, 36, 12).putInt(0x58696e67).putInt(1).putInt(audioFrames)
        fun readSeekMap(extractor: Mp3Extractor): Pair<SeekMap, Long> {
            val source = ByteArrayDataSource(bytes)
            val length = source.open(DataSpec(Uri.parse("memory://fixture.mp3")))
            val input = DefaultExtractorInput(source, 0, length)
            var map: SeekMap? = null
            extractor.init(object : ExtractorOutput {
                override fun track(id: Int, type: Int): TrackOutput = DiscardingTrackOutput()
                override fun endTracks() = Unit
                override fun seekMap(seekMap: SeekMap) { map = seekMap }
            })
            try {
                repeat(16) {
                    if (map == null) assertTrue(extractor.read(input, PositionHolder()) != Extractor.RESULT_END_OF_INPUT)
                }
                return checkNotNull(map) to input.position
            } finally { source.close(); extractor.release() }
        }
        assertFalse(readSeekMap(Mp3Extractor()).first.isSeekable)
        val (map, consumed) = readSeekMap(playbackExtractorsFactory().createExtractors()
            .filterIsInstance<Mp3Extractor>().single())
        assertTrue(map.isSeekable)
        assertTrue(map.durationUs in 39_000_000L..41_000_000L)
        assertTrue("Preparation must not scan the full file", consumed < bytes.size / 10)
        val bounds = PlaybackTransitionCuts.bounds(true, "USER", map.durationUs / 1_000,
            map.isSeekable, preserveHead = false, hasPlayableSuccessor = true, stopAfterItem = false)
        assertEquals(3_000L, bounds.startMs)
        assertEquals(map.durationUs / 1_000 - 3_000, bounds.endMs)
        // Headerless, known-length CBR already works in Media3; do not regress it or
        // incorrectly diagnose all MP3 files without Xing/VBRI as unseekable.
        bytes.fill(0, 36, 48)
        assertTrue(readSeekMap(Mp3Extractor()).first.isSeekable)
    }
}
