package app.autplay.application.artist

import app.autplay.application.library.names
import app.autplay.application.library.decoded
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.dao.LibraryArtistTrack
import app.autplay.data.local.entity.ArtistCreditProjectionEntity
import app.autplay.data.local.entity.ArtistProjectionEntity
import app.autplay.domain.ServerId
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.flowOn
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

// Local-only artists use the nil UUID for browse keys. Their rows stay in the standalone Room scope.
private val standaloneArtistScope = ServerProfileId(UUID(0, 0).toString())

internal fun artistScope(profileId: String?): ServerProfileId = profileId?.let(::ServerProfileId) ?: standaloneArtistScope

private val ServerProfileId.artistStorageScope: String
    get() = if (this == standaloneArtistScope) "legacy-unscoped" else value

/** Unknown server strings are retained verbatim instead of being coerced to client enums. */
data class ArtistSummary(
    val key: ArtistKey,
    val name: String,
    val sortName: String?,
    val artistType: String?,
    val disambiguation: String?,
    val countryCode: String?,
    val identityStatus: String?,
    val preferenceByName: Boolean = false,
    val portraitTrackIds: List<String> = emptyList(),
    val portraitArtistIds: List<String> = emptyList(),
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
    val performers: List<ArtistSummary> = emptyList(),
    val releaseGroup: String? = null,
    val albumArtist: String? = null,
) {
    val displayKey: String get() = subjectId?.value ?: "local:${(localTarget as ArtistLocalTarget.Track).localUserTrackRefId}"
}

/** Local-first application seam; every read is scoped to a currently owned profile closure. */
interface ArtistCatalogPort {
    suspend fun browse(profileId: ServerProfileId, limit: Int): List<ArtistSummary>
    fun observeBrowse(profileId: ServerProfileId, limit: Int): Flow<List<ArtistSummary>>
    suspend fun detail(key: ArtistKey, creditLimit: Int, memberLimit: Int): ArtistDetail?
    suspend fun loadArtistPage(
        key: ArtistKey,
        creditLimit: Int,
        memberLimit: Int,
        onDetail: (ArtistDetail) -> Unit,
    ): List<ArtistAppearance>? {
        val artist = detail(key, creditLimit, memberLimit) ?: return null
        onDetail(artist)
        return allAppearances(key)
    }

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
        val id = UUID.nameUUIDFromBytes("library-liked-artist:${profile.artistStorageScope}:$name".toByteArray())
        return ArtistKey(profile, ArtistId(id.toString()))
    }

    private fun artistGroups(profile: ServerProfileId, personal: Boolean): Flow<List<ArtistGroup>> {
        val dao = database.catalogProjectionDao()
        val catalog = if (personal) dao.observeOwnedArtists(profile.artistStorageScope, Int.MAX_VALUE)
            else dao.observeAccessibleArtists(profile.artistStorageScope, Int.MAX_VALUE)
        return combine(catalog, dao.libraryTracksWithoutArtistCredit(profile.artistStorageScope, personal)) { rows, tracks ->
            val catalogNames = rows.flatMap { row ->
                val names = if (row.identityStatus == "PROVISIONAL") displayArtists(row.name) else listOf(artistDisplayName(row.name))
                names.map { name -> Triple(name, row, names.size == 1) }
            }.groupBy { nameKey(it.first) }
            val tags = tracks.flatMap { row ->
                val names = trackNames(row.nameInput, row.artist, row.title, row.hasTitle, row.hasArtist, row.cleanSourceTitle, row.cleanSourceArtist)
                val track = row.copy(title = names.title, artist = names.artist)
                displayArtists(track.artist.orEmpty()).map { it to track }
            }
                .groupBy { nameKey(it.first) }
            val result = mutableListOf<ArtistGroup>()
            for (key in catalogNames.keys + tags.keys) {
                val credits = catalogNames[key].orEmpty()
                val (identified, provisional) = credits.partition { it.second.identityStatus != "PROVISIONAL" }
                val extras = tags[key].orEmpty()
                // Explicitly distinct catalog identities stay individually addressable.
                if (identified.size > 1) {
                    identified.forEach { (_, row) -> result += ArtistGroup(row.toSummary(profile), listOf(row.serverArtistId), emptyList(), personal) }
                }
                val groups = provisional.groupBy { (_, row) ->
                    listOf(row.disambiguation, row.countryCode, row.artistType?.takeUnless { it == "UNKNOWN" }).map { nameKey(it.orEmpty()) }
                }
                val compatible = identified.singleOrNull()?.second
                val matchingGroups = groups.values.filter { members ->
                    val row = members.first().second
                    compatible != null && listOf(row.disambiguation to compatible.disambiguation, row.countryCode to compatible.countryCode,
                        row.artistType to compatible.artistType).all { (a, b) -> a.isNullOrBlank() || b.isNullOrBlank() ||
                            a == "UNKNOWN" || b == "UNKNOWN" || a.equals(b, ignoreCase = true) }
                }
                // A canonical row with sparse evidence can match two mutually exclusive namesakes.
                val joined = matchingGroups.singleOrNull().orEmpty()
                // A release artist with the same display name alone does not identify a raw track performer.
                val takeTags = if (compatible != null) {
                    (joined.isNotEmpty() || compatible.serverArtistId == ARTIST_25_17_MBID) &&
                        joined.size == provisional.size && groups.size <= 1
                } else identified.isEmpty() && groups.size <= 1
                if (compatible != null) result += ArtistGroup(compatible.toSummary(profile),
                    listOf(compatible.serverArtistId) + joined.map { it.second.serverArtistId },
                    if (takeTags) extras.map { it.second } else emptyList(), personal)
                for (members in groups.values) {
                    val remaining = members - joined.toSet()
                    if (remaining.isEmpty()) continue
                    val single = remaining.filter { it.third }.minByOrNull { it.second.serverArtistId }
                    val name = remaining.first().first
                    val distinctName = identified.isEmpty() && groups.size == 1
                    val syntheticKey = if (distinctName) key else key + ":" + remaining.minOf { it.second.serverArtistId }
                    val summary = single?.second?.toSummary(profile)?.copy(name = name, preferenceByName = distinctName)
                        ?: ArtistSummary(likedArtistKey(profile, syntheticKey), name, null, null, null, null, null, distinctName)
                    result += ArtistGroup(summary, remaining.map { it.second.serverArtistId },
                        if (identified.isEmpty() && takeTags) extras.map { it.second } else emptyList(), personal)
                }
                if (extras.isNotEmpty() && (credits.isEmpty() || !takeTags)) {
                    result += ArtistGroup(ArtistSummary(likedArtistKey(profile, key), extras.first().first,
                        null, null, null, null, null, credits.isEmpty()), emptyList(), extras.map { it.second }, personal)
                }
            }
            result.map { group ->
                group.copy(artist = group.artist.copy(portraitTrackIds = group.extraTracks.map { it.trackId },
                    portraitArtistIds = group.ids))
            }.sortedWith(compareBy({ nameKey(it.artist.name) }, { it.artist.key.artistId.value }))
        }.flowOn(Dispatchers.Default)
    }

    private suspend fun group(key: ArtistKey): ArtistGroup? = group(key, artistGroups(key.profileId, personal = false).first())

    private fun group(key: ArtistKey, groups: List<ArtistGroup>): ArtistGroup? {
        return groups.firstOrNull { key == it.artist.key }
            ?: groups.firstOrNull { key.artistId.value in it.ids }
            ?: groups.singleOrNull { likedArtistKey(key.profileId, nameKey(it.artist.name)) == key }
    }

    fun observeTrackArtists(profile: ServerProfileId, trackId: String): Flow<List<ArtistSummary>> =
        combine(database.catalogProjectionDao().observeTrackArtists(profile.artistStorageScope, trackId),
            artistGroups(profile, personal = false)) { credits, groups ->
            val byCredit = credits.flatMap { row ->
                val names = displayArtists(row.name).map(::nameKey)
                groups.filter { row.serverArtistId in it.ids }.sortedBy { names.indexOf(nameKey(it.artist.name)) }
            }
            val tagged = groups.filter { group -> group.extraTracks.any { it.trackId == trackId } }
            val raw = tagged.firstOrNull()?.extraTracks?.firstOrNull { it.trackId == trackId }?.artist
            (byCredit.map { it.artist } + orderedPerformers(raw, tagged.map { it.artist })).distinctBy { it.key }
        }.flowOn(Dispatchers.Default)

    fun search(profile: ServerProfileId, query: String, personal: Boolean = false): Flow<List<ArtistSummary>> =
        artistGroups(profile, personal).map { groups ->
            groups.map { it.artist }.filter { artistNameMatches(it.name, query) }
                .sortedByDescending { it.name.equals(query.trim(), ignoreCase = true) }.take(MAX_PAGE)
        }.flowOn(Dispatchers.Default)

    override suspend fun browse(profileId: ServerProfileId, limit: Int): List<ArtistSummary> {
        return observeBrowse(profileId, limit).first()
    }

    override fun observeBrowse(profileId: ServerProfileId, limit: Int): Flow<List<ArtistSummary>> {
        require(limit in 1..MAX_PAGE)
        return artistGroups(profileId, personal = true).map { groups -> groups.take(limit).map { it.artist } }
    }

    override suspend fun detail(
        key: ArtistKey,
        creditLimit: Int,
        memberLimit: Int,
    ): ArtistDetail? {
        checkDetailLimits(creditLimit, memberLimit)
        val group = group(key) ?: return null
        return readDetail(key, group, creditLimit, memberLimit)
    }

    override suspend fun loadArtistPage(
        key: ArtistKey,
        creditLimit: Int,
        memberLimit: Int,
        onDetail: (ArtistDetail) -> Unit,
    ): List<ArtistAppearance>? {
        checkDetailLimits(creditLimit, memberLimit)
        val groups = artistGroups(key.profileId, personal = false).first()
        val group = withContext(Dispatchers.Default) { group(key, groups) } ?: return null
        onDetail(ArtistDetail(group.artist, emptyList()))
        val detail = withContext(Dispatchers.Default) { readDetail(key, group, creditLimit, memberLimit) }
        if (detail.credits.isNotEmpty()) onDetail(detail)
        return withContext(Dispatchers.Default) { projectAppearances(key, groups, group, null) }
    }

    private fun checkDetailLimits(creditLimit: Int, memberLimit: Int) {
        require(
            creditLimit in 1..MAX_PAGE && memberLimit in 1..MAX_MEMBERS &&
                creditLimit.toLong() * memberLimit <= MAX_DETAIL_MEMBERS
        )
    }

    private suspend fun readDetail(key: ArtistKey, group: ArtistGroup, creditLimit: Int, memberLimit: Int): ArtistDetail {
        val dao = database.catalogProjectionDao()
        val creditRows = group.ids.chunked(MAX_PAGE).flatMap {
            dao.ownedCreditsForArtist(key.profileId.artistStorageScope, it, creditLimit)
        }.distinctBy { it.serverArtistCreditId }.take(creditLimit)
        val membersByCredit = if (creditRows.isEmpty()) emptyMap() else dao.namesForCredits(
            key.profileId.artistStorageScope,
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
        return dao.ownedArtistCredit(profileId.artistStorageScope, creditId.value)?.let { credit ->
            credit.toCredit(dao.namesForCredit(profileId.artistStorageScope, creditId.value, memberLimit))
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
            profileId.artistStorageScope,
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
        val groups = artistGroups(key.profileId, personal = false).first()
        val group = group(key, groups) ?: return emptyList()
        return withContext(Dispatchers.Default) { projectAppearances(key, groups, group, limit) }
    }

    override suspend fun allAppearances(key: ArtistKey): List<ArtistAppearance> {
        val groups = artistGroups(key.profileId, personal = false).first()
        val group = group(key, groups) ?: return emptyList()
        return withContext(Dispatchers.Default) { projectAppearances(key, groups, group, null) }
    }

    private fun orderedPerformers(credit: String?, artists: List<ArtistSummary>): List<ArtistSummary> {
        if (artists.size < 2) return artists
        val order = displayArtists(credit.orEmpty()).map(::artistSearchKey)
        return artists.sortedBy { order.indexOf(artistSearchKey(it.name)).takeIf { i -> i >= 0 } ?: Int.MAX_VALUE }
    }

    private suspend fun projectAppearances(key: ArtistKey, groups: List<ArtistGroup>, group: ArtistGroup, limit: Int?): List<ArtistAppearance> {
        val dao = database.catalogProjectionDao()
        val byId = groups.flatMap { member -> member.ids.map { it to member.artist } }.groupBy({ it.first }, { it.second })
        val extraTrackIds = group.extraTracks.mapTo(hashSetOf()) { it.trackId }
        val byTrack = if (extraTrackIds.isEmpty()) emptyMap() else groups.asSequence().flatMap { member ->
            member.extraTracks.asSequence().filter { it.trackId in extraTrackIds }.map { it.trackId to member.artist }
        }.groupBy({ it.first }, { it.second })
        val pages = mutableListOf<ArtistAppearance>()
        for (ids in group.ids.chunked(MAX_PAGE)) {
            var offset = 0
            val pageSize = limit ?: MAX_PAGE
            do {
                val rows = dao.ownedSubjectLinksForArtist(key.profileId.artistStorageScope, ids, pageSize, group.personal, offset)
                if (rows.isEmpty()) break
                val names = dao.namesForCredits(key.profileId.artistStorageScope, rows.map { it.link.serverArtistCreditId }.distinct(), MAX_MEMBERS)
                    .groupBy { it.serverArtistCreditId }
                pages += projectPage(key, rows).map { row ->
                    val performers = names[row.creditId?.value].orEmpty().flatMap { name ->
                        val linked = byId[name.serverArtistId].orEmpty()
                        val artist = linked.singleOrNull()
                        if (artist?.identityStatus == "ACTIVE") listOf(artist.copy(name = musicDisplayTitle(name.creditedName)))
                        else orderedPerformers(name.creditedName, linked)
                    }.distinctBy { it.key }
                    row.copy(performers = performers, guest = row.guest || performers.size > 1)
                }
                offset += rows.size
            } while (limit == null && rows.size == pageSize)
        }
        val plays = extraTrackIds.chunked(MAX_PAGE).flatMap { dao.trackListenCounts(key.profileId.artistStorageScope, it) }
            .associate { it.trackId to it.plays }
        val extraTracks = group.extraTracks.map { track ->
            ArtistAppearance(null, SUBJECT_RECORDING, track.recordingId?.let(::ServerId), track.title, ArtistLocalTarget.Track(track.trackId),
                listenCount = plays[track.trackId] ?: 0,
                guest = byTrack[track.trackId].orEmpty().size > 1, performers = orderedPerformers(track.artist, byTrack[track.trackId].orEmpty()))
        }
        val merged = (extraTracks.sortedByDescending { it.listenCount } + pages).distinctBy { it.subjectType to it.displayKey }
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
                key.profileId.artistStorageScope,
                recordingIds,
                recordingIds.size,
            ).groupBy { checkNotNull(it.serverRecordingId) }.mapValues { (_, rows) -> rows.first() }
        }
        val descriptions = trackRefs.values.map { it.localUserTrackRefId }.chunked(MAX_PAGE).flatMap {
            database.trackMetadataDao().forTracks(key.profileId.artistStorageScope, it)
        }.associateBy { it.localUserTrackRefId }
        val recordings = if (recordingIds.isEmpty()) emptyMap() else {
            database.catalogProjectionDao().recordingsByServerIds(recordingIds, recordingIds.size)
                .associateBy { checkNotNull(it.serverRecordingId) }
        }
        val releases = if (releaseIds.isEmpty()) emptyMap() else {
            database.catalogProjectionDao().releasesByServerIdsForProfile(
                key.profileId.artistStorageScope,
                releaseIds,
                releaseIds.size,
            )
                .associateBy { checkNotNull(it.serverReleaseId) }
        }
        val editions = releaseIds.chunked(MAX_PAGE).flatMap { database.catalogProjectionDao().editionTracks(key.profileId.artistStorageScope, it) }
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
                    SUBJECT_RECORDING -> descriptions[trackRef?.localUserTrackRefId]?.decoded().names(
                        trackRef?.rawTitle ?: recording?.title, trackRef?.rawArtist ?: recording?.displayArtist).title
                    SUBJECT_RELEASE -> editions[link.subjectServerId]?.album ?: release?.title
                    else -> null
                },
                listenCount = plays,
                guest = guest,
                releaseDate = editions[link.subjectServerId]?.releaseDate ?: release?.releaseDateText,
                releaseStatus = editions[link.subjectServerId]?.releaseStatus,
                releaseMbid = editions[link.subjectServerId]?.releaseMbid,
                releaseGroup = editions[link.subjectServerId]?.releaseGroupId ?: release?.serverReleaseGroupId,
                albumArtist = editions[link.subjectServerId]?.albumArtist ?: release?.displayArtist,
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
        if (identityStatus == "PROVISIONAL") musicDisplayCredit(name) else artistDisplayName(name),
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

internal fun artistNameMatches(name: String, query: String): Boolean {
    val needle = artistSearchKey(StageCredits.display(query.trim()))
    val label = artistSearchKey(StageCredits.display(name.trim()))
    if (needle.isEmpty()) return false
    if (needle in label) return true
    if (needle.length < 4) return false
    val edits = if (needle.length >= 8) 2 else 1
    return (listOf(label) + label.split(' ')).any { word ->
        if (kotlin.math.abs(needle.length - word.length) > edits) return@any false
        var previous = IntArray(needle.length + 1) { it }
        var beforePrevious = previous
        for (i in word.indices) {
            val current = IntArray(needle.length + 1)
            current[0] = i + 1
            for (j in needle.indices) {
                current[j + 1] = minOf(previous[j + 1] + 1, current[j] + 1,
                    previous[j] + if (word[i] == needle[j]) 0 else 1)
                if (i > 0 && j > 0 && word[i] == needle[j - 1] && word[i - 1] == needle[j]) {
                    current[j + 1] = minOf(current[j + 1], beforePrevious[j - 1] + 1)
                }
            }
            if (current.min() > edits) return@any false
            beforePrevious = previous
            previous = current
        }
        previous.last() <= edits
    }
}
