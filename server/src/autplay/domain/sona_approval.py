"""Signed provenance approvals for Sona-Lite quality evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import rfc8785

from autplay.domain.profile_pairing import (
    ES256_P1363,
    ProfilePairingError,
    public_key_thumbprint,
    verify_p1363,
)

SONA_APPROVAL_SCHEMA_VERSION: Final = 1
SONA_APPROVAL_MAX_BYTES: Final = 65_536
SONA_QUALITY_PURPOSE: Final = "R1B_SHADOW_TRAINING_EVALUATION_ONLY"
SONA_DATA_CLASSIFICATION: Final = "APPROVED_OWNER_SAFE"
SONA_PRIVACY_DELETE_ACTION: Final = "REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1"
SONA_SOURCE_APPROVAL_KIND: Final = "SONA_SOURCE_APPROVAL_V1"
SONA_DATASET_APPROVAL_KIND: Final = "SONA_DATASET_APPROVAL_V1"
SONA_EVALUATION_APPROVAL_KIND: Final = "SONA_EVALUATION_EVIDENCE_APPROVAL_V1"
SONA_ARTIFACT_APPROVAL_KIND: Final = "SONA_QUALITY_ELIGIBLE_ARTIFACT_APPROVAL_V1"
SONA_SOURCE_APPROVAL_DOMAIN: Final = "autplay:r1b:sona-source-approval:v1\n"
SONA_DATASET_APPROVAL_DOMAIN: Final = "autplay:r1b:sona-dataset-approval:v1\n"
SONA_EVALUATION_APPROVAL_DOMAIN: Final = "autplay:r1b:sona-evaluation-approval:v1\n"
SONA_ARTIFACT_APPROVAL_DOMAIN: Final = "autplay:r1b:sona-artifact-approval:v1\n"
SONA_SPLIT_POLICY: Final = "OWNER_TIME_ORDERED_WITH_7D_LABEL_EMBARGO_V1"
SONA_LABEL_DELAY_EMBARGO_MS: Final = 7 * 24 * 60 * 60 * 1_000

_DATASET_BUNDLE_IDENTITY_KEYS = (
    "source_approval_sha256",
    "train_manifest_sha256",
    "validation_manifest_sha256",
    "test_manifest_sha256",
    "test_request_count",
    "test_request_set_sha256",
    "tokenizer_manifest_sha256",
    "tokenizer_source_snapshot_sha256",
    "teacher_manifest_sha256",
    "source_model_manifest_sha256",
    "split_policy",
    "label_delay_embargo_ms",
    "train_cutoff_start_ms",
    "train_cutoff_end_ms",
    "validation_cutoff_start_ms",
    "validation_cutoff_end_ms",
    "test_cutoff_start_ms",
    "test_cutoff_end_ms",
    "request_hash_overlap_count",
    "example_hash_overlap_count",
    "leakage_audit_sha256",
    "owner_lineage_manifest_sha256",
)

_ENVELOPE_KEYS = frozenset({"payload", "payload_sha256", "signature_algorithm", "signature_b64url"})
_SOURCE_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "approval_kind",
        "decision",
        "purpose",
        "synthetic",
        "data_classification",
        "source_manifest_sha256",
        "embedding_snapshot_sha256",
        "catalog_snapshot_sha256",
        "owner_lineage_key_id",
        "owner_count",
        "recording_count",
        "request_count",
        "request_set_sha256",
        "recording_set_sha256",
        "approved_at_ms",
        "not_after_ms",
        "reviewer_key_thumbprint_sha256",
        "privacy_delete_action",
    }
)
_DATASET_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "approval_kind",
        "decision",
        "purpose",
        "synthetic",
        "data_classification",
        "dataset_bundle_sha256",
        "train_manifest_sha256",
        "validation_manifest_sha256",
        "test_manifest_sha256",
        "test_request_count",
        "test_request_set_sha256",
        "source_approval_sha256",
        "tokenizer_manifest_sha256",
        "tokenizer_source_snapshot_sha256",
        "teacher_manifest_sha256",
        "source_model_manifest_sha256",
        "split_policy",
        "label_delay_embargo_ms",
        "train_cutoff_start_ms",
        "train_cutoff_end_ms",
        "validation_cutoff_start_ms",
        "validation_cutoff_end_ms",
        "test_cutoff_start_ms",
        "test_cutoff_end_ms",
        "request_hash_overlap_count",
        "example_hash_overlap_count",
        "leakage_audit_sha256",
        "owner_lineage_manifest_sha256",
        "approved_at_ms",
        "not_after_ms",
        "reviewer_key_thumbprint_sha256",
        "privacy_delete_action",
    }
)
_EVALUATION_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "approval_kind",
        "decision",
        "purpose",
        "synthetic",
        "data_classification",
        "dataset_approval_sha256",
        "dataset_bundle_sha256",
        "tokenizer_manifest_sha256",
        "checkpoint_manifest_sha256",
        "artifact_sha256",
        "artifact_manifest_sha256",
        "p11_pipeline_manifest_sha256",
        "sona_pipeline_manifest_sha256",
        "contract_policy_sha256",
        "evaluation_case_bundle_sha256",
        "execution_evidence_bundle_sha256",
        "outcome_evidence_bundle_sha256",
        "directional_scenario_bundle_sha256",
        "safety_evidence_bundle_sha256",
        "performance_evidence_sha256",
        "approved_at_ms",
        "not_after_ms",
        "reviewer_key_thumbprint_sha256",
        "privacy_delete_action",
    }
)
_ARTIFACT_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "approval_kind",
        "decision",
        "purpose",
        "synthetic",
        "data_classification",
        "evaluation_approval_sha256",
        "evaluation_report_sha256",
        "metrics_sha256",
        "performance_sha256",
        "dataset_approval_sha256",
        "dataset_bundle_sha256",
        "tokenizer_manifest_sha256",
        "checkpoint_manifest_sha256",
        "artifact_sha256",
        "artifact_manifest_sha256",
        "contract_policy_sha256",
        "quality_gate_decision",
        "quality_eligible",
        "approved_at_ms",
        "not_after_ms",
        "reviewer_key_thumbprint_sha256",
        "privacy_delete_action",
    }
)


class SonaApprovalError(ValueError):
    """A stable fail-closed approval validation error."""


@dataclass(frozen=True, slots=True)
class VerifiedSonaSourceApproval:
    """One verified approval for an immutable, non-synthetic source snapshot."""

    approval_sha256: str
    payload_sha256: str
    source_manifest_sha256: str
    embedding_snapshot_sha256: str
    catalog_snapshot_sha256: str
    owner_lineage_key_id: str
    owner_count: int
    recording_count: int
    request_count: int
    request_set_sha256: str
    recording_set_sha256: str
    approved_at_ms: int
    not_after_ms: int
    reviewer_key_thumbprint_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedSonaDatasetApproval:
    """One verified quality-eligible dataset bundle and its source ancestry."""

    approval_sha256: str
    payload_sha256: str
    dataset_bundle_sha256: str
    train_manifest_sha256: str
    validation_manifest_sha256: str
    test_manifest_sha256: str
    test_request_count: int
    test_request_set_sha256: str
    source_approval_sha256: str
    tokenizer_manifest_sha256: str
    tokenizer_source_snapshot_sha256: str
    teacher_manifest_sha256: str
    source_model_manifest_sha256: str
    train_cutoff_start_ms: int
    train_cutoff_end_ms: int
    validation_cutoff_start_ms: int
    validation_cutoff_end_ms: int
    test_cutoff_start_ms: int
    test_cutoff_end_ms: int
    leakage_audit_sha256: str
    owner_lineage_manifest_sha256: str
    approved_at_ms: int
    not_after_ms: int
    reviewer_key_thumbprint_sha256: str

    @property
    def quality_eligible(self) -> bool:
        """Eligibility is derived from successful verification, never a manifest boolean."""

        return True


@dataclass(frozen=True, slots=True)
class VerifiedSonaEvaluationApproval:
    """Reviewer-approved, hash-bound inputs admitted to one offline evaluation."""

    approval_sha256: str
    payload_sha256: str
    dataset_approval_sha256: str
    dataset_bundle_sha256: str
    tokenizer_manifest_sha256: str
    checkpoint_manifest_sha256: str
    artifact_sha256: str
    artifact_manifest_sha256: str
    p11_pipeline_manifest_sha256: str
    sona_pipeline_manifest_sha256: str
    contract_policy_sha256: str
    evaluation_case_bundle_sha256: str
    execution_evidence_bundle_sha256: str
    outcome_evidence_bundle_sha256: str
    directional_scenario_bundle_sha256: str
    safety_evidence_bundle_sha256: str
    performance_evidence_sha256: str
    approved_at_ms: int
    not_after_ms: int
    reviewer_key_thumbprint_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedSonaArtifactApproval:
    """Signed final R1B artifact eligibility derived from an exact passing report."""

    approval_sha256: str
    payload_sha256: str
    evaluation_approval_sha256: str
    evaluation_report_sha256: str
    metrics_sha256: str
    performance_sha256: str
    dataset_approval_sha256: str
    dataset_bundle_sha256: str
    tokenizer_manifest_sha256: str
    checkpoint_manifest_sha256: str
    artifact_sha256: str
    artifact_manifest_sha256: str
    contract_policy_sha256: str
    approved_at_ms: int
    not_after_ms: int
    reviewer_key_thumbprint_sha256: str

    @property
    def quality_eligible(self) -> bool:
        return True


def load_sona_source_approval(
    path: Path,
    *,
    trusted_reviewer_spki: bytes,
    at_ms: int,
) -> VerifiedSonaSourceApproval:
    """Load and verify one signed source approval document."""

    return verify_sona_source_approval(
        _load_bounded_document(path),
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )


def load_sona_dataset_approval(
    path: Path,
    *,
    trusted_reviewer_spki: bytes,
    source_approval: VerifiedSonaSourceApproval,
    at_ms: int,
) -> VerifiedSonaDatasetApproval:
    """Load and verify one signed dataset approval document and its ancestry."""

    return verify_sona_dataset_approval(
        _load_bounded_document(path),
        trusted_reviewer_spki=trusted_reviewer_spki,
        source_approval=source_approval,
        at_ms=at_ms,
    )


def load_sona_evaluation_approval(
    path: Path,
    *,
    trusted_reviewer_spki: bytes,
    dataset_approval: VerifiedSonaDatasetApproval,
    at_ms: int,
) -> VerifiedSonaEvaluationApproval:
    """Load signed admission for exact offline cases, audits and raw performance evidence."""

    return verify_sona_evaluation_approval(
        _load_bounded_document(path),
        trusted_reviewer_spki=trusted_reviewer_spki,
        dataset_approval=dataset_approval,
        at_ms=at_ms,
    )


def load_sona_artifact_approval(
    path: Path,
    *,
    trusted_reviewer_spki: bytes,
    evaluation_approval: VerifiedSonaEvaluationApproval,
    at_ms: int,
) -> VerifiedSonaArtifactApproval:
    """Load a signed final artifact approval descended from one evaluation admission."""

    return verify_sona_artifact_approval(
        _load_bounded_document(path),
        trusted_reviewer_spki=trusted_reviewer_spki,
        evaluation_approval=evaluation_approval,
        at_ms=at_ms,
    )


def verify_sona_source_approval(
    document: Mapping[str, Any],
    *,
    trusted_reviewer_spki: bytes,
    at_ms: int,
) -> VerifiedSonaSourceApproval:
    """Verify exact source provenance, consent scope, time bound, and signature."""

    payload, payload_sha256, approval_sha256 = _verify_envelope(
        document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        domain=SONA_SOURCE_APPROVAL_DOMAIN,
    )
    _require_exact_keys(payload, _SOURCE_PAYLOAD_KEYS, "source approval payload")
    _verify_common_payload(
        payload,
        kind=SONA_SOURCE_APPROVAL_KIND,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    owner_lineage_key_id = _expect_label(payload, "owner_lineage_key_id")
    owner_count = _expect_positive_int(payload, "owner_count")
    recording_count = _expect_positive_int(payload, "recording_count")
    request_count = _expect_positive_int(payload, "request_count")
    return VerifiedSonaSourceApproval(
        approval_sha256=approval_sha256,
        payload_sha256=payload_sha256,
        source_manifest_sha256=_expect_sha256(payload, "source_manifest_sha256"),
        embedding_snapshot_sha256=_expect_sha256(payload, "embedding_snapshot_sha256"),
        catalog_snapshot_sha256=_expect_sha256(payload, "catalog_snapshot_sha256"),
        owner_lineage_key_id=owner_lineage_key_id,
        owner_count=owner_count,
        recording_count=recording_count,
        request_count=request_count,
        request_set_sha256=_expect_sha256(payload, "request_set_sha256"),
        recording_set_sha256=_expect_sha256(payload, "recording_set_sha256"),
        approved_at_ms=_expect_int(payload, "approved_at_ms"),
        not_after_ms=_expect_int(payload, "not_after_ms"),
        reviewer_key_thumbprint_sha256=_expect_sha256(payload, "reviewer_key_thumbprint_sha256"),
    )


def verify_sona_dataset_approval(
    document: Mapping[str, Any],
    *,
    trusted_reviewer_spki: bytes,
    source_approval: VerifiedSonaSourceApproval,
    at_ms: int,
) -> VerifiedSonaDatasetApproval:
    """Verify one leakage-audited dataset bundle and its approved source ancestry."""

    payload, payload_sha256, approval_sha256 = _verify_envelope(
        document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        domain=SONA_DATASET_APPROVAL_DOMAIN,
    )
    _require_exact_keys(payload, _DATASET_PAYLOAD_KEYS, "dataset approval payload")
    _verify_common_payload(
        payload,
        kind=SONA_DATASET_APPROVAL_KIND,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    if not source_approval.approved_at_ms <= at_ms < source_approval.not_after_ms:
        raise SonaApprovalError("Sona dataset approval source is not valid at the requested time")
    if _expect_string(payload, "source_approval_sha256") != source_approval.approval_sha256:
        raise SonaApprovalError("Sona dataset approval source ancestry mismatch")
    if (
        _expect_string(payload, "tokenizer_source_snapshot_sha256")
        != source_approval.embedding_snapshot_sha256
    ):
        raise SonaApprovalError("Sona tokenizer source ancestry mismatch")
    if _expect_string(payload, "split_policy") != SONA_SPLIT_POLICY:
        raise SonaApprovalError("Sona dataset split policy is unsupported")
    if _expect_int(payload, "label_delay_embargo_ms") != SONA_LABEL_DELAY_EMBARGO_MS:
        raise SonaApprovalError("Sona dataset label embargo is unsupported")
    if _expect_int(payload, "request_hash_overlap_count") != 0:
        raise SonaApprovalError("Sona dataset request split leakage detected")
    if _expect_int(payload, "example_hash_overlap_count") != 0:
        raise SonaApprovalError("Sona dataset example split leakage detected")
    train_cutoff_start_ms = _expect_non_negative_int(payload, "train_cutoff_start_ms")
    train_cutoff_end_ms = _expect_non_negative_int(payload, "train_cutoff_end_ms")
    validation_cutoff_start_ms = _expect_non_negative_int(payload, "validation_cutoff_start_ms")
    validation_cutoff_end_ms = _expect_non_negative_int(payload, "validation_cutoff_end_ms")
    test_cutoff_start_ms = _expect_non_negative_int(payload, "test_cutoff_start_ms")
    test_cutoff_end_ms = _expect_non_negative_int(payload, "test_cutoff_end_ms")
    if not (
        train_cutoff_start_ms <= train_cutoff_end_ms
        and train_cutoff_end_ms + SONA_LABEL_DELAY_EMBARGO_MS <= validation_cutoff_start_ms
        and validation_cutoff_start_ms <= validation_cutoff_end_ms
        and validation_cutoff_end_ms + SONA_LABEL_DELAY_EMBARGO_MS <= test_cutoff_start_ms
        and test_cutoff_start_ms <= test_cutoff_end_ms
    ):
        raise SonaApprovalError("Sona dataset temporal split overlaps its label embargo")
    train_manifest_sha256 = _expect_sha256(payload, "train_manifest_sha256")
    validation_manifest_sha256 = _expect_sha256(payload, "validation_manifest_sha256")
    test_manifest_sha256 = _expect_sha256(payload, "test_manifest_sha256")
    if len({train_manifest_sha256, validation_manifest_sha256, test_manifest_sha256}) != 3:
        raise SonaApprovalError("Sona dataset split manifests must be distinct")
    test_request_count = _expect_positive_int(payload, "test_request_count")
    if test_request_count > source_approval.request_count:
        raise SonaApprovalError("Sona dataset test request count exceeds its approved source")
    approved_at_ms = _expect_int(payload, "approved_at_ms")
    not_after_ms = _expect_int(payload, "not_after_ms")
    if (
        approved_at_ms < source_approval.approved_at_ms
        or not_after_ms > source_approval.not_after_ms
    ):
        raise SonaApprovalError("Sona dataset approval validity escapes its source approval")
    dataset_bundle_sha256 = _expect_sha256(payload, "dataset_bundle_sha256")
    if dataset_bundle_sha256 != compute_sona_dataset_bundle_sha256(payload):
        raise SonaApprovalError("Sona dataset bundle identity mismatch")
    return VerifiedSonaDatasetApproval(
        approval_sha256=approval_sha256,
        payload_sha256=payload_sha256,
        dataset_bundle_sha256=dataset_bundle_sha256,
        train_manifest_sha256=train_manifest_sha256,
        validation_manifest_sha256=validation_manifest_sha256,
        test_manifest_sha256=test_manifest_sha256,
        test_request_count=test_request_count,
        test_request_set_sha256=_expect_sha256(payload, "test_request_set_sha256"),
        source_approval_sha256=source_approval.approval_sha256,
        tokenizer_manifest_sha256=_expect_sha256(payload, "tokenizer_manifest_sha256"),
        tokenizer_source_snapshot_sha256=source_approval.embedding_snapshot_sha256,
        teacher_manifest_sha256=_expect_sha256(payload, "teacher_manifest_sha256"),
        source_model_manifest_sha256=_expect_sha256(payload, "source_model_manifest_sha256"),
        train_cutoff_start_ms=train_cutoff_start_ms,
        train_cutoff_end_ms=train_cutoff_end_ms,
        validation_cutoff_start_ms=validation_cutoff_start_ms,
        validation_cutoff_end_ms=validation_cutoff_end_ms,
        test_cutoff_start_ms=test_cutoff_start_ms,
        test_cutoff_end_ms=test_cutoff_end_ms,
        leakage_audit_sha256=_expect_sha256(payload, "leakage_audit_sha256"),
        owner_lineage_manifest_sha256=_expect_sha256(payload, "owner_lineage_manifest_sha256"),
        approved_at_ms=approved_at_ms,
        not_after_ms=not_after_ms,
        reviewer_key_thumbprint_sha256=_expect_sha256(payload, "reviewer_key_thumbprint_sha256"),
    )


def compute_sona_dataset_bundle_sha256(payload: Mapping[str, Any]) -> str:
    """Compute the canonical identity of every artifact and split in a dataset bundle."""

    identity = {key: payload.get(key) for key in _DATASET_BUNDLE_IDENTITY_KEYS}
    return hashlib.sha256(rfc8785.dumps(identity)).hexdigest()


def verify_sona_evaluation_approval(
    document: Mapping[str, Any],
    *,
    trusted_reviewer_spki: bytes,
    dataset_approval: VerifiedSonaDatasetApproval,
    at_ms: int,
) -> VerifiedSonaEvaluationApproval:
    """Verify reviewer admission of exact immutable evidence before metric computation."""

    payload, payload_sha256, approval_sha256 = _verify_envelope(
        document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        domain=SONA_EVALUATION_APPROVAL_DOMAIN,
    )
    _require_exact_keys(payload, _EVALUATION_PAYLOAD_KEYS, "evaluation approval payload")
    _verify_common_payload(
        payload,
        kind=SONA_EVALUATION_APPROVAL_KIND,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    if not dataset_approval.approved_at_ms <= at_ms < dataset_approval.not_after_ms:
        raise SonaApprovalError("Sona evaluation dataset approval is not valid")
    if (
        _expect_sha256(payload, "dataset_approval_sha256") != dataset_approval.approval_sha256
        or _expect_sha256(payload, "dataset_bundle_sha256")
        != dataset_approval.dataset_bundle_sha256
        or _expect_sha256(payload, "tokenizer_manifest_sha256")
        != dataset_approval.tokenizer_manifest_sha256
    ):
        raise SonaApprovalError("Sona evaluation approval dataset ancestry mismatch")
    approved_at_ms = _expect_int(payload, "approved_at_ms")
    not_after_ms = _expect_int(payload, "not_after_ms")
    if (
        approved_at_ms < dataset_approval.approved_at_ms
        or not_after_ms > dataset_approval.not_after_ms
    ):
        raise SonaApprovalError("Sona evaluation approval validity escapes its dataset approval")
    return VerifiedSonaEvaluationApproval(
        approval_sha256=approval_sha256,
        payload_sha256=payload_sha256,
        dataset_approval_sha256=dataset_approval.approval_sha256,
        dataset_bundle_sha256=dataset_approval.dataset_bundle_sha256,
        tokenizer_manifest_sha256=dataset_approval.tokenizer_manifest_sha256,
        checkpoint_manifest_sha256=_expect_sha256(payload, "checkpoint_manifest_sha256"),
        artifact_sha256=_expect_sha256(payload, "artifact_sha256"),
        artifact_manifest_sha256=_expect_sha256(payload, "artifact_manifest_sha256"),
        p11_pipeline_manifest_sha256=_expect_sha256(payload, "p11_pipeline_manifest_sha256"),
        sona_pipeline_manifest_sha256=_expect_sha256(payload, "sona_pipeline_manifest_sha256"),
        contract_policy_sha256=_expect_sha256(payload, "contract_policy_sha256"),
        evaluation_case_bundle_sha256=_expect_sha256(payload, "evaluation_case_bundle_sha256"),
        execution_evidence_bundle_sha256=_expect_sha256(
            payload, "execution_evidence_bundle_sha256"
        ),
        outcome_evidence_bundle_sha256=_expect_sha256(payload, "outcome_evidence_bundle_sha256"),
        directional_scenario_bundle_sha256=_expect_sha256(
            payload, "directional_scenario_bundle_sha256"
        ),
        safety_evidence_bundle_sha256=_expect_sha256(payload, "safety_evidence_bundle_sha256"),
        performance_evidence_sha256=_expect_sha256(payload, "performance_evidence_sha256"),
        approved_at_ms=approved_at_ms,
        not_after_ms=not_after_ms,
        reviewer_key_thumbprint_sha256=_expect_sha256(payload, "reviewer_key_thumbprint_sha256"),
    )


def verify_sona_artifact_approval(
    document: Mapping[str, Any],
    *,
    trusted_reviewer_spki: bytes,
    evaluation_approval: VerifiedSonaEvaluationApproval,
    at_ms: int,
) -> VerifiedSonaArtifactApproval:
    """Verify the final signed artifact decision and its evaluation ancestry."""

    payload, payload_sha256, approval_sha256 = _verify_envelope(
        document,
        trusted_reviewer_spki=trusted_reviewer_spki,
        domain=SONA_ARTIFACT_APPROVAL_DOMAIN,
    )
    _require_exact_keys(payload, _ARTIFACT_PAYLOAD_KEYS, "artifact approval payload")
    _verify_common_payload(
        payload,
        kind=SONA_ARTIFACT_APPROVAL_KIND,
        trusted_reviewer_spki=trusted_reviewer_spki,
        at_ms=at_ms,
    )
    if not evaluation_approval.approved_at_ms <= at_ms < evaluation_approval.not_after_ms:
        raise SonaApprovalError("Sona artifact evaluation approval is not valid")
    bindings = {
        "evaluation_approval_sha256": evaluation_approval.approval_sha256,
        "dataset_approval_sha256": evaluation_approval.dataset_approval_sha256,
        "dataset_bundle_sha256": evaluation_approval.dataset_bundle_sha256,
        "tokenizer_manifest_sha256": evaluation_approval.tokenizer_manifest_sha256,
        "checkpoint_manifest_sha256": evaluation_approval.checkpoint_manifest_sha256,
        "artifact_sha256": evaluation_approval.artifact_sha256,
        "artifact_manifest_sha256": evaluation_approval.artifact_manifest_sha256,
        "contract_policy_sha256": evaluation_approval.contract_policy_sha256,
    }
    if any(_expect_sha256(payload, field) != expected for field, expected in bindings.items()):
        raise SonaApprovalError("Sona artifact approval evaluation ancestry mismatch")
    if _expect_string(payload, "quality_gate_decision") != "PASS" or not _expect_bool(
        payload, "quality_eligible"
    ):
        raise SonaApprovalError("Sona artifact approval quality decision is not PASS")
    approved_at_ms = _expect_int(payload, "approved_at_ms")
    not_after_ms = _expect_int(payload, "not_after_ms")
    if (
        approved_at_ms < evaluation_approval.approved_at_ms
        or not_after_ms > evaluation_approval.not_after_ms
    ):
        raise SonaApprovalError("Sona artifact approval validity escapes evaluation approval")
    return VerifiedSonaArtifactApproval(
        approval_sha256=approval_sha256,
        payload_sha256=payload_sha256,
        evaluation_approval_sha256=evaluation_approval.approval_sha256,
        evaluation_report_sha256=_expect_sha256(payload, "evaluation_report_sha256"),
        metrics_sha256=_expect_sha256(payload, "metrics_sha256"),
        performance_sha256=_expect_sha256(payload, "performance_sha256"),
        dataset_approval_sha256=evaluation_approval.dataset_approval_sha256,
        dataset_bundle_sha256=evaluation_approval.dataset_bundle_sha256,
        tokenizer_manifest_sha256=evaluation_approval.tokenizer_manifest_sha256,
        checkpoint_manifest_sha256=evaluation_approval.checkpoint_manifest_sha256,
        artifact_sha256=evaluation_approval.artifact_sha256,
        artifact_manifest_sha256=evaluation_approval.artifact_manifest_sha256,
        contract_policy_sha256=evaluation_approval.contract_policy_sha256,
        approved_at_ms=approved_at_ms,
        not_after_ms=not_after_ms,
        reviewer_key_thumbprint_sha256=_expect_sha256(payload, "reviewer_key_thumbprint_sha256"),
    )


def _verify_envelope(
    document: Mapping[str, Any],
    *,
    trusted_reviewer_spki: bytes,
    domain: str,
) -> tuple[dict[str, Any], str, str]:
    envelope = dict(document)
    _require_exact_keys(envelope, _ENVELOPE_KEYS, "approval envelope")
    payload = _expect_object(envelope, "payload")
    payload_sha256 = _expect_sha256(envelope, "payload_sha256")
    digest = hashlib.sha256(rfc8785.dumps(payload)).digest()
    if digest.hex() != payload_sha256:
        raise SonaApprovalError("Sona approval payload hash mismatch")
    if _expect_string(envelope, "signature_algorithm") != ES256_P1363:
        raise SonaApprovalError("Sona approval signature algorithm is unsupported")
    signature = _expect_string(envelope, "signature_b64url")
    try:
        verify_p1363(trusted_reviewer_spki, domain, digest, signature)
    except (ProfilePairingError, TypeError, ValueError) as error:
        raise SonaApprovalError("Sona approval signature is invalid") from error
    canonical_envelope = rfc8785.dumps(envelope)
    return payload, payload_sha256, hashlib.sha256(canonical_envelope).hexdigest()


def _verify_common_payload(
    payload: Mapping[str, Any],
    *,
    kind: str,
    trusted_reviewer_spki: bytes,
    at_ms: int,
) -> None:
    if _expect_int(payload, "schema_version") != SONA_APPROVAL_SCHEMA_VERSION:
        raise SonaApprovalError("Sona approval schema version is unsupported")
    if _expect_string(payload, "approval_kind") != kind:
        raise SonaApprovalError("Sona approval kind is invalid")
    if _expect_string(payload, "decision") != "APPROVED":
        raise SonaApprovalError("Sona approval decision is not APPROVED")
    if _expect_string(payload, "purpose") != SONA_QUALITY_PURPOSE:
        raise SonaApprovalError("Sona approval purpose is invalid")
    if _expect_bool(payload, "synthetic"):
        raise SonaApprovalError("Synthetic Sona evidence cannot be quality approved")
    if _expect_string(payload, "data_classification") != SONA_DATA_CLASSIFICATION:
        raise SonaApprovalError("Sona approval data classification is invalid")
    if _expect_string(payload, "privacy_delete_action") != SONA_PRIVACY_DELETE_ACTION:
        raise SonaApprovalError("Sona approval privacy-delete action is invalid")
    approved_at_ms = _expect_int(payload, "approved_at_ms")
    not_after_ms = _expect_int(payload, "not_after_ms")
    if approved_at_ms < 0 or not approved_at_ms <= at_ms < not_after_ms:
        raise SonaApprovalError("Sona approval is not valid at the requested time")
    expected_thumbprint = public_key_thumbprint(trusted_reviewer_spki).hex()
    if _expect_string(payload, "reviewer_key_thumbprint_sha256") != expected_thumbprint:
        raise SonaApprovalError("Sona approval reviewer key mismatch")


def _load_bounded_document(path: Path) -> dict[str, Any]:
    if path.stat().st_size > SONA_APPROVAL_MAX_BYTES:
        raise SonaApprovalError("Sona approval document exceeds the accepted bound")
    try:
        parsed = cast(Any, json.loads(path.read_bytes()))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SonaApprovalError("Sona approval document is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise SonaApprovalError("Sona approval document must be an object")
    return cast(dict[str, Any], parsed)


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    if set(value) != expected:
        raise SonaApprovalError(f"{field} has missing or unknown fields")


def _expect_object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = value.get(field)
    if not isinstance(result, dict):
        raise SonaApprovalError(f"Sona approval {field} must be an object")
    return cast(dict[str, Any], result)


def _expect_string(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise SonaApprovalError(f"Sona approval {field} must be a string")
    return result


def _expect_sha256(value: Mapping[str, Any], field: str) -> str:
    result = _expect_string(value, field)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise SonaApprovalError(f"Sona approval {field} must be a lowercase SHA-256 digest")
    return result


def _expect_int(value: Mapping[str, Any], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool):
        raise SonaApprovalError(f"Sona approval {field} must be an integer")
    return result


def _expect_positive_int(value: Mapping[str, Any], field: str) -> int:
    result = _expect_int(value, field)
    if result <= 0:
        raise SonaApprovalError(f"Sona approval {field} must be positive")
    return result


def _expect_non_negative_int(value: Mapping[str, Any], field: str) -> int:
    result = _expect_int(value, field)
    if result < 0:
        raise SonaApprovalError(f"Sona approval {field} must be non-negative")
    return result


def _expect_bool(value: Mapping[str, Any], field: str) -> bool:
    result = value.get(field)
    if not isinstance(result, bool):
        raise SonaApprovalError(f"Sona approval {field} must be a boolean")
    return result


def _expect_label(value: Mapping[str, Any], field: str) -> str:
    result = _expect_string(value, field)
    if not 1 <= len(result) <= 64 or any(
        not (character.isascii() and (character.isalnum() or character in "-_."))
        for character in result
    ):
        raise SonaApprovalError(f"Sona approval {field} is invalid")
    return result


__all__ = (
    "SONA_APPROVAL_MAX_BYTES",
    "SONA_APPROVAL_SCHEMA_VERSION",
    "SONA_ARTIFACT_APPROVAL_DOMAIN",
    "SONA_ARTIFACT_APPROVAL_KIND",
    "SONA_DATASET_APPROVAL_DOMAIN",
    "SONA_DATASET_APPROVAL_KIND",
    "SONA_EVALUATION_APPROVAL_DOMAIN",
    "SONA_EVALUATION_APPROVAL_KIND",
    "SONA_LABEL_DELAY_EMBARGO_MS",
    "SONA_SOURCE_APPROVAL_DOMAIN",
    "SONA_SOURCE_APPROVAL_KIND",
    "SONA_SPLIT_POLICY",
    "SonaApprovalError",
    "VerifiedSonaArtifactApproval",
    "VerifiedSonaDatasetApproval",
    "VerifiedSonaEvaluationApproval",
    "VerifiedSonaSourceApproval",
    "compute_sona_dataset_bundle_sha256",
    "load_sona_artifact_approval",
    "load_sona_dataset_approval",
    "load_sona_evaluation_approval",
    "load_sona_source_approval",
    "verify_sona_artifact_approval",
    "verify_sona_dataset_approval",
    "verify_sona_evaluation_approval",
    "verify_sona_source_approval",
)
