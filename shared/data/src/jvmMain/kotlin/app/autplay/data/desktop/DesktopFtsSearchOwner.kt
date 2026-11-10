package app.autplay.data.desktop

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopTextSearchMatches
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.application.desktop.desktopTextSearchExpression
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Replaceable FTS5 over display text, separate from Room and its immutable local facts.
 * The only filename is :memory:. No extension loading, filesystem scan, grant or catalog ID.
 * One mutex owns every connection/statement. Rebuild publishes only a committed replacement.
 * The derived database is capped at 64 MiB; exceeding it fails search, never truncates results.
 * Native steps/sorts are synchronous: cancellation is cooperative between steps/insert batches,
 * not a hard latency guarantee. Full input/results still use O(snapshot) memory like the UI.
 */
class DesktopFtsSearchOwner internal constructor(
    private val openConnection: () -> SQLiteConnection,
) : DesktopTextSearchPort {
    constructor() : this({ BundledSQLiteDriver().open(":memory:") })

    private data class Document(val kind: String, val id: String, val title: String, val artist: String, val description: String)
    private val mutex: Mutex = Mutex()
    private val retired: AtomicBoolean = AtomicBoolean(false)
    private var connection: SQLiteConnection? = null
    private var failedReplacement: SQLiteConnection? = null
    private var indexed: List<Document>? = null

    override suspend fun search(snapshot: DesktopLibrarySnapshot, query: String): DesktopTextSearchMatches =
        withContext(Dispatchers.IO) {
            fun failure(code: String): DesktopTextSearchMatches = DesktopTextSearchMatches(query, true, errorCode = code)
            if (retired.get()) return@withContext failure("desktop_search_closed")
            if (query.length > 256) return@withContext failure("desktop_search_invalid")
            // Ordinary blank browsing does not allocate SQLite or run a MATCH/table scan.
            if (query.isBlank()) return@withContext DesktopTextSearchMatches(query, searched = false)
            val expression = desktopTextSearchExpression(query)
                ?: return@withContext DesktopTextSearchMatches(query, searched = true)
            try {
                mutex.withLock {
                    admit()
                    closeFailedReplacement()
                    if (indexed == null && connection != null) {
                        connection?.close()
                        connection = null
                    }
                    val documents = documents(snapshot)
                    if (indexed != documents || connection == null) replaceIndex(documents)
                    admit()
                    val tracks = ArrayList<String>()
                    val playlists = ArrayList<String>()
                    val seen = HashSet<Pair<String, String>>()
                    checkNotNull(connection).prepare(
                        "SELECT kind, entity_id FROM desktop_text WHERE desktop_text MATCH ? ORDER BY rank, kind, entity_id",
                    ).use { statement ->
                        statement.bindText(1, expression)
                        while (statement.step()) {
                            admit()
                            val kind = statement.getText(0)
                            val id = statement.getText(1)
                            check(seen.size < documents.size && seen.add(kind to id))
                            when (kind) {
                                "TRACK" -> tracks.add(id)
                                "PLAYLIST" -> playlists.add(id)
                                else -> error("desktop_search_invalid_projection")
                            }
                        }
                    }
                    admit()
                    DesktopTextSearchMatches(query, true, tracks.toList(), playlists.toList())
                }
            } catch (failure: CancellationException) {
                throw failure
            } catch (_: Exception) {
                failure(if (retired.get()) "desktop_search_closed" else "desktop_search_failed")
            } catch (_: LinkageError) {
                failure("desktop_search_unavailable")
            }
        }

    override fun retire(): Unit { retired.set(true) }

    override suspend fun close(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        retire()
        mutex.withLock {
            // Retain an uncertain connection for a later close rather than claiming disposal.
            closeFailedReplacement()
            connection?.close()
            connection = null
            indexed = null
        }
    }

    /** Called under the owner mutex before another allocation or final disposal. */
    private fun closeFailedReplacement(): Unit {
        val pending = failedReplacement ?: return
        pending.close()
        failedReplacement = null
    }

    private suspend fun admit(): Unit {
        currentCoroutineContext().ensureActive()
        check(!retired.get()) { "desktop_search_closed" }
    }

    private suspend fun documents(snapshot: DesktopLibrarySnapshot): List<Document> {
        val result = ArrayList<Document>()
        val ids = HashSet<Pair<String, String>>()
        fun append(kind: String, id: String, title: String, artist: String, description: String): Unit {
            require(id.length in 1..128 && ids.add(kind to id))
            require(title.length <= 4096 && artist.length <= 4096 && description.length <= 4096)
            require('\u0000' !in id && '\u0000' !in title && '\u0000' !in artist && '\u0000' !in description)
            result.add(Document(kind, id, title, artist, description))
        }
        for ((index, track) in snapshot.tracks.withIndex()) {
            if (index % 128 == 0) admit()
            append("TRACK", track.id, track.title, track.artist, "")
        }
        for ((index, playlist) in snapshot.playlists.withIndex()) {
            if (index % 128 == 0) admit()
            append("PLAYLIST", playlist.id, playlist.name, "", playlist.description.orEmpty())
        }
        return result
    }

    private suspend fun replaceIndex(documents: List<Document>): Unit {
        admit()
        val replacement = openConnection()
        var transaction = false
        try {
            replacement.execSQL("PRAGMA page_size=4096")
            replacement.execSQL("PRAGMA max_page_count=16384")
            replacement.execSQL("PRAGMA cache_size=-8192")
            replacement.execSQL("PRAGMA temp_store=MEMORY")
            replacement.execSQL("PRAGMA journal_mode=MEMORY")
            replacement.execSQL("BEGIN")
            transaction = true
            replacement.execSQL(
                "CREATE VIRTUAL TABLE desktop_text USING fts5(kind UNINDEXED, entity_id UNINDEXED, title, artist, description, tokenize='unicode61 remove_diacritics 2', prefix='2 3')",
            )
            replacement.prepare("INSERT INTO desktop_text(kind, entity_id, title, artist, description) VALUES(?, ?, ?, ?, ?)").use { insert ->
                for ((index, document) in documents.withIndex()) {
                    if (index % 128 == 0) admit()
                    insert.bindText(1, document.kind)
                    insert.bindText(2, document.id)
                    insert.bindText(3, document.title)
                    insert.bindText(4, document.artist)
                    insert.bindText(5, document.description)
                    check(!insert.step())
                    insert.reset()
                    insert.clearBindings()
                }
            }
            admit()
            replacement.execSQL("COMMIT")
            transaction = false
            admit()
            // Metadata-only queue/checkpoint changes have identical documents and reuse this index.
            try { connection?.close() }
            catch (failure: Throwable) { indexed = null; throw failure }
            connection = replacement
            indexed = documents
        } catch (failure: Throwable) {
            // Resource cleanup must not replace cancellation or the original construction error.
            try { if (transaction) replacement.execSQL("ROLLBACK") }
            catch (cleanup: Throwable) { failure.addSuppressed(cleanup) }
            try { replacement.close() }
            catch (cleanup: Throwable) {
                failedReplacement = replacement
                failure.addSuppressed(cleanup)
            }
            throw failure
        }
    }
}
