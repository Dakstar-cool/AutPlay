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
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.desktop.DesktopLocalStatistics
import app.autplay.application.desktop.DesktopLocalStatisticsLimits
import app.autplay.application.desktop.DesktopLocalTopArtistLabel
import app.autplay.application.desktop.DesktopLocalTopTrack
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopEmbeddedTrackTextApplication
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.DesktopTastePolicy
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobCursor
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobLimits
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobPage
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop

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
internal data class PlaylistRow(
    @PrimaryKey val id: String,
    val name: String,
    @ColumnInfo(defaultValue = "NULL") val description: String? = null,
)

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
internal data class ImportJobTrackIdentity(val id: String, val sha256: String, val byteSize: Long)

internal data class LocalStatisticsTotals(val listenedMs: Long?, val playSessionCount: Long)
internal data class LocalStatisticsTrackMetadata(val trackId: String, val title: String, val artist: String)
internal data class LocalStatisticsTrack(
    val trackId: String, val title: String, val artist: String,
    val playSessionCount: Long, val listenedMs: Long,
)
internal data class LocalStatisticsArtistLabel(
    val artistLabel: String, val playSessionCount: Long, val listenedMs: Long,
)

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
    @Query("SELECT id, queueEntryId, trackId, startedAtMs, playedMs, positionMs, finalized, " +
        "sessionTasteState, listenTasteState FROM desktop_listening ORDER BY startedAtMs DESC, id ASC LIMIT :limit")
    abstract suspend fun historyPreview(limit: Int): List<ListeningRow>
    // One SELECT supplies the page and its lookahead from the same read view. No cursor-row lookup.
    @Query("SELECT id, queueEntryId, trackId, startedAtMs, playedMs, positionMs, finalized, " +
        "sessionTasteState, listenTasteState FROM desktop_listening WHERE :beforeStartedAtMs IS NULL OR " +
        "startedAtMs < :beforeStartedAtMs OR (startedAtMs = :beforeStartedAtMs AND id > :beforeEventId) " +
        "ORDER BY startedAtMs DESC, id ASC LIMIT :readLimit")
    abstract suspend fun historyPage(beforeStartedAtMs: Long?, beforeEventId: String?, readLimit: Int): List<ListeningRow>

    // Bound stored values before transfer or aggregate coercion; history never leaves SQLite here.
    // A malformed timestamp cannot hide outside the cutoff. Valid future listens are excluded.
    @Query("""
        SELECT EXISTS(
            SELECT 1 FROM desktop_listening e LEFT JOIN desktop_track t ON t.id = e.trackId
            WHERE (typeof(e.startedAtMs) != 'integer' OR e.startedAtMs < 0 OR e.startedAtMs <= :throughInclusiveMs)
              AND (typeof(e.startedAtMs) != 'integer' OR e.startedAtMs < 0
                OR typeof(e.playedMs) != 'integer' OR e.playedMs < 0
                OR typeof(e.positionMs) != 'integer' OR e.positionMs < 0
                OR typeof(e.finalized) != 'integer' OR e.finalized NOT IN (0, 1)
                OR typeof(e.id) != 'text' OR length(CAST(e.id AS BLOB)) NOT BETWEEN 1 AND 128
                OR e.id GLOB '*[^A-Za-z0-9_-]*' OR instr(e.id, char(0)) > 0
                OR typeof(e.queueEntryId) != 'text' OR length(CAST(e.queueEntryId AS BLOB)) NOT BETWEEN 1 AND 128
                OR e.queueEntryId GLOB '*[^A-Za-z0-9_-]*' OR instr(e.queueEntryId, char(0)) > 0
                OR typeof(e.trackId) != 'text' OR length(CAST(e.trackId AS BLOB)) NOT BETWEEN 1 AND 128
                OR e.trackId GLOB '*[^A-Za-z0-9_-]*' OR instr(e.trackId, char(0)) > 0
                OR t.id IS NULL
                OR typeof(t.title) != 'text' OR length(CAST(t.title AS BLOB)) > 16384
                OR length(t.title) > 4096 OR instr(t.title, char(0)) > 0
                OR typeof(t.artist) != 'text' OR length(CAST(t.artist AS BLOB)) > 16384
                OR length(t.artist) > 4096 OR instr(t.artist, char(0)) > 0)
        )
    """)
    abstract suspend fun invalidLocalStatisticsSource(throughInclusiveMs: Long): Boolean

    // Visit each qualifying exact track once, with only one bounded metadata page in memory.
    @Query("""
        SELECT t.id AS trackId, t.title AS title, t.artist AS artist
        FROM desktop_track t
        WHERE t.id COLLATE BINARY > :afterTrackId
          AND EXISTS(SELECT 1 FROM desktop_listening e WHERE e.trackId = t.id
                     AND e.startedAtMs <= :throughInclusiveMs AND e.playedMs > 0)
        ORDER BY t.id COLLATE BINARY ASC
        LIMIT 100
    """)
    abstract suspend fun localStatisticsMetadataPage(
        throughInclusiveMs: Long, afterTrackId: String,
    ): List<LocalStatisticsTrackMetadata>

    // SUM on validated INTEGER inputs throws on overflow; NULL means no positive saved listens.
    @Query("SELECT SUM(playedMs) AS listenedMs, COUNT(*) AS playSessionCount FROM desktop_listening " +
        "WHERE startedAtMs <= :throughInclusiveMs AND playedMs > 0")
    abstract suspend fun localStatisticsTotals(throughInclusiveMs: Long): LocalStatisticsTotals

    @Query("""
        SELECT e.trackId AS trackId, t.title AS title, t.artist AS artist,
               COUNT(*) AS playSessionCount, SUM(e.playedMs) AS listenedMs
        FROM desktop_listening e JOIN desktop_track t ON t.id = e.trackId
        WHERE e.startedAtMs <= :throughInclusiveMs AND e.playedMs > 0
        GROUP BY e.trackId
        ORDER BY listenedMs DESC, playSessionCount DESC, e.trackId ASC
        LIMIT 5
    """)
    abstract suspend fun localStatisticsTopTracks(throughInclusiveMs: Long): List<LocalStatisticsTrack>

    // SQLite TRIM and binary label sorting match Android's raw display-label grouping.
    @Query("""
        SELECT TRIM(t.artist) AS artistLabel, COUNT(*) AS playSessionCount, SUM(e.playedMs) AS listenedMs
        FROM desktop_listening e JOIN desktop_track t ON t.id = e.trackId
        WHERE e.startedAtMs <= :throughInclusiveMs AND e.playedMs > 0
          AND NULLIF(TRIM(t.artist), '') IS NOT NULL
        GROUP BY TRIM(t.artist)
        ORDER BY listenedMs DESC, playSessionCount DESC, artistLabel ASC
        LIMIT 5
    """)
    abstract suspend fun localStatisticsTopArtistLabels(throughInclusiveMs: Long): List<LocalStatisticsArtistLabel>

    @Transaction
    open suspend fun readLocalStatistics(throughInclusiveMs: Long): DesktopLocalStatistics {
        check(throughInclusiveMs >= 0) { "desktop_statistics_cutoff_invalid" }
        check(!invalidLocalStatisticsSource(throughInclusiveMs)) { "desktop_statistics_persisted_invalid" }
        fun validLabel(value: String): Boolean {
            if (value.length > DesktopLocalStatisticsLimits.MAX_LABEL_LENGTH) return false
            var offset = 0
            while (offset < value.length) {
                val character = value[offset++]
                if (character == '\u0000') return false
                if (character.isHighSurrogate()) {
                    if (offset >= value.length || !value[offset++].isLowSurrogate()) return false
                } else if (character.isLowSurrogate()) return false
            }
            return true
        }
        // SQLite length counts code points, not Kotlin UTF-16 units or Unicode blank titles.
        // Validate all contributing metadata before LIMIT 5 can hide a corrupt lower-ranked row.
        var afterTrackId = ""
        val validTrackId = Regex("[A-Za-z0-9_-]{1,128}")
        while (true) {
            val page = localStatisticsMetadataPage(throughInclusiveMs, afterTrackId)
            check(page.size <= 100) { "desktop_statistics_persisted_invalid" }
            if (page.isEmpty()) break
            page.forEach {
                // Valid ASCII IDs share SQLite BINARY order; every row must advance the cursor.
                check(it.trackId.matches(validTrackId) && it.trackId > afterTrackId && it.title.isNotBlank() &&
                    validLabel(it.title) && validLabel(it.artist)) { "desktop_statistics_persisted_invalid" }
                afterTrackId = it.trackId
            }
        }
        val total = localStatisticsTotals(throughInclusiveMs)
        val listened = if (total.playSessionCount == 0L) {
            check(total.listenedMs == null) { "desktop_statistics_persisted_invalid" }
            0L
        } else {
            check(total.playSessionCount > 0 && total.listenedMs != null && total.listenedMs > 0) {
                "desktop_statistics_persisted_invalid"
            }
            checkNotNull(total.listenedMs)
        }
        val tracks = localStatisticsTopTracks(throughInclusiveMs).map {
            check(it.trackId.matches(Regex("[A-Za-z0-9_-]{1,128}")) && it.title.isNotBlank() &&
                validLabel(it.title) && validLabel(it.artist) && it.playSessionCount > 0 && it.listenedMs > 0) {
                "desktop_statistics_persisted_invalid"
            }
            DesktopLocalTopTrack(it.trackId, it.title, it.artist, it.playSessionCount, it.listenedMs)
        }
        val artists = localStatisticsTopArtistLabels(throughInclusiveMs).map {
            check(it.artistLabel.isNotEmpty() && validLabel(it.artistLabel) && it.playSessionCount > 0 && it.listenedMs > 0) {
                "desktop_statistics_persisted_invalid"
            }
            DesktopLocalTopArtistLabel(it.artistLabel, it.playSessionCount, it.listenedMs)
        }
        return DesktopLocalStatistics(throughInclusiveMs, listened, total.playSessionCount, tracks, artists)
    }

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
    @Query("UPDATE desktop_playlist SET description = :description WHERE id = :id")
    abstract suspend fun setPlaylistDescription(id: String, description: String?): Int
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

    @Query("SELECT changeId,kind,aggregateId,occurredAtMs,version,length(CAST(payloadJson AS BLOB)) AS payloadBytes " +
        "FROM desktop_local_outbox WHERE (:afterId IS NULL OR changeId > :afterId) ORDER BY changeId LIMIT :limit")
    abstract suspend fun accountFactPage(afterId: String?, limit: Int): List<StandaloneFactHeader>

    @Query("SELECT changeId,kind,aggregateId,occurredAtMs,version,length(CAST(payloadJson AS BLOB)) AS payloadBytes " +
        "FROM desktop_local_outbox WHERE changeId IN (:ids) ORDER BY changeId LIMIT 101")
    abstract suspend fun accountSelectedHeaders(ids: List<String>): List<StandaloneFactHeader>

    @Query("SELECT * FROM desktop_local_outbox WHERE changeId IN (:ids) " +
        "AND length(CAST(payloadJson AS BLOB)) <= 262144 ORDER BY changeId LIMIT 101")
    abstract suspend fun accountSelectedFacts(ids: List<String>): List<LocalFactRow>

    @Query("SELECT * FROM desktop_playlist_entry WHERE id = :id")
    abstract suspend fun accountPlaylistEntry(id: String): PlaylistEntryRow?

    @Transaction
    open suspend fun accountSelectedRows(ids: List<String>): StandaloneSelectedRows =
        readStandaloneSelectedRows(this, ids)

    @Query("SELECT COUNT(*) FROM desktop_local_outbox WHERE aggregateId = :aggregate")
    abstract suspend fun importJobFactCount(aggregate: String): Long
    @Query("SELECT COALESCE(SUM(length(CAST(payloadJson AS BLOB))), 0) FROM desktop_local_outbox WHERE aggregateId = :aggregate")
    abstract suspend fun importJobFactBytes(aggregate: String): Long
    @Query("SELECT COUNT(*) FROM desktop_local_outbox WHERE aggregateId = :aggregate AND " +
        "(version != 1 OR kind != 'IMPORT_JOB_FACT' OR length(changeId) != 36 OR length(CAST(payloadJson AS BLOB)) > 262144)")
    abstract suspend fun importJobUnsupportedFactCount(aggregate: String): Long
    @Query("SELECT id, sha256, byteSize FROM desktop_track WHERE id = :id AND length(id) <= 128 AND length(sha256) = 64")
    abstract suspend fun importJobTrackIdentity(id: String): ImportJobTrackIdentity?
    @Query("SELECT changeId, kind, aggregateId, occurredAtMs, version, " +
        "CASE WHEN length(CAST(payloadJson AS BLOB)) <= 262144 THEN payloadJson ELSE '' END AS payloadJson " +
        "FROM desktop_local_outbox WHERE aggregateId = :aggregate AND (:afterId IS NULL OR changeId > :afterId) " +
        "ORDER BY changeId LIMIT :limit")
    abstract suspend fun importJobFactPage(aggregate: String, afterId: String?, limit: Int): List<LocalFactRow>
    @Query("SELECT changeId, kind, aggregateId, occurredAtMs, version, " +
        "CASE WHEN length(CAST(payloadJson AS BLOB)) <= 262144 THEN payloadJson ELSE '' END AS payloadJson " +
        "FROM desktop_local_outbox WHERE changeId = :id AND version = 1 AND kind = 'TRACK_IMPORTED' AND length(aggregateId) <= 128")
    abstract suspend fun importJobLinkedFact(id: String): LocalFactRow?
    // Prefix isolates local job identities from all existing track/playlist/queue/provenance facts.
    // UUID ordering is only a keyset traversal. Every scanned aggregate advances the public cursor.
    @Query("SELECT DISTINCT aggregateId FROM desktop_local_outbox WHERE aggregateId >= 'desktop-import-job:' " +
        "AND aggregateId < 'desktop-import-job;' AND length(aggregateId) = 55 " +
        "AND substr(aggregateId, 20) NOT GLOB '*[^0-9a-f-]*' " +
        "AND substr(aggregateId, 28, 1) = '-' AND substr(aggregateId, 33, 1) = '-' " +
        "AND substr(aggregateId, 38, 1) = '-' AND substr(aggregateId, 43, 1) = '-' " +
        "AND length(replace(substr(aggregateId, 20), '-', '')) = 32 " +
        "AND (:afterAggregate IS NULL OR aggregateId > :afterAggregate) " +
        "ORDER BY aggregateId LIMIT :limit")
    abstract suspend fun importJobAggregates(afterAggregate: String?, limit: Int): List<String>

    @Transaction
    open suspend fun loadImportJob(id: String): DesktopImportJob? = DesktopImportJobProjection.read(this, id)

    @Transaction
    open suspend fun loadImportJobHistory(id: String): DesktopImportJobProjection.History =
        DesktopImportJobProjection.required(this, id)

    @Transaction
    open suspend fun loadUnfinishedImportJobs(after: DesktopImportJobCursor?, limit: Int): DesktopImportJobPage {
        check(limit in 1..DesktopImportJobLimits.JOB_PAGE) { "desktop_import_job_limit" }
        // Only canonical UUIDs name jobs; future events under those IDs remain visible/read-only.
        after?.let { DesktopImportJobProjection.requireUuid(it.afterJobId) }
        val ids = importJobAggregates(after?.let { DesktopImportJobProjection.aggregate(it.afterJobId) }, limit + 1)
        val scanned = ids.take(limit)
        val jobs = scanned.map { aggregate -> DesktopImportJobProjection.read(this, DesktopImportJobProjection.jobId(aggregate)) }
            .filterNotNull().filter { it.phase != DesktopImportJobPhase.COMPLETED }
        val next = if (ids.size > limit) DesktopImportJobCursor(DesktopImportJobProjection.jobId(scanned.last())) else null
        return DesktopImportJobPage(jobs, next)
    }

    @Transaction
    open suspend fun createImportJob(manifest: DesktopImportJobManifest): DesktopImportJob =
        DesktopImportJobProjection.create(this, manifest)

    @Transaction
    open suspend fun confirmImportJobAttempt(attempt: DesktopImportJobAttempt): DesktopImportJob =
        DesktopImportJobProjection.confirm(this, attempt)

    @Transaction
    open suspend fun settleImportJobCandidate(
        settlement: DesktopImportJobSettlement, track: DesktopImportedTrack?, failure: DesktopImportJobFailure?,
    ): DesktopImportJob = DesktopImportJobProjection.settle(this, settlement, track, failure)

    @Transaction
    open suspend fun stopImportJob(stop: DesktopImportJobStop): DesktopImportJob = DesktopImportJobProjection.stop(this, stop)

    // Scalar counts are revision anchors, independent of timestamps, UUID order and SQLite rowid.
    @Query("SELECT COUNT(*) FROM desktop_local_outbox WHERE aggregateId = :trackId AND kind = 'TRACK_METADATA_UPDATED'")
    abstract suspend fun metadataFactCount(trackId: String): Long
    @Query("SELECT COUNT(*) FROM desktop_local_outbox WHERE aggregateId = :trackId AND kind = 'TRACK_TEXT_PROVENANCE_UPDATED'")
    abstract suspend fun textProvenanceFactCount(trackId: String): Long
    @Query("SELECT changeId FROM desktop_local_outbox WHERE aggregateId = :trackId AND (version != 1 OR kind NOT IN " +
        "('TRACK_IMPORTED', 'TRACK_METADATA_UPDATED', 'TRACK_TEXT_PROVENANCE_UPDATED', 'TRACK_PREFERENCE_UPDATED', " +
        "'MANAGED_COPY_REPAIRED', 'TRACK_REMOVED_FROM_COLLECTION', 'TRACK_RESTORED_TO_COLLECTION')) LIMIT 1")
    abstract suspend fun unsupportedTrackFact(trackId: String): String?
    // Bound payload materialization even for unsupported future/oversized persisted facts.
    @Query("SELECT changeId, kind, aggregateId, occurredAtMs, version, " +
        "CASE WHEN length(CAST(payloadJson AS BLOB)) <= 131072 THEN payloadJson ELSE '' END AS payloadJson " +
        "FROM desktop_local_outbox WHERE aggregateId = :trackId AND kind = 'TRACK_TEXT_PROVENANCE_UPDATED' " +
        "AND (:afterId IS NULL OR changeId > :afterId) ORDER BY changeId LIMIT :limit")
    abstract suspend fun textProvenanceFactPage(trackId: String, afterId: String?, limit: Int): List<LocalFactRow>
    @Query("SELECT changeId, kind, aggregateId, occurredAtMs, version, " +
        "CASE WHEN length(CAST(payloadJson AS BLOB)) <= 262144 THEN payloadJson ELSE '' END AS payloadJson " +
        "FROM desktop_local_outbox WHERE changeId = :changeId")
    abstract suspend fun textProvenanceMetadataFact(changeId: String): LocalFactRow?

    @Transaction
    open suspend fun loadTextProvenance(trackId: String): DesktopTrackTextProvenance? {
        val row = track(trackId) ?: return null
        return DesktopTextProvenanceProjection.read(this, row)
    }

    private suspend fun textProvenanceFact(
        before: TrackRow, after: TrackRow, metadata: LocalFactRow, operation: String, format: String? = null,
    ): LocalFactRow {
        val revision = metadataFactCount(before.id)
        val sequence = textProvenanceFactCount(before.id)
        check(revision < Long.MAX_VALUE && sequence < Long.MAX_VALUE) { "desktop_text_provenance_limit" }
        return DesktopLocalIntent.trackTextProvenanceUpdated(before, after, metadata, revision + 1, sequence + 1,
            operation, format)
    }

    @Transaction
    open suspend fun snapshot(): DatabaseSnapshot = DatabaseSnapshot(
        tracks(), playlists(), playlistEntries(), queue(), queueEntries(), historyPreview(DesktopHistoryLimits.PREVIEW_SIZE),
        trackPreferences(), trackCollectionStates(),
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
        val current = track(row.id) ?: error("desktop_track_missing")
        check(current.sha256 == row.sha256 && current.byteSize == row.byteSize) { "desktop_metadata_conflict" }
        if (current == row) return
        val fact = DesktopLocalIntent.trackMetadataUpdated(row)
        val provenance = textProvenanceFact(current, row, fact, "USER")
        check(setMetadata(row.id, row.title, row.artist, row.durationMs) == 1) { "desktop_track_missing" }
        insertFact(fact)
        insertFact(provenance)
    }

    @Transaction
    open suspend fun updateTextMetadata(trackId: String, title: String, artist: String): Unit {
        val current = track(trackId) ?: error("desktop_track_missing")
        if (current.title == title && current.artist == artist) return
        val after = current.copy(title = title, artist = artist)
        val fact = DesktopLocalIntent.trackMetadataUpdated(after)
        val provenance = textProvenanceFact(current, after, fact, "USER")
        // Do not write duration/identity fields back through a projection, including future values.
        check(setTextMetadata(trackId, title, artist) == 1) { "desktop_track_missing" }
        insertFact(fact)
        insertFact(provenance)
    }

    @Transaction
    open suspend fun applyEmbeddedText(baseline: DesktopTrackTextBaseline, title: String, artist: String): Unit {
        val current = track(baseline.trackId) ?: error("desktop_track_missing")
        check(current.title == baseline.title && current.artist == baseline.artist &&
            current.sha256 == baseline.sha256 && current.byteSize == baseline.byteSize) { "desktop_metadata_conflict" }
        if (current.title == title && current.artist == artist) return
        val after = current.copy(title = title, artist = artist)
        val fact = DesktopLocalIntent.trackMetadataUpdated(after)
        // The compatibility API has no frozen format/source-field evidence. Changed fields are UNKNOWN.
        val provenance = textProvenanceFact(current, after, fact, "COMPATIBILITY")
        check(setTextMetadata(current.id, title, artist) == 1) { "desktop_track_missing" }
        insertFact(fact)
        insertFact(provenance)
    }

    @Transaction
    open suspend fun applyEmbeddedText(application: DesktopEmbeddedTrackTextApplication): Unit {
        val expected = application.expected
        val baseline = expected.baseline
        val current = track(baseline.trackId) ?: error("desktop_track_missing")
        check(current.title == baseline.title && current.artist == baseline.artist &&
            current.sha256 == baseline.sha256 && current.byteSize == baseline.byteSize &&
            metadataFactCount(current.id) == expected.metadataRevision) { "desktop_metadata_conflict" }
        val after = current.copy(title = application.title ?: current.title, artist = application.artist ?: current.artist)
        if (after == current) return
        val fact = DesktopLocalIntent.trackMetadataUpdated(after)
        val provenance = textProvenanceFact(current, after, fact, "EMBEDDED", application.format.name)
        check(setTextMetadata(current.id, after.title, after.artist) == 1) { "desktop_track_missing" }
        insertFact(fact)
        insertFact(provenance)
    }

    @Transaction
    open suspend fun updateDuration(trackId: String, durationMs: Long): Unit {
        val current = track(trackId) ?: error("desktop_track_missing")
        if (current.durationMs == durationMs) return
        val after = current.copy(durationMs = durationMs)
        val fact = DesktopLocalIntent.trackMetadataUpdated(after)
        val provenance = textProvenanceFact(current, after, fact, "DURATION")
        // Native enrichment owns duration only; retain the latest imported/user-owned labels.
        check(setDuration(trackId, durationMs) == 1) { "desktop_track_missing" }
        insertFact(fact)
        insertFact(provenance)
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
    open suspend fun updatePlaylistDescription(playlistId: String, expectedDescription: String?, description: String?): Unit {
        requirePlaylistDescription(description)
        val previous = playlist(playlistId) ?: error("desktop_playlist_missing")
        check(previous.description == expectedDescription) { "desktop_playlist_description_conflict" }
        if (previous.description == description) return
        // Preserve raw legacy text in the before-image. Encoding failure precedes all writes.
        val fact = DesktopLocalIntent.playlistDescriptionUpdated(previous, previous.copy(description = description))
        check(setPlaylistDescription(playlistId, description) == 1) { "desktop_playlist_missing" }
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
    open suspend fun appendEntries(playlistId: String, rows: List<PlaylistEntryRow>): Unit {
        check(rows.size <= DesktopLocalLimits.MAX_QUEUE_ENTRIES) { "desktop_playlist_batch_limit" }
        if (rows.isEmpty()) return
        check(rows.all { it.playlistId == playlistId }) { "desktop_identity_invalid" }
        check(playlist(playlistId) != null) { "desktop_playlist_missing" }
        // Recheck every reference in this transaction before publishing any entry or fact.
        rows.forEach { row ->
            check(track(row.trackId) != null) { "desktop_track_missing" }
            check((trackCollectionState(row.trackId)?.collectionState ?: "ACTIVE") == "ACTIVE") {
                "desktop_track_not_in_collection"
            }
        }
        // Refuse unsupported stored ordering rather than coerce or renumber it.
        check(invalidPlaylistOrder(playlistId) == null) { "desktop_playlist_order_invalid" }
        val ordinal = lastOrdinal(playlistId)
        check(ordinal <= Long.MAX_VALUE - rows.size.toLong()) { "desktop_playlist_limit" }
        val appended = rows.mapIndexed { index, row -> row.copy(ordinal = ordinal + index.toLong() + 1) }
        val facts = appended.map(DesktopLocalIntent::playlistEntryAppended)
        appended.zip(facts).forEach { (entry, fact) ->
            insertPlaylistEntry(entry)
            insertFact(fact)
        }
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
    LocalFactRow::class], version = 5, exportSchema = true)
internal abstract class DesktopDatabase : RoomDatabase() {
    abstract fun desktopDao(): DesktopDao
}
