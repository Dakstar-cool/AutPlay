package app.autplay.data.local.dao

import androidx.room3.Dao
import androidx.room3.Delete
import androidx.room3.Insert
import androidx.room3.OnConflictStrategy
import androidx.room3.Query
import androidx.room3.Transaction
import androidx.room3.Upsert
import androidx.room3.Update
import app.autplay.data.local.entity.*
import kotlinx.coroutines.flow.Flow

@Dao
interface CatalogProjectionDao {
    @Upsert suspend fun upsertRecordings(rows: List<RecordingProjectionEntity>)
    @Upsert suspend fun upsertReleases(rows: List<ReleaseProjectionEntity>)
    @Upsert suspend fun upsertReleaseTracks(rows: List<ReleaseTrackProjectionEntity>)
    @Query("SELECT * FROM recording_projection WHERE local_recording_id = :localId") suspend fun recording(localId: String): RecordingProjectionEntity?
}

@Dao
interface LibraryDao {
    @Upsert suspend fun upsertTrackRef(row: UserTrackRefEntity)
    @Upsert suspend fun upsertTrackRefs(rows: List<UserTrackRefEntity>)
    @Upsert suspend fun upsertEntry(row: LibraryEntryEntity)
    @Upsert suspend fun upsertEntries(rows: List<LibraryEntryEntity>)
    @Upsert suspend fun upsertPreference(row: UserTrackPreferenceEntity)
    @Query("SELECT * FROM user_track_preference WHERE local_user_track_ref_id = :trackRefId") suspend fun preference(trackRefId: String): UserTrackPreferenceEntity?
    @Query("SELECT * FROM library_entry WHERE removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry ORDER BY added_at_ms DESC LIMIT :limit") fun entries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId AND removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeEntriesForProfile(profileId: String, limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = 'legacy-unscoped' AND removed_at_ms IS NULL ORDER BY added_at_ms DESC LIMIT :limit") fun activeLegacyEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId ORDER BY added_at_ms DESC LIMIT :limit") fun entriesForProfile(profileId: String, limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM library_entry WHERE server_profile_id = 'legacy-unscoped' ORDER BY added_at_ms DESC LIMIT :limit") fun legacyEntries(limit: Int): Flow<List<LibraryEntryEntity>>
    @Query("SELECT * FROM user_track_ref WHERE local_user_track_ref_id = :localId") suspend fun trackRef(localId: String): UserTrackRefEntity?
    @Query("SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND server_user_track_ref_id = :serverId LIMIT 1") suspend fun trackRefByServerId(profileId: String, serverId: String): UserTrackRefEntity?
    @Query("SELECT * FROM user_track_ref WHERE server_profile_id = :profileId AND server_recording_id = :recordingId AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT 1") suspend fun trackRefByRecording(profileId: String, recordingId: String): UserTrackRefEntity?
    @Query("SELECT * FROM user_track_ref WHERE local_user_track_ref_id IN (:localIds) LIMIT :limit") suspend fun trackRefs(localIds: List<String>, limit: Int): List<UserTrackRefEntity>
    @Query("SELECT * FROM library_entry WHERE local_library_entry_id = :localId") suspend fun entry(localId: String): LibraryEntryEntity?
    @Query("SELECT * FROM library_entry WHERE server_profile_id = :profileId AND server_library_entry_id = :serverId LIMIT 1") suspend fun entryByServerId(profileId: String, serverId: String): LibraryEntryEntity?
    @Query("SELECT * FROM library_entry WHERE local_user_track_ref_id = :trackRefId LIMIT 1") suspend fun entryForTrack(trackRefId: String): LibraryEntryEntity?
    @Query("SELECT count(*) FROM user_track_ref") suspend fun trackRefCount(): Int
    @Query("SELECT count(*) FROM library_entry") suspend fun entryCount(): Int
}

@Dao
interface ImportReviewDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertJob(row: LocalImportJobEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertEntries(rows: List<LocalImportEntryEntity>)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertDecision(row: LocalMatchDecisionEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertCandidates(rows: List<LocalMatchCandidateEntity>)

    @Query("SELECT * FROM local_import_job WHERE import_job_id = :jobId")
    suspend fun job(jobId: String): LocalImportJobEntity?

    @Query("SELECT * FROM local_import_job WHERE server_profile_id = :profileId AND adapter_id = :adapterId AND input_sha256 = :inputSha256 AND (source_uri = :sourceUri OR (source_uri IS NULL AND :sourceUri IS NULL)) LIMIT 1")
    suspend fun jobByIdentity(profileId: String, adapterId: String, inputSha256: String, sourceUri: String?): LocalImportJobEntity?

    @Query("SELECT * FROM local_import_job WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT 1")
    fun observeLatestJob(profileId: String): Flow<LocalImportJobEntity?>

    @Query("SELECT * FROM local_import_entry WHERE import_job_id = :jobId ORDER BY source_position ASC LIMIT :limit")
    suspend fun entries(jobId: String, limit: Int): List<LocalImportEntryEntity>

    @Query("SELECT * FROM local_import_entry WHERE import_job_id = :jobId ORDER BY source_position ASC LIMIT :limit")
    fun observeEntries(jobId: String, limit: Int): Flow<List<LocalImportEntryEntity>>

    @Query("SELECT * FROM local_import_entry WHERE import_entry_id = :entryId")
    suspend fun entry(entryId: String): LocalImportEntryEntity?

    @Query("SELECT * FROM match_decision WHERE decision_id = :decisionId")
    suspend fun decision(decisionId: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE import_entry_id = :entryId AND idempotency_key = :idempotencyKey LIMIT 1")
    suspend fun decisionByIdempotency(entryId: String, idempotencyKey: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE import_entry_id = :entryId AND decision_kind = 'EVALUATION' ORDER BY created_at_ms DESC, decision_id DESC LIMIT 1")
    suspend fun latestEvaluation(entryId: String): LocalMatchDecisionEntity?

    @Query("SELECT * FROM match_decision WHERE supersedes_decision_id = :decisionId LIMIT 1")
    suspend fun successor(decisionId: String): LocalMatchDecisionEntity?

    @Query("SELECT d.* FROM match_decision d JOIN local_import_entry e ON e.import_entry_id = d.import_entry_id WHERE e.import_job_id = :jobId ORDER BY d.created_at_ms ASC, d.decision_id ASC")
    fun observeDecisionsForJob(jobId: String): Flow<List<LocalMatchDecisionEntity>>

    @Query("SELECT * FROM match_candidate WHERE candidate_id = :candidateId")
    suspend fun candidate(candidateId: String): LocalMatchCandidateEntity?

    @Query("SELECT * FROM match_candidate WHERE decision_id = :decisionId ORDER BY rank ASC LIMIT :limit")
    suspend fun candidates(decisionId: String, limit: Int): List<LocalMatchCandidateEntity>

    @Query("SELECT * FROM match_candidate WHERE decision_id = :decisionId ORDER BY rank ASC LIMIT :limit")
    fun observeCandidates(decisionId: String, limit: Int): Flow<List<LocalMatchCandidateEntity>>

    @Query("UPDATE local_import_entry SET workflow_state = :workflowState, selected_local_recording_id = :recordingId, latest_decision_id = :decisionId, last_error_code = :errorCode, updated_at_ms = :nowMs WHERE import_entry_id = :entryId AND ((latest_decision_id IS NULL AND :expectedDecisionId IS NULL) OR latest_decision_id = :expectedDecisionId)")
    suspend fun advanceEntry(
        entryId: String,
        expectedDecisionId: String?,
        workflowState: String,
        recordingId: String?,
        decisionId: String,
        errorCode: String?,
        nowMs: Long,
    ): Int

    @Query("UPDATE local_import_entry SET source_availability = :availability, persisted_uri_permission = :persistedPermission, updated_at_ms = :nowMs WHERE import_entry_id = :entryId")
    suspend fun updateSourceAvailability(entryId: String, availability: String, persistedPermission: Boolean, nowMs: Long): Int

    @Query("UPDATE local_import_job SET state = :newState, updated_at_ms = :nowMs, completed_at_ms = :completedAtMs WHERE import_job_id = :jobId AND state = :expectedState")
    suspend fun transitionJobState(
        jobId: String,
        expectedState: String,
        newState: String,
        nowMs: Long,
        completedAtMs: Long?,
    ): Int

    @Query("UPDATE local_import_job SET state = :state, checkpoint_position = :checkpointPosition, review_required_count = :reviewRequiredCount, resolved_count = :resolvedCount, no_match_count = :noMatchCount, unresolved_count = :unresolvedCount, failed_count = :failedCount, report_json = :reportJson, updated_at_ms = :nowMs, completed_at_ms = :completedAtMs WHERE import_job_id = :jobId")
    suspend fun updateJobSummary(
        jobId: String,
        state: String,
        checkpointPosition: Int,
        reviewRequiredCount: Int,
        resolvedCount: Int,
        noMatchCount: Int,
        unresolvedCount: Int,
        failedCount: Int,
        reportJson: String,
        nowMs: Long,
        completedAtMs: Long?,
    ): Int
}

@Dao
interface ServerFeatureProjectionDao {
    @Upsert
    suspend fun upsertRemoteImportJob(row: RemoteImportJobProjectionEntity)

    @Query("SELECT * FROM remote_import_job_projection WHERE server_profile_id = :profileId AND import_job_id = :importJobId")
    suspend fun remoteImportJob(profileId: String, importJobId: String): RemoteImportJobProjectionEntity?

    @Query("SELECT * FROM remote_import_job_projection WHERE import_job_id = :importJobId LIMIT 1")
    suspend fun remoteImportJobById(importJobId: String): RemoteImportJobProjectionEntity?

    @Query("SELECT * FROM remote_import_job_projection WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT :limit")
    fun observeRemoteImportJobs(profileId: String, limit: Int): Flow<List<RemoteImportJobProjectionEntity>>

    @Upsert
    suspend fun upsertVaultUploadIntent(row: VaultUploadIntentEntity)

    @Query("SELECT * FROM vault_upload_intent WHERE upload_intent_id = :intentId")
    suspend fun vaultUploadIntent(intentId: String): VaultUploadIntentEntity?

    @Query("SELECT * FROM vault_upload_intent WHERE server_profile_id = :profileId AND state IN (:states) ORDER BY updated_at_ms ASC LIMIT :limit")
    suspend fun pendingVaultUploadIntents(profileId: String, states: List<String>, limit: Int): List<VaultUploadIntentEntity>

    @Query("SELECT * FROM vault_upload_intent WHERE server_profile_id = :profileId ORDER BY updated_at_ms DESC LIMIT :limit")
    fun observeVaultUploadIntents(profileId: String, limit: Int): Flow<List<VaultUploadIntentEntity>>

    @Upsert
    suspend fun upsertRecommendationResponseSnapshot(row: RecommendationResponseSnapshotEntity)

    @Query("SELECT * FROM recommendation_response_snapshot WHERE server_profile_id = :profileId AND recommendation_request_id = :requestId")
    suspend fun recommendationResponseSnapshot(profileId: String, requestId: String): RecommendationResponseSnapshotEntity?

    @Query("SELECT * FROM recommendation_response_snapshot WHERE server_profile_id = :profileId ORDER BY received_at_ms DESC LIMIT :limit")
    fun observeRecommendationResponseSnapshots(profileId: String, limit: Int): Flow<List<RecommendationResponseSnapshotEntity>>
}

@Dao
interface PlaylistDao {
    @Upsert suspend fun upsertPlaylist(row: PlaylistEntity)
    @Upsert suspend fun upsertEntry(row: PlaylistEntryEntity)
    @Upsert suspend fun upsertEntries(rows: List<PlaylistEntryEntity>)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertPlaylist(row: PlaylistEntity)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertEntry(row: PlaylistEntryEntity)
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL ORDER BY active_position_key ASC LIMIT :limit") fun activeEntries(playlistId: String, limit: Int): Flow<List<PlaylistEntryEntity>>
    @Query("UPDATE playlist_entry SET position_key = :positionKey, active_position_key = :positionKey WHERE local_playlist_entry_id = :entryId AND removed_at_ms IS NULL") suspend fun moveEntry(entryId: String, positionKey: String): Int
    @Query("SELECT count(*) FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL") suspend fun activeEntryCount(playlistId: String): Int
    @Query("SELECT * FROM playlist WHERE local_playlist_id = :playlistId") suspend fun playlist(playlistId: String): PlaylistEntity?
    @Query("SELECT * FROM playlist WHERE server_profile_id = :profileId AND server_playlist_id = :serverId LIMIT 1") suspend fun playlistByServerId(profileId: String, serverId: String): PlaylistEntity?
    @Query("SELECT * FROM playlist WHERE deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activePlaylists(limit: Int): Flow<List<PlaylistEntity>>
    @Query("SELECT * FROM playlist WHERE server_profile_id = :profileId AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activePlaylistsForProfile(profileId: String, limit: Int): Flow<List<PlaylistEntity>>
    @Query("SELECT * FROM playlist WHERE server_profile_id = 'legacy-unscoped' AND deleted_at_ms IS NULL ORDER BY updated_at_ms DESC LIMIT :limit") fun activeLegacyPlaylists(limit: Int): Flow<List<PlaylistEntity>>
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_entry_id = :entryId") suspend fun entry(entryId: String): PlaylistEntryEntity?
    @Query("SELECT * FROM playlist_entry WHERE server_profile_id = :profileId AND server_playlist_entry_id = :serverId LIMIT 1") suspend fun entryByServerId(profileId: String, serverId: String): PlaylistEntryEntity?
    @Query("SELECT * FROM playlist_entry WHERE local_playlist_id = :playlistId AND removed_at_ms IS NULL ORDER BY active_position_key ASC LIMIT :limit") suspend fun activeEntryList(playlistId: String, limit: Int): List<PlaylistEntryEntity>
}

@Dao
interface LocalAudioDao {
    @Upsert suspend fun upsertState(row: LocalAudioStateEntity)
    @Upsert suspend fun upsertDownloadIntent(row: DownloadIntentEntity)
    @Query("SELECT * FROM local_audio_state WHERE local_user_track_ref_id = :trackRefId ORDER BY updated_at_ms DESC LIMIT :limit") fun states(trackRefId: String, limit: Int): Flow<List<LocalAudioStateEntity>>
    @Query("SELECT * FROM local_audio_state WHERE local_audio_state_id = :localId") suspend fun state(localId: String): LocalAudioStateEntity?
    @Query("SELECT * FROM local_audio_state WHERE content_uri = :contentUri LIMIT 1") suspend fun stateByUri(contentUri: String): LocalAudioStateEntity?
    @Query("SELECT * FROM local_audio_state WHERE local_user_track_ref_id = :trackRefId ORDER BY CASE status WHEN 'AVAILABLE' THEN 0 ELSE 1 END, updated_at_ms DESC LIMIT :limit") suspend fun statesForPlayback(trackRefId: String, limit: Int): List<LocalAudioStateEntity>
    @Query("SELECT * FROM download_intent WHERE download_intent_id = :intentId") suspend fun downloadIntent(intentId: String): DownloadIntentEntity?
    @Query("SELECT * FROM download_intent WHERE media3_download_id = :downloadId LIMIT 1") suspend fun downloadIntentByMedia3Id(downloadId: String): DownloadIntentEntity?
    @Query("SELECT * FROM download_intent ORDER BY created_at_ms ASC LIMIT :limit") suspend fun downloadIntents(limit: Int): List<DownloadIntentEntity>
    @Query("SELECT * FROM download_intent ORDER BY created_at_ms DESC LIMIT :limit") fun observeDownloadIntents(limit: Int): Flow<List<DownloadIntentEntity>>
    @Query("SELECT * FROM download_intent WHERE local_user_track_ref_id = :trackRefId ORDER BY created_at_ms DESC LIMIT :limit") suspend fun downloadIntentsForTrack(trackRefId: String, limit: Int): List<DownloadIntentEntity>
    @Query("SELECT * FROM local_audio_state WHERE storage_class IN (:storageClasses) AND status = 'AVAILABLE' ORDER BY last_accessed_at_ms ASC, created_at_ms ASC LIMIT :limit") suspend fun evictionCandidates(storageClasses: List<String>, limit: Int): List<LocalAudioStateEntity>
}

@Dao
interface QueueDao {
    @Upsert suspend fun upsertSnapshot(row: QueueSnapshotEntity)
    @Upsert suspend fun upsertEntries(rows: List<QueueEntryEntity>)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertSnapshot(row: QueueSnapshotEntity)
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertEntries(rows: List<QueueEntryEntity>)
    @Query("SELECT * FROM queue_snapshot WHERE active_slot = 'ACTIVE' LIMIT 1") fun activeSnapshot(): Flow<QueueSnapshotEntity?>
    @Query("SELECT * FROM queue_snapshot WHERE active_slot = 'ACTIVE' LIMIT 1") suspend fun activeSnapshotOnce(): QueueSnapshotEntity?
    @Query("SELECT * FROM queue_snapshot WHERE active_slot IS NULL AND active_listening_event_id IS NOT NULL ORDER BY updated_at_ms ASC LIMIT :limit") suspend fun inactiveSnapshotsWithActiveSessions(limit: Int): List<QueueSnapshotEntity>
    @Query("SELECT * FROM queue_snapshot WHERE queue_snapshot_id = :snapshotId") suspend fun snapshot(snapshotId: String): QueueSnapshotEntity?
    @Query("SELECT * FROM queue_entry WHERE queue_entry_id = :entryId") suspend fun entry(entryId: String): QueueEntryEntity?
    @Query("SELECT * FROM queue_entry WHERE queue_snapshot_id = :snapshotId ORDER BY position ASC LIMIT :limit") suspend fun entries(snapshotId: String, limit: Int): List<QueueEntryEntity>
    @Query("UPDATE queue_snapshot SET is_active = 0, active_slot = NULL, updated_at_ms = :nowMs WHERE active_slot = 'ACTIVE' AND queue_snapshot_id != :exceptSnapshotId") suspend fun deactivateOtherSnapshots(exceptSnapshotId: String, nowMs: Long): Int
    @Query("UPDATE queue_snapshot SET current_entry_id = :entryId, current_position_ms = :positionMs, shuffle_mode = :shuffleMode, repeat_mode = :repeatMode, seed = :seed, active_listening_event_id = :listeningEventId, active_session_started_at_ms = :startedAtMs, active_session_start_position_ms = :startPositionMs, active_session_observed_played_ms = :observedPlayedMs, active_session_user_id = :sessionUserId, active_session_device_id = :sessionDeviceId, active_session_server_profile_id = :sessionServerProfileId, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId")
    suspend fun checkpoint(
        snapshotId: String,
        entryId: String?,
        positionMs: Long,
        shuffleMode: String,
        repeatMode: String,
        seed: Long?,
        listeningEventId: String?,
        startedAtMs: Long?,
        startPositionMs: Long?,
        observedPlayedMs: Long?,
        sessionUserId: String?,
        sessionDeviceId: String?,
        sessionServerProfileId: String?,
        nowMs: Long,
    ): Int
    @Query("UPDATE queue_snapshot SET current_entry_id = :entryId, current_position_ms = :positionMs, active_listening_event_id = NULL, active_session_started_at_ms = NULL, active_session_start_position_ms = NULL, active_session_observed_played_ms = NULL, active_session_user_id = NULL, active_session_device_id = NULL, active_session_server_profile_id = NULL, updated_at_ms = :nowMs WHERE queue_snapshot_id = :snapshotId AND active_listening_event_id = :eventId")
    suspend fun clearFinalizedSession(snapshotId: String, eventId: String, entryId: String, positionMs: Long, nowMs: Long): Int
}

@Dao
interface WaveDao {
    @Upsert suspend fun upsertRoom(row: app.autplay.data.local.entity.WaveRoomEntity)
    @Upsert suspend fun upsertPreflight(rows: List<app.autplay.data.local.entity.WavePreflightEntity>)
    @Upsert suspend fun upsertQueue(rows: List<app.autplay.data.local.entity.WaveQueueProjectionEntity>)
    @Query("SELECT * FROM wave_room WHERE room_id = :roomId") suspend fun room(roomId: String): app.autplay.data.local.entity.WaveRoomEntity?
    @Query("UPDATE wave_room SET last_sequence = :sequence, updated_at_ms = :nowMs WHERE room_id = :roomId AND last_sequence < :sequence") suspend fun advanceSequence(roomId: String, sequence: Long, nowMs: Long): Int
    @Query("SELECT * FROM wave_preflight WHERE room_id = :roomId ORDER BY queue_entry_id") suspend fun preflight(roomId: String): List<app.autplay.data.local.entity.WavePreflightEntity>
    @Query("SELECT * FROM wave_queue_projection WHERE room_id = :roomId AND sequence = :sequence ORDER BY position LIMIT :limit") suspend fun queue(roomId: String, sequence: Long, limit: Int): List<app.autplay.data.local.entity.WaveQueueProjectionEntity>
    @Query("DELETE FROM wave_queue_projection WHERE room_id = :roomId") suspend fun clearQueue(roomId: String): Int
    @Query("DELETE FROM wave_preflight WHERE room_id = :roomId") suspend fun clearPreflight(roomId: String): Int

    /** Snapshot replacement is atomic; no partial shared queue is observable. */
    @Transaction
    suspend fun replaceSnapshot(
        room: app.autplay.data.local.entity.WaveRoomEntity,
        preflight: List<app.autplay.data.local.entity.WavePreflightEntity>,
        queue: List<app.autplay.data.local.entity.WaveQueueProjectionEntity>,
    ) {
        clearQueue(room.roomId)
        clearPreflight(room.roomId)
        upsertRoom(room)
        upsertPreflight(preflight)
        upsertQueue(queue)
    }
}

@Dao
interface HistoryDao {
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insert(event: ListeningEventEntity)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertOnce(event: ListeningEventEntity): Long
    @Upsert suspend fun upsert(event: ListeningEventEntity)
    @Query("SELECT * FROM listening_event WHERE listening_event_id = :eventId") suspend fun event(eventId: String): ListeningEventEntity?
    @Query("SELECT * FROM listening_event WHERE listening_event_id = :eventId AND server_profile_id = :profileId") suspend fun event(profileId: String, eventId: String): ListeningEventEntity?
    @Query("SELECT * FROM listening_event ORDER BY started_at_ms DESC LIMIT :limit") fun recent(limit: Int): Flow<List<ListeningEventEntity>>
    @Query("SELECT * FROM listening_event WHERE server_profile_id = :profileId ORDER BY started_at_ms DESC LIMIT :limit") fun recentForProfile(profileId: String, limit: Int): Flow<List<ListeningEventEntity>>
    @Query("SELECT * FROM listening_event WHERE server_profile_id = 'legacy-unscoped' ORDER BY started_at_ms DESC LIMIT :limit") fun recentLegacy(limit: Int): Flow<List<ListeningEventEntity>>
}

@Dao
interface JournalDao {
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insert(event: OfflineJournalEventEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertLineage(lineage: JournalLineageEntity): Long

    @Query("SELECT * FROM journal_lineage WHERE lineage_id = :lineageId")
    suspend fun lineageById(lineageId: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE device_id = :deviceId")
    suspend fun lineageByDeviceId(deviceId: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE journal_epoch = :journalEpoch")
    suspend fun lineageByJournalEpoch(journalEpoch: String): JournalLineageEntity?

    @Query("SELECT * FROM journal_lineage WHERE user_id = :userId AND device_id = :deviceId AND journal_epoch = :journalEpoch")
    suspend fun lineageByBinding(userId: String, deviceId: String, journalEpoch: String): JournalLineageEntity?

    @Query("SELECT count(*) FROM journal_lineage")
    suspend fun countLineages(): Int

    @Query("UPDATE journal_lineage SET next_device_sequence = next_device_sequence + 1 WHERE lineage_id = :lineageId")
    suspend fun incrementSequence(lineageId: String): Int

    @Query("SELECT next_device_sequence - 1 FROM journal_lineage WHERE lineage_id = :lineageId")
    suspend fun allocatedSequence(lineageId: String): Long?

    @Transaction
    suspend fun allocateSequence(lineageId: String): Long {
        check(incrementSequence(lineageId) == 1) { "JOURNAL_LINEAGE_NOT_FOUND" }
        return checkNotNull(allocatedSequence(lineageId))
    }

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertOutbox(row: LocalMutationOutboxEntity)

    @Query("SELECT * FROM local_mutation_outbox WHERE local_change_id = :localChangeId")
    suspend fun outbox(localChangeId: String): LocalMutationOutboxEntity?

    @Query("SELECT * FROM local_mutation_outbox WHERE materialization_state = 'UNMATERIALIZED' AND materialized_event_id IS NULL ORDER BY occurred_at_ms ASC, local_change_id ASC LIMIT :limit")
    suspend fun pendingOutbox(limit: Int): List<LocalMutationOutboxEntity>

    @Query("SELECT count(*) FROM local_mutation_outbox")
    suspend fun outboxCount(): Int

    @Query("UPDATE local_mutation_outbox SET materialization_state = 'MATERIALIZED', materialized_event_id = :eventId, materialized_at_ms = :materializedAtMs WHERE local_change_id = :localChangeId AND materialization_state = 'UNMATERIALIZED' AND materialized_event_id IS NULL")
    suspend fun linkOutboxMaterialized(localChangeId: String, eventId: String, materializedAtMs: Long): Int

    @Query("SELECT * FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state = 'PENDING' AND (next_attempt_at_ms IS NULL OR next_attempt_at_ms <= :nowMs) ORDER BY device_sequence ASC LIMIT :limit")
    suspend fun nextPending(lineageId: String, nowMs: Long, limit: Int): List<OfflineJournalEventEntity>

    @Query("UPDATE offline_journal_event SET state = 'PENDING', lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND state = 'SENDING' AND lease_expires_at_ms <= :nowMs")
    suspend fun recoverExpiredLeases(lineageId: String, nowMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = 'SENDING', lease_token = :leaseToken, lease_expires_at_ms = :leaseExpiresAtMs, attempt_count = attempt_count + 1 WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'PENDING'")
    suspend fun lease(lineageId: String, eventId: String, leaseToken: String, leaseExpiresAtMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = 'ACKED', acked_at_ms = :ackedAtMs, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun acknowledge(lineageId: String, eventId: String, leaseToken: String, ackedAtMs: Long): Int

    @Query("UPDATE offline_journal_event SET state = :state, next_attempt_at_ms = :nextAttemptAtMs, last_error_code = :errorCode, lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun finishAttempt(lineageId: String, eventId: String, leaseToken: String, state: String, nextAttemptAtMs: Long?, errorCode: String?): Int

    @Query("UPDATE offline_journal_event SET state = 'PENDING', attempt_count = CASE WHEN attempt_count > 0 THEN attempt_count - 1 ELSE 0 END, next_attempt_at_ms = NULL, last_error_code = 'SESSION_REQUIRED', lease_token = NULL, lease_expires_at_ms = NULL WHERE journal_lineage_id = :lineageId AND event_id = :eventId AND state = 'SENDING' AND lease_token = :leaseToken")
    suspend fun releaseForSession(lineageId: String, eventId: String, leaseToken: String): Int

    @Query("SELECT * FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND device_sequence >= :fromSequence AND state IN ('ACKED', 'DEAD_LETTER') ORDER BY device_sequence ASC LIMIT :limit")
    suspend fun compactable(lineageId: String, fromSequence: Long, limit: Int): List<OfflineJournalEventEntity>

    @Query("DELETE FROM offline_journal_event WHERE event_id IN (:eventIds) AND state IN ('ACKED', 'DEAD_LETTER')")
    suspend fun deleteTerminal(eventIds: List<String>): Int

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state IN ('PENDING', 'SENDING')")
    fun observePendingCount(lineageId: String): Flow<Int>

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND state = 'DEAD_LETTER'")
    fun observeDeadLetterCount(lineageId: String): Flow<Int>

    @Query("SELECT * FROM offline_journal_event WHERE event_id = :eventId")
    suspend fun event(eventId: String): OfflineJournalEventEntity?

    @Query("SELECT count(*) FROM offline_journal_event")
    suspend fun eventCount(): Int

    @Query("SELECT count(*) FROM offline_journal_event WHERE journal_lineage_id = :lineageId")
    suspend fun eventCountForLineage(lineageId: String): Int
}

@Dao
interface SyncDao {
    @Upsert suspend fun upsertCursor(cursor: SyncCursorEntity)
    @Upsert suspend fun upsertRuntimeStatus(status: SyncRuntimeStatusEntity)
    @Query("SELECT * FROM sync_runtime_status WHERE server_profile_id = :profileId") suspend fun runtimeStatus(profileId: String): SyncRuntimeStatusEntity?
    @Upsert suspend fun upsertBootstrapState(state: SyncBootstrapStateEntity)
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun insertInteractionFact(fact: RecommendationInteractionFactEntity): Long
    @Query("SELECT * FROM sync_bootstrap_state WHERE server_profile_id = :profileId") suspend fun bootstrapState(profileId: String): SyncBootstrapStateEntity?
    @Query("SELECT * FROM sync_cursor WHERE server_profile_id = :profileId") suspend fun cursor(profileId: String): SyncCursorEntity?
    @Query("UPDATE sync_cursor SET last_acked_device_sequence = :sequence, updated_at_ms = :nowMs WHERE server_profile_id = :profileId AND last_acked_device_sequence < :sequence AND NOT EXISTS (SELECT 1 FROM offline_journal_event WHERE journal_lineage_id = :lineageId AND device_sequence <= :sequence AND state NOT IN ('ACKED', 'DEAD_LETTER', 'CONFLICT'))") suspend fun advanceAck(profileId: String, lineageId: String, sequence: Long, nowMs: Long): Int
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun markApplied(event: AppliedServerEventEntity): Long
    @Insert(onConflict = OnConflictStrategy.IGNORE) suspend fun defer(event: DeferredServerEventEntity): Long
    @Query("SELECT EXISTS(SELECT 1 FROM applied_server_event WHERE server_profile_id = :profileId AND server_event_id = :eventId) OR EXISTS(SELECT 1 FROM deferred_server_event WHERE server_profile_id = :profileId AND server_event_id = :eventId)") suspend fun isServerEventKnown(profileId: String, eventId: String): Boolean
    @Upsert suspend fun upsertRedirect(redirect: AggregateRedirectEntity)
    @Query("SELECT * FROM aggregate_redirect WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND alias_local_id = :aliasLocalId") suspend fun redirect(profileId: String, aggregateType: String, aliasLocalId: String): AggregateRedirectEntity?
    @Query("SELECT * FROM aggregate_redirect WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND alias_server_id = :aliasServerId") suspend fun redirectByServerId(profileId: String, aggregateType: String, aliasServerId: String): AggregateRedirectEntity?
    @Upsert suspend fun upsertTombstone(row: TombstoneEntity)
    @Query("SELECT * FROM tombstone WHERE server_profile_id = :profileId AND aggregate_type = :aggregateType AND aggregate_server_id = :serverId LIMIT 1") suspend fun tombstoneByServerId(profileId: String, aggregateType: String, serverId: String): TombstoneEntity?
    @Upsert suspend fun upsertConflict(row: SyncConflictEntity)
    @Query("SELECT count(*) FROM sync_conflict WHERE server_profile_id = :profileId AND status = 'OPEN'") fun observeOpenConflictCount(profileId: String): Flow<Int>
    @Query("DELETE FROM tombstone WHERE server_profile_id = :profileId AND server_acked = 1 AND retain_until_ms <= :nowMs") suspend fun compactTombstones(profileId: String, nowMs: Long): Int
}

data class LocalRecommendationCandidateRow(
    val localUserTrackRefId: String,
    val recordingId: String,
    val title: String,
    val artist: String,
    val isLocallyAvailable: Boolean,
    val preference: String,
    val excludedFromTaste: Boolean,
    val preferenceUpdatedAtMs: Long,
    val latestListenedAtMs: Long?,
    val latestSkipAtMs: Long?,
)

data class RecentRelevantReleaseRow(
    val localReleaseId: String,
    val title: String,
    val artist: String,
    val releaseDateText: String?,
    val artworkRef: String?,
    val projectionUpdatedAtMs: Long,
)

@Dao
interface RecommendationPackDao {
    @Upsert suspend fun upsert(pack: RecommendationPackEntity)

    @Query("SELECT * FROM recommendation_pack WHERE server_profile_id = :profileId AND owner_user_id = :userId ORDER BY created_at_ms DESC, offline_pack_id DESC LIMIT :limit")
    suspend fun latest(profileId: String, userId: String, limit: Int): List<RecommendationPackEntity>

    @Query("SELECT * FROM recommendation_pack WHERE offline_pack_id = :packId AND server_profile_id = :profileId AND owner_user_id = :userId LIMIT 1")
    suspend fun pack(packId: String, profileId: String, userId: String): RecommendationPackEntity?

    @Query("SELECT * FROM recommendation_pack WHERE server_profile_id = :profileId AND owner_user_id = :userId AND expires_at_ms > :nowMs ORDER BY created_at_ms DESC, offline_pack_id DESC LIMIT 1")
    suspend fun active(profileId: String, userId: String, nowMs: Long): RecommendationPackEntity?

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertPresentation(row: RecommendationPresentationEntity)

    @Query("SELECT * FROM recommendation_presentation WHERE server_profile_id = :profileId AND owner_user_id = :userId AND presentation_id = :presentationId AND recommendation_request_id = :requestId AND source_rank = :sourceRank")
    suspend fun presentation(profileId: String, userId: String, presentationId: String, requestId: String, sourceRank: Int): RecommendationPresentationEntity?

    @Query("SELECT * FROM recommendation_presentation WHERE impression_event_id = :eventId")
    suspend fun presentationByEventId(eventId: String): RecommendationPresentationEntity?

    @Query("SELECT count(*) FROM recommendation_presentation WHERE server_profile_id = :profileId AND owner_user_id = :userId")
    suspend fun presentationCount(profileId: String, userId: String): Int

    @Query(
        """
        SELECT
            u.local_user_track_ref_id AS localUserTrackRefId,
            u.server_recording_id AS recordingId,
            COALESCE(r.title, u.raw_title, 'Unknown track') AS title,
            COALESCE(r.display_artist, u.raw_artist, 'Unknown artist') AS artist,
            CASE WHEN
                le.availability_status = 'LOCAL'
                OR EXISTS (
                    SELECT 1 FROM local_audio_state a
                    WHERE a.local_user_track_ref_id = u.local_user_track_ref_id
                      AND a.status = 'AVAILABLE'
                )
                OR EXISTS (
                    SELECT 1 FROM download_intent d
                    WHERE d.local_user_track_ref_id = u.local_user_track_ref_id
                      AND d.state = 'COMPLETED'
                )
            THEN 1 ELSE 0 END AS isLocallyAvailable,
            COALESCE(p.preference, 'NEUTRAL') AS preference,
            COALESCE(p.excluded_from_taste, 0) AS excludedFromTaste,
            COALESCE(p.updated_at_ms, 0) AS preferenceUpdatedAtMs,
            (
                SELECT MAX(h.started_at_ms) FROM listening_event h
                WHERE h.server_profile_id = :profileId
                  AND h.local_user_track_ref_id = u.local_user_track_ref_id
            ) AS latestListenedAtMs,
            (
                SELECT MAX(h.started_at_ms) FROM listening_event h
                WHERE h.server_profile_id = :profileId
                  AND h.local_user_track_ref_id = u.local_user_track_ref_id
                  AND (
                    (h.completion_ratio IS NOT NULL AND h.completion_ratio < 0.2)
                    OR (h.completion_ratio IS NULL AND h.played_ms < 30000)
                  )
            ) AS latestSkipAtMs
        FROM sync_cursor sc
        JOIN journal_lineage owner
          ON owner.lineage_id = sc.journal_lineage_id
         AND owner.user_id = :userId
        JOIN user_track_ref u ON u.server_profile_id = sc.server_profile_id
        LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id
        LEFT JOIN library_entry le
          ON le.local_user_track_ref_id = u.local_user_track_ref_id
         AND le.server_profile_id = :profileId
         AND le.removed_at_ms IS NULL
        LEFT JOIN user_track_preference p
          ON p.local_user_track_ref_id = u.local_user_track_ref_id
         AND p.server_profile_id = :profileId
        WHERE sc.server_profile_id = :profileId
          AND u.server_profile_id = :profileId
          AND u.server_recording_id IN (:recordingIds)
          AND u.deleted_at_ms IS NULL
        ORDER BY u.local_user_track_ref_id ASC
        LIMIT :limit
        """,
    )
    suspend fun localCandidates(profileId: String, userId: String, recordingIds: List<String>, limit: Int): List<LocalRecommendationCandidateRow>

    @Query(
        """
        SELECT DISTINCT
            rel.local_release_id AS localReleaseId,
            rel.title AS title,
            rel.display_artist AS artist,
            rel.release_date_text AS releaseDateText,
            rel.artwork_ref AS artworkRef,
            rel.projection_updated_at_ms AS projectionUpdatedAtMs
        FROM sync_cursor sc
        JOIN journal_lineage owner
          ON owner.lineage_id = sc.journal_lineage_id
         AND owner.user_id = :userId
        CROSS JOIN release_projection rel
        WHERE sc.server_profile_id = :profileId
          AND rel.is_deleted = 0
          AND EXISTS (
            SELECT 1
            FROM user_track_ref u
            JOIN recording_projection owned ON owned.local_recording_id = u.local_recording_id
            JOIN library_entry le
              ON le.local_user_track_ref_id = u.local_user_track_ref_id
             AND le.server_profile_id = :profileId
             AND le.removed_at_ms IS NULL
            WHERE u.server_profile_id = :profileId
              AND u.deleted_at_ms IS NULL
              AND owned.is_deleted = 0
              AND owned.normalized_artist = LOWER(TRIM(rel.display_artist))
          )
        ORDER BY rel.release_date_text DESC, rel.projection_updated_at_ms DESC, rel.local_release_id ASC
        LIMIT :limit
        """,
    )
    suspend fun recentRelevantReleases(profileId: String, userId: String, limit: Int): List<RecentRelevantReleaseRow>
}

@Dao
interface SearchDao {
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertContent(row: TrackSearchContentEntity): Long
    @Insert(onConflict = OnConflictStrategy.ABORT) suspend fun insertContents(rows: List<TrackSearchContentEntity>): List<Long>
    @Update suspend fun updateContent(row: TrackSearchContentEntity): Int
    @Delete suspend fun deleteContent(row: TrackSearchContentEntity): Int
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid WHERE track_search_fts MATCH :query ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun search(query: String, limit: Int): List<String>
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid JOIN user_track_ref u ON u.local_user_track_ref_id = c.local_user_track_ref_id WHERE track_search_fts MATCH :query AND u.server_profile_id = :profileId ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun searchForProfile(query: String, profileId: String, limit: Int): List<String>
    @Query("SELECT c.local_user_track_ref_id FROM track_search_fts f JOIN track_search_content c ON c.rowid = f.rowid JOIN user_track_ref u ON u.local_user_track_ref_id = c.local_user_track_ref_id WHERE track_search_fts MATCH :query AND u.server_profile_id = 'legacy-unscoped' ORDER BY bm25(track_search_fts), c.rowid ASC LIMIT :limit") suspend fun searchLegacy(query: String, limit: Int): List<String>
    @Query("DELETE FROM track_search_content") suspend fun clearContent(): Int
}
