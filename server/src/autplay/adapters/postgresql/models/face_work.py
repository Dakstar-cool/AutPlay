"""Dormant exact Face work, owner sponsor, and publication root rows."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)


def _digest(name: str) -> Column[bytes]:
    return Column(name, BYTEA(), nullable=False)


_work = SATable(
    "face_analysis_work",
    Base.metadata,
    Column("face_work_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    _digest("semantic_key_sha256"),
    Column("identity_document", BYTEA(), nullable=False),
    _digest("lineage_sha256"),
    _digest("artifact_policy_list_sha256"),
    Column(
        "recording_id",
        _UUID,
        ForeignKey("catalog.recording.recording_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "audio_variant_id",
        _UUID,
        ForeignKey("vault.audio_variant.audio_variant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    _digest("source_sha256"),
    Column("decoded_sample_rate", Integer(), nullable=False),
    Column("decoded_sample_count", BigInteger(), nullable=False),
    _digest("source_presentation_map_sha256"),
    Column(
        "embedding_model_id",
        _UUID,
        ForeignKey("ml.embedding_model.embedding_model_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_semantic_interpreter_id",
        _UUID,
        ForeignKey(
            "ml.face_semantic_interpreter.face_semantic_interpreter_id", ondelete="RESTRICT"
        ),
        nullable=False,
    ),
    _digest("preprocessing_sha256"),
    Column(
        "execution_profile_sha256",
        BYTEA(),
        ForeignKey("ml.face_execution_profile.execution_profile_sha256", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("work_authority_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("live_sponsor_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("attempt_count", Integer(), nullable=False, server_default=text("0")),
    Column("claim_generation", BigInteger(), nullable=False, server_default=text("0")),
    Column("claim_token", _UUID),
    Column("claimed_authority_generation", BigInteger()),
    Column("lease_expires_at", _TIME),
    Column("next_retry_at", _TIME),
    Column("last_reason", Text()),
    Column("state_changed_at", _TIME, nullable=False, server_default=text("now()")),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("execution_incarnation", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "predecessor_work_id",
        _UUID,
        ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT"),
    ),
    Column(
        "incarnation_predecessor_sponsor_id",
        _UUID,
        ForeignKey(
            "ml.face_analysis_sponsor.face_sponsor_id",
            ondelete="RESTRICT",
            name="face_analysis_work_incarnation_predecessor_sponsor_id_fkey",
            use_alter=True,
        ),
    ),
    Column("incarnation_source_generation", BigInteger()),
    Column(
        "incarnation_mutation_id",
        _UUID,
        ForeignKey("ml.face_source_mutation.face_source_mutation_id", ondelete="RESTRICT"),
    ),
    Column("regeneration_timeline_id", _UUID),
    Column("regeneration_prior_generation", BigInteger()),
    Column("erased_incarnation_origin_sha256", BYTEA()),
    CheckConstraint(
        "erased_incarnation_origin_sha256 IS NULL OR "
        "(octet_length(erased_incarnation_origin_sha256)=32 AND execution_incarnation>1 "
        "AND incarnation_predecessor_sponsor_id IS NULL AND regeneration_timeline_id IS NULL)",
        name="ck_face_work_erased_origin",
    ),
    ForeignKeyConstraint(
        ["regeneration_timeline_id", "regeneration_prior_generation"],
        [
            "ml.face_timeline_gc_receipt.face_timeline_id",
            "ml.face_timeline_gc_receipt.prior_generation",
        ],
        name="fk_face_work_regeneration_receipt",
        ondelete="RESTRICT",
        use_alter=True,
    ),
    CheckConstraint(
        "(regeneration_timeline_id IS NULL)=(regeneration_prior_generation IS NULL)",
        name="ck_face_work_regeneration_pair",
    ),
    Index(
        "uq_face_work_regeneration",
        "regeneration_timeline_id",
        "regeneration_prior_generation",
        unique=True,
        postgresql_where=text("regeneration_timeline_id IS NOT NULL"),
    ),
    UniqueConstraint(
        "recording_id",
        "audio_variant_id",
        "source_sha256",
        "lineage_sha256",
        "execution_incarnation",
        name="uq_face_work_exact_key",
    ),
    CheckConstraint(
        "execution_incarnation BETWEEN 1 AND 9007199254740990",
        name="ck_face_work_incarnation",
    ),
    CheckConstraint(
        "(execution_incarnation=1 AND predecessor_work_id IS NULL AND "
        "incarnation_predecessor_sponsor_id IS NULL AND incarnation_source_generation IS NULL "
        "AND regeneration_timeline_id IS NULL) OR "
        "(execution_incarnation>1 AND predecessor_work_id IS NOT NULL AND ("
        "(regeneration_timeline_id IS NULL AND "
        "((incarnation_predecessor_sponsor_id IS NOT NULL "
        "AND erased_incarnation_origin_sha256 IS NULL) OR "
        "(incarnation_predecessor_sponsor_id IS NULL "
        "AND erased_incarnation_origin_sha256 IS NOT NULL)) "
        "AND incarnation_source_generation BETWEEN 2 AND 9007199254740990) OR "
        "(regeneration_timeline_id IS NOT NULL AND incarnation_predecessor_sponsor_id IS NULL "
        "AND incarnation_source_generation IS NULL AND incarnation_mutation_id IS NULL)))",
        name="ck_face_work_incarnation_origin",
    ),
    CheckConstraint(
        "semantic_key_sha256=sha256(convert_to('autplay.face.semantic-key.v2','UTF8') "
        "|| decode('00','hex') || identity_document)",
        name="ck_face_work_semantic_digest",
    ),
    CheckConstraint(
        "convert_from(identity_document,'UTF8')::jsonb @> jsonb_build_object("
        "'recording_id',recording_id::text,'audio_variant_id',audio_variant_id::text,"
        "'source_sha256',encode(source_sha256,'hex'),"
        "'decoded_sample_rate',decoded_sample_rate,"
        "'decoded_sample_count',decoded_sample_count,"
        "'source_presentation_map_sha256',encode(source_presentation_map_sha256,'hex'),"
        "'embedding_model_id',embedding_model_id::text,"
        "'semantic_interpreter_id',face_semantic_interpreter_id::text,"
        "'preprocessing_sha256',encode(preprocessing_sha256,'hex'),"
        "'execution_profile_sha256',encode(execution_profile_sha256,'hex'))",
        name="ck_face_work_identity_binding",
    ),
    CheckConstraint(
        "(state='CLAIMED' AND claim_token IS NOT NULL "
        "AND claimed_authority_generation IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND next_retry_at IS NULL AND attempt_count>0) "
        "OR (state<>'CLAIMED' AND claim_token IS NULL "
        "AND claimed_authority_generation IS NULL AND lease_expires_at IS NULL)",
        name="ck_face_work_claim_shape",
    ),
    CheckConstraint(
        "(state='RETRY_WAIT' AND next_retry_at IS NOT NULL AND last_reason IS NOT NULL) "
        "OR (state<>'RETRY_WAIT' AND next_retry_at IS NULL)",
        name="ck_face_work_retry_shape",
    ),
    CheckConstraint(
        "state<>'PENDING' OR attempt_count>0 OR (claim_generation=0 AND last_reason IS NULL)",
        name="ck_face_work_initial_shape",
    ),
    Index("ix_face_work_source", "recording_id", "audio_variant_id", "source_sha256", "state"),
    Index(
        "uq_face_work_current_execution",
        "recording_id",
        "audio_variant_id",
        "source_sha256",
        "lineage_sha256",
        unique=True,
        postgresql_where=text("state IN ('PENDING','CLAIMED','RETRY_WAIT')"),
    ),
    Index(
        "ix_face_work_claimable",
        "created_at",
        "face_work_id",
        postgresql_where=text("state='PENDING' AND live_sponsor_count>0"),
    ),
    Index(
        "ix_face_work_expired_lease",
        "lease_expires_at",
        "face_work_id",
        postgresql_where=text("state='CLAIMED'"),
    ),
    Index(
        "ix_face_work_retry_due",
        "next_retry_at",
        "face_work_id",
        postgresql_where=text("state='RETRY_WAIT'"),
    ),
    schema="ml",
)


class FaceAnalysisWorkRow(Base):
    __table__ = _work

    face_work_id: Mapped[UUID]


_sponsor = SATable(
    "face_analysis_sponsor",
    Base.metadata,
    Column("face_sponsor_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "face_work_id",
        _UUID,
        ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "owner_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "user_track_ref_id",
        _UUID,
        ForeignKey("library.user_track_ref.user_track_ref_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "recording_id",
        _UUID,
        ForeignKey("catalog.recording.recording_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "audio_variant_id",
        _UUID,
        ForeignKey("vault.audio_variant.audio_variant_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    _digest("source_sha256"),
    _digest("lineage_sha256"),
    _digest("artifact_policy_list_sha256"),
    Column("source_generation", BigInteger(), nullable=False),
    Column("policy_generation", BigInteger(), nullable=False),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column("redirect_generation", BigInteger(), nullable=False),
    Column("sponsor_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column(
        "source_mutation_id",
        _UUID,
        ForeignKey("ml.face_source_mutation.face_source_mutation_id", ondelete="RESTRICT"),
    ),
    Column(
        "mutation_predecessor_sponsor_id",
        _UUID,
        ForeignKey(
            "ml.face_analysis_sponsor.face_sponsor_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    ),
    CheckConstraint(
        "(source_mutation_id IS NULL AND mutation_predecessor_sponsor_id IS NULL) "
        "OR (source_mutation_id IS NOT NULL AND mutation_predecessor_sponsor_id IS NOT NULL)",
        name="ck_face_sponsor_mutation_origin",
    ),
    Column(
        "predecessor_id",
        _UUID,
        ForeignKey(
            "ml.face_analysis_sponsor.face_sponsor_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    ),
    Column(
        "successor_id",
        _UUID,
        ForeignKey(
            "ml.face_analysis_sponsor.face_sponsor_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    ),
    Column("state", Text(), nullable=False, server_default=text("'LIVE'")),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("retired_at", _TIME),
    UniqueConstraint("predecessor_id", name="uq_face_sponsor_predecessor"),
    UniqueConstraint("successor_id", name="uq_face_sponsor_successor"),
    CheckConstraint(
        "face_sponsor_id IS DISTINCT FROM predecessor_id "
        "AND face_sponsor_id IS DISTINCT FROM successor_id "
        "AND ((state='LIVE' AND successor_id IS NULL AND retired_at IS NULL) "
        "OR (state='WITHDRAWN' AND successor_id IS NULL AND retired_at IS NOT NULL) "
        "OR (state='SUPERSEDED' AND successor_id IS NOT NULL AND retired_at IS NOT NULL))",
        name="ck_face_sponsor_link",
    ),
    Index(
        "uq_face_sponsor_live_ref",
        "owner_user_id",
        "user_track_ref_id",
        "policy_generation",
        "lineage_sha256",
        unique=True,
        postgresql_where=text("state='LIVE'"),
    ),
    Index(
        "ix_face_sponsor_work_live",
        "face_work_id",
        "face_sponsor_id",
        postgresql_where=text("state='LIVE'"),
    ),
    Index("ix_face_sponsor_owner_history", "owner_user_id", "user_track_ref_id", "created_at"),
    Index(
        "ix_face_sponsor_live_reference", "user_track_ref_id", postgresql_where=text("state='LIVE'")
    ),
    Index(
        "ix_face_sponsor_source_live",
        "recording_id",
        "audio_variant_id",
        "face_sponsor_id",
        postgresql_where=text("state='LIVE'"),
    ),
    schema="ml",
)


class FaceAnalysisSponsorRow(Base):
    __table__ = _sponsor

    face_sponsor_id: Mapped[UUID]


_fanout = SATable(
    "face_projection_fanout",
    Base.metadata,
    Column("face_projection_fanout_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "face_timeline_id",
        _UUID,
        ForeignKey("ml.face_timeline.face_timeline_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_work_id",
        _UUID,
        ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("work_authority_generation", BigInteger(), nullable=False),
    Column("claim_token", _UUID, nullable=False),
    Column("claim_generation", BigInteger(), nullable=False),
    Column("sponsor_high_watermark", _UUID, nullable=False),
    Column("sponsor_cursor", _UUID),
    Column("verification_cursor", _UUID),
    Column("batch_limit", Integer(), nullable=False, server_default=text("100")),
    Column("discovered_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("cancelled_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("state", Text(), nullable=False, server_default=text("'PENDING'")),
    Column("lease_token", _UUID),
    Column("lease_expires_at", _TIME),
    Column("restart_count", Integer(), nullable=False, server_default=text("0")),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("completed_at", _TIME),
    UniqueConstraint("face_work_id", name="uq_face_fanout_work"),
    CheckConstraint(
        "work_authority_generation>=1",
        name="face_projection_fanout_work_authority_generation_check",
    ),
    CheckConstraint(
        "claim_generation BETWEEN 1 AND 9007199254740990",
        name="face_projection_fanout_claim_generation_check",
    ),
    CheckConstraint("batch_limit=100", name="face_projection_fanout_batch_limit_check"),
    CheckConstraint("discovered_count>=0", name="face_projection_fanout_discovered_count_check"),
    CheckConstraint("cancelled_count>=0", name="face_projection_fanout_cancelled_count_check"),
    CheckConstraint(
        "state IN ('PENDING','SCANNING','VERIFYING','COMPLETE')",
        name="face_projection_fanout_state_check",
    ),
    CheckConstraint("restart_count>=0", name="face_projection_fanout_restart_count_check"),
    CheckConstraint(
        "(sponsor_cursor IS NULL OR sponsor_cursor<=sponsor_high_watermark) "
        "AND (verification_cursor IS NULL OR verification_cursor<=sponsor_high_watermark)",
        name="ck_face_fanout_cursor",
    ),
    CheckConstraint(
        "state<>'PENDING' OR (sponsor_cursor IS NULL AND verification_cursor IS NULL "
        "AND discovered_count=0 AND cancelled_count=0 AND lease_token IS NULL "
        "AND lease_expires_at IS NULL AND restart_count=0 AND completed_at IS NULL)",
        name="ck_face_fanout_initial_shape",
    ),
    Index(
        "ix_face_fanout_pending",
        "created_at",
        "face_projection_fanout_id",
        postgresql_where=text("state='PENDING'"),
    ),
    schema="ml",
)


class FaceProjectionFanoutRow(Base):
    __table__ = _fanout

    face_projection_fanout_id: Mapped[UUID]


_intent = SATable(
    "face_projection_intent",
    Base.metadata,
    Column("face_projection_intent_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "face_projection_fanout_id",
        _UUID,
        ForeignKey("ml.face_projection_fanout.face_projection_fanout_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_timeline_id",
        _UUID,
        ForeignKey("ml.face_timeline.face_timeline_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_work_id",
        _UUID,
        ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "face_sponsor_id",
        _UUID,
        ForeignKey("ml.face_analysis_sponsor.face_sponsor_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sponsor_generation", BigInteger(), nullable=False),
    Column("source_generation", BigInteger(), nullable=False),
    Column("policy_generation", BigInteger(), nullable=False),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column("redirect_generation", BigInteger(), nullable=False),
    Column("work_authority_generation", BigInteger(), nullable=False),
    Column("outcome", Text(), nullable=False),
    Column("reason", Text()),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "face_projection_fanout_id", "face_sponsor_id", name="uq_face_projection_intent_sponsor"
    ),
    CheckConstraint(
        "sponsor_generation IN (1,2)", name="face_projection_intent_sponsor_generation_check"
    ),
    CheckConstraint("source_generation>=1", name="face_projection_intent_source_generation_check"),
    CheckConstraint("policy_generation>=1", name="face_projection_intent_policy_generation_check"),
    CheckConstraint("activation_epoch>=1", name="face_projection_intent_activation_epoch_check"),
    CheckConstraint(
        "redirect_generation>=0", name="face_projection_intent_redirect_generation_check"
    ),
    CheckConstraint(
        "work_authority_generation>=1",
        name="face_projection_intent_work_authority_generation_check",
    ),
    CheckConstraint(
        "outcome IN ('INTENT','CANCELLED')", name="face_projection_intent_outcome_check"
    ),
    CheckConstraint(
        "(outcome='INTENT' AND reason IS NULL AND sponsor_generation=1) "
        "OR (outcome='CANCELLED' AND reason ~ '^[A-Z][A-Z0-9_]{0,63}$')",
        name="ck_face_projection_intent_reason",
    ),
    Index(
        "ix_face_projection_intent_pending",
        "face_projection_fanout_id",
        "face_sponsor_id",
        postgresql_where=text("outcome='INTENT'"),
    ),
    schema="ml",
)


class FaceProjectionIntentRow(Base):
    __table__ = _intent

    face_projection_intent_id: Mapped[UUID]


_materialization_cursor = SATable(
    "face_projection_materialization_cursor",
    Base.metadata,
    Column("cursor_id", Integer(), primary_key=True, server_default=text("1")),
    Column("last_intent_id", _UUID),
    Column("batch_limit", Integer(), nullable=False, server_default=text("100")),
    Column("applied_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("tombstoned_count", BigInteger(), nullable=False, server_default=text("0")),
    Column("updated_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint("cursor_id=1", name="face_projection_materialization_cursor_cursor_id_check"),
    CheckConstraint(
        "batch_limit=100", name="face_projection_materialization_cursor_batch_limit_check"
    ),
    CheckConstraint(
        "applied_count>=0", name="face_projection_materialization_cursor_applied_count_check"
    ),
    CheckConstraint(
        "tombstoned_count>=0", name="face_projection_materialization_cursor_tombstoned_count_check"
    ),
    schema="ml",
)


class FaceProjectionMaterializationCursorRow(Base):
    __table__ = _materialization_cursor

    cursor_id: Mapped[int]


_projection = SATable(
    "face_projection",
    Base.metadata,
    Column("face_projection_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column(
        "face_sponsor_id",
        _UUID,
        ForeignKey("ml.face_analysis_sponsor.face_sponsor_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    Column(
        "face_timeline_id",
        _UUID,
        ForeignKey("ml.face_timeline.face_timeline_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "owner_user_id",
        _UUID,
        ForeignKey("account.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "server_profile_id",
        _UUID,
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT"),
    ),
    Column(
        "user_track_ref_id",
        _UUID,
        ForeignKey("library.user_track_ref.user_track_ref_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("source_generation", BigInteger(), nullable=False),
    Column("policy_generation", BigInteger(), nullable=False),
    Column("activation_epoch", BigInteger(), nullable=False),
    Column("redirect_generation", BigInteger(), nullable=False),
    Column("work_authority_generation", BigInteger(), nullable=False),
    Column("projection_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("state", Text(), nullable=False),
    Column("reason", Text()),
    Column(
        "last_intent_id",
        _UUID,
        ForeignKey("ml.face_projection_intent.face_projection_intent_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("tombstoned_at", _TIME),
    CheckConstraint("source_generation>=1", name="face_projection_source_generation_check"),
    CheckConstraint("policy_generation>=1", name="face_projection_policy_generation_check"),
    CheckConstraint("activation_epoch>=1", name="face_projection_activation_epoch_check"),
    CheckConstraint("redirect_generation>=0", name="face_projection_redirect_generation_check"),
    CheckConstraint(
        "work_authority_generation>=1", name="face_projection_work_authority_generation_check"
    ),
    CheckConstraint(
        "projection_generation IN (1,2)", name="face_projection_projection_generation_check"
    ),
    CheckConstraint("state IN ('ACTIVE','TOMBSTONED')", name="face_projection_state_check"),
    CheckConstraint(
        "reason IS NULL OR reason ~ '^[A-Z][A-Z0-9_]{0,63}$'",
        name="face_projection_reason_check",
    ),
    CheckConstraint(
        "(state='ACTIVE' AND projection_generation=1 AND reason IS NULL AND tombstoned_at IS NULL) "
        "OR (state='TOMBSTONED' AND reason IS NOT NULL AND tombstoned_at IS NOT NULL)",
        name="ck_face_projection_state",
    ),
    Index(
        "ix_face_projection_owner_state",
        "owner_user_id",
        "user_track_ref_id",
        postgresql_where=text("state='ACTIVE'"),
    ),
    schema="ml",
)


class FaceProjectionRow(Base):
    __table__ = _projection

    face_projection_id: Mapped[UUID]


_signed_upsert = SATable(
    "face_projection_signed_upsert",
    Base.metadata,
    Column(
        "face_projection_id",
        _UUID,
        ForeignKey("ml.face_projection.face_projection_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "event_id",
        _UUID,
        ForeignKey("sync.sync_event.event_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    Column(
        "server_profile_id",
        _UUID,
        ForeignKey("account.server_instance.server_instance_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("canonical_envelope", BYTEA(), nullable=False),
    Column("envelope_sha256", BYTEA(), nullable=False),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "octet_length(canonical_envelope) BETWEEN 1 AND 131072",
        name="face_projection_signed_upsert_canonical_envelope_check",
    ),
    CheckConstraint(
        "octet_length(envelope_sha256)=32",
        name="face_projection_signed_upsert_envelope_sha256_check",
    ),
    CheckConstraint(
        "envelope_sha256=sha256(canonical_envelope)",
        name="ck_face_signed_upsert_digest",
    ),
    schema="ml",
)


class FaceProjectionSignedUpsertRow(Base):
    __table__ = _signed_upsert

    face_projection_id: Mapped[UUID]


_applied_receipt = SATable(
    "face_projection_applied_receipt",
    Base.metadata,
    Column(
        "face_projection_intent_id",
        _UUID,
        ForeignKey("ml.face_projection_intent.face_projection_intent_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "face_projection_id",
        _UUID,
        ForeignKey("ml.face_projection.face_projection_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("outcome", Text(), nullable=False),
    Column("reason", Text()),
    Column("applied_at", _TIME, nullable=False, server_default=text("now()")),
    CheckConstraint(
        "outcome IN ('APPLIED','TOMBSTONED')", name="face_projection_applied_receipt_outcome_check"
    ),
    CheckConstraint(
        "reason IS NULL OR reason ~ '^[A-Z][A-Z0-9_]{0,63}$'",
        name="face_projection_applied_receipt_reason_check",
    ),
    CheckConstraint(
        "(outcome='APPLIED' AND reason IS NULL) OR (outcome='TOMBSTONED' AND reason IS NOT NULL)",
        name="ck_face_projection_receipt_outcome",
    ),
    schema="ml",
)


class FaceProjectionAppliedReceiptRow(Base):
    __table__ = _applied_receipt

    face_projection_intent_id: Mapped[UUID]


Index(
    "ix_face_projection_intent_unapplied", _intent.c.created_at, _intent.c.face_projection_intent_id
)


for table, checks in (
    (
        _work,
        {
            "semantic_key_sha256": "octet_length(semantic_key_sha256)=32",
            "identity_document": "octet_length(identity_document) BETWEEN 1 AND 65536",
            "lineage_sha256": "octet_length(lineage_sha256)=32",
            "artifact_policy_list_sha256": "octet_length(artifact_policy_list_sha256)=32",
            "source_sha256": "octet_length(source_sha256)=32",
            "decoded_sample_rate": "decoded_sample_rate BETWEEN 8000 AND 384000",
            "decoded_sample_count": "decoded_sample_count BETWEEN 1 AND 33177600000",
            "source_presentation_map_sha256": "octet_length(source_presentation_map_sha256)=32",
            "preprocessing_sha256": "octet_length(preprocessing_sha256)=32",
            "state": "state IN ('PENDING','CLAIMED','RETRY_WAIT','SUCCEEDED',"
            "'TERMINAL_INELIGIBLE','TERMINAL_FAILED','RETRY_EXHAUSTED',"
            "'CANCELLED','SUPERSEDED')",
            "work_authority_generation": "work_authority_generation>=1",
            "live_sponsor_count": "live_sponsor_count>=0",
            "attempt_count": "attempt_count BETWEEN 0 AND 8",
            "claim_generation": "claim_generation BETWEEN 0 AND 9007199254740990",
            "last_reason": "last_reason ~ '^[A-Z][A-Z0-9_]{0,63}$'",
        },
    ),
    (
        _sponsor,
        {
            "source_sha256": "octet_length(source_sha256)=32",
            "lineage_sha256": "octet_length(lineage_sha256)=32",
            "artifact_policy_list_sha256": "octet_length(artifact_policy_list_sha256)=32",
            "source_generation": "source_generation BETWEEN 1 AND 9007199254740990",
            "policy_generation": "policy_generation BETWEEN 1 AND 9007199254740990",
            "activation_epoch": "activation_epoch BETWEEN 1 AND 9007199254740990",
            "redirect_generation": "redirect_generation BETWEEN 0 AND 9007199254740990",
            "sponsor_generation": "sponsor_generation IN (1,2)",
            "state": "state IN ('LIVE','WITHDRAWN','SUPERSEDED')",
        },
    ),
):
    for column_name, expression in checks.items():
        table.append_constraint(
            CheckConstraint(expression, name=f"{table.name}_{column_name}_check")
        )
