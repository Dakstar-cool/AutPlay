package app.autplay.data.desktop

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

/** Additive desktop-only membership migration; never deletes or rewrites existing data. */
internal object DesktopCollectionMigration : Migration(2, 3) {
    override suspend fun migrate(connection: SQLiteConnection): Unit {
        // Missing rows project ACTIVE. Migration creates no choices, intents or media operations.
        connection.execSQL("CREATE TABLE IF NOT EXISTS `desktop_track_collection` " +
            "(`trackId` TEXT NOT NULL, `collectionState` TEXT NOT NULL, PRIMARY KEY(`trackId`), " +
            "FOREIGN KEY(`trackId`) REFERENCES `desktop_track`(`id`) ON UPDATE NO ACTION ON DELETE NO ACTION)")
    }
}
