"""Exact admitted original source BYTEAs, without decoding or SQL authority.

The owning SQL reader must admit the complete dependency closure first. Fetch
these six source values before fetch_native_p11_root_v3: that final root fetch
consumes the reader-local active-head pin. All row locks are already held in
the owner's canonical order by the head/discovery reader.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.native_served_authority_v3 import _transaction_binding
from autplay.domain.native_connected_budget_v3 import NativeConnectedDependencyAdmissionV3
from autplay.domain.native_connected_request_v3 import (
    NativeConnectedDependencyKindV3,
    NativeConnectedV3Error,
    NativeP11UnvalidatedHeadV3,
)
from autplay.domain.native_original_capture_v3 import NATIVE_SOURCE3_CODEC

# Only closed identifiers enter SQL; callers cannot supply a column or table.
_PROJECTIONS: Final = {
    NativeConnectedDependencyKindV3.CAPTURE: (
        "b.bundle_document",
        "b.bundle_sha256",
        "capture_byte_length",
        "capture_sha256",
    ),
    NativeConnectedDependencyKindV3.ORIGINAL_REQUEST: (
        "p.request_document",
        "p.request_sha256",
        "original_request_byte_length",
        "original_request_sha256",
    ),
    NativeConnectedDependencyKindV3.ORIGINAL_BASELINE: (
        "p.baseline_document",
        "p.baseline_snapshot_sha256",
        "baseline_byte_length",
        "baseline_snapshot_sha256",
    ),
    NativeConnectedDependencyKindV3.ORIGINAL_RANKING: (
        "p.ranking_document",
        "p.original_ranking_sha256",
        "original_ranking_byte_length",
        "original_ranking_sha256",
    ),
    NativeConnectedDependencyKindV3.ORIGINAL_NUMERIC_PROFILE: (
        "p.original_profile_document",
        "p.original_profile_document_sha256",
        "original_profile_byte_length",
        "original_profile_document_sha256",
    ),
    NativeConnectedDependencyKindV3.PIPELINE: (
        "p.pipeline_document",
        "p.pipeline_document_sha256",
        "pipeline_byte_length",
        "pipeline_manifest_sha256",
    ),
}

_ORIGIN_SQL: Final = """
FROM ml.recommendation_request_root_v3 r
JOIN ml.native_atomic_capture_connection_v3 c
 ON c.owner_user_id=r.owner_user_id AND c.served_request_id=r.served_request_id
 AND c.original_request_id=r.original_request_id
JOIN ml.native_original_capture_v3 b
 ON b.owner_user_id=c.owner_user_id AND b.original_request_id=c.original_request_id
JOIN ml.native_capture_replay_v3 p
 ON p.owner_user_id=b.owner_user_id AND p.original_request_id=b.original_request_id
JOIN ml.native_capture_lineage_cursor_v3 k
 ON k.owner_user_id=b.owner_user_id AND k.original_request_id=b.original_request_id
JOIN ml.native_original_capture_format ofmt
 ON ofmt.owner_user_id=b.owner_user_id AND ofmt.original_request_id=b.original_request_id
JOIN ml.native_served_request_hash_format f
 ON f.owner_user_id=r.owner_user_id AND f.served_request_id=r.served_request_id
WHERE r.owner_user_id=:owner AND r.served_request_id=:served
 AND r.original_request_id=:original
 AND r.request_hash_format_version=3 AND r.original_capture_format_version=3
 AND c.request_hash_format_version=3 AND c.original_capture_format_version=3
 AND b.original_capture_format_version=3
 AND f.request_hash_format_version=3 AND ofmt.original_capture_format_version=3
 AND r.created_transaction::text=:created_transaction
 AND c.created_transaction::text=:created_transaction
 AND b.created_transaction::text=:created_transaction
 AND p.created_transaction::text=:created_transaction
 AND k.created_transaction::text=:created_transaction
 AND f.created_transaction::text=:created_transaction
 AND ofmt.created_transaction::text=:created_transaction
 AND k.state='ACTIVE' AND k.source_generation=:source_generation
 AND b.source_codec=:source_codec AND b.source_codec_sha256=:source_codec_sha
 AND r.source_codec_sha256=b.source_codec_sha256
 AND c.source_codec_sha256=b.source_codec_sha256
 AND b.bundle_sha256=:capture_sha AND c.capture_sha256=b.bundle_sha256
 AND r.capture_sha256=b.bundle_sha256
 AND p.request_sha256=:request_sha AND b.request_sha256=p.request_sha256
 AND r.original_request_sha256=p.request_sha256
 AND p.baseline_snapshot_id=:baseline AND b.baseline_snapshot_id=p.baseline_snapshot_id
 AND r.baseline_snapshot_id=p.baseline_snapshot_id
 AND p.baseline_snapshot_sha256=:baseline_sha
 AND b.baseline_snapshot_sha256=p.baseline_snapshot_sha256
 AND r.baseline_snapshot_sha256=p.baseline_snapshot_sha256
 AND p.original_ranking_sha256=:ranking_sha
 AND b.original_ranking_sha256=p.original_ranking_sha256
 AND r.original_ranking_sha256=p.original_ranking_sha256
 AND p.original_profile_document_sha256=:profile_document_sha
 AND p.original_profile_sha256=:profile_sha
 AND p.original_profile_binary64_sha256=:profile_binary64_sha
 AND p.pipeline_document_sha256=:pipeline_sha
 AND b.pipeline_manifest_sha256=p.pipeline_document_sha256
 AND r.root_proof_sha256=:root_sha AND c.root_proof_sha256=r.root_proof_sha256
 AND r.request_root_sha256=:request_root_sha
 AND c.request_root_sha256=r.request_root_sha256
 AND c.decision_sha256=:decision_sha AND c.connection_seal_sha256=:seal_sha
"""


def fetch_native_connected_origin_bytes_v3(
    session: Session,
    head: NativeP11UnvalidatedHeadV3,
    *,
    kind: NativeConnectedDependencyKindV3,
    admission: NativeConnectedDependencyAdmissionV3,
) -> bytes:
    """Read one immutable BYTEA under the current record's existing quota."""
    if (
        type(head) is not NativeP11UnvalidatedHeadV3
        or type(admission) is not NativeConnectedDependencyAdmissionV3
        or type(kind) is not NativeConnectedDependencyKindV3
        or kind not in _PROJECTIONS
    ):
        raise NativeConnectedV3Error("CONNECTED_V3_SOURCE_FETCH_ADMISSION")
    if kind is NativeConnectedDependencyKindV3.PIPELINE and head.pipeline_byte_length > 65_536:
        raise NativeConnectedV3Error("CONNECTED_V3_SOURCE_FETCH_PIPELINE_BYTES")
    column, hash_column, length_field, hash_field = _PROJECTIONS[kind]
    size: int = getattr(head, length_field)
    expected_sha: bytes = getattr(head, hash_field)
    try:
        # These small driver results also consume the same existing admission.
        admission.charge(bytes_read=36, node_visits=4)
        transaction = _transaction_binding(session)
        if session.info.get("native_v3_active_head") != (transaction, id(head)):
            raise NativeConnectedV3Error("CONNECTED_V3_SOURCE_FETCH_HEAD_PIN")
        admission.authorize_origin_fetch(head, kind, transaction_binding=transaction)
        # Consume the read before the query can allocate its bounded BYTEA.
        admission.charge(bytes_read=size, node_visits=1)
        raw = session.scalar(
            text(
                f"SELECT {column}\n{_ORIGIN_SQL}\n"
                f" AND {hash_column}=:sha AND octet_length({column})=:size\n"
                "FOR UPDATE OF r,c,b,p,k,ofmt,f"
            ),
            {
                "owner": head.owner_user_id,
                "served": head.served_request_id,
                "original": head.original_request_id,
                "created_transaction": head.created_transaction,
                "source_generation": head.source_generation,
                "source_codec": NATIVE_SOURCE3_CODEC,
                "source_codec_sha": head.source_codec_sha256,
                "baseline": head.baseline_snapshot_id,
                "capture_sha": head.capture_sha256,
                "request_sha": head.original_request_sha256,
                "baseline_sha": head.baseline_snapshot_sha256,
                "ranking_sha": head.original_ranking_sha256,
                "profile_document_sha": head.original_profile_document_sha256,
                "profile_sha": head.original_profile_sha256,
                "profile_binary64_sha": head.original_profile_binary64_sha256,
                "pipeline_sha": head.pipeline_manifest_sha256,
                "root_sha": head.root_proof_sha256,
                "request_root_sha": head.request_root_sha256,
                "decision_sha": head.decision_sha256,
                "seal_sha": head.connection_seal_sha256,
                "sha": expected_sha,
                "size": size,
            },
        )
        if type(raw) is not bytes:
            raise NativeConnectedV3Error("CONNECTED_V3_SOURCE_FETCH_UNAVAILABLE")
        admission.bind_bytes(raw)
        return raw
    except SQLAlchemyError:
        raise NativeConnectedV3Error("CONNECTED_V3_SOURCE_FETCH_REFUSED") from None
