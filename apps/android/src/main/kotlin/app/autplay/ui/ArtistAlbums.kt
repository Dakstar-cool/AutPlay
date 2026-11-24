package app.autplay.ui

import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.artist.ArtistLocalTarget
import app.autplay.application.server.InternetMetadataDiscoveryCard
import app.autplay.application.server.InternetMetadataDiscoveryEntity
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget

internal data class ArtistAlbumTile(
    val key: String,
    val title: String?,
    val date: String?,
    val official: Boolean,
    val localId: String?,
    val mbid: String?,
) {
    val target: DetailTarget? get() = localId?.let { DetailTarget(DetailKind.Release, it) }
        ?: mbid?.let { DetailTarget(DetailKind.CatalogRelease, it, title) }
}

internal fun artistAlbums(
    appearances: List<ArtistAppearance>,
    publicEditions: List<InternetMetadataDiscoveryCard>,
    localAlbums: List<app.autplay.application.library.CoreReleaseSummary> = emptyList(),
): List<ArtistAlbumTile> {
    val albums = linkedMapOf<String, ArtistAlbumTile>()
    for (row in appearances.filter { it.subjectType == "RELEASE" }) {
        val key = row.releaseMbid ?: "local:${row.displayKey}"
        albums[key] = ArtistAlbumTile(row.displayKey, row.title, row.releaseDate, row.releaseStatus == "OFFICIAL",
            (row.localTarget as? ArtistLocalTarget.Release)?.localReleaseId, row.releaseMbid)
    }
    for (album in localAlbums) {
        val existing = albums.values.firstOrNull { it.localId == album.stableId }
        if (existing == null) albums.putIfAbsent(album.mbid ?: "local:${album.stableId}",
            ArtistAlbumTile(album.stableId, album.title, album.releaseDateText, false, album.stableId, album.mbid))
    }
    for (card in publicEditions) {
        if (card.browseTarget?.entity != InternetMetadataDiscoveryEntity.RELEASE) continue
        val local = albums[card.entityId]
        albums[card.entityId] = ArtistAlbumTile(local?.key ?: card.entityId, card.title,
            card.releaseDate ?: local?.date, true, local?.localId, card.entityId)
    }
    return albums.values.sortedWith(compareByDescending<ArtistAlbumTile> { it.date }
        .thenBy(String.CASE_INSENSITIVE_ORDER) { it.title.orEmpty() }.thenBy { it.key })
}
