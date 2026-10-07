package app.autplay.application.library

import androidx.room3.withReadTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.dao.MetadataAlbumSourceRow
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.mapLatest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject

/** A local view of confirmed evidence. It never creates Recording or ReleaseTrack rows. */
public data class MetadataAlbum(
    public val group: MetadataAlbumGroup,
    public val members: List<MetadataAlbumMember>,
    public val addedAtMs: Long,
)

public data class MetadataAlbumMember(
    public val localUserTrackRefId: String,
    public val localRecordingId: String?,
    public val title: String?,
    public val artistName: String?,
    public val durationMs: Long?,
    public val discNumber: Int?,
    public val trackNumber: Int?,
)

@OptIn(ExperimentalCoroutinesApi::class)
public class AlbumGroupRepository(private val database: AutPlayDatabase) {
    /** Invalidation includes membership deletion and equal-timestamp metadata changes. */
    public fun observe(profileId: String?): Flow<List<MetadataAlbum>> =
        database.invalidationTracker.createFlow("track_metadata_projection", "user_track_ref", "library_entry")
            .mapLatest { snapshot(profileId) }.distinctUntilChanged()

    /** Full membership is independent of the bounded UI track list. Each SQL batch is bounded. */
    public suspend fun snapshot(profileId: String?): List<MetadataAlbum> = database.withReadTransaction {
        val projector = MetadataAlbumProjector()
        val profile = profileId ?: "legacy-unscoped"
        var after = ""
        do {
            val page = database.trackMetadataDao().albumSourcesPage(profile, after, PAGE_SIZE)
            page.forEach(projector::add)
            page.lastOrNull()?.let {
                check(it.localUserTrackRefId > after) { "ALBUM_PAGE_INVALID" }
                after = it.localUserTrackRefId
            }
        } while (page.size == PAGE_SIZE)
        projector.albums()
    }

    private companion object { const val PAGE_SIZE = 200 }
}

/** Pure projection seam. Raw metadata remains in Room, including unsupported group versions. */
internal class MetadataAlbumProjector {
    private data class Entry(val id: String, val updatedAtMs: Long, val addedAtMs: Long,
        val group: MetadataAlbumGroup, val member: MetadataAlbumMember)
    private val tracks = mutableMapOf<String, Entry>()

    fun add(row: MetadataAlbumSourceRow) {
        if (row.payloadJson.length > 140_000) return
        val metadata = runCatching { TrackMetadata.decode(Json.parseToJsonElement(row.payloadJson).jsonObject) }.getOrNull()
            ?: return
        val group = metadata.albumGroup() ?: return
        val previous = tracks[row.localUserTrackRefId]
        if (previous == null || row.metadataUpdatedAtMs >= previous.updatedAtMs) {
            tracks[row.localUserTrackRefId] = Entry(row.localUserTrackRefId, row.metadataUpdatedAtMs, row.addedAtMs,
                // Keep compact evidence only; the complete raw document stays in Room.
                group.copy(payload = kotlinx.serialization.json.JsonObject(emptyMap())),
                MetadataAlbumMember(row.localUserTrackRefId, row.localRecordingId,
                    metadata.text("title", row.rawTitle), metadata.text("artist", row.rawArtist),
                    row.rawDurationMs, group.discNumber, group.trackNumber))
        }
    }

    fun albums(): List<MetadataAlbum> = tracks.values.groupBy { it.group.key }.values.map { entries ->
        val newest = entries.sortedWith(compareByDescending<Entry> { it.updatedAtMs }.thenBy { it.id })
        MetadataAlbum(
            group = newest.first().group.copy(
                albumArtist = newest.firstNotNullOfOrNull { it.group.albumArtist },
                releaseDate = newest.firstNotNullOfOrNull { it.group.releaseDate },
            ),
            members = entries.map { it.member }.sortedWith(compareBy<MetadataAlbumMember> { it.discNumber ?: Int.MAX_VALUE }
                .thenBy { it.trackNumber ?: Int.MAX_VALUE }.thenBy { it.localUserTrackRefId }),
            addedAtMs = entries.maxOf { it.addedAtMs },
        )
    }.sortedWith(compareByDescending<MetadataAlbum> { it.addedAtMs }.thenBy { it.group.key })
}
