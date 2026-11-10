package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopTrack
import kotlinx.coroutines.CancellationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail

/** Called only by an existing persistence composite after integration; never a new test entry. */
internal suspend fun desktopFtsSearchAssertions(): Unit {
    var unopenedCalls = 0
    val unopened = DesktopFtsSearchOwner {
        unopenedCalls++
        error("unexpected_sqlite_open")
    }
    assertFalse(unopened.search(DesktopLibrarySnapshot(), "  ").searched)
    assertTrue(unopened.search(DesktopLibrarySnapshot(), "\" -* ()").trackIds.isEmpty())
    assertEquals(null, unopened.search(DesktopLibrarySnapshot(), "\" -* ()").errorCode)
    assertEquals("desktop_search_invalid", unopened.search(DesktopLibrarySnapshot(), "x".repeat(257)).errorCode)
    assertEquals(0, unopenedCalls)
    unopened.retire()
    assertEquals("desktop_search_closed", unopened.search(DesktopLibrarySnapshot(), "needle").errorCode)
    unopened.close()

    val tracks = List(1_205) { index ->
        DesktopTrack("track-${index.toString().padStart(4, '0')}", "needle", "", null, "0".repeat(64), 1)
    } + DesktopTrack("accent", "Beyonc\u00e9", "\u0422\u0438\u0448\u0438\u043d\u0430", null, "1".repeat(64), 1)
    val snapshot = DesktopLibrarySnapshot(tracks, listOf(DesktopPlaylist("playlist", "List", emptyList(), "needle diary")))
    var opens = 0
    val owner = DesktopFtsSearchOwner { opens++; BundledSQLiteDriver().open(":memory:") }
    try {
        val matches = owner.search(snapshot, "needle")
        assertEquals(null, matches.errorCode)
        assertEquals(tracks.dropLast(1).map { it.id }, matches.trackIds)
        assertEquals(listOf("playlist"), matches.playlistIds)
        assertEquals(listOf("accent"), owner.search(snapshot, "beyonce").trackIds)
        assertEquals(listOf("accent"), owner.search(snapshot, "\u0442\u0438\u0448").trackIds)
        assertEquals(matches.trackIds, owner.search(snapshot, "\"needle\" *").trackIds)
        val operatorWords = owner.search(snapshot, "needle OR")
        assertEquals(null, operatorWords.errorCode)
        assertTrue(operatorWords.trackIds.isEmpty())
        assertEquals(1, opens)
        // Queue/history-only snapshots do not rebuild an equal text projection.
        owner.search(snapshot.copy(history = emptyList()), "needle")
        assertEquals(1, opens)
        val changed = snapshot.copy(tracks = tracks.map { if (it.id == "accent") it.copy(title = "renamed") else it })
        assertTrue(owner.search(changed, "beyonce").trackIds.isEmpty())
        assertEquals(listOf("accent"), owner.search(changed, "renamed").trackIds)
        assertEquals(2, opens)
        val invalid = changed.copy(tracks = changed.tracks + changed.tracks.first())
        assertEquals("desktop_search_failed", owner.search(invalid, "needle").errorCode)
        assertEquals(matches.trackIds, owner.search(changed, "needle").trackIds)
        assertEquals(2, opens)
    } finally { owner.close() }
    assertEquals("desktop_search_closed", owner.search(snapshot, "needle").errorCode)
    owner.close()

    // A cancelled replacement must roll back/close, preserve cancellation and permit a clean retry.
    var cancelReplacement = false
    var rollbackObserved = false
    var cancelledConnectionClosed = false
    var factoryCalls = 0
    val cancelling = DesktopFtsSearchOwner {
        factoryCalls++
        val real = BundledSQLiteDriver().open(":memory:")
        if (factoryCalls != 2) real else object : SQLiteConnection by real {
            override fun prepare(sql: String): SQLiteStatement {
                if (sql == "ROLLBACK") rollbackObserved = true
                val statement = real.prepare(sql)
                if (!sql.startsWith("INSERT INTO desktop_text")) return statement
                return object : SQLiteStatement by statement {
                    private var inserted: Int = 0
                    override fun step(): Boolean {
                        if (cancelReplacement && ++inserted == 129) throw CancellationException("synthetic_rebuild_cancel")
                        return statement.step()
                    }
                }
            }
            override fun close(): Unit { cancelledConnectionClosed = true; real.close() }
        }
    }
    try {
        assertEquals(null, cancelling.search(snapshot, "needle").errorCode)
        cancelReplacement = true
        val changed = snapshot.copy(tracks = tracks.map { if (it.id == "accent") it.copy(title = "changed") else it })
        try { cancelling.search(changed, "needle"); fail("Cancellation must be preserved") }
        catch (_: CancellationException) { }
        assertTrue(rollbackObserved)
        assertTrue(cancelledConnectionClosed)
        // The old projection is still available; no third connection is needed to read it.
        assertEquals(1_205, cancelling.search(snapshot, "needle").trackIds.size)
        assertEquals(2, factoryCalls)
        cancelReplacement = false
        assertEquals(listOf("accent"), cancelling.search(changed, "changed").trackIds)
        assertEquals(3, factoryCalls)
    } finally { cancelling.close() }

    // A failed replacement close blocks further allocation and cached reads until actual cleanup.
    for (finishByClose in listOf(false, true)) {
        var replacementOpens = 0
        var cleanupAttempts = 0
        var allowCleanup = false
        var replacementClosed = false
        val retained = DesktopFtsSearchOwner {
            replacementOpens++
            val real = BundledSQLiteDriver().open(":memory:")
            if (replacementOpens != 2) real else object : SQLiteConnection by real {
                override fun prepare(sql: String): SQLiteStatement {
                    if (sql.startsWith("INSERT INTO desktop_text")) error("synthetic_index_failure")
                    return real.prepare(sql)
                }
                override fun close(): Unit {
                    cleanupAttempts++
                    if (!allowCleanup) error("synthetic_replacement_close_failure")
                    real.close()
                    replacementClosed = true
                }
            }
        }
        try {
            assertEquals(1_205, retained.search(snapshot, "needle").trackIds.size)
            val changed = snapshot.copy(tracks = tracks.map { if (it.id == "accent") it.copy(title = "changed") else it })
            assertEquals("desktop_search_failed", retained.search(changed, "needle").errorCode)
            assertEquals(1, cleanupAttempts)
            assertEquals("desktop_search_failed", retained.search(snapshot, "needle").errorCode)
            assertEquals("desktop_search_failed", retained.search(changed, "needle").errorCode)
            assertEquals(2, replacementOpens)
            assertFalse(replacementClosed)
            if (finishByClose) {
                try { retained.close(); fail("Unsettled connection cannot report disposal") }
                catch (_: IllegalStateException) { }
                assertFalse(replacementClosed)
                assertEquals(2, replacementOpens)
            }
            allowCleanup = true
            if (!finishByClose) {
                assertEquals(1_205, retained.search(snapshot, "needle").trackIds.size)
                assertTrue(replacementClosed)
                assertEquals(2, replacementOpens)
                assertEquals(listOf("accent"), retained.search(changed, "changed").trackIds)
                assertEquals(3, replacementOpens)
            }
        } finally {
            allowCleanup = true
            retained.close()
        }
        assertTrue(replacementClosed)
        assertEquals("desktop_search_closed", retained.search(snapshot, "needle").errorCode)
    }

    // Driver availability is an explicit search result; retry can replace a failed lazy open.
    val unavailable = DesktopFtsSearchOwner { throw UnsatisfiedLinkError("fixture_missing_driver") }
    assertEquals("desktop_search_unavailable", unavailable.search(snapshot, "needle").errorCode)
    assertFalse(unavailable.search(snapshot, "").searched)
    unavailable.close()
    var failOpen = true
    val retryable = DesktopFtsSearchOwner {
        if (failOpen) error("fixture_open_failure") else BundledSQLiteDriver().open(":memory:")
    }
    try {
        assertEquals("desktop_search_failed", retryable.search(snapshot, "needle").errorCode)
        assertFalse(retryable.search(snapshot, " ").searched)
        failOpen = false
        assertEquals(1_205, retryable.search(snapshot, "needle").trackIds.size)
    } finally { retryable.close() }

    // Retirement during a native step cannot publish a late success or race connection close.
    lateinit var retiring: DesktopFtsSearchOwner
    var retiredConnectionClosed = false
    retiring = DesktopFtsSearchOwner {
        val real = BundledSQLiteDriver().open(":memory:")
        object : SQLiteConnection by real {
            override fun prepare(sql: String): SQLiteStatement {
                val statement = real.prepare(sql)
                if (!sql.startsWith("SELECT kind")) return statement
                return object : SQLiteStatement by statement {
                    override fun step(): Boolean { retiring.retire(); return statement.step() }
                }
            }
            override fun close(): Unit { retiredConnectionClosed = true; real.close() }
        }
    }
    try {
        assertEquals("desktop_search_closed", retiring.search(snapshot, "needle").errorCode)
        assertFalse(retiredConnectionClosed)
    } finally { retiring.close() }
    assertTrue(retiredConnectionClosed)
}
