"""Create all private helper functions and database invariant triggers.

Revision ID: 0009_constraints_triggers
Revises: 0008_ml_history
"""

from __future__ import annotations

from collections.abc import Sequence

from migration_support import drop_functions, drop_triggers, execute_reference

revision: str = "0009_constraints_triggers"
down_revision: str | None = "0008_ml_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTIONS = (
    "bump_row_version",
    "prevent_recording_redirect_cycle",
    "prevent_job_dependency_cycle",
    "enforce_library_entry_owner",
    "enforce_playlist_entry_owner",
    "enforce_canonical_variant_recording",
    "reject_identity_history_mutation",
    "validate_match_policy_activation",
    "validate_match_decision",
    "enforce_embedding_dimension_and_recording",
    "enforce_taste_cluster_member_owner",
    "enforce_listening_event_owner",
    "audio_variant_is_servable",
)
FUNCTION_SIGNATURES = (
    ("app_private.bump_row_version", ""),
    ("app_private.prevent_recording_redirect_cycle", ""),
    ("app_private.prevent_job_dependency_cycle", ""),
    ("app_private.enforce_library_entry_owner", ""),
    ("app_private.enforce_playlist_entry_owner", ""),
    ("app_private.enforce_canonical_variant_recording", ""),
    ("app_private.reject_identity_history_mutation", ""),
    ("app_private.validate_match_policy_activation", ""),
    ("app_private.validate_match_decision", ""),
    ("app_private.enforce_embedding_dimension_and_recording", ""),
    ("app_private.enforce_taste_cluster_member_owner", ""),
    ("app_private.enforce_listening_event_owner", ""),
    ("app_private.audio_variant_is_servable", "uuid"),
)
TRIGGERS = (
    "tr_recording_redirect_no_cycle",
    "tr_job_dependency_no_cycle",
    "tr_library_entry_owner",
    "tr_playlist_entry_owner",
    "tr_canonical_variant_recording",
    "tr_matcher_release_append_only",
    "tr_calibrator_release_append_only",
    "tr_threshold_set_append_only",
    "tr_match_policy_activation_validate",
    "tr_match_decision_validate",
    "tr_match_candidate_evidence_validate",
    "tr_import_entry_match_projection",
    "tr_user_track_ref_match_projection",
    "tr_recording_embedding_integrity",
    "tr_taste_cluster_member_owner",
    "tr_listening_event_owner",
    "tr_user_account_row_version",
    "tr_device_row_version",
    "tr_artist_row_version",
    "tr_artist_credit_row_version",
    "tr_work_row_version",
    "tr_recording_row_version",
    "tr_release_group_row_version",
    "tr_release_row_version",
    "tr_medium_row_version",
    "tr_release_track_row_version",
    "tr_source_provider_row_version",
    "tr_external_reference_row_version",
    "tr_user_track_ref_row_version",
    "tr_library_entry_row_version",
    "tr_user_track_preference_row_version",
    "tr_playlist_row_version",
    "tr_playlist_entry_row_version",
    "tr_vault_object_row_version",
    "tr_vault_replica_row_version",
    "tr_audio_variant_row_version",
    "tr_import_job_row_version",
    "tr_import_entry_row_version",
    "tr_job_row_version",
    "tr_embedding_model_row_version",
)


def upgrade() -> None:
    execute_reference("function", FUNCTIONS)
    execute_reference("trigger", TRIGGERS)


def downgrade() -> None:
    drop_triggers(tuple(reversed(TRIGGERS)))
    drop_functions(tuple(reversed(FUNCTION_SIGNATURES)))
