package app.autplay.data.desktop

import androidx.room3.migration.Migration
import androidx.sqlite.SQLiteConnection
import androidx.sqlite.execSQL

/** Additive annotations only; old identities, accounting, facts and managed bytes are retained. */
internal object DesktopTasteMigration : Migration(3, 4) {
    override suspend fun migrate(connection: SQLiteConnection): Unit {
        connection.execSQL("ALTER TABLE `desktop_queue` ADD COLUMN `sessionTasteState` TEXT NOT NULL DEFAULT 'INCLUDED'")
        connection.execSQL("ALTER TABLE `desktop_queue` ADD COLUMN `listenTasteState` TEXT NOT NULL DEFAULT 'INCLUDED'")
        connection.execSQL("ALTER TABLE `desktop_listening` ADD COLUMN `sessionTasteState` TEXT NOT NULL DEFAULT 'INCLUDED'")
        connection.execSQL("ALTER TABLE `desktop_listening` ADD COLUMN `listenTasteState` TEXT NOT NULL DEFAULT 'INCLUDED'")
    }
}
