package app.autplay.application.artist

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.dao.LibraryArtistTrack
import app.autplay.data.local.entity.ArtistCreditProjectionEntity
import app.autplay.data.local.entity.ArtistProjectionEntity
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.text.Normalizer
import java.util.Locale
import java.util.UUID

@JvmInline
value class ArtistId(val value: String) {
    init { ServerId(value) }
}

@JvmInline
value class ArtistCreditId(val value: String) {
    init { ServerId(value) }
}

data class ArtistKey(val profileId: ServerProfileId, val artistId: ArtistId)

/** Unknown server strings are retained verbatim instead of being coerced to client enums. */
data class ArtistSummary(
    val key: ArtistKey,
    val name: String,
    val sortName: String?,
    val artistType: String?,
    val disambiguation: String?,
    val countryCode: String?,
    val identityStatus: String?,
)

data class ArtistCreditMember(
    val artistId: ArtistId,
    val position: Int,
    val creditedName: String,
    val joinPhrase: String,
    val role: String,
)

/** An empty [members] list is a valid unresolved legacy credit. */
data class ArtistCredit(
    val id: ArtistCreditId,
    val displayName: String?,
    val members: List<ArtistCreditMember>,
)

data class ArtistCreditSubjectLink(
    val subjectType: String,
    val subjectId: ServerId,
    val creditId: ArtistCreditId,
)

data class ArtistDetail(val summary: ArtistSummary, val credits: List<ArtistCredit>)

sealed interface ArtistLocalTarget {
    data class Track(val localUserTrackRefId: String) : ArtistLocalTarget
    data class Release(val localReleaseId: String) : ArtistLocalTarget
}

/** Owner-visible catalog subject; unknown/new subject types remain non-navigable. */
data class ArtistAppearance(
    val creditId: ArtistCreditId?,
    val subjectType: String,
    val subjectId: ServerId?,
    val title: String?,
    val localTarget: ArtistLocalTarget?,
    val listenCount: Int = 0,
    val guest: Boolean = false,
    val releaseDate: String? = null,
    val releaseStatus: String? = null,
    val releaseMbid: String? = null,
) {
    val displayKey: String get() = subjectId?.value ?: "local:${(localTarget as ArtistLocalTarget.Track).localUserTrackRefId}"
}

/** Local-first application seam; every read is scoped to a currently owned profile closure. */
interface ArtistCatalogPort {
    suspend fun browse(profileId: ServerProfileId, limit: Int): List<ArtistSummary>
    fun observeBrowse(profileId: ServerProfileId, limit: Int): Flow<List<ArtistSummary>>
    suspend fun detail(key: ArtistKey, creditLimit: Int, memberLimit: Int): ArtistDetail?
    suspend fun credit(
        profileId: ServerProfileId,
        creditId: ArtistCreditId,
        memberLimit: Int,
    ): ArtistCredit?
    suspend fun subjectCredits(
        profileId: ServerProfileId,
        subjectType: String,
        subjectId: ServerId,
        limit: Int,
    ): List<ArtistCreditSubjectLink>
    suspend fun appearances(key: ArtistKey, limit: Int): List<ArtistAppearance>
    suspend fun allAppearances(key: ArtistKey): List<ArtistAppearance>
}

class RoomArtistCatalogPort(private val database: AutPlayDatabase) : ArtistCatalogPort {
    private data class ArtistGroup(val artist: ArtistSummary, val ids: List<String>, val extraTracks: List<LibraryArtistTrack>, val personal: Boolean = true)

    private fun nameKey(name: String): String =
        Normalizer.normalize(name, Normalizer.Form.NFKC).trim().replace(SPACES, " ").lowercase(Locale.ROOT)

    private fun likedArtistKey(profile: ServerProfileId, name: String): ArtistKey {
        val id = UUID.nameUUIDFromBytes("library-liked-artist:${profile.value}:$name".toByteArray())
        return ArtistKey(profile, ArtistId(id.toString()))
    }

    private fun libraryArtists(profile: ServerProfileId): Flow<List<ArtistGroup>> {
        val dao = database.catalogProjectionDao()
        return combine(dao.observeOwnedArtists(profile.value, Int.MAX_VALUE), dao.libraryTracksWithoutArtistCredit(profile.value)) { artists, tracks ->
            val tracksByName = tracks.filter { !it.artist.isNullOrBlank() }.groupBy { nameKey(it.artist!!) }.toMutableMap()
            val result = mutableListOf<ArtistGroup>()
            for ((name, rows) in artists.groupBy { nameKey(it.name) }) {
                // An active catalog ID identifies one artist even when every label is identical.
                val (provisional, identified) = rows.partition { it.identityStatus == "PROVISIONAL" }
                identified.forEach { artist ->
                    result += ArtistGroup(artist.toSummary(profile), listOf(artist.serverArtistId), emptyList())
                }
                val tentative = provisional.groupBy {
                    Triple(nameKey(it.disambiguation.orEmpty()), nameKey(it.countryCode.orEmpty()), nameKey(it.artistType.orEmpty()))
                }
                for (members in tentative.values) {
                    val representative = members.minBy { it.serverArtistId }.toSummary(profile)
                    val extraTracks = if (identified.isEmpty() && tentative.size == 1) tracksByName.remove(name).orEmpty() else emptyList()
                    result += ArtistGroup(representative, members.map { it.serverArtistId }, extraTracks)
                }
            }
            for ((name, extraTracks) in tracksByName) {
                val summary = ArtistSummary(likedArtistKey(profile, name), extraTracks.first().artist!!.trim(), null, null, null, null, null)
                result += ArtistGroup(summary, emptyList(), extraTracks)
            }
            result.sortedWith(compareBy({ nameKey(it.artist.sortName ?: it.artist.name) }, { it.artist.key.artistId.value }))
        }
    }

    private suspend fun group(key: ArtistKey): ArtistGroup? {
        val groups = libraryArtists(key.profileId).first()
        return groups.firstOrNull { key == it.artist.key || key.artistId.value in it.ids }
            ?: groups.singleOrNull { likedArtistKey(key.profileId, nameKey(it.artist.name)) == key }
            ?: database.catalogProjectionDao().ownedArtist(key.profileId.value, key.artistId.value)?.let {
                ArtistGroup(it.toSummary(key.profileId), listOf(it.serverArtistId), emptyList(), personal = false)
            }
    }

    fun observeTrackArtists(profile: ServerProfileId, trackId: String): Flow<List<ArtistSummary>> =
        database.catalogProjectionDao().observeTrackArtists(profile.value, trackId).map { rows ->
            rows.map { it.toSummary(profile) }
        }

    override suspend fun browse(profileId: ServerProfileId, limit: Int): List<ArtistSummary> {
        return observeBrowse(profileId, limit).first()
    }

    override fun observeBrowse(profileId: ServerProfileId, limit: Int): Flow<List<ArtistSummary>> {
        require(limit in 1..MAX_PAGE)
        return libraryArtists(profileId).map { groups -> groups.take(limit).map { it.artist } }
    }

    override suspend fun detail(
        key: ArtistKey,
        creditLimit: Int,
        memberLimit: Int,
    ): ArtistDetail? {
        require(
            creditLimit in 1..MAX_PAGE && memberLimit in 1..MAX_MEMBERS &&
                creditLimit.toLong() * memberLimit <= MAX_DETAIL_MEMBERS
        )
        val dao = database.catalogProjectionDao()
        val group = group(key) ?: return null
        val creditRows = group.ids.chunked(MAX_PAGE).flatMap {
            dao.ownedCreditsForArtist(key.profileId.value, it, creditLimit)
        }.distinctBy { it.serverArtistCreditId }.take(creditLimit)
        val membersByCredit = if (creditRows.isEmpty()) emptyMap() else dao.namesForCredits(
            key.profileId.value,
            creditRows.map { it.serverArtistCreditId },
            memberLimit,
        ).groupBy { it.serverArtistCreditId }
        val credits = creditRows.map { it.toCredit(membersByCredit[it.serverArtistCreditId].orEmpty()) }
        return ArtistDetail(group.artist, credits)
    }

    override suspend fun credit(
        profileId: ServerProfileId,
        creditId: ArtistCreditId,
        memberLimit: Int,
    ): ArtistCredit? {
        require(memberLimit in 1..MAX_MEMBERS)
        val dao = database.catalogProjectionDao()
        return dao.ownedArtistCredit(profileId.value, creditId.value)?.let { credit ->
            credit.toCredit(dao.namesForCredit(profileId.value, creditId.value, memberLimit))
        }
    }

    override suspend fun subjectCredits(
        profileId: ServerProfileId,
        subjectType: String,
        subjectId: ServerId,
        limit: Int,
    ): List<ArtistCreditSubjectLink> {
        require(subjectType.length in 1..100 && limit in 1..MAX_PAGE)
        return database.catalogProjectionDao().creditLinksForSubject(
            profileId.value,
            subjectType,
            subjectId.value,
            limit,
        ).map {
            ArtistCreditSubjectLink(
                it.subjectType,
                ServerId(it.subjectServerId),
                ArtistCreditId(it.serverArtistCreditId),
            )
        }
    }

    override suspend fun appearances(key: ArtistKey, limit: Int): List<ArtistAppearance> {
        require(limit in 1..MAX_PAGE)
        val group = group(key) ?: return emptyList()
        return projectAppearances(key, group, limit)
    }

    override suspend fun allAppearances(key: ArtistKey): List<ArtistAppearance> {
        val group = group(key) ?: return emptyList()
        return projectAppearances(key, group, null)
    }

    private suspend fun projectAppearances(key: ArtistKey, group: ArtistGroup, limit: Int?): List<ArtistAppearance> {
        val dao = database.catalogProjectionDao()
        val pages = mutableListOf<ArtistAppearance>()
        for (ids in group.ids.chunked(MAX_PAGE)) {
            var offset = 0
            val pageSize = limit ?: MAX_PAGE
            do {
                val rows = dao.ownedSubjectLinksForArtist(key.profileId.value, ids, pageSize, group.personal, offset)
                pages += projectPage(key, rows)
                offset += rows.size
            } while (limit == null && rows.size == pageSize)
        }
        val extraTracks = group.extraTracks.map { track ->
            ArtistAppearance(null, SUBJECT_RECORDING, track.recordingId?.let(::ServerId), track.title, ArtistLocalTarget.Track(track.trackId))
        }
        val merged = (extraTracks + pages).distinctBy { it.subjectType to it.displayKey }
        return if (limit == null) merged else merged.take(limit)
    }

    private suspend fun projectPage(key: ArtistKey, rows: List<app.autplay.data.local.dao.ArtistAppearanceRow>): List<ArtistAppearance> {
        val links = rows.map { it.link }
        val recordingIds = links.asSequence().filter { it.subjectType == SUBJECT_RECORDING }
            .map { it.subjectServerId }.distinct().toList()
        val releaseIds = links.asSequence().filter { it.subjectType == SUBJECT_RELEASE }
            .map { it.subjectServerId }.distinct().toList()
        val trackRefs = if (recordingIds.isEmpty()) emptyMap() else {
            database.libraryDao().trackRefsByServerRecordings(
                key.profileId.value,
                recordingIds,
                recordingIds.size,
            ).groupBy { checkNotNull(it.serverRecordingId) }.mapValues { (_, rows) -> rows.first() }
        }
        val recordings = if (recordingIds.isEmpty()) emptyMap() else {
            database.catalogProjectionDao().recordingsByServerIds(recordingIds, recordingIds.size)
                .associateBy { checkNotNull(it.serverRecordingId) }
        }
        val releases = if (releaseIds.isEmpty()) emptyMap() else {
            database.catalogProjectionDao().releasesByServerIdsForProfile(
                key.profileId.value,
                releaseIds,
                releaseIds.size,
            )
                .associateBy { checkNotNull(it.serverReleaseId) }
        }
        val editions = releaseIds.chunked(MAX_PAGE).flatMap { database.catalogProjectionDao().editionTracks(key.profileId.value, it) }
            .associateBy { it.releaseId }
        return rows.map { (link, plays, guest) ->
            val trackRef = trackRefs[link.subjectServerId]
            val recording = recordings[link.subjectServerId]
            val release = releases[link.subjectServerId]
            ArtistAppearance(
                creditId = ArtistCreditId(link.serverArtistCreditId),
                subjectType = link.subjectType,
                subjectId = ServerId(link.subjectServerId),
                title = when (link.subjectType) {
                    SUBJECT_RECORDING -> recording?.title ?: trackRef?.rawTitle
                    SUBJECT_RELEASE -> editions[link.subjectServerId]?.album ?: release?.title
                    else -> null
                },
                listenCount = plays,
                guest = guest,
                releaseDate = editions[link.subjectServerId]?.releaseDate ?: release?.releaseDateText,
                releaseStatus = editions[link.subjectServerId]?.releaseStatus,
                releaseMbid = editions[link.subjectServerId]?.releaseMbid,
                localTarget = when (link.subjectType) {
                    SUBJECT_RECORDING -> trackRef?.let { ArtistLocalTarget.Track(it.localUserTrackRefId) }
                    SUBJECT_RELEASE -> release?.let { ArtistLocalTarget.Release(it.localReleaseId) }
                        ?: editions[link.subjectServerId]?.let { ArtistLocalTarget.Release(app.autplay.application.library.editionTargetId(it.releaseId)) }
                    else -> null
                },
            )
        }

    }

    private fun ArtistCreditProjectionEntity.toCredit(
        rows: List<app.autplay.data.local.entity.ArtistCreditNameProjectionEntity>,
    ): ArtistCredit {
        val members = rows.map {
            ArtistCreditMember(
                ArtistId(it.serverArtistId),
                it.position,
                it.creditedName,
                it.joinPhrase,
                it.role,
            )
        }
        return ArtistCredit(ArtistCreditId(serverArtistCreditId), displayName, members)
    }

    private fun ArtistProjectionEntity.toSummary(profileId: ServerProfileId) = ArtistSummary(
        ArtistKey(profileId, ArtistId(serverArtistId)),
        name,
        sortName,
        artistType,
        disambiguation,
        countryCode,
        identityStatus,
    )

    private companion object {
        val SPACES = Regex("[\\s\\p{Z}]+")
        const val MAX_PAGE = 500
        const val MAX_MEMBERS = 1_000
        const val MAX_DETAIL_MEMBERS = 5_000L
        const val SUBJECT_RECORDING = "RECORDING"
        const val SUBJECT_RELEASE = "RELEASE"
    }
}
