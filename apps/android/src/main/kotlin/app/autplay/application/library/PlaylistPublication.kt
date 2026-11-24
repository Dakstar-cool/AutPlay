package app.autplay.application.library

import androidx.room3.withReadTransaction
import app.autplay.data.local.AutPlayDatabase
import kotlinx.serialization.json.*

/** Read the full ordered list and its labels within the same Room snapshot. */
suspend fun playlistPublication(database: AutPlayDatabase, playlistId: String, profile: String): JsonObject =
    database.withReadTransaction {
        val detail = checkNotNull(CoreProductRepository(database).playlistDetail(
            playlistId, profile, SharedPlaylist.MAX_ENTRIES + 1))
        require(detail.playlistType == "MANUAL" && detail.entries.size <= SharedPlaylist.MAX_ENTRIES)
        val unique = detail.entries.distinctBy { it.localUserTrackRefId }
        val indices = unique.mapIndexed { index, row -> row.localUserTrackRefId to index }.toMap()
        val refs = indices.keys.chunked(500).flatMap { database.libraryDao().trackRefs(it, it.size) }
            .filter { it.serverProfileId == profile && it.deletedAtMs == null }.associateBy { it.localUserTrackRefId }
        buildJsonObject {
            put("playlist_id", database.playlistDao().playlist(playlistId)?.serverPlaylistId ?: playlistId)
            put("name", detail.name)
            put("description", JsonPrimitive(detail.description))
            putJsonArray("tracks") {
                unique.forEach { row ->
                    val ref = refs[row.localUserTrackRefId]
                    add(buildJsonObject {
                        put("ref", JsonPrimitive(ref?.serverUserTrackRefId ?: row.localUserTrackRefId))
                        put("title", JsonPrimitive(row.title))
                        put("artist", JsonPrimitive(row.artistName))
                        put("duration_ms", JsonPrimitive(row.durationMs))
                    })
                }
            }
            putJsonArray("entries") { detail.entries.forEach { add(indices.getValue(it.localUserTrackRefId)) } }
        }
    }
