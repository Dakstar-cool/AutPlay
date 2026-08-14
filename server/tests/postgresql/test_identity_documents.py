"""Canonical JSON, privacy, cleanup, and durable explanation boundaries."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import psycopg
import pytest
import rfc8785
from autplay.application.identity_evidence import (
    MAX_IDENTITY_DOCUMENT_BYTES,
    IdentityDocumentError,
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
)
from psycopg import Connection
from psycopg.types.json import Jsonb

from .identity_factory import (
    append_evaluation,
    force_deferred_constraints,
    insert_release_set,
    insert_world,
    make_candidate,
)


def _candidate_document(
    *, features: int = 1, origins: int = 1, conflicts: int = 0
) -> dict[str, Any]:
    return {
        "recording_id": str(uuid.uuid4()),
        "raw_score": 0.8,
        "confidence": 0.75,
        "evidence_tier": "T0",
        "feature_scores": [{"feature": f"f-{index}", "score": 0.5} for index in range(features)],
        "hard_conflicts": [{"code": f"c-{index}"} for index in range(conflicts)],
        "candidate_origins": [{"kind": "fixture", "id": index} for index in range(origins)],
        "extractor_versions": {"metadata": "1"},
    }


def test_rfc8785_bytes_and_sha256_are_deterministic() -> None:
    left = canonical_query_snapshot(
        {"duration_ms": 180000, "normalized_title": "café", "market_scope": "GLOBAL"}
    )
    right = canonical_query_snapshot(
        {"market_scope": "GLOBAL", "normalized_title": "café", "duration_ms": 180000}
    )
    expected = rfc8785.dumps(
        {"duration_ms": 180000, "normalized_title": "café", "market_scope": "GLOBAL"}
    )
    assert left.canonical_bytes == right.canonical_bytes == expected
    assert left.sha256 == right.sha256 == hashlib.sha256(expected).digest()


@pytest.mark.parametrize("delta", (-1, 0, 1))
def test_exact_canonical_document_byte_boundary(delta: int) -> None:
    overhead = len(rfc8785.dumps({"normalized_title": ""}))
    target = MAX_IDENTITY_DOCUMENT_BYTES + delta
    value = {"normalized_title": "x" * (target - overhead)}
    if delta <= 0:
        document = canonical_query_snapshot(value)
        assert document.byte_size == target
    else:
        with pytest.raises(IdentityDocumentError, match="exceeds 128 KiB"):
            canonical_query_snapshot(value)


@pytest.mark.parametrize(
    ("field", "limit"),
    (("feature_scores", 256), ("candidate_origins", 256), ("hard_conflicts", 64)),
)
def test_candidate_array_cardinality_boundary(field: str, limit: int) -> None:
    counts = {"features": 1, "origins": 1, "conflicts": 0}
    argument = {
        "feature_scores": "features",
        "candidate_origins": "origins",
        "hard_conflicts": "conflicts",
    }[field]
    counts[argument] = limit
    canonical_candidate_evidence(_candidate_document(**counts))
    counts[argument] = limit + 1
    with pytest.raises(IdentityDocumentError, match=f"{field} exceeds {limit} entries"):
        canonical_candidate_evidence(_candidate_document(**counts))


@pytest.mark.parametrize(
    "sensitive_key",
    (
        "access_token",
        "authToken",
        "refresh-token",
        "password",
        "privateUrl",
        "rawPath",
        "source-uri",
        "providerPayload",
        "raw_payload",
        "credential",
    ),
)
def test_nested_sensitive_fields_are_rejected_in_identity_documents(
    sensitive_key: str,
) -> None:
    document = _candidate_document()
    origins = document["candidate_origins"]
    assert isinstance(origins, list)
    origins.append({"nested": {sensitive_key: "must-not-persist"}})
    with pytest.raises(IdentityDocumentError, match="is sensitive"):
        canonical_candidate_evidence(document)


@pytest.mark.parametrize("unknown", ("provider_blob", "future_field", "opaque_payload"))
def test_unknown_top_level_fields_are_rejected(unknown: str) -> None:
    with pytest.raises(IdentityDocumentError, match="not allowed by schema v1"):
        canonical_query_snapshot({"normalized_title": "safe", unknown: "not-safe"})


def test_candidate_aggregate_hash_is_rank_ordered_and_contiguous() -> None:
    hashes = [(1, b"a" * 32), (2, b"b" * 32)]
    digest, stream_size = candidate_aggregate_sha256(hashes)
    assert stream_size == 72
    assert (
        digest
        == hashlib.sha256(
            (1).to_bytes(4, "big", signed=True)
            + b"a" * 32
            + (2).to_bytes(4, "big", signed=True)
            + b"b" * 32
        ).digest()
    )
    with pytest.raises(IdentityDocumentError, match="contiguous"):
        candidate_aggregate_sha256([(1, b"a" * 32), (3, b"b" * 32)])
    with pytest.raises(IdentityDocumentError, match="32 bytes"):
        candidate_aggregate_sha256([(1, b"short")])


def test_database_json_shape_cardinality_and_hash_lengths(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    oversized_features: list[Any] = [{"feature": str(index)} for index in range(257)]
    with pytest.raises(IdentityDocumentError, match="exceeds 256"):
        make_candidate(
            world.seed_recording_id,
            1,
            releases,
            feature_scores=oversized_features,
        )
    candidate = make_candidate(world.seed_recording_id, 1, releases)
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        [],
        state="NO_MATCH",
        execution_mode="APPLIED",
    )
    with (
        pytest.raises(psycopg.errors.CheckViolation) as hash_exc,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO identity.match_candidate_evidence (
                decision_id, recording_id, rank, raw_score, confidence,
                evidence_tier, feature_scores, hard_conflicts, candidate_origins,
                extractor_versions, evidence_schema_version, evidence_sha256,
                evidence_document_size_bytes
            ) VALUES (%s, %s, 1, 0.8, 0.8, 'T0', '[]', '[]', '[]', '{}', '1', %s, 2)
            """,
            (decision.decision_id, candidate.recording_id, b"x" * 31),
        )
    assert hash_exc.value.diag.constraint_name == "ck_match_candidate_evidence_hash_len"


def test_provider_independent_explanation_survives_provider_disable(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    candidate = make_candidate(
        world.seed_recording_id,
        1,
        releases,
        candidate_origins=[
            {"provider_key": "p02-provider", "opaque_evidence_id": "external:fixture"}
        ],
        feature_scores=[{"feature": "title", "score": 0.91, "extractor": "metadata:1"}],
    )
    decision = append_evaluation(
        database_connection,
        world.query("EXTERNAL_REFERENCE"),
        releases,
        [candidate],
    )
    force_deferred_constraints(database_connection)
    database_connection.execute(
        """
        UPDATE identity.source_provider
        SET enabled = false, deleted_at = now(), row_version = row_version + 1
        WHERE provider_id = %s
        """,
        (world.provider_id,),
    )
    stored = database_connection.execute(
        """
        SELECT d.query_snapshot, d.feature_scores, d.candidate_origins,
               e.feature_scores, e.candidate_origins, e.extractor_versions
        FROM identity.match_decision d
        JOIN identity.match_candidate_evidence e USING (decision_id)
        WHERE d.decision_id = %s AND e.rank = 1
        """,
        (decision.decision_id,),
    ).fetchone()
    assert stored is not None
    assert stored[1] == stored[3] == candidate.feature_scores
    assert stored[2] == stored[4] == candidate.candidate_origins
    assert stored[5] == releases.feature_extractor_versions


def test_import_raw_payload_cleanup_preserves_identity_envelope_and_restricts_delete(
    database_connection: Connection[Any],
) -> None:
    world = insert_world(database_connection)
    releases = insert_release_set(database_connection)
    database_connection.execute(
        "UPDATE importing.import_entry SET raw_payload = %s WHERE import_entry_id = %s",
        (
            Jsonb({"private_url": "https://secret.invalid", "provider_payload": "secret"}),
            world.import_entry_id,
        ),
    )
    decision = append_evaluation(
        database_connection,
        world.query("IMPORT_ENTRY"),
        releases,
        [make_candidate(world.seed_recording_id, 1, releases)],
        state="REVIEW_REQUIRED",
        execution_mode="SHADOW",
    )
    force_deferred_constraints(database_connection)
    database_connection.execute(
        "UPDATE importing.import_entry SET raw_payload = NULL WHERE import_entry_id = %s",
        (world.import_entry_id,),
    )
    envelope = database_connection.execute(
        """
        SELECT ie.raw_payload, md.query_snapshot_sha256,
               md.candidate_evidence_sha256, md.candidate_evidence_size_bytes
        FROM importing.import_entry ie
        JOIN identity.match_decision md ON md.import_entry_id = ie.import_entry_id
        WHERE md.decision_id = %s
        """,
        (decision.decision_id,),
    ).fetchone()
    assert envelope is not None
    assert envelope[0] is None
    assert envelope[1] is not None and len(envelope[1]) == 32
    assert envelope[2] is not None and len(envelope[2]) == 32
    assert envelope[3] == decision.candidates[0].document.byte_size
    with (
        pytest.raises(psycopg.errors.RestrictViolation),
        database_connection.transaction(),
    ):
        database_connection.execute(
            "DELETE FROM importing.import_entry WHERE import_entry_id = %s",
            (world.import_entry_id,),
        )


def test_postgresql_rendered_json_has_its_own_bounded_size(
    database_connection: Connection[Any],
) -> None:
    # RFC 8785 omits the space PostgreSQL emits after a JSON object colon.
    overhead = len(rfc8785.dumps({"normalized_title": ""}))
    canonical_n = canonical_query_snapshot(
        {"normalized_title": "x" * (MAX_IDENTITY_DOCUMENT_BYTES - overhead)}
    )
    assert canonical_n.byte_size == MAX_IDENTITY_DOCUMENT_BYTES
    pg_size = database_connection.execute(
        "SELECT octet_length(convert_to(%s::jsonb::text, 'UTF8'))",
        (Jsonb(canonical_n.value),),
    ).fetchone()
    assert pg_size is not None and pg_size[0] == MAX_IDENTITY_DOCUMENT_BYTES + 1
