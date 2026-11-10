package app.autplay.data.desktop

import androidx.room3.Dao
import androidx.room3.ColumnInfo
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
import app.autplay.application.desktop.DesktopTastePolicy

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

/** A track-level local choice. Future strings must survive unrelated writes unchanged. */
@Entity(tableName = "desktop_track_preference", foreignKeys = [ForeignKey(
    entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"],
)])
internal data class TrackPreferenceRow(@PrimaryKey val trackId: String, val preference: String)

/** Local membership only; an absent row is ACTIVE and future strings remain uninterpreted. */
@Entity(tableName = "desktop_track_collection", foreignKeys = [ForeignKey(
    entity = TrackRow::class, parentColumns = ["id"], childColumns = ["trackId"],
)])
internal data class TrackCollectionRow(@PrimaryKey val trackId: String, val collectionState: String)

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
    @ColumnInfo(defaultValue = "'INCLUDED'") val sessionTasteState: String = "INCLUDED",
    @ColumnInfo(defaultValue = "'INCLUDED'") val listenTasteState: String = "INCLUDED",
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
    @ColumnInfo(defaultValue = "'INCLUDED'") val sessionTasteState: String = "INCLUDED",
    @ColumnInfo(defaultValue = "'INCLUDED'") val listenTasteState: String = "INCLUDED",
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
    val preferences: List<TrackPreferenceRow>,
    val collection: List<TrackCollectionRow>,
)

internal data class TasteQueueSnapshot(val queue: QueueRow, val entries: List<QueueEntryRow>)

@Dao
internal abstract class DesktopDao {
    @Query("SELECT * FROM desktop_track ORDER BY title COLLATE NOCASE, id")
    abstract suspend fun tracks(): List<TrackRow>
    @Query("SELECT * FROM desktop_track WHERE sha256 = :hash")
    abstract suspend fun trackByHash(hash: String): TrackRow?
    @Query("SELECT * FROM desktop_track WHERE id = :id")
    abstract suspend fun track(id: String): TrackRow?
    @Query("SELECT * FROM desktop_track_preference ORDER BY trackId")
    abstract suspend fun trackPreferences(): List<TrackPreferenceRow>
    @Query("SELECT * FROM desktop_track_preference WHERE trackId = :trackId")
    abstract suspend fun trackPreference(trackId: String): TrackPreferenceRow?
    @Query("SELECT * FROM desktop_track_collection ORDER BY trackId")
    abstract suspend fun trackCollectionStates(): List<TrackCollectionRow>
    @Query("SELECT * FROM desktop_track_collection WHERE trackId = :trackId")
    abstract suspend fun trackCollectionState(trackId: String): TrackCollectionRow?
    @Query("SELECT * FROM desktop_managed_locator WHERE trackId = :id")
    abstract suspend fun locator(id: String): LocatorRow?
    @Query("SELECT * FROM desktop_playlist ORDER BY name COLLATE NOCASE, id")
    abstract suspend fun playlists(): List<PlaylistRow>
    @Query("SELECT * FROM desktop_playlist WHERE id = :id")
    abstract suspend fun playlist(id: String): PlaylistRow?
    @Query("SELECT * FROM desktop_playlist_entry ORDER BY playlistId, ordinal, id")
    abstract suspend fun playlistEntries(): List<PlaylistEntryRow>
    @Query("SELECT * FROM desktop_playlist_entry WHERE playlistId = :playlistId AND id = :entryId LIMIT 1")
    abstract suspend fun playlistEntry(playlistId: String, entryId: String): PlaylistEntryRow?
    @Query("SELECT * FROM desktop_playlist_entry WHERE playlistId = :playlistId AND " +
        "(ordinal < :ordinal OR (ordinal = :ordinal AND id < :entryId)) ORDER BY ordinal DESC, id DESC LIMIT 1")
    abstract suspend fun previousPlaylistEntry(playlistId: String, entryId: String, ordinal: Long): PlaylistEntryRow?
    @Query("SELECT * FROM desktop_playlist_entry WHERE playlistId = :playlistId AND " +
        "(ordinal > :ordinal OR (ordinal = :ordinal AND id > :entryId)) ORDER BY ordinal, id LIMIT 1")
    abstract suspend fun nextPlaylistEntry(playlistId: String, entryId: String, ordinal: Long): PlaylistEntryRow?
    @Query("SELECT id FROM desktop_playlist_entry WHERE playlistId = :playlistId GROUP BY ordinal " +
        "HAVING COUNT(*) > 1 OR typeof(ordinal) != 'integer' LIMIT 1")
    abstract suspend fun invalidPlaylistOrder(playlistId: String): String?
    @Query("SELECT COALESCE(MAX(ordinal), -1) FROM desktop_playlist_entry WHERE playlistId = :id")
    abstract suspend fun lastOrdinal(id: String): Long
    @Query("SELECT COUNT(*) FROM desktop_playlist_entry WHERE playlistId = :playlistId")
    abstract suspend fun playlistEntryCount(playlistId: String): Long
    @Query("SELECT * FROM desktop_queue WHERE singleton = 1")
    abstract suspend fun queue(): QueueRow?
    @Query("SELECT * FROM desktop_queue_entry ORDER BY ordinal")
    abstract suspend fun queueEntries(): List<QueueEntryRow>
    @Query("SELECT * FROM desktop_listening ORDER BY startedAtMs DESC, id")
    abstract suspend fun history(): List<ListeningRow>
    @Query("SELECT * FROM desktop_listening WHERE id = :id")
    abstract suspend fun listening(id: String): ListeningRow?
    @Insert abstract suspend fun insertTrack(row: TrackRow): Unit
    @Upsert abstract suspend fun putTrackPreference(row: TrackPreferenceRow): Unit
    @Upsert abstract suspend fun putTrackCollectionState(row: TrackCollectionRow): Unit
    @Upsert abstract suspend fun putLocator(row: LocatorRow): Unit
    @Query("UPDATE desktop_track SET title = :title, artist = :artist, durationMs = :duration WHERE id = :id")
    abstract suspend fun setMetadata(id: String, title: String, artist: String, duration: Long?): Int
    @Query("UPDATE desktop_track SET title = :title, artist = :artist WHERE id = :id")
    abstract suspend fun setTextMetadata(id: String, title: String, artist: String): Int
    @Query("UPDATE desktop_track SET durationMs = :duration WHERE id = :id")
    abstract suspend fun setDuration(id: String, duration: Long): Int
    @Insert abstract suspend fun insertPlaylist(row: PlaylistRow): Unit
    @Query("UPDATE desktop_playlist SET name = :name WHERE id = :id")
    abstract suspend fun setPlaylistName(id: String, name: String): Int
    @Insert abstract suspend fun insertPlaylistEntry(row: PlaylistEntryRow): Unit
    @Query("UPDATE desktop_playlist_entry SET ordinal = :ordinal WHERE playlistId = :playlistId AND id = :entryId")
    abstract suspend fun setPlaylistOrdinal(playlistId: String, entryId: String, ordinal: Long): Int
    @Query("DELETE FROM desktop_playlist_entry WHERE playlistId = :playlistId AND id = :entryId")
    abstract suspend fun deletePlaylistEntry(playlistId: String, entryId: String): Int
    @Query("DELETE FROM desktop_playlist_entry WHERE playlistId = :playlistId")
    abstract suspend fun deletePlaylistEntries(playlistId: String): Int
    @Query("DELETE FROM desktop_playlist WHERE id = :playlistId")
    abstract suspend fun deletePlaylistRow(playlistId: String): Int
    @Upsert abstract suspend fun putQueue(row: QueueRow): Unit
    @Query("UPDATE desktop_queue SET sessionTasteState = :state WHERE singleton = 1 AND id = :queueId")
    abstract suspend fun setQueueSessionTaste(queueId: String, state: String): Int
    @Query("UPDATE desktop_queue SET listenTasteState = :state WHERE singleton = 1 AND id = :queueId")
    abstract suspend fun setQueueListenTaste(queueId: String, state: String): Int
    @Query("DELETE FROM desktop_queue_entry") abstract suspend fun clearQueueEntries(): Unit
    @Insert abstract suspend fun insertQueueEntries(rows: List<QueueEntryRow>): Unit
    @Upsert abstract suspend fun putListening(row: ListeningRow): Unit
    @Query("UPDATE desktop_listening SET sessionTasteState = :sessionState, listenTasteState = :listenState " +
        "WHERE id = :eventId AND finalized = 0")
    abstract suspend fun setListeningTaste(eventId: String, sessionState: String, listenState: String): Int
    @Insert abstract suspend fun insertFact(row: LocalFactRow): Unit

    @Transaction
    open suspend fun snapshot(): DatabaseSnapshot = DatabaseSnapshot(
        tracks(), playlists(), playlistEntries(), queue(), queueEntries(), history(), trackPreferences(), trackCollectionStates(),
    )

    /** Recheck the persisted scope before encoding or changing any annotation. */
    @Transaction
    open suspend fun updateTaste(
        expectedQueueId: String, expectedEntryId: String?, expectedListeningEventId: String?,
        excluded: Boolean, currentListen: Boolean,
    ): TasteQueueSnapshot? {
        val before = queue() ?: return null
        if (before.id != expectedQueueId) return null
        if (currentListen && (before.currentEntryId != expectedEntryId ||
                before.sessionId == null || before.sessionId != expectedListeningEventId)) return null
        val entries = queueEntries()
        val entry = entries.singleOrNull { it.id == before.currentEntryId }
        val identity = Regex("[A-Za-z0-9_-]{1,128}")
        check(identity.matches(before.id) &&
            (before.currentEntryId == null || identity.matches(before.currentEntryId)) &&
            (before.sessionId == null || identity.matches(before.sessionId)) &&
            (entry == null || identity.matches(entry.trackId)) &&
            before.positionMs >= 0 && before.playedMs >= 0 &&
            (before.sessionId == null) == (before.sessionStartedAtMs == null) &&
            (before.sessionStartedAtMs == null || before.sessionStartedAtMs >= 0) &&
            (before.currentEntryId == null || entry != null) &&
            (before.sessionId == null || entry != null) &&
            (entry == null || track(entry.trackId) != null)) { "desktop_taste_context_invalid" }
        val previous = before.sessionId?.let { listening(it) }
        // A persisted session may be absent after interrupted admission, but never resurrect a
        // finalized event or reattribute an existing event to the selected queue entry.
        if (previous?.finalized == true) return null
        check(previous == null || (entry != null && previous.queueEntryId == entry.id && previous.trackId == entry.trackId &&
            previous.startedAtMs == before.sessionStartedAtMs && previous.playedMs >= 0 && previous.positionMs >= 0 &&
            previous.sessionTasteState == before.sessionTasteState && previous.listenTasteState == before.listenTasteState)) {
            "desktop_listening_conflict"
        }
        val choice = DesktopTastePolicy.choice(excluded)
        val after = if (currentListen) before.copy(listenTasteState = choice)
            else before.copy(sessionTasteState = choice)
        if (after == before) return TasteQueueSnapshot(before, entries)
        // Encode both the exact before-image and the checkpoint before touching either row.
        // Unsupported oversized or invalid future strings stay retained on refusal.
        val tasteFact = if (currentListen) DesktopLocalIntent.currentListenTasteUpdated(before, after)
            else DesktopLocalIntent.queueSessionTasteUpdated(before, after)
        val annotated = before.sessionId?.let { sessionId ->
            val activeEntry = checkNotNull(entry)
            (previous ?: ListeningRow(sessionId, activeEntry.id, activeEntry.trackId,
                checkNotNull(before.sessionStartedAtMs), before.playedMs, before.positionMs, false))
                .copy(sessionTasteState = after.sessionTasteState, listenTasteState = after.listenTasteState)
        }
        val checkpointFact = annotated?.let(DesktopLocalIntent::listeningSaved)
        val changed = if (currentListen) setQueueListenTaste(before.id, choice) else setQueueSessionTaste(before.id, choice)
        check(changed == 1) { "desktop_taste_context_invalid" }
        if (annotated != null) {
            if (previous == null) putListening(annotated)
            else check(setListeningTaste(annotated.id, annotated.sessionTasteState, annotated.listenTasteState) == 1) {
                "desktop_listening_conflict"
            }
            insertFact(checkNotNull(checkpointFact))
        }
        insertFact(tasteFact)
        return TasteQueueSnapshot(after, entries)
    }

    @Transaction
    open suspend fun addTrack(row: TrackRow, managedLocator: LocatorRow): TrackRow {
        val existing = trackByHash(row.sha256)
        if (existing != null) {
            check(existing.byteSize == row.byteSize) { "desktop_hash_conflict" }
            val previous = locator(existing.id)
            // A current writer cannot interpret or normalize a future locator format.
            check(previous == null || DesktopManagedMedia.isSafeFileName(previous.fileName)) { "desktop_locator_unsupported" }
            check(previous == null || DesktopManagedMedia.hasExpectedHash(previous.fileName, existing.sha256)) {
                "desktop_locator_conflict"
            }
            // Explicit reimport repairs a missing locator, preserving library/history identity.
            if (previous?.fileName != managedLocator.fileName) {
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
    open suspend fun updateTextMetadata(trackId: String, title: String, artist: String): Unit {
        val current = track(trackId) ?: error("desktop_track_missing")
        if (current.title == title && current.artist == artist) return
        val fact = DesktopLocalIntent.trackMetadataUpdated(current.copy(title = title, artist = artist))
        // Do not write duration/identity fields back through a projection, including future values.
        check(setTextMetadata(trackId, title, artist) == 1) { "desktop_track_missing" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun updateDuration(trackId: String, durationMs: Long): Unit {
        val current = track(trackId) ?: error("desktop_track_missing")
        if (current.durationMs == durationMs) return
        val fact = DesktopLocalIntent.trackMetadataUpdated(current.copy(durationMs = durationMs))
        // Native enrichment owns duration only; retain the latest imported/user-owned labels.
        check(setDuration(trackId, durationMs) == 1) { "desktop_track_missing" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun updateTrackPreference(trackId: String, preference: String): Unit {
        check(preference == "NEUTRAL" || preference == "LIKED" || preference == "DISLIKED") {
            "desktop_preference_invalid"
        }
        check(track(trackId) != null) { "desktop_track_missing" }
        val previous = trackPreference(trackId)
        val before = previous?.preference ?: "NEUTRAL"
        val after = TrackPreferenceRow(trackId, preference)
        // An absent row already means NEUTRAL. Repeating the effective choice is a true
        // no-op; explicit clearing of an existing non-neutral value stores a NEUTRAL row.
        if (before == preference) return
        // Encode before writing: an oversized/invalid future value cannot be lost if its
        // immutable before/after intent cannot fit the existing bounded outbox envelope.
        val fact = DesktopLocalIntent.trackPreferenceUpdated(TrackPreferenceRow(trackId, before), after)
        putTrackPreference(after)
        insertFact(fact)
    }

    @Transaction
    open suspend fun updateTrackCollectionState(trackId: String, collectionState: String): Unit {
        check(collectionState == "ACTIVE" || collectionState == "REMOVED") { "desktop_collection_state_invalid" }
        check(track(trackId) != null) { "desktop_track_missing" }
        val before = trackCollectionState(trackId)?.collectionState ?: "ACTIVE"
        // Restoring an absent ACTIVE row and repeating a known choice create neither row nor fact.
        if (before == collectionState) return
        val after = TrackCollectionRow(trackId, collectionState)
        // Encode the bounded Unicode before-image before replacing any future stored value.
        val fact = DesktopLocalIntent.trackCollectionUpdated(TrackCollectionRow(trackId, before), after)
        putTrackCollectionState(after)
        insertFact(fact)
    }

    @Transaction
    open suspend fun addPlaylist(row: PlaylistRow): Unit {
        val fact = DesktopLocalIntent.playlistCreated(row)
        insertPlaylist(row)
        insertFact(fact)
    }

    @Transaction
    open suspend fun renamePlaylist(playlistId: String, name: String): Unit {
        val previous = playlist(playlistId) ?: error("desktop_playlist_missing")
        if (previous.name == name) return
        val renamed = previous.copy(name = name)
        val fact = DesktopLocalIntent.playlistRenamed(previous, renamed)
        check(setPlaylistName(playlistId, name) == 1) { "desktop_playlist_missing" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun deletePlaylist(playlistId: String): Unit {
        val removed = playlist(playlistId) ?: return
        val entryCount = playlistEntryCount(playlistId)
        val fact = DesktopLocalIntent.playlistDeleted(removed, entryCount)
        // Remove only references before their FK parent. A count mismatch rolls back everything.
        check(deletePlaylistEntries(playlistId).toLong() == entryCount) { "desktop_playlist_delete_conflict" }
        check(deletePlaylistRow(playlistId) == 1) { "desktop_playlist_delete_conflict" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun appendEntry(row: PlaylistEntryRow): Unit {
        check(playlist(row.playlistId) != null) { "desktop_playlist_missing" }
        check(track(row.trackId) != null) { "desktop_track_missing" }
        check((trackCollectionState(row.trackId)?.collectionState ?: "ACTIVE") == "ACTIVE") {
            "desktop_track_not_in_collection"
        }
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
    open suspend fun moveEntry(playlistId: String, entryId: String, direction: Int): Unit {
        check(playlist(playlistId) != null) { "desktop_playlist_missing" }
        if (direction != -1 && direction != 1) return
        val moved = playlistEntry(playlistId, entryId) ?: return
        // Unknown storage types or tied ordinals cannot be safely reordered by swapping two values.
        check(invalidPlaylistOrder(playlistId) == null) { "desktop_playlist_order_invalid" }
        val adjacent = (if (direction == -1) previousPlaylistEntry(playlistId, entryId, moved.ordinal)
            else nextPlaylistEntry(playlistId, entryId, moved.ordinal)) ?: return
        check(if (direction == -1) adjacent.ordinal < moved.ordinal else adjacent.ordinal > moved.ordinal) {
            "desktop_playlist_order_invalid"
        }
        val fact = DesktopLocalIntent.playlistEntryMoved(moved, adjacent, direction)
        check(setPlaylistOrdinal(playlistId, moved.id, adjacent.ordinal) == 1) { "desktop_playlist_order_invalid" }
        check(setPlaylistOrdinal(playlistId, adjacent.id, moved.ordinal) == 1) { "desktop_playlist_order_invalid" }
        insertFact(fact)
    }

    @Transaction
    open suspend fun repairManagedLocator(trackId: String, previousFileName: String, repairedFileName: String): Unit {
        val track = track(trackId) ?: error("desktop_track_missing")
        val previous = locator(trackId) ?: error("desktop_locator_missing")
        check(previous.fileName == previousFileName) { "desktop_locator_conflict" }
        check(DesktopManagedMedia.hasExpectedHash(previousFileName, track.sha256) &&
            DesktopManagedMedia.hasExpectedHash(repairedFileName, track.sha256)) { "desktop_locator_conflict" }
        val fact = DesktopLocalIntent.managedCopyRepaired(track)
        putLocator(LocatorRow(trackId, repairedFileName))
        insertFact(fact)
    }

    @Transaction
    open suspend fun saveQueue(row: QueueRow, entries: List<QueueEntryRow>, event: ListeningRow?): Unit {
        val previousQueue = queue()
        if (previousQueue?.id == row.id) {
            // Checkpoints cannot silently replace an explicit choice with constructor defaults.
            // Only a new logical listen resets a known listen choice; future strings stay opaque.
            val expectedListenState = if (previousQueue.sessionId == row.sessionId)
                previousQueue.listenTasteState else DesktopTastePolicy.forNewListen(previousQueue.listenTasteState)
            check(row.sessionTasteState == previousQueue.sessionTasteState && row.listenTasteState == expectedListenState) {
                "desktop_taste_checkpoint_conflict"
            }
        }
        val fact = DesktopLocalIntent.queueSaved(row, entries)
        val eventFact = event?.let(DesktopLocalIntent::listeningSaved)
        val knownTracks = tracks().map { it.id }.toHashSet()
        check(entries.all { it.trackId in knownTracks }) { "desktop_track_missing" }
        val persistedEntries = if (previousQueue == null) emptyList() else queueEntries()
        val retainedMappings = persistedEntries.associate { it.id to it.trackId }
        val collectionStates = trackCollectionStates().associate { it.trackId to it.collectionState }
        // Retained entry/track pairs can checkpoint, reorder or play after removal. Reusing an
        // entry ID for another track is new admission, including when the queue ID stays the same.
        check(entries.all { retainedMappings[it.id] == it.trackId ||
            (collectionStates[it.trackId] ?: "ACTIVE") == "ACTIVE" }) { "desktop_track_not_in_collection" }
        if (event != null) {
            check(event.trackId in knownTracks) { "desktop_track_missing" }
            val previous = listening(event.id)
            check(previous != null || (entries + persistedEntries).any {
                it.id == event.queueEntryId && it.trackId == event.trackId
            }) { "desktop_listening_entry_missing" }
            check(previous == null || (previous.trackId == event.trackId && previous.queueEntryId == event.queueEntryId &&
                previous.startedAtMs == event.startedAtMs && event.playedMs >= previous.playedMs &&
                previous.sessionTasteState == event.sessionTasteState && previous.listenTasteState == event.listenTasteState &&
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

@Database(entities = [TrackRow::class, LocatorRow::class, TrackPreferenceRow::class, TrackCollectionRow::class,
    PlaylistRow::class, PlaylistEntryRow::class, QueueRow::class, QueueEntryRow::class, ListeningRow::class,
    LocalFactRow::class], version = 4, exportSchema = true)
internal abstract class DesktopDatabase : RoomDatabase() {
    abstract fun desktopDao(): DesktopDao
}
