"""Atomic verification of approved three-way Sona quality dataset bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import numpy as np
import rfc8785
from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    SonaSourceProvenanceAcceptance,
    load_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_planning import SONA_SOURCE_REKEY_PLAN_KIND
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona_approval import (
    SONA_DATA_CLASSIFICATION,
    SONA_LABEL_DELAY_EMBARGO_MS,
    SONA_SPLIT_POLICY,
    VerifiedSonaDatasetApproval,
    VerifiedSonaSourceApproval,
    load_sona_dataset_approval,
    load_sona_source_approval,
)

from .dataset import (
    SONA_SOURCE_KIND_OWNER_APPROVED,
    SonaTensorDataset,
    load_sona_dataset,
)
from .quality_trust import load_deployment_sona_reviewer_trust_anchor
from .source_manifest import SONA_CATALOG_MANIFEST_KIND, SONA_SOURCE_MANIFEST_KIND
from .teacher_calibration import (
    SONA_TEACHER_CALIBRATION_POLICY,
    SONA_TEACHER_MANIFEST_KIND,
    SonaTeacherCalibrationSet,
    calibrated_sona_teacher_probabilities,
    fit_sona_teacher_temperatures,
    load_sona_teacher_calibration_set,
    verify_sona_teacher_manifest,
)
from .tokenizer import SonaTokenizerFit, load_sona_tokenizer

SONA_QUALITY_MANIFEST_MAX_BYTES = 1_048_576

_SOURCE_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "manifest_kind",
        "synthetic",
        "data_classification",
        "embedding_snapshot_sha256",
        "catalog_snapshot_sha256",
        "owner_lineage_key_id",
        "owner_count",
        "recording_count",
        "request_count",
        "request_set_sha256",
        "recording_set_sha256",
        "rekey_plan_sha256",
        "temporal_provenance_kind",
        "provenance_acceptance_sha256",
        "original_persisted_temporal_snapshots_available",
        "server_profile_replacement_scheme",
    }
)
_CATALOG_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "manifest_kind",
        "recording_count",
        "recording_set_sha256",
        "contains_raw_owner_ids",
    }
)


@dataclass(frozen=True, slots=True)
class SonaQualityDatasetBundle:
    """Three approved, mutually disjoint splits and all verified ancestry."""

    train: SonaTensorDataset
    validation: SonaTensorDataset
    test: SonaTensorDataset
    tokenizer: SonaTokenizerFit
    source_provenance_acceptance: SonaSourceProvenanceAcceptance
    teacher_calibration: SonaTeacherCalibrationSet
    source_approval: VerifiedSonaSourceApproval
    dataset_approval: VerifiedSonaDatasetApproval
    leakage_audit_sha256: str
    owner_lineage_manifest_sha256: str
    verification_inputs: SonaQualityBundleVerificationInputs


@dataclass(frozen=True, slots=True)
class SonaQualityBundleVerificationInputs:
    """Immutable paths needed to re-verify immediately before publish."""

    train_directory: Path
    validation_directory: Path
    test_directory: Path
    tokenizer_directory: Path
    source_manifest_path: Path
    catalog_manifest_path: Path
    source_rekey_plan_path: Path
    source_provenance_acceptance_path: Path
    teacher_calibration_path: Path
    teacher_manifest_path: Path
    source_approval_path: Path
    dataset_approval_path: Path


def load_quality_approved_sona_dataset_bundle(
    *,
    train_directory: Path,
    validation_directory: Path,
    test_directory: Path,
    tokenizer_directory: Path,
    source_manifest_path: Path,
    catalog_manifest_path: Path,
    source_rekey_plan_path: Path,
    source_provenance_acceptance_path: Path,
    teacher_calibration_path: Path,
    teacher_manifest_path: Path,
    source_approval_path: Path,
    dataset_approval_path: Path,
    at_ms: int,
) -> SonaQualityDatasetBundle:
    """Fail closed unless the complete approved bundle is present and internally exact."""

    reviewer_trust = load_deployment_sona_reviewer_trust_anchor()
    source_approval = load_sona_source_approval(
        source_approval_path,
        trusted_reviewer_spki=reviewer_trust.reviewer_spki,
        at_ms=at_ms,
    )
    provenance_acceptance = load_sona_source_provenance_acceptance(
        source_provenance_acceptance_path
    )
    rekey_plan, rekey_plan_sha256 = _load_manifest(source_rekey_plan_path)
    source_manifest, source_manifest_sha256 = _load_manifest(source_manifest_path)
    _verify_source_manifest(
        source_manifest,
        source_manifest_sha256,
        source_approval,
        provenance_acceptance=provenance_acceptance,
        rekey_plan=rekey_plan,
        rekey_plan_sha256=rekey_plan_sha256,
    )
    catalog_manifest, catalog_manifest_sha256 = _load_manifest(catalog_manifest_path)
    _verify_catalog_manifest(catalog_manifest, catalog_manifest_sha256, source_approval)
    dataset_approval = load_sona_dataset_approval(
        dataset_approval_path,
        trusted_reviewer_spki=reviewer_trust.reviewer_spki,
        source_approval=source_approval,
        at_ms=at_ms,
    )
    teacher_calibration = load_sona_teacher_calibration_set(teacher_calibration_path)
    teacher_manifest, teacher_manifest_sha256 = _load_manifest(teacher_manifest_path)
    _verify_teacher_manifest(
        teacher_manifest,
        teacher_manifest_sha256,
        dataset_approval,
        teacher_calibration,
    )

    datasets = (
        load_sona_dataset(train_directory),
        load_sona_dataset(validation_directory),
        load_sona_dataset(test_directory),
    )
    _verify_exact_directory(
        tokenizer_directory,
        frozenset({"manifest.json", "centroids.npy", "mapping.json"}),
        "tokenizer",
    )
    tokenizer = load_sona_tokenizer(tokenizer_directory)
    _verify_teacher_calibration_dataset(
        datasets[1],
        tokenizer=tokenizer,
        calibration=teacher_calibration,
    )
    _verify_split_artifacts(
        datasets,
        tokenizer=tokenizer,
        source_approval=source_approval,
        dataset_approval=dataset_approval,
        teacher_manifest=teacher_manifest,
        teacher_manifest_sha256=teacher_manifest_sha256,
    )
    owner_lineage_manifest_sha256 = compute_sona_owner_lineage_manifest_sha256(datasets)
    if owner_lineage_manifest_sha256 != dataset_approval.owner_lineage_manifest_sha256:
        raise ValueError("Sona quality bundle owner lineage manifest mismatch")
    owner_tokens = {token for dataset in datasets for token in dataset.owner_lineage_tokens}
    if len(owner_tokens) != source_approval.owner_count:
        raise ValueError("Sona quality bundle owner count does not match its approved source")
    if sum(dataset.example_count for dataset in datasets) != source_approval.request_count:
        raise ValueError("Sona quality bundle request count does not match its approved source")
    request_set_sha256 = _set_sha256(
        {value.decode("ascii") for dataset in datasets for value in dataset.request_sha256.tolist()}
    )
    if request_set_sha256 != source_approval.request_set_sha256:
        raise ValueError(
            "Sona quality bundle request membership does not match its approved source"
        )
    test_request_set = {value.decode("ascii") for value in datasets[2].request_sha256.tolist()}
    if (
        len(test_request_set) != dataset_approval.test_request_count
        or _set_sha256(test_request_set) != dataset_approval.test_request_set_sha256
    ):
        raise ValueError("Sona quality bundle test request membership mismatch")
    recording_set_sha256 = _set_sha256({str(value) for value in tokenizer.recording_ids})
    if recording_set_sha256 != source_approval.recording_set_sha256:
        raise ValueError(
            "Sona quality bundle recording membership does not match its approved source"
        )
    if len(set(tokenizer.recording_ids)) != source_approval.recording_count:
        raise ValueError("Sona quality bundle recording count does not match its approved source")
    leakage_audit_sha256 = compute_sona_leakage_audit_sha256(datasets)
    if leakage_audit_sha256 != dataset_approval.leakage_audit_sha256:
        raise ValueError("Sona quality bundle leakage audit mismatch")

    approval_hash = dataset_approval.approval_sha256
    return SonaQualityDatasetBundle(
        train=replace(datasets[0], quality_eligible=True, quality_approval_sha256=approval_hash),
        validation=replace(
            datasets[1], quality_eligible=True, quality_approval_sha256=approval_hash
        ),
        test=replace(datasets[2], quality_eligible=True, quality_approval_sha256=approval_hash),
        tokenizer=tokenizer,
        source_provenance_acceptance=provenance_acceptance,
        teacher_calibration=teacher_calibration,
        source_approval=source_approval,
        dataset_approval=dataset_approval,
        leakage_audit_sha256=leakage_audit_sha256,
        owner_lineage_manifest_sha256=owner_lineage_manifest_sha256,
        verification_inputs=SonaQualityBundleVerificationInputs(
            train_directory=train_directory,
            validation_directory=validation_directory,
            test_directory=test_directory,
            tokenizer_directory=tokenizer_directory,
            source_manifest_path=source_manifest_path,
            catalog_manifest_path=catalog_manifest_path,
            source_rekey_plan_path=source_rekey_plan_path,
            source_provenance_acceptance_path=source_provenance_acceptance_path,
            teacher_calibration_path=teacher_calibration_path,
            teacher_manifest_path=teacher_manifest_path,
            source_approval_path=source_approval_path,
            dataset_approval_path=dataset_approval_path,
        ),
    )


def reverify_quality_approved_sona_dataset_bundle(
    bundle: SonaQualityDatasetBundle, *, at_ms: int
) -> SonaQualityDatasetBundle:
    """Reload every file and signature, then require the same approved bundle identity."""

    inputs = bundle.verification_inputs
    current = load_quality_approved_sona_dataset_bundle(
        train_directory=inputs.train_directory,
        validation_directory=inputs.validation_directory,
        test_directory=inputs.test_directory,
        tokenizer_directory=inputs.tokenizer_directory,
        source_manifest_path=inputs.source_manifest_path,
        catalog_manifest_path=inputs.catalog_manifest_path,
        source_rekey_plan_path=inputs.source_rekey_plan_path,
        source_provenance_acceptance_path=inputs.source_provenance_acceptance_path,
        teacher_calibration_path=inputs.teacher_calibration_path,
        teacher_manifest_path=inputs.teacher_manifest_path,
        source_approval_path=inputs.source_approval_path,
        dataset_approval_path=inputs.dataset_approval_path,
        at_ms=at_ms,
    )
    original_identity = (
        bundle.source_approval.approval_sha256,
        bundle.dataset_approval.approval_sha256,
        bundle.dataset_approval.dataset_bundle_sha256,
        bundle.train.manifest_sha256,
        bundle.validation.manifest_sha256,
        bundle.test.manifest_sha256,
        bundle.tokenizer.manifest_sha256,
        bundle.source_provenance_acceptance.acceptance_sha256,
        bundle.teacher_calibration.manifest_sha256,
    )
    current_identity = (
        current.source_approval.approval_sha256,
        current.dataset_approval.approval_sha256,
        current.dataset_approval.dataset_bundle_sha256,
        current.train.manifest_sha256,
        current.validation.manifest_sha256,
        current.test.manifest_sha256,
        current.tokenizer.manifest_sha256,
        current.source_provenance_acceptance.acceptance_sha256,
        current.teacher_calibration.manifest_sha256,
    )
    if current_identity != original_identity:
        raise ValueError("Sona quality bundle changed between training and publication")
    return current


def compute_sona_owner_lineage_manifest_sha256(
    datasets: tuple[SonaTensorDataset, SonaTensorDataset, SonaTensorDataset],
) -> str:
    """Hash the exact owner pseudonym union and lineage-key identity."""

    key_ids = {dataset.owner_lineage_key_id for dataset in datasets}
    if len(key_ids) != 1:
        raise ValueError("Sona quality bundle uses multiple owner-lineage keys")
    tokens = sorted({token for dataset in datasets for token in dataset.owner_lineage_tokens})
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "scheme": "HMAC_SHA256_OWNER_UUID_BYTES_V1",
        "owner_lineage_key_id": next(iter(key_ids)),
        "owner_count": len(tokens),
        "tokens": cast(list[JsonValue], tokens),
    }
    return sha256(rfc8785.dumps(document)).hexdigest()


def compute_sona_leakage_audit_sha256(
    datasets: tuple[SonaTensorDataset, SonaTensorDataset, SonaTensorDataset],
) -> str:
    """Recompute exact request/example disjointness and observed split bounds."""

    split_documents: list[JsonValue] = []
    request_sets: list[set[str]] = []
    example_sets: list[set[str]] = []
    for expected_split, dataset in zip(("train", "validation", "test"), datasets, strict=True):
        if dataset.split != expected_split:
            raise ValueError("Sona quality bundle split order is not canonical")
        request_hashes = tuple(value.decode("ascii") for value in dataset.request_sha256.tolist())
        example_hashes = tuple(value.decode("ascii") for value in dataset.example_sha256.tolist())
        request_set = set(request_hashes)
        example_set = set(example_hashes)
        request_sets.append(request_set)
        example_sets.append(example_set)
        split_documents.append(
            {
                "split": expected_split,
                "manifest_sha256": dataset.manifest_sha256,
                "example_count": dataset.example_count,
                "request_set_sha256": _set_sha256(request_set),
                "example_set_sha256": _set_sha256(example_set),
                "cutoff_min_ms": int(dataset.cutoff_at_ms.min()),
                "cutoff_max_ms": int(dataset.cutoff_at_ms.max()),
                "observed_max_ms": int(dataset.observed_at_ms.max()),
            }
        )
    request_overlap_count = _pairwise_overlap_count(request_sets)
    example_overlap_count = _pairwise_overlap_count(example_sets)
    if request_overlap_count or example_overlap_count:
        raise ValueError("Sona quality bundle contains cross-split request or example leakage")
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "split_policy": SONA_SPLIT_POLICY,
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "request_hash_overlap_count": request_overlap_count,
        "example_hash_overlap_count": example_overlap_count,
        "splits": split_documents,
    }
    return sha256(rfc8785.dumps(document)).hexdigest()


def _verify_split_artifacts(
    datasets: tuple[SonaTensorDataset, SonaTensorDataset, SonaTensorDataset],
    *,
    tokenizer: SonaTokenizerFit,
    source_approval: VerifiedSonaSourceApproval,
    dataset_approval: VerifiedSonaDatasetApproval,
    teacher_manifest: dict[str, JsonValue],
    teacher_manifest_sha256: str,
) -> None:
    expected_manifests = (
        dataset_approval.train_manifest_sha256,
        dataset_approval.validation_manifest_sha256,
        dataset_approval.test_manifest_sha256,
    )
    windows = (
        (dataset_approval.train_cutoff_start_ms, dataset_approval.train_cutoff_end_ms),
        (
            dataset_approval.validation_cutoff_start_ms,
            dataset_approval.validation_cutoff_end_ms,
        ),
        (dataset_approval.test_cutoff_start_ms, dataset_approval.test_cutoff_end_ms),
    )
    expected_teacher_key = _expect_string(teacher_manifest, "teacher_key")
    expected_teacher_version = _expect_string(teacher_manifest, "teacher_version")
    for expected_split, expected_manifest, window, dataset in zip(
        ("train", "validation", "test"),
        expected_manifests,
        windows,
        datasets,
        strict=True,
    ):
        if dataset.split != expected_split or dataset.manifest_sha256 != expected_manifest:
            raise ValueError("Sona quality bundle split manifest mismatch")
        if dataset.data_classification != SONA_DATA_CLASSIFICATION:
            raise ValueError("Sona quality bundle dataset classification is not approved")
        if dataset.source_kind != SONA_SOURCE_KIND_OWNER_APPROVED:
            raise ValueError("Synthetic Sona dataset cannot enter a quality bundle")
        if (
            dataset.source_manifest_sha256 != source_approval.source_manifest_sha256
            or dataset.owner_lineage_key_id != source_approval.owner_lineage_key_id
        ):
            raise ValueError("Sona quality bundle source ancestry mismatch")
        if (
            dataset.tokenizer_sha256 != dataset_approval.tokenizer_manifest_sha256
            or dataset.source_model_manifest_sha256 != dataset_approval.source_model_manifest_sha256
        ):
            raise ValueError("Sona quality bundle tokenizer or source model mismatch")
        if (
            dataset.teacher_manifest_sha256 != teacher_manifest_sha256
            or dataset.teacher_key != expected_teacher_key
            or dataset.teacher_version != expected_teacher_version
        ):
            raise ValueError("Sona quality bundle teacher ancestry mismatch")
        if np.any(dataset.cutoff_at_ms < window[0]) or np.any(dataset.cutoff_at_ms > window[1]):
            raise ValueError("Sona quality bundle request escapes its approved time split")
        _validate_dataset_tokenizer_mapping(dataset, tokenizer)
    if (
        tokenizer.manifest_sha256 != dataset_approval.tokenizer_manifest_sha256
        or tokenizer.source_embeddings_sha256 != source_approval.embedding_snapshot_sha256
    ):
        raise ValueError("Sona quality bundle tokenizer ancestry mismatch")


def _verify_source_manifest(
    manifest: dict[str, JsonValue],
    manifest_sha256: str,
    approval: VerifiedSonaSourceApproval,
    *,
    provenance_acceptance: SonaSourceProvenanceAcceptance,
    rekey_plan: dict[str, JsonValue],
    rekey_plan_sha256: str,
) -> None:
    _require_exact_keys(manifest, _SOURCE_MANIFEST_KEYS, "source manifest")
    if (
        manifest_sha256 != approval.source_manifest_sha256
        or _expect_int(manifest, "schema_version") != 2
        or _expect_string(manifest, "manifest_kind") != SONA_SOURCE_MANIFEST_KIND
        or _expect_bool(manifest, "synthetic")
        or _expect_string(manifest, "data_classification") != SONA_DATA_CLASSIFICATION
        or _expect_string(manifest, "embedding_snapshot_sha256")
        != approval.embedding_snapshot_sha256
        or _expect_string(manifest, "catalog_snapshot_sha256") != approval.catalog_snapshot_sha256
        or _expect_string(manifest, "owner_lineage_key_id") != approval.owner_lineage_key_id
        or _expect_int(manifest, "owner_count") != approval.owner_count
        or _expect_int(manifest, "recording_count") != approval.recording_count
        or _expect_int(manifest, "request_count") != approval.request_count
        or _expect_string(manifest, "request_set_sha256") != approval.request_set_sha256
        or _expect_string(manifest, "recording_set_sha256") != approval.recording_set_sha256
    ):
        raise ValueError("Sona quality source manifest does not match its approval")
    if (
        _expect_string(manifest, "rekey_plan_sha256") != rekey_plan_sha256
        or _expect_int(rekey_plan, "schema_version") != 1
        or _expect_string(rekey_plan, "plan_kind") != SONA_SOURCE_REKEY_PLAN_KIND
        or _expect_bool(rekey_plan, "quality_eligible")
        or _expect_string(rekey_plan, "request_set_sha256") != approval.request_set_sha256
        or _expect_int(rekey_plan, "selected_request_count") != approval.request_count
        or _expect_string(rekey_plan, "owner_lineage_key_id") != approval.owner_lineage_key_id
        or _expect_int(rekey_plan, "owner_count") != approval.owner_count
        or _expect_int(rekey_plan, "request_hash_overlap_count") != 0
        or _expect_int(rekey_plan, "owner_time_ordering_violation_count") != 0
        or _expect_int(rekey_plan, "label_boundary_violation_count") != 0
    ):
        raise ValueError("Sona quality source re-key plan does not match its approval")
    if (
        _expect_string(manifest, "temporal_provenance_kind") != SONA_SOURCE_TEMPORAL_PROVENANCE_KIND
        or _expect_string(manifest, "provenance_acceptance_sha256")
        != provenance_acceptance.acceptance_sha256
        or _expect_bool(manifest, "original_persisted_temporal_snapshots_available")
        or _expect_string(manifest, "server_profile_replacement_scheme")
        != SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME
    ):
        raise ValueError("Sona quality source reconstruction provenance is not accepted")


def _verify_catalog_manifest(
    manifest: dict[str, JsonValue],
    manifest_sha256: str,
    approval: VerifiedSonaSourceApproval,
) -> None:
    _require_exact_keys(manifest, _CATALOG_MANIFEST_KEYS, "catalog manifest")
    if (
        manifest_sha256 != approval.catalog_snapshot_sha256
        or _expect_int(manifest, "schema_version") != 1
        or _expect_string(manifest, "manifest_kind") != SONA_CATALOG_MANIFEST_KIND
        or _expect_int(manifest, "recording_count") != approval.recording_count
        or _expect_string(manifest, "recording_set_sha256") != approval.recording_set_sha256
        or _expect_bool(manifest, "contains_raw_owner_ids")
    ):
        raise ValueError("Sona quality catalog manifest does not match its approval")


def _verify_teacher_manifest(
    manifest: dict[str, JsonValue],
    manifest_sha256: str,
    approval: VerifiedSonaDatasetApproval,
    calibration: SonaTeacherCalibrationSet,
) -> None:
    verify_sona_teacher_manifest(manifest, calibration)
    if (
        manifest_sha256 != approval.teacher_manifest_sha256
        or _expect_string(manifest, "source_model_manifest_sha256")
        != approval.source_model_manifest_sha256
    ):
        raise ValueError("Sona quality teacher manifest does not match its approval")


def _verify_teacher_calibration_dataset(
    validation: SonaTensorDataset,
    *,
    tokenizer: SonaTokenizerFit,
    calibration: SonaTeacherCalibrationSet,
) -> None:
    """Require fitted teacher evidence to cover and reproduce the validation split exactly."""

    request_rows = {
        value.decode("ascii"): index
        for index, value in enumerate(validation.request_sha256.tolist())
    }
    if (
        len(request_rows) != validation.example_count
        or len(calibration.examples) != validation.example_count
        or calibration.request_set_sha256 != _set_sha256(set(request_rows))
    ):
        raise ValueError("Sona teacher calibration does not cover the exact validation split")
    fit = fit_sona_teacher_temperatures(calibration)
    tokenizer_mapping = tokenizer.mapping()
    for example in calibration.examples:
        row_index = request_rows.get(example.sona_request_sha256)
        if row_index is None:
            raise ValueError("Sona teacher calibration request is absent from validation")
        expected_rows = []
        for candidate in example.candidates:
            semantic_id = tokenizer_mapping.get(candidate.recording_id)
            if semantic_id is None:
                raise ValueError("Sona teacher calibration candidate is absent from tokenizer")
            expected_rows.append(
                (
                    semantic_id.values,
                    tuple(float(np.float32(value)) for value in candidate.labels),
                    tuple(float(value) for value in candidate.label_mask),
                    tuple(
                        float(np.float32(value))
                        for value in calibrated_sona_teacher_probabilities(
                            candidate.raw_p11_score, fit
                        )
                    ),
                )
            )
        active = validation.candidate_mask[row_index] == 1
        actual_rows = [
            (
                tuple(int(value) for value in semantic_id),
                tuple(float(value) for value in labels),
                tuple(float(value) for value in label_mask),
                tuple(float(value) for value in teacher_probabilities),
            )
            for semantic_id, labels, label_mask, teacher_probabilities in zip(
                validation.candidate_sids[row_index][active],
                validation.ranking_labels[row_index][active],
                validation.ranking_label_mask[row_index][active],
                validation.teacher_probabilities[row_index][active],
                strict=True,
            )
        ]
        if sorted(expected_rows) != sorted(actual_rows):
            raise ValueError(
                "Sona teacher calibration labels or fitted probabilities do not match validation"
            )


def _load_manifest(path: Path) -> tuple[dict[str, JsonValue], str]:
    if path.stat().st_size > SONA_QUALITY_MANIFEST_MAX_BYTES:
        raise ValueError("Sona quality manifest exceeds the accepted bound")
    parsed = cast(JsonValue, json.loads(path.read_bytes()))
    envelope = _expect_object(parsed, "manifest envelope")
    _require_exact_keys(envelope, frozenset({"manifest", "manifest_sha256"}), "manifest envelope")
    manifest = _expect_object(envelope.get("manifest"), "manifest")
    manifest_sha256 = _expect_string(envelope, "manifest_sha256")
    if sha256(rfc8785.dumps(manifest)).hexdigest() != manifest_sha256:
        raise ValueError("Sona quality manifest hash mismatch")
    return manifest, manifest_sha256


def _validate_dataset_tokenizer_mapping(
    dataset: SonaTensorDataset, tokenizer: SonaTokenizerFit
) -> None:
    if tokenizer.centroids.shape[1] != dataset.tokenizer_active_codes_per_level:
        raise ValueError("Sona quality bundle tokenizer active-code count mismatch")
    allowed = {semantic_id.values for semantic_id in tokenizer.semantic_ids}
    rows = (
        dataset.history_sids[dataset.history_mask == 1],
        dataset.candidate_sids[dataset.candidate_mask == 1],
        dataset.target_sids,
    )
    if any(
        tuple(int(code) for code in semantic_id) not in allowed
        for values in rows
        for semantic_id in values
    ):
        raise ValueError("Sona quality bundle Semantic ID is absent from its tokenizer mapping")


def _set_sha256(values: set[str]) -> str:
    return sha256(rfc8785.dumps(sorted(values))).hexdigest()


def _pairwise_overlap_count(values: list[set[str]]) -> int:
    return sum(
        len(values[left] & values[right])
        for left in range(len(values))
        for right in range(left + 1, len(values))
    )


def _verify_exact_directory(directory: Path, expected: frozenset[str], field: str) -> None:
    entries = tuple(directory.iterdir())
    if {entry.name for entry in entries} != expected or any(
        not entry.is_file() or entry.is_symlink() for entry in entries
    ):
        raise ValueError(f"Sona quality {field} directory contains undeclared or unsafe files")


def _require_exact_keys(value: dict[str, JsonValue], expected: frozenset[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"Sona quality {field} has missing or unknown fields")


def _expect_object(value: JsonValue | None, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"Sona quality {field} must be an object")
    return value


def _expect_string(value: dict[str, JsonValue], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise ValueError(f"Sona quality {field} must be a string")
    return result


def _expect_string_list(value: dict[str, JsonValue], field: str) -> list[str]:
    result = value.get(field)
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ValueError(f"Sona quality {field} must be a string list")
    return cast(list[str], result)


def _expect_int(value: dict[str, JsonValue], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"Sona quality {field} must be an integer")
    return result


def _expect_bool(value: dict[str, JsonValue], field: str) -> bool:
    result = value.get(field)
    if not isinstance(result, bool):
        raise ValueError(f"Sona quality {field} must be a boolean")
    return result


__all__ = (
    "SONA_CATALOG_MANIFEST_KIND",
    "SONA_QUALITY_MANIFEST_MAX_BYTES",
    "SONA_SOURCE_MANIFEST_KIND",
    "SONA_TEACHER_CALIBRATION_POLICY",
    "SONA_TEACHER_MANIFEST_KIND",
    "SonaQualityDatasetBundle",
    "compute_sona_leakage_audit_sha256",
    "compute_sona_owner_lineage_manifest_sha256",
    "load_quality_approved_sona_dataset_bundle",
)
