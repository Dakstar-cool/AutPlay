package app.autplay.application.artist

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.ArtistCreditProjectionEntity
import app.autplay.data.local.entity.ArtistProjectionEntity
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

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
    val creditId: ArtistCreditId,
    val subjectType: String,
    val subjectId: ServerId,
    val title: String?,
    val localTarget: ArtistLocalTarget?,
)

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
}

class RoomArtistCatalogPort(private val database: AutPlayDatabase) : ArtistCatalogPort {
    override suspend fun browse(profileId: ServerProfileId, limit: Int): List<ArtistSummary> {
        require(limit in 1..MAX_PAGE)
        return database.catalogProjectionDao().ownedArtists(profileId.value, limit).map {
            it.toSummary(profileId)
        }
    }

    override fun observeBrowse(profileId: ServerProfileId, limit: Int): Flow<List<ArtistSummary>> {
        require(limit in 1..MAX_PAGE)
        return database.catalogProjectionDao().observeOwnedArtists(profileId.value, limit).map { rows ->
            rows.map { it.toSummary(profileId) }
        }
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
        val owned = dao.ownedArtist(key.profileId.value, key.artistId.value) ?: return null
        val creditRows = dao.ownedCreditsForArtist(
            key.profileId.value,
            key.artistId.value,
            creditLimit,
        )
        val membersByCredit = if (creditRows.isEmpty()) emptyMap() else dao.namesForCredits(
            key.profileId.value,
            creditRows.map { it.serverArtistCreditId },
            memberLimit,
        ).groupBy { it.serverArtistCreditId }
        val credits = creditRows.map { it.toCredit(membersByCredit[it.serverArtistCreditId].orEmpty()) }
        return ArtistDetail(owned.toSummary(key.profileId), credits)
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
        val links = database.catalogProjectionDao().ownedSubjectLinksForArtist(
            key.profileId.value,
            key.artistId.value,
            limit,
        )
        if (links.isEmpty()) return emptyList()
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
        return links.map { link ->
            val trackRef = trackRefs[link.subjectServerId]
            val recording = recordings[link.subjectServerId]
            val release = releases[link.subjectServerId]
            ArtistAppearance(
                creditId = ArtistCreditId(link.serverArtistCreditId),
                subjectType = link.subjectType,
                subjectId = ServerId(link.subjectServerId),
                title = when (link.subjectType) {
                    SUBJECT_RECORDING -> recording?.title ?: trackRef?.rawTitle
                    SUBJECT_RELEASE -> release?.title
                    else -> null
                },
                localTarget = when (link.subjectType) {
                    SUBJECT_RECORDING -> trackRef?.let { ArtistLocalTarget.Track(it.localUserTrackRefId) }
                    SUBJECT_RELEASE -> release?.let { ArtistLocalTarget.Release(it.localReleaseId) }
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
        const val MAX_PAGE = 500
        const val MAX_MEMBERS = 1_000
        const val MAX_DETAIL_MEMBERS = 5_000L
        const val SUBJECT_RECORDING = "RECORDING"
        const val SUBJECT_RELEASE = "RELEASE"
    }
}
