package app.autplay.data.local.entity

import androidx.room3.ColumnInfo
import androidx.room3.Entity

/** A descriptive overlay; source audio and canonical recording IDs remain unchanged. */
@Entity(tableName = "track_metadata_projection", primaryKeys = ["server_profile_id", "local_user_track_ref_id"])
data class TrackMetadataEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String,
    val revision: Long,
    @ColumnInfo(name = "payload_json") val payloadJson: String,
    @ColumnInfo(name = "artwork_sha256") val artworkSha256: String?,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

@Entity(tableName = "metadata_artwork_cache", primaryKeys = ["server_profile_id", "sha256"])
data class MetadataArtworkEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    val sha256: String,
    @ColumnInfo(name = "file_path") val filePath: String,
    @ColumnInfo(name = "byte_size") val byteSize: Long,
)
