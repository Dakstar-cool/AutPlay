package app.autplay.application.search

import android.content.SharedPreferences
import androidx.core.content.edit
import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.application.artist.isAudioFilename
import app.autplay.application.artist.compactSpelling
import app.autplay.application.artist.musicSearchKey
import app.autplay.application.artist.sourceEditTitle
import app.autplay.application.artist.stripSourceSite
import app.autplay.application.artist.trackNames
import app.autplay.application.library.allowsRawArtistAlias
import app.autplay.application.library.names
import app.autplay.application.library.decoded

/** Call within the aggregate's Room write transaction; retain row identity and search aliases. */
internal suspend fun AutPlayDatabase.refreshTrackSearch(trackRefId: String) {
    val track = libraryDao().trackRef(trackRefId) ?: return
    if (track.deletedAtMs != null) return
    val existing = searchDao().contentForTrack(trackRefId)
    val metadata = trackMetadataDao().get(track.serverProfileId, trackRefId)?.decoded()
    val names = metadata.names(track.rawTitle, track.rawArtist)
    val originalLabel = track.rawTitle?.let(::stripSourceSite)?.takeIf { names.title != null && it != names.title &&
        (isAudioFilename(it) || trackNames(it, track.rawArtist).title != it) }
    val compact = names.title?.let { compactSpelling(musicSearchKey(sourceEditTitle(it).substringBeforeLast(" ("))) }
    val indexedTitle = listOfNotNull(names.title, originalLabel, compact).distinct().joinToString(" ").ifEmpty { null }
    val rawCredit = track.rawArtist?.let(::stripSourceSite)?.takeIf { it.isNotBlank() }
    val projection = (existing ?: TrackSearchContentEntity(
        localUserTrackRefId = trackRefId, title = null, artist = null, album = null,
        aliases = null, transliterations = null,
    )).copy(title = indexedTitle,
        artist = names.artist?.let { preferred ->
            if (rawCredit == null || rawCredit == preferred || !metadata.allowsRawArtistAlias()) preferred
            else "$preferred $rawCredit"
        },
        album = if (metadata == null) track.rawAlbum else metadata.text("album", track.rawAlbum))
    if (existing == null) searchDao().insertContent(projection)
    else if (existing != projection) searchDao().updateContent(projection)
}

/** Bring persisted FTS rows from older installs up to date without blocking the first library page. */
internal suspend fun AutPlayDatabase.refreshFilenameIndex(profile: String, checkpoints: SharedPreferences) {
    val key = "filename-index-v6/$profile"
    var cursor = checkpoints.getString(key, "") ?: return
    while (cursor != "complete") {
        val ids = withWriteTransaction {
            searchDao().filenameIndexBatch(profile, cursor, 32).also { batch ->
                batch.forEach { id -> refreshTrackSearch(id) }
            }
        }
        cursor = ids.lastOrNull() ?: "complete"
        checkpoints.edit { putString(key, cursor) }
        kotlinx.coroutines.yield()
    }
}
