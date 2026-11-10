package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopLocalStatistics
import app.autplay.application.desktop.DesktopLocalTopArtistLabel
import app.autplay.application.desktop.DesktopLocalTopTrack
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import java.nio.file.Files
import java.nio.file.Path
import java.util.Comparator
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail

/** Source fixtures invoked by the existing first persistence composite, never a new test entry. */
internal suspend fun desktopLocalStatisticsAssertions(): Unit {
    withLocalStatisticsFixture { fixture ->
        val empty = DesktopLocalStatistics(5_000, 0, 0, emptyList(), emptyList())
        assertEquals(empty, fixture.store.readLocalStatistics(5_000))
        fixture.seedRankings()
        val expected = DesktopLocalStatistics(
            5_000, 2_975, 1_216,
            listOf(
                DesktopLocalTopTrack("b-repeat", "Same title", "Alpha", 2, 1_400),
                DesktopLocalTopTrack("a-bulk", "Same title", " Alpha ", 1_205, 1_205),
                DesktopLocalTopTrack("c-exact", "Same title", " Beta ", 2, 100),
                DesktopLocalTopTrack("d-exact", "Same title", "Beta", 2, 100),
                DesktopLocalTopTrack("e-single", "Same title", "Gamma", 1, 100),
            ),
            listOf(
                DesktopLocalTopArtistLabel("Alpha", 1_207, 2_605),
                DesktopLocalTopArtistLabel("Beta", 4, 200),
                DesktopLocalTopArtistLabel("Gamma", 1, 100),
                DesktopLocalTopArtistLabel("Delta", 1, 20),
                DesktopLocalTopArtistLabel("Epsilon", 1, 20),
            ),
        )
        // The published empty preview is deliberately stale. Reads must use all saved rows.
        val published = fixture.store.state.value
        val rows = fixture.rows()
        assertEquals(expected, fixture.store.readLocalStatistics(5_000))
        assertSame(published, fixture.store.state.value)
        assertEquals(rows, fixture.rows())
        assertEquals(empty.copy(throughInclusiveMs = 0), fixture.store.readLocalStatistics(0))
        assertEquals(1_205L, fixture.store.readLocalStatistics(1_205).listenedMs)
        assertEquals(1_905L, fixture.store.readLocalStatistics(2_500).listenedMs)
        // The future Long.MAX_VALUE listen never affects an earlier cutoff, even aggregate overflow.
        statisticsFailure("desktop_statistics_read_failed") { fixture.store.readLocalStatistics(5_001) }
        assertEquals(expected, fixture.store.readLocalStatistics(5_000))
        assertSame(published, fixture.store.state.value)

        // Repeated checkpoints update ONE saved active listen; their increments are not new plays.
        val checkpoint = DesktopListeningEvent("repeat-one", "repeat-entry", "b-repeat", 2_500, 900, 900,
            false, "EXCLUDED", "INCLUDED")
        val queue = DesktopQueue("statistics-queue", listOf(DesktopQueueEntry("repeat-entry", "b-repeat")),
            "repeat-entry", positionMs = 900, sessionId = checkpoint.id, sessionStartedAtMs = checkpoint.startedAtMs,
            playedMs = 900, sessionTasteState = "EXCLUDED")
        fixture.store.saveQueue(queue, checkpoint)
        fixture.store.saveQueue(queue, checkpoint)
        val checkpointExpected = expected.copy(
            listenedMs = 3_175,
            topTracks = expected.topTracks.map { if (it.trackId == "b-repeat") it.copy(listenedMs = 1_600) else it },
            topArtistLabels = expected.topArtistLabels.map { if (it.artistLabel == "Alpha") it.copy(listenedMs = 2_805) else it },
        )
        val checkpointRows = fixture.rows()
        val checkpointState = fixture.store.state.value
        assertEquals(checkpointExpected, fixture.store.readLocalStatistics(5_000))
        assertSame(checkpointState, fixture.store.state.value)
        assertEquals(checkpointRows, fixture.rows())
        fixture.reopen()
        assertEquals(DesktopHistoryLimits.PREVIEW_SIZE, fixture.store.state.value.history.size)
        assertEquals(checkpointExpected, fixture.store.readLocalStatistics(5_000))

        // Collection membership and taste never remove private saved history from the totals.
        fixture.execute("INSERT INTO desktop_track_collection VALUES ('b-repeat', 'REMOVED')")
        assertEquals(checkpointExpected, fixture.store.readLocalStatistics(5_000))
        // Current raw text is read alongside aggregates, not from the stale published collection.
        val beforeTextRead = fixture.store.state.value
        fixture.text("b-repeat", "Current title", " Beta ")
        val current = fixture.store.readLocalStatistics(5_000)
        assertEquals(3_175L, current.listenedMs)
        assertEquals(1_216L, current.playSessionCount)
        assertEquals(DesktopLocalTopTrack("b-repeat", "Current title", " Beta ", 2, 1_600), current.topTracks.first())
        assertEquals(listOf(
            DesktopLocalTopArtistLabel("Beta", 6, 1_800),
            DesktopLocalTopArtistLabel("Alpha", 1_205, 1_205),
            DesktopLocalTopArtistLabel("Gamma", 1, 100),
            DesktopLocalTopArtistLabel("Delta", 1, 20),
            DesktopLocalTopArtistLabel("Epsilon", 1, 20),
        ), current.topArtistLabels)
        assertSame(beforeTextRead, fixture.store.state.value)
        assertEquals(11L, fixture.number("SELECT COUNT(DISTINCT sha256) FROM desktop_track"))
        assertEquals(1_218L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))
    }

    localStatisticsFailuresAndBounds()
    localStatisticsNonTopMetadata()
}

private suspend fun localStatisticsFailuresAndBounds(): Unit = withLocalStatisticsFixture { fixture ->
    fixture.insertTrack("only", "Title", "Artist", 1)
    fixture.insertTrack("other", "Other", "Other", 2)
    fixture.insertEvents(listOf(DesktopListeningEvent("one", "entry", "only", 1, 1, 1, false)))
    val baseline = fixture.store.readLocalStatistics(1)
    val published = fixture.store.state.value
    val initialRows = fixture.rows()
    listOf(-1L, Long.MIN_VALUE).forEach { cutoff ->
        statisticsFailure("desktop_statistics_cutoff_invalid") { fixture.store.readLocalStatistics(cutoff) }
    }
    val cancelled = Job()
    var propagated = false
    try {
        withContext(cancelled) {
            cancelled.cancel()
            fixture.store.readLocalStatistics(1)
        }
    } catch (_: CancellationException) { propagated = true }
    assertTrue(propagated)
    assertSame(published, fixture.store.state.value)
    assertEquals(initialRows, fixture.rows())

    // Integer overflow across different tracks, then within one track, must fail instead of wrap.
    fixture.insertEvents(listOf(DesktopListeningEvent("overflow", "other-entry", "other", 1, Long.MAX_VALUE, 0, true)))
    statisticsFailure("desktop_statistics_read_failed") { fixture.store.readLocalStatistics(1) }
    fixture.execute("UPDATE desktop_listening SET trackId = 'only' WHERE id = 'overflow'")
    statisticsFailure("desktop_statistics_read_failed") { fixture.store.readLocalStatistics(1) }
    fixture.execute("DELETE FROM desktop_listening WHERE id = 'overflow'")
    assertEquals(baseline, fixture.store.readLocalStatistics(1))

    // SQLite's permissive affinity must not silently coerce corrupt durations or timestamps.
    listOf("playedMs = -1", "playedMs = 0.5", "playedMs = 'broken'", "startedAtMs = -1",
        "startedAtMs = 0.5", "startedAtMs = 'broken'", "positionMs = -1", "positionMs = 0.5",
        "finalized = 2", "id = ''", "id = char(0)", "queueEntryId = 'bad/id'",
        "queueEntryId = 'entry' || char(0)", "trackId = 'missing'").forEach { assignment ->
        fixture.execute("UPDATE desktop_listening SET $assignment")
        val corrupted = fixture.rows()
        statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(1) }
        assertEquals(corrupted, fixture.rows())
        assertSame(published, fixture.store.state.value)
        fixture.execute("DELETE FROM desktop_listening")
        fixture.insertEvents(listOf(DesktopListeningEvent("one", "entry", "only", 1, 1, 1, false)))
        assertEquals(baseline, fixture.store.readLocalStatistics(1))
    }

    // UTF-16 metadata limits match desktop commands, including supplementary characters.
    listOf("x".repeat(4_096), "\uD83C\uDFB5".repeat(2_048)).forEach { maximum ->
        fixture.text("only", maximum, maximum)
        val bounded = fixture.store.readLocalStatistics(1)
        assertEquals(maximum, bounded.topTracks.single().title)
        assertEquals(maximum, bounded.topArtistLabels.single().artistLabel)
    }
    listOf("x".repeat(4_097), "\uD83C\uDFB5".repeat(2_049), "", "   ", "bad\u0000title").forEach { title ->
        fixture.text("only", title, "Artist")
        statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(1) }
    }
    listOf("x".repeat(4_097), "\uD83C\uDFB5".repeat(2_049), "bad\u0000artist").forEach { artist ->
        fixture.text("only", "Title", artist)
        statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(1) }
    }
    fixture.text("only", "Title", "   ")
    assertTrue(fixture.store.readLocalStatistics(1).topArtistLabels.isEmpty())
    fixture.text("only", "Title", "Artist")

    fixture.execute("ALTER TABLE desktop_listening RENAME TO acceptance_statistics_retained")
    try {
        val unavailable = fixture.rows()
        statisticsFailure("desktop_statistics_read_failed") { fixture.store.readLocalStatistics(1) }
        assertEquals(unavailable, fixture.rows())
        assertSame(published, fixture.store.state.value)
    } finally { fixture.execute("ALTER TABLE acceptance_statistics_retained RENAME TO desktop_listening") }
    assertEquals(baseline, fixture.store.readLocalStatistics(1))
    assertEquals(initialRows, fixture.rows())

    // A failed ordinary statistics read must not latch the store or prevent a subsequent mutation.
    fixture.store.updateTrackTextMetadata("only", "Writable after read failure", "Artist")
    assertEquals("Writable after read failure", fixture.store.readLocalStatistics(1).topTracks.single().title)
    val lastPublished = fixture.store.state.value
    fixture.execute("ALTER TABLE desktop_playlist RENAME TO acceptance_statistics_playlist")
    try {
        statisticsFailure("desktop_snapshot_unavailable") {
            fixture.store.updateTrackMetadata("only", "Committed before snapshot failure", "Artist", null)
        }
        val unavailable = fixture.rows()
        statisticsFailure("desktop_snapshot_unavailable") { fixture.store.readLocalStatistics(1) }
        statisticsFailure("desktop_snapshot_unavailable") { fixture.store.readLocalStatistics(-1) }
        assertSame(lastPublished, fixture.store.state.value)
        assertEquals(unavailable, fixture.rows())
    } finally { fixture.execute("ALTER TABLE acceptance_statistics_playlist RENAME TO desktop_playlist") }
    statisticsFailure("desktop_snapshot_unavailable") { fixture.store.readLocalStatistics(1) }
    fixture.reopen()
    assertEquals("Committed before snapshot failure", fixture.store.readLocalStatistics(1).topTracks.single().title)
    val closed = fixture.store
    fixture.close()
    statisticsFailure("desktop_store_closed") { closed.readLocalStatistics(1) }
    statisticsFailure("desktop_store_closed") { closed.readLocalStatistics(-1) }
}

private suspend fun localStatisticsNonTopMetadata(): Unit = withLocalStatisticsFixture { fixture ->
    // 106 exact tracks cross the 100-row metadata page; initial rank six is outside both top fives.
    val ids = (0 until 106).map { "track-${it.toString().padStart(3, '0')}" }
    ids.forEachIndexed { index, id -> fixture.insertTrack(id, "Title $index", "Artist $index", index) }
    fixture.insertEvents(ids.mapIndexed { index, id ->
        val playedMs = 106L - index
        DesktopListeningEvent("listen-$index", "entry-$index", id, if (index == 5) 2L else 1L,
            playedMs, playedMs, true)
    })
    val baseline = DesktopLocalStatistics(
        2, 5_671, 106,
        (0 until 5).map { DesktopLocalTopTrack(ids[it], "Title $it", "Artist $it", 1, 106L - it) },
        (0 until 5).map { DesktopLocalTopArtistLabel("Artist $it", 1, 106L - it) },
    )
    val earlier = baseline.copy(throughInclusiveMs = 1, listenedMs = 5_570, playSessionCount = 105)
    val published = fixture.store.state.value
    assertEquals(baseline, fixture.store.readLocalStatistics(2))
    assertEquals(earlier, fixture.store.readLocalStatistics(1))

    // Invalid sixth-place labels must fail before totals, independently of ranking visibility.
    // These blank/UTF-16 cases fit SQLite's code-point and stored-byte scalar bounds.
    listOf("", "   ", "\u00A0\u2003\u202F", "\uD83C\uDFB5".repeat(2_049)).forEach { title ->
        fixture.text(ids[5], title, "Artist 5")
        val rows = fixture.rows()
        statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(2) }
        assertEquals(earlier, fixture.store.readLocalStatistics(1))
        assertSame(published, fixture.store.state.value)
        assertEquals(rows, fixture.rows())
    }
    fixture.text(ids[5], "Title 5", "\uD83C\uDFB5".repeat(2_049))
    val badArtistRows = fixture.rows()
    statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(2) }
    assertEquals(earlier, fixture.store.readLocalStatistics(1))
    assertEquals(badArtistRows, fixture.rows())
    fixture.text(ids[5], "Title 5", "Artist 5")

    // A corrupt row in the second keyset page must also refuse the entire snapshot.
    listOf("", "\u2003", "\uD83C\uDFB5".repeat(2_049)).forEach { title ->
        fixture.text(ids.last(), title, "Artist 105")
        val rows = fixture.rows()
        statisticsFailure("desktop_statistics_persisted_invalid") { fixture.store.readLocalStatistics(2) }
        assertSame(published, fixture.store.state.value)
        assertEquals(rows, fixture.rows())
    }
    fixture.text(ids.last(), "Title 105", "Artist 105")

    // Valid supplementary text at exactly 4096 UTF-16 units remains accepted outside the top five.
    listOf("\uD83C\uDFB5".repeat(2_048), "A" + "\uD83C\uDFB5".repeat(2_047) + "B").forEach { maximum ->
        fixture.text(ids[5], maximum, maximum)
        val rows = fixture.rows()
        assertEquals(baseline, fixture.store.readLocalStatistics(2))
        assertEquals(earlier, fixture.store.readLocalStatistics(1))
        assertEquals(rows, fixture.rows())
        // Entering the ranking changes visibility, not metadata acceptance or the session count.
        fixture.execute("UPDATE desktop_listening SET playedMs = 107 WHERE id = 'listen-5'")
        val promoted = fixture.store.readLocalStatistics(2)
        assertEquals(5_677L, promoted.listenedMs)
        assertEquals(106L, promoted.playSessionCount)
        assertEquals(DesktopLocalTopTrack(ids[5], maximum, maximum, 1, 107), promoted.topTracks.first())
        assertEquals(DesktopLocalTopArtistLabel(maximum, 1, 107), promoted.topArtistLabels.first())
        fixture.execute("UPDATE desktop_listening SET playedMs = 101 WHERE id = 'listen-5'")
    }
    listOf("", "   ", "\u00A0\u2003\u202F").forEach { artist ->
        fixture.text(ids[5], "Title 5", artist)
        assertEquals(baseline, fixture.store.readLocalStatistics(2))
    }
    fixture.text(ids[5], "Title 5", "Artist 5")
    assertEquals(baseline, fixture.store.readLocalStatistics(2))
    assertEquals(earlier, fixture.store.readLocalStatistics(1))
    assertSame(published, fixture.store.state.value)
    assertEquals(106L, fixture.number("SELECT COUNT(*) FROM desktop_track"))
    assertEquals(106L, fixture.number("SELECT COUNT(*) FROM desktop_listening"))
}

private suspend fun statisticsFailure(code: String, action: suspend () -> Any?): Unit {
    val failure = try { action(); null } catch (error: DesktopStorageException) { error }
    if (failure == null) fail("Expected $code")
    assertEquals(code, failure?.code)
    assertEquals(code, failure?.message)
    assertNull(failure?.cause)
    assertTrue(checkNotNull(failure).suppressed.isEmpty())
}

/** All SQL and cleanup here target a newly created disposable directory; no managed media is read. */
private class LocalStatisticsFixture(val root: Path) {
    private val database: Path = root.resolve("desktop-library-v1.db")
    private var opened: DesktopLibraryStore? = null
    val store: DesktopLibraryStore get() = checkNotNull(opened)

    suspend fun reopen(): Unit { close(); opened = openDesktopLibraryStore(root) }
    suspend fun close(): Unit { opened?.close(); opened = null }

    private fun <T> connection(action: (SQLiteConnection) -> T): T =
        BundledSQLiteDriver().open(database.toString()).use { connection ->
            // Room can still be completing asynchronous invalidation after an awaited DAO call.
            // Bound contention on this disposable fixture connection; persistent locks still fail.
            connection.prepare("PRAGMA busy_timeout=1000").use { it.step() }
            connection.prepare("PRAGMA busy_timeout").use {
                assertTrue(it.step()); assertEquals(1_000L, it.getLong(0)); assertTrue(!it.step())
            }
            action(connection)
        }

    fun execute(sql: String): Unit = connection { connection ->
        connection.prepare(sql).use { it.step(); Unit }
    }

    fun number(sql: String): Long = connection { connection ->
        connection.prepare(sql).use { check(it.step()); it.getLong(0) }
    }

    fun insertTrack(id: String, title: String, artist: String, hashIndex: Int): Unit = connection { connection ->
        connection.prepare("INSERT INTO desktop_track (id, title, artist, durationMs, sha256, byteSize) " +
            "VALUES (?, ?, ?, NULL, ?, 1)").use {
            it.bindText(1, id); it.bindText(2, title); it.bindText(3, artist)
            it.bindText(4, hashIndex.toString(16).padStart(64, '0')); it.step(); Unit
        }
    }

    fun text(id: String, title: String, artist: String): Unit = connection { connection ->
        connection.prepare("UPDATE desktop_track SET title = ?, artist = ? WHERE id = ?").use {
            it.bindText(1, title); it.bindText(2, artist); it.bindText(3, id); it.step(); Unit
        }
    }

    fun insertEvents(events: List<DesktopListeningEvent>): Unit = connection { connection ->
        require(events.size <= 200)
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

    fun seedRankings(): Unit {
        val labels = linkedMapOf("a-bulk" to " Alpha ", "b-repeat" to "Alpha", "c-exact" to " Beta ",
            "d-exact" to "Beta", "e-single" to "Gamma", "f-zero" to "   ", "g-future" to "Future",
            "h-case" to "alpha", "i-extra" to "Delta", "j-extra" to "Epsilon", "k-extra" to "Zeta")
        labels.entries.forEachIndexed { index, (id, artist) -> insertTrack(id, "Same title", artist, index) }
        // Six full batches plus a tail exceed both the 100 preview and 1,000 visible-history caps.
        (1..1_205).chunked(200).forEach { batch ->
            insertEvents(batch.map { index ->
                DesktopListeningEvent("bulk-$index", "bulk-entry-$index", "a-bulk", index.toLong(), 1, 1,
                    true, "INCLUDED", "EXCLUDED")
            })
        }
        insertEvents(listOf(
            DesktopListeningEvent("repeat-one", "repeat-entry", "b-repeat", 2_500, 700, 700, false, "EXCLUDED", "INCLUDED"),
            DesktopListeningEvent("repeat-two", "repeat-other", "b-repeat", 2_501, 700, 700, true),
            DesktopListeningEvent("c-one", "c-entry", "c-exact", 2_600, 50, 50, true),
            DesktopListeningEvent("c-two", "c-entry", "c-exact", 2_601, 50, 50, true),
            DesktopListeningEvent("d-one", "d-entry", "d-exact", 2_600, 50, 50, true),
            DesktopListeningEvent("d-two", "d-entry", "d-exact", 2_601, 50, 50, true),
            DesktopListeningEvent("e-one", "e-entry", "e-single", 3_000, 100, 100, true),
            DesktopListeningEvent("zero", "zero-entry", "f-zero", 0, 0, 0, false),
            DesktopListeningEvent("future", "future-entry", "g-future", 5_001, Long.MAX_VALUE, 0, false),
            DesktopListeningEvent("case", "case-entry", "h-case", 3_000, 10, 10, true),
            DesktopListeningEvent("extra-i", "extra-entry-i", "i-extra", 3_000, 20, 20, true),
            DesktopListeningEvent("extra-j", "extra-entry-j", "j-extra", 3_000, 20, 20, true),
            DesktopListeningEvent("extra-k", "extra-entry-k", "k-extra", 3_000, 20, 20, true),
        ))
    }

    /** Lossless before/after evidence includes outbox, queue, unknown values and fixture renames. */
    fun rows(): Map<String, List<List<String>>> = connection { connection ->
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
}

private suspend fun withLocalStatisticsFixture(block: suspend (LocalStatisticsFixture) -> Unit): Unit {
    val root = Files.createTempDirectory("autplay-statistics-").toRealPath()
    val fixture = LocalStatisticsFixture(root)
    var originalFailure: Throwable? = null
    try {
        fixture.reopen()
        block(fixture)
    } catch (failure: Throwable) {
        originalFailure = failure
        throw failure
    } finally {
        withContext(NonCancellable) {
            try {
                withTimeout(5_000) { fixture.close() }
                check(!Files.isSymbolicLink(root) && root.toRealPath() == root) { "Unsafe statistics fixture root" }
                val paths = Files.walk(root).use { it.sorted(Comparator.reverseOrder()).toList() }
                check(paths.all { it.toAbsolutePath().normalize().startsWith(root) }) { "Unsafe statistics fixture entry" }
                paths.forEach { Files.deleteIfExists(it) }
                check(!Files.exists(root)) { "Statistics fixture cleanup incomplete" }
            } catch (cleanupFailure: Throwable) {
                val failure = originalFailure
                if (failure == null) throw cleanupFailure else failure.addSuppressed(cleanupFailure)
            }
        }
    }
}
