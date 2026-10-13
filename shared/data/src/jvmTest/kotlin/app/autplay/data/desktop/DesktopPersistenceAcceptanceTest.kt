package app.autplay.data.desktop

import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.Comparator
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.Timeout

/** Real on-disk Room/BundledSQLite: no repository, DAO or transaction mocks. */
class DesktopPersistenceAcceptanceTest {
    @get:Rule val deadline: Timeout = Timeout.seconds(45)

    @Test fun restartPreservesCollectionSessionAndImmutableLocalFacts(): Unit = runBlocking {
        withTimeout(30_000) {
            Fixture().useStore { fixture ->
                val store = fixture.store
                val imported = fixture.track("track-a", "Original title")
                val track = store.addImportedTrack(imported)
                val originalFacts = fixture.facts()
                assertEquals(1, originalFacts.size)
                assertEquals(track, store.addImportedTrack(imported.copy(id = "duplicate-id", title = "Other title")))
                assertEquals(originalFacts, fixture.facts())

                val playlist = store.createPlaylist("Acceptance playlist")
                repeat(2) { store.appendToPlaylist(playlist.id, track.id) }
                val entries = store.state.value.playlists.single().entries
                assertEquals(listOf(track.id, track.id), entries.map { it.trackId })
                assertEquals(2, entries.map { it.id }.distinct().size)
                store.removePlaylistEntry(playlist.id, entries.first().id)
                store.removePlaylistEntry(playlist.id, entries.first().id)

                val queue = DesktopQueue("queue-a", listOf(DesktopQueueEntry("entry-a", track.id),
                    DesktopQueueEntry("entry-b", track.id)), "entry-b", positionMs = 1_234,
                    shuffleEnabled = true, shuffleOrder = listOf("entry-b", "entry-a"), repeatMode = "ALL",
                    sessionId = "session-a", sessionStartedAtMs = 1_000, playedMs = 900)
                val checkpoint = DesktopListeningEvent("session-a", "entry-b", track.id, 1_000, 900, 1_234, false)
                store.saveQueue(queue, checkpoint)
                store.saveQueue(queue, checkpoint)
                assertEquals(listOf(checkpoint), store.state.value.history)
                store.updateTrackMetadata(track.id, "Edited title", "Edited artist", 4_000)
                val updatedFacts = fixture.facts()
                originalFacts.forEach { (id, payload) ->
                    assertArrayEquals(payload.toByteArray(Charsets.UTF_8), updatedFacts.getValue(id).toByteArray(Charsets.UTF_8))
                }
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
                assertNotNull(fixture.store.resolveManagedFile(track.id))

                // Future queue vocabulary must survive storage even if the controller cannot play it.
                fixture.store.saveQueue(queue.copy(queueType = "FUTURE_QUEUE", repeatMode = "FUTURE_REPEAT"))
                fixture.reopen()
                assertEquals("FUTURE_QUEUE", fixture.store.state.value.queue?.queueType)
                assertEquals("FUTURE_REPEAT", fixture.store.state.value.queue?.repeatMode)
            }
        }
    }

    @Test fun outboxAbortRollsBackTrackMetadataPlaylistAndQueueHistoryTogether(): Unit = runBlocking {
        withTimeout(30_000) {
            Fixture().useStore { fixture ->
                val store = fixture.store
                val track = store.addImportedTrack(fixture.track("track-a", "Before failure"))
                val playlist = store.createPlaylist("Before failure")
                store.appendToPlaylist(playlist.id, track.id)
                val queue = DesktopQueue("queue-a", listOf(DesktopQueueEntry("entry-a", track.id)), "entry-a",
                    sessionId = "session-a", sessionStartedAtMs = 100)
                store.saveQueue(queue)
                val baseline = store.state.value
                val facts = fixture.facts()
                val candidate = fixture.track("track-b", "Must roll back", byteArrayOf(8, 9, 10))
                fixture.execute("CREATE TRIGGER acceptance_abort_outbox BEFORE INSERT ON desktop_local_outbox " +
                    "BEGIN SELECT RAISE(ABORT, 'acceptance_injected_abort'); END")
                try {
                    val operations: List<suspend () -> Unit> = listOf(
                        { store.addImportedTrack(candidate); Unit },
                        { store.updateTrackMetadata(track.id, "Must roll back", "", 100) },
                        { store.createPlaylist("Must roll back"); Unit },
                        { store.appendToPlaylist(playlist.id, track.id) },
                        { store.removePlaylistEntry(playlist.id, baseline.playlists.single().entries.single().id) },
                        { store.saveQueue(queue.copy(positionMs = 200, repeatMode = "ALL"),
                            DesktopListeningEvent("session-a", "entry-a", track.id, 100, 200, 200, true)) },
                    )
                    operations.forEach { operation ->
                        val failure = try { operation(); null } catch (error: DesktopStorageException) { error }
                        assertEquals("desktop_mutation_failed", failure?.code)
                        assertEquals(baseline, store.state.value)
                        assertEquals(facts, fixture.facts())
                    }
                } finally {
                    fixture.execute("DROP TRIGGER acceptance_abort_outbox")
                }
                fixture.reopen()
                assertEquals(baseline, fixture.store.state.value)
                assertEquals(facts, fixture.facts())
                fixture.store.updateTrackMetadata(track.id, "Successful retry", "", 300)
                assertEquals("Successful retry", fixture.store.state.value.tracks.single().title)
                assertEquals(facts.size + 1, fixture.facts().size)
            }
        }
    }

    @Test fun futureDatabaseVersionIsRejectedWithoutDeletingData(): Unit = runBlocking {
        withTimeout(30_000) {
            Fixture().useStore { fixture ->
                fixture.store.addImportedTrack(fixture.track("track-a", "Retained future data"))
                val before = fixture.store.state.value
                val facts = fixture.facts()
                fixture.closeStore()
                fixture.execute("PRAGMA user_version = 2")
                val failure = try {
                    openDesktopLibraryStore(fixture.root).also { it.close() }
                    null
                } catch (error: DesktopStorageException) { error }
                assertEquals("desktop_database_open_failed", failure?.code)
                assertEquals(2L, fixture.number("PRAGMA user_version"))
                assertEquals(1L, fixture.number("SELECT count(*) FROM desktop_track"))
                assertEquals(facts, fixture.facts())
                // This reset belongs only to this synthetic fixture, proving failed open released its lock.
                fixture.execute("PRAGMA user_version = 1")
                fixture.reopen()
                assertEquals(before, fixture.store.state.value)
            }
        }
    }

    private class Fixture {
        val root: Path = Files.createTempDirectory("autplay-data-acceptance-").toRealPath()
        private val db: Path = root.resolve("desktop-library-v1.db")
        private var opened: DesktopLibraryStore? = null
        val store: DesktopLibraryStore get() = checkNotNull(opened)

        suspend fun useStore(block: suspend (Fixture) -> Unit): Unit {
            try { reopen(); block(this) } finally {
                try { closeStore() } finally {
                    Files.walk(root).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach(Files::delete) }
                }
            }
        }
        suspend fun closeStore(): Unit { opened?.close(); opened = null }
        suspend fun reopen(): Unit { closeStore(); opened = openDesktopLibraryStore(root) }
        fun track(id: String, title: String, bytes: ByteArray = byteArrayOf(1, 2, 3, 4)): DesktopImportedTrack {
            val hash = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
            Files.write(root.resolve("media/$hash.media"), bytes)
            return DesktopImportedTrack(id, title, "", null, hash, bytes.size.toLong(), "$hash.media")
        }
        fun execute(sql: String): Unit = BundledSQLiteDriver().open(db.toString()).use { connection ->
            connection.prepare(sql).use { it.step(); Unit }
        }
        fun number(sql: String): Long = BundledSQLiteDriver().open(db.toString()).use { connection ->
            connection.prepare(sql).use { assertTrue(it.step()); it.getLong(0) }
        }
        fun facts(): Map<String, String> = BundledSQLiteDriver().open(db.toString()).use { connection ->
            connection.prepare("SELECT changeId, payloadJson FROM desktop_local_outbox ORDER BY changeId").use { statement ->
                buildMap { while (statement.step()) put(statement.getText(0), statement.getText(1)) }
            }
        }
    }
}
