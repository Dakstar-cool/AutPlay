package app.autplay.data.desktop

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

/** Additive desktop-only text; legacy playlists keep a null description. */
internal object DesktopPlaylistDescriptionMigration : Migration(4, 5) {
    override suspend fun migrate(connection: SQLiteConnection): Unit {
        connection.execSQL("ALTER TABLE `desktop_playlist` ADD COLUMN `description` TEXT DEFAULT NULL")
    }
}
