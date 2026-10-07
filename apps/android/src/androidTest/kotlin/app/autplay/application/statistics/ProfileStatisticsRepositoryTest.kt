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
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
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
    fun snapshotIsAllTimeProfileScopedAndRankedByActualListeningTime() = runBlocking {
        val now = Instant.parse("2026-10-06T12:00:00Z")
        val clock = Clock.fixed(now, ZoneId.of("Europe/Moscow"))
        listOf(
            track("a-one", PROFILE_A, "First", "Artist one", "recording-one"),
            track("a-alias", PROFILE_A, "First", "Artist one", "recording-one"),
            track("a-two", PROFILE_A, "Second", "Artist two", null),
            track("b-one", PROFILE_B, "Other", "Other artist", null),
            track("legacy-one", LEGACY_PROFILE, "Local", "Local artist", null),
        ).forEach { database.libraryDao().upsertTrackRef(it) }
        val through = now.toEpochMilli()
        database.historyDao().insert(event("old", "a-one", PROFILE_A, Instant.parse("2020-01-01T00:00:00Z").toEpochMilli(), 90_000, excluded = true))
        database.historyDao().insert(event("alias", "a-alias", PROFILE_A, through, 10_000))
        repeat(3) { database.historyDao().insert(event("short-$it", "a-two", PROFILE_A, through - 1, 1_000)) }
        database.historyDao().insert(event("future", "a-two", PROFILE_A, through + 1, 999_000))
        database.historyDao().insert(event("zero", "a-one", PROFILE_A, through, 0))
        database.historyDao().insert(event("negative", "a-one", PROFILE_A, through, -1))
        database.historyDao().insert(event("other", "b-one", PROFILE_B, through, 500_000))
        database.historyDao().insert(event("standalone", "legacy-one", LEGACY_PROFILE, through, 7_000))
        // Removed library entries retain their playback outcomes.
        database.libraryDao().upsertTrackRef(track("a-one", PROFILE_A, "First", "Artist one", "recording-one").copy(deletedAtMs = through))
        val repository = ProfileStatisticsRepository(database, clock)
        val result = repository.refresh(PROFILE_A)
        assertEquals(103_000L, result.listenedMs)
        assertEquals(2, result.topTracks.size)
        assertEquals("server:recording-one", result.topTracks.first().identityKey)
        assertEquals(100_000L, result.topTracks.first().listenedMs)
        assertEquals("Artist one", result.topArtists.first().artistName)
        assertEquals(7_000L, repository.refresh(null).listenedMs)
        assertEquals(500_000L, repository.refresh(PROFILE_B).listenedMs)
    }

    @Test
    fun genreRankingUsesEveryMetadataPageAndCountsEachDistinctGenreOncePerTrack() = runBlocking {
        val clock = Clock.fixed(Instant.parse("2026-10-06T12:00:00Z"), ZoneId.of("UTC"))
        repeat(205) { index ->
            val id = "track-%03d".format(index)
            database.libraryDao().upsertTrackRef(track(id, PROFILE_A, id, "Artist $index", null))
            database.historyDao().insert(event("event-$index", id, PROFILE_A, clock.millis(), if (index == 204) 10_000 else 1_000))
            val payload = if (index == 204) """{"revision":1,"state":"READY","fields":{"genres":["Jazz"," jazz ","Soul"]}}"""
                else """{"revision":1,"state":"READY","fields":{"genres":["Rock","Rock"]}}"""
            database.trackMetadataDao().upsert(app.autplay.data.local.entity.TrackMetadataEntity(PROFILE_A, id, 1, payload, null, 1))
        }
        val result = ProfileStatisticsRepository(database, clock).refresh(PROFILE_A)
        assertEquals(214_000L, result.listenedMs)
        assertEquals(listOf("Rock", "Jazz", "Soul"), result.topGenres.map { it.genre })
        assertEquals(listOf(204_000L, 10_000L, 10_000L), result.topGenres.map { it.listenedMs })
        assertEquals(5, result.topTracks.size)
        assertEquals("track-204", result.topTracks.first().title)
        assertEquals(5, result.topArtists.size)
    }

    @Test
    fun genreRankingIsBoundedToFiveAndUsesStableTieOrder() = runBlocking {
        val clock = Clock.fixed(Instant.parse("2026-10-06T12:00:00Z"), ZoneId.of("UTC"))
        repeat(7) { index ->
            val id = "genre-track-$index"
            database.libraryDao().upsertTrackRef(track(id, PROFILE_A, id, "Artist", null))
            database.historyDao().insert(event("genre-event-$index", id, PROFILE_A, clock.millis(), 1_000))
            database.trackMetadataDao().upsert(app.autplay.data.local.entity.TrackMetadataEntity(PROFILE_A, id, 1,
                """{"revision":1,"state":"READY","fields":{"genres":["Genre $index"]}}""", null, 1))
        }
        val result = ProfileStatisticsRepository(database, clock).refresh(PROFILE_A)
        assertEquals((0..4).map { "Genre $it" }, result.topGenres.map { it.genre })
        assertEquals(7_000L, result.listenedMs)
    }

    @Test
    fun refreshIsExplicitAndCompatibilityFlowNeverReactsToRoomOrClockChanges() = runBlocking {
        val clock = MutableClock(Instant.parse("2026-10-06T12:00:00Z"), ZoneId.of("UTC"))
        database.libraryDao().upsertTrackRef(track("live", PROFILE_A, "Live", "Artist", null))
        val repository = ProfileStatisticsRepository(database, clock)
        val emissions = repository.observe(PROFILE_A).toList()
        assertEquals(1, emissions.size)
        val snapshot = emissions.single()
        clock.instant = clock.instant.plusSeconds(86_400)
        database.historyDao().insert(event("live-event", "live", PROFILE_A, clock.millis(), 5_000))
        assertEquals(0L, snapshot.listenedMs)
        assertEquals(5_000L, repository.refresh(PROFILE_A).listenedMs)
        assertTrue(repository.refresh(PROFILE_A).throughMs > snapshot.throughMs)
    }

    @Test
    fun missingMalformedAndOtherProfileMetadataNeverInventGenres() = runBlocking {
        val clock = Clock.fixed(Instant.parse("2026-10-06T12:00:00Z"), ZoneId.of("UTC"))
        listOf("missing", "bad", "unknown").forEach { id ->
            database.libraryDao().upsertTrackRef(track(id, PROFILE_A, id, " ", null))
            database.historyDao().insert(event("event-$id", id, PROFILE_A, clock.millis(), 1_000))
        }
        database.trackMetadataDao().upsert(app.autplay.data.local.entity.TrackMetadataEntity(PROFILE_A, "bad", 1, "invalid-json", null, 1))
        database.trackMetadataDao().upsert(app.autplay.data.local.entity.TrackMetadataEntity(PROFILE_B, "unknown", 1,
            """{"revision":1,"state":"READY","fields":{"genres":["Other profile"]}}""", null, 1))
        val result = ProfileStatisticsRepository(database, clock).refresh(PROFILE_A)
        assertEquals(3_000L, result.listenedMs)
        assertTrue(result.topGenres.isEmpty())
        assertTrue(result.topArtists.isEmpty())
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
