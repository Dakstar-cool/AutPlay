package app.autplay.data.local.entity

import androidx.room3.Entity
import androidx.room3.Index

/** One catalog position received in an owner's sync closure. */
@Entity(tableName = "catalog_edition_track", primaryKeys = ["profile", "trackId"],
    indices = [Index("profile", "releaseId"), Index("profile", "recordingId")])
data class CatalogEditionTrack(
    val profile: String, val trackId: String, val releaseId: String, val recordingId: String,
    val releaseGroupId: String, val releaseMbid: String?, val album: String, val albumArtist: String,
    val releaseType: String, val releaseDate: String?, val disc: Int, val position: Int,
    val numberText: String?, val title: String, val artist: String, val durationMs: Long?,
    val deleted: Boolean, val version: Long, val sequence: Long, val releaseStatus: String? = null,
)

@Entity(tableName = "catalog_page", primaryKeys = ["profile", "pageKey"])
data class CatalogPage(val profile: String, val pageKey: String, val document: String, val observedAtMs: Long)
