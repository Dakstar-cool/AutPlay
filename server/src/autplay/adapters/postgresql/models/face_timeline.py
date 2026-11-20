"""Dormant canonical Face v2 timeline storage."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, Integer, Text, text
from sqlalchemy import Table as SATable
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from ..base import Base

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)


_timeline = SATable(
    "face_timeline",
    Base.metadata,
    Column("face_timeline_id", _UUID, primary_key=True, server_default=text("uuidv7()")),
    Column("semantic_key_sha256", BYTEA(), nullable=False, unique=True),
    Column("result_sha256", BYTEA(), nullable=False),
    Column("identity_document", BYTEA(), nullable=False),
    Column("source_presentation_map_document", BYTEA(), nullable=False),
    Column("source_presentation_map_sha256", BYTEA(), nullable=False),
    Column("canonical_timeline", BYTEA()),
    Column("canonical_byte_size", Integer(), nullable=False),
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
    Column("source_sha256", BYTEA(), nullable=False),
    Column("decoded_sample_rate", Integer(), nullable=False),
    Column("decoded_sample_count", BigInteger(), nullable=False),
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
    Column("preprocessing_sha256", BYTEA(), nullable=False),
    Column(
        "execution_profile_sha256",
        BYTEA(),
        ForeignKey("ml.face_execution_profile.execution_profile_sha256", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("keyframe_count", Integer(), nullable=False),
    Column("event_count", Integer(), nullable=False),
    Column("state", Text(), nullable=False, server_default=text("'STAGED'")),
    Column("state_generation", BigInteger(), nullable=False, server_default=text("1")),
    Column("created_at", _TIME, nullable=False, server_default=text("now()")),
    Column("published_at", _TIME),
    Column("revoked_at", _TIME),
    Column("retained_until", _TIME),
    Column(
        "payload_work_id",
        _UUID,
        ForeignKey("ml.face_analysis_work.face_work_id", ondelete="RESTRICT", use_alter=True),
    ),
    Column("payload_claim_token", _UUID),
    CheckConstraint(
        "(state_generation<=4 AND payload_work_id IS NULL AND payload_claim_token IS NULL) OR "
        "(state_generation>4 AND payload_work_id IS NOT NULL AND payload_claim_token IS NOT NULL)",
        name="ck_face_timeline_payload_claim",
    ),
    CheckConstraint(
        "octet_length(semantic_key_sha256)=32", name="face_timeline_semantic_key_sha256_check"
    ),
    CheckConstraint("octet_length(result_sha256)=32", name="face_timeline_result_sha256_check"),
    CheckConstraint(
        "octet_length(identity_document) BETWEEN 1 AND 65536",
        name="face_timeline_identity_document_check",
    ),
    CheckConstraint(
        "octet_length(source_presentation_map_document) BETWEEN 1 AND 65536",
        name="face_timeline_source_presentation_map_document_check",
    ),
    CheckConstraint(
        "octet_length(source_presentation_map_sha256)=32",
        name="face_timeline_source_presentation_map_sha256_check",
    ),
    CheckConstraint(
        "octet_length(canonical_timeline) BETWEEN 1 AND 1048576",
        name="face_timeline_canonical_timeline_check",
    ),
    CheckConstraint(
        "canonical_byte_size BETWEEN 1 AND 1048576",
        name="face_timeline_canonical_byte_size_check",
    ),
    CheckConstraint("octet_length(source_sha256)=32", name="face_timeline_source_sha256_check"),
    CheckConstraint(
        "decoded_sample_rate BETWEEN 8000 AND 384000",
        name="face_timeline_decoded_sample_rate_check",
    ),
    CheckConstraint(
        "decoded_sample_count BETWEEN 1 AND 33177600000",
        name="face_timeline_decoded_sample_count_check",
    ),
    CheckConstraint(
        "octet_length(preprocessing_sha256)=32", name="face_timeline_preprocessing_sha256_check"
    ),
    CheckConstraint("keyframe_count BETWEEN 0 AND 4096", name="face_timeline_keyframe_count_check"),
    CheckConstraint("event_count BETWEEN 0 AND 4096", name="face_timeline_event_count_check"),
    CheckConstraint("state IN ('STAGED','PUBLISHED','PURGED')", name="face_timeline_state_check"),
    CheckConstraint(
        "state_generation BETWEEN 1 AND 9007199254740988",
        name="face_timeline_state_generation_check",
    ),
    CheckConstraint(
        "semantic_key_sha256=sha256(convert_to('autplay.face.semantic-key.v2','UTF8') "
        "|| decode('00','hex') || identity_document)",
        name="ck_face_timeline_semantic_digest",
    ),
    CheckConstraint(
        "result_sha256=sha256(convert_to('autplay.face.timeline-result.v2','UTF8') "
        "|| decode('00','hex') || canonical_timeline)",
        name="ck_face_timeline_result_digest",
    ),
    CheckConstraint(
        "source_presentation_map_sha256=sha256(source_presentation_map_document)",
        name="ck_face_timeline_map_digest",
    ),
    CheckConstraint(
        "canonical_byte_size=octet_length(canonical_timeline)",
        name="ck_face_timeline_byte_size",
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
        name="ck_face_timeline_identity_binding",
    ),
    CheckConstraint(
        "convert_from(canonical_timeline,'UTF8')::jsonb -> 'identity' "
        "= convert_from(identity_document,'UTF8')::jsonb AND "
        "convert_from(canonical_timeline,'UTF8')::jsonb -> 'source_presentation_map' "
        "= convert_from(source_presentation_map_document,'UTF8')::jsonb",
        name="ck_face_timeline_body_binding",
    ),
    CheckConstraint(
        "(state='STAGED' AND state_generation%4=1 AND published_at IS NULL "
        "AND revoked_at IS NULL "
        "AND canonical_timeline IS NOT NULL) OR "
        "(state='PUBLISHED' AND state_generation%4=2 "
        "AND published_at IS NOT NULL AND revoked_at IS NULL "
        "AND canonical_timeline IS NOT NULL) OR "
        "(state='PURGED' AND revoked_at IS NOT NULL AND canonical_timeline IS NULL AND "
        "((state_generation%4=3 AND published_at IS NULL) OR "
        "(state_generation%4=0 AND published_at IS NOT NULL)))",
        name="ck_face_timeline_state_times",
    ),
    CheckConstraint(
        "retained_until IS NULL OR retained_until>created_at", name="ck_face_timeline_retention"
    ),
    Index(
        "ix_face_timeline_source_lookup",
        "recording_id",
        "audio_variant_id",
        "source_sha256",
        "embedding_model_id",
        "face_semantic_interpreter_id",
        "preprocessing_sha256",
    ),
    Index(
        "ix_face_timeline_retention",
        "retained_until",
        postgresql_where=text("retained_until IS NOT NULL"),
    ),
    Index(
        "ix_face_timeline_gc_pending", "face_timeline_id", postgresql_where=text("state<>'PURGED'")
    ),
    schema="ml",
)


class FaceTimelineRow(Base):
    __table__ = _timeline
    face_timeline_id: Mapped[UUID]
