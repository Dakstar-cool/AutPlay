package app.autplay.application.search

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.application.library.decoded

/** Call within the aggregate's Room write transaction; retain row identity and search aliases. */
internal suspend fun AutPlayDatabase.refreshTrackSearch(trackRefId: String) {
    val track = libraryDao().trackRef(trackRefId) ?: return
    if (track.deletedAtMs != null) return
    val existing = searchDao().contentForTrack(trackRefId)
    val metadata = trackMetadataDao().get(track.serverProfileId, trackRefId)?.decoded()
    val projection = (existing ?: TrackSearchContentEntity(
        localUserTrackRefId = trackRefId, title = null, artist = null, album = null,
        aliases = null, transliterations = null,
    )).copy(title = if (metadata == null) track.rawTitle else metadata.text("title", track.rawTitle),
        artist = if (metadata == null) track.rawArtist else metadata.text("artist", track.rawArtist),
        album = if (metadata == null) track.rawAlbum else metadata.text("album", track.rawAlbum))
    if (existing == null) searchDao().insertContent(projection)
    else if (existing != projection) searchDao().updateContent(projection)
}
