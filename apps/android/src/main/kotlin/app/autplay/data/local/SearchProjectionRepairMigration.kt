package app.autplay.data.local

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

/** Repairs derived search data without changing canonical metadata or pending local intent. */
internal object SearchProjectionRepairMigration : Migration(15, 16) {
    override suspend fun migrate(connection: SQLiteConnection) {
        connection.execSQL("""
            INSERT INTO track_search_content(local_user_track_ref_id, title, artist, album, aliases, transliterations)
            SELECT local_user_track_ref_id, raw_title, raw_artist, raw_album, NULL, NULL
            FROM user_track_ref WHERE deleted_at_ms IS NULL
            ON CONFLICT(local_user_track_ref_id) DO UPDATE SET
                title = excluded.title, artist = excluded.artist, album = excluded.album
        """.trimIndent())
        // Room may suspend its content triggers during migration; rebuild from the repaired source.
        connection.execSQL("INSERT INTO track_search_fts(track_search_fts) VALUES('rebuild')")

        // Old payload bytes were not retained. Only a new authoritative snapshot can distinguish
        // a wrongly projected JSON null from legitimate text "null"; keep both intact offline.
        val affectedProfiles = """
            SELECT server_profile_id FROM user_track_ref
            WHERE sync_state = 'CLEAN' AND server_user_track_ref_id IS NOT NULL
                AND server_profile_id != 'legacy-unscoped'
                AND (raw_title = 'null' OR raw_artist = 'null' OR raw_album = 'null')
            UNION
            SELECT server_profile_id FROM playlist
            WHERE sync_state = 'CLEAN' AND server_playlist_id IS NOT NULL
                AND server_profile_id != 'legacy-unscoped' AND description = 'null'
        """.trimIndent()
        connection.execSQL("""
            UPDATE sync_cursor SET bootstrap_state = 'RESET_REQUIRED', bootstrap_snapshot_id = NULL
            WHERE server_profile_id IN ($affectedProfiles)
                AND bootstrap_state IN ('READY', 'BOOTSTRAPPING', 'RESET_REQUIRED')
        """.trimIndent())
        connection.execSQL("""
            UPDATE sync_bootstrap_state SET state = 'RESET_REQUIRED',
                snapshot_id = NULL, page_token = NULL, final_cursor = NULL
            WHERE server_profile_id IN ($affectedProfiles)
                AND server_profile_id IN (SELECT server_profile_id FROM sync_cursor WHERE bootstrap_state = 'RESET_REQUIRED')
                AND state IN ('READY', 'BOOTSTRAPPING', 'RESET_REQUIRED')
        """.trimIndent())
    }
}
