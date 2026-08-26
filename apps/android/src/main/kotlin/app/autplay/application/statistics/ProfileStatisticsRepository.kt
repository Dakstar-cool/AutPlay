package app.autplay.application.statistics

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.dao.OwnerStatisticsWindowProjection
import java.time.Clock
import java.time.LocalDate
import java.time.ZoneId
import kotlinx.coroutines.delay
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.onStart

/** Local-only owner statistics. No server or persistent aggregate participates in this flow. */
class ProfileStatisticsRepository(
    private val database: AutPlayDatabase,
    private val clock: Clock = Clock.systemDefaultZone(),
    private val zoneId: ZoneId = clock.zone,
    /** Test seam for calendar changes; production also observes listening_event invalidations. */
    private val cutoffInvalidations: Flow<Unit>? = null,
) {
    @OptIn(ExperimentalCoroutinesApi::class)
    fun observe(profileId: String?): Flow<OwnerProfileStatistics> {
        val effectiveProfileId = profileId ?: LEGACY_PROFILE_ID
        val dao = database.historyDao()
        val invalidations = cutoffInvalidations ?: merge(
            localDayRolloverInvalidations(clock, zoneId),
            database.invalidationTracker.createFlow("listening_event", emitInitialState = false).map { Unit },
        )
        return invalidations.onStart { emit(Unit) }.flatMapLatest {
            val cutoffs = ProfileStatisticsCutoffs.current(clock, zoneId)
            combine(
                dao.ownerWindow(effectiveProfileId, cutoffs.last7DaysFromMs, cutoffs.throughMs),
                dao.ownerWindow(effectiveProfileId, cutoffs.last30DaysFromMs, cutoffs.throughMs),
                dao.ownerWindow(effectiveProfileId, cutoffs.last365DaysFromMs, cutoffs.throughMs),
                dao.ownerTopTracks(effectiveProfileId, cutoffs.last30DaysFromMs, cutoffs.throughMs, TOP_LIST_LIMIT),
                dao.ownerTopArtists(effectiveProfileId, cutoffs.last30DaysFromMs, cutoffs.throughMs, TOP_LIST_LIMIT),
            ) { last7, last30, last365, tracks, artists ->
                OwnerProfileStatistics(
                    throughMs = cutoffs.throughMs,
                    last7Days = last7.toDomain(7),
                    last30Days = last30.toDomain(30),
                    last365Days = last365.toDomain(365),
                    topTracks30Days = tracks.map {
                        OwnerTopTrack(it.identityKey, it.title, it.artistName, it.playSessionCount, it.listenedMs)
                    },
                    topArtists30Days = artists.map {
                        OwnerTopArtist(it.artistName, it.playSessionCount, it.listenedMs)
                    },
                )
            }
        }
    }

    private fun OwnerStatisticsWindowProjection.toDomain(days: Int) = OwnerStatisticsWindow(
        days = days,
        playSessionCount = playSessionCount,
        listenedMs = listenedMs,
        uniqueTrackCount = uniqueTrackCount,
    )

    private companion object {
        const val LEGACY_PROFILE_ID = "legacy-unscoped"
        const val TOP_LIST_LIMIT = 5
    }
}

private fun localDayRolloverInvalidations(clock: Clock, zoneId: ZoneId): Flow<Unit> = flow {
    while (true) {
        val nextLocalDayMs = LocalDate.now(clock.withZone(zoneId))
            .plusDays(1)
            .atStartOfDay(zoneId)
            .toInstant()
            .toEpochMilli()
        delay((nextLocalDayMs - clock.millis()).coerceAtLeast(1L))
        emit(Unit)
    }
}

/** Calendar-day windows include the current local day and are capped at the injected current time. */
data class ProfileStatisticsCutoffs(
    val throughMs: Long,
    val last7DaysFromMs: Long,
    val last30DaysFromMs: Long,
    val last365DaysFromMs: Long,
) {
    companion object {
        fun current(clock: Clock, zoneId: ZoneId = clock.zone): ProfileStatisticsCutoffs {
            val through = clock.millis()
            val today = LocalDate.now(clock.withZone(zoneId))
            fun startOfWindow(days: Long): Long = today
                .minusDays(days - 1)
                .atStartOfDay(zoneId)
                .toInstant()
                .toEpochMilli()
            return ProfileStatisticsCutoffs(
                throughMs = through,
                last7DaysFromMs = startOfWindow(7),
                last30DaysFromMs = startOfWindow(30),
                last365DaysFromMs = startOfWindow(365),
            )
        }
    }
}

data class OwnerProfileStatistics(
    val throughMs: Long,
    val last7Days: OwnerStatisticsWindow,
    val last30Days: OwnerStatisticsWindow,
    val last365Days: OwnerStatisticsWindow,
    val topTracks30Days: List<OwnerTopTrack>,
    val topArtists30Days: List<OwnerTopArtist>,
) {
    init {
        require(topTracks30Days.size <= 5)
        require(topArtists30Days.size <= 5)
    }
}

data class OwnerStatisticsWindow(
    val days: Int,
    val playSessionCount: Long,
    val listenedMs: Long,
    val uniqueTrackCount: Long,
) {
    init {
        require(days in setOf(7, 30, 365))
        require(playSessionCount >= 0)
        require(listenedMs >= 0)
        require(uniqueTrackCount >= 0)
    }
}

data class OwnerTopTrack(
    /** Used only for stable in-memory rendering; never leaves the owner device. */
    val identityKey: String,
    val title: String?,
    val artistName: String?,
    val playSessionCount: Long,
    val listenedMs: Long,
)

data class OwnerTopArtist(
    val artistName: String?,
    val playSessionCount: Long,
    val listenedMs: Long,
)
