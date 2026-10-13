package app.autplay.data.desktop

import androidx.room3.Dao
import androidx.room3.Database
import androidx.room3.Entity
import androidx.room3.ForeignKey
import androidx.room3.Index
import androidx.room3.Insert
import androidx.room3.PrimaryKey
import androidx.room3.Query
import androidx.room3.RoomDatabase
import androidx.room3.Transaction
import androidx.room3.Upsert

@Entity(tableName = "desktop_track", indices = [Index(value = ["sha256"], unique = true)])
internal data class TrackRow(
    @PrimaryKey val id: String,
    val title: String,
    val artist: String,
    val durationMs: Long?,
    val sha256: String,
    val byteSize: Long,
)

// A locator is a device capability, never part of track identity or an outbox fact.
@Entity(tableName = "desktop_managed_locator", foreignKeys = [ForeignKey(
    entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"],
)])
internal data class LocatorRow(@PrimaryKey val trackId: String, val fileName: String)

@Entity(tableName = "desktop_playlist")
internal data class PlaylistRow(@PrimaryKey val id: String, val name: String)

@Entity(tableName = "desktop_playlist_entry", indices = [Index(value = ["playlistId"]), Index(value = ["trackId"])],
    foreignKeys = [ForeignKey(entity = PlaylistRow::class, parentColumns = ["id"], childColumns = ["playlistId"]),
        ForeignKey(entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"])])
internal data class PlaylistEntryRow(
    @PrimaryKey val id: String, val playlistId: String, val trackId: String, val ordinal: Long,
)

@Entity(tableName = "desktop_queue")
internal data class QueueRow(
    @PrimaryKey val singleton: Int = 1,
    val id: String,
    val currentEntryId: String?,
    val queueType: String,
    val positionMs: Long,
    val shuffleEnabled: Boolean,
    val repeatMode: String,
    val sessionId: String?,
    val sessionStartedAtMs: Long?,
    val playedMs: Long,
)

@Entity(tableName = "desktop_queue_entry", indices = [Index(value = ["trackId"])],
    foreignKeys = [ForeignKey(entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"])])
internal data class QueueEntryRow(
    @PrimaryKey val id: String, val trackId: String, val ordinal: Int, val shuffleOrdinal: Int,
)

@Entity(tableName = "desktop_listening", indices = [Index(value = ["trackId"]), Index(value = ["startedAtMs"])],
    foreignKeys = [ForeignKey(entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"])])
internal data class ListeningRow(
    @PrimaryKey val id: String,
    val queueEntryId: String,
    val trackId: String,
    val startedAtMs: Long,
    val playedMs: Long,
    val positionMs: Long,
    val finalized: Boolean,
)

/** Immutable local intent snapshots. They are not authenticated Journal events or server wire payloads. */
@Entity(tableName = "desktop_local_outbox")
internal data class LocalFactRow(
    @PrimaryKey val changeId: String,
    val kind: String,
    val aggregateId: String,
    val occurredAtMs: Long,
    val payloadJson: String,
    val version: Int = 1,
)

internal data class DatabaseSnapshot(
    val tracks: List<TrackRow>, val playlists: List<PlaylistRow>, val playlistEntries: List<PlaylistEntryRow>,
    val queue: QueueRow?, val queueEntries: List<QueueEntryRow>, val history: List<ListeningRow>,
)

@Dao
internal abstract class DesktopDao {
    @Query("SELECT * FROM desktop_track ORDER BY title COLLATE NOCASE, id")
    abstract suspend fun tracks(): List<TrackRow>
    @Query("SELECT * FROM desktop_track WHERE sha256 = :hash")
    abstract suspend fun trackByHash(hash: String): TrackRow?
    @Query("SELECT * FROM desktop_track WHERE id = :id")
    abstract suspend fun track(id: String): TrackRow?
    @Query("SELECT * FROM desktop_managed_locator WHERE trackId = :id")
    abstract suspend fun locator(id: String): LocatorRow?
    @Query("SELECT * FROM desktop_playlist ORDER BY name COLLATE NOCASE, id")
    abstract suspend fun playlists(): List<PlaylistRow>
    @Query("SELECT * FROM desktop_playlist WHERE id = :id")
    abstract suspend fun playlist(id: String): PlaylistRow?
    @Query("SELECT * FROM desktop_playlist_entry ORDER BY playlistId, ordinal, id")
    abstract suspend fun playlistEntries(): List<PlaylistEntryRow>
    @Query("SELECT * FROM desktop_playlist_entry WHERE playlistId = :playlistId AND id = :entryId")
    abstract suspend fun playlistEntry(playlistId: String, entryId: String): PlaylistEntryRow?
    @Query("SELECT COALESCE(MAX(ordinal), -1) FROM desktop_playlist_entry WHERE playlistId = :id")
    abstract suspend fun lastOrdinal(id: String): Long
    @Query("SELECT * FROM desktop_queue WHERE singleton = 1")
    abstract suspend fun queue(): QueueRow?
    @Query("SELECT * FROM desktop_queue_entry ORDER BY ordinal")
    abstract suspend fun queueEntries(): List<QueueEntryRow>
    @Query("SELECT * FROM desktop_listening ORDER BY startedAtMs DESC, id")
    abstract suspend fun history(): List<ListeningRow>
    @Query("SELECT * FROM desktop_listening WHERE id = :id")
    abstract suspend fun listening(id: String): ListeningRow?
    @Insert abstract suspend fun insertTrack(row: TrackRow): Unit
    @Upsert abstract suspend fun putLocator(row: LocatorRow): Unit
    @Query("UPDATE desktop_track SET title = :title, artist = :artist, durationMs = :duration WHERE id = :id")
    abstract suspend fun setMetadata(id: String, title: String, artist: String, duration: Long?): Int
    @Insert abstract suspend fun insertPlaylist(row: PlaylistRow): Unit
    @Insert abstract suspend fun insertPlaylistEntry(row: PlaylistEntryRow): Unit
    @Query("DELETE FROM desktop_playlist_entry WHERE playlistId = :playlistId AND id = :entryId")
    abstract suspend fun deletePlaylistEntry(playlistId: String, entryId: String): Int
    @Upsert abstract suspend fun putQueue(row: QueueRow): Unit
    @Query("DELETE FROM desktop_queue_entry") abstract suspend fun clearQueueEntries(): Unit
    @Insert abstract suspend fun insertQueueEntries(rows: List<QueueEntryRow>): Unit
    @Upsert abstract suspend fun putListening(row: ListeningRow): Unit
    @Insert abstract suspend fun insertFact(row: LocalFactRow): Unit

    @Transaction
    open suspend fun snapshot(): DatabaseSnapshot = DatabaseSnapshot(
        tracks(), playlists(), playlistEntries(), queue(), queueEntries(), history(),
    )

    @Transaction
    open suspend fun addTrack(row: TrackRow, managedLocator: LocatorRow): TrackRow {
        val existing = trackByHash(row.sha256)
        if (existing != null) {
            check(existing.byteSize == row.byteSize) { "desktop_hash_conflict" }
            // Explicit reimport repairs a missing locator, preserving library/history identity.
            if (locator(existing.id)?.fileName != managedLocator.fileName) {
                val fact = DesktopLocalIntent.managedCopyRepaired(existing)
                putLocator(managedLocator.copy(trackId = existing.id))
                insertFact(fact)
            }
            return existing
        }
        val fact = DesktopLocalIntent.trackImported(row)
        insertTrack(row)
        putLocator(managedLocator)
        insertFact(fact)
        return row
    }

    @Transaction
    open suspend fun updateMetadata(row: TrackRow): Unit {
        val fact = DesktopLocalIntent.trackMetadataUpdated(row)
        check(setMetadata(row.id, row.title, row.artist, row.durationMs) == 1) { "desktop_track_missing" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun addPlaylist(row: PlaylistRow): Unit {
        val fact = DesktopLocalIntent.playlistCreated(row)
        insertPlaylist(row)
        insertFact(fact)
    }

    @Transaction
    open suspend fun appendEntry(row: PlaylistEntryRow): Unit {
        check(playlist(row.playlistId) != null) { "desktop_playlist_missing" }
        check(track(row.trackId) != null) { "desktop_track_missing" }
        val ordinal = lastOrdinal(row.playlistId)
        check(ordinal < Long.MAX_VALUE) { "desktop_playlist_limit" }
        val appended = row.copy(ordinal = ordinal + 1)
        val fact = DesktopLocalIntent.playlistEntryAppended(appended)
        insertPlaylistEntry(appended)
        insertFact(fact)
    }

    @Transaction
    open suspend fun removeEntry(playlistId: String, entryId: String): Unit {
        check(playlist(playlistId) != null) { "desktop_playlist_missing" }
        val removed = playlistEntry(playlistId, entryId) ?: return
        val fact = DesktopLocalIntent.playlistEntryRemoved(removed)
        if (deletePlaylistEntry(playlistId, entryId) == 1) insertFact(fact)
    }

    @Transaction
    open suspend fun saveQueue(row: QueueRow, entries: List<QueueEntryRow>, event: ListeningRow?): Unit {
        val fact = DesktopLocalIntent.queueSaved(row, entries)
        val eventFact = event?.let(DesktopLocalIntent::listeningSaved)
        val knownTracks = tracks().map { it.id }.toHashSet()
        check(entries.all { it.trackId in knownTracks }) { "desktop_track_missing" }
        if (event != null) {
            check(event.trackId in knownTracks) { "desktop_track_missing" }
            val previous = listening(event.id)
            check(previous != null || (entries + queueEntries()).any {
                it.id == event.queueEntryId && it.trackId == event.trackId
            }) { "desktop_listening_entry_missing" }
            check(previous == null || (previous.trackId == event.trackId && previous.queueEntryId == event.queueEntryId &&
                previous.startedAtMs == event.startedAtMs && event.playedMs >= previous.playedMs &&
                (!previous.finalized || previous == event))) { "desktop_listening_conflict" }
            putListening(event)
            insertFact(checkNotNull(eventFact))
        }
        putQueue(row)
        clearQueueEntries()
        insertQueueEntries(entries)
        insertFact(fact)
    }
}

@Database(entities = [TrackRow::class, LocatorRow::class, PlaylistRow::class, PlaylistEntryRow::class,
    QueueRow::class, QueueEntryRow::class, ListeningRow::class, LocalFactRow::class], version = 1, exportSchema = true)
internal abstract class DesktopDatabase : RoomDatabase() {
    abstract fun desktopDao(): DesktopDao
}
