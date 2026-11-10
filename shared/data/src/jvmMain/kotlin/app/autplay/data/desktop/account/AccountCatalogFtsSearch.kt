package app.autplay.data.desktop.account

import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL
import app.autplay.application.desktop.account.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.atomic.AtomicBoolean

/** One request owns one replaceable in-memory index. It closes before returning a page.
 * No persistent table, raw J access, new driver, C1 fanout or substring fallback.
 * Native SQLite steps are synchronous; cancellation/retirement is cooperative between steps.
 */
class AccountCatalogFtsSearch {
    private val mutex = Mutex()
    private val retired = AtomicBoolean(false)
    private var retained:SQLiteConnection? = null
    fun retire():Unit { retired.set(true) }
    suspend fun close():Unit = withContext(NonCancellable+Dispatchers.IO) {
        retire()
        mutex.withLock { retained?.close(); retained=null }
    }
    suspend fun page(snapshot:AccountCatalogSearchSnapshot,query:String,after:String?,
        isCurrent:()->Boolean):AccountCatalogSearchPage = withContext(Dispatchers.IO) { mutex.withLock {
        val expression = accountCatalogSearchExpression(query)
        suspend fun admit() {
            currentCoroutineContext().ensureActive()
            if (retired.get() || !isCurrent()) throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        }
        admit()
        // An uncertain close is retained and retried before any further allocation.
        retained?.close(); retained=null
        val database = BundledSQLiteDriver().open(":memory:")
        retained=database
        var failure:Throwable? = null
        try {
            database.execSQL("PRAGMA page_size=4096")
            database.execSQL("PRAGMA max_page_count=16384")
            database.execSQL("PRAGMA cache_size=-8192")
            database.execSQL("PRAGMA temp_store=MEMORY")
            database.execSQL("PRAGMA journal_mode=MEMORY")
            database.execSQL("CREATE VIRTUAL TABLE catalog_text USING fts5(entity_key UNINDEXED,title,subtitle,tokenize='unicode61 remove_diacritics 2',prefix='2 3')")
            database.execSQL("BEGIN")
            database.prepare("INSERT INTO catalog_text(entity_key,title,subtitle) VALUES(?,?,?)").use { insert ->
                for (document in snapshot.documents) {
                    admit()
                    insert.bindText(1,document.key)
                    insert.bindText(2,document.artist?.name ?: document.album?.title.orEmpty())
                    insert.bindText(3,document.artist?.disambiguation ?: document.album?.albumArtist.orEmpty())
                    check(!insert.step())
                    insert.reset(); insert.clearBindings()
                }
            }
            database.execSQL("COMMIT")
            admit()
            // Whole captured set is indexed before MATCH. Stable real-key order is not ranking.
            val keys = ArrayList<String>(101)
            val sql = if (expression==null) "SELECT entity_key FROM catalog_text WHERE entity_key>? ORDER BY entity_key LIMIT 101"
                else "SELECT entity_key FROM catalog_text WHERE catalog_text MATCH ? AND entity_key>? ORDER BY entity_key LIMIT 101"
            database.prepare(sql).use { select ->
                if (expression==null) select.bindText(1,after.orEmpty()) else {
                    select.bindText(1,expression); select.bindText(2,after.orEmpty())
                }
                while (select.step()) { admit(); keys.add(select.getText(0)) }
            }
            val wanted = keys.take(100).toSet()
            val items = snapshot.documents.filter { it.key in wanted }
            if (items.size!=wanted.size) throw AccountStoreException(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            admit()
            AccountCatalogSearchPage(snapshot.revision,items,if(keys.size>100)keys[99] else null)
        } catch (error:Throwable) {
            failure=error
            throw error
        } finally {
            // A page cannot be published before actual close; close failure propagates on success.
            withContext(NonCancellable+Dispatchers.IO) {
                try { database.close(); retained=null } catch (cleanup:Throwable) {
                    val original=failure
                    if (original==null) throw cleanup else original.addSuppressed(cleanup)
                }
            }
        }
    } }
}
