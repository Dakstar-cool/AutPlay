package app.autplay.application.library

import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AutPlayRuntime
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.util.UUID

/** Opt-in proof on the paired phone; no database reset or identity replacement. */
class TrackMetadataLiveAcceptanceTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    @Before fun optIn() { assumeTrue(InstrumentationRegistry.getArguments().getString("autplayLiveMetadataAcceptance") == "true") }
    private suspend fun binding(): ClientEventBinding {
        val settings = applicationNonSecretSettingsStore(context).settings.first()
        val profile = checkNotNull(settings.activeServerProfileId)
        val cursor = checkNotNull(AutPlayRuntime.database(context).syncDao().cursor(profile.value))
        return ClientEventBinding(checkNotNull(settings.activeUserId), checkNotNull(settings.deviceId), profile, LocalId(cursor.journalEpoch))
    }
    @Test fun embeddedTagsAndCoverPreserveImportedBytes() = runBlocking {
        val owner = binding()
        val preferences = context.getSharedPreferences("metadata-live-proof", 0)
        val resolver = context.contentResolver
        val source = preferences.getString("embeddedSourceV2", null)?.let(android.net.Uri::parse) ?: run {
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Audio.Media.DISPLAY_NAME, "autplay-metadata-fixture-v2.mp3")
                put(android.provider.MediaStore.Audio.Media.TITLE, "Metadata fixture")
                put(android.provider.MediaStore.Audio.Media.ARTIST, "AutPlay verification")
                put(android.provider.MediaStore.Audio.Media.MIME_TYPE, "audio/mpeg")
                put(android.provider.MediaStore.Audio.Media.RELATIVE_PATH, "Music/AutPlay Test Sources/")
                put(android.provider.MediaStore.Audio.Media.IS_MUSIC, 1)
                put(android.provider.MediaStore.Audio.Media.IS_PENDING, 1)
            }
            val uri = checkNotNull(resolver.insert(android.provider.MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values))
            InstrumentationRegistry.getInstrumentation().context.assets.open("metadata/metadata-fixture.mp3").use { input -> resolver.openOutputStream(uri)!!.use { input.copyTo(it) } }
            resolver.update(uri, android.content.ContentValues().apply { put(android.provider.MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
            preferences.edit().putString("embeddedSourceV2", uri.toString()).commit()
            uri
        }
        val repository = app.autplay.application.importing.PhoneMusicRepository(context)
        val sourceTrack = repository.scan().first { it.uri == source.toString() }
        val inspector = app.autplay.application.importing.ContentUriInspector(resolver)
        val digest = inspector.inspectWithDigest(source.toString()).contentSha256
        val audioId = repository.linkIntoLibrary(sourceTrack, owner)
        val database = AutPlayRuntime.database(context)
        val audio = checkNotNull(database.localAudioDao().state(audioId))
        // Existing copies may have been imported before metadata support.
        TrackMetadataRepository(context).readLocal(audio.localUserTrackRefId, android.net.Uri.parse(audio.contentUri))
        val description = checkNotNull(database.trackMetadataDao().get(owner.serverProfileId.value, audio.localUserTrackRefId)).decoded()
        assertEquals("Embedded album", description.text("album"))
        assertEquals("2001-04", description.text("release_date"))
        val detail = checkNotNull(CoreProductRepository(database).trackDetail(audio.localUserTrackRefId, owner.serverProfileId.value))
        assertEquals(CoreTrackAvailability.PLAYABLE_LOCAL, detail.availability)
        assertTrue(CoreTrackDetailCapability.PLAY in detail.capabilities)
        val art = checkNotNull(database.trackMetadataDao().artwork(owner.serverProfileId.value, checkNotNull(description.artworkSha256)))
        assertNotNull(android.graphics.BitmapFactory.decodeFile(art.filePath))
        assertEquals(digest, inspector.inspectWithDigest(audio.contentUri).contentSha256)
        assertEquals(digest, inspector.inspectWithDigest(source.toString()).contentSha256)
        preferences.edit().putString("embeddedTrack", audio.localUserTrackRefId).putString("embeddedSha", description.artworkSha256).commit()
        Unit
    }
    @Test fun externalReviewSelectionAndOfflineCover() = runBlocking {
        val owner = binding()
        val database = AutPlayRuntime.database(context)
        val local = checkNotNull(context.getSharedPreferences("music-live-acceptance", 0).getString("internetTrack", null))
        val ref = checkNotNull(database.libraryDao().trackRef(local))
        val api = AutPlayRuntime.serverFeatures(context, owner)
        val repository = TrackMetadataRepository(context)
        suspend fun waitMetadata(): JsonObject {
            var value = api.trackMetadata(checkNotNull(ref.serverUserTrackRefId))
            for (attempt in 0 until 90) {
                repository.project(owner.serverProfileId.value, local, value)
                if (value["state"]?.jsonPrimitive?.content !in setOf("QUEUED", "RETRY")) return value
                delay(2000); value = api.trackMetadata(ref.serverUserTrackRefId!!)
            }
            error("Metadata did not finish: ${value["state"]}")
        }
        var metadata = api.trackMetadata(ref.serverUserTrackRefId!!)
        if (metadata["state"]?.jsonPrimitive?.content != "READY") {
            api.metadataCommand(ref.serverUserTrackRefId!!, buildJsonObject {
                put("operation_id", UUID.randomUUID().toString()); put("expected_revision", metadata["revision"]!!); put("action", "REFRESH")
            })
            metadata = waitMetadata()
            assertEquals("REVIEW", metadata["state"]?.jsonPrimitive?.content)
            val candidates = metadata["candidates"]!!.jsonArray
            assertTrue(candidates.size in 1..5)
            val chosen = candidates.map { it.jsonObject }.first { it["fields"]!!.jsonObject["album"]?.jsonPrimitive?.content == "Calming" }
            api.metadataCommand(ref.serverUserTrackRefId!!, buildJsonObject {
                put("operation_id", UUID.randomUUID().toString()); put("expected_revision", metadata["revision"]!!); put("action", "SELECT"); put("candidate_id", chosen["candidate_id"]!!)
            })
            metadata = waitMetadata()
        }
        assertEquals("READY", metadata["state"]?.jsonPrimitive?.content)
        repository.project(owner.serverProfileId.value, local, metadata)
        val sha = checkNotNull(metadata["artwork_sha256"]?.jsonPrimitive?.contentOrNull)
        repository.storeArtwork(owner.serverProfileId.value, api.metadataArtwork(ref.serverUserTrackRefId!!, sha), sha)
        assertTrue(AutPlayRuntime.syncCoordinator(context, owner).run(owner))
        val stored = checkNotNull(database.trackMetadataDao().get(owner.serverProfileId.value, local)).decoded()
        assertEquals("Calming", stored.text("album"))
        assertNotNull(stored.text("release_date"))
        val cached = checkNotNull(database.trackMetadataDao().artwork(owner.serverProfileId.value, sha))
        val bytes = File(cached.filePath).readBytes()
        assertNotNull(android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size))
        assertEquals(ref.localUserTrackRefId, database.libraryDao().trackRef(local)!!.localUserTrackRefId)
        context.getSharedPreferences("metadata-live-proof", 0).edit().putString("track", local).putString("sha", sha).commit()
        Unit
    }
    @Test fun persistedDescriptionAndArtworkNeedNoNetwork() = runBlocking {
        val owner = binding()
        val saved = context.getSharedPreferences("metadata-live-proof", 0)
        val local = checkNotNull(saved.getString("track", null))
        val database = AutPlayRuntime.database(context)
        val metadata = checkNotNull(database.trackMetadataDao().get(owner.serverProfileId.value, local)).decoded()
        assertEquals("Calming", metadata.text("album"))
        val art = checkNotNull(database.trackMetadataDao().artwork(owner.serverProfileId.value, checkNotNull(saved.getString("sha", null))))
        assertNotNull(android.graphics.BitmapFactory.decodeFile(art.filePath))
        Unit
    }

    @Test fun embeddedDescriptionAndArtworkSurviveProcessRestart() = runBlocking {
        val owner = binding()
        val saved = context.getSharedPreferences("metadata-live-proof", 0)
        val local = checkNotNull(saved.getString("embeddedTrack", null))
        val database = AutPlayRuntime.database(context)
        val metadata = checkNotNull(database.trackMetadataDao().get(owner.serverProfileId.value, local)).decoded()
        assertEquals("Embedded album", metadata.text("album"))
        assertEquals("2001-04", metadata.text("release_date"))
        assertEquals("EMBEDDED", metadata.payload["provenance"]!!.jsonObject["album"]!!.jsonObject["source"]!!.jsonPrimitive.content)
        val art = checkNotNull(database.trackMetadataDao().artwork(owner.serverProfileId.value, checkNotNull(saved.getString("embeddedSha", null))))
        assertNotNull(android.graphics.BitmapFactory.decodeFile(art.filePath))
        Unit
    }

    @Test fun removeOnlyObsoleteGeneratedFixtureFromLibrary() = runBlocking {
        val owner = binding()
        val source = context.getSharedPreferences("metadata-live-proof", 0).getString("embeddedSource", null)
            ?: return@runBlocking
        val bytes = context.contentResolver.openInputStream(android.net.Uri.parse(source))!!.use { it.readBytes() }
        check(bytes.size == 1532) { "Unexpected obsolete fixture; leave untouched" }
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        val local = UUID.nameUUIDFromBytes("phone-music:${owner.serverProfileId.value}:$digest:track".toByteArray()).toString()
        val database = AutPlayRuntime.database(context)
        val ref = checkNotNull(database.libraryDao().trackRef(local))
        check(ref.rawTitle == "autplay-metadata-fixture")
        val entry = checkNotNull(database.libraryDao().entryForTrack(local))
        if (entry.removedAtMs == null) LibraryVerticalSliceRepository(database, syncScheduler = AutPlayRuntime.syncScheduler(context))
            .removeLibrary(owner, LocalId(entry.localLibraryEntryId), LocalId.random(), System.currentTimeMillis())
        assertNotNull(database.libraryDao().entry(entry.localLibraryEntryId)!!.removedAtMs)
        Unit
    }

    @androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
    @Test fun currentPlayerObservesMetadataWithoutRestartingAudio() = runBlocking {
        val owner = binding()
        val database = AutPlayRuntime.database(context)
        val local = checkNotNull(context.getSharedPreferences("metadata-live-proof", 0).getString("embeddedTrack", null))
        val original = checkNotNull(database.trackMetadataDao().get(owner.serverProfileId.value, local))
        val entry = LocalId.random()
        val snapshot = LocalId.random()
        app.autplay.application.playback.PlaybackPersistenceRepository(database).activateQueue(
            snapshot, listOf(app.autplay.application.playback.NewPlaybackQueueEntry(entry, LocalId(local), "ORGANIC", "LOCAL_THEN_VAULT")),
            "USER", null, owner.serverProfileId.value, "GENERAL", System.currentTimeMillis(),
        )
        app.autplay.playback.ServicePlaybackSessionOwner(context).dispatch(app.autplay.playback.PlaybackCommand.StartQueue(snapshot))
        val controller = androidx.media3.session.MediaController.Builder(context,
            androidx.media3.session.SessionToken(context, android.content.ComponentName(context, app.autplay.playback.AutPlayPlaybackService::class.java)))
            .buildAsync().get(10, java.util.concurrent.TimeUnit.SECONDS)
        fun <T> main(block: () -> T): T {
            val result = java.util.concurrent.atomic.AtomicReference<Result<T>>()
            InstrumentationRegistry.getInstrumentation().runOnMainSync { result.set(runCatching(block)) }
            return result.get().getOrThrow()
        }
        suspend fun waitFor(label: String, condition: suspend () -> Boolean) {
            for (attempt in 0 until 100) { if (condition()) return; delay(50) }
            error("Timed out: $label")
        }
        try {
            waitFor("fixture ready") { main { controller.currentMediaItem?.mediaId == entry.value && controller.playbackState == androidx.media3.common.Player.STATE_READY } }
            waitFor("logical listening session created") { database.queueDao().activeSnapshotOnce()?.activeListeningEventId != null }
            main { controller.pause(); controller.seekTo(800) }
            waitFor("paused checkpoint") { main { !controller.isPlaying && controller.currentPosition >= 750 } }
            val position = main { controller.currentPosition }
            val event = checkNotNull(database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            val description = original.decoded().payload
            val fields = description["fields"]!!.jsonObject
            val updated = JsonObject(description + ("fields" to JsonObject(fields + ("title" to JsonPrimitive("Metadata live refresh proof")))))
            // Only our generated fixture: emulate arrival of a new Room projection, then restore it.
            database.trackMetadataDao().upsert(original.copy(payloadJson = updated.toString(), updatedAtMs = original.updatedAtMs + 1))
            waitFor("current MediaSession title") { main { controller.mediaMetadata.title?.toString() == "Metadata live refresh proof" } }
            assertEquals(entry.value, main { controller.currentMediaItem?.mediaId })
            assertEquals(position, main { controller.currentPosition })
            assertEquals(event, database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
            assertNotNull(main { controller.mediaMetadata.artworkData })
            val cleared = JsonObject(description + ("fields" to JsonObject(fields + mapOf("title" to JsonNull, "artist" to JsonNull, "album" to JsonNull))))
            database.trackMetadataDao().upsert(original.copy(payloadJson = cleared.toString(), updatedAtMs = original.updatedAtMs + 2))
            waitFor("explicit clears override embedded tags") { main {
                controller.mediaMetadata.title?.toString() == "" && controller.mediaMetadata.artist?.toString() == "" && controller.mediaMetadata.albumTitle?.toString() == ""
            } }
            assertEquals(position, main { controller.currentPosition })
            assertEquals(event, database.queueDao().activeSnapshotOnce()?.activeListeningEventId)
        } finally {
            database.trackMetadataDao().upsert(original)
            main { controller.pause(); controller.release() }
        }
        Unit
    }
}
