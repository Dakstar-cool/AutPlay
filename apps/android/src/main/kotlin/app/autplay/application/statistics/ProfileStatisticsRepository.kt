package app.autplay.application.statistics

import androidx.room3.withReadTransaction
import app.autplay.application.library.TrackMetadata
import app.autplay.data.local.AutPlayDatabase
import java.time.Clock
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject

/** Explicit local snapshot reads. Playback, Room invalidation and day rollover never refresh it. */
class ProfileStatisticsRepository(
    private val database: AutPlayDatabase,
    private val clock: Clock = Clock.systemDefaultZone(),
) {
    suspend fun refresh(profileId: String?): OwnerProfileStatistics = database.withReadTransaction {
        val owner = profileId ?: "legacy-unscoped"
        val through = clock.millis()
        val dao = database.historyDao()
        val genres = mutableMapOf<String, OwnerTopGenre>()
        var afterTrackId = ""
        do {
            val page = dao.ownerGenreSourcesPage(owner, through, afterTrackId, GENRE_PAGE_SIZE)
            page.forEach { row ->
                metadataGenres(row.payloadJson).forEach { genre ->
                    val key = genre.lowercase(Locale.ROOT)
                    val previous = genres[key]
                    genres[key] = OwnerTopGenre(previous?.genre ?: genre, (previous?.listenedMs ?: 0) + row.listenedMs)
                }
            }
            page.lastOrNull()?.let { afterTrackId = it.localTrackRefId }
        } while (page.size == GENRE_PAGE_SIZE)
        OwnerProfileStatistics(
            throughMs = through,
            listenedMs = dao.ownerListenedMs(owner, through),
            topGenres = genres.values.sortedWith(compareByDescending<OwnerTopGenre> { it.listenedMs }.thenBy { it.genre }).take(5),
            topTracks = dao.ownerTopTracksSnapshot(owner, through, 5).map {
                OwnerTopTrack(it.identityKey, it.title, it.artistName, it.playSessionCount, it.listenedMs)
            },
            topArtists = dao.ownerTopArtistsSnapshot(owner, through, 5).map {
                OwnerTopArtist(it.artistName, it.playSessionCount, it.listenedMs)
            },
        )
    }

    /** One-shot compatibility adapter. New callers use refresh and an explicit snapshot owner. */
    fun observe(profileId: String?): Flow<OwnerProfileStatistics> = flow { emit(refresh(profileId)) }

    private companion object {
        const val GENRE_PAGE_SIZE = 200
    }
}

/** Only real, bounded metadata genres participate; malformed/unknown data stays unavailable. */
internal fun metadataGenres(payload: String): List<String> {
    if (payload.length > 140_000) return emptyList()
    val metadata = runCatching { TrackMetadata.decode(Json.parseToJsonElement(payload).jsonObject) }.getOrNull()
        ?: return emptyList()
    val values = metadata.fields["genres"] as? JsonArray ?: return emptyList()
    if (values.size > 12) return emptyList()
    return values.mapNotNull { value ->
        (value as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull?.trim()?.takeIf {
            it.length in 1..100 && it.none { character -> character.code < 32 }
        }
    }.distinctBy { it.lowercase(Locale.ROOT) }
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

/** Entire retained history of one owner, with a single inclusive upper cutoff for every ranking. */
data class OwnerProfileStatistics(
    val throughMs: Long,
    val listenedMs: Long,
    val topGenres: List<OwnerTopGenre>,
    val topTracks: List<OwnerTopTrack>,
    val topArtists: List<OwnerTopArtist>,
) {
    init {
        require(listenedMs >= 0)
        require(topGenres.size <= 5 && topTracks.size <= 5 && topArtists.size <= 5)
    }
}

data class OwnerTopGenre(val genre: String, val listenedMs: Long)

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
