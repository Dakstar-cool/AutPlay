package app.autplay.ui

import app.autplay.application.artist.musicSearchKey
import app.autplay.application.library.ARTIST_ALBUM_PREFIX
import app.autplay.application.library.editionTargetId
import kotlinx.serialization.json.JsonPrimitive
import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.server.InternetMetadataDiscoveryCard
import app.autplay.application.server.InternetMetadataDiscoveryEntity
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget

internal fun releaseYear(date: String?): String? =
    date?.substringBefore('-')?.takeIf { it.length == 4 && it.all(Char::isDigit) }

internal data class ArtistAlbumTile(
    val key: String,
    val title: String?,
    val date: String?,
    val official: Boolean,
    val localId: String?,
    val mbid: String?,
    val artist: String? = null,
    val group: String? = null,
    val artworkTrackId: String? = null,
) {
    val target: DetailTarget? get() = localId?.let { DetailTarget(DetailKind.Release, it) }
        ?: mbid?.let { DetailTarget(DetailKind.CatalogRelease, it, title, artist) }
}

internal fun artistAlbums(
    appearances: List<ArtistAppearance>,
    publicEditions: List<InternetMetadataDiscoveryCard>,
    localAlbums: List<app.autplay.application.library.CoreReleaseSummary> = emptyList(),
): List<ArtistAlbumTile> {
    val cards = publicEditions.filter { it.browseTarget?.entity == InternetMetadataDiscoveryEntity.RELEASE }
        .associateBy { it.entityId }
    val catalogRows = appearances.filter { it.subjectType == "RELEASE" }
    val artwork = localAlbums.associate { it.stableId to it.artworkTrackId }
    val local = catalogRows.map { row ->
        val localId = (row.localTarget as? ArtistLocalTarget.Release)?.localReleaseId
        ArtistAlbumTile(row.displayKey, row.title, row.releaseDate, row.releaseStatus == "OFFICIAL",
            localId, row.releaseMbid, row.albumArtist, row.releaseGroup,
            artwork[localId] ?: row.subjectId?.let { artwork[editionTargetId(it.value)] })
    }
    val seen = local.mapNotNull { it.localId }.toSet() + catalogRows.filter { it.localTarget != null }
        .mapNotNull { it.subjectId?.value?.let(::editionTargetId) }
    val tiles = local + localAlbums.filterNot { it.stableId in seen }.map { album ->
        ArtistAlbumTile(album.stableId, album.title, album.releaseDateText, false, album.stableId, album.mbid,
            album.artistName, album.releaseGroup, album.artworkTrackId)
    }
    fun publicGroup(mbid: String?) = (cards[mbid]?.rawPayload?.get("release_group_id") as? JsonPrimitive)?.content
    val publicByLocalGroup = tiles.mapNotNull { tile ->
        tile.group?.let { group -> publicGroup(tile.mbid)?.let { group to it } }
    }.toMap()
    val localMbids = tiles.mapNotNull { it.mbid }.toSet()
    val albums = tiles.map { tile ->
        val card = cards[tile.mbid]
        tile.copy(title = card?.title ?: tile.title, artist = card?.artist ?: tile.artist,
            date = card?.releaseDate ?: tile.date, official = tile.official || card != null,
            group = publicGroup(tile.mbid) ?: publicByLocalGroup[tile.group] ?: tile.group)
    } + cards.values.filterNot { it.entityId in localMbids }.map { card ->
        ArtistAlbumTile(card.entityId, card.title, card.releaseDate, true, null, card.entityId, card.artist, publicGroup(card.entityId))
    }
    // One artist page can contain several territory editions, each owning different tracks.
    // Keep unrelated same-name groups apart when the catalogue distinguishes them.
    fun label(tile: ArtistAlbumTile) = tile.artist?.let(::musicSearchKey) to tile.title?.let(::musicSearchKey)
    val groupsByLabel = albums.groupBy(::label).mapValues { (_, tiles) -> tiles.mapNotNull { it.group }.distinct() }
    val catalogKeys = local.map { it.key }.toSet()
    return albums.groupBy { tile ->
        val label = label(tile)
        val known = groupsByLabel[label].orEmpty()
        val group = tile.group ?: known.singleOrNull()
        when {
            group != null -> "group:$group"
            label.first.isNullOrBlank() || label.second.isNullOrBlank() || known.size > 1 -> "edition:${tile.key}"
            else -> "label:${label.first}|${label.second}"
        }
    }.values.map { editions ->
        val first = editions.sortedWith(compareByDescending<ArtistAlbumTile> { it.localId != null }
            .thenByDescending { it.key in catalogKeys }
            .thenByDescending { it.mbid != null }.thenBy { it.date ?: "9999" }.thenBy { it.key }).first()
        val ids = editions.mapNotNull { it.localId }.distinct().sorted()
        first.copy(localId = if (ids.size < 2) first.localId else ARTIST_ALBUM_PREFIX + ids.joinToString("|"),
            date = first.date ?: editions.mapNotNull { it.date }.minOrNull(),
            artworkTrackId = first.artworkTrackId ?: editions.firstNotNullOfOrNull { it.artworkTrackId },
            mbid = first.mbid ?: editions.firstNotNullOfOrNull { it.mbid }, official = editions.any { it.official })
    }.sortedWith(compareByDescending<ArtistAlbumTile> { it.date }
        .thenBy(String.CASE_INSENSITIVE_ORDER) { it.title.orEmpty() }.thenBy { it.key })
}
