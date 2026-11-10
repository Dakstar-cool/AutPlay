package app.autplay.data.desktop

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

/** Additive desktop-only migration; existing rows, local facts and managed bytes stay intact. */
internal object DesktopPreferenceMigration : Migration(1, 2) {
    override suspend fun migrate(connection: SQLiteConnection): Unit {
        // Missing rows project NEUTRAL. Migration must not invent user choices or outbox facts.
        connection.execSQL("CREATE TABLE IF NOT EXISTS `desktop_track_preference` " +
            "(`trackId` TEXT NOT NULL, `preference` TEXT NOT NULL, PRIMARY KEY(`trackId`), " +
            "FOREIGN KEY(`trackId`) REFERENCES `desktop_track`(`id`) ON UPDATE NO ACTION ON DELETE NO ACTION)")
    }
}
