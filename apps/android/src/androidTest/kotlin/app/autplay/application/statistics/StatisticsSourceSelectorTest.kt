package app.autplay.application.statistics

import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.portableaccount.PortableAccountDataCodec
import app.autplay.application.portableaccount.PortableAccountDataRepository
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.PortableLocalListeningRow
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.local.entity.ListeningEventEntity
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class StatisticsSourceSelectorTest {
    @Test fun legacyRoomHistorySurvives404AndObserverWithoutAccountRelabelOrLateIdentityPublication() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "statistics-source-selection.db"
        context.deleteDatabase(name)
        val database = AutPlayDatabase.open(context, name)
        val ownerScope = CoroutineScope(coroutineContext + SupervisorJob())
        try {
            val a = StatisticsIdentity(id(1), id(2), id(3), id(4))
            val b = StatisticsIdentity(id(5), id(6), id(7), id(8))
            val clock = Clock.fixed(Instant.ofEpochMilli(1000), ZoneOffset.UTC)
            seed(database, a.profileId!!, id(10), id(11), 30_000)
            seed(database, b.profileId!!, id(12), id(13), 999_000)
            val originalTracks = database.libraryDao().trackRefs(listOf(id(10), id(12)), 3).sortedBy { it.localUserTrackRefId }
            val originalEvents = database.historyDao().recent(3).first().sortedBy { it.listeningEventId }
            assertEquals(listOf(id(10), id(12)), originalTracks.map { it.localUserTrackRefId })
            assertEquals(listOf(id(11), id(13)), originalEvents.map { it.listeningEventId })
            suspend fun assertOriginalRows() {
                assertEquals(2, database.libraryDao().trackRefCount())
                assertEquals(originalTracks, database.libraryDao().trackRefs(listOf(id(10), id(12)), 3).sortedBy { it.localUserTrackRefId })
                assertEquals(originalEvents, database.historyDao().recent(3).first().sortedBy { it.listeningEventId })
            }
            val accounts = PortableAccountStatisticsRepository(database, clock)
            val profiles = ProfileStatisticsRepository(database, clock)
            var current = a
            val selector = StatisticsSourceSelector(accounts, profiles) { captured ->
                check(captured == current) { "ACCOUNT_CONTEXT_CHANGED" }
            }
            val credentials = object : CredentialStore {
                private val encoded = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("synthetic-statistics-access", null, 0))
                override suspend fun read(profileId: ServerProfileId): ByteArray? =
                    if (profileId.value == a.profileId) encoded.copyOf() else null
                override suspend fun write(profileId: ServerProfileId, material: ByteArray) { error("Unexpected rotation") }
                override suspend fun clear(profileId: ServerProfileId) { error("Unexpected clear") }
            }
            MockWebServer().use { server ->
                server.start()
                val remote = OkHttpAccountStatisticsPort(server.url("/api/v1").toString(), credentials)
                fun assertOwnerRequest() {
                    val request = requireNotNull(server.takeRequest(5, TimeUnit.SECONDS))
                    assertEquals("GET", request.method)
                    assertEquals("/api/v1/social/profile-statistics/owner", request.path)
                    assertEquals("Bearer synthetic-statistics-access", request.getHeader("Authorization"))
                }
                // Reproduce the former UI's account-only branch through the preserved public API.
                // This is a Room/HTTP reproduction, not an observation of the historical APK.
                server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
                val accountOnly = accounts.refresh(a.accountId!!, ServerProfileId(a.profileId), remote)
                assertEquals(0L, accountOnly.listenedMs)
                assertEquals(a.accountId, accountOnly.accountId)
                assertEquals(30_000L, profiles.refresh(a.profileId).listenedMs)
                assertEquals(999_000L, profiles.refresh(b.profileId).listenedMs)
                assertOwnerRequest()
                assertOriginalRows()
                server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
                val manual = selector.load(a, remote)
                assertEquals(StatisticsSource.PROFILE_LOCAL, manual.source)
                assertEquals(30_000L, manual.snapshot.listenedMs)
                assertEquals(profiles.refresh(a.profileId), manual.snapshot)
                assertNull(manual.snapshot.accountId)
                assertOwnerRequest()
                assertEquals(manual, selector.load(a)) // observer initial emission, no extra HTTP
                assertOriginalRows()

                val owner = ProfileStatisticsSnapshotOwner(ownerScope, a.profileId) { selector.load(a).snapshot }
                owner.refresh()
                awaitIdle(owner)
                owner.refreshWith { selector.load(a).snapshot }
                awaitIdle(owner)
                assertEquals(30_000L, owner.state.value.snapshot!!.listenedMs)
                assertFalse(owner.state.value.accountBased)
                assertFalse(owner.state.value.refreshFailed)
                assertTrue(database.portableAccountDao().scopes(a.accountId!!).isEmpty())
                assertTrue(database.portableAccountDao().allStatistics(a.accountId).isEmpty())
                assertEquals(0L, database.portableAccountDao().latestRevision(a.accountId))
                assertOriginalRows()

                // Simulate a callback which ignores cancellation after reading A's exact snapshot.
                val ready = CompletableDeferred<Unit>()
                val release = CompletableDeferred<Unit>()
                val finished = CompletableDeferred<Unit>()
                val oldSnapshot = owner.state.value.snapshot
                owner.refreshWith {
                    val selected = selector.load(a).snapshot
                    ready.complete(Unit)
                    withContext(NonCancellable) {
                        release.await()
                        finished.complete(Unit)
                    }
                    selected
                }
                withTimeout(5000) { ready.await() }
                current = b
                owner.close()
                val next = ProfileStatisticsSnapshotOwner(ownerScope, b.profileId) { selector.load(b).snapshot }
                next.refresh()
                awaitIdle(next)
                assertEquals(999_000L, next.state.value.snapshot!!.listenedMs)
                assertNull(next.state.value.snapshot!!.accountId)
                assertFalse(next.state.value.accountBased)
                release.complete(Unit)
                withTimeout(5000) { finished.await() }
                yield()
                assertSame(oldSnapshot, owner.state.value.snapshot)
                assertEquals(999_000L, next.state.value.snapshot!!.listenedMs)
                assertTrue(runCatching { selector.load(a) }.isFailure)
                current = a.copy(bindingCommitId = id(9))
                assertTrue(runCatching { selector.load(a) }.isFailure)
                assertOriginalRows()

                // A valid authenticated zero and its cached zero must not choose legacy history.
                current = a
                val zero = OwnerProfileStatistics(900, 0, emptyList(), emptyList(), emptyList(), a.accountId)
                val authenticatedZero = selector.load(a, AccountStatisticsPort { _, _ -> zero })
                assertEquals(StatisticsSource.ACCOUNT, authenticatedZero.source)
                assertEquals(zero, authenticatedZero.snapshot)
                assertEquals(authenticatedZero, selector.load(a))
                val accountOwner = ProfileStatisticsSnapshotOwner(ownerScope, a.profileId) { selector.load(a).snapshot }
                accountOwner.refresh(); awaitIdle(accountOwner)
                assertTrue(accountOwner.state.value.accountBased)

                // Normalized account-owned facts with zero duration are also available evidence.
                current = b
                val zeroRecord = PortableAccountDataCodec.listening(PortableLocalListeningRow(id(20), null,
                    id(21), "Zero", "Artist", null, 1, 0, 1000, false, "ORGANIC", "GENERAL", 2))
                PortableAccountDataRepository(database).mergeRecords(b.accountId!!, listOf(zeroRecord))
                val normalizedZero = selector.load(b)
                assertEquals(StatisticsSource.ACCOUNT, normalizedZero.source)
                assertEquals(0L, normalizedZero.snapshot.listenedMs)
                assertEquals(b.accountId, normalizedZero.snapshot.accountId)
                assertOriginalRows()
                assertTrue(database.portableAccountDao().scopes(a.accountId).isEmpty())
                assertTrue(database.portableAccountDao().scopes(b.accountId).isEmpty())
                assertEquals(0L, database.portableAccountDao().latestRevision(a.accountId))
                assertEquals(2, server.requestCount) // only the two explicit owner GETs; no upload
                next.close(); accountOwner.close()
            }
        } finally {
            ownerScope.cancel()
            database.close()
            context.deleteDatabase(name)
        }
    }

    private suspend fun awaitIdle(owner: ProfileStatisticsSnapshotOwner) {
        withTimeout(5000) { while (owner.state.value.loading) delay(1) }
    }

    private suspend fun seed(database: AutPlayDatabase, profile: String, track: String, event: String, played: Long) {
        database.libraryDao().upsertTrackRef(UserTrackRefEntity(track, null, null, null, "UNRESOLVED",
            "Legacy", "Artist", null, null, null, "CLEAN", null, 0, 1, 1, null, profile))
        database.historyDao().insert(ListeningEventEntity(event, track, null, 1, played, 1_000_000, null,
            "ORGANIC", "GENERAL", null, "NONE", false, "CLEAN", 2, serverProfileId = profile))
    }
    private fun id(number: Int): String = "00000000-0000-4000-8000-%012d".format(number)
}
