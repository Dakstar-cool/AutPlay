"""Typed table mappings for the non-activating 0061 ML authority schema."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)


_artifact = SATable(
    "artifact",
    Base.metadata,
    Column("artifact_sha256", BYTEA(), primary_key=True),
    Column("artifact_byte_size", BigInteger(), nullable=False),
    Column("artifact_format", Text(), nullable=False),
    Column("source", Text(), nullable=False),
    Column("source_revision", Text(), nullable=False),
    Column("manifest_sha256", BYTEA(), nullable=False),
    Column("artifact_manifest", JSONB(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint("octet_length(artifact_sha256)=32", name="artifact_artifact_sha256_check"),
    CheckConstraint("artifact_byte_size>0", name="artifact_artifact_byte_size_check"),
    CheckConstraint(
        "length(artifact_format) BETWEEN 1 AND 100", name="artifact_artifact_format_check"
    ),
    CheckConstraint("length(source) BETWEEN 1 AND 500", name="artifact_source_check"),
    CheckConstraint(
        "length(source_revision) BETWEEN 1 AND 300", name="artifact_source_revision_check"
    ),
    CheckConstraint("octet_length(manifest_sha256)=32", name="artifact_manifest_sha256_check"),
    CheckConstraint(
        "octet_length(artifact_manifest::text)<=65536", name="artifact_artifact_manifest_check"
    ),
    schema="ml",
)


class ArtifactRow(Base):
    __table__ = _artifact


_migration_issue = SATable(
    "artifact_migration_issue",
    Base.metadata,
    Column(
        "embedding_model_id",
        _UUID,
        ForeignKey("ml.embedding_model.embedding_model_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("artifact_sha256", BYTEA(), nullable=False),
    Column("reason", Text(), nullable=False),
    Column("detected_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "octet_length(artifact_sha256)=32", name="artifact_migration_issue_artifact_sha256_check"
    ),
    CheckConstraint(
        "reason IN ('CONTENT_METADATA_CONFLICT','MANIFEST_OVERSIZE','LICENSE_CLAIM_CONFLICT')",
        name="artifact_migration_issue_reason_check",
    ),
    schema="ml",
)


class ArtifactMigrationIssueRow(Base):
    __table__ = _migration_issue


_license_decision = SATable(
    "artifact_license_decision",
    Base.metadata,
    Column(
        "artifact_sha256",
        BYTEA(),
        ForeignKey("ml.artifact.artifact_sha256", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("decision_sequence", BigInteger(), primary_key=True),
    Column("effective_generation", BigInteger(), nullable=False),
    Column("superseded_sequence", BigInteger()),
    Column("state", Text(), nullable=False),
    Column("license_identifier", Text()),
    Column("license_text_sha256", BYTEA()),
    Column("use_restrictions", JSONB(), nullable=False, server_default=text("'{}'::jsonb")),
    Column("redistribution_decision", Text()),
    Column("modification_decision", Text()),
    Column("attribution_payload", JSONB(), nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "reviewer_user_id", _UUID, ForeignKey("account.user_account.user_id", ondelete="RESTRICT")
    ),
    Column("reviewed_at", _TIME),
    Column("review_reference", Text()),
    Column("max_offline_revocation_lag_ms", BigInteger(), nullable=False, server_default=text("0")),
    Column(
        "derived_output_disposition",
        Text(),
        nullable=False,
        server_default=text("'DELETE_AFTER_LEASE'"),
    ),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "decision_sequence>=1", name="artifact_license_decision_decision_sequence_check"
    ),
    CheckConstraint(
        "effective_generation>=1", name="artifact_license_decision_effective_generation_check"
    ),
    CheckConstraint(
        "superseded_sequence IS NULL OR superseded_sequence>=1",
        name="artifact_license_decision_superseded_sequence_check",
    ),
    CheckConstraint(
        "state IN ('LEGACY_UNREVIEWED','APPROVED','DENIED','REVOKED')",
        name="artifact_license_decision_state_check",
    ),
    CheckConstraint(
        "license_identifier IS NULL OR length(license_identifier) BETWEEN 1 AND 200",
        name="artifact_license_decision_license_identifier_check",
    ),
    CheckConstraint(
        "license_text_sha256 IS NULL OR octet_length(license_text_sha256)=32",
        name="artifact_license_decision_license_text_sha256_check",
    ),
    CheckConstraint(
        "octet_length(use_restrictions::text)<=8192",
        name="artifact_license_decision_use_restrictions_check",
    ),
    CheckConstraint(
        "redistribution_decision IS NULL OR redistribution_decision IN "
        "('PERMITTED','DENIED','SEPARATE_INSTALL_ONLY')",
        name="artifact_license_decision_redistribution_decision_check",
    ),
    CheckConstraint(
        "modification_decision IS NULL OR modification_decision IN "
        "('PERMITTED','DENIED','UNREVIEWED')",
        name="artifact_license_decision_modification_decision_check",
    ),
    CheckConstraint(
        "octet_length(attribution_payload::text)<=8192",
        name="artifact_license_decision_attribution_payload_check",
    ),
    CheckConstraint(
        "review_reference IS NULL OR length(review_reference) BETWEEN 1 AND 500",
        name="artifact_license_decision_review_reference_check",
    ),
    CheckConstraint(
        "max_offline_revocation_lag_ms BETWEEN 0 AND 9007199254740991",
        name="artifact_license_decision_max_offline_revocation_lag_ms_check",
    ),
    CheckConstraint(
        "derived_output_disposition IN ('DELETE_AFTER_LEASE','RETAIN_NON_DISTRIBUTABLE')",
        name="artifact_license_decision_derived_output_disposition_check",
    ),
    CheckConstraint(
        "(state='LEGACY_UNREVIEWED' AND decision_sequence=1 AND effective_generation=1) OR "
        "(license_identifier IS NOT NULL AND license_text_sha256 IS NOT NULL "
        "AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "AND review_reference IS NOT NULL AND redistribution_decision IS NOT NULL "
        "AND modification_decision IS NOT NULL)",
        name="artifact_license_review_shape",
    ),
    CheckConstraint(
        "state<>'APPROVED' OR max_offline_revocation_lag_ms>0", name="artifact_license_approval_lag"
    ),
    schema="ml",
)


class ArtifactLicenseDecisionRow(Base):
    __table__ = _license_decision


_license_current = SATable(
    "artifact_license_current",
    Base.metadata,
    Column(
        "artifact_sha256",
        BYTEA(),
        ForeignKey("ml.artifact.artifact_sha256", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("decision_sequence", BigInteger(), nullable=False),
    Column("state", Text(), nullable=False),
    Column("effective_generation", BigInteger(), nullable=False),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    ForeignKeyConstraint(
        ["artifact_sha256", "decision_sequence"],
        [
            "ml.artifact_license_decision.artifact_sha256",
            "ml.artifact_license_decision.decision_sequence",
        ],
        name="artifact_license_current_artifact_sha256_decision_sequence_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "decision_sequence>=1", name="artifact_license_current_decision_sequence_check"
    ),
    CheckConstraint(
        "state IN ('LEGACY_UNREVIEWED','APPROVED','DENIED','REVOKED')",
        name="artifact_license_current_state_check",
    ),
    CheckConstraint(
        "effective_generation>=1", name="artifact_license_current_effective_generation_check"
    ),
    schema="ml",
)


class ArtifactLicenseCurrentRow(Base):
    __table__ = _license_current


_face_release = SATable(
    "face_artifact_release",
    Base.metadata,
    Column("face_artifact_release_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("role", Text(), nullable=False),
    Column("release_key", Text(), nullable=False),
    Column("version", Text(), nullable=False),
    Column("manifest_sha256", BYTEA(), nullable=False),
    Column(
        "artifact_sha256",
        BYTEA(),
        ForeignKey("ml.artifact.artifact_sha256", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "role", "release_key", "version", "manifest_sha256", name="uq_face_artifact_release_version"
    ),
    CheckConstraint(
        "role IN ('INTERPRETER_EXPORT','CALIBRATION','PREPROCESSING_EXECUTABLE',"
        "'DECODER_PROBE','TIMELINE_CODEC')",
        name="face_artifact_release_role_check",
    ),
    CheckConstraint(
        "length(release_key) BETWEEN 1 AND 200", name="face_artifact_release_release_key_check"
    ),
    CheckConstraint(
        "length(version) BETWEEN 1 AND 100", name="face_artifact_release_version_check"
    ),
    CheckConstraint(
        "octet_length(manifest_sha256)=32", name="face_artifact_release_manifest_sha256_check"
    ),
    schema="ml",
)


class FaceArtifactReleaseRow(Base):
    __table__ = _face_release


_sona_release = SATable(
    "sona_artifact_release",
    Base.metadata,
    Column("sona_artifact_release_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("role", Text(), nullable=False),
    Column("release_key", Text(), nullable=False),
    Column("version", Text(), nullable=False),
    Column("manifest_sha256", BYTEA(), nullable=False),
    Column(
        "artifact_sha256",
        BYTEA(),
        ForeignKey("ml.artifact.artifact_sha256", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "role", "release_key", "version", "manifest_sha256", name="uq_sona_artifact_release_version"
    ),
    CheckConstraint(
        "role IN ('SONA_MODEL','SONA_TOKENIZER')", name="sona_artifact_release_role_check"
    ),
    CheckConstraint(
        "length(release_key) BETWEEN 1 AND 200", name="sona_artifact_release_release_key_check"
    ),
    CheckConstraint(
        "length(version) BETWEEN 1 AND 100", name="sona_artifact_release_version_check"
    ),
    CheckConstraint(
        "octet_length(manifest_sha256)=32", name="sona_artifact_release_manifest_sha256_check"
    ),
    schema="ml",
)


class SonaArtifactReleaseRow(Base):
    __table__ = _sona_release


_credential = SATable(
    "control_step_up_credential",
    Base.metadata,
    Column("credential_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "actor_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "device_id",
        _UUID,
        ForeignKey("account.device.device_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("parent_device_key_generation", BigInteger(), nullable=False),
    Column("credential_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("public_key_spki", BYTEA(), nullable=False),
    Column("public_key_thumbprint_sha256", BYTEA(), nullable=False),
    Column("attestation_chain_sha256", BYTEA(), nullable=False),
    Column("attestation_summary", JSONB(), nullable=False),
    Column("registered_at", _TIME, nullable=False, server_default=text("now()")),
    Column("revoked_at", _TIME),
    ForeignKeyConstraint(
        ["actor_user_id", "device_id"],
        ["account.device.user_id", "account.device.device_id"],
        name="control_step_up_credential_actor_user_id_device_id_fkey",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "actor_user_id",
        "device_id",
        "credential_generation",
        name="uq_ml_step_up_credential_generation",
    ),
    CheckConstraint(
        "parent_device_key_generation>=1",
        name="control_step_up_credential_parent_device_key_generation_check",
    ),
    CheckConstraint(
        "credential_generation>=1", name="control_step_up_credential_credential_generation_check"
    ),
    CheckConstraint(
        "octet_length(public_key_spki) BETWEEN 64 AND 256",
        name="control_step_up_credential_public_key_spki_check",
    ),
    CheckConstraint(
        "octet_length(public_key_thumbprint_sha256)=32",
        name="control_step_up_credential_public_key_thumbprint_sha256_check",
    ),
    CheckConstraint(
        "octet_length(attestation_chain_sha256)=32",
        name="control_step_up_credential_attestation_chain_sha256_check",
    ),
    CheckConstraint(
        "octet_length(attestation_summary::text)<=8192",
        name="control_step_up_credential_attestation_summary_check",
    ),
    schema="ml",
)
Index(
    "ix_control_credential_actor",
    _credential.c.actor_user_id,
    _credential.c.device_id,
    postgresql_where=text("revoked_at IS NULL"),
)


class ControlStepUpCredentialRow(Base):
    __table__ = _credential


_challenge = SATable(
    "control_step_up_challenge",
    Base.metadata,
    Column("challenge_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("nonce_sha256", BYTEA(), nullable=False),
    Column(
        "actor_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("device_id", _UUID, ForeignKey("account.device.device_id", ondelete="RESTRICT")),
    Column("session_id", _UUID, ForeignKey("account.user_session.session_id", ondelete="RESTRICT")),
    Column(
        "web_session_id",
        _UUID,
        ForeignKey("account.web_session.web_session_id", ondelete="RESTRICT"),
    ),
    Column(
        "credential_id",
        _UUID,
        ForeignKey("ml.control_step_up_credential.credential_id", ondelete="RESTRICT"),
    ),
    Column("parent_device_key_generation", BigInteger()),
    Column("purpose", Text(), nullable=False),
    Column("action", Text(), nullable=False),
    Column("operation_id", _UUID, nullable=False),
    Column("request_sha256", BYTEA(), nullable=False),
    Column("expected_generation", BigInteger(), nullable=False),
    Column("issued_at", _TIME, nullable=False, server_default=text("now()")),
    Column("expires_at", _TIME, nullable=False),
    Column("consumed_at", _TIME),
    Column("result_sha256", BYTEA()),
    CheckConstraint(
        "octet_length(nonce_sha256)=32", name="control_step_up_challenge_nonce_sha256_check"
    ),
    CheckConstraint(
        "parent_device_key_generation IS NULL OR parent_device_key_generation>=1",
        name="control_step_up_challenge_parent_device_key_generation_check",
    ),
    CheckConstraint(
        "purpose IN ('ML_CONSENT_STEP_UP_REGISTER_V1','ML_CONSENT_STEP_UP_GRANT_V1',"
        "'ML_ADMIN_OPERATION_V1')",
        name="control_step_up_challenge_purpose_check",
    ),
    CheckConstraint(
        "length(action) BETWEEN 1 AND 100", name="control_step_up_challenge_action_check"
    ),
    CheckConstraint(
        "octet_length(request_sha256)=32", name="control_step_up_challenge_request_sha256_check"
    ),
    CheckConstraint(
        "expected_generation>=0", name="control_step_up_challenge_expected_generation_check"
    ),
    CheckConstraint(
        "result_sha256 IS NULL OR octet_length(result_sha256)=32",
        name="control_step_up_challenge_result_sha256_check",
    ),
    CheckConstraint(
        "expires_at>issued_at AND expires_at<=issued_at+interval '5 minutes'",
        name="control_step_up_challenge_check",
    ),
    CheckConstraint(
        "(session_id IS NOT NULL AND web_session_id IS NULL AND device_id IS NOT NULL) "
        "OR (web_session_id IS NOT NULL AND session_id IS NULL AND device_id IS NULL)",
        name="control_step_up_challenge_check1",
    ),
    CheckConstraint(
        "(consumed_at IS NULL AND result_sha256 IS NULL) "
        "OR (consumed_at IS NOT NULL AND result_sha256 IS NOT NULL)",
        name="control_step_up_challenge_check2",
    ),
    CheckConstraint(
        "(device_id IS NULL AND parent_device_key_generation IS NULL) "
        "OR (device_id IS NOT NULL AND parent_device_key_generation IS NOT NULL)",
        name="control_step_up_challenge_check3",
    ),
    schema="ml",
)
Index(
    "ix_control_step_up_expiry",
    _challenge.c.expires_at,
    postgresql_where=text("consumed_at IS NULL"),
)
Index("ix_control_step_up_actor", _challenge.c.actor_user_id, _challenge.c.operation_id)


class ControlStepUpChallengeRow(Base):
    __table__ = _challenge


_receipt = SATable(
    "control_step_up_receipt",
    Base.metadata,
    Column(
        "challenge_id",
        _UUID,
        ForeignKey("ml.control_step_up_challenge.challenge_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "actor_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("operation_id", _UUID, nullable=False),
    Column("request_sha256", BYTEA(), nullable=False),
    Column("result_sha256", BYTEA(), nullable=False),
    Column("result_code", Text(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "actor_user_id",
        "operation_id",
        name="control_step_up_receipt_actor_user_id_operation_id_key",
    ),
    CheckConstraint(
        "octet_length(request_sha256)=32", name="control_step_up_receipt_request_sha256_check"
    ),
    CheckConstraint(
        "octet_length(result_sha256)=32", name="control_step_up_receipt_result_sha256_check"
    ),
    CheckConstraint(
        "length(result_code) BETWEEN 1 AND 80", name="control_step_up_receipt_result_code_check"
    ),
    schema="ml",
)


class ControlStepUpReceiptRow(Base):
    __table__ = _receipt


__all__ = (
    "ArtifactLicenseCurrentRow",
    "ArtifactLicenseDecisionRow",
    "ArtifactMigrationIssueRow",
    "ArtifactRow",
    "ControlStepUpChallengeRow",
    "ControlStepUpCredentialRow",
    "ControlStepUpReceiptRow",
    "FaceArtifactReleaseRow",
    "SonaArtifactReleaseRow",
)
