package app.autplay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import app.autplay.application.library.TrackMetadataRepository
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.*
import app.autplay.domain.LocalId

internal data class CollectionPageFixture(val album: String, val serverAlbum: String, val albumEntries: List<String>,
    val recordings: List<String>, val playlist: String, val playlistEntries: List<String>)

internal suspend fun seedCollectionPages(context: Context, db: AutPlayDatabase, profile: String, tracks: List<String>, titles: List<String>): CollectionPageFixture {
    fun id() = LocalId.random().value
    val catalog = db.catalogProjectionDao()
    val artist = id()
    val credit = id()
    val album = id()
    val serverAlbum = id()
    val recordings = tracks.map { id() }
    val albumEntries = tracks.map { id() }
    val name = "AutPlay"
    catalog.upsertArtists(listOf(ArtistProjectionEntity(id(), profile, artist, name, null, null, null, null, null, 1, 1)))
    catalog.upsertArtistCredits(listOf(ArtistCreditProjectionEntity(id(), profile, credit, name, 1, 1)))
    catalog.upsertArtistCreditNames(listOf(ArtistCreditNameProjectionEntity(id(), profile, credit, artist, 1, name, "", "PRIMARY")))
    catalog.upsertReleases(listOf(ReleaseProjectionEntity(album, serverAlbum, null, "Ночной берег", name, "2026", "ALBUM", null, 1, 1, false)))
    suspend fun link(type: String, subject: String, recording: String) {
        val scope = id()
        catalog.upsertArtistCreditLinks(listOf(CatalogArtistCreditLinkEntity(id(), profile, type, subject, credit, scope, 1, 0, true, 1, 0, 1)))
        catalog.upsertArtistCreditLinkOwners(listOf(CatalogArtistCreditLinkOwnerEntity(id(), profile, type, subject, scope, recording)))
    }
    tracks.forEachIndexed { index, track ->
        val rec = id()
        catalog.upsertRecordings(listOf(RecordingProjectionEntity(rec, recordings[index], null, titles[index], titles[index].lowercase(),
            name, name.lowercase(), "[]", 40_000, "SONG", null, 0, null, 1, 1, false)))
        val ref = requireNotNull(db.libraryDao().trackRef(track))
        db.libraryDao().upsertTrackRef(ref.copy(localRecordingId = rec, serverRecordingId = recordings[index], resolutionStatus = "RESOLVED"))
        catalog.upsertReleaseTracks(listOf(ReleaseTrackProjectionEntity(albumEntries[index], null, album, rec, 1, index + 1,
            null, titles[index], name, 40_000)))
        link("RECORDING", recordings[index], recordings[index])
    }
    link("RELEASE", serverAlbum, recordings.first())
    repeat(3) { index ->
        db.historyDao().insert(ListeningEventEntity(id(), tracks.last(), recordings.last(), index.toLong(), 40_000, 40_000,
            1.0, "ORGANIC", "GENERAL", null, "NONE", false, "LOCAL_ONLY", 10, serverProfileId = profile))
    }
    // Only the second song has embedded artwork. Both collection headers should choose it.
    seedSunsetArtwork(context, db, profile, tracks.last())
    val playlist = id()
    db.playlistDao().upsertPlaylist(PlaylistEntity(playlist, null, "В дорогу", "Ночная подборка", "PRIVATE", "MANUAL",
        null, null, "LOCAL_ONLY", null, 0, 1, 1, null, profile))
    val entries = listOf(tracks[0], tracks[1], tracks[0]).mapIndexed { index, track ->
        val entryId = id()
        val order = listOf("A", "B", "C")[index]
        db.playlistDao().upsertEntry(PlaylistEntryEntity(entryId, null, playlist, track, order, order, null, 1, "LOCAL_ONLY", null, 0, null, profile))
        entryId
    }
    return CollectionPageFixture(album, serverAlbum, albumEntries, recordings, playlist, entries)
}

internal suspend fun seedSunsetArtwork(context: Context, db: AutPlayDatabase, profile: String, trackId: String, warm: Boolean = false) {
    val cover = Bitmap.createBitmap(160, 160, Bitmap.Config.ARGB_8888)
    val canvas = Canvas(cover)
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    canvas.drawColor(if (warm) Color.rgb(168, 49, 46) else Color.rgb(30, 48, 86))
    paint.color = Color.rgb(255, 174, 94)
    canvas.drawCircle(80f, 74f, 44f, paint)
    paint.color = if (warm) Color.rgb(115, 42, 61) else Color.rgb(39, 102, 129)
    canvas.drawRect(0f, 102f, 160f, 160f, paint)
    paint.color = if (warm) Color.rgb(223, 139, 94) else Color.rgb(115, 174, 178)
    canvas.drawRect(32f, 111f, 120f, 114f, paint)
    canvas.drawRect(56f, 124f, 108f, 126f, paint)
    val bytes = java.io.ByteArrayOutputStream().use { output ->
        check(cover.compress(Bitmap.CompressFormat.JPEG, 95, output))
        output.toByteArray()
    }
    cover.recycle()
    val sha = TrackMetadataRepository(context, db).storeArtwork(profile, bytes)
    val payload = """{"revision":1,"state":"READY","fields":{},"artwork_sha256":"$sha"}"""
    db.trackMetadataDao().upsert(TrackMetadataEntity(profile, trackId, 1, payload, sha, 1))
}
