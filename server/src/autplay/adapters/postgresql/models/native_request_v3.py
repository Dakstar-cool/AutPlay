"""Explicitly imported metadata for native source3 and request-root3 storage.

Format rows and historical root descriptors are immutable SQL facts, not current
training consent. Private creation, validation-generation and purge contexts are
SQL-only, as are cross-format uniqueness and lifecycle dispatch triggers.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped

from autplay.domain.native_original_capture_v3 import MAX_ORIGINAL_BASELINE_V4_BYTES

from ..base import Base
from .native_atomic_capture import _Xid8

_UUID = PG_UUID(as_uuid=True)
_TIME = TIMESTAMP(timezone=True)
_SAFE53 = 9007199254740991
_DOCUMENT_LIMIT = 2097152
_SOURCE_CODEC = "NATIVE_SOURCE3_F64BITS_JSON_SAFE53_V1"
_SOURCE_CODEC_SHA256 = "eda9439b265c5e92934f8f62a333782579a14aed116385271f158819045581a2"


def _uuid(name: str, *, primary_key: bool = False, nullable: bool = False) -> Column[UUID]:
    return Column(name, _UUID, primary_key=primary_key, nullable=nullable)


def _hashes(prefix: str, *names: str) -> list[Column[bytes] | CheckConstraint]:
    result: list[Column[bytes] | CheckConstraint] = []
    for name in names:
        result.extend(
            (
                Column(name, BYTEA(), nullable=False),
                CheckConstraint(f"octet_length({name})=32", name=f"ck_{prefix}_{name}"),
            )
        )
    return result


def _codec_hash(prefix: str) -> list[Column[bytes] | CheckConstraint]:
    return [
        *_hashes(prefix, "source_codec_sha256"),
        CheckConstraint(
            f"source_codec_sha256=decode('{_SOURCE_CODEC_SHA256}','hex')",
            name=f"ck_{prefix}_codec_policy",
        ),
    ]


def _request_fk(prefix: str, key: str, *, ondelete: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["owner_user_id", key],
        [
            "ml.recommendation_request.user_id",
            "ml.recommendation_request.recommendation_request_id",
        ],
        name=f"fk_{prefix}_request_owner",
        ondelete=ondelete,
    )


def _bundle_fk(prefix: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["owner_user_id", "original_request_id"],
        [
            "ml.native_original_capture_v3.owner_user_id",
            "ml.native_original_capture_v3.original_request_id",
        ],
        name=f"fk_{prefix}_bundle_owner",
        ondelete="CASCADE",
    )


def _baseline_fk(prefix: str, column: str = "baseline_snapshot_id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["owner_user_id", column],
        [
            "ml.recommendation_input_snapshot.user_id",
            "ml.recommendation_input_snapshot.recommendation_input_snapshot_id",
        ],
        name=f"fk_{prefix}_baseline_owner",
        ondelete="RESTRICT",
    )


def _lifetime(
    prefix: str, *, legacy_version: str | None = None
) -> list[Column[datetime] | CheckConstraint]:
    expression = "expires_at=created_at+interval '15552000 seconds'"
    if legacy_version is not None:
        expression = (
            f"({legacy_version}=2 AND expires_at=created_at+interval '180 days') OR "
            f"({legacy_version}=3 AND {expression})"
        )
    return [
        Column("created_at", _TIME, nullable=False),
        Column("expires_at", _TIME, nullable=False),
        CheckConstraint(expression, name=f"ck_{prefix}_expiry"),
    ]


def _length(
    prefix: str, name: str, *, minimum: int = 1, maximum: int = _DOCUMENT_LIMIT
) -> list[Column[int] | CheckConstraint]:
    return [
        Column(name, Integer(), nullable=False),
        CheckConstraint(
            f"{name} BETWEEN {minimum} AND {maximum}", name=f"ck_{prefix}_{name}"
        ),
    ]


def _counts(prefix: str) -> list[Column[int] | CheckConstraint]:
    return [
        Column("source_snapshot_track_count", Integer(), nullable=False),
        Column("candidate_universe_count", Integer(), nullable=False),
        Column("p11_item_count", Integer(), nullable=False),
        CheckConstraint(
            "source_snapshot_track_count BETWEEN 0 AND 5000 AND "
            "candidate_universe_count BETWEEN 0 AND source_snapshot_track_count AND "
            "p11_item_count BETWEEN 0 AND 100 AND "
            "p11_item_count<=candidate_universe_count",
            name=f"ck_{prefix}_counts",
        ),
    ]


def _format_table(name: str, prefix: str, key: str, version: str) -> Table:
    return Table(
        name,
        Base.metadata,
        _uuid(key, primary_key=True),
        _uuid("owner_user_id"),
        Column(version, Integer(), nullable=False),
        # Legacy assignment validates old rows without fabricating their xid.
        Column("created_transaction", _Xid8(), nullable=True),
        *_lifetime(prefix, legacy_version=version),
        _request_fk(prefix, key, ondelete="RESTRICT"),
        UniqueConstraint("owner_user_id", key, name=f"uq_{prefix}_owner"),
        UniqueConstraint("owner_user_id", key, version, name=f"uq_{prefix}_owner_version"),
        CheckConstraint(f"{version} IN (2,3)", name=f"ck_{prefix}_version"),
        CheckConstraint(
            f"{version}=2 OR created_transaction IS NOT NULL", name=f"ck_{prefix}_origin_xid"
        ),
        Index(f"ix_{prefix}_owner_expiry", "owner_user_id", "expires_at", key),
        schema="ml",
    )


_original_format = _format_table(
    "native_original_capture_format",
    "native_original_format",
    "original_request_id",
    "original_capture_format_version",
)
_served_format = _format_table(
    "native_served_request_hash_format",
    "native_hash_format",
    "served_request_id",
    "request_hash_format_version",
)

_capture = Table(
    "native_original_capture_v3",
    Base.metadata,
    _uuid("original_request_id", primary_key=True),
    _uuid("owner_user_id"),
    Column("original_capture_format_version", Integer(), nullable=False),
    Column("source_codec", Text(), nullable=False),
    *_codec_hash("native_capture_v3"),
    Column("bundle_document", BYTEA(), nullable=False),
    *_hashes(
        "native_capture_v3",
        "bundle_sha256",
        "request_sha256",
        "pipeline_manifest_sha256",
        "baseline_snapshot_sha256",
        "temporal_snapshot_sha256",
        "candidate_membership_sha256",
        "original_ranking_sha256",
        "feature_policy_sha256",
        "eligibility_policy_sha256",
        "consent_receipt_sha256",
    ),
    _uuid("baseline_snapshot_id"),
    _uuid("temporal_snapshot_id"),
    Column("consent_generation", BigInteger(), nullable=False),
    Column("cutoff_at_ms", BigInteger(), nullable=False),
    Column("interaction_watermark", BigInteger(), nullable=False),
    Column("native_interaction_watermark", BigInteger(), nullable=False),
    *_counts("native_capture_v3"),
    Column("source_event_count", Integer(), nullable=False),
    Column("source_evidence_count", Integer(), nullable=False),
    Column("profile_dimension_count", Integer(), nullable=False),
    Column("ineligibility_reason", Text(), nullable=True),
    *_length("native_capture_v3", "source_index_byte_length", minimum=0),
    Column("created_transaction", _Xid8(), nullable=False),
    *_lifetime("native_capture_v3"),
    _request_fk("native_capture_v3", "original_request_id", ondelete="CASCADE"),
    _baseline_fk("native_capture_v3"),
    ForeignKeyConstraint(
        ["owner_user_id", "original_request_id", "original_capture_format_version"],
        [
            "ml.native_original_capture_format.owner_user_id",
            "ml.native_original_capture_format.original_request_id",
            "ml.native_original_capture_format.original_capture_format_version",
        ],
        name="fk_native_capture_v3_format",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("owner_user_id", "original_request_id", name="uq_native_capture_v3_owner"),
    CheckConstraint(
        f"original_capture_format_version=3 AND source_codec='{_SOURCE_CODEC}'",
        name="ck_native_capture_v3_format",
    ),
    CheckConstraint(
        f"consent_generation BETWEEN 1 AND {_SAFE53} AND "
        f"cutoff_at_ms BETWEEN 0 AND {_SAFE53} AND "
        f"interaction_watermark BETWEEN 0 AND {_SAFE53} AND "
        f"native_interaction_watermark BETWEEN 0 AND {_SAFE53}",
        name="ck_native_capture_v3_counters",
    ),
    CheckConstraint(
        "source_event_count BETWEEN 0 AND 10000 AND "
        "source_evidence_count BETWEEN 0 AND least(2*source_event_count,10000) AND "
        "profile_dimension_count BETWEEN 0 AND 512",
        name="ck_native_capture_v3_temporal_counts",
    ),
    CheckConstraint(
        "(candidate_universe_count<=1024 AND ineligibility_reason IS NULL) OR "
        "(candidate_universe_count>1024 AND ineligibility_reason IS NOT NULL AND "
        "ineligibility_reason='UNIVERSE_OVER_CAP')",
        name="ck_native_capture_v3_eligibility",
    ),
    CheckConstraint(
        f"octet_length(bundle_document) BETWEEN 1 AND {_DOCUMENT_LIMIT}",
        name="ck_native_capture_v3_document_length",
    ),
    CheckConstraint(
        "bundle_sha256=sha256(bundle_document)", name="ck_native_capture_v3_document_hash"
    ),
    Index(
        "ix_native_capture_v3_owner_expiry", "owner_user_id", "expires_at", "original_request_id"
    ),
    schema="ml",
)

_source = Table(
    "native_capture_source_event_v3",
    Base.metadata,
    _uuid("original_request_id", primary_key=True),
    _uuid("source_event_id", primary_key=True),
    _uuid("owner_user_id"),
    *_hashes("native_source_v3", "source_request_sha256", "normalized_payload_sha256"),
    _uuid("device_id"),
    _uuid("journal_epoch"),
    Column("device_sequence", BigInteger(), nullable=False),
    Column("server_sequence", BigInteger(), nullable=False),
    Column("event_type", Text(), nullable=False),
    _uuid("association_id", nullable=True),
    _uuid("causal_request_id", nullable=True),
    _uuid("impression_event_id", nullable=True),
    Column("retained_until", _TIME, nullable=False),
    _bundle_fk("native_source_v3"),
    CheckConstraint(
        f"device_sequence BETWEEN 1 AND {_SAFE53} AND server_sequence BETWEEN 1 AND {_SAFE53}",
        name="ck_native_source_v3_sequences",
    ),
    CheckConstraint(
        "event_type IN ('LISTENING_EVENT_RECORDED','RECOMMENDATION_FEEDBACK_RECORDED',"
        "'RECOMMENDATION_IMPRESSION_RECORDED')",
        name="ck_native_source_v3_type",
    ),
    CheckConstraint(
        "(event_type='LISTENING_EVENT_RECORDED' AND "
        "((association_id IS NULL AND causal_request_id IS NULL AND impression_event_id IS NULL) "
        "OR (association_id IS NOT NULL AND causal_request_id IS NOT NULL AND "
        "impression_event_id IS NOT NULL))) OR "
        "(event_type='RECOMMENDATION_IMPRESSION_RECORDED' AND association_id IS NOT NULL "
        "AND causal_request_id IS NOT NULL AND impression_event_id IS NULL) OR "
        "(event_type='RECOMMENDATION_FEEDBACK_RECORDED' AND association_id IS NOT NULL "
        "AND causal_request_id IS NOT NULL AND impression_event_id IS NOT NULL)",
        name="ck_native_source_v3_causality",
    ),
    Index(
        "ix_native_source_v3_owner_event", "owner_user_id", "source_event_id", "original_request_id"
    ),
    Index(
        "ix_native_source_v3_causal", "owner_user_id", "causal_request_id", "original_request_id"
    ),
    Index(
        "ix_native_source_v3_parent", "owner_user_id", "impression_event_id", "original_request_id"
    ),
    schema="ml",
)

_cursor = Table(
    "native_capture_lineage_cursor_v3",
    Base.metadata,
    _uuid("original_request_id", primary_key=True),
    _uuid("owner_user_id"),
    Column("state", Text(), nullable=False),
    Column("source_event_count", Integer(), nullable=False),
    *_hashes("native_cursor_v3", "source_membership_sha256"),
    Column("cutoff_at_ms", BigInteger(), nullable=False),
    Column("native_interaction_watermark", BigInteger(), nullable=False),
    Column("source_generation", BigInteger(), nullable=False),
    *_length("native_cursor_v3", "source_cursor_byte_length"),
    Column("created_transaction", _Xid8(), nullable=False),
    *_lifetime("native_cursor_v3"),
    _bundle_fk("native_cursor_v3"),
    CheckConstraint("state='ACTIVE'", name="ck_native_cursor_v3_state"),
    CheckConstraint("source_event_count BETWEEN 0 AND 10000", name="ck_native_cursor_v3_count"),
    CheckConstraint(
        f"cutoff_at_ms BETWEEN 0 AND {_SAFE53} AND "
        f"native_interaction_watermark BETWEEN 0 AND {_SAFE53} AND "
        f"source_generation BETWEEN 1 AND {_SAFE53}",
        name="ck_native_cursor_v3_counters",
    ),
    Index("ix_native_cursor_v3_owner_expiry", "owner_user_id", "expires_at", "original_request_id"),
    schema="ml",
)

_replay = Table(
    "native_capture_replay_v3",
    Base.metadata,
    _uuid("original_request_id", primary_key=True),
    _uuid("owner_user_id"),
    Column("request_document", BYTEA(), nullable=False),
    # Independently serialized original data; connected framing carries no duplicate.
    Column("baseline_document", BYTEA(), nullable=False),
    Column("ranking_document", BYTEA(), nullable=False),
    # Independent numeric R1 profile and manifest, produced before source encoding.
    Column("original_profile_document", BYTEA(), nullable=False),
    Column("pipeline_document", BYTEA(), nullable=False),
    *_hashes(
        "native_replay_v3",
        "request_sha256",
        "original_ranking_sha256",
        "baseline_snapshot_sha256",
        "temporal_snapshot_sha256",
        "original_profile_document_sha256",
        "original_profile_sha256",
        "original_profile_binary64_sha256",
        "pipeline_document_sha256",
    ),
    *_length("native_replay_v3", "original_profile_byte_length"),
    *_length("native_replay_v3", "original_ranking_byte_length"),
    *_length("native_replay_v3", "baseline_byte_length", maximum=MAX_ORIGINAL_BASELINE_V4_BYTES),
    Column(
        "baseline_storage_version",
        Integer(),
        Computed(f"CASE WHEN baseline_byte_length <= {_DOCUMENT_LIMIT} THEN 3 ELSE 4 END"),
        nullable=False,
    ),
    *_length("native_replay_v3", "original_items_byte_length", minimum=0),
    _uuid("baseline_snapshot_id"),
    _uuid("temporal_snapshot_id"),
    Column("original_item_count", Integer(), nullable=False),
    Column("created_transaction", _Xid8(), nullable=False),
    *_lifetime("native_replay_v3"),
    _bundle_fk("native_replay_v3"),
    _baseline_fk("native_replay_v3"),
    CheckConstraint(
        f"octet_length(request_document) BETWEEN 1 AND {_DOCUMENT_LIMIT}",
        name="ck_native_replay_v3_request_length",
    ),
    CheckConstraint(
        "request_sha256=sha256(request_document)", name="ck_native_replay_v3_request_hash"
    ),
    CheckConstraint(
        f"octet_length(baseline_document) BETWEEN 1 AND {MAX_ORIGINAL_BASELINE_V4_BYTES} AND "
        "baseline_byte_length=octet_length(baseline_document)",
        name="ck_native_replay_v3_baseline_length",
    ),
    CheckConstraint(
        "baseline_snapshot_sha256=sha256(baseline_document)",
        name="ck_native_replay_v3_baseline_hash",
    ),
    CheckConstraint(
        f"octet_length(ranking_document) BETWEEN 1 AND {_DOCUMENT_LIMIT} AND "
        "original_ranking_byte_length=octet_length(ranking_document)",
        name="ck_native_replay_v3_ranking_length",
    ),
    CheckConstraint(
        "original_ranking_sha256=sha256(ranking_document)",
        name="ck_native_replay_v3_ranking_hash",
    ),
    CheckConstraint("original_item_count BETWEEN 0 AND 100", name="ck_native_replay_v3_count"),
    CheckConstraint(
        f"octet_length(original_profile_document) BETWEEN 1 AND {_DOCUMENT_LIMIT} AND "
        "original_profile_byte_length=octet_length(original_profile_document)",
        name="ck_native_replay_v3_profile_length",
    ),
    CheckConstraint(
        "original_profile_document_sha256=sha256(original_profile_document)",
        name="ck_native_replay_v3_profile_document_hash",
    ),
    CheckConstraint(
        "octet_length(pipeline_document) BETWEEN 1 AND 65536",
        name="ck_native_replay_v3_pipeline_length",
    ),
    CheckConstraint(
        "pipeline_document_sha256=sha256(pipeline_document)",
        name="ck_native_replay_v3_pipeline_document_hash",
    ),
    Index("ix_native_replay_v3_owner_expiry", "owner_user_id", "expires_at", "original_request_id"),
    schema="ml",
)

_root = Table(
    "recommendation_request_root_v3",
    Base.metadata,
    _uuid("served_request_id", primary_key=True),
    _uuid("owner_user_id"),
    Column("request_hash_format_version", Integer(), nullable=False),
    Column("root_document", BYTEA(), nullable=False),
    *_codec_hash("native_root_v3"),
    *_hashes(
        "native_root_v3",
        "root_proof_sha256",
        "request_root_sha256",
        "input_origin_sha256",
        "original_request_sha256",
        "original_ranking_sha256",
        "capture_sha256",
        "eligibility_policy_sha256",
        "baseline_snapshot_sha256",
        "temporal_snapshot_sha256",
        "candidate_membership_sha256",
        "original_policy_snapshot_sha256",
        "original_availability_snapshot_sha256",
        "consent_receipt_sha256",
    ),
    # Historical origin survives live source withdrawal and original-row purge.
    _uuid("original_request_id"),
    Column("original_capture_format_version", Integer(), nullable=False),
    _uuid("baseline_snapshot_id"),
    _uuid("temporal_snapshot_id"),
    Column("consent_generation", BigInteger(), nullable=False),
    _uuid("device_id"),
    _uuid("journal_epoch"),
    _uuid("server_instance_id"),
    Column("identity_epoch", BigInteger(), nullable=False),
    Column("effective_limit", Integer(), nullable=False),
    Column("endpoint_max", Integer(), nullable=False),
    Column("exploration_units_e8", Integer(), nullable=False),
    Column("pipeline_byte_length", Integer(), nullable=False),
    Column("cutoff_at_ms", BigInteger(), nullable=False),
    *_counts("native_root_v3"),
    Column("served_item_count", Integer(), nullable=False),
    *_length("native_root_v3", "served_items_byte_length", minimum=0),
    Column("created_transaction", _Xid8(), nullable=False),
    *_lifetime("native_root_v3"),
    _uuid("referenced_snapshot_id", nullable=True),
    _baseline_fk("native_root_v4", "referenced_snapshot_id"),
    CheckConstraint(
        "referenced_snapshot_id IS NULL OR referenced_snapshot_id=baseline_snapshot_id",
        name="ck_native_root_v4_snapshot_reference",
    ),
    _request_fk("native_root_v3", "served_request_id", ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["owner_user_id", "served_request_id", "request_hash_format_version"],
        [
            "ml.native_served_request_hash_format.owner_user_id",
            "ml.native_served_request_hash_format.served_request_id",
            "ml.native_served_request_hash_format.request_hash_format_version",
        ],
        name="fk_native_root_v3_format",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["owner_user_id", "device_id"],
        ["account.device.user_id", "account.device.device_id"],
        name="fk_native_root_v3_device_owner",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["server_instance_id"],
        ["account.server_instance.server_instance_id"],
        name="fk_native_root_v3_server",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("owner_user_id", "served_request_id", name="uq_native_root_v3_owner"),
    UniqueConstraint(
        "owner_user_id", "served_request_id", "original_request_id", name="uq_native_root_v3_origin"
    ),
    CheckConstraint(
        "request_hash_format_version=3 AND original_capture_format_version=3 AND "
        "original_request_id<>served_request_id",
        name="ck_native_root_v3_format",
    ),
    CheckConstraint(
        f"octet_length(root_document) BETWEEN 1 AND {_DOCUMENT_LIMIT}",
        name="ck_native_root_v3_document_length",
    ),
    CheckConstraint(
        "root_proof_sha256=sha256(root_document)", name="ck_native_root_v3_document_hash"
    ),
    CheckConstraint(
        f"identity_epoch>=1 AND consent_generation BETWEEN 1 AND {_SAFE53} AND "
        f"cutoff_at_ms BETWEEN 0 AND {_SAFE53}",
        name="ck_native_root_v3_counters",
    ),
    CheckConstraint(
        "effective_limit BETWEEN 1 AND 100 AND endpoint_max=100 AND "
        "p11_item_count<=effective_limit AND served_item_count=p11_item_count",
        name="ck_native_root_v3_served_counts",
    ),
    CheckConstraint(
        "exploration_units_e8 BETWEEN 0 AND 100000000 AND pipeline_byte_length BETWEEN 1 AND 65536",
        name="ck_native_root_v3_pipeline_bounds",
    ),
    Index("ix_native_root_v3_owner_expiry", "owner_user_id", "expires_at", "served_request_id"),
    schema="ml",
)

_connection = Table(
    "native_atomic_capture_connection_v3",
    Base.metadata,
    _uuid("original_request_id", primary_key=True),
    _uuid("owner_user_id"),
    _uuid("served_request_id"),
    Column("original_capture_format_version", Integer(), nullable=False),
    Column("request_hash_format_version", Integer(), nullable=False),
    *_codec_hash("native_connection_v3"),
    *_hashes(
        "native_connection_v3",
        "input_origin_sha256",
        "root_proof_sha256",
        "request_root_sha256",
        "decision_sha256",
        "connection_seal_sha256",
        "capture_sha256",
        "eligibility_policy_sha256",
        "consent_receipt_sha256",
    ),
    Column("consent_generation", BigInteger(), nullable=False),
    Column("created_transaction", _Xid8(), nullable=False),
    *_lifetime("native_connection_v3"),
    _bundle_fk("native_connection_v3"),
    ForeignKeyConstraint(
        ["owner_user_id", "served_request_id", "original_request_id"],
        [
            "ml.recommendation_request_root_v3.owner_user_id",
            "ml.recommendation_request_root_v3.served_request_id",
            "ml.recommendation_request_root_v3.original_request_id",
        ],
        name="fk_native_connection_v3_root_origin",
        ondelete="CASCADE",
    ),
    UniqueConstraint("served_request_id", name="uq_native_connection_v3_served"),
    CheckConstraint(
        "original_capture_format_version=3 AND request_hash_format_version=3 AND "
        "original_request_id<>served_request_id",
        name="ck_native_connection_v3_format",
    ),
    CheckConstraint(
        f"consent_generation BETWEEN 1 AND {_SAFE53}", name="ck_native_connection_v3_consent"
    ),
    Index(
        "ix_native_connection_v3_owner_expiry", "owner_user_id", "expires_at", "original_request_id"
    ),
    schema="ml",
)


class NativeOriginalCaptureFormatRow(Base):
    __table__ = _original_format
    original_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    original_capture_format_version: Mapped[int]
    created_transaction: Mapped[str | None]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class NativeServedRequestHashFormatRow(Base):
    __table__ = _served_format
    served_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    request_hash_format_version: Mapped[int]
    created_transaction: Mapped[str | None]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class NativeOriginalCaptureV3Row(Base):
    __table__ = _capture
    original_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    original_capture_format_version: Mapped[int]
    source_codec: Mapped[str]
    source_codec_sha256: Mapped[bytes]
    bundle_document: Mapped[bytes]
    bundle_sha256: Mapped[bytes]
    request_sha256: Mapped[bytes]
    pipeline_manifest_sha256: Mapped[bytes]
    baseline_snapshot_sha256: Mapped[bytes]
    temporal_snapshot_sha256: Mapped[bytes]
    candidate_membership_sha256: Mapped[bytes]
    original_ranking_sha256: Mapped[bytes]
    feature_policy_sha256: Mapped[bytes]
    eligibility_policy_sha256: Mapped[bytes]
    consent_receipt_sha256: Mapped[bytes]
    baseline_snapshot_id: Mapped[UUID]
    temporal_snapshot_id: Mapped[UUID]
    consent_generation: Mapped[int]
    cutoff_at_ms: Mapped[int]
    interaction_watermark: Mapped[int]
    native_interaction_watermark: Mapped[int]
    source_snapshot_track_count: Mapped[int]
    candidate_universe_count: Mapped[int]
    p11_item_count: Mapped[int]
    source_event_count: Mapped[int]
    source_evidence_count: Mapped[int]
    profile_dimension_count: Mapped[int]
    ineligibility_reason: Mapped[str | None]
    source_index_byte_length: Mapped[int]
    created_transaction: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class NativeCaptureSourceEventV3Row(Base):
    __table__ = _source
    original_request_id: Mapped[UUID]
    source_event_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    source_request_sha256: Mapped[bytes]
    normalized_payload_sha256: Mapped[bytes]
    device_id: Mapped[UUID]
    journal_epoch: Mapped[UUID]
    device_sequence: Mapped[int]
    server_sequence: Mapped[int]
    event_type: Mapped[str]
    association_id: Mapped[UUID | None]
    causal_request_id: Mapped[UUID | None]
    impression_event_id: Mapped[UUID | None]
    retained_until: Mapped[datetime]


class NativeCaptureLineageCursorV3Row(Base):
    __table__ = _cursor
    original_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    state: Mapped[str]
    source_event_count: Mapped[int]
    source_membership_sha256: Mapped[bytes]
    cutoff_at_ms: Mapped[int]
    native_interaction_watermark: Mapped[int]
    source_generation: Mapped[int]
    source_cursor_byte_length: Mapped[int]
    created_transaction: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class NativeCaptureReplayV3Row(Base):
    __table__ = _replay
    original_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    request_document: Mapped[bytes]
    baseline_document: Mapped[bytes]
    baseline_storage_version: Mapped[int]
    ranking_document: Mapped[bytes]
    original_profile_document: Mapped[bytes]
    original_profile_document_sha256: Mapped[bytes]
    original_profile_sha256: Mapped[bytes]
    original_profile_binary64_sha256: Mapped[bytes]
    original_profile_byte_length: Mapped[int]
    pipeline_document: Mapped[bytes]
    pipeline_document_sha256: Mapped[bytes]
    request_sha256: Mapped[bytes]
    original_ranking_sha256: Mapped[bytes]
    baseline_snapshot_sha256: Mapped[bytes]
    temporal_snapshot_sha256: Mapped[bytes]
    original_ranking_byte_length: Mapped[int]
    baseline_byte_length: Mapped[int]
    original_items_byte_length: Mapped[int]
    baseline_snapshot_id: Mapped[UUID]
    temporal_snapshot_id: Mapped[UUID]
    original_item_count: Mapped[int]
    created_transaction: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class RecommendationRequestRootV3Row(Base):
    __table__ = _root
    referenced_snapshot_id: Mapped[UUID | None]
    served_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    request_hash_format_version: Mapped[int]
    root_document: Mapped[bytes]
    source_codec_sha256: Mapped[bytes]
    root_proof_sha256: Mapped[bytes]
    request_root_sha256: Mapped[bytes]
    input_origin_sha256: Mapped[bytes]
    original_request_sha256: Mapped[bytes]
    original_ranking_sha256: Mapped[bytes]
    capture_sha256: Mapped[bytes]
    eligibility_policy_sha256: Mapped[bytes]
    baseline_snapshot_sha256: Mapped[bytes]
    temporal_snapshot_sha256: Mapped[bytes]
    candidate_membership_sha256: Mapped[bytes]
    original_policy_snapshot_sha256: Mapped[bytes]
    original_availability_snapshot_sha256: Mapped[bytes]
    consent_receipt_sha256: Mapped[bytes]
    original_request_id: Mapped[UUID]
    original_capture_format_version: Mapped[int]
    baseline_snapshot_id: Mapped[UUID]
    temporal_snapshot_id: Mapped[UUID]
    consent_generation: Mapped[int]
    device_id: Mapped[UUID]
    journal_epoch: Mapped[UUID]
    server_instance_id: Mapped[UUID]
    identity_epoch: Mapped[int]
    effective_limit: Mapped[int]
    endpoint_max: Mapped[int]
    exploration_units_e8: Mapped[int]
    pipeline_byte_length: Mapped[int]
    cutoff_at_ms: Mapped[int]
    source_snapshot_track_count: Mapped[int]
    candidate_universe_count: Mapped[int]
    p11_item_count: Mapped[int]
    served_item_count: Mapped[int]
    served_items_byte_length: Mapped[int]
    created_transaction: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class NativeAtomicCaptureConnectionV3Row(Base):
    __table__ = _connection
    original_request_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    served_request_id: Mapped[UUID]
    original_capture_format_version: Mapped[int]
    request_hash_format_version: Mapped[int]
    source_codec_sha256: Mapped[bytes]
    input_origin_sha256: Mapped[bytes]
    root_proof_sha256: Mapped[bytes]
    request_root_sha256: Mapped[bytes]
    decision_sha256: Mapped[bytes]
    connection_seal_sha256: Mapped[bytes]
    capture_sha256: Mapped[bytes]
    eligibility_policy_sha256: Mapped[bytes]
    consent_receipt_sha256: Mapped[bytes]
    consent_generation: Mapped[int]
    created_transaction: Mapped[str]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]


NATIVE_REQUEST_V3_TABLES: tuple[Table, ...] = (
    _original_format,
    _served_format,
    _capture,
    _source,
    _cursor,
    _replay,
    _root,
    _connection,
)

__all__ = (
    "NATIVE_REQUEST_V3_TABLES",
    "NativeAtomicCaptureConnectionV3Row",
    "NativeCaptureLineageCursorV3Row",
    "NativeCaptureReplayV3Row",
    "NativeCaptureSourceEventV3Row",
    "NativeOriginalCaptureFormatRow",
    "NativeOriginalCaptureV3Row",
    "NativeServedRequestHashFormatRow",
    "RecommendationRequestRootV3Row",
)
