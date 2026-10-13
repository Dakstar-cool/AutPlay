package app.autplay.application.portableaccount

import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.PortableLocalListeningRow
import app.autplay.application.library.AddLocalTrackCommand
import app.autplay.application.library.LocalLibraryCommandRepository
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.domain.LocalId
import kotlinx.serialization.json.*
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Real Room transactions with a scripted wire boundary, including process-style coordinator restart. */
class PortableAccountDataSyncTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val name = "portable-data-sync-test.db"
    private lateinit var db: AutPlayDatabase
    @Before fun setup() { context.deleteDatabase(name); db = AutPlayDatabase.open(context, name) }
    @After fun cleanup() { db.close(); context.deleteDatabase(name) }

    @Test fun aliasesCutoffsCrashRetryAndLastPageContinuationPreserveOneLogicalEvent() = runBlocking {
        val account = id(1); val profile = id(2)
        val repository = PortableAccountDataRepository(db)
        repository.persistOwnership(account, profile)
        repository.persistOwnership(account, null, 100)
        assertTrue(runCatching { repository.persistOwnership(id(3), profile) }.isFailure)
        val first = listening(id(10), id(20))
        val originalBase = listening(id(20), null)
        val original = PortableAccountDataCodec.checked(originalBase.recordId, "LISTENING", JsonObject(originalBase.document + mapOf(
            "title" to JsonPrimitive("Zulu"), "artist" to JsonPrimitive("Other artist"),
            "event_origin" to JsonPrimitive("SEARCH"), "context" to JsonPrimitive("SLEEP"),
        )))
        repository.mergeRecords(account, listOf(original, first))
        assertEquals(1, repository.listeningPage(account).size)
        val revision = db.portableAccountDao().latestRevision(account)
        repository.mergeRecords(account, listOf(first, original))
        assertEquals(revision, db.portableAccountDao().latestRevision(account))
        assertEquals(100L, db.portableAccountDao().scope(PortableAccountDataRepository.STANDALONE_PROFILE)!!.ownerSinceMs)

        LocalLibraryCommandRepository(db).add(AddLocalTrackCommand(null, LocalId(id(50)), LocalId(id(51)), LocalId(id(52)), "Track", "Artist", 1))
        val library = LibraryVerticalSliceRepository(db)
        library.recordListening(null, LocalId(id(53)), LocalId(id(50)), 30000, 60000, false, "ORGANIC", now = 50)
        library.recordListening(null, LocalId(id(54)), LocalId(id(50)), 30000, 60000, false, "ORGANIC", now = 200)

        val downloaded = PortableAccountDataCodec.checked(id(30), "TASTE", buildJsonObject {
            put("version", 1); put("updated_at_ms", 2); put("track_key", id(50)); put("interaction", "LIKE"); put("weight", 1000)
        })
        var uploads = 0
        var failFirstUpload = true
        val transport = object : PortableAccountDataTransport {
            override suspend fun upload(accountId: String, records: List<PortableDataRecord>) {
                assertEquals(account, accountId); assertTrue(records.size <= 32)
                uploads++
                if (failFirstUpload) { failFirstUpload = false; throw java.io.IOException("injected") }
            }
            override suspend fun download(accountId: String, source: String, after: PortableDataCursor): PortableDataPage {
                return if (source == "tastes") PortableDataPage(account, listOf(downloaded), PortableDataCursor("2026-10-08T00:00:00Z", id(99)), null)
                else PortableDataPage(account, emptyList(), null, null)
            }
        }
        // Ten empty local stages, one upload, and six sources: the last remote page exactly
        // consumes the budget. Its newly imported row still needs a final upload continuation.
        assertTrue(runCatching { PortableAccountDataSync(repository, transport, maxPages = 17).run(account, profile) }.isFailure)
        assertEquals(0L, repository.cursor(account, profile, "upload").uploadedRevision)
        assertEquals(PortableAccountDataSyncOutcome.CONTINUE, PortableAccountDataSync(repository, transport, maxPages = 7).run(account, profile))
        assertFalse(repository.cursor(account, profile, "pass").complete)
        assertEquals(PortableAccountDataSyncOutcome.COMPLETE, PortableAccountDataSync(repository, transport, maxPages = 1).run(account, profile))
        assertTrue(repository.cursor(account, profile, "pass").complete)
        assertEquals(2, repository.listeningPage(account).size)
        val aliases = repository.listeningPage(account).flatMap { it.document["event_ids"]!!.jsonArray.map { value -> value.jsonPrimitive.content } }
        assertFalse(id(53) in aliases); assertTrue(id(54) in aliases)
        assertEquals(3, uploads)
        val foreignPage = PortableDataPage(id(3), listOf(listening(id(40), null)), PortableDataCursor("2026-10-09T00:00:00Z", id(40)), null)
        assertTrue(runCatching { repository.applyPage(account, profile, "records", foreignPage) }.isFailure)
        assertEquals(2, repository.listeningPage(account).size)
    }

    private fun listening(event: String, materialized: String?): PortableDataRecord = PortableAccountDataCodec.listening(
        PortableLocalListeningRow(event, materialized, id(50), "Track", "Artist", null, 1, 30000, 60000, false, "ORGANIC", "GENERAL", 2))
    private fun id(number: Int): String = "00000000-0000-0000-0000-%012d".format(number)
}
