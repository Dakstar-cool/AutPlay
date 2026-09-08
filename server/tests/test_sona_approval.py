"""Signed approval-chain tests for quality-eligible Sona evidence."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import cast

import pytest
import rfc8785
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki, sign_p1363
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona_approval import (
    SONA_DATASET_APPROVAL_DOMAIN,
    SONA_EVALUATION_APPROVAL_DOMAIN,
    SONA_LABEL_DELAY_EMBARGO_MS,
    SONA_SOURCE_APPROVAL_DOMAIN,
    SonaApprovalError,
    compute_sona_dataset_bundle_sha256,
    verify_sona_dataset_approval,
    verify_sona_evaluation_approval,
    verify_sona_source_approval,
)
from cryptography.hazmat.primitives.asymmetric import ec

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64
HASH_1 = "1" * 64
HASH_2 = "2" * 64
HASH_3 = "3" * 64
DAY_MS = 24 * 60 * 60 * 1_000


def _source_payload(key: ec.EllipticCurvePrivateKey) -> dict[str, object]:
    return {
        "schema_version": 1,
        "approval_kind": "SONA_SOURCE_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "source_manifest_sha256": HASH_A,
        "embedding_snapshot_sha256": HASH_B,
        "catalog_snapshot_sha256": HASH_C,
        "owner_lineage_key_id": "r1b-lineage-20260904",
        "owner_count": 1,
        "recording_count": 24,
        "request_count": 100,
        "request_set_sha256": "6" * 64,
        "recording_set_sha256": "7" * 64,
        "approved_at_ms": 100,
        "not_after_ms": 1_000,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }


def _dataset_payload(
    key: ec.EllipticCurvePrivateKey, *, source_approval_sha256: str
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "approval_kind": "SONA_DATASET_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_bundle_sha256": HASH_D,
        "train_manifest_sha256": HASH_1,
        "validation_manifest_sha256": HASH_2,
        "test_manifest_sha256": HASH_3,
        "test_request_count": 10,
        "test_request_set_sha256": "8" * 64,
        "source_approval_sha256": source_approval_sha256,
        "tokenizer_manifest_sha256": HASH_E,
        "tokenizer_source_snapshot_sha256": HASH_B,
        "teacher_manifest_sha256": "4" * 64,
        "source_model_manifest_sha256": "5" * 64,
        "split_policy": "OWNER_TIME_ORDERED_WITH_7D_LABEL_EMBARGO_V1",
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "train_cutoff_start_ms": 0,
        "train_cutoff_end_ms": 10 * DAY_MS,
        "validation_cutoff_start_ms": 17 * DAY_MS,
        "validation_cutoff_end_ms": 20 * DAY_MS,
        "test_cutoff_start_ms": 27 * DAY_MS,
        "test_cutoff_end_ms": 30 * DAY_MS,
        "request_hash_overlap_count": 0,
        "example_hash_overlap_count": 0,
        "leakage_audit_sha256": HASH_F,
        "owner_lineage_manifest_sha256": "0" * 64,
        "approved_at_ms": 200,
        "not_after_ms": 900,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }
    payload["dataset_bundle_sha256"] = compute_sona_dataset_bundle_sha256(payload)
    return payload


def _signed(
    payload: Mapping[str, object],
    key: ec.EllipticCurvePrivateKey,
    domain: str,
) -> dict[str, object]:
    canonical_payload = cast(dict[str, JsonValue], dict(payload))
    digest = hashlib.sha256(rfc8785.dumps(canonical_payload)).digest()
    return {
        "payload": dict(payload),
        "payload_sha256": digest.hex(),
        "signature_algorithm": "ES256-P1363",
        "signature_b64url": sign_p1363(key, domain, digest),
    }


def _evaluation_payload(
    key: ec.EllipticCurvePrivateKey,
    *,
    dataset_approval_sha256: str,
    dataset_bundle_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "approval_kind": "SONA_EVALUATION_EVIDENCE_APPROVAL_V1",
        "decision": "APPROVED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "synthetic": False,
        "data_classification": "APPROVED_OWNER_SAFE",
        "dataset_approval_sha256": dataset_approval_sha256,
        "dataset_bundle_sha256": dataset_bundle_sha256,
        "tokenizer_manifest_sha256": HASH_E,
        "checkpoint_manifest_sha256": "8" * 64,
        "artifact_sha256": "9" * 64,
        "artifact_manifest_sha256": "a" * 64,
        "p11_pipeline_manifest_sha256": "b" * 64,
        "sona_pipeline_manifest_sha256": "c" * 64,
        "contract_policy_sha256": "d" * 64,
        "evaluation_case_bundle_sha256": "e" * 64,
        "execution_evidence_bundle_sha256": "3" * 64,
        "outcome_evidence_bundle_sha256": "2" * 64,
        "directional_scenario_bundle_sha256": "f" * 64,
        "safety_evidence_bundle_sha256": "0" * 64,
        "performance_evidence_sha256": "1" * 64,
        "approved_at_ms": 300,
        "not_after_ms": 800,
        "reviewer_key_thumbprint_sha256": public_key_thumbprint(public_spki(key)).hex(),
        "privacy_delete_action": "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1",
    }


def test_verified_quality_eligibility_is_derived_from_signed_ancestry() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=250,
    )
    dataset = verify_sona_dataset_approval(
        _signed(
            _dataset_payload(key, source_approval_sha256=source.approval_sha256),
            key,
            SONA_DATASET_APPROVAL_DOMAIN,
        ),
        trusted_reviewer_spki=spki,
        source_approval=source,
        at_ms=250,
    )

    assert dataset.quality_eligible is True
    assert dataset.source_approval_sha256 == source.approval_sha256
    assert dataset.tokenizer_source_snapshot_sha256 == source.embedding_snapshot_sha256


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("synthetic", True, "Synthetic"),
        ("decision", "BLOCKED", "decision"),
        ("purpose", "SERVING", "purpose"),
        ("owner_count", 0, "owner_count"),
        ("not_after_ms", 250, "not valid"),
    ],
)
def test_source_approval_rejects_unapproved_or_invalid_evidence(
    field: str, value: object, message: str
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    payload = _source_payload(key)
    payload[field] = value

    with pytest.raises(SonaApprovalError, match=message):
        verify_sona_source_approval(
            _signed(payload, key, SONA_SOURCE_APPROVAL_DOMAIN),
            trusted_reviewer_spki=public_spki(key),
            at_ms=250,
        )


def test_source_approval_rejects_tamper_even_when_payload_hash_is_recomputed() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    document = _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN)
    tampered = copy.deepcopy(document)
    payload = cast(
        dict[str, JsonValue],
        dict(cast(Mapping[str, object], tampered["payload"])),
    )
    payload["request_count"] = 101
    tampered["payload"] = payload
    tampered["payload_sha256"] = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()

    with pytest.raises(SonaApprovalError, match="signature"):
        verify_sona_source_approval(
            tampered,
            trusted_reviewer_spki=public_spki(key),
            at_ms=250,
        )


def test_source_approval_rejects_unknown_field_and_wrong_reviewer() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())
    payload = _source_payload(key)
    payload["quality_eligible"] = True
    document = _signed(payload, key, SONA_SOURCE_APPROVAL_DOMAIN)

    with pytest.raises(SonaApprovalError, match="signature"):
        verify_sona_source_approval(
            document,
            trusted_reviewer_spki=public_spki(other),
            at_ms=250,
        )
    with pytest.raises(SonaApprovalError, match="unknown"):
        verify_sona_source_approval(
            document,
            trusted_reviewer_spki=public_spki(key),
            at_ms=250,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("synthetic", True, "Synthetic"),
        ("request_hash_overlap_count", 1, "request split leakage"),
        ("example_hash_overlap_count", 1, "example split leakage"),
        ("label_delay_embargo_ms", 1, "label embargo"),
        ("validation_cutoff_start_ms", 16 * DAY_MS, "temporal split"),
        ("test_cutoff_start_ms", 26 * DAY_MS, "temporal split"),
        ("tokenizer_source_snapshot_sha256", HASH_C, "tokenizer source ancestry"),
        ("validation_manifest_sha256", HASH_1, "split manifests"),
    ],
)
def test_dataset_approval_rejects_synthetic_leakage_and_ancestry_mismatch(
    field: str, value: object, message: str
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=250,
    )
    payload = _dataset_payload(key, source_approval_sha256=source.approval_sha256)
    payload[field] = value

    with pytest.raises(SonaApprovalError, match=message):
        verify_sona_dataset_approval(
            _signed(payload, key, SONA_DATASET_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            source_approval=source,
            at_ms=250,
        )


def test_dataset_approval_rejects_unrelated_source_approval() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=250,
    )
    payload = _dataset_payload(key, source_approval_sha256=HASH_A)

    with pytest.raises(SonaApprovalError, match="source ancestry"):
        verify_sona_dataset_approval(
            _signed(payload, key, SONA_DATASET_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            source_approval=source,
            at_ms=250,
        )


def test_dataset_approval_rejects_stale_source_object_and_escaped_validity() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=250,
    )
    payload = _dataset_payload(key, source_approval_sha256=source.approval_sha256)
    payload["not_after_ms"] = 1_001
    payload["dataset_bundle_sha256"] = compute_sona_dataset_bundle_sha256(payload)

    with pytest.raises(SonaApprovalError, match="validity escapes"):
        verify_sona_dataset_approval(
            _signed(payload, key, SONA_DATASET_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            source_approval=source,
            at_ms=250,
        )

    payload["not_after_ms"] = 1_200
    with pytest.raises(SonaApprovalError, match="source is not valid"):
        verify_sona_dataset_approval(
            _signed(payload, key, SONA_DATASET_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            source_approval=source,
            at_ms=1_050,
        )


def test_evaluation_evidence_approval_is_bound_to_verified_dataset() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=350,
    )
    dataset = verify_sona_dataset_approval(
        _signed(
            _dataset_payload(key, source_approval_sha256=source.approval_sha256),
            key,
            SONA_DATASET_APPROVAL_DOMAIN,
        ),
        trusted_reviewer_spki=spki,
        source_approval=source,
        at_ms=350,
    )

    evaluation = verify_sona_evaluation_approval(
        _signed(
            _evaluation_payload(
                key,
                dataset_approval_sha256=dataset.approval_sha256,
                dataset_bundle_sha256=dataset.dataset_bundle_sha256,
            ),
            key,
            SONA_EVALUATION_APPROVAL_DOMAIN,
        ),
        trusted_reviewer_spki=spki,
        dataset_approval=dataset,
        at_ms=350,
    )

    assert evaluation.dataset_approval_sha256 == dataset.approval_sha256
    assert evaluation.dataset_bundle_sha256 == dataset.dataset_bundle_sha256
    assert evaluation.tokenizer_manifest_sha256 == dataset.tokenizer_manifest_sha256


def test_evaluation_evidence_approval_rejects_dataset_ancestry_and_validity_escape() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    source = verify_sona_source_approval(
        _signed(_source_payload(key), key, SONA_SOURCE_APPROVAL_DOMAIN),
        trusted_reviewer_spki=spki,
        at_ms=350,
    )
    dataset = verify_sona_dataset_approval(
        _signed(
            _dataset_payload(key, source_approval_sha256=source.approval_sha256),
            key,
            SONA_DATASET_APPROVAL_DOMAIN,
        ),
        trusted_reviewer_spki=spki,
        source_approval=source,
        at_ms=350,
    )
    payload = _evaluation_payload(
        key,
        dataset_approval_sha256=HASH_A,
        dataset_bundle_sha256=dataset.dataset_bundle_sha256,
    )
    with pytest.raises(SonaApprovalError, match="dataset ancestry"):
        verify_sona_evaluation_approval(
            _signed(payload, key, SONA_EVALUATION_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            dataset_approval=dataset,
            at_ms=350,
        )

    payload["dataset_approval_sha256"] = dataset.approval_sha256
    payload["not_after_ms"] = 901
    with pytest.raises(SonaApprovalError, match="validity escapes"):
        verify_sona_evaluation_approval(
            _signed(payload, key, SONA_EVALUATION_APPROVAL_DOMAIN),
            trusted_reviewer_spki=spki,
            dataset_approval=dataset,
            at_ms=350,
        )
