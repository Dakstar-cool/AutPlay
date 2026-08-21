package app.autplay.data.local.entity

import android.content.ContentResolver
import androidx.core.net.toUri
import androidx.room3.ColumnInfo
import androidx.room3.Entity
import androidx.room3.ForeignKey
import androidx.room3.Fts5
import androidx.room3.FtsOptions
import androidx.room3.Index
import androidx.room3.PrimaryKey

@Entity(tableName = "recording_projection", indices = [Index(value = ["server_recording_id"], unique = true), Index(value = ["normalized_artist", "normalized_title"]), Index(value = ["projection_updated_at_ms"]), Index(value = ["redirect_server_recording_id"])])
data class RecordingProjectionEntity(@PrimaryKey @ColumnInfo(name = "local_recording_id") val localRecordingId: String, @ColumnInfo(name = "server_recording_id") val serverRecordingId: String?, @ColumnInfo(name = "redirect_server_recording_id") val redirectServerRecordingId: String?, val title: String, @ColumnInfo(name = "normalized_title") val normalizedTitle: String, @ColumnInfo(name = "display_artist") val displayArtist: String, @ColumnInfo(name = "normalized_artist") val normalizedArtist: String, @ColumnInfo(name = "artist_credit_json") val artistCreditJson: String, @ColumnInfo(name = "duration_ms") val durationMs: Long?, @ColumnInfo(name = "recording_kind") val recordingKind: String, @ColumnInfo(name = "version_text") val versionText: String?, @ColumnInfo(name = "explicit_state") val explicitState: Int, @ColumnInfo(name = "artwork_ref") val artworkRef: String?, @ColumnInfo(name = "catalog_version") val catalogVersion: Long, @ColumnInfo(name = "projection_updated_at_ms") val projectionUpdatedAtMs: Long, @ColumnInfo(name = "is_deleted") val isDeleted: Boolean = false)

@Entity(tableName = "release_projection", indices = [Index(value = ["server_release_id"], unique = true)])
data class ReleaseProjectionEntity(@PrimaryKey @ColumnInfo(name = "local_release_id") val localReleaseId: String, @ColumnInfo(name = "server_release_id") val serverReleaseId: String?, @ColumnInfo(name = "server_release_group_id") val serverReleaseGroupId: String?, val title: String, @ColumnInfo(name = "display_artist") val displayArtist: String, @ColumnInfo(name = "release_date_text") val releaseDateText: String?, @ColumnInfo(name = "release_type") val releaseType: String?, @ColumnInfo(name = "artwork_ref") val artworkRef: String?, @ColumnInfo(name = "catalog_version") val catalogVersion: Long, @ColumnInfo(name = "projection_updated_at_ms") val projectionUpdatedAtMs: Long, @ColumnInfo(name = "is_deleted") val isDeleted: Boolean)

/** Server-authoritative identity.  Display names are intentionally neither keys nor unique. */
@Entity(tableName = "artist_projection", indices = [Index(value = ["server_profile_id", "server_artist_id"], unique = true), Index(value = ["server_profile_id", "name"])])
data class ArtistProjectionEntity(
    @PrimaryKey @ColumnInfo(name = "local_artist_id") val localArtistId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "server_artist_id") val serverArtistId: String,
    val name: String,
    @ColumnInfo(name = "sort_name") val sortName: String?,
    @ColumnInfo(name = "artist_type") val artistType: String?,
    val disambiguation: String?,
    @ColumnInfo(name = "country_code") val countryCode: String?,
    @ColumnInfo(name = "identity_status") val identityStatus: String?,
    @ColumnInfo(name = "server_row_version") val serverRowVersion: Long,
    @ColumnInfo(name = "projection_updated_at_ms") val projectionUpdatedAtMs: Long,
    @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long? = null,
)

/** Ordered credit aggregate; an empty child set represents unresolved legacy evidence. */
@Entity(tableName = "artist_credit_projection", indices = [Index(value = ["server_profile_id", "server_artist_credit_id"], unique = true)])
data class ArtistCreditProjectionEntity(
    @PrimaryKey @ColumnInfo(name = "local_artist_credit_id") val localArtistCreditId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "server_artist_credit_id") val serverArtistCreditId: String,
    @ColumnInfo(name = "display_name") val displayName: String?,
    @ColumnInfo(name = "server_row_version") val serverRowVersion: Long,
    @ColumnInfo(name = "projection_updated_at_ms") val projectionUpdatedAtMs: Long,
    @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long? = null,
)

@Entity(tableName = "artist_credit_name_projection", indices = [Index(value = ["server_profile_id", "server_artist_credit_id", "position"], unique = true), Index(value = ["server_profile_id", "server_artist_id"])])
data class ArtistCreditNameProjectionEntity(
    @PrimaryKey @ColumnInfo(name = "local_artist_credit_name_id") val localArtistCreditNameId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "server_artist_credit_id") val serverArtistCreditId: String,
    @ColumnInfo(name = "server_artist_id") val serverArtistId: String,
    val position: Int,
    @ColumnInfo(name = "credited_name") val creditedName: String,
    @ColumnInfo(name = "join_phrase") val joinPhrase: String,
    val role: String,
)

@Entity(tableName = "catalog_artist_credit_link", indices = [Index(value = ["server_profile_id", "subject_type", "subject_server_id"], unique = true), Index(value = ["server_profile_id", "server_artist_credit_id"])])
data class CatalogArtistCreditLinkEntity(
    @PrimaryKey @ColumnInfo(name = "local_catalog_artist_credit_link_id") val localLinkId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "subject_type") val subjectType: String,
    @ColumnInfo(name = "subject_server_id") val subjectServerId: String,
    @ColumnInfo(name = "server_artist_credit_id") val serverArtistCreditId: String,
    @ColumnInfo(name = "owner_scope_id") val ownerScopeId: String,
    @ColumnInfo(name = "owner_page_count") val ownerPageCount: Int,
    @ColumnInfo(name = "last_owner_page") val lastOwnerPage: Int,
    @ColumnInfo(name = "owner_scope_complete") val ownerScopeComplete: Boolean,
    @ColumnInfo(name = "server_row_version") val serverRowVersion: Long,
    @ColumnInfo(name = "last_server_sequence") val lastServerSequence: Long,
    @ColumnInfo(name = "projection_updated_at_ms") val projectionUpdatedAtMs: Long,
    @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long? = null,
)

/** One bounded page member; visibility still requires a live same-profile UTR. */
@Entity(
    tableName = "catalog_artist_credit_link_owner",
    indices = [
        Index(value = ["server_profile_id", "subject_type", "subject_server_id", "owner_scope_id", "owner_recording_id"], unique = true),
        Index(value = ["server_profile_id", "owner_recording_id"]),
    ],
)
data class CatalogArtistCreditLinkOwnerEntity(
    @PrimaryKey @ColumnInfo(name = "local_catalog_artist_credit_link_owner_id") val localOwnerId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "subject_type") val subjectType: String,
    @ColumnInfo(name = "subject_server_id") val subjectServerId: String,
    @ColumnInfo(name = "owner_scope_id") val ownerScopeId: String,
    @ColumnInfo(name = "owner_recording_id") val ownerRecordingId: String,
)

@Entity(tableName = "release_track_projection", foreignKeys = [ForeignKey(entity = ReleaseProjectionEntity::class, parentColumns = ["local_release_id"], childColumns = ["local_release_id"], onDelete = ForeignKey.RESTRICT), ForeignKey(entity = RecordingProjectionEntity::class, parentColumns = ["local_recording_id"], childColumns = ["local_recording_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["server_release_track_id"], unique = true), Index(value = ["local_release_id"]), Index(value = ["local_recording_id"])])
data class ReleaseTrackProjectionEntity(@PrimaryKey @ColumnInfo(name = "local_release_track_id") val localReleaseTrackId: String, @ColumnInfo(name = "server_release_track_id") val serverReleaseTrackId: String?, @ColumnInfo(name = "local_release_id") val localReleaseId: String, @ColumnInfo(name = "local_recording_id") val localRecordingId: String, @ColumnInfo(name = "medium_position") val mediumPosition: Int, @ColumnInfo(name = "sequence_no") val sequenceNo: Int, @ColumnInfo(name = "number_text") val numberText: String?, @ColumnInfo(name = "credited_title") val creditedTitle: String, @ColumnInfo(name = "credited_artist") val creditedArtist: String, @ColumnInfo(name = "duration_ms") val durationMs: Long?)

@Entity(tableName = "user_track_ref", foreignKeys = [ForeignKey(entity = RecordingProjectionEntity::class, parentColumns = ["local_recording_id"], childColumns = ["local_recording_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["server_profile_id", "server_user_track_ref_id"], unique = true), Index(value = ["server_recording_id"]), Index(value = ["local_recording_id"]), Index(value = ["resolution_status", "updated_at_ms"]), Index(value = ["sync_state", "updated_at_ms"]), Index(value = ["server_profile_id"])])
data class UserTrackRefEntity(@PrimaryKey @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, @ColumnInfo(name = "server_user_track_ref_id") val serverUserTrackRefId: String?, @ColumnInfo(name = "local_recording_id") val localRecordingId: String?, @ColumnInfo(name = "server_recording_id") val serverRecordingId: String?, @ColumnInfo(name = "resolution_status") val resolutionStatus: String, @ColumnInfo(name = "raw_title") val rawTitle: String?, @ColumnInfo(name = "raw_artist") val rawArtist: String?, @ColumnInfo(name = "raw_album") val rawAlbum: String?, @ColumnInfo(name = "raw_duration_ms") val rawDurationMs: Long?, @ColumnInfo(name = "resolution_confidence") val resolutionConfidence: Double?, @ColumnInfo(name = "sync_state") val syncState: String, @ColumnInfo(name = "server_row_version") val serverRowVersion: Long?, @ColumnInfo(name = "last_local_sequence") val lastLocalSequence: Long, @ColumnInfo(name = "created_at_ms") val createdAtMs: Long, @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long, @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long?, @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped")

/** Bounded local-first import envelope. The private source URI never enters report_json. */
@Entity(
    tableName = "local_import_job",
    indices = [
        Index(value = ["server_profile_id", "adapter_id", "input_sha256", "source_uri"], unique = true),
        Index(value = ["server_profile_id", "updated_at_ms"]),
    ],
)
data class LocalImportJobEntity(
    @PrimaryKey @ColumnInfo(name = "import_job_id") val importJobId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "adapter_id") val adapterId: String,
    @ColumnInfo(name = "adapter_version") val adapterVersion: String,
    @ColumnInfo(name = "envelope_version") val envelopeVersion: Int,
    @ColumnInfo(name = "input_sha256") val inputSha256: String,
    @ColumnInfo(name = "input_digest_verified") val inputDigestVerified: Boolean,
    @ColumnInfo(name = "source_uri") val sourceUri: String?,
    @ColumnInfo(name = "persisted_uri_permission") val persistedUriPermission: Boolean,
    @ColumnInfo(name = "source_availability") val sourceAvailability: String,
    val state: String,
    @ColumnInfo(name = "checkpoint_position") val checkpointPosition: Int,
    @ColumnInfo(name = "total_entries") val totalEntries: Int,
    @ColumnInfo(name = "review_required_count") val reviewRequiredCount: Int,
    @ColumnInfo(name = "resolved_count") val resolvedCount: Int,
    @ColumnInfo(name = "no_match_count") val noMatchCount: Int,
    @ColumnInfo(name = "unresolved_count") val unresolvedCount: Int,
    @ColumnInfo(name = "failed_count") val failedCount: Int,
    @ColumnInfo(name = "report_json") val reportJson: String,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
    @ColumnInfo(name = "completed_at_ms") val completedAtMs: Long?,
)

/** One source row. Raw provenance remains after review and duplicate metadata is not coalesced. */
@Entity(
    tableName = "local_import_entry",
    foreignKeys = [
        ForeignKey(
            entity = LocalImportJobEntity::class,
            parentColumns = ["import_job_id"],
            childColumns = ["import_job_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
        ForeignKey(
            entity = UserTrackRefEntity::class,
            parentColumns = ["local_user_track_ref_id"],
            childColumns = ["local_user_track_ref_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
        ForeignKey(
            entity = RecordingProjectionEntity::class,
            parentColumns = ["local_recording_id"],
            childColumns = ["selected_local_recording_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["import_job_id", "source_row_key"], unique = true),
        Index(value = ["import_job_id", "source_position"], unique = true),
        Index(value = ["import_job_id", "workflow_state"]),
        Index(value = ["local_user_track_ref_id"]),
        Index(value = ["selected_local_recording_id"]),
        Index(value = ["latest_decision_id"]),
    ],
)
data class LocalImportEntryEntity(
    @PrimaryKey @ColumnInfo(name = "import_entry_id") val importEntryId: String,
    @ColumnInfo(name = "import_job_id") val importJobId: String,
    @ColumnInfo(name = "source_row_key") val sourceRowKey: String,
    @ColumnInfo(name = "source_position") val sourcePosition: Int,
    @ColumnInfo(name = "row_sha256") val rowSha256: String,
    @ColumnInfo(name = "raw_title") val rawTitle: String,
    @ColumnInfo(name = "raw_artist") val rawArtist: String,
    @ColumnInfo(name = "raw_album") val rawAlbum: String?,
    @ColumnInfo(name = "raw_duration_ms") val rawDurationMs: Long?,
    @ColumnInfo(name = "raw_provenance_json") val rawProvenanceJson: String,
    @ColumnInfo(name = "content_uri") val contentUri: String?,
    @ColumnInfo(name = "persisted_uri_permission") val persistedUriPermission: Boolean,
    @ColumnInfo(name = "source_availability") val sourceAvailability: String,
    @ColumnInfo(name = "fingerprint_algorithm") val fingerprintAlgorithm: String?,
    @ColumnInfo(name = "fingerprint_version") val fingerprintVersion: String?,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String,
    @ColumnInfo(name = "workflow_state") val workflowState: String,
    @ColumnInfo(name = "selected_local_recording_id") val selectedLocalRecordingId: String?,
    @ColumnInfo(name = "latest_decision_id") val latestDecisionId: String?,
    @ColumnInfo(name = "last_error_code") val lastErrorCode: String?,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

/** Immutable local identity decision; review actions supersede rather than update history. */
@Entity(
    tableName = "match_decision",
    foreignKeys = [
        ForeignKey(
            entity = LocalImportEntryEntity::class,
            parentColumns = ["import_entry_id"],
            childColumns = ["import_entry_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
        ForeignKey(
            entity = RecordingProjectionEntity::class,
            parentColumns = ["local_recording_id"],
            childColumns = ["selected_local_recording_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
        ForeignKey(
            entity = LocalMatchDecisionEntity::class,
            parentColumns = ["decision_id"],
            childColumns = ["supersedes_decision_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["import_entry_id", "idempotency_key"], unique = true),
        Index(value = ["supersedes_decision_id"], unique = true),
        Index(value = ["selected_local_recording_id"]),
        Index(value = ["evidence_decision_id"]),
    ],
)
data class LocalMatchDecisionEntity(
    @PrimaryKey @ColumnInfo(name = "decision_id") val decisionId: String,
    @ColumnInfo(name = "import_entry_id") val importEntryId: String,
    @ColumnInfo(name = "decision_kind") val decisionKind: String,
    @ColumnInfo(name = "execution_mode") val executionMode: String,
    @ColumnInfo(name = "resolver_state") val resolverState: String,
    @ColumnInfo(name = "review_action") val reviewAction: String?,
    @ColumnInfo(name = "selected_local_recording_id") val selectedLocalRecordingId: String?,
    @ColumnInfo(name = "reviewed_candidate_id") val reviewedCandidateId: String?,
    @ColumnInfo(name = "supersedes_decision_id") val supersedesDecisionId: String?,
    @ColumnInfo(name = "evidence_decision_id") val evidenceDecisionId: String,
    @ColumnInfo(name = "candidate_count") val candidateCount: Int,
    @ColumnInfo(name = "top_confidence") val topConfidence: Double?,
    @ColumnInfo(name = "top_two_margin") val topTwoMargin: Double?,
    @ColumnInfo(name = "evidence_mode") val evidenceMode: String,
    @ColumnInfo(name = "matcher_version") val matcherVersion: String,
    @ColumnInfo(name = "fingerprint_algorithm") val fingerprintAlgorithm: String?,
    @ColumnInfo(name = "fingerprint_version") val fingerprintVersion: String?,
    @ColumnInfo(name = "explanation_json") val explanationJson: String,
    @ColumnInfo(name = "idempotency_key") val idempotencyKey: String,
    @ColumnInfo(name = "request_sha256") val requestSha256: String,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
)

/** Immutable bounded explanation for one candidate in an evaluation snapshot. */
@Entity(
    tableName = "match_candidate",
    foreignKeys = [
        ForeignKey(
            entity = LocalMatchDecisionEntity::class,
            parentColumns = ["decision_id"],
            childColumns = ["decision_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
        ForeignKey(
            entity = RecordingProjectionEntity::class,
            parentColumns = ["local_recording_id"],
            childColumns = ["local_recording_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["decision_id", "rank"], unique = true),
        Index(value = ["decision_id", "local_recording_id"], unique = true),
        Index(value = ["local_recording_id"]),
    ],
)
data class LocalMatchCandidateEntity(
    @PrimaryKey @ColumnInfo(name = "candidate_id") val candidateId: String,
    @ColumnInfo(name = "decision_id") val decisionId: String,
    @ColumnInfo(name = "local_recording_id") val localRecordingId: String,
    val rank: Int,
    @ColumnInfo(name = "raw_score") val rawScore: Double?,
    val confidence: Double?,
    @ColumnInfo(name = "evidence_tier") val evidenceTier: String,
    @ColumnInfo(name = "title_snapshot") val titleSnapshot: String,
    @ColumnInfo(name = "artist_snapshot") val artistSnapshot: String,
    @ColumnInfo(name = "version_snapshot") val versionSnapshot: String?,
    @ColumnInfo(name = "duration_ms") val durationMs: Long?,
    @ColumnInfo(name = "feature_evidence_json") val featureEvidenceJson: String,
    @ColumnInfo(name = "hard_conflicts_json") val hardConflictsJson: String,
    @ColumnInfo(name = "candidate_origins_json") val candidateOriginsJson: String,
    @ColumnInfo(name = "extractor_versions_json") val extractorVersionsJson: String,
    @ColumnInfo(name = "fingerprint_algorithm") val fingerprintAlgorithm: String?,
    @ColumnInfo(name = "fingerprint_version") val fingerprintVersion: String?,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
)

@Entity(tableName = "user_track_external_ref", primaryKeys = ["local_user_track_ref_id", "provider_key", "external_entity_type", "external_id", "market_scope"], foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.CASCADE)], indices = [Index(value = ["local_user_track_ref_id"])])
data class UserTrackExternalRefEntity(@ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, @ColumnInfo(name = "provider_key") val providerKey: String, @ColumnInfo(name = "external_entity_type") val externalEntityType: String, @ColumnInfo(name = "external_id") val externalId: String, @ColumnInfo(name = "market_scope") val marketScope: String, @ColumnInfo(name = "relation_role") val relationRole: String, @ColumnInfo(name = "first_seen_at_ms") val firstSeenAtMs: Long)

@Entity(tableName = "library_entry", foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["server_profile_id", "server_library_entry_id"], unique = true), Index(value = ["local_user_track_ref_id"], unique = true), Index(value = ["server_profile_id"])])
data class LibraryEntryEntity(@PrimaryKey @ColumnInfo(name = "local_library_entry_id") val localLibraryEntryId: String, @ColumnInfo(name = "server_library_entry_id") val serverLibraryEntryId: String?, @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, @ColumnInfo(name = "added_at_ms") val addedAtMs: Long, val source: String, @ColumnInfo(name = "availability_status") val availabilityStatus: String, @ColumnInfo(name = "sync_state") val syncState: String, @ColumnInfo(name = "server_row_version") val serverRowVersion: Long?, @ColumnInfo(name = "last_local_sequence") val lastLocalSequence: Long, @ColumnInfo(name = "removed_at_ms") val removedAtMs: Long?, @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long, @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped")

@Entity(tableName = "user_track_preference", foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["server_profile_id"])])
data class UserTrackPreferenceEntity(@PrimaryKey @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, val preference: String, val rating: Int?, @ColumnInfo(name = "excluded_from_taste") val excludedFromTaste: Boolean, @ColumnInfo(name = "sync_state") val syncState: String, @ColumnInfo(name = "last_local_sequence") val lastLocalSequence: Long, @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long, @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped")

@Entity(tableName = "playlist", indices = [Index(value = ["server_profile_id", "server_playlist_id"], unique = true), Index(value = ["server_profile_id"])])
data class PlaylistEntity(@PrimaryKey @ColumnInfo(name = "local_playlist_id") val localPlaylistId: String, @ColumnInfo(name = "server_playlist_id") val serverPlaylistId: String?, val name: String, val description: String?, val visibility: String, @ColumnInfo(name = "playlist_type") val playlistType: String, @ColumnInfo(name = "smart_rule_version") val smartRuleVersion: Long?, @ColumnInfo(name = "smart_rule_json") val smartRuleJson: String?, @ColumnInfo(name = "sync_state") val syncState: String, @ColumnInfo(name = "server_row_version") val serverRowVersion: Long?, @ColumnInfo(name = "last_local_sequence") val lastLocalSequence: Long, @ColumnInfo(name = "created_at_ms") val createdAtMs: Long, @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long, @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long?, @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped")

@Entity(tableName = "playlist_entry", foreignKeys = [ForeignKey(entity = PlaylistEntity::class, parentColumns = ["local_playlist_id"], childColumns = ["local_playlist_id"], onDelete = ForeignKey.RESTRICT), ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["server_profile_id", "server_playlist_entry_id"], unique = true), Index(value = ["local_playlist_id", "active_position_key"], unique = true), Index(value = ["local_user_track_ref_id"]), Index(value = ["server_profile_id"])])
data class PlaylistEntryEntity(@PrimaryKey @ColumnInfo(name = "local_playlist_entry_id") val localPlaylistEntryId: String, @ColumnInfo(name = "server_playlist_entry_id") val serverPlaylistEntryId: String?, @ColumnInfo(name = "local_playlist_id") val localPlaylistId: String, @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, @ColumnInfo(name = "position_key") val positionKey: String, @ColumnInfo(name = "active_position_key") val activePositionKey: String?, @ColumnInfo(name = "source_position") val sourcePosition: Long?, @ColumnInfo(name = "added_at_ms") val addedAtMs: Long, @ColumnInfo(name = "sync_state") val syncState: String, @ColumnInfo(name = "server_row_version") val serverRowVersion: Long?, @ColumnInfo(name = "last_local_sequence") val lastLocalSequence: Long, @ColumnInfo(name = "removed_at_ms") val removedAtMs: Long?, @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped")

@Entity(tableName = "local_audio_state", foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT), ForeignKey(entity = RecordingProjectionEntity::class, parentColumns = ["local_recording_id"], childColumns = ["local_recording_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["content_uri"], unique = true), Index(value = ["local_sha256"]), Index(value = ["local_user_track_ref_id", "status"]), Index(value = ["local_recording_id"]), Index(value = ["storage_class", "last_accessed_at_ms"])])
data class LocalAudioStateEntity(@PrimaryKey @ColumnInfo(name = "local_audio_state_id") val localAudioStateId: String, @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, @ColumnInfo(name = "local_recording_id") val localRecordingId: String?, @ColumnInfo(name = "server_audio_variant_id") val serverAudioVariantId: String?, @ColumnInfo(name = "content_uri") val contentUri: String, @ColumnInfo(name = "persisted_uri_permission") val persistedUriPermission: Boolean, @ColumnInfo(name = "local_sha256") val localSha256: ByteArray?, @ColumnInfo(name = "fingerprint_algorithm") val fingerprintAlgorithm: String?, @ColumnInfo(name = "fingerprint_version") val fingerprintVersion: String?, @ColumnInfo(name = "fingerprint_payload") val fingerprintPayload: ByteArray?, val codec: String?, val container: String?, @ColumnInfo(name = "bitrate_bps") val bitrateBps: Long?, @ColumnInfo(name = "sample_rate_hz") val sampleRateHz: Long?, val channels: Int?, @ColumnInfo(name = "duration_ms") val durationMs: Long?, val status: String, @ColumnInfo(name = "storage_class") val storageClass: String, @ColumnInfo(name = "byte_size") val byteSize: Long?, @ColumnInfo(name = "last_accessed_at_ms") val lastAccessedAtMs: Long?, @ColumnInfo(name = "last_verified_at_ms") val lastVerifiedAtMs: Long?, @ColumnInfo(name = "created_at_ms") val createdAtMs: Long, @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long) {
    init {
        val parsed = contentUri.toUri()
        require(parsed.scheme == ContentResolver.SCHEME_CONTENT && !parsed.authority.isNullOrBlank()) {
            "Local audio must use a MediaStore/SAF content URI."
        }
    }
}

@Entity(tableName = "download_intent", foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["media3_download_id"], unique = true), Index(value = ["local_user_track_ref_id"])])
data class DownloadIntentEntity(
    @PrimaryKey @ColumnInfo(name = "download_intent_id") val downloadIntentId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String,
    @ColumnInfo(name = "server_audio_variant_id") val serverAudioVariantId: String?,
    @ColumnInfo(name = "media3_download_id") val media3DownloadId: String?,
    @ColumnInfo(name = "desired_storage_class") val desiredStorageClass: String,
    @ColumnInfo(name = "quality_policy") val qualityPolicy: String,
    @ColumnInfo(name = "source_policy") val sourcePolicy: String,
    val state: String,
    @ColumnInfo(name = "failure_code") val failureCode: String?,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
    @ColumnInfo(name = "completed_at_ms") val completedAtMs: Long?,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String? = null,
    @ColumnInfo(name = "last_accessed_at_ms") val lastAccessedAtMs: Long? = null,
)

@Entity(tableName = "queue_snapshot", indices = [Index(value = ["active_slot"], unique = true)])
data class QueueSnapshotEntity(
    @PrimaryKey @ColumnInfo(name = "queue_snapshot_id") val queueSnapshotId: String,
    @ColumnInfo(name = "queue_type") val queueType: String,
    @ColumnInfo(name = "source_context_id") val sourceContextId: String?,
    @ColumnInfo(name = "current_entry_id") val currentEntryId: String?,
    @ColumnInfo(name = "current_position_ms") val currentPositionMs: Long,
    @ColumnInfo(name = "shuffle_mode") val shuffleMode: String,
    @ColumnInfo(name = "repeat_mode") val repeatMode: String,
    val seed: Long?,
    @ColumnInfo(name = "generation_version") val generationVersion: String?,
    @ColumnInfo(name = "is_active") val isActive: Boolean,
    @ColumnInfo(name = "active_slot") val activeSlot: String?,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String? = null,
    @ColumnInfo(name = "listening_context", defaultValue = "'GENERAL'") val listeningContext: String = "GENERAL",
    @ColumnInfo(name = "active_listening_event_id") val activeListeningEventId: String? = null,
    @ColumnInfo(name = "active_session_started_at_ms") val activeSessionStartedAtMs: Long? = null,
    @ColumnInfo(name = "active_session_start_position_ms") val activeSessionStartPositionMs: Long? = null,
    @ColumnInfo(name = "active_session_observed_played_ms") val activeSessionObservedPlayedMs: Long? = null,
    @ColumnInfo(name = "active_session_user_id") val activeSessionUserId: String? = null,
    @ColumnInfo(name = "active_session_device_id") val activeSessionDeviceId: String? = null,
    @ColumnInfo(name = "active_session_server_profile_id") val activeSessionServerProfileId: String? = null,
)

@Entity(tableName = "queue_entry", foreignKeys = [ForeignKey(entity = QueueSnapshotEntity::class, parentColumns = ["queue_snapshot_id"], childColumns = ["queue_snapshot_id"], onDelete = ForeignKey.CASCADE), ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["queue_snapshot_id", "position"], unique = true), Index(value = ["local_user_track_ref_id"])])
data class QueueEntryEntity(
    @PrimaryKey @ColumnInfo(name = "queue_entry_id") val queueEntryId: String,
    @ColumnInfo(name = "queue_snapshot_id") val queueSnapshotId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String,
    val position: Long,
    @ColumnInfo(name = "source_origin") val sourceOrigin: String,
    @ColumnInfo(name = "recommendation_request_id") val recommendationRequestId: String?,
    @ColumnInfo(name = "source_audio_policy") val sourceAudioPolicy: String,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "recommendation_attribution_json") val recommendationAttributionJson: String? = null,
)

/** No bearer token, URL or media bytes are persisted in Wave state. */
@Entity(tableName = "wave_room", indices = [Index(value = ["server_profile_id"])])
data class WaveRoomEntity(
    @PrimaryKey @ColumnInfo(name = "room_id") val roomId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "room_epoch") val roomEpoch: String,
    @ColumnInfo(name = "queue_version") val queueVersion: Long,
    @ColumnInfo(name = "role") val role: String,
    @ColumnInfo(name = "state") val state: String,
    @ColumnInfo(name = "last_sequence") val lastSequence: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

@Entity(tableName = "wave_preflight", primaryKeys = ["room_id", "queue_entry_id"])
data class WavePreflightEntity(
    @ColumnInfo(name = "room_id") val roomId: String,
    @ColumnInfo(name = "queue_entry_id") val queueEntryId: String,
    @ColumnInfo(name = "server_recording_id") val serverRecordingId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String?,
    @ColumnInfo(name = "queue_version") val queueVersion: Long,
    @ColumnInfo(name = "availability") val availability: String,
    @ColumnInfo(name = "final_ready") val finalReady: Boolean,
    @ColumnInfo(name = "checked_at_ms") val checkedAtMs: Long,
)

/** Authoritative REST snapshot projection. It contains no URL, token or clock estimate. */
@Entity(tableName = "wave_queue_projection", primaryKeys = ["room_id", "sequence", "position"])
data class WaveQueueProjectionEntity(
    @ColumnInfo(name = "room_id") val roomId: String,
    @ColumnInfo(name = "sequence") val sequence: Long,
    @ColumnInfo(name = "position") val position: Long,
    @ColumnInfo(name = "queue_entry_id") val queueEntryId: String,
    @ColumnInfo(name = "server_recording_id") val serverRecordingId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String?,
    @ColumnInfo(name = "ready") val ready: Boolean,
)

@Entity(tableName = "listening_event", foreignKeys = [ForeignKey(entity = UserTrackRefEntity::class, parentColumns = ["local_user_track_ref_id"], childColumns = ["local_user_track_ref_id"], onDelete = ForeignKey.RESTRICT)], indices = [Index(value = ["started_at_ms"]), Index(value = ["sync_state", "created_at_ms"]), Index(value = ["local_user_track_ref_id"]), Index(value = ["server_profile_id"])])
data class ListeningEventEntity(
    @PrimaryKey @ColumnInfo(name = "listening_event_id") val listeningEventId: String,
    @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String,
    @ColumnInfo(name = "server_recording_id") val serverRecordingId: String?,
    @ColumnInfo(name = "started_at_ms") val startedAtMs: Long,
    @ColumnInfo(name = "played_ms") val playedMs: Long,
    @ColumnInfo(name = "track_duration_ms") val trackDurationMs: Long?,
    @ColumnInfo(name = "completion_ratio") val completionRatio: Double?,
    @ColumnInfo(name = "event_origin") val eventOrigin: String,
    val context: String,
    @ColumnInfo(name = "recommendation_request_id") val recommendationRequestId: String?,
    @ColumnInfo(name = "explicit_feedback") val explicitFeedback: String,
    @ColumnInfo(name = "excluded_from_taste") val excludedFromTaste: Boolean,
    @ColumnInfo(name = "sync_state") val syncState: String,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "recommendation_attribution_json") val recommendationAttributionJson: String? = null,
    @ColumnInfo(name = "session_start_position_ms") val sessionStartPositionMs: Long? = null,
    @ColumnInfo(name = "session_end_position_ms") val sessionEndPositionMs: Long? = null,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String = "legacy-unscoped",
)

@Entity(
    tableName = "journal_lineage",
    indices = [
        Index(value = ["device_id"], unique = true),
        Index(value = ["journal_epoch"], unique = true),
        Index(value = ["user_id", "device_id", "journal_epoch"], unique = true),
        Index(value = ["lineage_id", "user_id", "device_id"], unique = true),
        Index(value = ["lineage_id", "device_id", "journal_epoch"], unique = true),
    ],
)
data class JournalLineageEntity(
    @PrimaryKey @ColumnInfo(name = "lineage_id") val lineageId: String,
    @ColumnInfo(name = "user_id") val userId: String,
    @ColumnInfo(name = "device_id") val deviceId: String,
    @ColumnInfo(name = "journal_epoch") val journalEpoch: String,
    @ColumnInfo(name = "next_device_sequence") val nextDeviceSequence: Long = 1,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
)

@Entity(
    tableName = "offline_journal_event",
    foreignKeys = [
        ForeignKey(
            entity = JournalLineageEntity::class,
            parentColumns = ["lineage_id", "user_id", "device_id"],
            childColumns = ["journal_lineage_id", "user_id", "device_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["journal_lineage_id", "device_sequence"], unique = true),
        Index(value = ["journal_lineage_id", "idempotency_key"], unique = true),
        Index(value = ["journal_lineage_id", "user_id", "device_id"]),
        Index(value = ["journal_lineage_id", "state", "next_attempt_at_ms", "device_sequence"]),
        Index(value = ["journal_lineage_id", "aggregate_type", "aggregate_local_id", "device_sequence"]),
    ],
)
data class OfflineJournalEventEntity(
    @PrimaryKey @ColumnInfo(name = "event_id") val eventId: String,
    @ColumnInfo(name = "journal_lineage_id") val journalLineageId: String,
    @ColumnInfo(name = "idempotency_key") val idempotencyKey: String,
    @ColumnInfo(name = "user_id") val userId: String,
    @ColumnInfo(name = "device_id") val deviceId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "device_sequence") val deviceSequence: Long,
    @ColumnInfo(name = "event_type") val eventType: String,
    @ColumnInfo(name = "schema_version") val schemaVersion: Int,
    @ColumnInfo(name = "aggregate_type") val aggregateType: String,
    @ColumnInfo(name = "aggregate_local_id") val aggregateLocalId: String,
    @ColumnInfo(name = "aggregate_server_id") val aggregateServerId: String?,
    @ColumnInfo(name = "base_server_row_version") val baseServerRowVersion: Long?,
    @ColumnInfo(name = "payload_json") val payloadJson: String,
    @ColumnInfo(name = "request_hash") val requestHash: ByteArray,
    @ColumnInfo(name = "occurred_at_ms") val occurredAtMs: Long,
    val state: String,
    @ColumnInfo(name = "attempt_count") val attemptCount: Int,
    @ColumnInfo(name = "next_attempt_at_ms") val nextAttemptAtMs: Long?,
    @ColumnInfo(name = "lease_token") val leaseToken: String?,
    @ColumnInfo(name = "lease_expires_at_ms") val leaseExpiresAtMs: Long?,
    @ColumnInfo(name = "last_error_code") val lastErrorCode: String?,
    @ColumnInfo(name = "acked_at_ms") val ackedAtMs: Long?,
)

@Entity(
    tableName = "local_mutation_outbox",
    foreignKeys = [
        ForeignKey(
            entity = OfflineJournalEventEntity::class,
            parentColumns = ["event_id"],
            childColumns = ["materialized_event_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["materialized_event_id"], unique = true),
        Index(value = ["materialization_state", "occurred_at_ms"]),
        Index(value = ["aggregate_type", "aggregate_local_id", "occurred_at_ms"]),
    ],
)
data class LocalMutationOutboxEntity(
    @PrimaryKey @ColumnInfo(name = "local_change_id") val localChangeId: String,
    @ColumnInfo(name = "event_type") val eventType: String,
    @ColumnInfo(name = "schema_version") val schemaVersion: Int,
    @ColumnInfo(name = "aggregate_type") val aggregateType: String,
    @ColumnInfo(name = "aggregate_local_id") val aggregateLocalId: String,
    @ColumnInfo(name = "payload_json") val payloadJson: String,
    @ColumnInfo(name = "occurred_at_ms") val occurredAtMs: Long,
    @ColumnInfo(name = "materialization_state") val materializationState: String,
    @ColumnInfo(name = "materialized_event_id") val materializedEventId: String? = null,
    @ColumnInfo(name = "materialized_at_ms") val materializedAtMs: Long? = null,
)

@Entity(
    tableName = "sync_cursor",
    foreignKeys = [
        ForeignKey(
            entity = JournalLineageEntity::class,
            parentColumns = ["lineage_id", "device_id", "journal_epoch"],
            childColumns = ["journal_lineage_id", "device_id", "journal_epoch"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [Index(value = ["journal_lineage_id", "device_id", "journal_epoch"])],
)
data class SyncCursorEntity(
    @PrimaryKey @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "journal_lineage_id") val journalLineageId: String,
    @ColumnInfo(name = "device_id") val deviceId: String,
    @ColumnInfo(name = "journal_epoch") val journalEpoch: String,
    @ColumnInfo(name = "opaque_cursor") val opaqueCursor: String?,
    @ColumnInfo(name = "last_pulled_server_sequence") val lastPulledServerSequence: Long,
    @ColumnInfo(name = "last_acked_device_sequence") val lastAckedDeviceSequence: Long,
    @ColumnInfo(name = "bootstrap_snapshot_id") val bootstrapSnapshotId: String?,
    @ColumnInfo(name = "bootstrap_state") val bootstrapState: String,
    @ColumnInfo(name = "last_sync_at_ms") val lastSyncAtMs: Long?,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

/** Profile-scoped, redacted runtime diagnostics. Never stores endpoint, credential, or payload. */
@Entity(tableName = "sync_runtime_status")
data class SyncRuntimeStatusEntity(
    @PrimaryKey @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "last_error_code") val lastErrorCode: String?,
    @ColumnInfo(name = "last_attempt_at_ms") val lastAttemptAtMs: Long?,
    @ColumnInfo(name = "last_success_at_ms") val lastSuccessAtMs: Long?,
)

/** Snapshot paging state is intentionally separate from the opaque incremental pull cursor. */
@Entity(tableName = "sync_bootstrap_state")
data class SyncBootstrapStateEntity(
    @PrimaryKey @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "snapshot_id") val snapshotId: String?,
    @ColumnInfo(name = "page_token") val pageToken: String?,
    @ColumnInfo(name = "final_cursor") val finalCursor: String?,
    @ColumnInfo(name = "state") val state: String,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

/** Immutable bounded recommendation interaction evidence retained for later recommendation projection. */
@Entity(tableName = "recommendation_interaction_fact", primaryKeys = ["server_profile_id", "event_id"], indices = [Index(value = ["server_profile_id", "event_type", "created_at_ms"])])
data class RecommendationInteractionFactEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "event_id") val eventId: String,
    @ColumnInfo(name = "event_type") val eventType: String,
    @ColumnInfo(name = "payload_json") val payloadJson: String,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
)

@Entity(tableName = "tombstone", indices = [Index(value = ["server_profile_id", "aggregate_type", "aggregate_local_id"], unique = true)])
data class TombstoneEntity(@PrimaryKey @ColumnInfo(name = "tombstone_id") val tombstoneId: String, @ColumnInfo(name = "server_profile_id") val serverProfileId: String, @ColumnInfo(name = "aggregate_type") val aggregateType: String, @ColumnInfo(name = "aggregate_local_id") val aggregateLocalId: String, @ColumnInfo(name = "aggregate_server_id") val aggregateServerId: String?, @ColumnInfo(name = "deleted_by_event_id") val deletedByEventId: String, @ColumnInfo(name = "deleted_at_ms") val deletedAtMs: Long, @ColumnInfo(name = "retain_until_ms") val retainUntilMs: Long, @ColumnInfo(name = "server_acked") val serverAcked: Boolean)

@Entity(tableName = "sync_conflict", indices = [Index(value = ["server_profile_id", "aggregate_type", "aggregate_local_id"]), Index(value = ["server_profile_id", "status", "created_at_ms"])])
data class SyncConflictEntity(@PrimaryKey @ColumnInfo(name = "sync_conflict_id") val syncConflictId: String, @ColumnInfo(name = "server_profile_id") val serverProfileId: String, @ColumnInfo(name = "aggregate_type") val aggregateType: String, @ColumnInfo(name = "aggregate_local_id") val aggregateLocalId: String, @ColumnInfo(name = "local_event_id") val localEventId: String?, @ColumnInfo(name = "server_event_id") val serverEventId: String?, @ColumnInfo(name = "reason_code") val reasonCode: String, @ColumnInfo(name = "local_snapshot_json") val localSnapshotJson: String?, @ColumnInfo(name = "server_snapshot_json") val serverSnapshotJson: String?, val status: String, @ColumnInfo(name = "resolution_json") val resolutionJson: String?, @ColumnInfo(name = "created_at_ms") val createdAtMs: Long, @ColumnInfo(name = "resolved_at_ms") val resolvedAtMs: Long?)

@Entity(tableName = "recommendation_pack", indices = [Index(value = ["server_profile_id", "owner_user_id", "expires_at_ms"])])
data class RecommendationPackEntity(
    @PrimaryKey @ColumnInfo(name = "offline_pack_id") val offlinePackId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    /** Null only for a pre-v9 legacy row, which the verifier rejects for every owner. */
    @ColumnInfo(name = "owner_user_id") val ownerUserId: String?,
    @ColumnInfo(name = "catalog_snapshot") val catalogSnapshot: Long,
    @ColumnInfo(name = "model_bundle_version") val modelBundleVersion: String,
    @ColumnInfo(name = "payload_version") val payloadVersion: Int,
    @ColumnInfo(name = "payload_encoding") val payloadEncoding: String,
    val payload: ByteArray,
    @ColumnInfo(name = "payload_sha256") val payloadSha256: ByteArray,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "expires_at_ms") val expiresAtMs: Long,
)

/**
 * Durable semantic idempotency key for one actual recommendation presentation.
 *
 * The composite primary key is intentionally the P04/P11 owner-scoped semantic tuple. A new UUID
 * therefore cannot replace or duplicate the first stable impression event for that tuple.
 */
@Entity(
    tableName = "recommendation_presentation",
    primaryKeys = [
        "server_profile_id",
        "owner_user_id",
        "presentation_id",
        "recommendation_request_id",
        "source_rank",
    ],
    indices = [
        Index(value = ["impression_event_id"], unique = true),
        Index(value = ["server_profile_id", "owner_user_id", "offline_pack_id"]),
    ],
)
data class RecommendationPresentationEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "owner_user_id") val ownerUserId: String,
    @ColumnInfo(name = "presentation_id") val presentationId: String,
    @ColumnInfo(name = "recommendation_request_id") val recommendationRequestId: String,
    @ColumnInfo(name = "source_rank") val sourceRank: Int,
    @ColumnInfo(name = "impression_event_id") val impressionEventId: String,
    @ColumnInfo(name = "recording_id") val recordingId: String,
    @ColumnInfo(name = "offline_pack_id") val offlinePackId: String?,
    val source: String,
    val surface: String,
    @ColumnInfo(name = "section_key") val sectionKey: String?,
    @ColumnInfo(name = "display_position") val displayPosition: Int,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
)

/**
 * Bounded, owner-scoped status projection for a server import.  The parsed import bytes and
 * per-entry payload remain server-side; Android keeps only enough state to resume its UI.
 */
@Entity(
    tableName = "remote_import_job_projection",
    primaryKeys = ["server_profile_id", "import_job_id"],
    indices = [Index(value = ["server_profile_id", "updated_at_ms"])],
)
data class RemoteImportJobProjectionEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "import_job_id") val importJobId: String,
    @ColumnInfo(name = "delivery_job_id") val deliveryJobId: String?,
    val state: String,
    @ColumnInfo(name = "progress_current") val progressCurrent: Int,
    @ColumnInfo(name = "progress_total") val progressTotal: Int,
    @ColumnInfo(name = "review_required_count") val reviewRequiredCount: Int,
    @ColumnInfo(name = "resolved_count") val resolvedCount: Int,
    @ColumnInfo(name = "no_match_count") val noMatchCount: Int,
    @ColumnInfo(name = "unresolved_count") val unresolvedCount: Int,
    @ColumnInfo(name = "failed_count") val failedCount: Int,
    @ColumnInfo(name = "last_error_code") val lastErrorCode: String?,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

/**
 * Local resume intent for a Vault upload. It deliberately refers to an existing local-audio row
 * instead of retaining a URI/path, upload URL, request body, or credentials.
 */
@Entity(
    tableName = "vault_upload_intent",
    foreignKeys = [
        ForeignKey(
            entity = LocalAudioStateEntity::class,
            parentColumns = ["local_audio_state_id"],
            childColumns = ["local_audio_state_id"],
            onDelete = ForeignKey.RESTRICT,
        ),
    ],
    indices = [
        Index(value = ["server_profile_id", "state", "updated_at_ms"]),
        Index(value = ["local_audio_state_id"]),
        Index(value = ["server_profile_id", "server_upload_id"], unique = true),
    ],
)
data class VaultUploadIntentEntity(
    @PrimaryKey @ColumnInfo(name = "upload_intent_id") val uploadIntentId: String,
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "local_audio_state_id") val localAudioStateId: String,
    @ColumnInfo(name = "server_recording_id") val serverRecordingId: String,
    @ColumnInfo(name = "declared_sha256") val declaredSha256: String,
    @ColumnInfo(name = "expected_size") val expectedSize: Long,
    @ColumnInfo(name = "server_upload_id") val serverUploadId: String?,
    @ColumnInfo(name = "remote_offset") val remoteOffset: Long,
    val state: String,
    @ColumnInfo(name = "attempt_count") val attemptCount: Int,
    @ColumnInfo(name = "last_error_code") val lastErrorCode: String?,
    @ColumnInfo(name = "created_at_ms") val createdAtMs: Long,
    @ColumnInfo(name = "updated_at_ms") val updatedAtMs: Long,
)

/**
 * Response metadata for a bounded server recommendation request. Recommendation items stay in
 * the existing verified offline-pack boundary; this table never stores a response body.
 */
@Entity(
    tableName = "recommendation_response_snapshot",
    primaryKeys = ["server_profile_id", "recommendation_request_id"],
    indices = [Index(value = ["server_profile_id", "received_at_ms"])],
)
data class RecommendationResponseSnapshotEntity(
    @ColumnInfo(name = "server_profile_id") val serverProfileId: String,
    @ColumnInfo(name = "recommendation_request_id") val recommendationRequestId: String,
    val replay: String,
    @ColumnInfo(name = "item_count") val itemCount: Int,
    @ColumnInfo(name = "response_sha256") val responseSha256: String,
    @ColumnInfo(name = "received_at_ms") val receivedAtMs: Long,
)

@Entity(tableName = "track_search_content", indices = [Index(value = ["local_user_track_ref_id"], unique = true)])
data class TrackSearchContentEntity(@PrimaryKey(autoGenerate = true) @ColumnInfo(name = "rowid") val rowId: Long = 0, @ColumnInfo(name = "local_user_track_ref_id") val localUserTrackRefId: String, val title: String?, val artist: String?, val album: String?, val aliases: String?, val transliterations: String?)

@Fts5(contentEntity = TrackSearchContentEntity::class, tokenizer = FtsOptions.TOKENIZER_UNICODE61)
@Entity(tableName = "track_search_fts")
data class TrackSearchFtsEntity(@PrimaryKey @ColumnInfo(name = "rowid") val rowId: Long, val title: String, val artist: String, val album: String, val aliases: String, val transliterations: String)

@Entity(tableName = "applied_server_event", primaryKeys = ["server_profile_id", "server_event_id"], indices = [Index(value = ["server_profile_id", "server_sequence"], unique = true)])
data class AppliedServerEventEntity(@ColumnInfo(name = "server_profile_id") val serverProfileId: String, @ColumnInfo(name = "server_event_id") val serverEventId: String, @ColumnInfo(name = "server_sequence") val serverSequence: Long, @ColumnInfo(name = "applied_at_ms") val appliedAtMs: Long)

@Entity(tableName = "deferred_server_event", primaryKeys = ["server_profile_id", "server_event_id"], indices = [Index(value = ["server_profile_id", "server_sequence"], unique = true)])
data class DeferredServerEventEntity(@ColumnInfo(name = "server_profile_id") val serverProfileId: String, @ColumnInfo(name = "server_event_id") val serverEventId: String, @ColumnInfo(name = "server_sequence") val serverSequence: Long, @ColumnInfo(name = "event_type") val eventType: String, @ColumnInfo(name = "schema_version") val schemaVersion: Int, @ColumnInfo(name = "payload_json") val payloadJson: String, @ColumnInfo(name = "deferred_at_ms") val deferredAtMs: Long, @ColumnInfo(name = "reason_code") val reasonCode: String)

@Entity(tableName = "aggregate_redirect", primaryKeys = ["server_profile_id", "aggregate_type", "alias_local_id"], indices = [Index(value = ["server_profile_id", "aggregate_type", "alias_server_id"], unique = true), Index(value = ["server_profile_id", "aggregate_type", "canonical_local_id"])])
data class AggregateRedirectEntity(@ColumnInfo(name = "server_profile_id") val serverProfileId: String, @ColumnInfo(name = "aggregate_type") val aggregateType: String, @ColumnInfo(name = "alias_local_id") val aliasLocalId: String, @ColumnInfo(name = "alias_server_id") val aliasServerId: String?, @ColumnInfo(name = "canonical_local_id") val canonicalLocalId: String, @ColumnInfo(name = "canonical_server_id") val canonicalServerId: String?, @ColumnInfo(name = "created_by_server_sequence") val createdByServerSequence: Long, @ColumnInfo(name = "created_at_ms") val createdAtMs: Long)
