package app.autplay.data.sourceaccess

import androidx.sqlite.SQLiteConnection
import androidx.sqlite.SQLiteStatement
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.execSQL
import app.autplay.application.sourceaccess.*
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.attribute.PosixFileAttributes
import java.nio.file.attribute.PosixFilePermissions
import java.util.UUID

/** No configurable data root. Native lifetime owns the single canonical installation lock. */
public fun createSourceAccessIndex(native: SourceAccessNative): SourceAccessIndex = SourceAccessSqliteIndex(native)

/** Separate UNENCRYPTED schema1; no Room, account or settings/export dependency. */
private class SourceAccessSqliteIndex(private val native: SourceAccessNative) : SourceAccessIndex {
    private var connection: SQLiteConnection? = null
    private var database: Path? = null
    private var attempted: Boolean = false
    private var usable: Boolean = false
    private var uncertain: Boolean = false
    private var identity: Any? = null

    @Synchronized override fun initialize(): Unit {
        if (usable) return
        need(!attempted, "source_index_reopen_required"); attempted = true
        native.verifyPrivateIndex()
        val home = Path.of(System.getProperty("user.home")).toAbsolutePath().normalize()
        need(home == home.toRealPath(), "source_index_unavailable")
        val path = home.resolve("Library/Application Support/AutPlay/source-access-v1/source-access-v1.db")
        need(path.toString() == native.canonicalIndexPath(), "source_index_unavailable")
        database = path
        val exists = Files.exists(path, NOFOLLOW_LINKS)
        if (exists) { file(path); need(Files.size(path) > 0, "source_index_unsupported") }
        else {
            budget(1_048_576)
            Files.createFile(path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
        }
        identity = file(path).fileKey() ?: fail("source_index_unavailable")
        val opened = BundledSQLiteDriver().open(path.toString()); connection = opened
        // Unknown databases are retained before any persistent pragma/table change.
        if (exists) {
            need(number("PRAGMA application_id") == APPLICATION_ID && number("PRAGMA user_version") == 1L,
                "source_index_unsupported")
            query("SELECT store_id,version FROM source_store WHERE singleton=1") {
                need(canonicalUuid(it.getText(0)) && it.getLong(1) == 1L, "source_index_unsupported")
            }.also { need(it.size == 1, "source_index_unsupported") }
        } else opened.execSQL("PRAGMA page_size=4096")
        opened.execSQL("PRAGMA busy_timeout=5000")
        opened.execSQL("PRAGMA temp_store=MEMORY")
        opened.execSQL("PRAGMA synchronous=FULL")
        opened.execSQL("PRAGMA journal_mode=WAL")
        opened.execSQL("PRAGMA max_page_count=8192")
        opened.execSQL("PRAGMA wal_autocheckpoint=256")
        if (!exists) transaction {
            opened.execSQL("CREATE TABLE source_store(singleton INTEGER PRIMARY KEY CHECK(singleton=1),store_id TEXT NOT NULL,version INTEGER NOT NULL)")
            opened.execSQL("""CREATE TABLE source_locator(source_id TEXT PRIMARY KEY NOT NULL,record_version INTEGER NOT NULL,
                revision INTEGER NOT NULL,selected_kind TEXT NOT NULL,enabled INTEGER NOT NULL,label TEXT NOT NULL,
                resource_identity BLOB NOT NULL,bookmark BLOB NOT NULL)""")
            execute("INSERT INTO source_store VALUES(1,?,1)", UUID.randomUUID().toString())
            opened.execSQL("PRAGMA application_id=0x41504731")
            opened.execSQL("PRAGMA user_version=1")
        }
        budget(0); usable = true
    }

    @Synchronized override fun list(): List<SourceLocator> {
        ready(); val rows = query("SELECT * FROM source_locator ORDER BY source_id LIMIT 257", mapper = ::row)
        need(rows.size <= SourceAccessLimits.SOURCES, "source_index_unsupported"); return rows
    }
    @Synchronized override fun load(id: String): SourceLocator? {
        ready(); need(canonicalUuid(id), "source_selection_invalid")
        return query("SELECT * FROM source_locator WHERE source_id=?", id, mapper = ::row).singleOrNull()
    }
    @Synchronized override fun remember(kind: SourceKind, label: String, identity: ByteArray, bookmark: ByteArray): SourceLocator {
        ready(); fields(label, identity, bookmark)
        val result = SourceLocator(UUID.randomUUID().toString(), 1, 1, kind.name, true, label, identity, bookmark)
        transaction {
            need(number("SELECT count(*) FROM source_locator") < SourceAccessLimits.SOURCES, "source_index_full")
            execute("INSERT INTO source_locator VALUES(?,1,1,?,1,?,?,?)", result.id, kind.name, label, identity, bookmark)
        }
        return result
    }
    @Synchronized override fun replace(expected: SourceLocator, kind: SourceKind, label: String, identity: ByteArray, bookmark: ByteArray): SourceLocator {
        ready(); fields(label, identity, bookmark)
        val result = SourceLocator(expected.id, 1, next(expected), kind.name, expected.enabled, label, identity, bookmark)
        transaction {
            exact(expected)
            execute("UPDATE source_locator SET revision=?,selected_kind=?,label=?,resource_identity=?,bookmark=? WHERE source_id=?",
                result.revision, kind.name, label, identity, bookmark, expected.id)
        }
        return result
    }
    @Synchronized override fun setEnabled(expected: SourceLocator, enabled: Boolean): SourceLocator {
        ready(); val revision = next(expected)
        transaction { exact(expected); execute("UPDATE source_locator SET revision=?,enabled=? WHERE source_id=?", revision, if(enabled) 1L else 0L, expected.id) }
        return SourceLocator(expected.id, 1, revision, expected.kind, enabled, expected.label, expected.identity(), expected.bookmark())
    }
    @Synchronized override fun forget(expected: SourceLocator): Unit {
        ready(); transaction { exact(expected); execute("DELETE FROM source_locator WHERE source_id=?", expected.id) }
    }
    @Synchronized override fun close(): Unit {
        usable = false
        // If close fails retain the connection/native lock; caller must retry the real close.
        connection?.close(); connection = null
    }

    private fun ready(): Unit {
        need(usable && !uncertain, "source_index_reopen_required")
        native.verifyPrivateIndex(); val path = checkNotNull(database)
        need(file(path).fileKey() == identity, "source_index_unavailable"); budget(0)
    }
    private fun exact(expected: SourceLocator): Unit {
        val actual = query("SELECT * FROM source_locator WHERE source_id=?", expected.id, mapper = ::row).singleOrNull()
            ?: fail("source_revision_stale")
        need(actual.version == 1L && actual.revision == expected.revision && actual.kind == expected.kind &&
            actual.enabled == expected.enabled && actual.label == expected.label &&
            actual.identity().contentEquals(expected.identity()) && actual.bookmark().contentEquals(expected.bookmark()), "source_revision_stale")
        need(supported(actual), "source_record_unsupported")
    }
    private fun next(row: SourceLocator): Long {
        need(supported(row) && row.revision < Long.MAX_VALUE, "source_record_unsupported"); return row.revision + 1
    }
    private fun supported(row: SourceLocator): Boolean = row.version == 1L && row.revision > 0 &&
        SourceKind.entries.any { it.name == row.kind } && canonicalUuid(row.id)
    private fun row(s: SQLiteStatement): SourceLocator {
        val version = s.getLong(1); val enabled = s.getLong(4)
        val label = s.getText(5); val id = s.getText(0)
        val identity = s.getBlob(6); val bookmark = s.getBlob(7)
        // Retain unknown values in SQL. Unsupported projection cannot authorize a rewrite.
        val known = version == 1L && enabled in 0L..1L && canonicalUuid(id) && s.getLong(2) > 0 &&
            SourceKind.entries.any { it.name == s.getText(3) } && validLabel(label) &&
            identity.size in 1..SourceAccessLimits.IDENTITY_BYTES && bookmark.size in 1..SourceAccessLimits.BOOKMARK_BYTES
        return SourceLocator(id, if (known) version else -1, s.getLong(2), s.getText(3), enabled == 1L,
            if (known) label else "Unavailable source", identity, bookmark)
    }
    private fun fields(label: String, identity: ByteArray, bookmark: ByteArray): Unit {
        need(validLabel(label) && identity.size in 1..SourceAccessLimits.IDENTITY_BYTES &&
            bookmark.size in 1..SourceAccessLimits.BOOKMARK_BYTES, "source_selection_invalid")
    }
    private fun validLabel(label: String): Boolean = label.isNotBlank() && label.toByteArray(Charsets.UTF_8).size <= SourceAccessLimits.LABEL_BYTES &&
        label.none { it.isISOControl() || it == '/' || it == '\\' || Character.getType(it) == Character.FORMAT.toInt() }
    private fun file(path: Path): PosixFileAttributes {
        val attrs = Files.readAttributes(path, PosixFileAttributes::class.java, NOFOLLOW_LINKS)
        need(attrs.isRegularFile && attrs.owner() == Files.getOwner(Path.of(System.getProperty("user.home")), NOFOLLOW_LINKS) &&
            attrs.permissions() == PosixFilePermissions.fromString("rw-------") &&
            (Files.getAttribute(path, "unix:nlink", NOFOLLOW_LINKS) as Number).toLong() == 1L, "source_index_unavailable")
        return attrs
    }
    private fun budget(growth: Long): Unit {
        native.verifyPrivateIndex()
        val path = checkNotNull(database)
        val paths = listOf(path, Path.of("$path-wal"), Path.of("$path-shm"))
        val sizes = paths.map { if(Files.exists(it, NOFOLLOW_LINKS)) { file(it); Files.size(it) } else 0L }
        val allocated = connection?.let { number("PRAGMA page_count") * number("PRAGMA page_size") } ?: sizes[0]
        need(allocated <= SourceAccessLimits.MAIN_BYTES - growth && sizes[0] <= SourceAccessLimits.MAIN_BYTES - growth &&
            sizes.sum() <= SourceAccessLimits.COMBINED_BYTES - growth * 2 &&
            Files.getFileStore(path.parent).usableSpace >= SourceAccessLimits.FREE_BYTES + growth * 2, "source_index_full")
    }
    private fun <T> transaction(action: () -> T): T {
        budget(1_048_576); val db = checkNotNull(connection); db.execSQL("BEGIN IMMEDIATE")
        var committing = false
        try {
            val result = action(); budget(1_048_576); committing = true; db.execSQL("COMMIT"); return result
        } catch (failure: Exception) {
            if (committing) uncertain = true
            try { db.execSQL("ROLLBACK") } catch (_: Exception) { uncertain = true }
            throw if (failure is SourceAccessFailure) failure else SourceAccessFailure("source_index_reopen_required")
        }
    }
    private fun execute(sql: String, vararg values: Any): Unit = query(sql, *values) { Unit }.let { }
    private fun number(sql: String): Long = query(sql) { it.getLong(0) }.single()
    private fun <T> query(sql: String, vararg values: Any, mapper: (SQLiteStatement) -> T): List<T> {
        val result = mutableListOf<T>(); checkNotNull(connection).prepare(sql).use { s ->
            values.forEachIndexed { i, value -> when(value) {
                is String -> s.bindText(i + 1, value)
                is ByteArray -> s.bindBlob(i + 1, value)
                is Long -> s.bindLong(i + 1, value)
                else -> fail("source_index_unavailable")
            } }
            while(s.step()) result += mapper(s)
        }; return result
    }
    private fun canonicalUuid(value: String): Boolean = runCatching { UUID.fromString(value).toString() == value }.getOrDefault(false)
    private fun need(value: Boolean, code: String): Unit { if(!value) fail(code) }
    private fun fail(code: String): Nothing = throw SourceAccessFailure(code)
    private companion object { const val APPLICATION_ID: Long = 0x41504731 }
}
