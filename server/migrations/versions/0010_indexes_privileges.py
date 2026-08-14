"""Create explicit reference indexes and remove PUBLIC object access.

Revision ID: 0010_indexes_privileges
Revises: 0009_constraints_triggers
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_indexes, execute_reference

revision: str = "0010_indexes_privileges"
down_revision: str | None = "0009_constraints_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    "ix_device_user_active",
    "ix_user_session_user_active",
    "ix_artist_normalized_name_trgm",
    "ix_artist_credit_normalized_name_trgm",
    "ix_artist_credit_name_artist",
    "ix_recording_artist_credit",
    "ix_recording_normalized_title_trgm",
    "ix_release_group_title_trgm",
    "ix_release_release_group",
    "ix_release_barcode",
    "ix_release_track_recording",
    "ix_audit_event_occurred_at",
    "ix_audit_event_target",
    "ix_recording_identifier_lookup",
    "ix_external_reference_recording",
    "ix_source_observation_reference_time",
    "ix_threshold_set_scope",
    "ix_match_policy_activation_threshold_time",
    "ix_device_event_inbox_pending",
    "ix_sync_event_user_sequence",
    "ix_device_sync_cursor_user",
    "ix_tombstone_retention",
    "ix_idempotency_record_expiry",
    "uq_job_idempotency",
    "ix_job_claim",
    "ix_job_expired_lease",
    "uq_user_track_ref_active_recording",
    "ix_user_track_ref_user_status",
    "ix_user_track_ref_external_reverse",
    "uq_library_entry_active",
    "ix_library_entry_page",
    "ix_playlist_owner_active",
    "uq_playlist_entry_active_position",
    "ix_playlist_entry_order",
    "ix_vault_object_status",
    "ix_vault_replica_object_status",
    "ix_audio_variant_recording_valid",
    "ix_audio_fingerprint_candidate",
    "ix_acquisition_record_variant",
    "ix_import_job_user",
    "ix_import_entry_job_status",
    "ix_match_decision_query_time",
    "ix_match_decision_candidate_time",
    "ix_match_decision_matcher_time",
    "ix_match_candidate_evidence_recording",
    "uq_embedding_model_single_active_task",
    "ix_recording_embedding_model_recording",
    "ix_recommendation_request_user_time",
    "ix_recommendation_item_recording",
    "ix_taste_cluster_user_active",
    "ix_offline_pack_user_device",
    "ix_listening_event_user_time",
    "ix_listening_event_recording_time",
)
PUBLIC_REVOKES = (
    "all_tables_public",
    "all_sequences_public",
    "app_private_functions_public",
)


def upgrade() -> None:
    execute_reference("index", INDEXES)
    execute_reference("revoke", PUBLIC_REVOKES)


def downgrade() -> None:
    # Security revokes are deliberately monotonic; base removes the schemas.
    drop_indexes(tuple(reversed(INDEXES)))
