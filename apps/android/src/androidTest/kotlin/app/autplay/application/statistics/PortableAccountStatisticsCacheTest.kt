package app.autplay.application.statistics

import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.portableaccount.PortableAccountDataCodec
import app.autplay.application.portableaccount.PortableAccountDataRepository
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.PortableLocalListeningRow
import app.autplay.domain.ServerProfileId
import java.io.IOException
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PortableAccountStatisticsCacheTest {
    @Test fun twoDeviceServerSnapshotSurvivesOfflineAndNormalizedUnionRemainsOwnerScoped() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "portable-account-statistics-test.db"
        context.deleteDatabase(name)
        val db = AutPlayDatabase.open(context, name)
        try {
            val owner = id(1); val other = id(2); val profile = ServerProfileId(id(3))
            val data = PortableAccountDataRepository(db)
            data.mergeRecords(owner, listOf(listening(10, 30_000, "Rock")))
            data.mergeRecords(other, listOf(listening(20, 999_000, "Foreign")))
            val repository = PortableAccountStatisticsRepository(db, Clock.fixed(Instant.ofEpochMilli(1000), ZoneOffset.UTC))
            val remote = OwnerProfileStatistics(900, 90_000, listOf(OwnerTopGenre("Rock", 90_000)), emptyList(), emptyList(), owner)
            assertEquals(remote, repository.refresh(owner, profile, AccountStatisticsPort { _, _ -> remote }))
            assertEquals(remote, repository.refresh(owner, profile, AccountStatisticsPort { _, _ -> throw IOException("offline") }))
            data.mergeRecords(owner, listOf(listening(11, 60_000, "Jazz")))
            val cached = repository.refresh(owner, null, null)
            assertEquals(owner, cached.accountId)
            assertEquals(90_000L, cached.listenedMs)
            assertEquals(listOf("Jazz", "Rock"), cached.topGenres.map { it.genre })
            assertEquals(999_000L, repository.refresh(other, null, null).listenedMs)
            assertTrue(db.portableAccountDao().allStatistics(other).isEmpty())
            val wrongOwner = remote.copy(accountId = other, listenedMs = 8_000_000)
            assertEquals(90_000L, repository.refresh(owner, profile, AccountStatisticsPort { _, _ -> wrongOwner }).listenedMs)
            assertEquals(90_000L, decodeAccountStatistics(db.portableAccountDao().statistics(owner, profile.value)!!.documentJson.toByteArray(), owner).listenedMs)
        } finally {
            db.close()
            context.deleteDatabase(name)
        }
    }

    private fun listening(number: Int, played: Long, genre: String) = PortableAccountDataCodec.listening(
        PortableLocalListeningRow(id(number), null, id(number + 100), "Song", "Artist",
            """{"version":1,"fields":{"genres":["$genre"]}}""", 1, played, 1_000_000, false, "ORGANIC", "GENERAL", 2),
    ).let { record ->
        // Genre parsing belongs to metadata ingestion; the account cache receives a checked document.
        PortableAccountDataCodec.checked(record.recordId, record.kind,
            kotlinx.serialization.json.JsonObject(record.document + ("genres" to kotlinx.serialization.json.JsonArray(listOf(kotlinx.serialization.json.JsonPrimitive(genre))))))
    }
    private fun id(number: Int): String = "00000000-0000-4000-8000-%012d".format(number)
}
