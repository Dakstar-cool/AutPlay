"""Admit the audited local acquisition bridge without fabricating a user session."""

from alembic import op

revision = "0060_local_bridge_authority"
down_revision = "0059_training_publication_seal"
branch_labels = None
depends_on = None

_DEVICE_AUTHORITY = """
(authority_kind='DEVICE_SESSION' AND device_id IS NOT NULL AND
 session_family_id IS NOT NULL AND session_mode IS NOT NULL AND
 session_mode IN ('V2','LEGACY') AND acquisition_attempt_id IS NULL)
"""
_LOCAL_BRIDGE_AUTHORITY = """
(authority_kind='LOCAL_BRIDGE' AND kind='TRANSFER' AND device_id IS NOT NULL AND
 session_family_id IS NULL AND session_mode IS NULL AND job_id IS NULL AND
 acquisition_attempt_id IS NULL AND source_authorization_id IS NULL AND
 source_authorization_revision IS NULL AND policy_id IS NULL AND policy_revision IS NULL)
"""
_SERVER_AUTHORITY = """
(authority_kind='SERVER_ACQUISITION' AND kind='TRANSFER' AND device_id IS NULL AND
 session_family_id IS NULL AND session_mode IS NULL AND acquisition_attempt_id IS NOT NULL AND
 job_id IS NOT NULL AND source_authorization_id IS NOT NULL AND
 source_authorization_revision IS NOT NULL)
"""


def _replace_constraints(*, include_bridge: bool) -> None:
    authority = (
        f"({_DEVICE_AUTHORITY} OR {_LOCAL_BRIDGE_AUTHORITY} OR {_SERVER_AUTHORITY})"
        if include_bridge
        else f"({_DEVICE_AUTHORITY} OR {_SERVER_AUTHORITY})"
    )
    bridge_target = (
        "AND (authority_kind<>'LOCAL_BRIDGE' OR resource_type='UPLOAD_INTENT')"
        if include_bridge
        else ""
    )
    op.execute(
        "ALTER TABLE account.resource_admission "
        "DROP CONSTRAINT admission_authority_shape_check, "
        "ADD CONSTRAINT admission_authority_shape_check CHECK "
        f"({authority})"
    )
    op.execute(
        "ALTER TABLE account.resource_admission "
        "DROP CONSTRAINT admission_worker_target_check, "
        "ADD CONSTRAINT admission_worker_target_check CHECK ("
        "(resource_type<>'DISCOVERY_ACQUISITION' OR "
        "(authority_kind='SERVER_ACQUISITION' AND target_id=acquisition_attempt_id)) "
        "AND (authority_kind<>'SERVER_ACQUISITION' OR resource_type='DISCOVERY_ACQUISITION') "
        f"{bridge_target} "
        "AND (resource_type<>'INTERNET_ACQUISITION' OR "
        "(authority_kind='DEVICE_SESSION' AND job_id IS NOT NULL)))"
    )


def upgrade() -> None:
    _replace_constraints(include_bridge=True)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM account.resource_admission
            WHERE authority_kind='LOCAL_BRIDGE'
          ) THEN
            RAISE EXCEPTION
              'refusing local bridge authority downgrade: admission evidence exists';
          END IF;
        END
        $$
        """
    )
    _replace_constraints(include_bridge=False)
