"""Deterministic owner-time split planning for quality-source materialization."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest
import rfc8785
from autplay.application.sona import sona_inference_request_document
from autplay.application.sona_source_planning import (
    P11_CANONICAL_REQUEST_SHA256_V1,
    SONA_INFERENCE_REQUEST_SHA256_V1,
    SonaSourceRequestIdentity,
    SonaSourceRequestObservation,
    SonaSourceRequestRekey,
    derive_sona_source_request_rekey,
    plan_sona_quality_source_observations,
    plan_sona_quality_source_splits,
    rekey_sona_quality_source_plan,
)
from autplay.domain.sona import SonaCandidate, SonaInferenceRequest, SonaSemanticId

DAY_MS = 24 * 60 * 60 * 1_000


def _request(index: int, day: int, *, observed_day_offset: int = 1) -> SonaSourceRequestIdentity:
    return SonaSourceRequestIdentity(
        request_sha256=f"{index:064x}",
        owner_lineage_token=f"{index % 2 + 100:064x}",
        cutoff_at_ms=day * DAY_MS,
        observed_at_ms=(day + observed_day_offset) * DAY_MS,
    )


def test_split_plan_is_deterministic_excludes_embargo_gaps_and_is_content_addressed() -> None:
    requests = (
        _request(1, 0),
        _request(2, 8),
        _request(3, 12),
        _request(4, 17),
        _request(5, 19),
        _request(6, 23),
        _request(7, 27),
        _request(8, 30),
    )

    first = plan_sona_quality_source_splits(
        requests,
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )
    second = plan_sona_quality_source_splits(
        tuple(reversed(requests)),
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )

    assert first == second
    assert tuple(value.request_sha256 for value in first.train) == (f"{1:064x}", f"{2:064x}")
    assert tuple(value.request_sha256 for value in first.validation) == (
        f"{4:064x}",
        f"{5:064x}",
    )
    assert tuple(value.request_sha256 for value in first.test) == (f"{7:064x}", f"{8:064x}")
    assert first.excluded_request_count == 2
    assert first.document["quality_eligible"] is False
    assert first.request_identity_kind == P11_CANONICAL_REQUEST_SHA256_V1
    assert first.document["request_identity_kind"] == P11_CANONICAL_REQUEST_SHA256_V1
    assert first.document["request_hash_overlap_count"] == 0
    assert first.document["owner_time_ordering_violation_count"] == 0
    assert first.document["label_boundary_violation_count"] == 0
    assert first.plan_sha256 == sha256(rfc8785.dumps(first.document)).hexdigest()


def test_split_plan_rejects_label_evidence_touching_next_split_boundary() -> None:
    requests = (
        _request(1, 0),
        _request(2, 6, observed_day_offset=7),
        _request(3, 13),
        _request(4, 15),
        _request(5, 22),
        _request(6, 24),
    )

    with pytest.raises(ValueError, match="label evidence"):
        plan_sona_quality_source_splits(
            requests,
            request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
            owner_lineage_key_id="owner-key-v1",
        )


def test_split_plan_rejects_duplicate_or_insufficient_source_membership() -> None:
    duplicate = _request(1, 30)
    with pytest.raises(ValueError, match="duplicate"):
        plan_sona_quality_source_splits(
            (_request(1, 0), _request(2, 17), duplicate, duplicate),
            request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
            owner_lineage_key_id="owner-key-v1",
        )
    with pytest.raises(ValueError, match="embargoes"):
        plan_sona_quality_source_splits(
            (_request(1, 0), _request(2, 7), _request(3, 14)),
            request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
            owner_lineage_key_id="owner-key-v1",
        )

    with pytest.raises(ValueError, match="request_identity_kind"):
        plan_sona_quality_source_splits(
            (_request(1, 0), _request(2, 17), _request(3, 31)),
            request_identity_kind="AMBIGUOUS_REQUEST_SHA256_V1",
            owner_lineage_key_id="owner-key-v1",
        )


def test_owner_bearing_observations_are_pseudonymized_before_plan_serialization() -> None:
    owner = UUID("00000000-0000-7000-8000-000000000001")
    observations = tuple(
        SonaSourceRequestObservation(
            request_sha256=f"{index:064x}",
            owner_user_id=owner,
            cutoff_at_ms=day * DAY_MS,
            observed_at_ms=(day + 1) * DAY_MS,
        )
        for index, day in enumerate((0, 8, 17, 19, 27, 30), 1)
    )

    plan = plan_sona_quality_source_observations(
        observations,
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
        owner_lineage_hmac_key=b"k" * 32,
    )

    serialized = rfc8785.dumps(plan.document)
    assert str(owner).encode() not in serialized
    assert plan.document["owner_count"] == 1
    assert plan.document["owner_lineage_key_id"] == "owner-key-v1"
    assert len(str(plan.document["owner_lineage_manifest_sha256"])) == 64


def test_rekey_preserves_exact_p11_split_and_temporal_lineage() -> None:
    p11_plan = plan_sona_quality_source_splits(
        tuple(_request(index, day) for index, day in enumerate((0, 8, 17, 19, 27, 30), 1)),
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )
    rekeys = tuple(
        SonaSourceRequestRekey(
            p11_request_sha256=value.request_sha256,
            sona_request_sha256=f"{index + 1000:064x}",
            owner_lineage_token=value.owner_lineage_token,
            cutoff_at_ms=value.cutoff_at_ms,
            observed_at_ms=value.observed_at_ms,
        )
        for index, value in enumerate(p11_plan.selected, 1)
    )

    first = rekey_sona_quality_source_plan(p11_plan, rekeys)
    second = rekey_sona_quality_source_plan(p11_plan, tuple(reversed(rekeys)))

    assert first == second
    assert first.request_identity_kind == SONA_INFERENCE_REQUEST_SHA256_V1
    assert first.owner_lineage_key_id == p11_plan.owner_lineage_key_id
    assert tuple(value.cutoff_at_ms for value in first.train) == tuple(
        value.cutoff_at_ms for value in p11_plan.train
    )
    assert tuple(value.cutoff_at_ms for value in first.validation) == tuple(
        value.cutoff_at_ms for value in p11_plan.validation
    )
    assert tuple(value.cutoff_at_ms for value in first.test) == tuple(
        value.cutoff_at_ms for value in p11_plan.test
    )
    assert first.document["parent_plan_sha256"] == p11_plan.plan_sha256
    assert first.document["parent_request_set_sha256"] == p11_plan.request_set_sha256
    assert first.document["request_set_sha256"] != p11_plan.request_set_sha256
    assert first.plan_sha256 == sha256(rfc8785.dumps(first.document)).hexdigest()


def test_rekey_rejects_incomplete_duplicate_or_changed_lineage() -> None:
    p11_plan = plan_sona_quality_source_splits(
        tuple(_request(index, day) for index, day in enumerate((0, 8, 17, 19, 27, 30), 1)),
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )
    rekeys = tuple(
        SonaSourceRequestRekey(
            p11_request_sha256=value.request_sha256,
            sona_request_sha256=f"{index + 1000:064x}",
            owner_lineage_token=value.owner_lineage_token,
            cutoff_at_ms=value.cutoff_at_ms,
            observed_at_ms=value.observed_at_ms,
        )
        for index, value in enumerate(p11_plan.selected, 1)
    )

    with pytest.raises(ValueError, match="exact selected P11 membership"):
        rekey_sona_quality_source_plan(p11_plan, rekeys[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        rekey_sona_quality_source_plan(
            p11_plan,
            (
                rekeys[0],
                *rekeys[1:-1],
                replace(
                    rekeys[-1],
                    sona_request_sha256=rekeys[0].sona_request_sha256,
                ),
            ),
        )
    changed = SonaSourceRequestRekey(
        p11_request_sha256=rekeys[0].p11_request_sha256,
        sona_request_sha256=rekeys[0].sona_request_sha256,
        owner_lineage_token="f" * 64,
        cutoff_at_ms=rekeys[0].cutoff_at_ms,
        observed_at_ms=rekeys[0].observed_at_ms,
    )
    with pytest.raises(ValueError, match="owner or temporal lineage"):
        rekey_sona_quality_source_plan(p11_plan, (changed, *rekeys[1:]))


def test_rekey_is_derived_only_from_canonical_same_owner_sona_request() -> None:
    owner = UUID("00000000-0000-7000-8000-000000000001")
    observation = SonaSourceRequestObservation(
        request_sha256="1" * 64,
        owner_user_id=owner,
        cutoff_at_ms=10,
        observed_at_ms=11,
    )
    draft = SonaInferenceRequest(
        owner_user_id=owner,
        temporal_snapshot_id=UUID("00000000-0000-7000-8000-000000000101"),
        baseline_snapshot_id=UUID("00000000-0000-7000-8000-000000000102"),
        cutoff_at_ms=10,
        interaction_watermark=1,
        tokenizer_sha256="2" * 64,
        model_manifest_sha256="3" * 64,
        seed=0,
        history=(),
        candidates=(
            SonaCandidate(
                UUID("00000000-0000-7000-8000-000000000201"),
                SonaSemanticId(1, 2, 3),
            ),
        ),
        request_sha256="0" * 64,
    )
    request = replace(
        draft,
        request_sha256=sha256(rfc8785.dumps(sona_inference_request_document(draft))).hexdigest(),
    )

    rekey = derive_sona_source_request_rekey(
        observation,
        request,
        owner_lineage_hmac_key=b"k" * 32,
    )

    assert rekey.p11_request_sha256 == observation.request_sha256
    assert rekey.sona_request_sha256 == request.request_sha256
    assert len(rekey.owner_lineage_token) == 64
    with pytest.raises(ValueError, match="not canonical"):
        derive_sona_source_request_rekey(
            observation,
            replace(request, request_sha256="f" * 64),
            owner_lineage_hmac_key=b"k" * 32,
        )
    with pytest.raises(ValueError, match="cross-owner"):
        derive_sona_source_request_rekey(
            replace(observation, owner_user_id=UUID("00000000-0000-7000-8000-000000000002")),
            request,
            owner_lineage_hmac_key=b"k" * 32,
        )
