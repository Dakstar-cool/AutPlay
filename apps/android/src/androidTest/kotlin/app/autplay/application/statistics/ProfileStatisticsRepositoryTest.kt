package app.autplay.application.statistics

import androidx.room3.Room
import androidx.sqlite.driver.bundled.BundledSQLiteDriver
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.ListeningEventEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import java.time.Clock
import java.time.Instant
import java.time.ZoneId
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ProfileStatisticsRepositoryTest {
    private lateinit var database: AutPlayDatabase

    @Before
    fun setUp() {
        database = Room.inMemoryDatabaseBuilder<AutPlayDatabase>(
            ApplicationProvider.getApplicationContext<android.content.Context>(),
        )
            .setDriver(BundledSQLiteDriver())
            .build()
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun ownerStatisticsAreProfileScopedBoundedAndIncludeTasteExcludedEvents() = runBlocking {
        val zone = ZoneId.of("Europe/Moscow")
        val now = Instant.parse("2026-08-25T12:00:00Z")
        val clock = Clock.fixed(now, zone)
        val cutoffs = ProfileStatisticsCutoffs.current(clock, zone)
        listOf(
            track("a-one", PROFILE_A, "First", "Artist one", "recording-one"),
            track("a-two", PROFILE_A, "Second", "Artist two", "recording-two"),
            track("a-three", PROFILE_A, "Third", "Artist two", null),
            track("b-one", PROFILE_B, "Other profile", "Other artist", null),
            track("legacy-one", LEGACY_PROFILE, "Standalone", "Local artist", null),
        ).forEach { database.libraryDao().upsertTrackRef(it) }

        database.historyDao().insert(event("a-current-private", "a-one", PROFILE_A, cutoffs.throughMs - 1, 7_000, excluded = true))
        database.historyDao().insert(event("a-seven-boundary", "a-two", PROFILE_A, cutoffs.last7DaysFromMs, 2_000))
        database.historyDao().insert(event("a-before-seven", "a-three", PROFILE_A, cutoffs.last7DaysFromMs - 1, 3_000))
        database.historyDao().insert(event("a-future", "a-one", PROFILE_A, cutoffs.throughMs + 1, 50_000))
        database.historyDao().insert(event("a-zero", "a-one", PROFILE_A, cutoffs.throughMs - 2, 0))
        database.historyDao().insert(event("b-current", "b-one", PROFILE_B, cutoffs.throughMs - 1, 11_000))
        database.historyDao().insert(event("legacy-current", "legacy-one", LEGACY_PROFILE, cutoffs.throughMs - 1, 13_000))

        val profileA = ProfileStatisticsRepository(database, clock, zone).observe(PROFILE_A).first()
        assertEquals(2L, profileA.last7Days.playSessionCount)
        assertEquals(9_000L, profileA.last7Days.listenedMs)
        assertEquals(2L, profileA.last7Days.uniqueTrackCount)
        assertEquals(3L, profileA.last30Days.playSessionCount)
        assertEquals(12_000L, profileA.last30Days.listenedMs)
        assertEquals(3L, profileA.last365Days.playSessionCount)
        assertTrue(profileA.topTracks30Days.any { it.title == "First" && it.listenedMs == 7_000L })
        assertEquals("Artist two", profileA.topArtists30Days.first().artistName)
        assertEquals(2L, profileA.topArtists30Days.first().playSessionCount)

        val profileB = ProfileStatisticsRepository(database, clock, zone).observe(PROFILE_B).first()
        assertEquals(1L, profileB.last7Days.playSessionCount)
        assertEquals(11_000L, profileB.last7Days.listenedMs)

        val standalone = ProfileStatisticsRepository(database, clock, zone).observe(null).first()
        assertEquals(1L, standalone.last7Days.playSessionCount)
        assertEquals(13_000L, standalone.last7Days.listenedMs)
    }

    @Test
    fun ownerStatisticsRefreshForListeningEventsAndLocalDayRolloverWhileObserved() = runBlocking {
        val zone = ZoneId.of("Europe/Moscow")
        val mutableClock = MutableClock(Instant.parse("2026-08-25T20:59:59Z"), zone)
        val invalidations = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
        database.libraryDao().upsertTrackRef(track("live", PROFILE_A, "Live", "Artist", "recording-live"))
        val observed = mutableListOf<OwnerProfileStatistics>()
        val job = launch {
            ProfileStatisticsRepository(database, mutableClock, zone, invalidations).observe(PROFILE_A).collect { observed += it }
        }
        try {
            withTimeout(5_000) { while (observed.isEmpty()) delay(10) }
            database.historyDao().insert(event("live-event", "live", PROFILE_A, mutableClock.millis() - 1, 5_000))
            withTimeout(5_000) { while (observed.none { it.last7Days.playSessionCount == 1L }) delay(10) }

            mutableClock.instant = Instant.parse("2026-08-25T21:00:01Z")
            invalidations.emit(Unit)
            withTimeout(5_000) { while (observed.none { it.throughMs == mutableClock.millis() }) delay(10) }
        } finally {
            job.cancel()
        }
    }

    @Test
    fun defaultRepositoryRecomputesThroughCutoffAfterListeningEventInvalidation() = runBlocking {
        val zone = ZoneId.of("Europe/Moscow")
        val mutableClock = MutableClock(Instant.parse("2026-08-25T12:00:00Z"), zone)
        database.libraryDao().upsertTrackRef(track("production-live", PROFILE_A, "Live", "Artist", "recording-live"))
        val observed = mutableListOf<OwnerProfileStatistics>()
        val job = launch {
            ProfileStatisticsRepository(database, mutableClock, zone).observe(PROFILE_A).collect { observed += it }
        }
        try {
            withTimeout(5_000) { while (observed.isEmpty()) delay(10) }
            mutableClock.instant = Instant.parse("2026-08-25T12:00:02Z")
            database.historyDao().insert(event("production-live-event", "production-live", PROFILE_A, mutableClock.millis() - 1, 5_000))
            withTimeout(5_000) {
                while (observed.none {
                    it.throughMs == mutableClock.millis() && it.last7Days.playSessionCount == 1L
                }) delay(10)
            }
        } finally {
            job.cancel()
        }
    }

    private fun track(
        id: String,
        profileId: String,
        title: String,
        artist: String,
        recordingId: String?,
    ) = UserTrackRefEntity(
        localUserTrackRefId = id,
        serverUserTrackRefId = null,
        localRecordingId = null,
        serverRecordingId = recordingId,
        resolutionStatus = if (recordingId == null) "UNRESOLVED" else "RESOLVED",
        rawTitle = title,
        rawArtist = artist,
        rawAlbum = null,
        rawDurationMs = null,
        resolutionConfidence = null,
        syncState = "CLEAN",
        serverRowVersion = null,
        lastLocalSequence = 0,
        createdAtMs = 1,
        updatedAtMs = 1,
        deletedAtMs = null,
        serverProfileId = profileId,
    )

    private fun event(
        id: String,
        trackId: String,
        profileId: String,
        startedAtMs: Long,
        playedMs: Long,
        excluded: Boolean = false,
    ) = ListeningEventEntity(
        listeningEventId = id,
        localUserTrackRefId = trackId,
        serverRecordingId = null,
        startedAtMs = startedAtMs,
        playedMs = playedMs,
        trackDurationMs = 180_000,
        completionRatio = playedMs.toDouble() / 180_000.0,
        eventOrigin = "ORGANIC",
        context = "GENERAL",
        recommendationRequestId = null,
        explicitFeedback = "NONE",
        excludedFromTaste = excluded,
        syncState = "CLEAN",
        createdAtMs = startedAtMs,
        serverProfileId = profileId,
    )

    private companion object {
        const val PROFILE_A = "11111111-1111-4111-8111-111111111111"
        const val PROFILE_B = "22222222-2222-4222-8222-222222222222"
        const val LEGACY_PROFILE = "legacy-unscoped"
    }

    private class MutableClock(
        var instant: Instant,
        private val zoneId: ZoneId,
    ) : Clock() {
        override fun getZone(): ZoneId = zoneId
        override fun withZone(zone: ZoneId): Clock = MutableClock(instant, zone)
        override fun instant(): Instant = instant
    }
}
