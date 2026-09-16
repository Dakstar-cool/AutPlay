package app.autplay.data.local

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

internal object TrackMetadataMigration : Migration(16, 17) {
    override suspend fun migrate(connection: SQLiteConnection) {
        connection.execSQL("CREATE TABLE IF NOT EXISTS track_metadata_projection (server_profile_id TEXT NOT NULL, local_user_track_ref_id TEXT NOT NULL, revision INTEGER NOT NULL, payload_json TEXT NOT NULL, artwork_sha256 TEXT, updated_at_ms INTEGER NOT NULL, PRIMARY KEY(server_profile_id,local_user_track_ref_id))")
        connection.execSQL("CREATE TABLE IF NOT EXISTS metadata_artwork_cache (server_profile_id TEXT NOT NULL, sha256 TEXT NOT NULL, file_path TEXT NOT NULL, byte_size INTEGER NOT NULL, PRIMARY KEY(server_profile_id,sha256))")
    }
}
