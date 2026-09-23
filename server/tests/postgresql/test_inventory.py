"""Exact PostgreSQL object inventory and reference-drift tests."""

from __future__ import annotations

from .conftest import REFERENCE_DDL_PATH, DatabaseHarness
from .schema_contract import (
    EXPECTED_EXPLICIT_INDEX_COUNT,
    EXPECTED_FUNCTION_COUNT,
    EXPECTED_TABLE_COUNT,
    EXPECTED_TRIGGER_COUNT,
    parse_reference_names,
    snapshot_schema,
)

A1B_TABLES = frozenset(
    {
        ("discovery", "bulk_operation"),
        ("discovery", "bulk_operation_item"),
        ("discovery", "candidate"),
        ("discovery", "source_authorization"),
        ("importing", "web_import_operation_receipt"),
    }
)
A1B_INDEXES = frozenset(
    {
        "ix_bulk_operation_item_candidate",
        "ix_bulk_operation_owner_time",
        "ix_discovery_candidate_owner_state",
        "ix_source_authorization_owner_expiry",
        "uq_source_authorization_current_scope",
    }
)
A1C_TABLES = frozenset(
    {
        ("discovery", "artist_policy"),
        ("discovery", "artist_policy_revision"),
        ("discovery", "run"),
        ("discovery", "run_page"),
        ("discovery", "run_candidate"),
        ("discovery", "acquisition_attempt"),
        ("discovery", "candidate_action_receipt"),
    }
)
A1C_INDEXES = frozenset(
    {
        "ix_artist_policy_due",
        "ix_discovery_run_policy_slot",
        "ix_acquisition_attempt_candidate",
        "ix_candidate_action_receipt_candidate",
        "uq_acquisition_attempt_active_candidate",
    }
)
A1C_FUNCTIONS = frozenset(
    {"reject_artist_policy_binding_mutation", "reject_artist_policy_revision_mutation"}
)
A1C_TRIGGERS = frozenset(
    {"trg_artist_policy_binding_immutable", "trg_artist_policy_revision_immutable"}
)
S1B_TABLES = frozenset(
    {
        ("account", "device_admission"),
        ("account", "device_admission_nonce"),
        ("account", "device_admission_exchange_receipt"),
        ("account", "device_admission_rate_window"),
        ("account", "device_admission_web_operation_receipt"),
        ("account", "device_key_block"),
        ("account", "trusted_device_key"),
        ("account", "trusted_device_reenrollment_challenge"),
    }
)
S1B_INDEXES = frozenset(
    {
        "ix_device_admission_cleanup",
        "ix_device_admission_poll_expiry",
        "uq_device_admission_locator",
        "uq_device_admission_pending_key",
        "ix_device_key_block_active",
        "ix_trusted_reenrollment_challenge_expiry",
        "ix_device_admission_receipt_expiry",
        "ix_device_admission_web_operation_receipt_expiry",
        "ix_device_admission_rate_window_expiry",
    }
)
S1C_TABLES = frozenset(
    {
        ("social", "friend_request"),
        ("social", "friendship"),
        ("social", "user_block"),
        ("social", "presence_settings"),
        ("social", "presence_heartbeat"),
        ("social", "friend_room_invitation"),
        ("social", "operation_receipt"),
        ("social", "rate_window"),
    }
)
S1C_INDEXES = frozenset(
    {
        "uq_social_pending_friend_request",
        "ix_social_friend_request_target",
        "ix_social_user_block_active",
        "ix_social_presence_fresh",
        "ix_social_room_invitation_target",
        "uq_social_pending_room_target",
        "ix_social_operation_receipt_expiry",
        "ix_social_rate_window_expiry",
    }
)
S1C_TRIGGERS = frozenset({"trg_social_account_retire"})
S2_TABLES = frozenset({("social", "profile_statistics_settings")})
S1D_TABLES = frozenset(
    {
        ("social", "guest_invitation"),
        ("social", "guest_session"),
        ("social", "guest_operation_receipt"),
        ("social", "guest_preflight"),
        ("social", "guest_timing_report"),
        ("social", "guest_rate_window"),
    }
)
S1D_INDEXES = frozenset(
    {
        "ix_social_guest_invitation_room_state",
        "ix_social_guest_invitation_expiry",
        "ix_social_guest_session_room_state",
        "ix_social_guest_session_expiry",
        "ix_social_guest_operation_expiry",
        "ix_social_guest_preflight_expiry",
        "ix_social_guest_timing_reported",
        "ix_social_guest_rate_expiry",
    }
)
S1D_TRIGGERS = frozenset(
    {
        "trg_social_guest_invitation_retire",
        "trg_social_guest_account_retire",
        "trg_social_guest_device_retire",
        "trg_social_guest_user_session_retire",
    }
)
PA2_TABLES = frozenset(
    {
        ("account", "account_invitation"),
        ("account", "account_registration_receipt"),
        ("account", "account_provisioning_link"),
        ("account", "account_provisioning_operation_receipt"),
        ("account", "account_provisioning_rate_window"),
    }
)
PA2_INDEXES = frozenset(
    {
        "ix_account_invitation_expiry",
        "ix_account_registration_receipt_expiry",
        "ix_account_provisioning_rate_expiry",
    }
)
R1B_TABLES = frozenset(
    {
        ("ml", "recommendation_temporal_event"),
        ("ml", "recommendation_adaptive_profile"),
        ("ml", "recommendation_temporal_snapshot"),
    }
)
R1B_INDEXES = frozenset(
    {
        "ix_recommendation_temporal_event_owner_watermark",
        "ix_recommendation_temporal_event_retention",
        "ix_recommendation_adaptive_profile_owner_cutoff",
        "ix_recommendation_temporal_snapshot_owner_retention",
    }
)
R1B_FUNCTIONS = frozenset(
    {
        "protect_sona_shadow_binding",
        "protect_recommendation_temporal_event",
        "protect_recommendation_adaptive_profile",
        "protect_recommendation_temporal_snapshot",
    }
)
R1B_TRIGGERS = frozenset(
    {
        "tr_recommendation_request_sona_shadow_immutable",
        "tr_recommendation_temporal_event_immutable",
        "tr_recommendation_adaptive_profile_immutable",
        "tr_recommendation_temporal_snapshot_immutable",
    }
)
POST_0030_TABLES = frozenset(
    {
        ("account", "account_deletion_hold"),
        ("account", "account_deletion_request"),
        ("account", "account_purge_receipt"),
        ("account", "account_quota_override"),
        ("account", "account_recovery_credential"),
        ("account", "account_recovery_operation"),
        ("account", "internal_io_policy"),
        ("account", "quota_operation_receipt"),
        ("account", "resource_admission"),
        ("account", "resource_grant_cursor"),
        ("account", "resource_io_execution"),
        ("account", "resource_io_permit"),
        ("account", "resource_quota_policy"),
        ("account", "self_device_pairing"),
        ("account", "self_pairing_command"),
        ("account", "self_pairing_rate"),
        ("account", "training_consent"),
        ("account", "training_consent_operation"),
        ("account", "web_passkey"),
        ("account", "web_passkey_ceremony"),
        ("account", "web_passkey_revocation"),
        ("discovery", "internet_acquisition"),
        ("discovery", "internet_search"),
        ("library", "metadata_artwork"),
        ("library", "metadata_execution"),
        ("library", "metadata_provider_gate"),
        ("library", "track_metadata"),
        ("library", "track_metadata_revision"),
        ("ml", "training_checkpoint"),
        ("ml", "training_cleanup_claim"),
        ("ml", "training_execution"),
        ("ml", "training_participant"),
        ("ml", "training_publication_revocation"),
        ("ml", "training_run"),
        ("vault", "ingest_cleanup_claim"),
        ("vault", "ingest_cleanup_execution"),
        ("vault", "ingest_execution"),
        ("vault", "orphan_object_claim"),
        ("vault", "provider_maintenance"),
        ("vault", "provider_staging"),
        ("vault", "upload_cleanup_claim"),
    }
)
POST_0030_INDEXES = frozenset(
    {
        "internet_search_owner_time",
        "ix_admission_account_state",
        "ix_admission_lease_expiry",
        "ix_admission_waiting",
        "ix_deletion_request_deadline",
        "ix_deletion_request_pending_user",
        "ix_ingest_cleanup_pending",
        "ix_io_execution_owner",
        "ix_io_execution_unclosed",
        "ix_io_execution_writer",
        "ix_io_permit_expiry",
        "ix_io_permit_operation",
        "ix_provider_staging_cleanup",
        "ix_provider_staging_scratch_pending",
        "ix_provider_staging_target",
        "ix_quota_receipt_created",
        "ix_recovery_operation_expiry",
        "ix_recovery_operation_user",
        "ix_self_pairing_command_ceremony",
        "ix_self_pairing_expiry",
        "ix_self_pairing_rate_expiry",
        "ix_self_pairing_user_expiry",
        "ix_training_participant_owner",
        "ix_upload_cleanup_pending",
        "ix_web_passkey_ceremony_expiry",
        "ix_web_passkey_ceremony_user",
        "ix_web_passkey_user",
        "ix_web_session_passkey",
        "uq_ingest_cleanup_execution_claim",
        "uq_ingest_execution_staging",
        "uq_ingest_execution_upload",
        "uq_metadata_execution_ref",
        "uq_orphan_object_claim_active",
        "uq_provider_maintenance_active",
        "uq_training_execution_run_open",
    }
)
POST_0030_FUNCTIONS = frozenset(
    {
        "admit_internal_io",
        "advance_training_cleanup_claim",
        "authorize_training_execution_transition",
        "exclude_ingest_cleanup_writer",
        "guard_catalog_actor_erasure",
        "guard_purge_receipt",
        "lock_training_execution_identity",
        "privacy_entity_allowed",
        "privacy_owner_allowed",
        "privacy_request_for_owner",
        "protect_acquisition_authority",
        "protect_finalized_ingest_upload",
        "protect_ingest_cleanup_claim",
        "protect_ingest_cleanup_execution",
        "protect_ingest_execution",
        "protect_ingest_upload",
        "protect_internal_io_workload",
        "protect_internet_ingest_lineage",
        "protect_metadata_evidence",
        "protect_metadata_execution",
        "protect_metadata_provider_gate",
        "protect_music_snapshot",
        "protect_orphan_object_claim",
        "protect_provider_maintenance",
        "protect_provider_scratch",
        "protect_provider_staging",
        "protect_training_execution",
        "protect_training_publication_seal",
        "protect_upload_cleanup_claim",
        "protect_upload_cleanup_owner",
        "protect_upload_maintenance_target",
        "queue_ingest_cleanup",
    }
)
POST_0030_TRIGGERS = frozenset(
    {
        "a_internal_io_admission",
        "a_training_execution_identity",
        "acquisition_attempt_authority_guard",
        "advance_training_cleanup_claim",
        "check_account_deletion",
        "check_deletion_account",
        "finalized_ingest_upload_guard",
        "guard_catalog_actor_erasure",
        "guard_deletion_request",
        "guard_last_owner",
        "guard_purge_receipt",
        "guard_recovery_credential",
        "guard_recovery_operation",
        "guard_training_checkpoint",
        "guard_training_cleanup_claim",
        "guard_training_consent",
        "guard_training_consent_operation",
        "guard_training_participant",
        "guard_training_publication_revocation",
        "guard_training_run",
        "ingest_cleanup_claim_guard",
        "ingest_cleanup_execution_guard",
        "ingest_cleanup_queue",
        "ingest_cleanup_writer_guard",
        "ingest_execution_guard",
        "ingest_upload_guard",
        "internal_io_workload_guard",
        "internet_acquisition_authority_guard",
        "internet_acquisition_lineage_guard",
        "internet_search_immutable",
        "internet_selection_immutable",
        "internet_upload_lineage_guard",
        "lock_owner_lifecycle",
        "m_training_io_admission",
        "metadata_artwork_immutable",
        "metadata_execution_guard",
        "metadata_provider_gate_guard",
        "metadata_revision_immutable",
        "orphan_object_claim_guard",
        "provider_maintenance_guard",
        "provider_scratch_guard",
        "provider_staging_guard",
        "require_training_checkpoint_publication",
        "resource_io_execution_guard",
        "training_consent_work_changed",
        "training_execution_guard",
        "training_owner_unavailable",
        "upload_cleanup_claim_guard",
        "upload_cleanup_owner_guard",
        "upload_maintenance_target_guard",
        "verify_training_registration",
        "z_training_publication_seal_guard",
    }
)

ML_R15_TABLES = frozenset(
    {
        ("ml", "artifact"),
        ("ml", "artifact_migration_issue"),
        ("ml", "artifact_license_decision"),
        ("ml", "artifact_license_current"),
        ("ml", "face_artifact_release"),
        ("ml", "sona_artifact_release"),
        ("ml", "control_step_up_credential"),
        ("ml", "control_step_up_challenge"),
        ("ml", "control_step_up_receipt"),
    }
)
ML_R15_INDEXES = frozenset(
    {
        "ix_control_credential_actor",
        "ix_control_step_up_expiry",
        "ix_control_step_up_actor",
    }
)
ML_R15_FUNCTIONS = frozenset(
    {
        "protect_ml_artifact_immutable",
        "lock_ml_artifact_for_issue",
        "protect_embedding_artifact_binding",
        "enforce_artifact_license_sequence",
        "advance_artifact_license_current",
        "protect_artifact_license_current",
        "enforce_device_key_generation",
    }
)
ML_R15_TRIGGERS = frozenset(
    {
        "z_ml_artifact_immutable",
        "a_artifact_migration_issue_lock",
        "z_artifact_migration_issue_immutable",
        "z_embedding_artifact_binding",
        "a_artifact_license_sequence",
        "z_artifact_license_current",
        "z_artifact_license_immutable",
        "z_artifact_license_current_derived",
        "z_face_artifact_release_immutable",
        "z_sona_artifact_release_immutable",
        "z_device_key_generation",
        "z_control_step_up_receipt_immutable",
    }
)
ML_R15_GPU_TABLES = frozenset(
    {
        ("ml", "gpu_device_authority"),
        ("ml", "gpu_reservation_current"),
        ("ml", "gpu_admission_receipt"),
    }
)
ML_R15_GPU_INDEXES = frozenset({"ix_gpu_current_lease", "ix_gpu_receipt_device_time"})
ML_R15_GPU_FUNCTIONS = frozenset({"protect_gpu_current_projection", "apply_gpu_admission_receipt"})
ML_R15_GPU_TRIGGERS = frozenset(
    {
        "z_gpu_device_derived",
        "z_gpu_current_derived",
        "a_gpu_receipt_apply",
        "z_gpu_receipt_immutable",
    }
)
ML_R15_SONA_CAPTURE_TABLES = frozenset(
    {
        ("ml", "sona_capture_bundle"),
        ("ml", "sona_capture_lineage_cursor"),
        ("ml", "sona_capture_target_dispatch"),
        ("ml", "sona_shadow_work"),
        ("ml", "sona_shadow_attempt"),
        ("ml", "sona_shadow_evidence"),
    }
)
ML_R15_SONA_CAPTURE_INDEXES = frozenset(
    {
        "ix_sona_capture_owner_expiry",
        "ix_sona_cursor_active_owner_expiry",
        "ix_sona_dispatch_owner_state",
        "ix_sona_work_claim_owner",
        "ix_sona_attempt_owner_work",
        "ix_sona_evidence_owner_request",
    }
)
ML_R15_SONA_CAPTURE_FUNCTIONS = frozenset(
    {
        "protect_sona_capture_update",
        "fence_sona_capture_state",
        "validate_sona_work_target",
        "validate_sona_capture_bundle",
        "validate_sona_evidence_publication",
        "validate_sona_capture_child",
        "validate_sona_attempt_publication",
        "require_sona_success_evidence",
    }
)
ML_R15_SONA_CAPTURE_TRIGGERS = frozenset(
    {
        "z_sona_bundle_immutable",
        "z_sona_attempt_immutable",
        "z_sona_evidence_immutable",
        "z_sona_cursor_fence",
        "z_sona_dispatch_fence",
        "z_sona_work_fence",
        "a_sona_work_target",
        "a_sona_bundle_validate",
        "a_sona_evidence_validate",
        "a_sona_cursor_validate",
        "a_sona_dispatch_validate",
        "a_sona_attempt_validate",
        "z_sona_success_evidence",
    }
)
ML_R15_FACE_ACTIVATION_TABLES = frozenset(
    {
        ("ml", "face_execution_profile"),
        ("ml", "face_semantic_interpreter"),
        ("ml", "face_qualification_set"),
        ("ml", "face_qualification_approval"),
        ("ml", "face_timeline_activation"),
        ("ml", "face_activation_current"),
        ("ml", "face_analysis_policy_history"),
        ("ml", "face_analysis_policy_current"),
    }
)
ML_R15_FACE_ACTIVATION_INDEXES = frozenset(
    {
        "ix_face_interpreter_encoder",
        "ix_face_qualification_state_expiry",
        "ix_face_approval_state_expiry",
        "uq_face_policy_actor_operation",
        "ix_face_policy_owner_created",
        "ix_face_policy_current_effective",
    }
)
ML_R15_FACE_ACTIVATION_FUNCTIONS = frozenset(
    {
        "protect_face_activation_immutable",
        "fence_face_policy_actor_purge",
        "fence_face_policy_history_insert",
        "fence_face_qualification_insert",
        "fence_face_qualification_state",
        "fence_face_activation_current",
        "fence_face_policy_current",
    }
)
ML_R15_FACE_ACTIVATION_TRIGGERS = frozenset(
    {
        "z_face_profile_immutable",
        "z_face_profile_no_delete",
        "z_face_interpreter_immutable",
        "z_face_interpreter_no_delete",
        "z_face_qualification_no_delete",
        "z_face_approval_no_delete",
        "z_face_activation_immutable",
        "z_face_activation_no_delete",
        "z_face_activation_current_no_delete",
        "z_face_policy_history_immutable",
        "a_face_policy_history_authority",
        "z_face_qualification_insert",
        "z_face_approval_insert",
        "z_face_qualification_state",
        "z_face_approval_state",
        "z_face_activation_current",
        "z_face_policy_current",
    }
)


def test_migrated_database_has_exact_named_inventory(
    database_harness: DatabaseHarness, database_name: str
) -> None:
    """Compare exact migration object names with the independent reference SQL."""
    expected = parse_reference_names(REFERENCE_DDL_PATH)
    with database_harness.connect(database_name) as connection:
        snapshot = snapshot_schema(connection)
        table_names = {(str(row[0]), str(row[1])) for row in snapshot.tables}
        index_names = {str(row[1]) for row in snapshot.explicit_indexes}
        function_names = {str(row[0]) for row in snapshot.functions}
        trigger_names = {str(row[2]) for row in snapshot.triggers}
        activation_row = connection.execute(
            "SELECT count(*) FROM identity.match_policy_activation"
        ).fetchone()
        if activation_row is None or not isinstance(activation_row[0], int):
            raise AssertionError("activation count query returned no integer")
        activation_count = activation_row[0]
        extensions = dict(
            connection.execute(
                """
                SELECT extname, extversion FROM pg_extension
                WHERE extname IN ('pg_trgm', 'vector')
                """
            ).fetchall()
        )
        ann_row = connection.execute(
            """
            SELECT count(*)
            FROM pg_class index_class
            JOIN pg_index i ON i.indexrelid = index_class.oid
            JOIN pg_am am ON am.oid = index_class.relam
            WHERE am.amname IN ('hnsw', 'ivfflat')
            """
        ).fetchone()
        if ann_row is None or not isinstance(ann_row[0], int):
            raise AssertionError("ANN index count query returned no integer")
        ann_count = ann_row[0]

    assert len(table_names) == EXPECTED_TABLE_COUNT
    assert len(index_names) == EXPECTED_EXPLICIT_INDEX_COUNT
    assert len(function_names) == EXPECTED_FUNCTION_COUNT
    # Trigger names are table-scoped and four names are intentionally reused;
    # the contract count covers catalog rows while the set below covers names.
    assert len(snapshot.triggers) == EXPECTED_TRIGGER_COUNT
    assert table_names == (
        expected.tables
        | A1B_TABLES
        | A1C_TABLES
        | S1B_TABLES
        | S1C_TABLES
        | S2_TABLES
        | S1D_TABLES
        | PA2_TABLES
        | R1B_TABLES
        | POST_0030_TABLES
        | ML_R15_TABLES
        | ML_R15_GPU_TABLES
        | ML_R15_SONA_CAPTURE_TABLES
        | ML_R15_FACE_ACTIVATION_TABLES
    )
    assert index_names == (
        expected.indexes
        | A1B_INDEXES
        | A1C_INDEXES
        | S1B_INDEXES
        | S1C_INDEXES
        | S1D_INDEXES
        | PA2_INDEXES
        | R1B_INDEXES
        | POST_0030_INDEXES
        | ML_R15_INDEXES
        | ML_R15_GPU_INDEXES
        | ML_R15_SONA_CAPTURE_INDEXES
        | ML_R15_FACE_ACTIVATION_INDEXES
    )
    assert function_names == (
        expected.functions
        | A1C_FUNCTIONS
        | R1B_FUNCTIONS
        | POST_0030_FUNCTIONS
        | ML_R15_FUNCTIONS
        | ML_R15_GPU_FUNCTIONS
        | ML_R15_SONA_CAPTURE_FUNCTIONS
        | ML_R15_FACE_ACTIVATION_FUNCTIONS
    )
    assert trigger_names == (
        expected.triggers
        | S1C_TRIGGERS
        | A1C_TRIGGERS
        | S1D_TRIGGERS
        | R1B_TRIGGERS
        | POST_0030_TRIGGERS
        | ML_R15_TRIGGERS
        | ML_R15_GPU_TRIGGERS
        | ML_R15_SONA_CAPTURE_TRIGGERS
        | ML_R15_FACE_ACTIVATION_TRIGGERS
    )
    assert ("importing", "match_candidate") not in table_names
    assert activation_count == 0
    assert extensions == {"pg_trgm": "1.6", "vector": "0.8.6"}
    assert ann_count == 0


def test_migration_schema_has_no_unexplained_reference_ddl_drift(
    database_harness: DatabaseHarness,
    database_name: str,
    reference_database_name: str,
) -> None:
    """Compare catalog structure from migrations and normative reference DDL."""
    database_harness.downgrade(database_name, "0019_m6_web_admin_runtime")
    with database_harness.connect(database_name) as migrated_connection:
        migrated = snapshot_schema(migrated_connection)
    with database_harness.connect(reference_database_name) as reference_connection:
        reference = snapshot_schema(reference_connection)

    assert migrated == reference
