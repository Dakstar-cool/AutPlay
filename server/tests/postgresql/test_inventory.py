"""Exact PostgreSQL object inventory and reference-drift tests."""

from __future__ import annotations

from typing import Any

from psycopg import Connection

from .conftest import REFERENCE_DDL_PATH, DatabaseHarness
from .schema_contract import (
    EXPECTED_EXPLICIT_INDEX_COUNT,
    EXPECTED_FUNCTION_COUNT,
    EXPECTED_TABLE_COUNT,
    EXPECTED_TRIGGER_COUNT,
    MODULE_SCHEMAS,
    parse_reference_names,
    snapshot_schema,
)

PORTABLE_TABLES = frozenset(
    {
        ("account", "portable_identity"),
        ("account", "portable_challenge"),
        ("account", "portable_record"),
        ("library", "portable_taste_signal"),
    }
)
PORTABLE_INDEXES = frozenset(
    {
        "portable_challenge_source_time",
        "portable_challenge_expiry",
        "portable_challenge_binding_commit",
        "portable_record_account_cursor",
        "portable_record_listening_aliases",
        "portable_taste_signal_account_cursor",
    }
)
PORTABLE_TRIGGERS = frozenset({"erase_portable_challenges"})
DEVICE_V2_TABLES = frozenset(
    {("account", "device_capability_revision"), ("account", "account_deletion_proof")}
)
DEVICE_V2_FUNCTIONS = frozenset(
    {
        "guard_device_capability_revision",
        "guard_device_profile_receipt",
        "guard_portable_v2_challenge",
        "guard_device_admission_v2",
        "valid_self_document_v2",
        "guard_self_device_v2",
        "valid_recovery_commit_v2",
        "valid_deletion_document_v2",
        "guard_deletion_proof_v2",
        "check_deletion_v2",
    }
)
DEVICE_V2_TRIGGERS = frozenset(
    {
        "device_capability_revision_guard",
        "device_capability_revision_no_truncate",
        "enrollment_receipt_contract_guard",
        "rotation_receipt_contract_guard",
        "portable_challenge_v2_guard",
        "admission_v2_guard",
        "trusted_key_v2_guard",
        "trusted_challenge_v2_guard",
        "admission_receipt_v2_guard",
        "admission_web_v2_guard",
        "self_pairing_v2_guard",
        "self_command_v2_guard",
        "deletion_proof_v2_guard",
        "deletion_proof_v2_no_truncate",
        "deletion_proof_v2_result",
        "deletion_request_v2_original",
        "deletion_cancel_v2_original",
    }
)

NATIVE_CAPTURE_TABLES = frozenset({("ml", "sona_native_capture")})
NATIVE_CAPTURE_INDEXES = frozenset({"sona_native_capture_owner_time", "sona_native_capture_expiry"})
NATIVE_CAPTURE_TRIGGERS = frozenset(
    {
        "guard_sona_native_capture",
        "erase_sona_native_capture_consent",
        "erase_sona_native_capture_account",
    }
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
        "admission_v2_binding_commit",
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
        "trg_social_guest_room_retire",
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
        ("jobs", "worker_health"),
        ("social", "public_id_registration"),
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
        ("discovery", "internet_catalogue_context"),
        ("discovery", "internet_search_context"),
        ("library", "metadata_artwork"),
        ("library", "metadata_execution"),
        ("library", "catalog_execution"),
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
        "internet_catalogue_context_owner_time",
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
        "protect_catalog_execution",
        "protect_catalogue_context",
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
        "catalog_execution_guard",
        "internet_catalogue_context_immutable",
        "internet_search_context_immutable",
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


WAVE_TABLES = frozenset(
    {
        ("wave", "room"),
        ("wave", "member"),
        ("wave", "invitation"),
        ("wave", "queue_entry"),
        ("wave", "command"),
        ("wave", "preflight"),
        ("wave", "timing_report"),
    }
)

WAVE_INDEXES = frozenset(
    {"ix_wave_room_expiry", "ix_wave_member_presence", "ix_wave_preflight_room_recording"}
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
        ("ml", "gpu_sona_process_binding"),
    }
)

ML_R15_GPU_INDEXES = frozenset({"ix_gpu_current_lease", "ix_gpu_receipt_device_time"})

ML_R15_GPU_FUNCTIONS = frozenset(
    {
        "protect_gpu_current_projection",
        "apply_gpu_admission_receipt",
        "guard_gpu_sona_process_binding",
        "guard_gpu_sona_release_identity",
    }
)

ML_R15_GPU_TRIGGERS = frozenset(
    {
        "z_gpu_device_derived",
        "z_gpu_current_derived",
        "a_gpu_receipt_apply",
        "z_gpu_receipt_immutable",
        "a_gpu_sona_binding_guard",
        "z_gpu_sona_binding_immutable",
        "a0_gpu_sona_release_identity",
    }
)

ML_R15_SONA_CAPTURE_TABLES = frozenset(
    {
        ("ml", "sona_capture_bundle"),
        ("ml", "sona_capture_source_event"),
        ("ml", "sona_event_erasure_receipt"),
        ("ml", "sona_capture_replay_payload"),
        ("ml", "sona_capture_outcome"),
        ("ml", "sona_capture_lineage_cursor"),
        ("ml", "sona_capture_target_dispatch"),
        ("ml", "sona_capture_registry_target"),
        ("ml", "sona_capture_registry_block"),
        ("ml", "sona_target_vetting_head"),
        ("ml", "sona_shadow_work"),
        ("ml", "sona_shadow_attempt"),
        ("ml", "sona_shadow_evidence"),
    }
)

ML_R15_SONA_CAPTURE_INDEXES = frozenset(
    {
        "ix_sona_capture_owner_expiry",
        "ix_sona_source_owner_event",
        "ix_sona_erasure_owner_watermark",
        "ix_sona_replay_owner_expiry",
        "ix_sona_outcome_owner_expiry",
        "ix_sona_outcome_listen",
        "ix_sona_outcome_impression",
        "ix_sona_cursor_active_owner_expiry",
        "ix_sona_cursor_active_scan",
        "ix_sona_dispatch_owner_state",
        "ix_sona_work_claim_owner",
        "ix_sona_attempt_owner_work",
        "ix_sona_evidence_owner_request",
    }
)

ML_R15_SONA_CAPTURE_FUNCTIONS = frozenset(
    {
        "protect_sona_capture_update",
        "protect_sona_erasure_receipt",
        "fence_sona_erased_source",
        "fence_sona_capture_state",
        "validate_sona_work_target",
        "validate_sona_capture_bundle",
        "validate_sona_capture_replay",
        "require_sona_capture_replay",
        "validate_sona_evidence_publication",
        "validate_sona_capture_child",
        "purge_sona_capture_on_source_change",
        "validate_sona_attempt_publication",
        "require_sona_success_evidence",
        "initialize_sona_vetting_head",
        "fence_sona_vetting_head",
    }
)

ML_R15_SONA_CAPTURE_TRIGGERS = frozenset(
    {
        "z_sona_bundle_immutable",
        "z_sona_erasure_receipt_immutable",
        "a_sona_source_erasure_fence",
        "a_sona_replay_validate",
        "z_sona_bundle_replay_required",
        "z_sona_replay_delete_required",
        "z_sona_replay_immutable",
        "a_sona_outcome_validate",
        "z_sona_listen_change",
        "z_sona_impression_change",
        "z_sona_sync_change",
        "z_sona_inbox_change",
        "z_sona_outcome_immutable",
        "z_sona_registry_target_immutable",
        "z_sona_registry_block_immutable",
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
        "z_sona_registry_initialize_vetting_head",
        "z_sona_vetting_head_fence",
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

ML_R15_FACE_WORK_TABLES = frozenset(
    {
        ("ml", "face_analysis_work"),
        ("ml", "face_analysis_sponsor"),
        ("ml", "face_source_invalidation"),
        ("ml", "face_source_replacement"),
        ("ml", "face_source_replacement_candidate"),
        ("ml", "face_source_replacement_remediation"),
        ("ml", "face_redirect_authority"),
        ("ml", "face_source_mutation"),
        ("ml", "face_source_mutation_candidate"),
    }
)

ML_R15_FACE_WORK_INDEXES = frozenset(
    {
        "ix_face_work_source",
        "ix_face_work_claimable",
        "ix_face_work_expired_lease",
        "ix_face_work_retry_due",
        "uq_face_sponsor_live_ref",
        "ix_face_sponsor_work_live",
        "ix_face_sponsor_owner_history",
        "ix_face_sponsor_source_live",
        "uq_face_source_invalidation_pending",
        "ix_face_source_invalidation_pending",
        "uq_face_source_replacement_pending",
        "ix_face_source_replacement_pending",
        "ix_face_replacement_candidate_pending",
        "uq_face_work_current_execution",
        "ix_face_sponsor_live_reference",
        "ix_face_source_mutation_pending",
        "ix_face_mutation_candidate_pending",
    }
)

ML_R15_FACE_WORK_FUNCTIONS = frozenset(
    {
        "fence_face_work_mutation",
        "fence_face_sponsor_mutation",
        "advance_face_work_authority",
        "verify_face_sponsor_link",
        "fence_face_source_replacement",
        "fence_face_replacement_remediation",
        "verify_face_remediation_pair",
        "fence_face_work_incarnation",
        "verify_face_work_incarnation",
        "face_redirect_path",
        "face_mutation_affects",
        "face_mutation_has_remaining",
        "capture_face_source_mutation",
        "capture_face_source_dml",
        "fence_face_source_mutation",
        "pair_face_source_mutation",
        "face_mutation_origin_valid",
        "fence_face_mutation_continuation",
        "verify_face_mutation_continuation",
        "fence_face_redirect_authority",
        "verify_face_redirect_authority",
    }
)

ML_R15_FACE_WORK_TRIGGERS = frozenset(
    {
        "z_face_work_mutation",
        "z_face_sponsor_mutation",
        "z_face_sponsor_authority",
        "z_face_sponsor_link",
        "z_face_source_replacement",
        "z_face_replacement_candidate",
        "z_face_replacement_remediation",
        "y_face_replacement_candidate_remediation",
        "z_face_remediation_terminal_pair",
        "y_face_work_incarnation",
        "z_face_work_incarnation_link",
        "z_face_canonical_capture",
        "z_face_variant_capture",
        "z_face_object_capture",
        "z_face_replica_capture",
        "z_face_recording_capture",
        "z_face_redirect_capture",
        "z_face_source_mutation",
        "z_face_mutation_candidate",
        "z_face_replacement_mutation_pair",
        "z_face_invalidation_mutation_pair",
        "x_face_work_mutation_origin",
        "x_face_sponsor_mutation_origin",
        "z_face_work_mutation_link",
        "z_face_sponsor_mutation_link",
        "z_face_redirect_authority",
        "z_face_redirect_authority_receipt",
    }
)

ML_R15_M11_TABLES = frozenset(
    {
        ("library", "artist_watch_choice"),
        ("library", "favorite_artist_refresh"),
        ("library", "favorite_artist_projection"),
        ("library", "favorite_artist_refresh_retry"),
        ("discovery", "scan_schedule"),
        ("discovery", "scan_schedule_revision"),
        ("discovery", "watch_auto_download_gate"),
        ("discovery", "watch_auto_download_consent"),
        ("discovery", "watch_auto_download_consent_revision"),
    }
)

ML_R15_M11_INDEXES = frozenset(
    {
        "ix_artist_watch_choice_followed",
        "ix_favorite_projection_rank",
        "ix_favorite_refresh_retry_due",
        "ix_m11_auto_backlog_job",
        "ix_acquisition_attempt_auto_job",
    }
)

ML_R15_M11_FUNCTIONS = frozenset({"reject_watch_auto_consent_revision_mutation"})

ML_R15_M11_TRIGGERS = frozenset(
    {"watch_auto_download_gate_bump", "watch_auto_consent_revision_immutable"}
)

ML_R15_FACE_TIMELINE_TABLES = frozenset(
    {
        ("ml", "face_timeline"),
        ("ml", "face_timeline_gc_receipt"),
        ("ml", "face_timeline_regeneration_receipt"),
        ("ml", "face_rollback_retention"),
        ("ml", "face_restore_quarantine"),
        ("ml", "face_projection_fanout"),
        ("ml", "face_projection_intent"),
        ("ml", "face_projection_materialization_cursor"),
        ("ml", "face_projection"),
        ("ml", "face_projection_applied_receipt"),
    }
)

ML_R15_FACE_TIMELINE_INDEXES = frozenset(
    {
        "ix_face_timeline_source_lookup",
        "ix_face_timeline_retention",
        "ix_face_timeline_gc_pending",
        "uq_face_work_regeneration",
        "ix_face_fanout_pending",
        "ix_face_projection_intent_pending",
        "ix_face_projection_intent_unapplied",
        "ix_face_projection_owner_state",
    }
)

ML_R15_FACE_TIMELINE_FUNCTIONS = frozenset(
    {
        "fence_face_timeline_mutation",
        "face_regeneration_work_insert",
        "face_regeneration_work_verify",
        "face_regeneration_receipt",
        "fence_face_regeneration_receipt",
        "lock_face_lifecycle",
        "fence_face_restore_quarantine",
        "capture_face_rollback_retention",
        "fence_face_lifecycle_evidence",
        "face_timeline_gc_reason",
        "receipt_face_timeline_gc",
        "fence_face_lifecycle_positive",
        "fence_face_fanout_mutation",
        "fence_face_projection_intent",
        "fence_face_projection_receipt",
        "fence_face_projection_mutation",
        "tombstone_retired_face_projection",
    }
)

ML_R15_FACE_TIMELINE_TRIGGERS = frozenset(
    {
        "z_face_timeline_update",
        "x_face_regeneration_origin",
        "z_face_regeneration_sponsor",
        "z_face_regeneration_receipt",
        "z_face_regeneration_receipt_immutable",
        "a_face_lifecycle_lock",
        "b_face_lifecycle_positive",
        "z_face_restore_quarantine",
        "z_face_rollback_retention",
        "z_face_gc_receipt",
        "z_face_retention_evidence",
        "z_face_timeline_gc_receipt",
        "z_face_timeline_delete",
        "z_face_fanout_mutation",
        "z_face_projection_intent_mutation",
        "z_face_projection_receipt_mutation",
        "z_face_projection_mutation",
        "zz_face_projection_sponsor_retired",
    }
)

ML_R15_FACE_TOMBSTONE_TABLES = frozenset(
    {
        ("ml", "face_projection_tombstone_outbox"),
        ("ml", "face_projection_tombstone_audit"),
    }
)

ML_R15_FACE_TOMBSTONE_INDEXES = frozenset(
    {
        "ix_face_tombstone_claim",
        "ix_face_tombstone_owner",
        "ix_face_tombstone_audit_outbox",
    }
)

ML_R15_FACE_TOMBSTONE_FUNCTIONS = frozenset(
    {
        "enqueue_face_projection_tombstone",
        "fence_face_projection_tombstone_outbox",
        "audit_face_projection_tombstone_outbox",
        "fence_face_projection_tombstone_audit",
        "verify_face_tombstone_delivery",
    }
)

ML_R15_FACE_TOMBSTONE_TRIGGERS = frozenset(
    {
        "zz_face_projection_tombstone_outbox",
        "z_face_tombstone_outbox_fence",
        "zz_face_tombstone_outbox_audit",
        "z_face_tombstone_audit_fence",
        "zz_face_tombstone_delivery_commit",
    }
)

ML_R15_ERASURE_FLOOR_TABLES = frozenset(
    {
        ("sync", "owner_erasure_floor"),
        ("sync", "device_erasure_floor"),
        ("sync", "device_erasure_invalidated_epoch"),
    }
)

ML_R15_ERASURE_FLOOR_INDEXES = frozenset(
    {"ix_device_erasure_floor_owner", "ix_device_erasure_invalidated_epoch_owner"}
)

ML_R15_ERASURE_FLOOR_FUNCTIONS = frozenset({"protect_erasure_floor"})

ML_R15_ERASURE_FLOOR_TRIGGERS = frozenset(
    {
        "protect_owner_erasure_floor",
        "protect_device_erasure_floor",
        "protect_device_erasure_invalidated_epoch",
    }
)

ML_R15_FACE_SIGNING_TABLES = frozenset(
    {
        ("ml", "face_activation_artifact_provenance"),
        ("ml", "face_activation_required_artifact"),
    }
)

ML_R15_FACE_SIGNING_FUNCTIONS = frozenset({"verify_face_activation_artifact_provenance"})

ML_R15_FACE_SIGNING_TRIGGERS = frozenset(
    {
        "z_face_activation_provenance_insert",
        "z_face_activation_provenance_complete",
        "z_face_activation_required_complete",
        "z_face_activation_provenance_immutable",
        "z_face_activation_required_immutable",
    }
)

ML_R15_FACE_SIGNED_UPSERT_TABLES = frozenset({("ml", "face_projection_signed_upsert")})

ML_R15_FACE_SIGNED_UPSERT_FUNCTIONS = frozenset(
    {
        "check_face_signed_upsert",
        "protect_face_signed_upsert",
        "fence_face_signed_sync_event",
        "verify_face_signed_event_commit",
    }
)

ML_R15_FACE_SIGNED_UPSERT_TRIGGERS = frozenset(
    {
        "z_face_signed_upsert_insert",
        "z_face_signed_upsert_immutable",
        "z_face_signed_sync_event",
        "z_face_signed_sync_event_commit",
        "z_face_signed_upsert_commit",
    }
)

ML_R15_STEP_UP_FUNCTIONS = frozenset({"check_ml_step_up_register_receipt"})

ML_R15_STEP_UP_TRIGGERS = frozenset({"z_ml_step_up_register_receipt"})

ML_R15_OWNER_RESTORE_TABLES = frozenset(
    {
        ("ml", "android_grant_binding"),
        ("ml", "registration_session_binding"),
        ("ml", "serving_consent_current"),
        ("ml", "serving_consent_receipt"),
        ("ml", "face_lifecycle_fact"),
        ("ml", "face_lifecycle_fact_receipt"),
        ("ml", "face_lifecycle_ledger_binding"),
        ("ml", "face_restore_reconciliation"),
    }
)

ML_R15_OWNER_RESTORE_INDEXES = frozenset({"ix_face_lifecycle_fact_transaction"})

ML_R15_OWNER_RESTORE_FUNCTIONS = frozenset(
    {
        "begin_face_restore",
        "admit_face_library_restore",
        "face_library_reference_authority",
        "verify_face_library_restore_consumed",
        "capture_face_lifecycle_fact",
        "check_serving_grant_receipt",
        "end_face_restore",
        "face_lifecycle_entity_tag",
        "fence_face_lifecycle_journal",
        "fence_face_restore_reconciliation",
        "fence_ml_step_up_privacy_delete",
        "freeze_ml_serving_evidence",
        "purge_face_owner",
        "purge_ml_serving_owner",
        "verify_face_lifecycle_independent_commit",
    }
)

ML_R15_OWNER_RESTORE_TRIGGERS = frozenset(
    {
        "check_serving_grant_receipt",
        "freeze_android_grant_binding",
        "freeze_registration_session_binding",
        "freeze_serving_consent_receipt",
        "guard_serving_consent_current_delete",
        "z_face_ledger_binding",
        "z_face_lifecycle_fact",
        "z_face_lifecycle_fact_receipt",
        "z_face_lifecycle_independent_commit",
        "z_face_restore_reconciliation",
        "z_ml_challenge_privacy_delete",
        "z_ml_credential_privacy_delete",
        "zz_face_reference_fact",
        "zzz_face_projection_fact",
        "zzz_face_sponsor_fact",
        "zzz_face_timeline_fact",
    }
)

ML_R15_NORMALIZED_INGRESS_FUNCTIONS = frozenset(
    {
        "sync_payload_pg_v1",
        "sync_payload_sha256_pg_v1",
        "validate_normalized_sync_payload_v2",
        "guard_normalized_sync_ingress",
        "check_normalized_sync_ingress_pair",
        "stage_normalized_sync_ingress_v2",
    }
)

ML_R15_NORMALIZED_INGRESS_TRIGGERS = frozenset(
    {
        "guard_normalized_sync_ingress",
        "guard_normalized_inbox_ingress",
        "check_normalized_sync_ingress_pair",
        "check_normalized_inbox_ingress_pair",
    }
)

ML_R15_INGEST_STARTUP_TABLES = frozenset({("vault", "ingest_process_owner")})

ML_R15_INGEST_STARTUP_INDEXES = frozenset(
    {"ix_ingest_process_owner_pending", "ix_ingest_cleanup_fair_pending"}
)

ML_R15_INGEST_STARTUP_FUNCTIONS = frozenset({"protect_ingest_process_owner"})

ML_R15_INGEST_STARTUP_TRIGGERS = frozenset({"ingest_process_owner_guard"})

ML_R15_NORMALIZED_ACCEPTANCE_FUNCTIONS = frozenset(
    {
        "normalized_listen_ack",
        "normalized_listen_erasure_allowed",
        "assert_normalized_listen_acceptance",
        "check_normalized_listen_acceptance",
        "guard_normalized_listen_base",
        "guard_normalized_listen_erasure",
        "accept_normalized_listen_v2",
    }
)

ML_R15_NORMALIZED_ACCEPTANCE_TRIGGERS = frozenset(
    {
        "check_normalized_listen_sync",
        "check_normalized_listen_inbox",
        "check_normalized_listen_base",
        "guard_normalized_listen_base",
        "guard_normalized_listen_tombstone",
        "guard_normalized_listen_erasure_event",
    }
)

ML_R15_NATIVE_AUTHORITY_TABLES = frozenset(
    {
        ("ml", "native_atomic_capture_connection"),
        ("ml", "recommendation_request_replay_payload"),
        ("ml", "recommendation_serving_decision"),
        ("ml", "recommendation_served_item"),
        ("ml", "recommendation_attribution_expiry_tombstone"),
        ("ml", "recommendation_attribution_association"),
        ("ml", "native_attribution_purge_receipt"),
    }
)

ML_R15_NATIVE_AUTHORITY_INDEXES = frozenset(
    {
        "ix_attribution_listen_parent",
        "ix_attribution_request_expiry",
        "ix_attribution_feedback_parent",
        "ix_native_attribution_receipt_expiry",
        "uq_attribution_impression_presentation",
    }
)

ML_R15_NATIVE_AUTHORITY_FUNCTIONS = frozenset(
    {
        "accept_native_listen_v2",
        "arm_native_atomic_capture",
        "assert_native_atomic_capture",
        "assert_native_listen_parent",
        "assert_native_listen_v2",
        "check_native_atomic_capture",
        "check_native_atomic_context",
        "check_native_listen_v2",
        "commit_native_atomic_capture",
        "commit_native_atomic_served",
        "expire_native_atomic_capture",
        "native_listen_acceptance_hash",
        "native_listen_association_hash",
        "native_listen_canonical",
        "native_listen_target_hash",
        "protect_native_atomic_capture",
        "purge_native_listen_v2",
        "witness_native_atomic_insert",
        "accept_native_feedback_v2",
        "assert_native_capture_closure",
        "check_native_capture_closure",
        "guard_native_capture_source",
        "assert_native_feedback_parent",
        "assert_native_feedback_v2",
        "native_feedback_association_hash",
        "native_feedback_command_hash",
        "native_feedback_target_hash",
        "accept_native_impression_v2",
        "assert_native_impression_v2",
        "assert_native_served_authority",
        "check_native_impression_v2",
        "check_native_served_graph",
        "check_native_served_identity",
        "commit_native_served_authority",
        "ensure_native_served_expiry",
        "expire_native_served_authority",
        "guard_native_attribution",
        "guard_native_attribution_receipt",
        "guard_native_impression_target",
        "guard_native_inline_request",
        "guard_native_request_legacy_collision",
        "lock_native_attribution_owner",
        "lock_native_served_server",
        "lock_recommendation_request_v2",
        "native_attribution_hash_v1",
        "native_impression_ack",
        "native_impression_association_hash",
        "native_impression_command_hash",
        "native_impression_erasure_allowed",
        "native_impression_target_hash",
        "native_served_baseline_digest",
        "native_served_decision_digest",
        "native_served_item_digest",
        "native_served_json_hash",
        "native_served_json_lp",
        "native_served_lp",
        "native_served_schema",
        "native_served_set_digest",
        "protect_native_served_authority",
        "protect_native_served_expiry",
        "purge_native_attribution_receipts_v2",
        "purge_native_impression_v2",
        "purge_native_request_attributions_v2",
        "purge_native_served_authority",
        "purge_native_served_expiry_receipts",
        "purge_native_served_owner",
        "purge_recommendation_attribution_v2",
        "read_native_served_authority",
    }
)

ML_R15_NATIVE_AUTHORITY_TRIGGERS = frozenset(
    {
        "check_native_listen_association",
        "guard_native_listen_insert",
        "z_native_atomic_bundle_insert",
        "z_native_atomic_complete",
        "z_native_atomic_immutable",
        "z_native_atomic_item_closure",
        "z_native_atomic_request_insert",
        "z_native_atomic_root_closure",
        "z_native_atomic_served_retention",
        "z_native_atomic_snapshot_insert",
        "b_native_capture_source_immutable",
        "z_native_capture_bundle_closure",
        "z_native_capture_source_closure",
        "z_native_capture_cursor_closure",
        "check_native_impression_association",
        "check_native_impression_target",
        "guard_native_attribution",
        "guard_native_attribution_receipt",
        "guard_native_impression_target",
        "guard_native_inline_interaction",
        "guard_native_inline_listening",
        "guard_native_inline_pack",
        "guard_native_inline_temporal",
        "guard_native_request_legacy_collision",
        "z_native_served_complete",
        "z_native_served_expiry_immutable",
        "z_native_served_immutable",
    }
)

ML_R15_NATIVE_EXPORT_TABLES = frozenset(
    {
        ("ml", "native_export"),
        ("ml", "native_export_owner"),
        ("ml", "native_export_dependency"),
    }
)

ML_R15_NATIVE_EXPORT_INDEXES = frozenset(
    {
        "ix_native_export_work",
        "ix_native_export_owner_lookup",
        "ix_native_export_dependency_parent",
    }
)

ML_R15_NATIVE_EXPORT_FUNCTIONS = frozenset(
    {
        "guard_native_export",
        "guard_native_export_link",
        "invalidate_native_export_owner",
        "invalidate_native_export_dependents",
    }
)

ML_R15_NATIVE_EXPORT_TRIGGERS = frozenset(
    {
        "native_export_guard",
        "native_export_owner_guard",
        "native_export_dependency_guard",
        "native_export_account_negative",
        "native_export_consent_negative",
        "native_export_source_negative",
        "native_export_source_event_negative",
        "native_export_erasure_negative",
        "native_export_dependents",
    }
)

ML_R15_NATIVE_PACK_TABLES = frozenset(
    {("ml", "offline_pack_binding_receipt"), ("ml", "native_offline_pack_purge_receipt")}
)

ML_R15_NATIVE_PACK_INDEXES = frozenset(
    {"ix_native_pack_binding_owner", "ix_native_pack_binding_expiry", "ix_native_pack_purge_owner"}
)

ML_R15_NATIVE_PACK_FUNCTIONS = frozenset(
    {
        "native_pack_binding_hash",
        "native_pack_association_hash",
        "native_pack_payload_v2",
        "assert_native_offline_pack_v2",
        "check_native_offline_pack_v2",
        "guard_native_offline_pack_v2",
        "purge_native_offline_pack_receipts_v2",
        "purge_native_offline_pack_v2",
        "purge_native_offline_request_v2",
        "create_native_offline_pack_v2",
        "read_native_offline_pack_v2",
    }
)

ML_R15_NATIVE_PACK_TRIGGERS = frozenset({"guard_native_pack", "check_native_pack"})

M11_RUNTIME_TABLES = frozenset(
    {
        ("discovery", "m11_native_owner"),
        ("discovery", "m11_allocation_slot"),
        ("discovery", "m11_native_seal"),
        ("discovery", "m11_never_spawned"),
        ("discovery", "m11_positive_no_go"),
        ("discovery", "m11_priority_control"),
        ("discovery", "m11_priority_execution"),
        ("discovery", "m11_priority_no_start"),
        ("discovery", "m11_priority_queue_clock"),
    }
)

M11_RUNTIME_INDEXES = frozenset(
    {
        "ix_m11_native_owner_recovery",
        "ix_m11_allocation_slot_fence",
        "ix_m11_allocation_slot_owner",
        "ix_m11_allocation_slot_pending_job",
        "ix_m11_execution_job_fence",
        "ix_m11_execution_unsettled",
        "ix_m11_execution_unsettled_job",
        "ix_m11_native_seal_fence",
        "ix_m11_native_seal_owner",
        "ix_m11_never_spawned_owner",
        "ix_m11_never_spawned_proof",
        "ix_m11_no_start_job",
        "ix_m11_positive_no_go_fence",
        "ix_m11_positive_no_go_owner",
        "ix_m11_queue_clock_eligible_wait",
        "ix_m11_fair_job_owner",
    }
)

M11_RUNTIME_FUNCTIONS = frozenset(
    {
        "guard_m11_native_owner",
        "m11_assert_epoch_accounted",
        "m11_assert_epoch_no_start",
        "m11_assert_native_slot",
        "m11_assert_owner_retirable",
        "m11_assert_positive_no_go",
        "m11_assert_slot_replacement",
        "m11_assert_slot_replacement_v1",
        "m11_epoch_catalog_ready",
        "m11_epoch_catalog_ready_v2",
        "m11_guard_allocation_slot",
        "m11_guard_attested_positive",
        "m11_guard_epoch_no_start",
        "m11_guard_execution_slots",
        "m11_guard_native_seal",
        "m11_guard_positive_no_go",
        "m11_owner_jobs",
        "m11_positive_no_go_association",
        "m11_protect_accounting_retirement",
        "m11_protect_never_spawned",
        "m11_require_allocation_prefix",
        "m11_retire_owner_accounting",
        "m11_same_allocation_binding",
        "m11_record_queue_clock",
    }
)

M11_RUNTIME_TRIGGERS = frozenset(
    {
        "trg_m11_native_owner_guard",
        "m11_allocation_slot_guard",
        "m11_native_seal_guard",
        "m11_never_spawned_retirement_guard",
        "m11_never_spawned_slot_guard",
        "m11_positive_no_go_guard",
        "m11_attested_positive_guard",
        "m11_execution_joint_slot_guard",
        "m11_execution_retirement_guard",
        "m11_epoch_no_start_guard",
        "m11_no_start_retirement_guard",
        "m11_record_queue_clock",
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
                WHERE extname IN ('pg_trgm', 'vector', 'pgcrypto')
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
        | {("ml", "sona_collection_window")}
        | POST_0030_TABLES
        | PORTABLE_TABLES
        | NATIVE_CAPTURE_TABLES
        | DEVICE_V2_TABLES
        | WAVE_TABLES
        | ML_R15_TABLES
        | ML_R15_GPU_TABLES
        | ML_R15_SONA_CAPTURE_TABLES
        | ML_R15_FACE_ACTIVATION_TABLES
        | ML_R15_M11_TABLES
        | M11_RUNTIME_TABLES
        | ML_R15_FACE_TIMELINE_TABLES
        | ML_R15_FACE_TOMBSTONE_TABLES
        | ML_R15_ERASURE_FLOOR_TABLES
        | ML_R15_FACE_WORK_TABLES
        | ML_R15_FACE_SIGNING_TABLES
        | ML_R15_FACE_SIGNED_UPSERT_TABLES
        | ML_R15_OWNER_RESTORE_TABLES
        | ML_R15_INGEST_STARTUP_TABLES
        | ML_R15_NATIVE_AUTHORITY_TABLES
        | ML_R15_NATIVE_EXPORT_TABLES
        | ML_R15_NATIVE_PACK_TABLES
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
        | PORTABLE_INDEXES
        | NATIVE_CAPTURE_INDEXES
        | {"self_pairing_v2_binding_commit", "ix_deletion_proof_user"}
        | WAVE_INDEXES
        | ML_R15_INDEXES
        | ML_R15_GPU_INDEXES
        | ML_R15_SONA_CAPTURE_INDEXES
        | ML_R15_FACE_ACTIVATION_INDEXES
        | ML_R15_M11_INDEXES
        | M11_RUNTIME_INDEXES
        | ML_R15_FACE_TIMELINE_INDEXES
        | ML_R15_FACE_TOMBSTONE_INDEXES
        | ML_R15_ERASURE_FLOOR_INDEXES
        | ML_R15_FACE_WORK_INDEXES
        | ML_R15_OWNER_RESTORE_INDEXES
        | ML_R15_INGEST_STARTUP_INDEXES
        | ML_R15_NATIVE_AUTHORITY_INDEXES
        | ML_R15_NATIVE_EXPORT_INDEXES
        | ML_R15_NATIVE_PACK_INDEXES
    )
    assert function_names == (
        expected.functions
        | A1C_FUNCTIONS
        | R1B_FUNCTIONS
        | POST_0030_FUNCTIONS
        | DEVICE_V2_FUNCTIONS
        | ML_R15_FUNCTIONS
        | ML_R15_GPU_FUNCTIONS
        | ML_R15_SONA_CAPTURE_FUNCTIONS
        | ML_R15_FACE_ACTIVATION_FUNCTIONS
        | ML_R15_M11_FUNCTIONS
        | M11_RUNTIME_FUNCTIONS
        | ML_R15_FACE_TIMELINE_FUNCTIONS
        | ML_R15_FACE_TOMBSTONE_FUNCTIONS
        | ML_R15_FACE_WORK_FUNCTIONS
        | ML_R15_FACE_SIGNING_FUNCTIONS
        | ML_R15_FACE_SIGNED_UPSERT_FUNCTIONS
        | ML_R15_ERASURE_FLOOR_FUNCTIONS
        | ML_R15_STEP_UP_FUNCTIONS
        | ML_R15_OWNER_RESTORE_FUNCTIONS
        | ML_R15_NORMALIZED_INGRESS_FUNCTIONS
        | ML_R15_INGEST_STARTUP_FUNCTIONS
        | ML_R15_NORMALIZED_ACCEPTANCE_FUNCTIONS
        | ML_R15_NATIVE_AUTHORITY_FUNCTIONS
        | ML_R15_NATIVE_EXPORT_FUNCTIONS
        | ML_R15_NATIVE_PACK_FUNCTIONS
    )
    assert trigger_names == (
        expected.triggers
        | S1C_TRIGGERS
        | A1C_TRIGGERS
        | S1D_TRIGGERS
        | R1B_TRIGGERS
        | POST_0030_TRIGGERS
        | PORTABLE_TRIGGERS
        | NATIVE_CAPTURE_TRIGGERS
        | DEVICE_V2_TRIGGERS
        | ML_R15_TRIGGERS
        | ML_R15_GPU_TRIGGERS
        | ML_R15_SONA_CAPTURE_TRIGGERS
        | ML_R15_FACE_ACTIVATION_TRIGGERS
        | ML_R15_M11_TRIGGERS
        | M11_RUNTIME_TRIGGERS
        | ML_R15_FACE_TIMELINE_TRIGGERS
        | ML_R15_FACE_TOMBSTONE_TRIGGERS
        | ML_R15_FACE_WORK_TRIGGERS
        | ML_R15_FACE_SIGNING_TRIGGERS
        | ML_R15_FACE_SIGNED_UPSERT_TRIGGERS
        | ML_R15_STEP_UP_TRIGGERS
        | ML_R15_ERASURE_FLOOR_TRIGGERS
        | ML_R15_OWNER_RESTORE_TRIGGERS
        | ML_R15_NORMALIZED_INGRESS_TRIGGERS
        | ML_R15_INGEST_STARTUP_TRIGGERS
        | ML_R15_NORMALIZED_ACCEPTANCE_TRIGGERS
        | ML_R15_NATIVE_AUTHORITY_TRIGGERS
        | ML_R15_NATIVE_EXPORT_TRIGGERS
        | ML_R15_NATIVE_PACK_TRIGGERS
    )
    assert ("importing", "match_candidate") not in table_names
    assert activation_count == 0
    assert extensions == {"pg_trgm": "1.6", "vector": "0.8.6", "pgcrypto": "1.4"}
    assert ann_count == 0


def test_migration_schema_has_no_unexplained_reference_ddl_drift(
    database_harness: DatabaseHarness,
    database_name: str,
    reference_database_name: str,
) -> None:
    """Compare catalog structure from migrations and normative reference DDL."""
    database_harness.downgrade(database_name, "0019_m6_web_admin_runtime")
    with database_harness.connect(database_name) as migrated_connection:
        full_migrated = snapshot_schema(migrated_connection)
        migrated = snapshot_schema(
            migrated_connection,
            module_schemas=tuple(schema for schema in MODULE_SCHEMAS if schema != "wave"),
        )
    with database_harness.connect(reference_database_name) as reference_connection:
        reference = snapshot_schema(reference_connection)

    assert migrated == reference
    assert set(full_migrated.tables) - set(migrated.tables) == WAVE_TABLES
    assert {
        str(row[1]) for row in full_migrated.explicit_indexes if row[0] == "wave"
    } == WAVE_INDEXES


def _portable_reference_sql() -> str:
    ddl = REFERENCE_DDL_PATH.read_text(encoding="utf-8")
    block = ddl.split("-- BEGIN PORTABLE ACCOUNT 0066 REFERENCE\n", 1)[1].split(
        "-- END PORTABLE ACCOUNT 0066 REFERENCE", 1
    )[0]
    return "\n".join(
        line.removeprefix("-- | ") for line in block.splitlines() if line.startswith("-- | ")
    )


def _portable_privacy_definitions(connection: Connection[Any]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        connection.execute(
            """
            SELECT pg_get_functiondef(name::regprocedure) FROM unnest(ARRAY[
              'account.erase_portable_challenges()', 'account.purge_account(uuid,uuid)'
            ]) name ORDER BY name
            """
        ).fetchall()
    )


def test_portable_additive_reference_matches_migrated_schema_and_privacy_functions(
    database_harness: DatabaseHarness,
    empty_database_name: str,
) -> None:
    """Check the independently stored 0066 reference without changing the historic 0019 baseline."""
    database_harness.upgrade(empty_database_name, "0065_internet_catalogue_context")
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(_portable_reference_sql(), prepare=False)
        reference = snapshot_schema(connection)
        reference_privacy = _portable_privacy_definitions(connection)
    # Both sides are exactly 0066. The shared head template is intentionally
    # 0074; never downgrade it or compare it against this frozen reference.
    migrated_database = database_harness.create_database()
    try:
        database_harness.upgrade(migrated_database, "0066_portable_local_account")
        with database_harness.connect(migrated_database) as connection:
            assert snapshot_schema(connection) == reference
            assert _portable_privacy_definitions(connection) == reference_privacy
    finally:
        database_harness.drop_database(migrated_database)


def test_native_collection_reference_matches_migrated_schema(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    ddl = REFERENCE_DDL_PATH.read_text(encoding="utf-8")
    block = ddl.split("-- BEGIN SONA COLLECTION 0067 REFERENCE\n", 1)[1].split(
        "-- END SONA COLLECTION 0067 REFERENCE", 1
    )[0]
    reference_sql = "\n".join(
        line.removeprefix("-- | ") for line in block.splitlines() if line.startswith("-- | ")
    )
    database_harness.upgrade(empty_database_name, "0066_portable_local_account")
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(reference_sql, prepare=False)
        reference = snapshot_schema(connection)
    migrated_database = database_harness.create_database()
    try:
        database_harness.upgrade(migrated_database, "0067_sona_native_collection")
        with database_harness.connect(migrated_database) as connection:
            assert snapshot_schema(connection) == reference
    finally:
        database_harness.drop_database(migrated_database)
