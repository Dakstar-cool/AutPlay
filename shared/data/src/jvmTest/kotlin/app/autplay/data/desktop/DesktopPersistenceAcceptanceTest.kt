package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobLimits
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobReselection
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop
import app.autplay.application.desktop.DesktopImportJobStopReason
import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopEmbeddedTextFormat
import app.autplay.application.desktop.DesktopEmbeddedTrackTextApplication
import app.autplay.application.desktop.DesktopTrackFieldProvenance
import app.autplay.application.desktop.DesktopTrackTextOrigin
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.DesktopHistoryCursor
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryPage
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTastePolicy
import java.nio.ByteBuffer
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.IIOImage
import javax.imageio.ImageIO
import javax.imageio.stream.MemoryCacheImageOutputStream
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.Comparator
import java.util.UUID
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.yield
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

/** Real on-disk Room/BundledSQLite: no repository, DAO or transaction mocks. */
class DesktopPersistenceAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(45)

    @Test fun restartPreservesCollectionSessionAndImmutableLocalFacts(): Unit = runBlocking {
        withTimeout(30_000) {
            desktopDeviceContractV2Assertions()
            desktopPortableDeviceV2Assertions()
            desktopSelfDeviceV2Assertions()
            desktopProfileDeviceV2Assertions()
            desktopRecoveryDeletionV2Assertions()
            desktopSyncBindingV2Assertions()
            desktopAccountStorageAssertions()
            desktopLocalStatisticsAssertions()
            desktopFtsSearchAssertions()
            importJobRestartAndExplicitReselection()
            importJobCompletePagingAndBounds()
            trackTextProvenanceRestartAndWriterCoexistence()
            trackTextProvenanceRefusalAndRollback()
            embeddedMetadataReaderAndExplicitApply()
            pagedHistoryRestartAndReadOnlyTraversal()
            legacyMigrationAndTrackPreferences()
            schemaTwoCollectionMigration()
            schemaThreeTasteMigration()
            tasteRestartAndPolicies()
            collectionRestartAndAdmission()
            playlistBatchRestartAndBounds()
            // Prior v4 is built from the genuine v1 export plus migrations 1->2->3->4.
            // This is not a replacement for deferred genuine KSP exports 2/3/4/5.
            Fixture().useStore(legacyBytes = pcmWave(), legacyVersion = 4) { fixture ->
                assertEquals(5L, fixture.number("PRAGMA user_version"))
                assertEquals(fixture.legacyRows, fixture.rows(checkNotNull(fixture.legacySchema)))
                assertNull(fixture.store.state.value.playlists.single().description)
                assertEquals(1L, fixture.number("SELECT COUNT(*) FROM pragma_table_info('desktop_playlist') " +
                    "WHERE name = 'description' AND type = 'TEXT' AND [notnull] = 0 AND dflt_value = 'NULL'"))
                assertEquals("FUTURE_SCHEMA4_SESSION", fixture.store.state.value.queue?.sessionTasteState)
                assertEquals("FUTURE_SCHEMA4_LISTEN", fixture.store.state.value.history.single().listenTasteState)
                val before = fixture.store.state.value
                val legacyTrack = before.tracks.single()
                val inspectionRows = fixture.allRows()
                assertEquals(DesktopCopyInspection(legacyTrack.id, legacyTrack.sha256, legacyTrack.byteSize, "PRESENT"),
                    fixture.store.inspectManagedCopy(legacyTrack.id))
                assertSame(before, fixture.store.state.value)
                assertEquals(inspectionRows, fixture.allRows())
                assertFalse(Files.exists(fixture.root.resolve("media/${legacyTrack.sha256}.wav")))
                assertArrayEquals(pcmWave(), Files.readAllBytes(fixture.root.resolve("media/${legacyTrack.sha256}.media")))
                val facts = fixture.facts()
                fixture.store.updatePlaylistDescription("legacy-playlist", null, null)
                fixture.execute("INSERT INTO desktop_playlist (id, name) VALUES ('default-null', 'Default null')")
                fixture.reopen()
                assertNull(fixture.store.state.value.playlists.single { it.id == "default-null" }.description)
                assertEquals(before, fixture.store.state.value.copy(playlists = fixture.store.state.value.playlists.filter {
                    it.id != "default-null"
                }))
                assertEquals(facts, fixture.facts())
                assertEquals(fixture.legacyRows, fixture.rows(checkNotNull(fixture.legacySchema)).mapValues { (table, rows) ->
                    if (table == "desktop_playlist") rows.filter { it.first() != "'default-null'" } else rows
                })
                assertArrayEquals(checkNotNull(fixture.legacySchemaBytes), Files.readAllBytes(fixture.schemaFile()))
            }
            Fixture().useStore { fixture ->
                val store = fixture.store
                preferencesRestartAndPortableAllowlist(fixture)
                val imported = fixture.track("track-a", "Original title")
                val track = store.addImportedTrack(imported)
                val originalFacts = fixture.facts()
                assertEquals(1, originalFacts.size)
                assertEquals(track, store.addImportedTrack(imported.copy(id = "duplicate-id", title = "Other title")))
                assertEquals(originalFacts, fixture.facts())

                val playlist = store.createPlaylist("Acceptance playlist")
                assertNull(playlist.description)
                val createdSnapshot = fixture.facts().values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_CREATED" }
                    .getValue("snapshot").jsonObject
                assertEquals(setOf("playlist_id", "name", "description"), createdSnapshot.keys)
                assertNull(createdSnapshot.getValue("description").jsonPrimitive.contentOrNull)
                val description = "  First line\nSecond \uD83C\uDFB5\r\n  "
                val beforeDescription = store.state.value
                val beforeDescriptionFacts = fixture.facts()
                store.updatePlaylistDescription(playlist.id, null, description)
                assertEquals(beforeDescription.copy(playlists = listOf(playlist.copy(description = description))), store.state.value)
                val descriptionFact = (fixture.facts() - beforeDescriptionFacts.keys).values.single()
                val descriptionDocument = Json.parseToJsonElement(descriptionFact).jsonObject
                assertEquals("PLAYLIST_DESCRIPTION_UPDATED", descriptionDocument.getValue("mutation").jsonPrimitive.content)
                assertEquals("1", descriptionDocument.getValue("version").jsonPrimitive.content)
                val descriptionSnapshot = descriptionDocument.getValue("snapshot").jsonObject
                assertEquals(setOf("playlist_id", "previous_description", "description"), descriptionSnapshot.keys)
                assertEquals(playlist.id, descriptionSnapshot.getValue("playlist_id").jsonPrimitive.content)
                assertNull(descriptionSnapshot.getValue("previous_description").jsonPrimitive.contentOrNull)
                assertEquals(description, descriptionSnapshot.getValue("description").jsonPrimitive.content)
                assertEquals(playlist.id, fixture.text("SELECT aggregateId FROM desktop_local_outbox WHERE kind = 'PLAYLIST_DESCRIPTION_UPDATED'"))
                val descriptionFacts = fixture.facts()
                // Exact no-op must not even UPDATE a row, including null->null below.
                fixture.execute("CREATE TRIGGER acceptance_abort_description_update BEFORE UPDATE OF description ON desktop_playlist " +
                    "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_update'); END")
                try {
                    store.updatePlaylistDescription(playlist.id, description, description)
                    assertEquals("desktop_playlist_description_conflict", failureCode {
                        store.updatePlaylistDescription(playlist.id, null, description)
                    })
                } finally { fixture.execute("DROP TRIGGER acceptance_abort_description_update") }
                listOf("", "Invalid\u0000", "Invalid\uD800", "Invalid\uDC00", "x".repeat(501),
                    "x".repeat(499) + "\uD83C\uDFB5").forEach { invalid ->
                    assertEquals("desktop_playlist_description_invalid", failureCode {
                        store.updatePlaylistDescription(playlist.id, description, invalid)
                    })
                }
                assertEquals("desktop_playlist_missing", failureCode { store.updatePlaylistDescription("missing-playlist", null, "Valid") })
                assertEquals("desktop_identity_invalid", failureCode { store.updatePlaylistDescription("bad/id", null, "Valid") })
                assertEquals(descriptionFacts, fixture.facts())
                assertEquals(description, store.state.value.playlists.single().description)
                // Simulate a newer persisted edit without refreshing the caller's published snapshot.
                val newerDescription = "Newer persisted text\n\uD83C\uDFB5"
                fixture.execute("UPDATE desktop_playlist SET description = '$newerDescription' WHERE id = '${playlist.id}'")
                assertEquals(description, store.state.value.playlists.single().description)
                assertEquals("desktop_playlist_description_conflict", failureCode {
                    store.updatePlaylistDescription(playlist.id, description, "Stale editor")
                })
                assertEquals(newerDescription, store.state.value.playlists.single().description)
                assertEquals(descriptionFacts, fixture.facts())
                store.updatePlaylistDescription(playlist.id, newerDescription, description)
                val beforeClearFacts = fixture.facts()
                store.updatePlaylistDescription(playlist.id, description, null)
                val clearFact = (fixture.facts() - beforeClearFacts.keys).values.single()
                val clearSnapshot = Json.parseToJsonElement(clearFact).jsonObject.getValue("snapshot").jsonObject
                assertEquals(description, clearSnapshot.getValue("previous_description").jsonPrimitive.content)
                assertNull(clearSnapshot.getValue("description").jsonPrimitive.contentOrNull)
                fixture.execute("CREATE TRIGGER acceptance_abort_description_update BEFORE UPDATE OF description ON desktop_playlist " +
                    "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_update'); END")
                val clearFacts = fixture.facts()
                try { store.updatePlaylistDescription(playlist.id, null, null) }
                finally { fixture.execute("DROP TRIGGER acceptance_abort_description_update") }
                assertEquals(clearFacts, fixture.facts())
                val limitDescription = "x".repeat(498) + "\uD83C\uDFB5"
                store.updatePlaylistDescription(playlist.id, null, limitDescription)
                assertEquals(500, store.state.value.playlists.single().description?.length)
                store.updatePlaylistDescription(playlist.id, limitDescription, " \n\t ")
                assertEquals(" \n\t ", store.state.value.playlists.single().description)
                store.updatePlaylistDescription(playlist.id, " \n\t ", description)
                val otherTrack = store.addImportedTrack(fixture.track("track-b", "Other track", byteArrayOf(5, 6, 7)))
                repeat(2) { store.appendToPlaylist(playlist.id, track.id) }
                store.appendToPlaylist(playlist.id, otherTrack.id)
                val entries = store.state.value.playlists.single().entries
                assertEquals(listOf(track.id, track.id, otherTrack.id), entries.map { it.trackId })
                assertEquals(3, entries.map { it.id }.distinct().size)
                val ordinals = listOf(Long.MIN_VALUE + 7, 9_007_199_254_740_993L, Long.MAX_VALUE - 4)
                entries.zip(ordinals).forEach { (entry, ordinal) ->
                    fixture.execute("UPDATE desktop_playlist_entry SET ordinal = $ordinal WHERE id = '${entry.id}'")
                }
                val renamedName = "  Renamed \uD83C\uDFB5  "
                store.renamePlaylist(playlist.id, renamedName)
                store.movePlaylistEntry(playlist.id, entries.first().id, 1)
                assertEquals(description, store.state.value.playlists.single().description)
                assertEquals(listOf(entries[1], entries[0], entries[2]), store.state.value.playlists.single().entries)
                val editedFacts = fixture.facts()
                val renameSnapshot = editedFacts.values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_RENAMED" }
                    .getValue("snapshot").jsonObject
                assertEquals(playlist.id, renameSnapshot.getValue("playlist_id").jsonPrimitive.content)
                assertEquals(playlist.name, renameSnapshot.getValue("previous_name").jsonPrimitive.content)
                assertEquals(renamedName, renameSnapshot.getValue("name").jsonPrimitive.content)
                val moveSnapshot = editedFacts.values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_ENTRY_MOVED" }
                    .getValue("snapshot").jsonObject
                assertEquals(playlist.id, moveSnapshot.getValue("playlist_id").jsonPrimitive.content)
                assertEquals(entries[0].id, moveSnapshot.getValue("entry_id").jsonPrimitive.content)
                assertEquals("1", moveSnapshot.getValue("direction").jsonPrimitive.content)
                val movedRows = moveSnapshot.getValue("entries").jsonArray.map { it.jsonObject }
                assertEquals(listOf(entries[1].id, entries[0].id), movedRows.map { it.getValue("entry_id").jsonPrimitive.content })
                assertEquals(listOf(track.id, track.id), movedRows.map { it.getValue("track_id").jsonPrimitive.content })
                assertEquals(listOf(ordinals[1], ordinals[0]).map(Long::toString),
                    movedRows.map { it.getValue("previous_ordinal").jsonPrimitive.content })
                assertEquals(ordinals.take(2).map(Long::toString), movedRows.map { it.getValue("ordinal").jsonPrimitive.content })
                assertTrue(movedRows.all { it.getValue("ordinal").jsonPrimitive.isString &&
                    it.getValue("previous_ordinal").jsonPrimitive.isString })
                assertEquals(ordinals[2], fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${entries[2].id}'"))
                store.movePlaylistEntry(playlist.id, entries[2].id, -1)
                assertEquals(listOf(entries[1], entries[2], entries[0]), store.state.value.playlists.single().entries)
                val beforeNoops = store.state.value
                val noopFacts = fixture.facts()
                store.renamePlaylist(playlist.id, renamedName)
                store.movePlaylistEntry(playlist.id, entries[1].id, -1)
                store.movePlaylistEntry(playlist.id, entries[0].id, 1)
                listOf(Int.MIN_VALUE, 0, 2, Int.MAX_VALUE).forEach { store.movePlaylistEntry(playlist.id, entries[0].id, it) }
                store.movePlaylistEntry(playlist.id, "missing-entry", 1)
                assertEquals("desktop_playlist_missing", failureCode { store.renamePlaylist("missing-playlist", "Valid") })
                assertEquals("desktop_playlist_missing", failureCode { store.movePlaylistEntry("missing-playlist", entries[0].id, 0) })
                listOf("", " \t", "Invalid\u0000", "Invalid\uD800", "Invalid\uDC00", "x".repeat(4_097),
                    "x".repeat(4_095) + "\uD83C\uDFB5").forEach { name ->
                    assertEquals("desktop_playlist_name_invalid", failureCode { store.renamePlaylist(playlist.id, name) })
                }
                assertEquals(beforeNoops, store.state.value)
                assertEquals(noopFacts, fixture.facts())
                store.renamePlaylist(playlist.id, "x".repeat(4_094) + "\uD83C\uDFB5")
                assertEquals(4_096, store.state.value.playlists.single().name.length)
                store.renamePlaylist(playlist.id, renamedName)
                store.removePlaylistEntry(playlist.id, entries.first().id)
                store.removePlaylistEntry(playlist.id, entries.first().id)

                val queue = DesktopQueue("queue-a", listOf(DesktopQueueEntry("entry-a", track.id),
                    DesktopQueueEntry("entry-b", track.id)), "entry-b", positionMs = 1_234,
                    shuffleEnabled = true, shuffleOrder = listOf("entry-b", "entry-a"), repeatMode = "ALL",
                    sessionId = "session-a", sessionStartedAtMs = 1_000, playedMs = 900)
                val checkpoint = DesktopListeningEvent("session-a", "entry-b", track.id, 1_000, 900, 1_234, false)
                store.saveQueue(queue, checkpoint)
                store.saveQueue(queue, checkpoint)
                val playbackBeforeDescription = store.state.value
                store.updatePlaylistDescription(playlist.id, description, "While selected\n\uD83C\uDFB5")
                assertEquals(playbackBeforeDescription.copy(playlists = playbackBeforeDescription.playlists.map {
                    it.copy(description = "While selected\n\uD83C\uDFB5")
                }), store.state.value)
                store.updatePlaylistDescription(playlist.id, "While selected\n\uD83C\uDFB5", description)
                assertEquals(listOf(checkpoint), store.state.value.history)
                store.updateTrackMetadata(track.id, "Edited title", "Edited artist", 4_000)
                // The displayed duration is stale. The text command must read the current DB row.
                val currentDuration = 9_007_199_254_740_993L
                fixture.execute("UPDATE desktop_track SET durationMs = $currentDuration WHERE id = 'track-a'")
                val editedTitle = "  Edited \uD83C\uDFB5  "
                val editedArtist = "x".repeat(4_094) + "\uD83C\uDFB5"
                store.updateTrackTextMetadata(track.id, editedTitle, editedArtist)
                assertEquals(track.copy(title = editedTitle, artist = editedArtist, durationMs = currentDuration),
                    store.state.value.tracks.single { it.id == track.id })
                assertEquals(imported.managedFileName, fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
                val textFact = fixture.facts().values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "TRACK_METADATA_UPDATED" &&
                        it.getValue("snapshot").jsonObject.getValue("title").jsonPrimitive.content == editedTitle }
                    .getValue("snapshot").jsonObject
                assertEquals(currentDuration.toString(), textFact.getValue("duration_ms").jsonPrimitive.content)
                assertTrue(textFact.getValue("duration_ms").jsonPrimitive.isString)
                assertEquals(track.id, textFact.getValue("track_id").jsonPrimitive.content)
                assertEquals(track.sha256, textFact.getValue("sha256").jsonPrimitive.content)
                assertEquals(track.byteSize.toString(), textFact.getValue("byte_size").jsonPrimitive.content)
                val beforeTextNoops = store.state.value
                val textFacts = fixture.facts()
                store.updateTrackTextMetadata(track.id, editedTitle, editedArtist)
                listOf("", " \t", "Invalid\u0000", "Invalid\uD800", "Invalid\uDC00", "x".repeat(4_097),
                    "x".repeat(4_095) + "\uD83C\uDFB5").forEach { title ->
                    assertEquals("desktop_metadata_invalid", failureCode { store.updateTrackTextMetadata(track.id, title, "") })
                }
                listOf("Invalid\u0000", "Invalid\uD800", "Invalid\uDC00", "x".repeat(4_097)).forEach { artist ->
                    assertEquals("desktop_metadata_invalid", failureCode { store.updateTrackTextMetadata(track.id, "Valid", artist) })
                }
                assertEquals("desktop_track_missing", failureCode { store.updateTrackTextMetadata("missing-track", "Valid", "") })
                assertEquals("desktop_identity_invalid", failureCode { store.updateTrackTextMetadata("bad/id", "Valid", "") })
                assertEquals(beforeTextNoops, store.state.value)
                assertEquals(textFacts, fixture.facts())
                // Duration enrichment after a text edit must retain the latest labels, and vice versa.
                store.updateTrackDuration(track.id, 6_500)
                assertEquals(track.copy(title = editedTitle, artist = editedArtist, durationMs = 6_500),
                    store.state.value.tracks.single { it.id == track.id })
                store.updateTrackTextMetadata(track.id, "Latest user title", "")
                store.updateTrackDuration(track.id, 6_501)
                assertEquals(track.copy(title = "Latest user title", artist = "", durationMs = 6_501),
                    store.state.value.tracks.single { it.id == track.id })
                store.updateTrackTextMetadata(otherTrack.id, "Edited unknown-duration track", "")
                assertNull(store.state.value.tracks.single { it.id == otherTrack.id }.durationMs)
                val beforeDurationNoops = fixture.facts()
                store.updateTrackDuration(track.id, 6_501)
                assertEquals("desktop_metadata_invalid", failureCode { store.updateTrackDuration(track.id, -1) })
                assertEquals("desktop_track_missing", failureCode { store.updateTrackDuration("missing-track", 0) })
                assertEquals("desktop_identity_invalid", failureCode { store.updateTrackDuration("bad/id", 0) })
                assertEquals(beforeDurationNoops, fixture.facts())
                val latestMetadata = beforeDurationNoops.values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "TRACK_METADATA_UPDATED" &&
                        it.getValue("snapshot").jsonObject.getValue("duration_ms").jsonPrimitive.content == "6501" }
                    .getValue("snapshot").jsonObject
                assertEquals("Latest user title", latestMetadata.getValue("title").jsonPrimitive.content)
                assertEquals("", latestMetadata.getValue("artist").jsonPrimitive.content)
                val updatedFacts = fixture.facts()
                originalFacts.forEach { (id, payload) ->
                    assertArrayEquals(payload.toByteArray(Charsets.UTF_8), updatedFacts.getValue(id).toByteArray(Charsets.UTF_8))
                }
                editedFacts.forEach { (id, payload) -> assertEquals(payload, updatedFacts[id]) }
                val importedSnapshot = Json.parseToJsonElement(originalFacts.values.single()).jsonObject
                    .getValue("snapshot").jsonObject
                assertEquals("Original title", importedSnapshot.getValue("title").jsonPrimitive.content)
                assertFalse(importedSnapshot.containsKey("managed_file_name"))
                updatedFacts.values.forEach { payload ->
                    assertFalse(payload.contains(fixture.root.toString()))
                    assertFalse(payload.contains(imported.managedFileName))
                    val keys = Json.parseToJsonElement(payload).jsonObject.getValue("snapshot").jsonObject.keys
                    assertTrue(keys.none { it in setOf("token", "authorization", "locator", "raw_path", "filesystem_path") })
                }
                val expected = store.state.value
                fixture.reopen()
                assertEquals(expected, fixture.store.state.value)
                assertEquals(updatedFacts, fixture.facts())
                assertEquals(queue, fixture.store.state.value.queue)
                assertEquals(listOf(checkpoint), fixture.store.state.value.history)
                assertEquals(renamedName, fixture.store.state.value.playlists.single().name)
                assertEquals(description, fixture.store.state.value.playlists.single().description)
                assertEquals(listOf(entries[1], entries[2]), fixture.store.state.value.playlists.single().entries)
                assertEquals(ordinals[0], fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${entries[1].id}'"))
                assertEquals(ordinals[1], fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${entries[2].id}'"))
                // Opaque legacy fixture bytes prove persistence; real PCM capabilities are covered by import/native cases.

                // Future queue vocabulary must survive storage even if the controller cannot play it.
                fixture.store.saveQueue(queue.copy(queueType = "FUTURE_QUEUE", repeatMode = "FUTURE_REPEAT"))
                fixture.reopen()
                assertEquals("FUTURE_QUEUE", fixture.store.state.value.queue?.queueType)
                assertEquals("FUTURE_REPEAT", fixture.store.state.value.queue?.repeatMode)

                val deleted = fixture.store.createPlaylist("  Delete \uD83C\uDFB5  ")
                fixture.store.updatePlaylistDescription(deleted.id, null, description)
                repeat(2) { fixture.store.appendToPlaylist(deleted.id, track.id) }
                fixture.store.appendToPlaylist(deleted.id, otherTrack.id)
                val empty = fixture.store.createPlaylist("Empty delete")
                val beforeDelete = fixture.store.state.value
                val beforeDeleteFacts = fixture.facts()
                fixture.store.deletePlaylist(deleted.id)
                assertEquals(beforeDelete.copy(playlists = beforeDelete.playlists.filter { it.id != deleted.id }),
                    fixture.store.state.value)
                assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_playlist WHERE id = '${deleted.id}'"))
                assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = '${deleted.id}'"))
                assertEquals(playlist.id, fixture.store.state.value.playlists.single { it.id != empty.id }.id)
                assertEquals(2L, fixture.number("SELECT COUNT(*) FROM desktop_managed_locator"))
                assertArrayEquals(byteArrayOf(1, 2, 3, 4), Files.readAllBytes(fixture.root.resolve("media/${imported.managedFileName}")))
                assertArrayEquals(byteArrayOf(5, 6, 7), Files.readAllBytes(fixture.root.resolve("media/${otherTrack.sha256}.media")))
                val deletedFact = fixture.facts().values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_DELETED" }
                    .getValue("snapshot").jsonObject
                assertEquals(setOf("playlist_id", "name", "description", "entry_count"), deletedFact.keys)
                assertEquals(description, deletedFact.getValue("description").jsonPrimitive.content)
                assertEquals(deleted.id, deletedFact.getValue("playlist_id").jsonPrimitive.content)
                assertEquals(deleted.name, deletedFact.getValue("name").jsonPrimitive.content)
                assertEquals("3", deletedFact.getValue("entry_count").jsonPrimitive.content)
                assertTrue(deletedFact.getValue("entry_count").jsonPrimitive.isString)
                assertEquals(deleted.id, fixture.text("SELECT aggregateId FROM desktop_local_outbox WHERE kind = 'PLAYLIST_DELETED'"))
                fixture.store.deletePlaylist(empty.id)
                val deletionFacts = fixture.facts()
                assertEquals(beforeDeleteFacts.size + 2, deletionFacts.size)
                beforeDeleteFacts.forEach { (id, payload) -> assertEquals(payload, deletionFacts[id]) }
                val emptyFact = deletionFacts.values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_DELETED" &&
                        it.getValue("snapshot").jsonObject.getValue("playlist_id").jsonPrimitive.content == empty.id }
                    .getValue("snapshot").jsonObject
                assertEquals("0", emptyFact.getValue("entry_count").jsonPrimitive.content)
                assertNull(emptyFact.getValue("description").jsonPrimitive.contentOrNull)
                fixture.store.deletePlaylist(deleted.id)
                fixture.store.deletePlaylist("missing-playlist")
                assertEquals("desktop_identity_invalid", failureCode { fixture.store.deletePlaylist("bad/id") })
                assertEquals(deletionFacts, fixture.facts())
                val afterDelete = fixture.store.state.value
                fixture.reopen()
                assertEquals(afterDelete, fixture.store.state.value)
                assertEquals(deletionFacts, fixture.facts())

                // Legacy descriptions are opaque on read and unrelated writes, including values
                // outside the current editor limit. Explicit replacement must retain the before-image.
                val legacyDescription = "Legacy\n" + "x".repeat(600) + "\uD83C\uDFB5"
                fixture.execute("UPDATE desktop_playlist SET description = '$legacyDescription' WHERE id = '${playlist.id}'")
                fixture.reopen()
                assertEquals(legacyDescription, fixture.store.state.value.playlists.single().description)
                val legacyDescriptionFacts = fixture.facts()
                fixture.store.renamePlaylist(playlist.id, "Retain legacy description")
                fixture.store.movePlaylistEntry(playlist.id, entries[1].id, 1)
                fixture.store.updateTrackTextMetadata(track.id, "Latest user title", "")
                assertEquals(legacyDescription, fixture.store.state.value.playlists.single().description)
                assertEquals("desktop_playlist_description_conflict", failureCode {
                    fixture.store.updatePlaylistDescription(playlist.id, null, "Replacement")
                })
                fixture.store.updatePlaylistDescription(playlist.id, legacyDescription, description)
                val replacedLegacy = (fixture.facts() - legacyDescriptionFacts.keys).values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("mutation").jsonPrimitive.content == "PLAYLIST_DESCRIPTION_UPDATED" }
                    .getValue("snapshot").jsonObject
                assertEquals(legacyDescription, replacedLegacy.getValue("previous_description").jsonPrimitive.content)
                val oversizedDescription = "x".repeat(262_145)
                fixture.execute("UPDATE desktop_playlist SET description = '$oversizedDescription' WHERE id = '${playlist.id}'")
                fixture.reopen()
                val oversizedBefore = fixture.store.state.value
                val oversizedFacts = fixture.facts()
                assertEquals("desktop_outbox_payload_too_large", failureCode {
                    fixture.store.updatePlaylistDescription(playlist.id, oversizedDescription, null)
                })
                assertEquals("desktop_outbox_payload_too_large", failureCode { fixture.store.deletePlaylist(playlist.id) })
                assertEquals(oversizedBefore, fixture.store.state.value)
                assertEquals(oversizedFacts, fixture.facts())
                assertEquals(oversizedDescription, fixture.text("SELECT description FROM desktop_playlist WHERE id = '${playlist.id}'"))
                // SQLite text conversion cannot honestly fixture an unpaired UTF-16 surrogate.
                // Exercise the typed encoder directly; real invalid-before-image evidence is deferred.
                assertEquals("desktop_outbox_payload_invalid", failureCode {
                    DesktopLocalIntent.playlistDescriptionUpdated(PlaylistRow(playlist.id, playlist.name, "Legacy\uD800"),
                        PlaylistRow(playlist.id, playlist.name, null)); Unit
                })
                assertEquals(oversizedFacts, fixture.facts())
                fixture.reopen()
                assertEquals(oversizedBefore, fixture.store.state.value)

                val retained = fixture.store.state.value
                val retainedFacts = fixture.facts()
                val invalidMetadata = try {
                    fixture.store.updateTrackMetadata(track.id, "Invalid\uD800", "", null)
                    null
                } catch (error: DesktopStorageException) { error }
                assertEquals("desktop_metadata_invalid", invalidMetadata?.code)
                assertEquals(retained, fixture.store.state.value)
                assertEquals(retainedFacts, fixture.facts())

                // A current writer cannot reinterpret a future locator during exact-byte reimport.
                fixture.execute("UPDATE desktop_managed_locator SET fileName = 'future.managed' WHERE trackId = 'track-a'")
                fixture.store.updateTrackTextMetadata(track.id, "Latest user title", "")
                fixture.store.updateTrackDuration(track.id, 6_501)
                val unknownLocator = try { fixture.store.addImportedTrack(imported); null }
                    catch (error: DesktopStorageException) { error }
                assertEquals("desktop_locator_unsupported", unknownLocator?.code)
                assertEquals("future.managed", fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
                assertEquals(retained, fixture.store.state.value)
                assertEquals(retainedFacts, fixture.facts())
                fixture.reopen()
                assertEquals(retained, fixture.store.state.value)
                assertEquals("future.managed", fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
            }
        }
    }

    @Test fun outboxAbortRollsBackTrackMetadataPlaylistAndQueueHistoryTogether(): Unit = runBlocking {
        withTimeout(30_000) {
            importJobAtomicRollbackAndCancellation()
            embeddedApplyStickyLatch()
            historyReadFailureAndCancellation()
            trackPreferenceStickyLatch()
            collectionRollbackAndStickyLatch()
            tasteRollbackAndStickyLatch()
            playlistBatchRollbackAndCancellation()
            Fixture().useStore { fixture ->
                preferencesCancellationAndClose(fixture)
                val store = fixture.store
                val legacyBytes = pcmWave()
                val track = store.addImportedTrack(fixture.track("track-a", "Before failure", legacyBytes))
                val playlist = store.createPlaylist("Before failure")
                store.updatePlaylistDescription(playlist.id, null, "Retained\n\uD83C\uDFB5")
                store.appendToPlaylist(playlist.id, track.id)
                store.appendToPlaylist(playlist.id, track.id)
                val playlistEntries = store.state.value.playlists.single().entries
                fixture.execute("UPDATE desktop_playlist_entry SET ordinal = 9007199254740993 WHERE id = '${playlistEntries[1].id}'")
                val queue = DesktopQueue("queue-a", listOf(DesktopQueueEntry("entry-a", track.id)), "entry-a",
                    sessionId = "session-a", sessionStartedAtMs = 100)
                store.saveQueue(queue)
                val baseline = store.state.value
                val facts = fixture.facts()
                val candidate = fixture.track("track-b", "Must roll back", byteArrayOf(8, 9, 10))
                val repairedName = "${track.sha256}.wav"
                Files.copy(fixture.root.resolve("media/${track.sha256}.media"), fixture.root.resolve("media/$repairedName"))
                val reimport = DesktopImportedTrack("repair-candidate", "Must retain metadata", "", null,
                    track.sha256, track.byteSize, repairedName)
                fixture.execute("CREATE TRIGGER acceptance_abort_outbox BEFORE INSERT ON desktop_local_outbox " +
                    "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
                try {
                    val legacyRepairFailure = try { store.resolveManagedFile(track.id); null }
                        catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_media_lookup_failed", legacyRepairFailure?.code)
                    assertEquals("${track.sha256}.media", fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
                    assertEquals(baseline, store.state.value)
                    assertEquals(facts, fixture.facts())
                    assertArrayEquals(legacyBytes, Files.readAllBytes(fixture.root.resolve("media/$repairedName")))
                    val operations: List<suspend () -> Unit> = listOf(
                        { store.addImportedTrack(candidate); Unit },
                        { store.addImportedTrack(reimport); Unit },
                        { store.updateTrackMetadata(track.id, "Must roll back", "", 100) },
                        { store.updateTrackTextMetadata(track.id, "Text must roll back", "Changed artist") },
                        { store.applyEmbeddedTrackText(track.textBaseline(), "Embedded must roll back", "Embedded artist") },
                        { store.updateTrackDuration(track.id, 101) },
                        { store.setTrackPreference(track.id, "LIKED") },
                        { store.setTrackPreference(track.id, "DISLIKED") },
                        { store.removeTrackFromCollection(track.id) },
                        { store.setCurrentListenTasteExcluded(queue.id, "entry-a", "session-a", true); Unit },
                        { store.setQueueSessionTasteExcluded(queue.id, true); Unit },
                        { store.createPlaylist("Must roll back"); Unit },
                        { store.renamePlaylist(playlist.id, "Must roll back") },
                        { store.updatePlaylistDescription(playlist.id, "Retained\n\uD83C\uDFB5", "Must roll back") },
                        { store.updatePlaylistDescription(playlist.id, "Retained\n\uD83C\uDFB5", null) },
                        { store.appendToPlaylist(playlist.id, track.id) },
                        { store.appendTracksToPlaylist(playlist.id, listOf(track.id, track.id)) },
                        { store.movePlaylistEntry(playlist.id, playlistEntries[0].id, 1) },
                        { store.removePlaylistEntry(playlist.id, playlistEntries[0].id) },
                        { store.deletePlaylist(playlist.id) },
                        { store.saveQueue(queue.copy(positionMs = 200, repeatMode = "ALL"),
                            DesktopListeningEvent("session-a", "entry-a", track.id, 100, 200, 200, true)) },
                    )
                    operations.forEach { operation ->
                        val failure = try { operation(); null } catch (error: DesktopStorageException) { error }
                        assertEquals("desktop_mutation_failed", failure?.code)
                        assertEquals(baseline, store.state.value)
                        assertEquals(facts, fixture.facts())
                        assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_preference"))
                        assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
                        assertEquals(1L, fixture.number("SELECT COUNT(*) FROM desktop_playlist WHERE id = '${playlist.id}'"))
                        assertEquals(2L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = '${playlist.id}'"))
                        assertArrayEquals(legacyBytes, Files.readAllBytes(fixture.root.resolve("media/${track.sha256}.media")))
                        assertEquals(0L, fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${playlistEntries[0].id}'"))
                        assertEquals(9_007_199_254_740_993L,
                            fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${playlistEntries[1].id}'"))
                        assertEquals("${track.sha256}.media", fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
                    }
                } finally {
                    fixture.execute("DROP TRIGGER acceptance_abort_outbox")
                }
                fixture.reopen()
                assertEquals(baseline, fixture.store.state.value)
                assertEquals(facts, fixture.facts())
                assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_preference"))
                // Rollback must also retain an existing future value when upsert/clear fails.
                fixture.execute("INSERT INTO desktop_track_preference VALUES ('track-a', 'FUTURE_RETAINED')")
                fixture.reopen()
                val preferenceBaseline = fixture.store.state.value
                fixture.execute("CREATE TRIGGER acceptance_abort_preference_fact BEFORE INSERT ON desktop_local_outbox " +
                    "WHEN NEW.kind = 'TRACK_PREFERENCE_UPDATED' BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
                try {
                    listOf("LIKED", "DISLIKED", "NEUTRAL").forEach { preference ->
                        assertEquals("desktop_mutation_failed", failureCode { fixture.store.setTrackPreference(track.id, preference) })
                        assertEquals("FUTURE_RETAINED", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'track-a'"))
                        assertEquals(preferenceBaseline, fixture.store.state.value)
                        assertEquals(facts, fixture.facts())
                    }
                } finally {
                    fixture.execute("DROP TRIGGER acceptance_abort_preference_fact")
                    // Restore only this fixture's synthetic value before older composite assertions.
                    fixture.execute("DELETE FROM desktop_track_preference WHERE trackId = 'track-a'")
                    fixture.reopen()
                }
                assertEquals(baseline, fixture.store.state.value)
                assertEquals("${track.sha256}.media", fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = 'track-a'"))
                // Partial child deletion must fail its affected-count check and restore both refs.
                fixture.execute("CREATE TRIGGER acceptance_ignore_entry BEFORE DELETE ON desktop_playlist_entry " +
                    "WHEN OLD.id = '${playlistEntries[0].id}' BEGIN SELECT RAISE(IGNORE); END")
                try {
                    assertEquals("desktop_playlist_delete_conflict", failureCode { fixture.store.deletePlaylist(playlist.id) })
                    assertEquals(baseline, fixture.store.state.value)
                    assertEquals(facts, fixture.facts())
                    assertEquals(2L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = '${playlist.id}'"))
                } finally {
                    fixture.execute("DROP TRIGGER acceptance_ignore_entry")
                }
                fixture.store.updateTrackMetadata(track.id, "Successful retry", "", 300)
                assertEquals("Successful retry", fixture.store.state.value.tracks.single().title)
                assertEquals(facts.size + 2, fixture.facts().size)

                // Corrupt/unknown ordering is retained and rejected before any domain or fact write.
                val beforeInvalidOrderFacts = fixture.facts()
                fixture.execute("UPDATE desktop_playlist_entry SET ordinal = 0 WHERE id = '${playlistEntries[1].id}'")
                assertEquals("desktop_playlist_order_invalid", failureCode {
                    fixture.store.movePlaylistEntry(playlist.id, playlistEntries[0].id, 1)
                })
                assertEquals("desktop_playlist_order_invalid", failureCode {
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(track.id, track.id))
                })
                assertEquals(0L, fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${playlistEntries[1].id}'"))
                fixture.execute("UPDATE desktop_playlist_entry SET ordinal = 'FUTURE_ORDER' WHERE id = '${playlistEntries[1].id}'")
                assertEquals("desktop_playlist_order_invalid", failureCode {
                    fixture.store.movePlaylistEntry(playlist.id, playlistEntries[0].id, 1)
                })
                assertEquals("FUTURE_ORDER", fixture.text("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${playlistEntries[1].id}'"))
                assertEquals("desktop_playlist_order_invalid", failureCode {
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(track.id, track.id))
                })
                assertEquals(beforeInvalidOrderFacts, fixture.facts())
                fixture.execute("UPDATE desktop_playlist_entry SET ordinal = 9007199254740993 WHERE id = '${playlistEntries[1].id}'")
                fixture.reopen()

                // Only this owned fixture temporarily makes a projection query unavailable.
                // Metadata + its fact can commit before the snapshot read fails.
                val lastPublished = fixture.store.state.value
                val beforeProjectionFailure = fixture.facts()
                fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_retained_playlist")
                try {
                    val projectionFailure = try {
                        fixture.store.updateTrackMetadata(track.id, "Committed before projection failure", "", 400)
                        null
                    } catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_snapshot_unavailable", projectionFailure?.code)
                    assertEquals(lastPublished, fixture.store.state.value)
                    assertEquals("Committed before projection failure", fixture.text("SELECT title FROM desktop_track WHERE id = 'track-a'"))
                    val committedFacts = fixture.facts()
                    assertEquals(beforeProjectionFailure.size + 2, committedFacts.size)
                    beforeProjectionFailure.forEach { (id, payload) -> assertEquals(payload, committedFacts[id]) }

                    val nextWrite = try { fixture.store.createPlaylist("Must not retry"); null }
                        catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_snapshot_unavailable", nextWrite?.code)
                    assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.loadHistoryPage() })
                    assertEquals("desktop_snapshot_unavailable", failureCode {
                        fixture.store.loadHistoryPage(DesktopHistoryCursor(-1, "bad/id"), 0)
                    })
                    listOf<suspend () -> Unit>(
                        { fixture.store.applyEmbeddedTrackText(track.textBaseline(), "Must not retry", "") },
                        { fixture.store.resolveMetadataFile(track.id); Unit },
                        { fixture.store.updateTrackTextMetadata(track.id, "Must not retry", "") },
                        { fixture.store.updateTrackDuration(track.id, 401) },
                        { fixture.store.setTrackPreference(track.id, "LIKED") },
                        { fixture.store.setTrackPreference(track.id, "NEUTRAL") },
                        { fixture.store.removeTrackFromCollection(track.id) },
                        { fixture.store.restoreTrackToCollection(track.id) },
                        { fixture.store.setCurrentListenTasteExcluded(queue.id, "entry-a", "session-a", true); Unit },
                        { fixture.store.setQueueSessionTasteExcluded(queue.id, true); Unit },
                        { fixture.store.deletePlaylist(playlist.id) },
                        { fixture.store.deletePlaylist("missing-playlist") },
                        { fixture.store.appendTracksToPlaylist(playlist.id, listOf(track.id, track.id)) },
                        { fixture.store.appendTracksToPlaylist(playlist.id, emptyList()) },
                        { fixture.store.updatePlaylistDescription(playlist.id, "Retained\n\uD83C\uDFB5", null) },
                    ).forEach { operation -> assertEquals("desktop_snapshot_unavailable", failureCode(operation)) }
                    val capability = try { fixture.store.resolveManagedFile(track.id); null }
                        catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_snapshot_unavailable", capability?.code)
                    assertEquals(committedFacts, fixture.facts())
                    assertEquals(lastPublished, fixture.store.state.value)
                } finally {
                    // Restore this synthetic schema before explicit reopen; no rows are removed.
                    fixture.execute("ALTER TABLE acceptance_retained_playlist RENAME TO desktop_playlist")
                }
                val stillGuarded = try { fixture.store.createPlaylist("Still must not retry"); null }
                    catch (error: DesktopStorageException) { error }
                assertEquals("desktop_snapshot_unavailable", stillGuarded?.code)
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.loadHistoryPage() })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.deletePlaylist(playlist.id) })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.updateTrackTextMetadata(track.id, "Still guarded", "") })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.updateTrackDuration(track.id, 401) })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setTrackPreference(track.id, "NEUTRAL") })
                fixture.reopen()
                assertEquals("Committed before projection failure", fixture.store.state.value.tracks.single().title)
                assertEquals(lastPublished.playlists, fixture.store.state.value.playlists)
                assertEquals(lastPublished.queue, fixture.store.state.value.queue)
                assertEquals(lastPublished.history, fixture.store.state.value.history)
                assertEquals(beforeProjectionFailure.size + 2, fixture.facts().size)

                // A simultaneous transaction abort retains its primary code and a sanitized
                // marker, so the host can require restart without waiting for another operation.
                val recoveredSnapshot = fixture.store.state.value
                val recoveredFacts = fixture.facts()
                fixture.execute("CREATE TRIGGER acceptance_abort_outbox BEFORE INSERT ON desktop_local_outbox " +
                    "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
                try {
                    fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_retained_playlist")
                    try {
                        val combinedFailure = try {
                            fixture.store.updateTrackTextMetadata(track.id, "Must roll back again", "")
                            null
                        } catch (error: DesktopStorageException) { error }
                        assertEquals("desktop_mutation_failed", combinedFailure?.code)
                        val marker = checkNotNull(combinedFailure).suppressed.single() as DesktopStorageException
                        assertEquals("desktop_snapshot_unavailable", marker.code)
                        assertNull(marker.cause)
                        assertTrue(marker.suppressed.isEmpty())
                        assertEquals(recoveredSnapshot, fixture.store.state.value)
                        assertEquals(recoveredFacts, fixture.facts())
                    } finally {
                        fixture.execute("ALTER TABLE acceptance_retained_playlist RENAME TO desktop_playlist")
                    }
                } finally {
                    fixture.execute("DROP TRIGGER acceptance_abort_outbox")
                }
                fixture.reopen()
                assertEquals(recoveredSnapshot, fixture.store.state.value)
                assertEquals(recoveredFacts, fixture.facts())
            }
            Fixture().useStore { fixture ->
                val playlist = fixture.store.createPlaylist("Description projection boundary")
                val unknownDescription = "Future retained\n" + "x".repeat(600)
                fixture.execute("UPDATE desktop_playlist SET description = '$unknownDescription' WHERE id = '${playlist.id}'")
                fixture.reopen()
                val unknownBefore = fixture.store.state.value
                val unknownFacts = fixture.facts()
                fixture.execute("CREATE TRIGGER acceptance_abort_description_fact BEFORE INSERT ON desktop_local_outbox " +
                    "WHEN NEW.kind = 'PLAYLIST_DESCRIPTION_UPDATED' BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
                try {
                    listOf<String?>("Replacement", null).forEach { replacement ->
                        assertEquals("desktop_mutation_failed", failureCode {
                            fixture.store.updatePlaylistDescription(playlist.id, unknownDescription, replacement)
                        })
                        assertEquals(unknownBefore, fixture.store.state.value)
                        assertEquals(unknownFacts, fixture.facts())
                        assertEquals(unknownDescription, fixture.text("SELECT description FROM desktop_playlist WHERE id = '${playlist.id}'"))
                    }
                } finally { fixture.execute("DROP TRIGGER acceptance_abort_description_fact") }
                fixture.reopen()
                assertEquals(unknownBefore, fixture.store.state.value)
                assertEquals(unknownFacts, fixture.facts())
                fixture.store.updatePlaylistDescription(playlist.id, unknownDescription, null)
                val before = fixture.store.state.value
                val facts = fixture.facts()
                val description = "Committed\n\uD83C\uDFB5"
                fixture.execute("ALTER TABLE desktop_listening RENAME TO acceptance_retained_listening")
                try {
                    assertEquals("desktop_snapshot_unavailable", failureCode {
                        fixture.store.updatePlaylistDescription(playlist.id, null, description)
                    })
                    assertEquals(before, fixture.store.state.value)
                    assertEquals(description, fixture.text("SELECT description FROM desktop_playlist WHERE id = '${playlist.id}'"))
                    assertEquals(facts.size + 1, fixture.facts().size)
                } finally { fixture.execute("ALTER TABLE acceptance_retained_listening RENAME TO desktop_listening") }
                assertEquals("desktop_snapshot_unavailable", failureCode {
                    fixture.store.updatePlaylistDescription(playlist.id, description, null)
                })
                fixture.reopen()
                assertEquals(before.copy(playlists = listOf(playlist.copy(description = description))), fixture.store.state.value)
                val committed = fixture.facts()
                facts.forEach { (id, payload) -> assertEquals(payload, committed[id]) }
                fixture.store.updatePlaylistDescription(playlist.id, description, null)
                assertNull(fixture.store.state.value.playlists.single().description)
                assertEquals(committed.size + 1, fixture.facts().size)
            }
        }
    }

    @Test fun futureDatabaseVersionIsRejectedWithoutDeletingData(): Unit = runBlocking {
        withTimeout(30_000) {
            importJobUnsupportedHistoryRetained()
            Fixture().useStore { fixture ->
                preferencesRejectedFilesStayByteExact(fixture)
                fixture.store.addImportedTrack(fixture.track("track-a", "Retained future data"))
                val playlist = fixture.store.createPlaylist("Before future version")
                val description = "Future retained\n" + "x".repeat(600) + "\uD83C\uDFB5"
                fixture.execute("UPDATE desktop_playlist SET description = '$description' WHERE id = '${playlist.id}'")
                repeat(2) { fixture.store.appendToPlaylist(playlist.id, "track-a") }
                val entries = fixture.store.state.value.playlists.single().entries
                fixture.store.renamePlaylist(playlist.id, "Retained future playlist")
                fixture.store.movePlaylistEntry(playlist.id, entries.first().id, 1)
                assertEquals(entries.reversed(), fixture.store.state.value.playlists.single().entries)
                // Legacy unknown negative duration survives label edits; blank artist is valid.
                fixture.execute("UPDATE desktop_track SET durationMs = -7 WHERE id = 'track-a'")
                fixture.store.updateTrackTextMetadata("track-a", "Retained edited future data", "")
                assertEquals(-7L, fixture.store.state.value.tracks.single().durationMs)
                assertEquals(-7L, fixture.number("SELECT durationMs FROM desktop_track WHERE id = 'track-a'"))
                fixture.store.updateTrackDuration("track-a", 0)
                assertEquals("Retained edited future data", fixture.store.state.value.tracks.single().title)
                assertEquals(0L, fixture.store.state.value.tracks.single().durationMs)
                val empty = fixture.store.createPlaylist("Removed before future version")
                fixture.execute("INSERT INTO desktop_local_outbox (changeId, kind, aggregateId, occurredAtMs, payloadJson, version) " +
                    "VALUES ('future-fact', 'FUTURE_KIND', '${empty.id}', 0, '{\"future\":true}', 2)")
                fixture.store.deletePlaylist(empty.id)
                fixture.store.updateTrackTextMetadata("track-a", "Future data with retained fact", " ")
                val futureQueue = DesktopQueue("future-taste-queue", listOf(DesktopQueueEntry("future-taste-entry", "track-a")),
                    "future-taste-entry", sessionId = "future-taste-session", sessionStartedAtMs = 100,
                    sessionTasteState = "FUTURE_SESSION_\u2603", listenTasteState = "FUTURE_LISTEN_\uD83C\uDFB5")
                val futureListen = DesktopListeningEvent("future-taste-session", "future-taste-entry", "track-a", 100, 0, 0, false,
                    futureQueue.sessionTasteState, futureQueue.listenTasteState)
                fixture.store.saveQueue(futureQueue, futureListen)
                fixture.execute("INSERT INTO desktop_track_preference VALUES ('track-a', 'FUTURE_SCHEMA_CHOICE')")
                fixture.execute("INSERT INTO desktop_track_collection VALUES ('track-a', 'FUTURE_SCHEMA_MEMBERSHIP')")
                fixture.reopen()
                val mediaBefore = Files.readAllBytes(fixture.root.resolve("media/${fixture.store.state.value.tracks.single().sha256}.media"))
                assertEquals("{\"future\":true}", fixture.facts().getValue("future-fact"))
                assertEquals(description, fixture.store.state.value.playlists.single().description)
                assertEquals(5L, fixture.number("PRAGMA user_version"))
                val before = fixture.store.state.value
                val facts = fixture.facts()
                val historyBeforeFutureVersion = fixture.store.loadHistoryPage()
                assertEquals(DesktopHistoryPage(listOf(futureListen), null), historyBeforeFutureVersion)
                fixture.closeStore()
                fixture.execute("PRAGMA user_version = 6")
                val failure = try {
                    openDesktopLibraryStore(fixture.root).also { it.close() }
                    null
                } catch (error: DesktopStorageException) { error }
                assertEquals("desktop_database_open_failed", failure?.code)
                assertEquals(6L, fixture.number("PRAGMA user_version"))
                assertEquals(description, fixture.text("SELECT description FROM desktop_playlist WHERE id = '${playlist.id}'"))
                assertEquals(1L, fixture.number("SELECT count(*) FROM desktop_track"))
                assertEquals("FUTURE_SCHEMA_CHOICE", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'track-a'"))
                assertEquals("FUTURE_SCHEMA_MEMBERSHIP", fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = 'track-a'"))
                assertEquals(futureQueue.sessionTasteState, fixture.text("SELECT sessionTasteState FROM desktop_queue"))
                assertEquals(futureQueue.listenTasteState, fixture.text("SELECT listenTasteState FROM desktop_queue"))
                assertEquals(futureListen.sessionTasteState, fixture.text("SELECT sessionTasteState FROM desktop_listening"))
                assertEquals(futureListen.listenTasteState, fixture.text("SELECT listenTasteState FROM desktop_listening"))
                assertArrayEquals(mediaBefore, Files.readAllBytes(fixture.root.resolve("media/${before.tracks.single().sha256}.media")))
                assertEquals(facts, fixture.facts())
                // This reset belongs only to this synthetic fixture, proving failed open released its lock.
                fixture.execute("PRAGMA user_version = 5")
                fixture.reopen()
                assertEquals(before, fixture.store.state.value)
                assertEquals(historyBeforeFutureVersion, fixture.store.loadHistoryPage())
                val closed = fixture.store
                fixture.closeStore()
                assertEquals("desktop_store_closed", failureCode { closed.updatePlaylistDescription(playlist.id, description, null) })
                assertEquals("desktop_store_closed", failureCode { closed.appendTracksToPlaylist(playlist.id, listOf("track-a")) })
                assertEquals("desktop_store_closed", failureCode { closed.appendTracksToPlaylist(playlist.id, emptyList()) })
                assertEquals("desktop_store_closed", failureCode { closed.loadHistoryPage() })
                assertEquals("desktop_store_closed", failureCode { closed.loadHistoryPage(DesktopHistoryCursor(-1, "bad/id"), 0) })
                assertEquals("desktop_store_closed", failureCode { closed.resolveMetadataFile("track-a") })
                assertEquals("desktop_store_closed", failureCode { closed.inspectManagedCopy("track-a"); Unit })
                assertEquals("desktop_store_closed", failureCode { closed.inspectManagedCopy("bad/id"); Unit })
                assertEquals("desktop_store_closed", failureCode {
                    closed.applyEmbeddedTrackText(before.tracks.single().textBaseline(), "Closed", "")
                })
                assertEquals(facts, fixture.facts())
            }
        }
    }

    /** Authored source only; these helpers extend the existing three composite cases. */

    private fun importJobId(): String = UUID.randomUUID().toString()
    private fun importJobAttempt(job: DesktopImportJob, indices: List<Int>): DesktopImportJobAttempt = DesktopImportJobAttempt(
        job.id, checkNotNull(job.revision), importJobId(), importJobId(), importJobId(), indices.map { index ->
            val candidate = job.candidates[index].candidate
            DesktopImportJobReselection(candidate.id, importJobId(), candidate.title, candidate.byteSize)
        })
    private fun importJobSettlement(job: DesktopImportJob, attempt: DesktopImportJobAttempt, index: Int): DesktopImportJobSettlement {
        val selection = attempt.selections[index]
        return DesktopImportJobSettlement(job.id, checkNotNull(job.revision), importJobId(), attempt.attemptId,
            selection.candidateId, selection.selectionId)
    }

    private suspend fun importJobRestartAndExplicitReselection(): Unit {
        Fixture().useStore { fixture ->
            val manifest = DesktopImportJobManifest(importJobId(), List(3) { DesktopImportJobCandidate(importJobId(), "Same", 4) })
            var job = fixture.store.createImportJob(manifest)
            assertEquals(0L, job.revision)
            assertEquals(DesktopImportJobPhase.REVIEW_REQUIRED, job.phase)
            assertTrue(fixture.store.state.value.tracks.isEmpty())
            val frozenFacts = fixture.facts()
            assertEquals(job, fixture.store.createImportJob(manifest))
            assertEquals(frozenFacts, fixture.facts())
            assertEquals("desktop_import_job_conflict", failureCode {
                fixture.store.createImportJob(manifest.copy(candidates = manifest.candidates.reversed()))
            })
            val attempt = importJobAttempt(job, listOf(0, 1))
            val authorization = fixture.store.confirmImportJobAttempt(attempt)
            val permit = checkNotNull(authorization.permit)
            job = authorization.job
            assertEquals(permit, fixture.store.confirmImportJobAttempt(attempt).permit)
            val first = fixture.track(importJobId(), "Same")
            val firstSettlement = importJobSettlement(job, attempt, 0)
            job = fixture.store.commitImportJobCandidate(firstSettlement, permit, first)
            assertEquals(DesktopImportJobOutcome.IMPORTED, job.candidates[0].outcome)
            val cancelled = DesktopImportJobStop(job.id, checkNotNull(job.revision), importJobId(), DesktopImportJobStopReason.CANCELLED)
            job = fixture.store.stopImportJob(cancelled)
            val stoppedRows = fixture.allRows()
            assertEquals(job, fixture.store.stopImportJob(cancelled))
            assertEquals(stoppedRows, fixture.allRows())
            val obsolete = importJobSettlement(job, attempt, 1)
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.commitImportJobCandidate(obsolete, permit, first.copy(id = importJobId()))
            })
            fixture.reopen()
            assertEquals(job, fixture.store.loadImportJob(job.id))
            assertNull(fixture.store.confirmImportJobAttempt(attempt).permit)
            assertEquals(stoppedRows, fixture.allRows())
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.commitImportJobCandidate(obsolete, permit, first.copy(id = importJobId()))
            })
            // Exact replay of a COMMITTED command needs neither the lost permit nor a source read.
            assertEquals(job, fixture.store.commitImportJobCandidate(firstSettlement, permit,
                first.copy(managedFileName = "missing.media")))
            assertEquals(stoppedRows, fixture.allRows())
            val invalidCommitted = importJobAttempt(job, listOf(0))
            assertEquals("desktop_import_job_conflict", failureCode { fixture.store.confirmImportJobAttempt(invalidCommitted) })
            val resumed = importJobAttempt(job, listOf(1))
            val resumedAuthorization = fixture.store.confirmImportJobAttempt(resumed)
            val resumedPermit = checkNotNull(resumedAuthorization.permit)
            job = resumedAuthorization.job
            // A stale replayed stop must not revoke this newer permit.
            assertEquals(job, fixture.store.stopImportJob(cancelled))
            val duplicate = importJobSettlement(job, resumed, 0)
            val duplicateTrack = first.copy(id = importJobId())
            job = fixture.store.commitImportJobCandidate(duplicate, resumedPermit, duplicateTrack)
            assertEquals(DesktopImportJobOutcome.DUPLICATE, job.candidates[1].outcome)
            assertEquals(first.id, job.candidates[1].trackId)
            assertEquals(1, fixture.store.state.value.tracks.size)
            // Unselected candidates are never silently imported or treated as committed.
            assertNull(job.candidates[2].outcome)
            val third = importJobAttempt(job, listOf(2))
            val thirdAuthorization = fixture.store.confirmImportJobAttempt(third)
            job = thirdAuthorization.job
            val thirdPermit = checkNotNull(thirdAuthorization.permit)
            val failed = importJobSettlement(job, third, 0)
            job = fixture.store.failImportJobCandidate(failed, thirdPermit, DesktopImportJobFailure.SOURCE_CHANGED)
            val failedFacts = fixture.facts()
            assertEquals(job, fixture.store.failImportJobCandidate(failed, thirdPermit, DesktopImportJobFailure.SOURCE_CHANGED))
            assertEquals(failedFacts, fixture.facts())
            assertEquals("desktop_import_job_conflict", failureCode {
                fixture.store.failImportJobCandidate(failed, thirdPermit, DesktopImportJobFailure.COPY_FAILED)
            })
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.failImportJobCandidate(importJobSettlement(job, third, 0), thirdPermit, DesktopImportJobFailure.COPY_FAILED)
            })
            val fresh = importJobAttempt(job, listOf(2))
            val freshAuthorization = fixture.store.confirmImportJobAttempt(fresh)
            job = freshAuthorization.job
            val freshPermit = checkNotNull(freshAuthorization.permit)
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.failImportJobCandidate(importJobSettlement(job, third, 0), thirdPermit, DesktopImportJobFailure.COPY_FAILED)
            })
            // Equal labels and sizes do not establish identity; different verified bytes create another track.
            val different = fixture.track(importJobId(), "Same", byteArrayOf(4, 3, 2, 1))
            job = fixture.store.commitImportJobCandidate(importJobSettlement(job, fresh, 0), freshPermit, different)
            assertEquals(DesktopImportJobPhase.COMPLETED, job.phase)
            assertEquals(2, fixture.store.state.value.tracks.size)
            assertNotEquals(job.candidates[0].sha256, job.candidates[2].sha256)
            assertTrue(fixture.store.loadUnfinishedImportJobs().jobs.isEmpty())
            fixture.reopen()
            assertEquals(job, fixture.store.loadImportJob(job.id))
            assertEquals(2L, fixture.number("SELECT COUNT(*) FROM desktop_local_outbox WHERE kind = 'TRACK_IMPORTED'"))
            fixture.facts().values.forEach { payload ->
                assertFalse(payload.contains("managed_file_name"))
                assertFalse(payload.contains(first.managedFileName))
                assertFalse(payload.contains(permit.nonce))
            }
            val replacementManifest = DesktopImportJobManifest(importJobId(), listOf(DesktopImportJobCandidate(importJobId(), "Old", 4)))
            val replacementJob = fixture.store.createImportJob(replacementManifest)
            val replacementAttempt = importJobAttempt(replacementJob, listOf(0)).let { next ->
                next.copy(selections = next.selections.map { it.copy(title = "New explicit preview", byteSize = 3) })
            }
            val replacementAuthorization = fixture.store.confirmImportJobAttempt(replacementAttempt)
            assertEquals(replacementAttempt, replacementAuthorization.job.confirmedAttempt)
            val replaced = fixture.store.commitImportJobCandidate(importJobSettlement(replacementAuthorization.job, replacementAttempt, 0),
                checkNotNull(replacementAuthorization.permit), fixture.track(importJobId(), "New explicit preview", byteArrayOf(7, 8, 9)))
            assertEquals(4L, replaced.candidates.single().candidate.byteSize) // Original manifest is immutable.
            assertEquals(3L, replaced.candidates.single().byteSize)
            assertEquals(replacementAttempt.attemptId, replaced.candidates.single().settledAttemptId)
            fixture.reopen()
            assertEquals(replaced, fixture.store.loadImportJob(replaced.id))
        }
    }

    private suspend fun importJobCompletePagingAndBounds(): Unit {
        Fixture().useStore { fixture ->
            val completedManifest = DesktopImportJobManifest(UUID(0, 1).toString(),
                listOf(DesktopImportJobCandidate(importJobId(), "Completed", 4)))
            var completed = fixture.store.createImportJob(completedManifest)
            val attempt = importJobAttempt(completed, listOf(0))
            val authorization = fixture.store.confirmImportJobAttempt(attempt)
            completed = fixture.store.commitImportJobCandidate(importJobSettlement(authorization.job, attempt, 0),
                checkNotNull(authorization.permit), fixture.track(importJobId(), "Completed"))
            val expected = (2L..20L).map { suffix ->
                fixture.store.createImportJob(DesktopImportJobManifest(UUID(0, suffix).toString(),
                    listOf(DesktopImportJobCandidate(importJobId(), "Pending", 4)))).id
            }.toSet()
            val rows = fixture.allRows()
            val first = fixture.store.loadUnfinishedImportJobs(limit = 1)
            assertTrue(first.jobs.isEmpty())
            assertNotNull(first.next)
            var cursor: DesktopImportJobCursor? = null
            val found = ArrayList<String>()
            var pages = 0
            do {
                val page = fixture.store.loadUnfinishedImportJobs(cursor, 3)
                found.addAll(page.jobs.map { it.id })
                cursor = page.next
                pages++
                assertTrue(pages <= 8)
            } while (cursor != null)
            assertEquals(expected, found.toSet())
            assertEquals(expected.size, found.size)
            assertEquals(rows, fixture.allRows())
            fixture.reopen()
            assertEquals(completed, fixture.store.loadImportJob(completed.id))
            assertEquals(rows, fixture.allRows())
            assertEquals("desktop_import_job_limit", failureCode { fixture.store.loadUnfinishedImportJobs(limit = 0) })
            val candidate = DesktopImportJobCandidate(importJobId(), "Bound", 4)
            val invalid = listOf(emptyList(), List(DesktopImportJobLimits.CANDIDATES + 1) { candidate },
                listOf(candidate, candidate), listOf(candidate.copy(title = "x".repeat(513))),
                listOf(candidate.copy(id = "not-a-uuid")), listOf(candidate.copy(byteSize = 0)),
                listOf(candidate.copy(byteSize = DesktopImportJobLimits.FILE_BYTES + 1)),
                List(5) { candidate.copy(id = importJobId(), byteSize = DesktopImportJobLimits.FILE_BYTES) })
            for (candidates in invalid) {
                assertNotNull(failureCode { fixture.store.createImportJob(DesktopImportJobManifest(importJobId(), candidates)) })
                assertEquals(rows, fixture.allRows())
            }
            val pending = checkNotNull(fixture.store.loadImportJob(expected.first()))
            val valid = importJobAttempt(pending, listOf(0))
            for (bad in listOf(valid.copy(selections = emptyList()), valid.copy(selections = valid.selections + valid.selections),
                valid.copy(selections = listOf(valid.selections.single().copy(candidateId = importJobId()))))) {
                assertNotNull(failureCode { fixture.store.confirmImportJobAttempt(bad) })
                assertEquals(rows, fixture.allRows())
            }
            // PAUSED -> attempt -> PAUSED is an ABA in phase, but revision never goes backwards.
            val paused = fixture.store.stopImportJob(DesktopImportJobStop(pending.id, checkNotNull(pending.revision),
                importJobId(), DesktopImportJobStopReason.PAUSED))
            val fresh = importJobAttempt(paused, listOf(0))
            val running = fixture.store.confirmImportJobAttempt(fresh)
            val again = fixture.store.stopImportJob(DesktopImportJobStop(pending.id, checkNotNull(running.job.revision),
                importJobId(), DesktopImportJobStopReason.PAUSED))
            assertEquals(paused.phase, again.phase)
            assertTrue(checkNotNull(again.revision) > checkNotNull(paused.revision))
            val abaRows = fixture.allRows()
            assertEquals("desktop_import_job_conflict", failureCode { fixture.store.confirmImportJobAttempt(valid) })
            assertEquals(abaRows, fixture.allRows())
        }
    }

    private suspend fun importJobAtomicRollbackAndCancellation(): Unit {
        Fixture().useStore { fixture ->
            val existing = fixture.track(importJobId(), "Retained", byteArrayOf(9, 8, 7, 6))
            fixture.store.addImportedTrack(existing)
            fixture.store.setTrackPreference(existing.id, "DISLIKED")
            fixture.store.removeTrackFromCollection(existing.id)
            val manifest = DesktopImportJobManifest(importJobId(), List(2) { DesktopImportJobCandidate(importJobId(), "Review", 4) })
            var before = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_job BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'IMPORT_JOB_FACT' AND NEW.payloadJson LIKE '%\"event\":\"CANDIDATE\"%' " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode { fixture.store.createImportJob(manifest) })
                assertEquals(before, fixture.allRows())
                assertNull(fixture.store.loadImportJob(manifest.jobId))
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_job") }
            var job = fixture.store.createImportJob(manifest)
            val attempt = importJobAttempt(job, listOf(0, 1))
            before = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_job BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'IMPORT_JOB_FACT' AND NEW.payloadJson LIKE '%\"event\":\"MAPPING\"%' " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode { fixture.store.confirmImportJobAttempt(attempt) })
                assertEquals(before, fixture.allRows())
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_job") }
            val authorization = fixture.store.confirmImportJobAttempt(attempt)
            job = authorization.job
            val permit = checkNotNull(authorization.permit)
            val proposed = fixture.track(importJobId(), "Review")
            val settlement = importJobSettlement(job, attempt, 0)
            before = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_job BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'IMPORT_JOB_FACT' AND NEW.payloadJson LIKE '%\"event\":\"SETTLED\"%' " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.commitImportJobCandidate(settlement, permit, proposed)
                })
                assertEquals(before, fixture.allRows())
                assertEquals(job, fixture.store.loadImportJob(job.id))
                assertTrue(Files.exists(fixture.root.resolve("media/${proposed.managedFileName}")))
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.failImportJobCandidate(settlement, permit, DesktopImportJobFailure.SOURCE_CHANGED)
                })
                assertEquals(before, fixture.allRows())
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_job") }
            // Cancellation before admission leaves rows/facts untouched, with the copied file retained.
            val cancelled = Job()
            try {
                withContext(cancelled) {
                    cancelled.cancel()
                    fixture.store.commitImportJobCandidate(settlement, permit, proposed)
                }
                fail("Cancelled job settlement accepted")
            } catch (_: CancellationException) { /* Expected. */ }
            assertEquals(before, fixture.allRows())
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.commitImportJobCandidate(settlement, permit.copy(nonce = importJobId()), proposed)
            })
            val badBytes = proposed.copy(sha256 = "0".repeat(64), managedFileName = "0".repeat(64) + ".media")
            assertEquals("desktop_media_unavailable", failureCode {
                fixture.store.commitImportJobCandidate(settlement, permit, badBytes)
            })
            assertEquals(before, fixture.allRows())
            job = fixture.store.commitImportJobCandidate(settlement, permit, proposed)
            val duplicate = importJobSettlement(job, attempt, 1)
            fixture.execute("DELETE FROM desktop_managed_locator WHERE trackId = '${existing.id}'")
            before = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_job BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'IMPORT_JOB_FACT' AND NEW.payloadJson LIKE '%\"event\":\"SETTLED\"%' " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.commitImportJobCandidate(duplicate, permit, existing.copy(id = importJobId()))
                })
                assertEquals(before, fixture.allRows()) // Includes the rolled-back locator repair fact.
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_job") }
            job = fixture.store.commitImportJobCandidate(duplicate, permit, existing.copy(id = importJobId()))
            assertEquals(DesktopImportJobPhase.COMPLETED, job.phase)
            val retained = fixture.store.state.value.tracks.single { it.id == existing.id }
            assertEquals("REMOVED", retained.collectionState)
            assertEquals("DISLIKED", retained.preference)
            fixture.reopen()
            assertEquals(job, fixture.store.loadImportJob(job.id))
            assertEquals(retained, fixture.store.state.value.tracks.single { it.id == existing.id })
        }
        Fixture().useStore { fixture ->
            val job = fixture.store.createImportJob(DesktopImportJobManifest(importJobId(),
                listOf(DesktopImportJobCandidate(importJobId(), "Stop failure", 4))))
            val attempt = importJobAttempt(job, listOf(0))
            val authorization = fixture.store.confirmImportJobAttempt(attempt)
            val permit = checkNotNull(authorization.permit)
            val before = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_job BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'IMPORT_JOB_FACT' AND NEW.payloadJson LIKE '%\"event\":\"STOPPED\"%' " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.stopImportJob(DesktopImportJobStop(job.id, checkNotNull(authorization.job.revision),
                        importJobId(), DesktopImportJobStopReason.CANCELLED))
                })
                assertEquals(before, fixture.allRows())
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_job") }
            assertEquals("desktop_import_job_reselection_required", failureCode {
                fixture.store.failImportJobCandidate(importJobSettlement(authorization.job, attempt, 0), permit,
                    DesktopImportJobFailure.SOURCE_UNAVAILABLE)
            })
            assertNull(fixture.store.confirmImportJobAttempt(attempt).permit)
            assertEquals(before, fixture.allRows())
            fixture.reopen()
            assertEquals(authorization.job, fixture.store.loadImportJob(job.id))
            assertNotNull(fixture.store.confirmImportJobAttempt(importJobAttempt(authorization.job, listOf(0))).permit)
        }
    }

    private suspend fun importJobUnsupportedHistoryRetained(): Unit {
        for (scenario in listOf("version", "kind", "sequence", "duplicate_sequence", "revision", "link", "missing",
            "track_link", "track_snapshot_title", "track_snapshot_artist", "track_snapshot_duration",
            "unknown_event", "oversized", "nesting", "budget")) {
            Fixture().useStore { fixture ->
                val manifest = DesktopImportJobManifest(importJobId(), List(if (scenario.startsWith("track_snapshot_")) 2 else 1) {
                    DesktopImportJobCandidate(importJobId(), "Retained", 4)
                })
                val job = fixture.store.createImportJob(manifest)
                val aggregate = DesktopImportJobProjection.aggregate(job.id)
                val template = fixture.facts().entries.single { (_, payload) ->
                    val document = Json.parseToJsonElement(payload).jsonObject
                    document.getValue("mutation").jsonPrimitive.content == "IMPORT_JOB_FACT" &&
                        document.getValue("snapshot").jsonObject.let { snapshot ->
                            snapshot.getValue("event").jsonPrimitive.content == "CANDIDATE" &&
                                snapshot.getValue("body").jsonObject.getValue("candidate_id").jsonPrimitive.content == manifest.candidates.first().id
                        }
                }
                when (scenario) {
                    "version" -> fixture.execute("UPDATE desktop_local_outbox SET version = 2 WHERE changeId = '${template.key}'")
                    "kind" -> fixture.insertSyntheticFact(LocalFactRow(importJobId(), "FUTURE_IMPORT_JOB", aggregate, 0, "{}"))
                    "duplicate_sequence" -> fixture.insertSyntheticFact(LocalFactRow(importJobId(), "IMPORT_JOB_FACT", aggregate, 0, template.value))
                    "missing" -> fixture.execute("DELETE FROM desktop_local_outbox WHERE changeId = '${template.key}'")
                    "track_link", "track_snapshot_title", "track_snapshot_artist", "track_snapshot_duration" -> {
                        val attempt = importJobAttempt(job, listOf(0))
                        val authorization = fixture.store.confirmImportJobAttempt(attempt)
                        val imported = fixture.track(importJobId(), "Retained").copy(artist = "Original artist", durationMs = 42)
                        fixture.store.commitImportJobCandidate(importJobSettlement(authorization.job, attempt, 0),
                            checkNotNull(authorization.permit), imported)
                        if (scenario == "track_link") fixture.execute("DELETE FROM desktop_local_outbox WHERE kind = 'TRACK_IMPORTED'")
                        else {
                            // Ordinary later edits do not invalidate the immutable original snapshot.
                            fixture.store.updateTrackMetadata(imported.id, "Edited later", "Edited artist", 100)
                            assertEquals(DesktopImportJobPhase.RESELECTION_REQUIRED, fixture.store.loadImportJob(job.id)?.phase)
                            val linked = fixture.facts().entries.single { (_, payload) ->
                                Json.parseToJsonElement(payload).jsonObject.getValue("mutation").jsonPrimitive.content == "TRACK_IMPORTED"
                            }
                            val document = Json.parseToJsonElement(linked.value).jsonObject
                            val snapshot = document.getValue("snapshot").jsonObject
                            val key = when (scenario) {
                                "track_snapshot_title" -> "title"
                                "track_snapshot_artist" -> "artist"
                                else -> "duration_ms"
                            }
                            val value = if (key == "title") JsonNull else JsonPrimitive(if (key == "artist") "Wrong source artist" else "43")
                            val changed = JsonObject(document + ("snapshot" to JsonObject(snapshot + (key to value))))
                            fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                                changed.toString().replace("'", "''") + "' WHERE changeId = '${linked.key}'")
                        }
                    }
                    "oversized" -> fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                        "x".repeat(DesktopImportJobLimits.FACT_BYTES + 1) + "' WHERE changeId = '${template.key}'")
                    "nesting" -> fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                        "[".repeat(9) + "0" + "]".repeat(9) + "' WHERE changeId = '${template.key}'")
                    "budget" -> fixture.execute("WITH RECURSIVE n(value) AS (SELECT 1 UNION ALL SELECT value + 1 FROM n " +
                        "WHERE value < ${DesktopImportJobLimits.FACTS}) INSERT INTO desktop_local_outbox " +
                        "(changeId, kind, aggregateId, occurredAtMs, payloadJson, version) SELECT 'job-budget-' || value, " +
                        "'IMPORT_JOB_FACT', '$aggregate', 0, '{}', 1 FROM n")
                    else -> {
                        val document = Json.parseToJsonElement(template.value).jsonObject
                        val snapshot = document.getValue("snapshot").jsonObject
                        val key = when (scenario) { "sequence" -> "sequence"; "revision" -> "revision";
                            "link" -> "previous_fact_id"; else -> "event" }
                        val value = when (scenario) { "sequence" -> "1"; "revision" -> "2";
                            "link" -> importJobId(); else -> "FUTURE_EVENT" }
                        val changed = JsonObject(document + ("snapshot" to JsonObject(snapshot + (key to JsonPrimitive(value)))))
                        fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                            changed.toString().replace("'", "''") + "' WHERE changeId = '${template.key}'")
                    }
                }
                val rows = fixture.allRows()
                val facts = fixture.facts()
                val unsupported = checkNotNull(fixture.store.loadImportJob(job.id))
                assertEquals(DesktopImportJobPhase.UNSUPPORTED, unsupported.phase)
                assertNull(unsupported.revision)
                assertTrue(unsupported.candidates.isEmpty())
                assertEquals(listOf(unsupported), fixture.store.loadUnfinishedImportJobs().jobs)
                assertEquals("desktop_import_job_history_unsupported", failureCode { fixture.store.createImportJob(manifest) })
                assertEquals("desktop_import_job_history_unsupported", failureCode {
                    fixture.store.confirmImportJobAttempt(importJobAttempt(job, listOf(0)))
                })
                assertEquals("desktop_import_job_history_unsupported", failureCode {
                    fixture.store.stopImportJob(DesktopImportJobStop(job.id, 0, importJobId(), DesktopImportJobStopReason.CANCELLED))
                })
                assertEquals(rows, fixture.allRows())
                fixture.reopen()
                assertEquals(unsupported, fixture.store.loadImportJob(job.id))
                assertEquals(facts, fixture.facts())
                // Legacy unrelated outbox values cannot obstruct a separate canonical job identity.
                fixture.insertSyntheticFact(LocalFactRow(importJobId(), "FUTURE_UNRELATED", importJobId(), 0, "{}", 99))
                val separate = fixture.store.createImportJob(manifest.copy(jobId = importJobId()))
                assertEquals(DesktopImportJobPhase.REVIEW_REQUIRED, separate.phase)
                val retainedFacts = fixture.facts()
                facts.forEach { (id, payload) -> assertEquals(payload, retainedFacts[id]) }
            }
        }
    }

    private suspend fun playlistBatchRestartAndBounds(): Unit = coroutineScope {
        Fixture().useStore { fixture ->
            val first = fixture.store.addImportedTrack(fixture.track("batch-first", "First"))
            val second = fixture.store.addImportedTrack(fixture.track("batch-second", "Second", byteArrayOf(5, 6, 7)))
            val playlist = fixture.store.createPlaylist("Batch order")
            fixture.store.appendToPlaylist(playlist.id, first.id)
            val original = fixture.store.state.value.playlists.single().entries.single()
            val oldOrdinal = 9_007_199_254_740_993L
            val futureDescription = "Future retained " + "x".repeat(600)
            fixture.execute("UPDATE desktop_playlist_entry SET ordinal = $oldOrdinal WHERE id = '${original.id}'")
            fixture.execute("UPDATE desktop_playlist SET description = '$futureDescription' WHERE id = '${playlist.id}'")
            fixture.execute("UPDATE desktop_track SET durationMs = -7 WHERE id = '${first.id}'")
            fixture.execute("INSERT INTO desktop_track_preference VALUES ('${first.id}', 'FUTURE_BATCH_PREFERENCE')")
            fixture.reopen()
            val before = fixture.store.state.value
            val beforeRows = fixture.allRows()
            val beforeFacts = fixture.facts()
            val references = listOf(second.id, first.id, second.id)
            val callerOwned = references.toMutableList()
            // The undispatched call reaches its first IO suspension before the caller mutates it.
            val append = launch(start = CoroutineStart.UNDISPATCHED) {
                fixture.store.appendTracksToPlaylist(playlist.id, callerOwned)
            }
            callerOwned.clear()
            callerOwned.add("missing-after-dispatch")
            append.join()
            val entries = fixture.store.state.value.playlists.single().entries
            assertEquals(original, entries.first())
            assertEquals(references, entries.drop(1).map { it.trackId })
            assertEquals(4, entries.map { it.id }.toSet().size)
            assertEquals(before.copy(playlists = before.playlists.map { it.copy(entries = entries) }), fixture.store.state.value)
            val afterRows = fixture.allRows()
            beforeRows.forEach { (table, rows) ->
                if (table == "desktop_playlist_entry" || table == "desktop_local_outbox") {
                    assertTrue(afterRows.getValue(table).containsAll(rows))
                } else assertEquals(rows, afterRows.getValue(table))
            }
            val facts = fixture.facts()
            assertEquals(beforeFacts.size + references.size, facts.size)
            beforeFacts.forEach { (id, payload) -> assertEquals(payload, facts[id]) }
            entries.drop(1).forEachIndexed { index, entry ->
                val fact = (facts - beforeFacts.keys).values.map { Json.parseToJsonElement(it).jsonObject }
                    .single { it.getValue("snapshot").jsonObject.getValue("entry_id").jsonPrimitive.content == entry.id }
                assertEquals("PLAYLIST_ENTRY_APPENDED", fact.getValue("mutation").jsonPrimitive.content)
                val snapshot = fact.getValue("snapshot").jsonObject
                assertEquals(setOf("entry_id", "playlist_id", "track_id", "ordinal"), snapshot.keys)
                assertEquals(playlist.id, snapshot.getValue("playlist_id").jsonPrimitive.content)
                assertEquals(references[index], snapshot.getValue("track_id").jsonPrimitive.content)
                assertEquals((oldOrdinal + index + 1).toString(), snapshot.getValue("ordinal").jsonPrimitive.content)
                assertTrue(snapshot.getValue("ordinal").jsonPrimitive.isString)
                assertEquals(oldOrdinal + index + 1,
                    fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '${entry.id}'"))
            }
            val expected = fixture.store.state.value
            fixture.reopen()
            assertEquals(expected, fixture.store.state.value)
            assertEquals(afterRows, fixture.allRows())
            assertEquals(facts, fixture.facts())
            fixture.store.appendTracksToPlaylist(playlist.id, emptyList())
            fixture.store.appendTracksToPlaylist("missing-playlist", emptyList())
            assertEquals(expected, fixture.store.state.value)
            assertEquals(afterRows, fixture.allRows())
            assertEquals("desktop_identity_invalid", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, "bad/id"))
            })
            assertEquals("desktop_identity_invalid", failureCode {
                fixture.store.appendTracksToPlaylist("bad/id", listOf(first.id))
            })
            assertEquals("desktop_playlist_missing", failureCode {
                fixture.store.appendTracksToPlaylist("missing-playlist", references)
            })
            assertEquals("desktop_playlist_batch_limit", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, List(1_001) { first.id })
            })
            var referencesRead = 0
            val unbounded = object : AbstractList<String>() {
                override val size: Int = Int.MAX_VALUE
                override fun get(index: Int): String {
                    assertTrue(index <= DesktopLocalLimits.MAX_QUEUE_ENTRIES)
                    referencesRead++
                    return first.id
                }
            }
            assertEquals("desktop_playlist_batch_limit", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, unbounded)
            })
            assertEquals(1_001, referencesRead)
            assertEquals(afterRows, fixture.allRows())
            val maximum = List(DesktopLocalLimits.MAX_QUEUE_ENTRIES) { if (it % 2 == 0) second.id else first.id }
            fixture.store.appendTracksToPlaylist(playlist.id, maximum)
            val maximumEntries = fixture.store.state.value.playlists.single().entries
            assertEquals(entries, maximumEntries.take(entries.size))
            assertEquals(maximum, maximumEntries.drop(entries.size).map { it.trackId })
            assertEquals(1_004, maximumEntries.map { it.id }.toSet().size)
            val maximumFacts = fixture.facts()
            assertEquals(facts.size + maximum.size, maximumFacts.size)
            facts.forEach { (id, payload) -> assertEquals(payload, maximumFacts[id]) }
            val maximumSnapshot = fixture.store.state.value
            fixture.reopen()
            assertEquals(maximumSnapshot, fixture.store.state.value)
            assertEquals(maximumFacts, fixture.facts())

            val lastId = maximumEntries.last().id
            fixture.execute("UPDATE desktop_playlist_entry SET ordinal = ${Long.MAX_VALUE - 1} WHERE id = '$lastId'")
            fixture.reopen()
            val nearOverflow = fixture.allRows()
            val nearOverflowSnapshot = fixture.store.state.value
            assertEquals("desktop_playlist_limit", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, second.id))
            })
            assertEquals(nearOverflow, fixture.allRows())
            assertEquals(nearOverflowSnapshot, fixture.store.state.value)
            fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id))
            val terminalId = fixture.store.state.value.playlists.single().entries.last().id
            assertEquals(Long.MAX_VALUE, fixture.number("SELECT ordinal FROM desktop_playlist_entry WHERE id = '$terminalId'"))
            val atLimit = fixture.allRows()
            val atLimitSnapshot = fixture.store.state.value
            assertEquals("desktop_playlist_limit", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(second.id))
            })
            fixture.store.appendTracksToPlaylist(playlist.id, emptyList())
            assertEquals(atLimit, fixture.allRows())
            assertEquals(atLimitSnapshot, fixture.store.state.value)
            fixture.reopen()
            assertEquals(atLimitSnapshot, fixture.store.state.value)
            assertEquals(atLimit, fixture.allRows())
        }
    }

    private suspend fun playlistBatchRollbackAndCancellation(): Unit {
        Fixture().useStore { fixture ->
            val first = fixture.store.addImportedTrack(fixture.track("batch-active", "Active"))
            val second = fixture.store.addImportedTrack(fixture.track("batch-membership", "Membership", byteArrayOf(5, 6, 7)))
            val playlist = fixture.store.createPlaylist("Batch rollback")
            fixture.store.appendToPlaylist(playlist.id, first.id)
            val before = fixture.store.state.value
            val rows = fixture.allRows()
            val facts = fixture.facts()
            val cancelled = Job()
            var propagated = false
            try {
                withContext(cancelled) {
                    cancelled.cancel()
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, second.id))
                }
            } catch (failure: CancellationException) { propagated = true }
            assertTrue(propagated)
            assertEquals(before, fixture.store.state.value)
            assertEquals(rows, fixture.allRows())
            // The second new entry's fact fails only after the first entry AND fact were inserted.
            fixture.execute("CREATE TRIGGER acceptance_abort_later_append BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'PLAYLIST_ENTRY_APPENDED' AND " +
                "EXISTS (SELECT 1 FROM desktop_playlist_entry WHERE id = NEW.aggregateId AND ordinal = 2) AND " +
                "(SELECT COUNT(*) FROM desktop_local_outbox WHERE kind = 'PLAYLIST_ENTRY_APPENDED') = 2 " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_later_append'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(second.id, first.id, second.id))
                })
                assertEquals(before, fixture.store.state.value)
                assertEquals(rows, fixture.allRows())
                assertEquals(facts, fixture.facts())
            } finally {
                fixture.execute("DROP TRIGGER acceptance_abort_later_append")
            }
            fixture.reopen()
            assertEquals(before, fixture.store.state.value)
            assertEquals(rows, fixture.allRows())
            assertEquals("desktop_track_missing", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, "missing-last"))
            })
            assertEquals(rows, fixture.allRows())
            // The persisted membership changes while the caller still sees the old ACTIVE snapshot.
            for (membership in listOf("REMOVED", "FUTURE_BATCH_MEMBERSHIP")) {
                fixture.execute("INSERT OR REPLACE INTO desktop_track_collection VALUES ('${second.id}', '$membership')")
                val refusedRows = fixture.allRows()
                assertEquals("desktop_track_not_in_collection", failureCode {
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, second.id, first.id))
                })
                assertEquals(refusedRows, fixture.allRows())
                assertEquals(facts, fixture.facts())
                assertEquals(before.playlists, fixture.store.state.value.playlists)
                assertEquals(membership, fixture.store.state.value.tracks.single { it.id == second.id }.collectionState)
                fixture.reopen()
                assertEquals(refusedRows, fixture.allRows())
            }
            // Explicit fixture restoration enables an explicit retry; the adapter never retries itself.
            fixture.execute("DELETE FROM desktop_track_collection WHERE trackId = '${second.id}'")
            fixture.store.appendTracksToPlaylist(playlist.id, listOf(second.id, first.id, second.id))
            val committed = fixture.store.state.value
            assertEquals(listOf(first.id, second.id, first.id, second.id), committed.playlists.single().entries.map { it.trackId })
            val committedFacts = fixture.facts()
            assertEquals(facts.size + 3, committedFacts.size)
            facts.forEach { (id, payload) -> assertEquals(payload, committedFacts[id]) }
            fixture.reopen()
            assertEquals(committed, fixture.store.state.value)
            assertEquals(committedFacts, fixture.facts())

            // A batch may commit completely while the subsequent snapshot refresh is unavailable.
            fixture.execute("ALTER TABLE desktop_listening RENAME TO acceptance_retained_batch_listening")
            try {
                assertEquals("desktop_snapshot_unavailable", failureCode {
                    fixture.store.appendTracksToPlaylist(playlist.id, listOf(second.id, first.id))
                })
                assertEquals(committed, fixture.store.state.value)
                assertEquals(6L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = '${playlist.id}'"))
                assertEquals(committedFacts.size + 2, fixture.facts().size)
            } finally {
                fixture.execute("ALTER TABLE acceptance_retained_batch_listening RENAME TO desktop_listening")
            }
            val committedRows = fixture.allRows()
            val refreshedFacts = fixture.facts()
            assertEquals("desktop_snapshot_unavailable", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id))
            })
            assertEquals("desktop_snapshot_unavailable", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, emptyList())
            })
            assertEquals(committedRows, fixture.allRows())
            fixture.reopen()
            val recovered = fixture.store.state.value
            assertEquals(listOf(first.id, second.id, first.id, second.id, second.id, first.id),
                recovered.playlists.single().entries.map { it.trackId })
            assertEquals(refreshedFacts, fixture.facts())
            committedFacts.forEach { (id, payload) -> assertEquals(payload, refreshedFacts[id]) }

            fixture.execute("CREATE TRIGGER acceptance_abort_later_batch_fact BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'PLAYLIST_ENTRY_APPENDED' AND " +
                "EXISTS (SELECT 1 FROM desktop_playlist_entry WHERE id = NEW.aggregateId AND ordinal = 7) " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_later_append'); END")
            try {
                fixture.execute("ALTER TABLE desktop_listening RENAME TO acceptance_retained_batch_listening")
                try {
                    val failure = try {
                        fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id, second.id))
                        null
                    } catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_mutation_failed", failure?.code)
                    val marker = checkNotNull(failure).suppressed.single() as DesktopStorageException
                    assertEquals("desktop_snapshot_unavailable", marker.code)
                    assertNull(marker.cause)
                    assertTrue(marker.suppressed.isEmpty())
                    assertEquals(recovered, fixture.store.state.value)
                    assertEquals(6L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = '${playlist.id}'"))
                    assertEquals(refreshedFacts, fixture.facts())
                } finally {
                    fixture.execute("ALTER TABLE acceptance_retained_batch_listening RENAME TO desktop_listening")
                }
            } finally {
                fixture.execute("DROP TRIGGER acceptance_abort_later_batch_fact")
            }
            assertEquals(committedRows, fixture.allRows())
            assertEquals("desktop_snapshot_unavailable", failureCode {
                fixture.store.appendTracksToPlaylist(playlist.id, listOf(first.id))
            })
            fixture.reopen()
            assertEquals(recovered, fixture.store.state.value)
            assertEquals(committedRows, fixture.allRows())
        }
    }

    private suspend fun trackTextProvenanceRestartAndWriterCoexistence(): Unit {
        val unknown = DesktopTrackFieldProvenance()
        val user = DesktopTrackFieldProvenance(DesktopTrackTextOrigin.USER)
        Fixture().useStore { fixture ->
            val imported = fixture.track("provenance-track", "A")
            val track = fixture.store.addImportedTrack(imported)
            val mediaPath = fixture.root.resolve("media/${imported.managedFileName}")
            val bytes = Files.readAllBytes(mediaPath)
            fixture.store.setTrackPreference(track.id, "DISLIKED")
            val queue = DesktopQueue("provenance-queue", listOf(DesktopQueueEntry("provenance-entry", track.id)),
                "provenance-entry", sessionId = "provenance-listen", sessionStartedAtMs = 100,
                sessionTasteState = "EXCLUDED", listenTasteState = "EXCLUDED")
            val listen = DesktopListeningEvent("provenance-listen", "provenance-entry", track.id, 100, 0, 0, false,
                sessionTasteState = "EXCLUDED", listenTasteState = "EXCLUDED")
            fixture.store.saveQueue(queue, listen)
            fixture.store.removeTrackFromCollection(track.id)
            val retained = fixture.store.state.value
            suspend fun evidence(): DesktopTrackTextProvenance = checkNotNull(fixture.store.loadTrackTextProvenance(track.id))
            suspend fun origins(title: DesktopTrackFieldProvenance, artist: DesktopTrackFieldProvenance): Unit {
                val before = fixture.allRows()
                val snapshot = fixture.store.state.value
                val value = evidence()
                assertEquals(title, value.title)
                assertEquals(artist, value.artist)
                assertSame(snapshot, fixture.store.state.value)
                assertEquals(before, fixture.allRows())
            }
            assertNull(fixture.store.loadTrackTextProvenance("missing-provenance"))
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.loadTrackTextProvenance("bad/id"); Unit })
            origins(unknown, unknown)
            val importFacts = fixture.facts()
            fixture.reopen()
            origins(unknown, unknown)
            assertEquals(importFacts, fixture.facts())
            val oldFact = DesktopLocalIntent.trackMetadataUpdated(
                TrackRow(track.id, "Legacy unknown", track.artist, 99, track.sha256, track.byteSize))
            fixture.execute("UPDATE desktop_track SET title = 'Legacy unknown', durationMs = 99 WHERE id = '${track.id}'")
            fixture.insertSyntheticFact(oldFact)
            origins(unknown, unknown)
            fixture.store.updateTrackDuration(track.id, 100)
            origins(unknown, unknown)
            fixture.store.updateTrackTextMetadata(track.id, "Manual", track.artist)
            origins(user, unknown)
            val titleOnly = evidence()
            fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(titleOnly, DesktopEmbeddedTextFormat.FLAC,
                artist = "Embedded artist"))
            val embeddedArtist = DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, DesktopEmbeddedTextFormat.FLAC)
            origins(user, embeddedArtist)
            fixture.store.updateTrackDuration(track.id, 200)
            fixture.store.updateTrackMetadata(track.id, "Manual", "Embedded artist", 300)
            origins(user, embeddedArtist)
            val noOp = evidence()
            val noOpFacts = fixture.facts()
            fixture.store.updateTrackTextMetadata(track.id, noOp.baseline.title, noOp.baseline.artist)
            fixture.store.updateTrackMetadata(track.id, noOp.baseline.title, noOp.baseline.artist, 300)
            fixture.store.updateTrackDuration(track.id, 300)
            fixture.store.applyEmbeddedTrackText(noOp.baseline, noOp.baseline.title, noOp.baseline.artist)
            fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(noOp, DesktopEmbeddedTextFormat.MP4,
                title = noOp.baseline.title, artist = noOp.baseline.artist))
            fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(noOp, DesktopEmbeddedTextFormat.ID3V23))
            assertEquals(noOpFacts, fixture.facts())
            assertEquals(noOp, evidence())
            fixture.reopen()
            assertEquals(noOp, evidence())
            // Both writes proceed through the store mutex; duration cannot replay a stale label snapshot.
            coroutineScope {
                val retained = fixture.store
                launch { retained.updateTrackTextMetadata(track.id, "Concurrent", "Embedded artist") }
                launch { retained.updateTrackDuration(track.id, 400) }
            }
            origins(user, embeddedArtist)
            assertEquals("Concurrent", evidence().baseline.title)
            assertEquals(400L, fixture.store.state.value.tracks.single().durationMs)
            fixture.store.applyEmbeddedTrackText(evidence().baseline, "Compatibility", "Embedded artist")
            origins(unknown, embeddedArtist)
            for (format in DesktopEmbeddedTextFormat.values()) {
                fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(evidence(), format,
                    title = "From ${format.name}"))
                origins(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, format), embeddedArtist)
            }
            fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(evidence(), DesktopEmbeddedTextFormat.MP4,
                title = "A"))
            origins(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, DesktopEmbeddedTextFormat.MP4), embeddedArtist)
            fixture.store.updateTrackTextMetadata(track.id, "B", "Embedded artist")
            fixture.store.updateTrackTextMetadata(track.id, "A", "Embedded artist")
            origins(user, embeddedArtist)
            // Multiple complete pages, equal timestamps, and A->B->A must use explicit revision order.
            repeat(34) { index ->
                fixture.store.updateTrackTextMetadata(track.id, if (index % 2 == 0) "A" else "B", "Embedded artist")
            }
            val stale = evidence()
            fixture.store.updateTrackTextMetadata(track.id, "A", "Embedded artist")
            fixture.store.updateTrackTextMetadata(track.id, stale.baseline.title, stale.baseline.artist)
            assertEquals(stale.baseline, evidence().baseline)
            assertEquals("desktop_metadata_conflict", failureCode {
                fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(stale, DesktopEmbeddedTextFormat.MP4,
                    title = "Must refuse A-B-A"))
            })
            fixture.execute("UPDATE desktop_local_outbox SET occurredAtMs = 7")
            origins(user, embeddedArtist)
            val beforeLegacy = evidence()
            val row = fixture.store.state.value.tracks.single()
            // Genuine old writer shape, including duration-only facts: no fabricated field intent.
            fun legacy(title: String, duration: Long): Unit {
                val fact = DesktopLocalIntent.trackMetadataUpdated(TrackRow(row.id, title, row.artist, duration, row.sha256, row.byteSize))
                fixture.execute("UPDATE desktop_track SET title = '$title', durationMs = $duration WHERE id = '${row.id}'")
                fixture.insertSyntheticFact(fact)
            }
            legacy("Legacy B", 401)
            legacy(beforeLegacy.baseline.title, 402)
            assertEquals(beforeLegacy.baseline, evidence().baseline)
            origins(unknown, unknown)
            val legacyFacts = fixture.facts()
            fixture.reopen()
            origins(unknown, unknown)
            fixture.store.updateTrackDuration(track.id, 403)
            origins(unknown, unknown)
            fixture.store.updateTrackTextMetadata(track.id, "New manual", row.artist)
            origins(user, unknown)
            val now = evidence()
            legacy(now.baseline.title, 404)
            origins(unknown, unknown)
            fixture.reopen()
            origins(unknown, unknown)
            val all = fixture.facts()
            importFacts.forEach { (id, payload) -> assertEquals(payload, all[id]) }
            legacyFacts.forEach { (id, payload) -> assertEquals(payload, all[id]) }
            assertEquals(track.sha256, evidence().baseline.sha256)
            assertEquals(track.byteSize, evidence().baseline.byteSize)
            assertEquals(queue, fixture.store.state.value.queue)
            assertEquals(listOf(listen), fixture.store.state.value.history)
            val finalTrack = fixture.store.state.value.tracks.single()
            assertEquals(retained.tracks.single().copy(title = finalTrack.title, artist = finalTrack.artist, durationMs = 404), finalTrack)
            assertArrayEquals(bytes, Files.readAllBytes(mediaPath))
        }
    }

    private suspend fun trackTextProvenanceRefusalAndRollback(): Unit {
        Fixture().useStore { fixture ->
            val track = fixture.store.addImportedTrack(fixture.track("provenance-abort", "Original"))
            fixture.store.updateTrackTextMetadata(track.id, "Known user", track.artist)
            fixture.store.updateTrackDuration(track.id, 10)
            val expected = checkNotNull(fixture.store.loadTrackTextProvenance(track.id))
            val rows = fixture.allRows()
            fixture.execute("CREATE TRIGGER acceptance_abort_provenance BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'TRACK_TEXT_PROVENANCE_UPDATED' BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                val operations: List<suspend () -> Unit> = listOf(
                    { fixture.store.updateTrackTextMetadata(track.id, "Rollback manual", "Changed") },
                    { fixture.store.updateTrackMetadata(track.id, "Rollback full", "Changed", 11) },
                    { fixture.store.updateTrackDuration(track.id, 12) },
                    { fixture.store.applyEmbeddedTrackText(expected.baseline, "Rollback compat", "Changed") },
                    { fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(expected,
                        DesktopEmbeddedTextFormat.MP4, title = "Rollback typed")) },
                )
                operations.forEach { operation ->
                    assertEquals("desktop_mutation_failed", failureCode(operation))
                    assertEquals(rows, fixture.allRows())
                    assertEquals(expected, fixture.store.loadTrackTextProvenance(track.id))
                }
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_provenance") }
            fixture.reopen()
            assertEquals(expected, fixture.store.loadTrackTextProvenance(track.id))
            val cancelled = Job()
            try {
                withContext(cancelled) {
                    cancelled.cancel()
                    fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(expected,
                        DesktopEmbeddedTextFormat.FLAC, title = "Cancelled"))
                }
                fail("Cancelled provenance mutation accepted")
            } catch (_: CancellationException) { /* Expected before the transactional mutation. */ }
            assertEquals(rows, fixture.allRows())
            fixture.store.updateTrackDuration(track.id, 13)
            val afterDuration = fixture.facts()
            assertEquals("desktop_metadata_conflict", failureCode {
                fixture.store.applyEmbeddedTrackText(DesktopEmbeddedTrackTextApplication(expected,
                    DesktopEmbeddedTextFormat.MP4, artist = "Stale revision"))
            })
            assertEquals(afterDuration, fixture.facts())
        }
        // Each unsupported history is isolated; production readers never delete or rewrite it.
        for (scenario in listOf("version", "kind", "duplicate_revision", "link", "format", "truncated", "oversized", "budget")) {
            Fixture().useStore { fixture ->
                val track = fixture.store.addImportedTrack(fixture.track("provenance-future", "Original"))
                fixture.store.updateTrackTextMetadata(track.id, "One", track.artist)
                fixture.store.updateTrackTextMetadata(track.id, "Two", track.artist)
                val template = fixture.facts().entries.single {
                    val document = Json.parseToJsonElement(it.value).jsonObject
                    document.getValue("mutation").jsonPrimitive.content == "TRACK_TEXT_PROVENANCE_UPDATED" &&
                        document.getValue("snapshot").jsonObject.getValue("revision").jsonPrimitive.content == "1"
                }
                when (scenario) {
                    "version" -> fixture.execute("UPDATE desktop_local_outbox SET version = 2 WHERE changeId = '${template.key}'")
                    "kind" -> fixture.insertSyntheticFact(LocalFactRow("future-kind", "TRACK_TEXT_V2", track.id, 0, "{}"))
                    "duplicate_revision" -> fixture.insertSyntheticFact(LocalFactRow("duplicate-revision",
                        "TRACK_TEXT_PROVENANCE_UPDATED", track.id, 0, template.value))
                    "link", "format" -> {
                        val document = Json.parseToJsonElement(template.value).jsonObject
                        val snapshot = document.getValue("snapshot").jsonObject
                        val member = if (scenario == "link") "metadata_change_id" else "format"
                        val changed = JsonObject(document + ("snapshot" to JsonObject(snapshot +
                            (member to JsonPrimitive(if (scenario == "link") "missing-link" else "FUTURE_FORMAT")))))
                        fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                            changed.toString().replace("'", "''") + "' WHERE changeId = '${template.key}'")
                    }
                    "truncated" -> fixture.execute("DELETE FROM desktop_local_outbox WHERE changeId = '${template.key}'")
                    "oversized" -> fixture.execute("UPDATE desktop_local_outbox SET payloadJson = '" +
                        "x".repeat(DesktopTextProvenanceProjection.MAX_PROVENANCE_BYTES + 1) + "' WHERE changeId = '${template.key}'")
                    "budget" -> fixture.execute("WITH RECURSIVE n(value) AS (SELECT 1 UNION ALL SELECT value + 1 FROM n " +
                        "WHERE value < ${DesktopTextProvenanceProjection.MAX_FACTS}) INSERT INTO desktop_local_outbox " +
                        "(changeId, kind, aggregateId, occurredAtMs, payloadJson, version) SELECT 'over-budget-' || value, " +
                        "'TRACK_TEXT_PROVENANCE_UPDATED', '${track.id}', 0, '${template.value.replace("'", "''")}', 1 FROM n")
                }
                val rows = fixture.allRows()
                val facts = fixture.facts()
                repeat(2) {
                    val result = checkNotNull(fixture.store.loadTrackTextProvenance(track.id))
                    assertEquals("Two", result.baseline.title)
                    assertEquals(DesktopTrackFieldProvenance(), result.title)
                    assertEquals(DesktopTrackFieldProvenance(), result.artist)
                    assertEquals(rows, fixture.allRows())
                    assertEquals(facts, fixture.facts())
                    fixture.reopen()
                }
                fixture.store.updateTrackTextMetadata(track.id, "Future history retained", track.artist)
                val retainedUnknown = checkNotNull(fixture.store.loadTrackTextProvenance(track.id))
                assertEquals(DesktopTrackFieldProvenance(), retainedUnknown.title)
                val retainedFacts = fixture.facts()
                facts.forEach { (id, payload) -> assertEquals(payload, retainedFacts[id]) }
            }
        }
    }

    private fun Fixture.insertSyntheticFact(fact: LocalFactRow): Unit {
        fun quote(value: String): String = "'" + value.replace("'", "''") + "'"
        execute("INSERT INTO desktop_local_outbox (changeId, kind, aggregateId, occurredAtMs, payloadJson, version) VALUES (" +
            "${quote(fact.changeId)}, ${quote(fact.kind)}, ${quote(fact.aggregateId)}, ${fact.occurredAtMs}, " +
            "${quote(fact.payloadJson)}, ${fact.version})")
    }

    private suspend fun embeddedMetadataReaderAndExplicitApply(): Unit {
        assertTrue("Artwork acceptance requires an offscreen JVM", java.awt.GraphicsEnvironment.isHeadless())
        Fixture().useStore { fixture ->
            val png = metadataImage("png", 640, 320)
            val jpeg = metadataImage("jpeg", 24, 12)
            fun read(bytes: ByteArray): DesktopEmbeddedMetadata {
                val path = fixture.root.resolve("synthetic-metadata.bin")
                Files.write(path, bytes)
                val result = DesktopEmbeddedMetadataReader.read(path, bytes.size.toLong())
                assertArrayEquals(bytes, Files.readAllBytes(path))
                return result
            }
            val v23 = metadataId3(3, listOf(
                metadataFrame(3, "TIT2", byteArrayOf(0) + "  Caf\u00e9  ".toByteArray(Charsets.ISO_8859_1) + byteArrayOf(0)),
                metadataFrame(3, "TPE1", byteArrayOf(1) + "Artist \uD83C\uDFB5".toByteArray(Charsets.UTF_16)),
                metadataFrame(3, "TALB", byteArrayOf(0) + "Album".toByteArray()),
                metadataFrame(3, "TIT2", byteArrayOf(0) + "Later title".toByteArray()),
                metadataFrame(3, "APIC", metadataApic(png)),
            ))
            val parsed23 = read(v23)
            assertEquals("AVAILABLE", parsed23.status)
            assertEquals("ID3V23", parsed23.format)
            assertEquals("  Caf\u00e9  ", parsed23.title)
            assertEquals("Artist \uD83C\uDFB5", parsed23.artist)
            assertEquals("Album", parsed23.album)
            val cover = checkNotNull(parsed23.artwork)
            assertEquals(512, cover.width)
            assertEquals(256, cover.height)
            assertTrue(cover.byteSize <= DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES)
            val saved = cover.copyPngBytes()
            val copy = cover.copyPngBytes()
            copy.fill(0)
            assertArrayEquals(saved, cover.copyPngBytes())
            assertEquals(MessageDigest.getInstance("SHA-256").digest(saved).joinToString("") {
                "%02x".format(it.toInt() and 255)
            }, cover.sha256)
            val callerBytes = saved.copyOf()
            val immutable = DesktopArtwork(cover.sha256, callerBytes, cover.width, cover.height)
            callerBytes.fill(0)
            assertArrayEquals(saved, immutable.copyPngBytes())
            val v24 = metadataId3(4, listOf(
                metadataFrame(4, "TIT2", byteArrayOf(3) + "Title \uD83C\uDFB5".toByteArray(Charsets.UTF_8)),
                metadataFrame(4, "TPE1", byteArrayOf(2) + "Big endian".toByteArray(Charsets.UTF_16BE)),
                metadataFrame(4, "APIC", metadataApic(jpeg, mime = "image/jpeg")),
            ))
            val parsed24 = read(v24)
            assertEquals("ID3V24", parsed24.format)
            assertEquals("Title \uD83C\uDFB5", parsed24.title)
            assertEquals("Big endian", parsed24.artist)
            assertNull(parsed24.album)
            assertEquals(24, checkNotNull(parsed24.artwork).width)
            val flac = metadataFlac(listOf(
                4 to metadataComments(listOf("title=First", "TITLE=Second", "ARTIST=Artist", "ALBUM=Album")),
                6 to metadataPicture(png, 640, 320),
            ))
            val parsedFlac = read(flac)
            assertEquals("AVAILABLE", parsedFlac.status)
            assertEquals("FLAC", parsedFlac.format)
            assertEquals("First", parsedFlac.title)
            assertEquals("Artist", parsedFlac.artist)
            assertEquals("Album", parsedFlac.album)
            assertArrayEquals(saved, checkNotNull(parsedFlac.artwork).copyPngBytes())
            val noTags = read(metadataFlac(emptyList()))
            assertEquals("NO_TAGS", noTags.status)
            assertNull(noTags.title)
            assertNull(noTags.artist)
            assertNull(noTags.album)
            assertNull(noTags.artwork)
            listOf(
                byteArrayOf(-1, -5, -112, 0), // MPEG-1 Layer III.
                byteArrayOf(-1, -13, -128, 0), // MPEG-2 Layer III.
                byteArrayOf(-1, -29, -128, 0), // MPEG-2.5 Layer III.
            ).forEach { assertEquals("NO_TAGS", read(it).status) }
            listOf(
                byteArrayOf(-1, -15, 80, -128, 0, -1, -4), // AAC/ADTS, MPEG-4 ID.
                byteArrayOf(-1, -7, 80, -128, 0, -1, -4), // AAC/ADTS, MPEG-2 ID.
                byteArrayOf(-1, -21, -112, 0), // Reserved MPEG version.
                byteArrayOf(-1, -3, -112, 0), // Layer II is outside the MP3 subset.
                byteArrayOf(-1, -1, -112, 0), // Layer I.
                byteArrayOf(-1, -5, -16, 0), // Reserved bitrate index.
                byteArrayOf(-1, -5, -100, 0), // Reserved sample-rate index.
                byteArrayOf(-1, -5, -112, 2), // Reserved emphasis.
                byteArrayOf(-1, -5, -112), // Truncated header.
            ).forEach { assertEquals("UNSUPPORTED", read(it).status) }
            assertEquals("UNSUPPORTED", read(pcmWave()).status)

            val goodText = metadataFrame(4, "TIT2", byteArrayOf(3) + "Safe".toByteArray())
            listOf(
                metadataId3(4, listOf(goodText), flags = 128),
                metadataId3(4, listOf(goodText), flags = 64),
                metadataId3(4, listOf(goodText), flags = 16),
                metadataId3(4, listOf(metadataFrame(4, "TIT2", byteArrayOf(3, 65), features = 8))),
                metadataId3(3, listOf(metadataFrame(3, "TIT2", byteArrayOf(3, 65)))),
                metadataFlac(listOf(7 to byteArrayOf(0))),
            ).forEach { assertEquals("UNSUPPORTED", read(it).status) }
            val validUnicode = "x".repeat(4_094) + "\uD83C\uDFB5"
            assertEquals(validUnicode, read(metadataId3(4, listOf(metadataFrame(4, "TIT2",
                byteArrayOf(3) + validUnicode.toByteArray(Charsets.UTF_8))))).title)
            listOf(
                byteArrayOf(0) + "Safe".toByteArray() + byteArrayOf(0, 0, -1),
                byteArrayOf(0) + "Safe".toByteArray() + byteArrayOf(0) + ByteArray(20_000),
                byteArrayOf(1, -1, -2) + "Safe".toByteArray(Charsets.UTF_16LE) + byteArrayOf(0, 0, 0, -40, -1),
                byteArrayOf(1, -2, -1) + "Safe".toByteArray(Charsets.UTF_16BE) + byteArrayOf(0, 0, -40),
            ).forEach { payload ->
                val terminated = read(metadataId3(3, listOf(metadataFrame(3, "TIT2", payload),
                    metadataFrame(3, "TPE1", byteArrayOf(0) + "After".toByteArray()))))
                assertEquals("AVAILABLE", terminated.status)
                assertEquals("Safe", terminated.title)
                assertEquals("After", terminated.artist)
            }
            for (charset in listOf(Charsets.UTF_16LE, Charsets.UTF_16BE)) {
                val bom = if (charset == Charsets.UTF_16LE) byteArrayOf(-1, -2) else byteArrayOf(-2, -1)
                val aligned = byteArrayOf(1) + bom + "A\u0100B".toByteArray(charset) + byteArrayOf(0, 0, -1)
                assertEquals("A\u0100B", read(metadataId3(3, listOf(metadataFrame(3, "TIT2", aligned)))).title)
            }
            listOf(
                v23.copyOf(v23.size - 5),
                metadataId3(4, listOf(goodText), flags = 1),
                metadataId3(4, listOf(metadataFrame(4, "TIT2", byteArrayOf(3, -64, -81)))),
                metadataId3(3, listOf(metadataFrame(3, "TIT2", byteArrayOf(1, -1, -2, 0, -40)))),
                metadataId3(4, listOf(metadataFrame(4, "TIT2", byteArrayOf(3) + "A\u0000B".toByteArray()))),
                metadataId3(4, listOf(metadataFrame(4, "TIT2", byteArrayOf(3) + "x".repeat(4_097).toByteArray()))),
                metadataId3(4, List(1_025) { metadataFrame(4, "XAAA", byteArrayOf(0)) }),
                metadataFlac(List(1_024) { 1 to ByteArray(0) }),
                metadataFlac(listOf(4 to metadataComments(List(1_022) { "TITLE=One" }), 1 to ByteArray(0))),
                metadataFlac(listOf(4 to (metadataNumber(0, true) + metadataNumber(1_024, true)))),
                metadataFlac(listOf(4 to metadataComments(listOf("TITLE=One")), 4 to metadataComments(listOf("TITLE=Two")))),
                metadataFlac(listOf(4 to (metadataNumber(0, true) + metadataNumber(1, true) +
                    metadataNumber(-1, true)))),
                "ID3".toByteArray() + byteArrayOf(4, 0, 0) + metadataSyncsafe(8 * 1024 * 1024),
            ).forEach { assertEquals("INVALID", read(it).status) }
            listOf(
                metadataApic("https://example.invalid/no-fetch".toByteArray(), mime = "-->"),
                metadataApic(png, type = 4),
                metadataApic(byteArrayOf(1, 2, 3)),
                metadataApic(metadataImage("png", 2_049, 1)),
                metadataApic(ByteArray(DesktopEmbeddedMetadataLimits.MAX_IMAGE_BYTES + 1)),
                byteArrayOf(7, 0, 3, 0), // Unsupported encoding inside a bounded optional picture.
                byteArrayOf(0) + "image/png".toByteArray(), // Missing MIME terminator and picture fields.
                byteArrayOf(1) + "image/png".toByteArray() + byteArrayOf(0, 3, -1, -2, 0, -40, 0, 0) + png,
            ).forEach { picture ->
                val safeText = read(metadataId3(4, listOf(goodText, metadataFrame(4, "APIC", picture),
                    metadataFrame(4, "TPE1", byteArrayOf(3) + "After".toByteArray()))))
                assertEquals("AVAILABLE", safeText.status)
                assertEquals("Safe", safeText.title)
                assertEquals("After", safeText.artist)
                assertNull(safeText.artwork)
            }
            val safeFlac = read(metadataFlac(listOf(4 to metadataComments(listOf("TITLE=Safe")),
                6 to metadataPicture(png, 2_049, 1))))
            assertEquals("Safe", safeFlac.title)
            assertNull(safeFlac.artwork)
            listOf(
                metadataPicture(ByteArray(DesktopEmbeddedMetadataLimits.MAX_IMAGE_BYTES + 1), 1, 1),
                metadataNumber(3) + metadataNumber(33), // Invalid optional MIME length.
                metadataPicture(png, 1, 1).copyOf(12), // Missing description/dimension/payload fields.
            ).forEach { picture ->
                for (blocks in listOf(
                    listOf(4 to metadataComments(listOf("TITLE=Safe")), 6 to picture),
                    listOf(6 to picture, 4 to metadataComments(listOf("TITLE=Safe"))),
                )) {
                    val retained = read(metadataFlac(blocks))
                    assertEquals("AVAILABLE", retained.status)
                    assertEquals("Safe", retained.title)
                    assertNull(retained.artwork)
                }
            }
            assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(fixture.root.resolve("missing"), 10).status)
            val changed = fixture.root.resolve("synthetic-metadata.bin")
            Files.write(changed, v24)
            val sourceBefore = Files.readAttributes(changed, java.nio.file.attribute.BasicFileAttributes::class.java,
                java.nio.file.LinkOption.NOFOLLOW_LINKS)
            assertTrue(DesktopEmbeddedMetadataReader.sameFileSnapshot(sourceBefore, sourceBefore))
            Files.setLastModifiedTime(changed, java.nio.file.attribute.FileTime.fromMillis(sourceBefore.lastModifiedTime().toMillis() + 10_000))
            val sourceAfter = Files.readAttributes(changed, java.nio.file.attribute.BasicFileAttributes::class.java,
                java.nio.file.LinkOption.NOFOLLOW_LINKS)
            assertFalse(DesktopEmbeddedMetadataReader.sameFileSnapshot(sourceBefore, sourceAfter))
            assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(changed, v24.size + 1L).status)
            Files.write(changed, v24 + byteArrayOf(0))
            assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(changed, v24.size.toLong()).status)
            try {
                Thread.currentThread().interrupt()
                try { DesktopEmbeddedMetadataReader.read(changed, v24.size + 1L); fail("Interrupted read accepted") }
                catch (expected: InterruptedException) { assertTrue(Thread.currentThread().isInterrupted) }
            } finally { Thread.interrupted() }

            val imported = fixture.track("embedded-track", "User label", v23)
            val track = fixture.store.addImportedTrack(imported)
            fixture.store.setTrackPreference(track.id, "LIKED")
            val retainedQueue = DesktopQueue("embedded-queue", listOf(DesktopQueueEntry("embedded-entry", track.id)),
                "embedded-entry", sessionId = "embedded-session", sessionStartedAtMs = 100)
            val retainedListen = DesktopListeningEvent("embedded-session", "embedded-entry", track.id, 100, 0, 0, false)
            fixture.store.saveQueue(retainedQueue, retainedListen)
            fixture.store.removeTrackFromCollection(track.id)
            val path = fixture.root.resolve("media/${imported.managedFileName}")
            val beforeLookup = fixture.store.state.value
            val beforeLookupFacts = fixture.facts()
            val presentCopy = DesktopCopyInspection(track.id, track.sha256, track.byteSize, "PRESENT")
            val unavailableCopy = presentCopy.copy(status = "UNAVAILABLE")
            val beforeInspectionRows = fixture.allRows()
            assertEquals(presentCopy, fixture.store.inspectManagedCopy(track.id))
            assertNull(fixture.store.inspectManagedCopy("missing-track"))
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.inspectManagedCopy("bad/id"); Unit })
            assertSame(beforeLookup, fixture.store.state.value)
            assertEquals(beforeInspectionRows, fixture.allRows())
            val cancelledInspection = Job()
            var inspectionCancelled = false
            try {
                withContext(cancelledInspection) {
                    cancelledInspection.cancel()
                    fixture.store.inspectManagedCopy(track.id)
                }
            } catch (failure: CancellationException) { inspectionCancelled = true }
            assertTrue(inspectionCancelled)
            assertSame(beforeLookup, fixture.store.state.value)
            assertEquals(beforeInspectionRows, fixture.allRows())
            assertEquals(path.toString(), fixture.store.resolveMetadataFile(track.id))
            assertEquals(imported.managedFileName, fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = '${track.id}'"))
            assertNull(fixture.store.resolveMetadataFile("missing-track"))
            assertEquals(beforeLookup, fixture.store.state.value)
            assertEquals(beforeLookupFacts, fixture.facts())
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.resolveMetadataFile("bad/id") })
            fixture.execute("ALTER TABLE desktop_managed_locator RENAME TO acceptance_embedded_retained_locator")
            try {
                val inspectionFailure = try { fixture.store.inspectManagedCopy(track.id); null }
                    catch (error: DesktopStorageException) { error }
                assertEquals("desktop_copy_inspection_failed", inspectionFailure?.code)
                assertEquals("desktop_copy_inspection_failed", inspectionFailure?.message)
                assertNull(inspectionFailure?.cause)
                assertTrue(checkNotNull(inspectionFailure).suppressed.isEmpty())
                assertEquals("desktop_media_lookup_failed", failureCode { fixture.store.resolveMetadataFile(track.id) })
                assertSame(beforeLookup, fixture.store.state.value)
                assertEquals(beforeLookupFacts, fixture.facts())
            } finally { fixture.execute("ALTER TABLE acceptance_embedded_retained_locator RENAME TO desktop_managed_locator") }
            assertEquals(presentCopy, fixture.store.inspectManagedCopy(track.id))
            assertEquals(beforeInspectionRows, fixture.allRows())
            assertEquals(path.toString(), fixture.store.resolveMetadataFile(track.id))
            val baseline = track.textBaseline()
            fixture.execute("UPDATE desktop_track SET durationMs = 9007199254740993 WHERE id = '${track.id}'")
            fixture.store.applyEmbeddedTrackText(baseline, checkNotNull(parsed23.title), checkNotNull(parsed23.artist))
            val applied = fixture.store.state.value.tracks.single()
            assertEquals(9_007_199_254_740_993L, applied.durationMs)
            assertEquals(track.sha256, applied.sha256)
            assertEquals(track.byteSize, applied.byteSize)
            assertEquals(parsed23.title, applied.title)
            assertEquals(parsed23.artist, applied.artist)
            assertEquals("LIKED", applied.preference)
            assertEquals("REMOVED", applied.collectionState)
            assertEquals(retainedQueue, fixture.store.state.value.queue)
            assertEquals(listOf(retainedListen), fixture.store.state.value.history)
            val factSnapshot = fixture.facts().filterKeys { it !in beforeLookupFacts }.values
                .map { Json.parseToJsonElement(it).jsonObject }
                .single { it.getValue("mutation").jsonPrimitive.content == "TRACK_METADATA_UPDATED" }
                .getValue("snapshot").jsonObject
            assertEquals("9007199254740993", factSnapshot.getValue("duration_ms").jsonPrimitive.content)
            val appliedFacts = fixture.facts()
            fixture.store.applyEmbeddedTrackText(applied.textBaseline(), applied.title, applied.artist)
            assertEquals(appliedFacts, fixture.facts())
            listOf(baseline, applied.textBaseline().copy(artist = "Stale"),
                applied.textBaseline().copy(sha256 = "0".repeat(64)), applied.textBaseline().copy(byteSize = track.byteSize + 1))
                .forEach { stale -> assertEquals("desktop_metadata_conflict", failureCode {
                    fixture.store.applyEmbeddedTrackText(stale, "Must refuse", "")
                }) }
            fixture.store.updateTrackTextMetadata(track.id, "Later user label", applied.artist)
            val afterUserEdit = fixture.facts()
            assertEquals("desktop_metadata_conflict", failureCode {
                fixture.store.applyEmbeddedTrackText(applied.textBaseline(), "Stale preview", "")
            })
            assertEquals("Later user label", fixture.store.state.value.tracks.single().title)
            assertEquals(afterUserEdit, fixture.facts())
            fixture.store.applyEmbeddedTrackText(fixture.store.state.value.tracks.single().textBaseline(), applied.title, applied.artist)
            val finalFacts = fixture.facts()
            assertEquals("desktop_track_missing", failureCode {
                fixture.store.applyEmbeddedTrackText(baseline.copy(trackId = "missing-track"), "Valid", "")
            })
            assertEquals("desktop_identity_invalid", failureCode {
                fixture.store.applyEmbeddedTrackText(baseline.copy(trackId = "bad/id"), "Valid", "")
            })
            listOf("Bad\u0000", "Bad\uD800", "Bad\uDC00", "x".repeat(4_097)).forEach { artist ->
                assertEquals("desktop_metadata_invalid", failureCode {
                    fixture.store.applyEmbeddedTrackText(applied.textBaseline(), "Valid", artist)
                })
            }
            listOf("", " \t", "Bad\u0000", "Bad\uD800", "x".repeat(4_097)).forEach { title ->
                assertEquals("desktop_metadata_invalid", failureCode {
                    fixture.store.applyEmbeddedTrackText(applied.textBaseline(), title, "")
                })
            }
            assertEquals(finalFacts, fixture.facts())
            assertArrayEquals(v23, Files.readAllBytes(path))
            val beforeUnavailable = fixture.store.state.value
            val retainedRows = fixture.allRows()
            // Unsupported/misbound locators and missing locator rows never become capabilities or repairs.
            for (name in listOf("future.managed", "${track.sha256}.new", "../${imported.managedFileName}",
                    "${"0".repeat(64)}.media")) {
                fixture.execute("UPDATE desktop_managed_locator SET fileName = '$name' WHERE trackId = '${track.id}'")
                val rows = fixture.allRows()
                assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
                assertEquals(rows, fixture.allRows())
                assertSame(beforeUnavailable, fixture.store.state.value)
                assertArrayEquals(v23, Files.readAllBytes(path))
            }
            fixture.execute("DELETE FROM desktop_managed_locator WHERE trackId = '${track.id}'")
            val missingLocatorRows = fixture.allRows()
            assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
            assertEquals(missingLocatorRows, fixture.allRows())
            fixture.execute("INSERT INTO desktop_managed_locator VALUES ('${track.id}', '${imported.managedFileName}')")
            // An observation binds to current persisted size, even while the display snapshot is stale.
            fixture.execute("UPDATE desktop_track SET byteSize = ${track.byteSize + 1} WHERE id = '${track.id}'")
            val changedSizeRows = fixture.allRows()
            assertEquals(unavailableCopy.copy(byteSize = track.byteSize + 1), fixture.store.inspectManagedCopy(track.id))
            assertEquals(changedSizeRows, fixture.allRows())
            assertSame(beforeUnavailable, fixture.store.state.value)
            fixture.execute("UPDATE desktop_track SET byteSize = ${track.byteSize} WHERE id = '${track.id}'")
            assertEquals(retainedRows, fixture.allRows())
            if ("posix" in path.fileSystem.supportedFileAttributeViews()) {
                // Only owned synthetic entries are linked; this branch is mandatory on macOS/Linux.
                val link = fixture.root.resolve("metadata-link")
                Files.createSymbolicLink(link, path)
                assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(link, track.byteSize).status)
                val directoryLink = fixture.root.resolve("metadata-directory-link")
                Files.createSymbolicLink(directoryLink, path.parent)
                assertEquals("UNAVAILABLE", DesktopEmbeddedMetadataReader.read(directoryLink.resolve(path.fileName), track.byteSize).status)
                val backup = fixture.root.resolve("synthetic-owned-backup")
                Files.move(path, backup)
                Files.createSymbolicLink(path, backup)
                assertNull(fixture.store.resolveMetadataFile(track.id))
                assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
                assertArrayEquals(v23, Files.readAllBytes(backup))
                assertTrue(Files.isSymbolicLink(path))
                Files.delete(path)
                Files.move(backup, path)
            }
            Files.write(path, v23 + byteArrayOf(0))
            assertEquals("desktop_media_integrity_failed", failureCode { fixture.store.resolveMetadataFile(track.id) })
            assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
            assertArrayEquals(v23 + byteArrayOf(0), Files.readAllBytes(path))
            val sameSizeChangedBytes = v23.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }
            Files.write(path, sameSizeChangedBytes)
            // PRESENT is deliberately no hash, header or decoder proof.
            assertEquals(presentCopy, fixture.store.inspectManagedCopy(track.id))
            assertArrayEquals(sameSizeChangedBytes, Files.readAllBytes(path))
            Files.write(path, v23)
            Files.move(path, fixture.root.resolve("synthetic-missing-media"))
            assertNull(fixture.store.resolveMetadataFile(track.id))
            assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
            assertSame(beforeUnavailable, fixture.store.state.value)
            assertEquals(retainedRows, fixture.allRows())
            assertEquals(finalFacts, fixture.facts())
            val replacementName = "${track.sha256}.mp3"
            val replacement = path.resolveSibling(replacementName)
            Files.copy(fixture.root.resolve("synthetic-missing-media"), replacement)
            // An unreferenced sibling alone cannot repair the observation's locator.
            assertEquals(unavailableCopy, fixture.store.inspectManagedCopy(track.id))
            assertSame(beforeUnavailable, fixture.store.state.value)
            assertEquals(retainedRows, fixture.allRows())
            assertEquals(track.id, fixture.store.addImportedTrack(imported.copy(id = "copy-reimport", managedFileName = replacementName)).id)
            val afterReimport = fixture.store.state.value
            val reimportRows = fixture.allRows()
            val reimportFacts = fixture.facts()
            assertEquals(replacementName, fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = '${track.id}'"))
            assertEquals(presentCopy, fixture.store.inspectManagedCopy(track.id))
            assertSame(afterReimport, fixture.store.state.value)
            assertEquals(reimportRows, fixture.allRows())
            assertEquals(reimportFacts, fixture.facts())
            assertArrayEquals(v23, Files.readAllBytes(replacement))
            assertArrayEquals(v23, Files.readAllBytes(fixture.root.resolve("synthetic-missing-media")))
        }
    }

    private fun DesktopTrack.textBaseline(): DesktopTrackTextBaseline = DesktopTrackTextBaseline(id, title, artist, sha256, byteSize)

    private suspend fun embeddedApplyStickyLatch(): Unit {
        for (abortOutbox in listOf(false, true)) Fixture().useStore { fixture ->
            val track = fixture.store.addImportedTrack(fixture.track("embedded-sticky", "Before apply"))
            val before = fixture.store.state.value
            val facts = fixture.facts()
            if (abortOutbox) fixture.execute("CREATE TRIGGER acceptance_abort_embedded_fact BEFORE INSERT ON desktop_local_outbox " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_embedded_retained_playlist")
            try {
                val failure = try {
                    fixture.store.applyEmbeddedTrackText(track.textBaseline(), "Applied before failed snapshot", "Embedded artist")
                    null
                } catch (error: DesktopStorageException) { error }
                assertEquals(if (abortOutbox) "desktop_mutation_failed" else "desktop_snapshot_unavailable", failure?.code)
                if (abortOutbox) {
                    assertEquals("desktop_snapshot_unavailable", (checkNotNull(failure).suppressed.single() as DesktopStorageException).code)
                }
                assertEquals(before, fixture.store.state.value)
                assertEquals(if (abortOutbox) track.title else "Applied before failed snapshot",
                    fixture.text("SELECT title FROM desktop_track WHERE id = '${track.id}'"))
                assertEquals(facts.size + if (abortOutbox) 0 else 2, fixture.facts().size)
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.resolveMetadataFile(track.id) })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.inspectManagedCopy(track.id); Unit })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.inspectManagedCopy("missing-track"); Unit })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.inspectManagedCopy("bad/id"); Unit })
                assertEquals("desktop_snapshot_unavailable", failureCode {
                    fixture.store.applyEmbeddedTrackText(track.textBaseline(), "Must not retry", "")
                })
            } finally {
                fixture.execute("ALTER TABLE acceptance_embedded_retained_playlist RENAME TO desktop_playlist")
                if (abortOutbox) fixture.execute("DROP TRIGGER acceptance_abort_embedded_fact")
            }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.resolveMetadataFile(track.id) })
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.inspectManagedCopy(track.id); Unit })
            fixture.reopen()
            assertEquals(DesktopCopyInspection(track.id, track.sha256, track.byteSize, "PRESENT"), fixture.store.inspectManagedCopy(track.id))
            assertEquals(if (abortOutbox) track.title else "Applied before failed snapshot", fixture.store.state.value.tracks.single().title)
            if (abortOutbox) assertEquals(facts, fixture.facts())
        }
    }

    private fun metadataNumber(value: Int, little: Boolean = false): ByteArray = ByteBuffer.allocate(4)
        .order(if (little) ByteOrder.LITTLE_ENDIAN else ByteOrder.BIG_ENDIAN).putInt(value).array()

    private fun metadataSyncsafe(value: Int): ByteArray = byteArrayOf(
        ((value ushr 21) and 127).toByte(), ((value ushr 14) and 127).toByte(),
        ((value ushr 7) and 127).toByte(), (value and 127).toByte(),
    )

    private fun metadataFrame(version: Int, name: String, value: ByteArray, features: Int = 0): ByteArray =
        name.toByteArray(Charsets.US_ASCII) + (if (version == 4) metadataSyncsafe(value.size) else metadataNumber(value.size)) +
            byteArrayOf(0, features.toByte()) + value

    private fun metadataId3(version: Int, frames: List<ByteArray>, flags: Int = 0): ByteArray {
        val payload = ByteArrayOutputStream().apply { frames.forEach { write(it) } }.toByteArray()
        return "ID3".toByteArray() + byteArrayOf(version.toByte(), 0, flags.toByte()) + metadataSyncsafe(payload.size) + payload
    }

    private fun metadataApic(image: ByteArray, mime: String = "image/png", type: Int = 3): ByteArray =
        byteArrayOf(0) + mime.toByteArray(Charsets.ISO_8859_1) + byteArrayOf(0, type.toByte(), 0) + image

    private fun metadataImage(format: String, width: Int, height: Int): ByteArray {
        val image = BufferedImage(width, height, BufferedImage.TYPE_INT_RGB)
        val output = ByteArrayOutputStream()
        val writer = ImageIO.getImageWritersByFormatName(format).next()
        try {
            MemoryCacheImageOutputStream(output).use { stream ->
                writer.output = stream
                writer.write(null, IIOImage(image, null, null), writer.defaultWriteParam)
            }
            return output.toByteArray()
        } finally { writer.dispose(); image.flush() }
    }

    private fun metadataComments(fields: List<String>): ByteArray = ByteArrayOutputStream().apply {
        write(metadataNumber(0, true))
        write(metadataNumber(fields.size, true))
        fields.forEach { field ->
            val bytes = field.toByteArray(Charsets.UTF_8)
            write(metadataNumber(bytes.size, true)); write(bytes)
        }
    }.toByteArray()

    private fun metadataPicture(image: ByteArray, width: Int, height: Int): ByteArray =
        metadataNumber(3) + metadataNumber(9) + "image/png".toByteArray() + metadataNumber(0) +
            metadataNumber(width) + metadataNumber(height) + metadataNumber(24) + metadataNumber(0) +
            metadataNumber(image.size) + image

    private fun metadataFlac(blocks: List<Pair<Int, ByteArray>>): ByteArray = ByteArrayOutputStream().apply {
        write("fLaC".toByteArray())
        (listOf(0 to ByteArray(34)) + blocks).forEachIndexed { index, (type, payload) ->
            write(type or if (index == blocks.size) 128 else 0)
            write(byteArrayOf((payload.size ushr 16).toByte(), (payload.size ushr 8).toByte(), payload.size.toByte()))
            write(payload)
        }
    }.toByteArray()

    private suspend fun pagedHistoryRestartAndReadOnlyTraversal(): Unit {
        Fixture().useStore { fixture ->
            val emptySnapshot = fixture.store.state.value
            assertEquals(DesktopHistoryPage(emptyList(), null), fixture.store.loadHistoryPage())
            assertEquals(emptySnapshot, fixture.store.state.value)
            val track = fixture.store.addImportedTrack(fixture.track("history-track", "History page fixture"))
            // Ties span both page boundaries; insertion order differs from keyset order.
            val expected = (0 until 237).map { index ->
                DesktopListeningEvent(
                    "history-${index.toString().padStart(3, '0')}", "history-entry-$index", track.id,
                    if (index == 236) 0 else 10_000L - (index / 113) * 5,
                    9_007_199_254_740_993L + index, 9_007_199_254_741_993L + index, index % 3 != 0,
                    listOf("INCLUDED", "EXCLUDED", "FUTURE_SESSION_\u2603")[index % 3],
                    listOf("EXCLUDED", "FUTURE_LISTEN_\uD83C\uDFB5", "INCLUDED")[index % 3],
                )
            }
            val current = expected.first()
            fixture.store.saveQueue(DesktopQueue("history-queue", listOf(DesktopQueueEntry(current.queueEntryId, track.id)),
                current.queueEntryId, positionMs = current.positionMs, sessionId = current.id,
                sessionStartedAtMs = current.startedAtMs, playedMs = current.playedMs,
                sessionTasteState = current.sessionTasteState, listenTasteState = current.listenTasteState), current)
            fixture.insertHistory(expected.drop(1).reversed())
            val stalePreview = fixture.store.state.value
            val persisted = fixture.allRows()
            val facts = fixture.facts()
            val first = fixture.store.loadHistoryPage()
            assertEquals(expected.take(100), first.events)
            assertEquals(DesktopHistoryCursor(expected[99].startedAtMs, expected[99].id), first.nextCursor)
            // A history read must not refresh even an intentionally stale preview.
            assertEquals(stalePreview, fixture.store.state.value)
            val second = fixture.store.loadHistoryPage(first.nextCursor, 100)
            assertEquals(expected.subList(100, 200), second.events)
            assertEquals(DesktopHistoryCursor(expected[199].startedAtMs, expected[199].id), second.nextCursor)
            val third = fixture.store.loadHistoryPage(second.nextCursor, 100)
            assertEquals(expected.drop(200), third.events)
            assertNull(third.nextCursor)
            val traversed = first.events + second.events + third.events
            assertEquals(expected, traversed)
            assertEquals(expected.size, traversed.map { it.id }.distinct().size)
            val one = fixture.store.loadHistoryPage(limit = 1)
            assertEquals(listOf(expected.first()), one.events)
            assertEquals(DesktopHistoryCursor(current.startedAtMs, current.id), one.nextCursor)
            assertEquals(listOf(expected[1]), fixture.store.loadHistoryPage(one.nextCursor, 1).events)
            val lastCursor = DesktopHistoryCursor(expected.last().startedAtMs, expected.last().id)
            assertEquals(DesktopHistoryPage(emptyList(), null), fixture.store.loadHistoryPage(lastCursor))
            val penultimate = expected[235]
            assertEquals(DesktopHistoryPage(listOf(expected.last()), null), fixture.store.loadHistoryPage(
                DesktopHistoryCursor(penultimate.startedAtMs, penultimate.id), 1))
            assertEquals(DesktopHistoryPage(expected.takeLast(100), null), fixture.store.loadHistoryPage(
                DesktopHistoryCursor(expected[136].startedAtMs, expected[136].id), 100))
            // Nonexistent keys are valid positions; the row itself need not exist.
            assertEquals(DesktopHistoryPage(emptyList(), null), fixture.store.loadHistoryPage(DesktopHistoryCursor(0, "z")))
            assertEquals(first, fixture.store.loadHistoryPage(DesktopHistoryCursor(Long.MAX_VALUE, "x".repeat(128))))
            listOf(Int.MIN_VALUE, -1, 0, 101, Int.MAX_VALUE).forEach { limit ->
                assertEquals("desktop_history_limit_invalid", failureCode { fixture.store.loadHistoryPage(limit = limit) })
            }
            listOf(DesktopHistoryCursor(-1, "valid-id"), DesktopHistoryCursor(Long.MIN_VALUE, "valid-id"),
                DesktopHistoryCursor(0, ""), DesktopHistoryCursor(0, " "), DesktopHistoryCursor(0, "bad/id"),
                DesktopHistoryCursor(0, "bad\u0000id"), DesktopHistoryCursor(0, "x".repeat(129)),
                DesktopHistoryCursor(0, "bad\uD83C\uDFB5")).forEach { cursor ->
                assertEquals("desktop_history_cursor_invalid", failureCode { fixture.store.loadHistoryPage(cursor) })
            }
            assertEquals(stalePreview, fixture.store.state.value)
            assertEquals(persisted, fixture.allRows())
            assertEquals(facts, fixture.facts())

            // Deleting the prior page's final row does not invalidate its positional cursor.
            fixture.execute("DELETE FROM desktop_listening WHERE id = '${expected[99].id}'")
            val afterDelete = fixture.allRows()
            assertEquals(second, fixture.store.loadHistoryPage(first.nextCursor))
            assertEquals(afterDelete, fixture.allRows())
            assertEquals(stalePreview, fixture.store.state.value)
            fixture.insertHistory(listOf(expected[99]))
            assertEquals(persisted, fixture.allRows())
            fixture.reopen()
            assertEquals(5L, fixture.number("PRAGMA user_version"))
            assertEquals(DesktopHistoryLimits.PREVIEW_SIZE, fixture.store.state.value.history.size)
            assertEquals(expected.take(DesktopHistoryLimits.PREVIEW_SIZE), fixture.store.state.value.history)
            assertEquals(237L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))
            assertEquals(first, fixture.store.loadHistoryPage())
            assertEquals(second, fixture.store.loadHistoryPage(first.nextCursor))
            assertEquals(third, fixture.store.loadHistoryPage(second.nextCursor))
            assertEquals(stalePreview.queue, fixture.store.state.value.queue)
            assertEquals(persisted, fixture.allRows())
            assertEquals(facts, fixture.facts())
        }
    }

    private suspend fun historyReadFailureAndCancellation(): Unit {
        Fixture().useStore { fixture ->
            val track = fixture.store.addImportedTrack(fixture.track("history-failure-track", "History read fixture"))
            val event = DesktopListeningEvent("history-failure-event", "history-failure-entry", track.id, 1, 2, 3, true,
                "FUTURE_SESSION", "EXCLUDED")
            fixture.insertHistory(listOf(event))
            val published = fixture.store.state.value
            val persisted = fixture.allRows()
            val facts = fixture.facts()
            val cancelled = Job()
            var propagated = false
            try {
                withContext(cancelled) {
                    cancelled.cancel()
                    fixture.store.loadHistoryPage()
                }
            } catch (failure: CancellationException) { propagated = true }
            assertTrue(propagated)
            assertEquals(published, fixture.store.state.value)
            assertEquals(persisted, fixture.allRows())
            assertEquals(DesktopHistoryPage(listOf(event), null), fixture.store.loadHistoryPage())

            // Only this disposable fixture temporarily withholds the read table.
            fixture.execute("ALTER TABLE desktop_listening RENAME TO acceptance_retained_history")
            try {
                val unavailable = fixture.allRows()
                val failure = try { fixture.store.loadHistoryPage(); null } catch (error: DesktopStorageException) { error }
                assertEquals("desktop_history_read_failed", failure?.code)
                assertEquals("desktop_history_read_failed", failure?.message)
                assertNull(failure?.cause)
                assertTrue(checkNotNull(failure).suppressed.isEmpty())
                assertEquals(published, fixture.store.state.value)
                assertEquals(unavailable, fixture.allRows())
                assertEquals(facts, fixture.facts())
            } finally {
                fixture.execute("ALTER TABLE acceptance_retained_history RENAME TO desktop_listening")
            }
            // A plain page failure has no sticky latch and needs no reopen or mutation to recover.
            assertEquals(DesktopHistoryPage(listOf(event), null), fixture.store.loadHistoryPage())
            assertEquals(published, fixture.store.state.value)
            assertEquals(persisted, fixture.allRows())
            assertEquals(facts, fixture.facts())
            fixture.store.updateTrackTextMetadata(track.id, "Writes remain available", "")
            assertEquals("Writes remain available", fixture.store.state.value.tracks.single().title)
            assertEquals(DesktopHistoryPage(listOf(event), null), fixture.store.loadHistoryPage())
        }
    }

    private suspend fun schemaThreeTasteMigration(): Unit {
        Fixture().useStore(legacyBytes = pcmWave(), legacyVersion = 3) { fixture ->
            val schema = checkNotNull(fixture.legacySchema)
            val schemaBytes = checkNotNull(fixture.legacySchemaBytes)
            assertEquals(5L, fixture.number("PRAGMA user_version"))
            assertEquals(fixture.legacyRows, fixture.rows(schema))
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))
            val migrated = fixture.store.state.value
            assertEquals("FUTURE_SCHEMA2_PREFERENCE", migrated.tracks.single().preference)
            assertEquals("FUTURE_SCHEMA3_COLLECTION", migrated.tracks.single().collectionState)
            assertEquals("INCLUDED", migrated.queue?.sessionTasteState)
            assertEquals("INCLUDED", migrated.queue?.listenTasteState)
            assertEquals(listOf("INCLUDED"), migrated.history.map { it.sessionTasteState })
            assertEquals(listOf("INCLUDED"), migrated.history.map { it.listenTasteState })
            listOf("desktop_queue", "desktop_listening").forEach { table ->
                assertEquals(2L, fixture.number("SELECT COUNT(*) FROM pragma_table_info('$table') " +
                    "WHERE name IN ('sessionTasteState', 'listenTasteState') AND type = 'TEXT' " +
                    "AND [notnull] = 1 AND dflt_value = '''INCLUDED'''"))
            }
            val facts = fixture.facts()
            assertEquals(1, facts.size)
            // Both commands are no-ops even with unknown modes and retained removed/future membership.
            assertEquals(migrated.queue, fixture.store.setQueueSessionTasteExcluded("legacy-queue", false))
            assertEquals(migrated.queue, fixture.store.setCurrentListenTasteExcluded("legacy-queue", "legacy-entry-b", "legacy-session", false))
            fixture.reopen()
            assertEquals(migrated, fixture.store.state.value)
            assertEquals(facts, fixture.facts())
            assertEquals(fixture.legacyRows, fixture.rows(schema))
            assertArrayEquals(pcmWave(), Files.readAllBytes(fixture.root.resolve("media/${migrated.tracks.single().sha256}.media")))
            assertEquals("FUTURE_SCHEMA2_PREFERENCE", fixture.text("SELECT preference FROM desktop_track_preference"))
            assertEquals("FUTURE_SCHEMA3_COLLECTION", fixture.text("SELECT collectionState FROM desktop_track_collection"))
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))
        }
    }

    private suspend fun tasteRestartAndPolicies(): Unit {
        Fixture().useStore { fixture ->
            assertNull(fixture.store.setQueueSessionTasteExcluded("retired", true))
            assertNull(fixture.store.setCurrentListenTasteExcluded("retired", "entry", "listen", true))
            assertTrue(fixture.facts().isEmpty())
            val imported = fixture.track("taste-track", "Taste track")
            fixture.store.addImportedTrack(imported)
            val queue = DesktopQueue("taste-queue", listOf(DesktopQueueEntry("taste-entry", imported.id),
                DesktopQueueEntry("taste-next", imported.id)), "taste-entry", positionMs = 1_234,
                shuffleEnabled = true, shuffleOrder = listOf("taste-next", "taste-entry"), repeatMode = "ALL",
                sessionId = "taste-listen", sessionStartedAtMs = 100, playedMs = 900)
            assertEquals("INCLUDED", queue.sessionTasteState)
            assertEquals("INCLUDED", queue.listenTasteState)
            // Interrupted admission: persisted session exists, checkpoint row does not.
            fixture.store.saveQueue(queue)
            val noEventFacts = fixture.facts()
            assertEquals(queue, fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "taste-listen", false))
            assertEquals(queue, fixture.store.setQueueSessionTasteExcluded(queue.id, false))
            assertNull(fixture.store.setQueueSessionTasteExcluded("retired", true))
            assertNull(fixture.store.setCurrentListenTasteExcluded("retired", "taste-entry", "taste-listen", true))
            assertNull(fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-next", "taste-listen", true))
            assertNull(fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "retired-listen", true))
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))
            assertEquals(noEventFacts, fixture.facts())
            val listenExcluded = queue.copy(listenTasteState = "EXCLUDED")
            assertEquals(listenExcluded, fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "taste-listen", true))
            val event = DesktopListeningEvent("taste-listen", "taste-entry", imported.id, 100, 900, 1_234, false,
                "INCLUDED", "EXCLUDED")
            assertEquals(listOf(event), fixture.store.state.value.history)
            val firstFacts = fixture.facts()
            assertEquals(noEventFacts.size + 2, firstFacts.size)
            assertTasteFact((firstFacts - noEventFacts.keys).values.single { it.contains("CURRENT_LISTEN_TASTE_UPDATED") },
                "CURRENT_LISTEN_TASTE_UPDATED", "CURRENT_LISTEN", queue, listenExcluded)
            val firstCheckpoint = (firstFacts - noEventFacts.keys).values.single { it.contains("LISTENING_CHECKPOINT_SAVED") }
            val checkpoint = Json.parseToJsonElement(firstCheckpoint).jsonObject.getValue("snapshot").jsonObject
            assertEquals("taste-listen", checkpoint.getValue("event_id").jsonPrimitive.content)
            assertEquals("EXCLUDED", checkpoint.getValue("listen_taste_state").jsonPrimitive.content)
            assertEquals("900", checkpoint.getValue("played_ms").jsonPrimitive.content)
            fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "taste-listen", true)
            assertEquals(firstFacts, fixture.facts())

            val both = listenExcluded.copy(sessionTasteState = "EXCLUDED")
            assertEquals(both, fixture.store.setQueueSessionTasteExcluded(queue.id, true))
            assertEquals(listOf(event.copy(sessionTasteState = "EXCLUDED")), fixture.store.state.value.history)
            val beforeStaleCheckpoint = fixture.store.state.value
            val beforeStaleCheckpointFacts = fixture.facts()
            assertEquals("desktop_taste_checkpoint_conflict", failureCode { fixture.store.saveQueue(queue) })
            assertEquals("desktop_listening_conflict", failureCode { fixture.store.saveQueue(both, event) })
            assertEquals(beforeStaleCheckpoint, fixture.store.state.value)
            assertEquals(beforeStaleCheckpointFacts, fixture.facts())
            val bothFacts = fixture.facts()
            assertEquals(firstFacts.size + 2, bothFacts.size)
            assertTasteFact((bothFacts - firstFacts.keys).values.single { it.contains("QUEUE_SESSION_TASTE_UPDATED") },
                "QUEUE_SESSION_TASTE_UPDATED", "QUEUE_SESSION", listenExcluded, both)
            val sessionOnly = both.copy(listenTasteState = "INCLUDED")
            assertEquals(sessionOnly, fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "taste-listen", false))
            val sessionEvent = event.copy(sessionTasteState = "EXCLUDED", listenTasteState = "INCLUDED")
            assertEquals(listOf(sessionEvent), fixture.store.state.value.history)
            assertEquals(true, DesktopTastePolicy.isExcluded(sessionEvent.sessionTasteState, sessionEvent.listenTasteState))
            fixture.reopen()
            assertEquals(sessionOnly, fixture.store.state.value.queue)
            assertEquals(listOf(sessionEvent), fixture.store.state.value.history)

            // A new logical listen under the same queue retains the queue flag and clears a
            // known per-listen choice. The prior event is finalized once using its own flags.
            val next = sessionOnly.copy(currentEntryId = "taste-next", sessionId = "taste-next-listen",
                sessionStartedAtMs = 200, positionMs = 0, playedMs = 0,
                listenTasteState = DesktopTastePolicy.forNewListen(sessionOnly.listenTasteState))
            val finalized = sessionEvent.copy(finalized = true, playedMs = 950, positionMs = 1_284)
            fixture.store.saveQueue(next, finalized)
            val nextEvent = DesktopListeningEvent("taste-next-listen", "taste-next", imported.id, 200, 0, 0, false,
                next.sessionTasteState, next.listenTasteState)
            fixture.store.saveQueue(next, nextEvent)
            fixture.store.setQueueSessionTasteExcluded(queue.id, false)
            assertEquals(finalized, fixture.store.state.value.history.single { it.id == finalized.id })
            assertEquals(nextEvent.copy(sessionTasteState = "INCLUDED"), fixture.store.state.value.history.single { it.id == nextEvent.id })
            val factsBeforeStale = fixture.facts()
            assertNull(fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-entry", "taste-listen", true))
            assertEquals(factsBeforeStale, fixture.facts())

            // Replacement admission/finalization/new checkpoint retain the old event's captured
            // flags even though the admitted candidate starts with new-queue defaults.
            val old = checkNotNull(fixture.store.setQueueSessionTasteExcluded(queue.id, true))
            val oldCheckpoint = fixture.store.state.value.history.single { it.id == nextEvent.id }
            val replacement = DesktopQueue("replacement-taste-queue", old.entries, "taste-entry",
                sessionId = "replacement-listen", sessionStartedAtMs = 300)
            fixture.store.saveQueue(replacement, oldCheckpoint)
            val stoppedOld = oldCheckpoint.copy(finalized = true, playedMs = 75, positionMs = 75)
            fixture.store.saveQueue(replacement, stoppedOld)
            val replacementEvent = DesktopListeningEvent("replacement-listen", "taste-entry", imported.id, 300, 0, 0, false)
            fixture.store.saveQueue(replacement, replacementEvent)
            assertEquals("INCLUDED", fixture.store.state.value.queue?.sessionTasteState)
            assertEquals(stoppedOld, fixture.store.state.value.history.single { it.id == stoppedOld.id })
            assertEquals(replacementEvent, fixture.store.state.value.history.single { it.id == replacementEvent.id })
            val oldFinalSnapshot = fixture.facts().values.map { Json.parseToJsonElement(it).jsonObject }
                .single { it.getValue("mutation").jsonPrimitive.content == "LISTENING_CHECKPOINT_SAVED" &&
                    it.getValue("snapshot").jsonObject.getValue("event_id").jsonPrimitive.content == stoppedOld.id &&
                    it.getValue("snapshot").jsonObject.getValue("finalized").jsonPrimitive.content == "true" }
                .getValue("snapshot").jsonObject
            assertEquals("EXCLUDED", oldFinalSnapshot.getValue("session_taste_state").jsonPrimitive.content)
            val replacementFacts = fixture.facts()
            fixture.store.saveQueue(replacement, replacementEvent.copy(finalized = true))
            val endedFacts = fixture.facts()
            val endedHistory = fixture.store.state.value.history
            // Any existing finalized active identity refuses the whole command, including no-op.
            assertNull(fixture.store.setQueueSessionTasteExcluded(replacement.id, false))
            assertNull(fixture.store.setQueueSessionTasteExcluded(replacement.id, true))
            assertNull(fixture.store.setCurrentListenTasteExcluded(replacement.id, "taste-entry", "replacement-listen", false))
            assertNull(fixture.store.setCurrentListenTasteExcluded(replacement.id, "taste-entry", "replacement-listen", true))
            assertEquals("desktop_listening_conflict", failureCode {
                fixture.store.saveQueue(replacement, replacementEvent.copy(finalized = true, listenTasteState = "EXCLUDED"))
            })
            assertEquals(endedFacts, fixture.facts())
            assertEquals(endedHistory, fixture.store.state.value.history)
            assertTrue(replacementFacts.keys.all { fixture.facts()[it] == replacementFacts[it] })
            val ended = replacement.copy(sessionId = null, sessionStartedAtMs = null)
            fixture.store.saveQueue(ended)
            val noListenFacts = fixture.facts()
            val endedExcluded = ended.copy(sessionTasteState = "EXCLUDED")
            assertEquals(endedExcluded, fixture.store.setQueueSessionTasteExcluded(ended.id, true))
            assertEquals(endedHistory, fixture.store.state.value.history)
            val endedChoice = fixture.facts() - noListenFacts.keys
            assertEquals(1, endedChoice.size)
            assertTasteFact(endedChoice.values.single(), "QUEUE_SESSION_TASTE_UPDATED", "QUEUE_SESSION", ended, endedExcluded)
            assertNull(fixture.store.setCurrentListenTasteExcluded(ended.id, "taste-entry", "replacement-listen", true))
            val empty = DesktopQueue("empty-taste-queue", emptyList(), null)
            fixture.store.saveQueue(empty)
            assertEquals(empty.copy(sessionTasteState = "EXCLUDED"), fixture.store.setQueueSessionTasteExcluded(empty.id, true))
            assertEquals(endedHistory, fixture.store.state.value.history)
            val interrupted = queue.copy(id = "interrupted-taste-queue", sessionId = "interrupted-taste-listen")
            fixture.store.saveQueue(interrupted)
            val interruptedFacts = fixture.facts()
            val recoveredQueue = interrupted.copy(sessionTasteState = "EXCLUDED")
            assertEquals(recoveredQueue, fixture.store.setQueueSessionTasteExcluded(interrupted.id, true))
            val recoveredEvent = DesktopListeningEvent("interrupted-taste-listen", "taste-entry", imported.id,
                100, 900, 1_234, false, "EXCLUDED", "INCLUDED")
            assertEquals(recoveredEvent, fixture.store.state.value.history.single { it.id == recoveredEvent.id })
            val recoveredFacts = fixture.facts()
            assertEquals(interruptedFacts.size + 2, recoveredFacts.size)
            assertTasteFact((recoveredFacts - interruptedFacts.keys).values.single { it.contains("QUEUE_SESSION_TASTE_UPDATED") },
                "QUEUE_SESSION_TASTE_UPDATED", "QUEUE_SESSION", interrupted, recoveredQueue)
            assertEquals(recoveredQueue, fixture.store.setQueueSessionTasteExcluded(interrupted.id, true))
            assertEquals(recoveredFacts, fixture.facts())
            fixture.reopen()
            assertEquals(recoveredQueue, fixture.store.state.value.queue)
            assertEquals(recoveredEvent, fixture.store.state.value.history.single { it.id == recoveredEvent.id })
            noEventFacts.forEach { (id, bytes) -> assertArrayEquals(bytes.encodeToByteArray(), fixture.facts().getValue(id).encodeToByteArray()) }
            assertArrayEquals(byteArrayOf(1, 2, 3, 4), Files.readAllBytes(fixture.root.resolve("media/${imported.managedFileName}")))
            tasteUnknownAndContextRefusals(fixture, queue, event)
        }
    }

    private suspend fun tasteUnknownAndContextRefusals(fixture: Fixture, queue: DesktopQueue, event: DesktopListeningEvent): Unit {
        val futureSession = "Future session '\u2603"
        val futureListen = "Future listen \uD83C\uDFB5"
        val opaque = queue.copy(sessionId = "opaque-taste-listen", sessionStartedAtMs = 400,
            sessionTasteState = futureSession, listenTasteState = futureListen)
        val opaqueEvent = event.copy(id = "opaque-taste-listen", startedAtMs = 400,
            playedMs = 910, positionMs = 1_240,
            sessionTasteState = futureSession, listenTasteState = futureListen)
        fixture.store.saveQueue(opaque, opaqueEvent)
        assertNull(DesktopTastePolicy.isExcluded(futureSession, futureListen))
        assertEquals(true, DesktopTastePolicy.isExcluded("EXCLUDED", futureListen))
        assertEquals(true, DesktopTastePolicy.isExcluded(futureSession, "EXCLUDED"))
        assertEquals(false, DesktopTastePolicy.isExcluded("INCLUDED", "INCLUDED"))
        assertNull(DesktopTastePolicy.isExcluded("included", "INCLUDED"))
        assertEquals(futureListen, DesktopTastePolicy.forNewListen(futureListen))
        assertEquals("INCLUDED", DesktopTastePolicy.forNewListen("EXCLUDED"))
        fixture.store.updateTrackTextMetadata("taste-track", "Changed labels", "")
        fixture.store.updateTrackDuration("taste-track", 4_000)
        fixture.store.setTrackPreference("taste-track", "DISLIKED")
        fixture.store.removeTrackFromCollection("taste-track")
        fixture.store.restoreTrackToCollection("taste-track")
        fixture.store.addImportedTrack(fixture.track("duplicate-taste", "Ignored labels"))
        fixture.store.saveQueue(opaque, opaqueEvent)
        fixture.reopen()
        assertEquals(opaque, fixture.store.state.value.queue)
        assertEquals(opaqueEvent, fixture.store.state.value.history.single { it.id == opaqueEvent.id })
        val priorFacts = fixture.facts()
        val clearedListen = opaque.copy(listenTasteState = "INCLUDED")
        assertEquals(clearedListen, fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, false))
        assertEquals(opaqueEvent.copy(listenTasteState = "INCLUDED"), fixture.store.state.value.history.single { it.id == opaqueEvent.id })
        assertTasteFact((fixture.facts() - priorFacts.keys).values.single { it.contains("CURRENT_LISTEN_TASTE_UPDATED") },
            "CURRENT_LISTEN_TASTE_UPDATED", "CURRENT_LISTEN", opaque, clearedListen)
        val beforeQueueChoice = fixture.facts()
        val included = clearedListen.copy(sessionTasteState = "INCLUDED")
        assertEquals(included, fixture.store.setQueueSessionTasteExcluded(opaque.id, false))
        assertTasteFact((fixture.facts() - beforeQueueChoice.keys).values.single { it.contains("QUEUE_SESSION_TASTE_UPDATED") },
            "QUEUE_SESSION_TASTE_UPDATED", "QUEUE_SESSION", clearedListen, included)
        val baselineFacts = fixture.facts()
        // The published projection still points at the old listen. Transactional admission must
        // inspect the actual selected row rather than trust that in-memory snapshot.
        fixture.execute("UPDATE desktop_queue SET currentEntryId = 'taste-next', sessionId = 'externally-retired-listen'")
        assertEquals(included, fixture.store.state.value.queue)
        assertNull(fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, true))
        assertEquals("externally-retired-listen", fixture.text("SELECT sessionId FROM desktop_queue"))
        assertEquals(baselineFacts, fixture.facts())
        fixture.execute("UPDATE desktop_queue SET currentEntryId = 'taste-entry', sessionId = '${opaqueEvent.id}'")
        fixture.reopen()
        val oversized = "x".repeat(262_145)
        // Bound both a selected future before-value and the unchanged other future flag.
        for ((session, listen) in listOf(oversized to "INCLUDED", "INCLUDED" to oversized)) {
            fixture.setCurrentTasteFlags(session, listen)
            fixture.reopen()
            val retained = fixture.store.state.value
            assertEquals("desktop_outbox_payload_too_large", failureCode { fixture.store.setQueueSessionTasteExcluded(opaque.id, true) })
            assertEquals("desktop_outbox_payload_too_large", failureCode {
                fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, true)
            })
            assertEquals(retained, fixture.store.state.value)
            assertEquals(baselineFacts, fixture.facts())
            assertEquals(session, fixture.text("SELECT sessionTasteState FROM desktop_queue"))
            assertEquals(listen, fixture.text("SELECT listenTasteState FROM desktop_listening WHERE id = '${opaqueEvent.id}'"))
            // A known no-op retains the other oversized future value without encoding a fact.
            if (session == "INCLUDED") assertEquals(retained.queue, fixture.store.setQueueSessionTasteExcluded(opaque.id, false))
            if (listen == "INCLUDED") assertEquals(retained.queue,
                fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, false))
            assertEquals(baselineFacts, fixture.facts())
        }
        fixture.setCurrentTasteFlags("INCLUDED", "INCLUDED")
        fixture.reopen()
        // Unicode-invalid before-images are refused by the typed writer before DB mutation;
        // no claim is made that SQLite's string codec round-trips lone UTF-16 surrogates.
        listOf("Invalid\uD800", "Invalid\uDC00").forEach { invalid ->
            val before = QueueRow(id = opaque.id, currentEntryId = "taste-entry", queueType = "LIBRARY",
                positionMs = 1_234, shuffleEnabled = false, repeatMode = "OFF", sessionId = opaqueEvent.id,
                sessionStartedAtMs = 400, playedMs = 900, listenTasteState = invalid)
            assertEquals("desktop_outbox_payload_invalid", failureCode {
                DesktopLocalIntent.currentListenTasteUpdated(before, before.copy(listenTasteState = "INCLUDED")); Unit
            })
            assertEquals(invalid, before.listenTasteState)
            assertEquals("desktop_outbox_payload_invalid", failureCode {
                DesktopLocalIntent.queueSessionTasteUpdated(before, before.copy(sessionTasteState = "EXCLUDED")); Unit
            })
            val invalidSession = before.copy(sessionTasteState = invalid, listenTasteState = "INCLUDED")
            assertEquals("desktop_outbox_payload_invalid", failureCode {
                DesktopLocalIntent.queueSessionTasteUpdated(invalidSession, invalidSession.copy(sessionTasteState = "INCLUDED")); Unit
            })
            assertEquals(invalid, invalidSession.sessionTasteState)
            assertEquals(baselineFacts, fixture.facts())
        }
        // Conflicting attribution and malformed active context refuse all row/fact changes.
        fixture.execute("UPDATE desktop_listening SET queueEntryId = 'taste-next' WHERE id = '${opaqueEvent.id}'")
        fixture.reopen()
        val conflict = fixture.store.state.value
        assertEquals("desktop_listening_conflict", failureCode { fixture.store.setQueueSessionTasteExcluded(opaque.id, true) })
        assertEquals("desktop_listening_conflict", failureCode {
            fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, true)
        })
        assertEquals(conflict, fixture.store.state.value)
        assertEquals(baselineFacts, fixture.facts())
        fixture.execute("UPDATE desktop_listening SET queueEntryId = 'taste-entry' WHERE id = '${opaqueEvent.id}'")
        fixture.execute("UPDATE desktop_queue SET sessionStartedAtMs = NULL")
        fixture.reopen()
        val malformed = fixture.store.state.value
        assertEquals("desktop_taste_context_invalid", failureCode { fixture.store.setQueueSessionTasteExcluded(opaque.id, true) })
        assertEquals("desktop_taste_context_invalid", failureCode {
            fixture.store.setCurrentListenTasteExcluded(opaque.id, "taste-entry", opaqueEvent.id, true)
        })
        assertEquals(malformed, fixture.store.state.value)
        assertEquals(baselineFacts, fixture.facts())
        fixture.execute("UPDATE desktop_queue SET sessionStartedAtMs = 400")
        fixture.reopen()
        val expected = fixture.store.state.value
        val facts = fixture.facts()
        fixture.reopen()
        assertEquals(expected, fixture.store.state.value)
        assertEquals(facts, fixture.facts())
    }

    private suspend fun tasteRollbackAndStickyLatch(): Unit {
        Fixture().useStore { fixture ->
            val track = fixture.store.addImportedTrack(fixture.track("taste-recovery", "Taste recovery"))
            val queue = DesktopQueue("taste-recovery-queue", listOf(DesktopQueueEntry("taste-recovery-entry", track.id)),
                "taste-recovery-entry", sessionId = "taste-recovery-listen", sessionStartedAtMs = 100,
                positionMs = 150, playedMs = 90, sessionTasteState = "FUTURE_RECOVERY", listenTasteState = "FUTURE_LISTEN")
            val event = DesktopListeningEvent("taste-recovery-listen", "taste-recovery-entry", track.id, 100, 90, 150, false,
                queue.sessionTasteState, queue.listenTasteState)
            fixture.store.saveQueue(queue, event)
            val baseline = fixture.store.state.value
            val facts = fixture.facts()
            // Failing the taste fact happens after the annotation and checkpoint fact writes:
            // both rows AND the already inserted checkpoint must roll back together.
            fixture.execute("CREATE TRIGGER acceptance_abort_taste_fact BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind IN ('CURRENT_LISTEN_TASTE_UPDATED', 'QUEUE_SESSION_TASTE_UPDATED') " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-recovery-entry", event.id, true)
                })
                assertEquals("desktop_mutation_failed", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, true) })
                assertEquals(baseline, fixture.store.state.value)
                assertEquals(facts, fixture.facts())
                fixture.reopen()
                assertEquals(baseline, fixture.store.state.value)
                assertEquals(facts, fixture.facts())
                // The same rollback applies to synthesizing an absent persisted session event.
                fixture.execute("DELETE FROM desktop_listening WHERE id = '${event.id}'")
                fixture.reopen()
                val missing = fixture.store.state.value
                assertEquals("desktop_mutation_failed", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, true) })
                assertEquals("desktop_mutation_failed", failureCode {
                    fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-recovery-entry", event.id, true)
                })
                assertEquals(missing, fixture.store.state.value)
                assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))
                assertEquals(facts, fixture.facts())
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_taste_fact") }
            fixture.store.saveQueue(queue, event)
            val beforeCommit = fixture.store.state.value
            val beforeCommitFacts = fixture.facts()
            fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_taste_retained_playlist")
            try {
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, true) })
                assertEquals(beforeCommit, fixture.store.state.value)
                assertEquals("EXCLUDED", fixture.text("SELECT sessionTasteState FROM desktop_queue"))
                assertEquals("EXCLUDED", fixture.text("SELECT sessionTasteState FROM desktop_listening WHERE id = '${event.id}'"))
                assertEquals(beforeCommitFacts.size + 2, fixture.facts().size)
                assertEquals("desktop_snapshot_unavailable", failureCode {
                    fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-recovery-entry", event.id, true)
                })
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, false) })
            } finally { fixture.execute("ALTER TABLE acceptance_taste_retained_playlist RENAME TO desktop_playlist") }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, false) })
            fixture.reopen()
            val committed = beforeCommit.copy(queue = queue.copy(sessionTasteState = "EXCLUDED"),
                history = listOf(event.copy(sessionTasteState = "EXCLUDED")))
            assertEquals(committed, fixture.store.state.value)
            val committedFacts = fixture.facts()
            beforeCommitFacts.forEach { (id, bytes) -> assertArrayEquals(bytes.encodeToByteArray(), committedFacts.getValue(id).encodeToByteArray()) }
            fixture.execute("CREATE TRIGGER acceptance_abort_taste_fact BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'CURRENT_LISTEN_TASTE_UPDATED' BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_taste_retained_playlist")
                try {
                    val failure = try {
                        fixture.store.setCurrentListenTasteExcluded(queue.id, "taste-recovery-entry", event.id, true); null
                    } catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_mutation_failed", failure?.code)
                    val marker = checkNotNull(failure).suppressed.single() as DesktopStorageException
                    assertEquals("desktop_snapshot_unavailable", marker.code)
                    assertNull(marker.cause)
                    assertTrue(marker.suppressed.isEmpty())
                    assertEquals(committed, fixture.store.state.value)
                    assertEquals(committedFacts, fixture.facts())
                    assertEquals(event.listenTasteState, fixture.text("SELECT listenTasteState FROM desktop_queue"))
                    assertEquals(event.listenTasteState, fixture.text("SELECT listenTasteState FROM desktop_listening WHERE id = '${event.id}'"))
                } finally { fixture.execute("ALTER TABLE acceptance_taste_retained_playlist RENAME TO desktop_playlist") }
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_taste_fact") }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setQueueSessionTasteExcluded(queue.id, false) })
            fixture.reopen()
            assertEquals(committed, fixture.store.state.value)
            assertEquals(committedFacts, fixture.facts())
        }
    }

    private fun assertTasteFact(payload: String, kind: String, scope: String, before: DesktopQueue, after: DesktopQueue): Unit {
        val document = Json.parseToJsonElement(payload).jsonObject
        assertEquals("1", document.getValue("version").jsonPrimitive.content)
        assertEquals(kind, document.getValue("mutation").jsonPrimitive.content)
        val snapshot = document.getValue("snapshot").jsonObject
        assertEquals(setOf("scope", "queue_id", "current_entry_id", "listening_event_id",
            "previous_session_taste_state", "previous_listen_taste_state", "session_taste_state", "listen_taste_state"), snapshot.keys)
        assertEquals(scope, snapshot.getValue("scope").jsonPrimitive.content)
        assertEquals(after.id, snapshot.getValue("queue_id").jsonPrimitive.content)
        assertEquals(after.currentEntryId, snapshot.getValue("current_entry_id").jsonPrimitive.contentOrNull)
        assertEquals(after.sessionId, snapshot.getValue("listening_event_id").jsonPrimitive.contentOrNull)
        assertEquals(before.sessionTasteState, snapshot.getValue("previous_session_taste_state").jsonPrimitive.content)
        assertEquals(before.listenTasteState, snapshot.getValue("previous_listen_taste_state").jsonPrimitive.content)
        assertEquals(after.sessionTasteState, snapshot.getValue("session_taste_state").jsonPrimitive.content)
        assertEquals(after.listenTasteState, snapshot.getValue("listen_taste_state").jsonPrimitive.content)
    }

    private suspend fun schemaTwoCollectionMigration(): Unit {
        Fixture().useStore(legacyBytes = pcmWave(), legacyVersion = 2) { fixture ->
            val schema = checkNotNull(fixture.legacySchema)
            val schemaBytes = checkNotNull(fixture.legacySchemaBytes)
            assertEquals(5L, fixture.number("PRAGMA user_version"))
            assertEquals(fixture.legacyRows, fixture.rows(schema))
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))
            assertEquals("FUTURE_SCHEMA2_PREFERENCE", fixture.store.state.value.tracks.single().preference)
            assertEquals("FUTURE_SCHEMA2_PREFERENCE", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'legacy-a'"))
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            assertEquals("ACTIVE", fixture.store.state.value.tracks.single().collectionState)
            val media = fixture.root.resolve("media/${fixture.store.state.value.tracks.single().sha256}.media")
            assertArrayEquals(pcmWave(), Files.readAllBytes(media))
            val migrated = fixture.store.state.value
            val facts = fixture.facts()
            fixture.store.restoreTrackToCollection("legacy-a")
            fixture.reopen()
            assertEquals(migrated, fixture.store.state.value)
            assertEquals(facts, fixture.facts())
            assertEquals(fixture.legacyRows, fixture.rows(schema))
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            fixture.store.removeTrackFromCollection("legacy-a")
            assertEquals(migrated.copy(tracks = migrated.tracks.map { it.copy(collectionState = "REMOVED") }), fixture.store.state.value)
            val removedFacts = fixture.facts()
            fixture.reopen()
            assertEquals("REMOVED", fixture.store.state.value.tracks.single().collectionState)
            assertEquals("FUTURE_SCHEMA2_PREFERENCE", fixture.store.state.value.tracks.single().preference)
            assertEquals(removedFacts, fixture.facts())
            // Old domain tables remain lossless; the one new immutable membership fact is additive.
            assertEquals(fixture.legacyRows - "desktop_local_outbox", fixture.rows(schema) - "desktop_local_outbox")
            facts.forEach { (id, payload) -> assertEquals(payload, removedFacts[id]) }
            assertArrayEquals(pcmWave(), Files.readAllBytes(media))
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))
        }
    }

    private suspend fun collectionRestartAndAdmission(): Unit {
        Fixture().useStore { fixture ->
            val imported = fixture.track("member-a", "Collection track", pcmWave())
            val fileName = "${imported.sha256}.wav"
            val media = fixture.root.resolve("media/$fileName")
            Files.move(fixture.root.resolve("media/${imported.managedFileName}"), media)
            val waveImport = imported.copy(managedFileName = fileName)
            val track = fixture.store.addImportedTrack(waveImport)
            val other = fixture.store.addImportedTrack(fixture.track("member-b", "Other collection track", byteArrayOf(6, 7, 8)))
            fixture.store.setTrackPreference(track.id, "LIKED")
            val playlist = fixture.store.createPlaylist("Retained references")
            repeat(2) { fixture.store.appendToPlaylist(playlist.id, track.id) }
            fixture.store.appendToPlaylist(playlist.id, other.id)
            val queue = DesktopQueue("membership-queue", listOf(DesktopQueueEntry("member-entry-a", track.id),
                DesktopQueueEntry("member-entry-b", track.id), DesktopQueueEntry("member-entry-c", other.id)),
                "member-entry-b", positionMs = 90, shuffleEnabled = true,
                shuffleOrder = listOf("member-entry-b", "member-entry-c", "member-entry-a"), repeatMode = "ALL",
                sessionId = "membership-session", sessionStartedAtMs = 100, playedMs = 80)
            val event = DesktopListeningEvent("membership-session", "member-entry-b", track.id, 100, 80, 90, false)
            fixture.store.saveQueue(queue, event)
            val active = fixture.store.state.value
            val beforeFacts = fixture.facts()
            fixture.store.restoreTrackToCollection(track.id)
            fixture.store.restoreTrackToCollection(other.id)
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            assertEquals(beforeFacts, fixture.facts())
            listOf<suspend () -> Unit>(
                { fixture.store.removeTrackFromCollection("missing-track") },
                { fixture.store.restoreTrackToCollection("missing-track") },
            ).forEach { assertEquals("desktop_track_missing", failureCode(it)) }
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.removeTrackFromCollection("bad/id") })
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.restoreTrackToCollection("bad/id") })
            assertEquals(active, fixture.store.state.value)
            assertEquals(beforeFacts, fixture.facts())

            fixture.store.removeTrackFromCollection(track.id)
            val removed = active.copy(tracks = active.tracks.map { if (it.id == track.id) it.copy(collectionState = "REMOVED") else it })
            assertEquals(removed, fixture.store.state.value)
            val removalFacts = fixture.facts()
            assertEquals(beforeFacts.size + 1, removalFacts.size)
            assertCollectionFact((removalFacts - beforeFacts.keys).values.single(), "TRACK_REMOVED_FROM_COLLECTION",
                track.id, "ACTIVE", "REMOVED")
            assertEquals(track.id, fixture.text("SELECT aggregateId FROM desktop_local_outbox WHERE kind = 'TRACK_REMOVED_FROM_COLLECTION'"))
            fixture.store.removeTrackFromCollection(track.id)
            fixture.store.restoreTrackToCollection(other.id)
            assertEquals(1L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            assertEquals(removalFacts, fixture.facts())
            assertEquals(media.toString(), fixture.store.resolveManagedFile(track.id))
            assertEquals(removed.tracks.single { it.id == track.id },
                fixture.store.addImportedTrack(waveImport.copy(id = "duplicate-member", title = "Ignored duplicate")))
            assertEquals(removed, fixture.store.state.value)
            assertEquals(removalFacts, fixture.facts())
            fixture.reopen()
            assertEquals(removed, fixture.store.state.value)
            assertEquals(removalFacts, fixture.facts())
            assertArrayEquals(pcmWave(), Files.readAllBytes(media))
            assertEquals(fileName, fixture.text("SELECT fileName FROM desktop_managed_locator WHERE trackId = '${track.id}'"))
            assertEquals(3L, fixture.number("SELECT COUNT(*) FROM desktop_playlist_entry"))
            assertEquals(3L, fixture.number("SELECT COUNT(*) FROM desktop_queue_entry"))
            assertEquals(1L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))

            // Both a new ID and a reused ID with a different track are new queue admission.
            val extra = DesktopQueueEntry("member-new-entry", track.id)
            val remapped = queue.copy(entries = queue.entries.map { if (it.id == "member-entry-c") it.copy(trackId = track.id) else it })
            val rejectedQueues = listOf(queue.copy(entries = queue.entries + extra, shuffleOrder = queue.shuffleOrder + extra.id),
                remapped, queue.copy(id = "replacement-queue", entries = listOf(extra), currentEntryId = extra.id,
                    shuffleOrder = listOf(extra.id)))
            assertEquals("desktop_track_not_in_collection", failureCode { fixture.store.appendToPlaylist(playlist.id, track.id) })
            rejectedQueues.forEach { candidate ->
                assertEquals("desktop_track_not_in_collection", failureCode {
                    fixture.store.saveQueue(candidate, event.copy(playedMs = 81, positionMs = 91))
                })
                assertEquals(removed, fixture.store.state.value)
                assertEquals(removalFacts, fixture.facts())
            }
            val retained = queue.copy(entries = queue.entries.reversed(), currentEntryId = "member-entry-a",
                positionMs = 91, shuffleOrder = queue.shuffleOrder.reversed(), playedMs = 81)
            fixture.store.saveQueue(retained, event.copy(playedMs = 81, positionMs = 91))
            assertEquals(retained, fixture.store.state.value.queue)
            assertEquals("REMOVED", fixture.store.state.value.tracks.single { it.id == track.id }.collectionState)
            assertEquals(media.toString(), fixture.store.resolveManagedFile(track.id))
            fixture.store.appendToPlaylist(playlist.id, other.id) // ACTIVE remains admissible.

            val future = "FUTURE_MEMBERSHIP_\uD83C\uDFB5"
            fixture.execute("UPDATE desktop_track_collection SET collectionState = '$future' WHERE trackId = '${track.id}'")
            fixture.execute("UPDATE desktop_track_preference SET preference = 'FUTURE_MEMBER_PREFERENCE' WHERE trackId = '${track.id}'")
            fixture.reopen()
            val futureTrack = fixture.store.state.value.tracks.single { it.id == track.id }
            val futureFacts = fixture.facts()
            assertEquals(futureTrack, fixture.store.addImportedTrack(waveImport.copy(id = "future-duplicate")))
            assertEquals(future, futureTrack.collectionState)
            assertEquals(futureFacts, fixture.facts())
            fixture.store.updateTrackTextMetadata(track.id, "Changed while unknown", "")
            fixture.store.updateTrackDuration(track.id, 7_000)
            fixture.store.updateTrackMetadata(track.id, "Changed again while unknown", "Artist", 7_001)
            fixture.store.setTrackPreference(other.id, "DISLIKED")
            fixture.store.saveQueue(retained)
            assertEquals(future, fixture.store.state.value.tracks.single { it.id == track.id }.collectionState)
            assertEquals("FUTURE_MEMBER_PREFERENCE", fixture.store.state.value.tracks.single { it.id == track.id }.preference)
            val unknown = fixture.store.state.value
            val unknownFacts = fixture.facts()
            assertEquals("desktop_track_not_in_collection", failureCode { fixture.store.appendToPlaylist(playlist.id, track.id) })
            assertEquals("desktop_track_not_in_collection", failureCode { fixture.store.saveQueue(remapped) })
            assertEquals(unknown, fixture.store.state.value)
            assertEquals(unknownFacts, fixture.facts())
            fixture.reopen()
            assertEquals(unknown, fixture.store.state.value)
            fixture.store.removeTrackFromCollection(track.id)
            val replacedFacts = fixture.facts()
            assertCollectionFact((replacedFacts - unknownFacts.keys).values.single(), "TRACK_REMOVED_FROM_COLLECTION",
                track.id, future, "REMOVED")
            fixture.store.restoreTrackToCollection(track.id)
            val restoredFacts = fixture.facts()
            assertCollectionFact((restoredFacts - replacedFacts.keys).values.single(), "TRACK_RESTORED_TO_COLLECTION",
                track.id, "REMOVED", "ACTIVE")
            assertEquals(unknown.copy(tracks = unknown.tracks.map { if (it.id == track.id) it.copy(collectionState = "ACTIVE") else it }),
                fixture.store.state.value)
            fixture.store.restoreTrackToCollection(track.id)
            assertEquals(restoredFacts, fixture.facts())
            fixture.execute("UPDATE desktop_track_collection SET collectionState = '$future' WHERE trackId = '${track.id}'")
            fixture.reopen()
            fixture.store.restoreTrackToCollection(track.id)
            assertCollectionFact((fixture.facts() - restoredFacts.keys).values.single(), "TRACK_RESTORED_TO_COLLECTION",
                track.id, future, "ACTIVE")
            fixture.store.appendToPlaylist(playlist.id, track.id)
            fixture.store.saveQueue(retained.copy(entries = retained.entries + extra, shuffleOrder = retained.shuffleOrder + extra.id))
            val admitted = fixture.store.state.value
            val admittedFacts = fixture.facts()
            fixture.reopen()
            assertEquals(admitted, fixture.store.state.value)
            assertEquals(admittedFacts, fixture.facts())
            beforeFacts.forEach { (id, payload) -> assertArrayEquals(payload.encodeToByteArray(), admittedFacts.getValue(id).encodeToByteArray()) }
            assertArrayEquals(pcmWave(), Files.readAllBytes(media))
            // Explicitly discarded entries lose their exemption even if an old fact knows them.
            fixture.store.removeTrackFromCollection(track.id)
            val admittedQueue = checkNotNull(admitted.queue)
            val dropped = admittedQueue.copy(entries = admittedQueue.entries.filter { it.id != extra.id },
                shuffleOrder = admittedQueue.shuffleOrder.filter { it != extra.id })
            fixture.store.saveQueue(dropped)
            val droppedState = fixture.store.state.value
            val droppedFacts = fixture.facts()
            assertEquals("desktop_track_not_in_collection", failureCode { fixture.store.saveQueue(admittedQueue) })
            assertEquals(droppedState, fixture.store.state.value)
            assertEquals(droppedFacts, fixture.facts())
            fixture.store.restoreTrackToCollection(track.id)
            fixture.store.saveQueue(admittedQueue)
            collectionBeforeImageBounds(fixture, track.id)
        }
    }

    private fun assertCollectionFact(payload: String, kind: String, trackId: String, before: String, after: String): Unit {
        val document = Json.parseToJsonElement(payload).jsonObject
        assertEquals("1", document.getValue("version").jsonPrimitive.content)
        assertEquals(kind, document.getValue("mutation").jsonPrimitive.content)
        val snapshot = document.getValue("snapshot").jsonObject
        assertEquals(setOf("track_id", "previous_collection_state", "collection_state"), snapshot.keys)
        assertEquals(trackId, snapshot.getValue("track_id").jsonPrimitive.content)
        assertEquals(before, snapshot.getValue("previous_collection_state").jsonPrimitive.content)
        assertEquals(after, snapshot.getValue("collection_state").jsonPrimitive.content)
        assertTrue(payload.encodeToByteArray().size <= 262_144)
    }

    private suspend fun collectionBeforeImageBounds(fixture: Fixture, trackId: String): Unit {
        val facts = fixture.facts()
        val active = fixture.store.state.value
        // UTF-16 length alone is insufficient: multibyte UTF-8 and JSON escaping also count.
        for (future in listOf("x".repeat(262_145), "\u2603".repeat(90_000), "\u0001".repeat(44_000))) {
            fixture.execute("UPDATE desktop_track_collection SET collectionState = '$future' WHERE trackId = '$trackId'")
            fixture.reopen()
            val oversized = active.copy(tracks = active.tracks.map { if (it.id == trackId) it.copy(collectionState = future) else it })
            assertEquals(oversized, fixture.store.state.value)
            assertEquals("desktop_outbox_payload_too_large", failureCode { fixture.store.removeTrackFromCollection(trackId) })
            assertEquals("desktop_outbox_payload_too_large", failureCode { fixture.store.restoreTrackToCollection(trackId) })
            assertEquals(oversized, fixture.store.state.value)
            assertEquals(future, fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = '$trackId'"))
            assertEquals(facts, fixture.facts())
            fixture.reopen()
            assertEquals(oversized, fixture.store.state.value)
        }
        // SQLite UTF-8 cannot faithfully seed an unpaired surrogate; exercise the typed writer.
        val oversized = fixture.store.state.value
        for (invalid in listOf("Invalid\uD800", "Invalid\uDC00")) {
            for (after in listOf("ACTIVE", "REMOVED")) {
                assertEquals("desktop_outbox_payload_invalid", failureCode {
                    DesktopLocalIntent.trackCollectionUpdated(TrackCollectionRow(trackId, invalid), TrackCollectionRow(trackId, after))
                    Unit
                })
            }
        }
        assertEquals(oversized, fixture.store.state.value)
        assertEquals(facts, fixture.facts())
    }

    private suspend fun collectionRollbackAndStickyLatch(): Unit {
        Fixture().useStore { fixture ->
            val bytes = pcmWave()
            val imported = fixture.track("collection-recovery", "Collection recovery", bytes)
            val fileName = "${imported.sha256}.wav"
            val media = fixture.root.resolve("media/$fileName")
            Files.move(fixture.root.resolve("media/${imported.managedFileName}"), media)
            fixture.store.addImportedTrack(imported.copy(managedFileName = fileName))
            fixture.store.setTrackPreference(imported.id, "DISLIKED")
            val playlist = fixture.store.createPlaylist("Membership recovery references")
            repeat(2) { fixture.store.appendToPlaylist(playlist.id, imported.id) }
            val queue = DesktopQueue("recovery-queue", listOf(DesktopQueueEntry("recovery-entry", imported.id)),
                "recovery-entry", sessionId = "recovery-session", sessionStartedAtMs = 100)
            fixture.store.saveQueue(queue, DesktopListeningEvent("recovery-session", "recovery-entry", imported.id, 100, 0, 0, false))
            fixture.store.removeTrackFromCollection(imported.id)
            val removed = fixture.store.state.value
            val removalFacts = fixture.facts()
            fixture.execute("CREATE TRIGGER acceptance_abort_collection_fact BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind IN ('TRACK_REMOVED_FROM_COLLECTION', 'TRACK_RESTORED_TO_COLLECTION') " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                assertEquals("desktop_mutation_failed", failureCode { fixture.store.restoreTrackToCollection(imported.id) })
                assertEquals(removed, fixture.store.state.value)
                assertEquals("REMOVED", fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = '${imported.id}'"))
                assertEquals(removalFacts, fixture.facts())
                fixture.store.removeTrackFromCollection(imported.id)
                assertEquals(removalFacts, fixture.facts())
                fixture.execute("UPDATE desktop_track_collection SET collectionState = 'FUTURE_RECOVERY' WHERE trackId = '${imported.id}'")
                fixture.reopen()
                val future = fixture.store.state.value
                for (operation in listOf<suspend () -> Unit>(
                    { fixture.store.removeTrackFromCollection(imported.id) },
                    { fixture.store.restoreTrackToCollection(imported.id) },
                )) {
                    assertEquals("desktop_mutation_failed", failureCode(operation))
                    assertEquals(future, fixture.store.state.value)
                    assertEquals("FUTURE_RECOVERY", fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = '${imported.id}'"))
                    assertEquals(removalFacts, fixture.facts())
                    assertArrayEquals(bytes, Files.readAllBytes(media))
                }
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_collection_fact") }
            fixture.reopen()
            assertEquals("FUTURE_RECOVERY", fixture.store.state.value.tracks.single().collectionState)
            assertEquals(removalFacts, fixture.facts())
            fixture.store.restoreTrackToCollection(imported.id)
            val active = fixture.store.state.value
            val activeFacts = fixture.facts()
            assertEquals(removed.copy(tracks = removed.tracks.map { it.copy(collectionState = "ACTIVE") }), active)
            fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_collection_retained_playlist")
            try {
                // Membership and intent commit, but display refresh fails: no guessed rollback.
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.removeTrackFromCollection(imported.id) })
                assertEquals(active, fixture.store.state.value)
                assertEquals("REMOVED", fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = '${imported.id}'"))
                val committedFacts = fixture.facts()
                assertEquals(activeFacts.size + 1, committedFacts.size)
                assertCollectionFact((committedFacts - activeFacts.keys).values.single(), "TRACK_REMOVED_FROM_COLLECTION",
                    imported.id, "ACTIVE", "REMOVED")
                listOf<suspend () -> Unit>(
                    { fixture.store.removeTrackFromCollection(imported.id) },
                    { fixture.store.restoreTrackToCollection(imported.id) },
                    { fixture.store.saveQueue(queue) },
                    { fixture.store.resolveManagedFile(imported.id); Unit },
                    { fixture.store.setTrackPreference(imported.id, "NEUTRAL") },
                ).forEach { assertEquals("desktop_snapshot_unavailable", failureCode(it)) }
                assertEquals(committedFacts, fixture.facts())
            } finally { fixture.execute("ALTER TABLE acceptance_collection_retained_playlist RENAME TO desktop_playlist") }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.restoreTrackToCollection(imported.id) })
            fixture.reopen()
            assertEquals(removed, fixture.store.state.value)
            val committedFacts = fixture.facts()
            activeFacts.forEach { (id, payload) -> assertArrayEquals(payload.encodeToByteArray(), committedFacts.getValue(id).encodeToByteArray()) }
            assertArrayEquals(bytes, Files.readAllBytes(media))
            fixture.execute("CREATE TRIGGER acceptance_abort_collection_fact BEFORE INSERT ON desktop_local_outbox " +
                "WHEN NEW.kind = 'TRACK_RESTORED_TO_COLLECTION' BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_collection_retained_playlist")
                try {
                    val failure = try { fixture.store.restoreTrackToCollection(imported.id); null }
                        catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_mutation_failed", failure?.code)
                    val marker = checkNotNull(failure).suppressed.single() as DesktopStorageException
                    assertEquals("desktop_snapshot_unavailable", marker.code)
                    assertNull(marker.cause)
                    assertTrue(marker.suppressed.isEmpty())
                    assertEquals(removed, fixture.store.state.value)
                    assertEquals("REMOVED", fixture.text("SELECT collectionState FROM desktop_track_collection WHERE trackId = '${imported.id}'"))
                    assertEquals(committedFacts, fixture.facts())
                } finally { fixture.execute("ALTER TABLE acceptance_collection_retained_playlist RENAME TO desktop_playlist") }
            } finally { fixture.execute("DROP TRIGGER acceptance_abort_collection_fact") }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.removeTrackFromCollection(imported.id) })
            fixture.reopen()
            assertEquals(removed, fixture.store.state.value)
            assertEquals(committedFacts, fixture.facts())
            assertEquals(media.toString(), fixture.store.resolveManagedFile(imported.id))
            fixture.store.restoreTrackToCollection(imported.id)
            assertEquals(active, fixture.store.state.value)
            assertEquals(committedFacts.size + 1, fixture.facts().size)
            assertArrayEquals(bytes, Files.readAllBytes(media))
        }
    }

    /** Preference commit/refresh and abort/refresh use the same sticky recovery as metadata. */
    private suspend fun trackPreferenceStickyLatch(): Unit {
        Fixture().useStore { fixture ->
            fixture.store.addImportedTrack(fixture.track("preference-a", "Preference recovery"))
            val before = fixture.store.state.value
            val facts = fixture.facts()
            fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_preference_retained_playlist")
            try {
                assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setTrackPreference("preference-a", "DISLIKED") })
                assertEquals(before, fixture.store.state.value)
                assertEquals("DISLIKED", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'preference-a'"))
                assertEquals(facts.size + 1, fixture.facts().size)
                listOf("LIKED", "DISLIKED", "NEUTRAL").forEach {
                    assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setTrackPreference("preference-a", it) })
                }
            } finally {
                fixture.execute("ALTER TABLE acceptance_preference_retained_playlist RENAME TO desktop_playlist")
            }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setTrackPreference("preference-a", "NEUTRAL") })
            fixture.reopen()
            val committed = before.copy(tracks = before.tracks.map { it.copy(preference = "DISLIKED") })
            assertEquals(committed, fixture.store.state.value)
            val committedFacts = fixture.facts()
            facts.forEach { (id, payload) -> assertEquals(payload, committedFacts[id]) }
            fixture.execute("CREATE TRIGGER acceptance_abort_preference_fact BEFORE INSERT ON desktop_local_outbox " +
                "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
            try {
                fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_preference_retained_playlist")
                try {
                    val failure = try { fixture.store.setTrackPreference("preference-a", "NEUTRAL"); null }
                        catch (error: DesktopStorageException) { error }
                    assertEquals("desktop_mutation_failed", failure?.code)
                    val marker = checkNotNull(failure).suppressed.single() as DesktopStorageException
                    assertEquals("desktop_snapshot_unavailable", marker.code)
                    assertNull(marker.cause)
                    assertTrue(marker.suppressed.isEmpty())
                    assertEquals(committed, fixture.store.state.value)
                    assertEquals("DISLIKED", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'preference-a'"))
                    assertEquals(committedFacts, fixture.facts())
                } finally {
                    fixture.execute("ALTER TABLE acceptance_preference_retained_playlist RENAME TO desktop_playlist")
                }
            } finally {
                fixture.execute("DROP TRIGGER acceptance_abort_preference_fact")
            }
            assertEquals("desktop_snapshot_unavailable", failureCode { fixture.store.setTrackPreference("preference-a", "LIKED") })
            fixture.reopen()
            assertEquals(committed, fixture.store.state.value)
            assertEquals(committedFacts, fixture.facts())
            fixture.store.setTrackPreference("preference-a", "NEUTRAL")
            assertEquals(before, fixture.store.state.value)
            assertEquals(committedFacts.size + 1, fixture.facts().size)
        }
    }

    /** Authored inside the existing restart composite; requires later authorized Room execution. */
    private suspend fun legacyMigrationAndTrackPreferences(): Unit {
        Fixture().useStore(legacyBytes = pcmWave()) { fixture ->
            val schema = checkNotNull(fixture.legacySchema)
            val schemaBytes = checkNotNull(fixture.legacySchemaBytes)
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))
            assertEquals(fixture.legacyRows, fixture.rows(schema))
            assertEquals(5L, fixture.number("PRAGMA user_version"))
            assertNull(fixture.store.state.value.playlists.single().description)
            assertEquals(1L, fixture.number("SELECT COUNT(*) FROM pragma_table_info('desktop_playlist') " +
                "WHERE name = 'description' AND type = 'TEXT' AND [notnull] = 0 AND dflt_value = 'NULL'"))
            assertEquals(1L, fixture.number("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'desktop_track_preference'"))
            assertEquals(1L, fixture.number("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'desktop_track_collection'"))
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            assertEquals("ACTIVE", fixture.store.state.value.tracks.single().collectionState)
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_preference"))
            assertEquals("NEUTRAL", fixture.store.state.value.tracks.single().preference)
            val managed = fixture.root.resolve("media/${fixture.store.state.value.tracks.single().sha256}.media")
            assertArrayEquals(pcmWave(), Files.readAllBytes(managed))
            val migrated = fixture.store.state.value
            val priorFacts = fixture.facts()
            assertEquals("INCLUDED", migrated.queue?.sessionTasteState)
            assertEquals("INCLUDED", migrated.queue?.listenTasteState)
            assertTrue(migrated.history.all { it.sessionTasteState == "INCLUDED" && it.listenTasteState == "INCLUDED" })
            assertEquals(1, priorFacts.size)
            fixture.store.setTrackPreference("legacy-a", "NEUTRAL")
            fixture.store.setTrackPreference("legacy-a", "NEUTRAL")
            fixture.store.restoreTrackToCollection("legacy-a")
            fixture.store.restoreTrackToCollection("legacy-a")
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_collection"))
            assertEquals(0L, fixture.number("SELECT COUNT(*) FROM desktop_track_preference"))
            assertEquals(priorFacts, fixture.facts())
            fixture.reopen()
            assertEquals(migrated, fixture.store.state.value)
            assertEquals(fixture.legacyRows, fixture.rows(schema))

            fixture.store.setTrackPreference("legacy-a", "LIKED")
            val liked = fixture.store.state.value
            assertEquals(migrated.copy(tracks = migrated.tracks.map { it.copy(preference = "LIKED") }), liked)
            assertEquals("LIKED", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'legacy-a'"))
            val likedFacts = fixture.facts()
            assertEquals(priorFacts.size + 1, likedFacts.size)
            val changed = (likedFacts - priorFacts.keys).values.single()
            val snapshot = Json.parseToJsonElement(changed).jsonObject.getValue("snapshot").jsonObject
            assertEquals(setOf("track_id", "previous_preference", "preference"), snapshot.keys)
            assertEquals("legacy-a", snapshot.getValue("track_id").jsonPrimitive.content)
            assertEquals("NEUTRAL", snapshot.getValue("previous_preference").jsonPrimitive.content)
            assertEquals("LIKED", snapshot.getValue("preference").jsonPrimitive.content)
            assertEquals("legacy-a", fixture.text("SELECT aggregateId FROM desktop_local_outbox WHERE kind = 'TRACK_PREFERENCE_UPDATED'"))
            assertFalse(changed.contains(fixture.root.toString()))
            fixture.store.setTrackPreference("legacy-a", "LIKED")
            listOf("", "liked", "LIKED ", "FUTURE", "DISLIKE", "Invalid\u0000", "Invalid\uD800", "x".repeat(262_145)).forEach {
                assertEquals("desktop_preference_invalid", failureCode { fixture.store.setTrackPreference("legacy-a", it) })
            }
            assertEquals("desktop_identity_invalid", failureCode { fixture.store.setTrackPreference("bad/id", "LIKED") })
            assertEquals("desktop_track_missing", failureCode { fixture.store.setTrackPreference("missing-track", "LIKED") })
            assertEquals("desktop_track_missing", failureCode { fixture.store.setTrackPreference("missing-track", "NEUTRAL") })
            assertEquals(liked, fixture.store.state.value)
            assertEquals(likedFacts, fixture.facts())
            fixture.reopen()
            assertEquals(liked, fixture.store.state.value)
            fixture.store.setTrackPreference("legacy-a", "DISLIKED")
            val disliked = fixture.store.state.value
            assertEquals(liked.copy(tracks = liked.tracks.map { it.copy(preference = "DISLIKED") }), disliked)
            fixture.reopen()
            assertEquals(disliked, fixture.store.state.value)

            // Future values survive projection, duplicate import and unrelated writes verbatim.
            val future = "FUTURE_CHOICE_\u2603"
            fixture.execute("UPDATE desktop_track_preference SET preference = '$future' WHERE trackId = 'legacy-a'")
            fixture.reopen()
            val futureTrack = fixture.store.state.value.tracks.single()
            assertEquals(future, futureTrack.preference)
            val beforeDuplicateFacts = fixture.facts()
            val duplicate = fixture.track("duplicate-id", "Ignored labels", pcmWave())
            assertEquals(futureTrack, fixture.store.addImportedTrack(duplicate))
            assertEquals(beforeDuplicateFacts, fixture.facts())
            fixture.store.updateTrackTextMetadata("legacy-a", "Explicit text", "")
            assertEquals(future, fixture.store.state.value.tracks.single().preference)
            fixture.store.updateTrackDuration("legacy-a", 5_000)
            fixture.store.updateTrackMetadata("legacy-a", "Explicit full metadata", "Artist", 5_001)
            val queue = checkNotNull(fixture.store.state.value.queue)
            fixture.store.saveQueue(queue)
            fixture.store.renamePlaylist("legacy-playlist", "Explicit playlist")
            assertEquals(future, fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'legacy-a'"))
            val beforeClear = fixture.store.state.value
            val beforeClearFacts = fixture.facts()
            fixture.reopen()
            assertEquals(beforeClear, fixture.store.state.value)
            fixture.store.setTrackPreference("legacy-a", "NEUTRAL")
            assertEquals(beforeClear.copy(tracks = beforeClear.tracks.map { it.copy(preference = "NEUTRAL") }), fixture.store.state.value)
            assertEquals("NEUTRAL", fixture.text("SELECT preference FROM desktop_track_preference WHERE trackId = 'legacy-a'"))
            val clearFacts = fixture.facts()
            assertEquals(beforeClearFacts.size + 1, clearFacts.size)
            val cleared = Json.parseToJsonElement((clearFacts - beforeClearFacts.keys).values.single())
                .jsonObject.getValue("snapshot").jsonObject
            assertEquals(future, cleared.getValue("previous_preference").jsonPrimitive.content)
            assertEquals("NEUTRAL", cleared.getValue("preference").jsonPrimitive.content)
            fixture.store.setTrackPreference("legacy-a", "NEUTRAL")
            assertEquals(clearFacts, fixture.facts())
            fixture.reopen()
            assertEquals("NEUTRAL", fixture.store.state.value.tracks.single().preference)
            assertEquals(clearFacts, fixture.facts())
            priorFacts.forEach { (id, payload) -> assertArrayEquals(payload.encodeToByteArray(), clearFacts.getValue(id).encodeToByteArray()) }
            assertArrayEquals(pcmWave(), Files.readAllBytes(managed))
            assertArrayEquals(schemaBytes, Files.readAllBytes(fixture.schemaFile()))

            // An unknown before-value exceeding the immutable fact bound cannot be discarded.
            fixture.execute("UPDATE desktop_track_preference SET preference = '${"x".repeat(262_145)}' WHERE trackId = 'legacy-a'")
            fixture.reopen()
            val oversized = fixture.store.state.value
            assertEquals("desktop_outbox_payload_too_large", failureCode { fixture.store.setTrackPreference("legacy-a", "LIKED") })
            assertEquals(oversized, fixture.store.state.value)
            assertEquals(clearFacts, fixture.facts())
            assertEquals(262_145L, fixture.number("SELECT length(preference) FROM desktop_track_preference WHERE trackId = 'legacy-a'"))
        }
    }

    private suspend fun preferencesRestartAndPortableAllowlist(fixture: Fixture): Unit {
        val domain = fixture.store.state.value
        val facts = fixture.facts()
        val file = fixture.root.resolve("desktop-settings-v1.json")
        val defaults = DesktopPreferences(appLanguage = "RU")
        var prefs = fixture.openPreferences(defaults)
        assertEquals(defaults, prefs.state.value.value)
        assertFalse(Files.exists(file))
        prefs.setAppLanguage("RU")
        assertFalse(Files.exists(file))
        assertEquals("desktop_settings_language_invalid", settingsFailure { prefs.setAppLanguage("FUTURE") })
        prefs.setAppearanceMode("SYSTEM")
        prefs.setAccentPalette("BLUE")
        prefs.setSmoothTrackTransitions(true)
        assertTrue(prefs.state.value.value.smoothTrackTransitions)
        val persisted = prefs.state.value.value
        val unchanged = Files.readAllBytes(file)
        prefs.setAccentPalette("BLUE")
        prefs.setSmoothTrackTransitions(true)
        assertArrayEquals(unchanged, Files.readAllBytes(file))
        prefs = fixture.openPreferences()
        assertEquals(persisted, prefs.state.value.value)
        val competing = openDesktopPreferencesStore(fixture.root)
        try {
            assertFalse(competing.state.value.writable)
            assertEquals("desktop_settings_in_use", competing.state.value.errorCode)
            assertNotNull(settingsFailure { competing.setSmoothTrackTransitions(false) })
            assertArrayEquals(unchanged, Files.readAllBytes(file))
        } finally { competing.close() }
        fixture.closePreferences()
        val extension = Json.parseToJsonElement("""{"future":[1,"keep"],"exact":9007199254740993}""")
        val local = JsonObject(Json.parseToJsonElement(unchanged.decodeToString()).jsonObject + mapOf(
            "local_extension" to extension, "app_language" to JsonPrimitive("KLINGON"),
            "appearance_mode" to JsonPrimitive("FUTURE_APPEARANCE"),
            "accent_palette" to JsonPrimitive("FUTURE_PALETTE"), "wave_prefetch_mode" to JsonPrimitive("FUTURE_PREFETCH"),
        ))
        Files.write(file, local.toString().encodeToByteArray()) // Owned synthetic fixture only.
        prefs = fixture.openPreferences()
        assertTrue(prefs.state.value.writable)
        assertEquals("FUTURE_APPEARANCE", prefs.state.value.value.appearanceMode)
        prefs.setAppearanceMode("LIGHT")
        assertEquals("KLINGON", prefs.state.value.value.appLanguage)
        assertEquals("FUTURE_PALETTE", prefs.state.value.value.accentPalette)
        assertEquals("FUTURE_PREFETCH", prefs.state.value.value.wavePrefetchMode)
        prefs.setSmoothTrackTransitions(false)
        assertFalse(prefs.state.value.value.smoothTrackTransitions)
        assertEquals("KLINGON", prefs.state.value.value.appLanguage)
        assertEquals("FUTURE_PALETTE", prefs.state.value.value.accentPalette)
        assertEquals("FUTURE_PREFETCH", prefs.state.value.value.wavePrefetchMode)
        assertEquals(extension, Json.parseToJsonElement(Files.readAllBytes(file).decodeToString()).jsonObject["local_extension"])
        assertEquals("desktop_settings_palette_invalid", settingsFailure { DesktopSettingsTransferCodec.encode(prefs.state.value.value); Unit })
        for (version in 1..3) {
            val network = if (version == 1) "sync_on_metered_network" else "download_on_metered_network"
            val source = """{"schema_version":$version,"appearance_mode":"DARK","accent_palette":"VIOLET","$network":true,"wave_prefetch_mode":"NEXT_3","credentials":"must-ignore","server_base_url":"https://private.invalid","local_extension":{"untrusted":true}}"""
            val current = prefs.state.value.value.copy(smoothTrackTransitions = true)
            val decoded = DesktopSettingsTransferCodec.decode(source.encodeToByteArray(), current)
            assertEquals("KLINGON", decoded.appLanguage)
            assertTrue(decoded.smoothTrackTransitions)
            assertTrue(decoded.downloadOnMeteredNetwork)
            prefs.applyPortable(decoded)
            val exported = DesktopSettingsTransferCodec.encode(prefs.state.value.value)
            val portable = Json.parseToJsonElement(exported.decodeToString()).jsonObject
            assertEquals(setOf("schema_version", "app_language", "appearance_mode", "accent_palette",
                "download_on_metered_network", "wave_prefetch_mode", "smooth_track_transitions"), portable.keys)
            assertEquals("3", portable.getValue("schema_version").jsonPrimitive.content)
            assertEquals(decoded, DesktopSettingsTransferCodec.decode(exported, DesktopPreferences()))
            val retained = Json.parseToJsonElement(Files.readAllBytes(file).decodeToString()).jsonObject
            assertEquals(extension, retained["local_extension"])
            assertFalse("credentials" in retained || "server_base_url" in retained)
        }
        val confirmed = prefs.state.value.value
        prefs = fixture.openPreferences()
        assertEquals(confirmed, prefs.state.value.value)
        assertEquals(domain, fixture.store.state.value)
        assertEquals(facts, fixture.facts())
        fixture.closePreferences()
    }

    private suspend fun preferencesCancellationAndClose(fixture: Fixture): Unit = coroutineScope {
        val domain = fixture.store.state.value
        val facts = fixture.facts()
        val prefs = fixture.openPreferences()
        prefs.setAccentPalette("GREEN")
        val file = fixture.root.resolve("desktop-settings-v1.json")
        // Cancellation may win before publication, or arrive after the whole publication barrier.
        val writer = launch { prefs.setAppearanceMode("LIGHT") }
        yield()
        writer.cancel()
        writer.join()
        val smoothWriter = launch { prefs.setSmoothTrackTransitions(true) }
        yield()
        smoothWriter.cancel()
        smoothWriter.join()
        assertTrue(prefs.state.value.writable)
        val committed = prefs.state.value.value
        assertEquals(committed, DesktopSettingsJson.preferences(DesktopSettingsJson.parse(Files.readAllBytes(file)),
            DesktopPreferences(), 1, portable = false))
        val bytes = Files.readAllBytes(file)
        val orphan = fixture.root.resolve(".desktop-settings-fixture-orphan.tmp")
        Files.write(orphan, byteArrayOf(7, 8, 9))
        fixture.closePreferences()
        assertEquals("desktop_settings_closed", settingsFailure { prefs.setAppLanguage("RU") })
        assertEquals("desktop_settings_closed", settingsFailure { prefs.setSmoothTrackTransitions(true) })
        assertArrayEquals(bytes, Files.readAllBytes(file))
        assertArrayEquals(byteArrayOf(7, 8, 9), Files.readAllBytes(orphan))
        assertEquals(committed, fixture.openPreferences().state.value.value)
        assertArrayEquals(byteArrayOf(7, 8, 9), Files.readAllBytes(orphan))
        assertEquals(domain, fixture.store.state.value)
        assertEquals(facts, fixture.facts())
        fixture.closePreferences()
    }

    private suspend fun preferencesRejectedFilesStayByteExact(fixture: Fixture): Unit {
        val file = fixture.root.resolve("desktop-settings-v1.json")
        val defaults = DesktopPreferences(appLanguage = "RU")
        val valid = DesktopSettingsJson.document(DesktopPreferences(), 1).toString()
        val domain = fixture.store.state.value
        val facts = fixture.facts()
        val documents = listOf(
            valid.replace("\"schema_version\":1", "\"schema_version\":99").encodeToByteArray(),
            "{broken".encodeToByteArray(),
            "[]".encodeToByteArray(),
            valid.replace("\"app_language\":\"EN\"", "\"app_language\":false").encodeToByteArray(),
            valid.replace("\"download_on_metered_network\":false", "\"download_on_metered_network\":\"false\"").encodeToByteArray(),
            byteArrayOf(0xC3.toByte(), 0x28),
            ByteArray(DesktopSettingsTransferCodec.MAX_BYTES + 1) { 32 },
            ("[".repeat(18) + "0" + "]".repeat(18)).encodeToByteArray(),
            "{\"schema_version\":1,\"schema_version\":1}".encodeToByteArray(),
        )
        for (bytes in documents) {
            Files.write(file, bytes) // No production/user paths are touched by this case.
            val prefs = fixture.openPreferences(defaults)
            assertFalse(prefs.state.value.writable)
            assertEquals(defaults, prefs.state.value.value)
            assertTrue(checkNotNull(prefs.state.value.errorCode).startsWith("desktop_settings_"))
            assertNotNull(settingsFailure { prefs.setAppLanguage("EN") })
            assertNotNull(settingsFailure { prefs.setSmoothTrackTransitions(true) })
            assertArrayEquals(bytes, Files.readAllBytes(file))
            fixture.closePreferences()
        }
        // Valid but unsupported portable enum values fail without touching the owned settings file.
        val portable = DesktopSettingsTransferCodec.encode(DesktopPreferences()).decodeToString()
        for (source in listOf(portable.replace("\"DARK\"", "\"FUTURE\""),
            portable.replace("\"CORAL\"", "\"FUTURE\""), portable.replace("\"NEXT\"", "\"FUTURE\""),
            portable.replace("\"EN\"", "\"bad\\u0000\""), portable.replace("\"EN\"", "\"bad\\uD800\""),
            portable.replace("\"EN\"", "\"${"x".repeat(129)}\""),
            portable.replace("\"smooth_track_transitions\":false", "\"smooth_track_transitions\":0"))) {
            assertNotNull(settingsFailure { DesktopSettingsTransferCodec.decode(source.encodeToByteArray(), defaults); Unit })
        }
        assertEquals(domain, fixture.store.state.value)
        assertEquals(facts, fixture.facts())
        // Preferences failure leaves the real library store available, with an effective no-op.
        fixture.store.deletePlaylist("missing-playlist")
        assertEquals(facts, fixture.facts())
    }

    private suspend fun settingsFailure(operation: suspend () -> Unit): String? = try {
        operation()
        null
    } catch (failure: DesktopSettingsException) { failure.code }

    private suspend fun failureCode(operation: suspend () -> Unit): String? = try {
        operation()
        null
    } catch (failure: DesktopStorageException) { failure.code }

    /** Minimal PCM container for selected legacy repair; database tests never decode it. */
    private fun pcmWave(): ByteArray = ByteBuffer.allocate(76).order(ByteOrder.LITTLE_ENDIAN).apply {
        put("RIFF".toByteArray(Charsets.US_ASCII)); putInt(68)
        put("WAVEfmt ".toByteArray(Charsets.US_ASCII)); putInt(16)
        putShort(1.toShort()); putShort(1.toShort()); putInt(16_000); putInt(32_000)
        putShort(2.toShort()); putShort(16.toShort())
        put("data".toByteArray(Charsets.US_ASCII)); putInt(32)
        repeat(16) { putShort(0.toShort()) }
    }.array()

    private class Fixture {
        val root: Path = Files.createTempDirectory("autplay-data-acceptance-").toRealPath()
        private val db: Path = root.resolve("desktop-library-v1.db")
        private var opened: DesktopLibraryStore? = null
        private var preferences: DesktopPreferencesStore? = null
        val store: DesktopLibraryStore get() = checkNotNull(opened)
        var legacySchema: JsonObject? = null
            private set
        var legacySchemaBytes: ByteArray? = null
            private set
        var legacyRows: Map<String, List<List<String>>> = emptyMap()
            private set

        suspend fun useStore(legacyBytes: ByteArray? = null, legacyVersion: Int = 1, block: suspend (Fixture) -> Unit): Unit {
            var originalFailure: Throwable? = null
            try {
                legacyBytes?.let { createLegacySchema(it, legacyVersion) }
                reopen()
                block(this)
            } catch (failure: Throwable) {
                originalFailure = failure
                throw failure
            } finally {
                withContext(NonCancellable) {
                    try {
                        // A failed close retains the fixture rather than deleting a live database.
                        withTimeout(5_000) { closePreferences(); closeStore() }
                        check(!Files.isSymbolicLink(root) && root.toRealPath() == root) { "Unsafe fixture root" }
                        val ownedPaths = Files.walk(root).use { paths -> paths.sorted(Comparator.reverseOrder()).toList() }
                        check(ownedPaths.all { it.toAbsolutePath().normalize().startsWith(root) }) { "Unsafe fixture entry" }
                        // A transient SQLite sidecar may disappear after the closed traversal snapshot.
                        ownedPaths.forEach { Files.deleteIfExists(it) }
                        check(!Files.exists(root)) { "Fixture cleanup did not remove owned root" }
                    } catch (cleanupFailure: Throwable) {
                        val failure = originalFailure
                        if (failure == null) throw cleanupFailure else failure.addSuppressed(cleanupFailure)
                    }
                }
            }
        }
        suspend fun closeStore(): Unit { opened?.close(); opened = null }
        suspend fun closePreferences(): Unit { preferences?.close(); preferences = null }
        suspend fun openPreferences(defaults: DesktopPreferences = DesktopPreferences()): DesktopPreferencesStore {
            closePreferences()
            return openDesktopPreferencesStore(root, defaults).also { preferences = it }
        }
        suspend fun reopen(): Unit { closeStore(); opened = openDesktopLibraryStore(root) }
        private inline fun <T> connection(action: (SQLiteConnection) -> T): T =
            BundledSQLiteDriver().open(db.toString()).use { connection ->
                // Room invalidation can outlive an awaited DAO call. Bound only fixture contention.
                connection.prepare("PRAGMA busy_timeout=1000").use { it.step() }
                connection.prepare("PRAGMA busy_timeout").use {
                    assertTrue(it.step()); assertEquals(1_000L, it.getLong(0)); assertFalse(it.step())
                }
                action(connection)
            }
        fun schemaFile(): Path {
            val relative = "schemas/app.autplay.data.desktop.DesktopDatabase/1.json"
            return generateSequence(Path.of("").toAbsolutePath().normalize()) { it.parent }
                .flatMap { sequenceOf(it.resolve(relative), it.resolve("shared/data/$relative")) }
                .first { Files.isRegularFile(it) }
        }
        /** Build v1 from its real pinned export, never from the new schema or a forged v2 export. */
        private suspend fun createLegacySchema(mediaBytes: ByteArray, version: Int): Unit {
            check(version in 1..4)
            val bytes = Files.readAllBytes(schemaFile())
            val schema = Json.parseToJsonElement(bytes.decodeToString()).jsonObject.getValue("database").jsonObject
            check(schema.getValue("version").jsonPrimitive.content == "1")
            legacySchemaBytes = bytes
            legacySchema = schema
            connection { connection ->
                fun sql(value: String): Unit = connection.prepare(value).use { it.step(); Unit }
                sql("PRAGMA foreign_keys = ON")
                schema.getValue("entities").jsonArray.forEach { entity ->
                    val definition = entity.jsonObject
                    val name = definition.getValue("tableName").jsonPrimitive.content
                    sql(definition.getValue("createSql").jsonPrimitive.content.replace("\u0024{TABLE_NAME}", name))
                    definition["indices"]?.jsonArray?.forEach { index ->
                        sql(index.jsonObject.getValue("createSql").jsonPrimitive.content.replace("\u0024{TABLE_NAME}", name))
                    }
                }
                schema.getValue("setupQueries").jsonArray.forEach { sql(it.jsonPrimitive.content) }
                sql("PRAGMA user_version = 1")
                Files.createDirectories(root.resolve("media"))
                val imported = track("legacy-a", "Legacy title", mediaBytes)
                sql("INSERT INTO desktop_track VALUES ('legacy-a', 'Legacy title', 'Legacy artist', -7, '${imported.sha256}', ${imported.byteSize})")
                sql("INSERT INTO desktop_managed_locator VALUES ('legacy-a', '${imported.managedFileName}')")
                sql("INSERT INTO desktop_playlist VALUES ('legacy-playlist', 'Legacy playlist')")
                sql("INSERT INTO desktop_playlist_entry VALUES ('legacy-ref-a', 'legacy-playlist', 'legacy-a', -7)")
                sql("INSERT INTO desktop_playlist_entry VALUES ('legacy-ref-b', 'legacy-playlist', 'legacy-a', 9007199254740993)")
                sql("INSERT INTO desktop_queue VALUES (1, 'legacy-queue', 'legacy-entry-b', 'FUTURE_QUEUE', 9007199254740993, 1, 'FUTURE_REPEAT', 'legacy-session', 100, 90)")
                sql("INSERT INTO desktop_queue_entry VALUES ('legacy-entry-a', 'legacy-a', 0, 1)")
                sql("INSERT INTO desktop_queue_entry VALUES ('legacy-entry-b', 'legacy-a', 1, 0)")
                sql("INSERT INTO desktop_listening VALUES ('legacy-session', 'legacy-entry-b', 'legacy-a', 100, 90, 9007199254740993, 0)")
                sql("INSERT INTO desktop_local_outbox VALUES ('legacy-fact', 'FUTURE_FACT', 'legacy-a', 9007199254740993, '{\"future\":\"retained\\u2603\"}', 77)")
                if (version >= 2) {
                    // A real v1 database plus the actual 1->2 migration, not a fabricated export.
                    DesktopPreferenceMigration.migrate(connection)
                    sql("INSERT INTO desktop_track_preference VALUES ('legacy-a', 'FUTURE_SCHEMA2_PREFERENCE')")
                    sql("PRAGMA user_version = 2")
                }
                if (version >= 3) {
                    // Genuine exported v1 plus the actual 1->2->3 migrations is a prior-v3
                    // database fixture. Source-history/3.kt remains a reference, not KSP evidence.
                    DesktopCollectionMigration.migrate(connection)
                    sql("INSERT INTO desktop_track_collection VALUES ('legacy-a', 'FUTURE_SCHEMA3_COLLECTION')")
                    sql("PRAGMA user_version = 3")
                }
                if (version == 4) {
                    // Actual migrations from exported v1, with retained v4 annotations.
                    // Source-history/4.kt is only a source reference; generated exports are deferred.
                    DesktopTasteMigration.migrate(connection)
                    sql("UPDATE desktop_queue SET sessionTasteState = 'FUTURE_SCHEMA4_SESSION', listenTasteState = 'FUTURE_SCHEMA4_LISTEN'")
                    sql("UPDATE desktop_listening SET sessionTasteState = 'FUTURE_SCHEMA4_SESSION', listenTasteState = 'FUTURE_SCHEMA4_LISTEN'")
                    sql("PRAGMA user_version = 4")
                }
            }
            legacyRows = rows(schema)
        }
        /** Lossless SQL values for every field/row in every old table, including old fact metadata. */
        fun rows(schema: JsonObject): Map<String, List<List<String>>> = connection { connection ->
            schema.getValue("entities").jsonArray.associate { entity ->
                val definition = entity.jsonObject
                val name = definition.getValue("tableName").jsonPrimitive.content
                val columns = definition.getValue("fields").jsonArray.map { it.jsonObject.getValue("columnName").jsonPrimitive.content }
                val keys = definition.getValue("primaryKey").jsonObject.getValue("columnNames").jsonArray.map { it.jsonPrimitive.content }
                val selection = columns.joinToString(",") { "quote(`$it`)" }
                val ordering = keys.joinToString(",") { "`$it`" }
                name to connection.prepare("SELECT $selection FROM `$name` ORDER BY $ordering").use { statement ->
                    buildList { while (statement.step()) add(columns.indices.map { statement.getText(it) }) }
                }
            }
        }
        fun track(id: String, title: String, bytes: ByteArray = byteArrayOf(1, 2, 3, 4)): DesktopImportedTrack {
            val hash = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
            Files.write(root.resolve("media/$hash.media"), bytes)
            return DesktopImportedTrack(id, title, "", null, hash, bytes.size.toLong(), "$hash.media")
        }
        fun execute(sql: String): Unit = connection { connection ->
            connection.prepare(sql).use { it.step(); Unit }
        }
        /** Synthetic history fixture; a single bounded batch, never a production import or mutation API. */
        fun insertHistory(events: List<DesktopListeningEvent>): Unit = connection { connection ->
            require(events.size <= 237)
            connection.prepare("BEGIN TRANSACTION").use { it.step() }
            try {
                events.forEach { event ->
                    connection.prepare("INSERT INTO desktop_listening (id, queueEntryId, trackId, startedAtMs, playedMs, " +
                        "positionMs, finalized, sessionTasteState, listenTasteState) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)").use {
                        it.bindText(1, event.id); it.bindText(2, event.queueEntryId); it.bindText(3, event.trackId)
                        it.bindLong(4, event.startedAtMs); it.bindLong(5, event.playedMs); it.bindLong(6, event.positionMs)
                        it.bindLong(7, if (event.finalized) 1L else 0L)
                        it.bindText(8, event.sessionTasteState); it.bindText(9, event.listenTasteState); it.step()
                    }
                }
                connection.prepare("COMMIT").use { it.step() }
            } catch (failure: Throwable) {
                connection.prepare("ROLLBACK").use { it.step() }
                throw failure
            }
        }
        /** Include current columns and temporarily renamed fixture tables, not only legacy export fields. */
        fun allRows(): Map<String, List<List<String>>> = connection { connection ->
            val tables = connection.prepare("SELECT name FROM sqlite_master WHERE type = 'table' " +
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").use { statement ->
                buildList { while (statement.step()) add(statement.getText(0)) }
            }
            tables.associateWith { name ->
                require(name.matches(Regex("[A-Za-z0-9_]+")))
                val columns = connection.prepare("PRAGMA table_info(\"$name\")").use { statement ->
                    buildList { while (statement.step()) add(statement.getText(1)) }
                }
                require(columns.all { it.matches(Regex("[A-Za-z0-9_]+")) })
                val selection = columns.joinToString(",") { "quote(\"$it\")" }
                val ordering = columns.joinToString(",") { "\"$it\"" }
                connection.prepare("SELECT $selection FROM \"$name\" ORDER BY $ordering").use { statement ->
                    buildList { while (statement.step()) add(columns.indices.map { statement.getText(it) }) }
                }
            }
        }
        fun setCurrentTasteFlags(session: String, listen: String): Unit = connection { connection ->
            connection.prepare("UPDATE desktop_queue SET sessionTasteState = ?, listenTasteState = ?").use {
                it.bindText(1, session); it.bindText(2, listen); it.step()
            }
            connection.prepare("UPDATE desktop_listening SET sessionTasteState = ?, listenTasteState = ? " +
                "WHERE id = (SELECT sessionId FROM desktop_queue WHERE singleton = 1)").use {
                it.bindText(1, session); it.bindText(2, listen); it.step()
            }
        }
        fun number(sql: String): Long = connection { connection ->
            connection.prepare(sql).use { assertTrue(it.step()); it.getLong(0) }
        }
        fun text(sql: String): String = connection { connection ->
            connection.prepare(sql).use { assertTrue(it.step()); it.getText(0) }
        }
        fun facts(): Map<String, String> = connection { connection ->
            connection.prepare("SELECT changeId, payloadJson FROM desktop_local_outbox ORDER BY changeId").use { statement ->
                buildMap { while (statement.step()) put(statement.getText(0), statement.getText(1)) }
            }
        }
    }
}
