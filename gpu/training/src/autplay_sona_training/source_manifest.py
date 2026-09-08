"""Owner-safe source manifests derived from one verified Sona re-key plan."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import rfc8785
from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    SonaSourceProvenanceAcceptance,
    verify_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_planning import (
    SONA_INFERENCE_REQUEST_SHA256_V1,
    SONA_SOURCE_REKEY_PLAN_KIND,
    SonaSourceSplitPlan,
    verify_sona_source_split_plan,
)
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona_approval import SONA_DATA_CLASSIFICATION

from .tokenizer import SONA_MAX_TOKENIZER_RECORDINGS

SONA_SOURCE_MANIFEST_KIND = "SONA_OWNER_SOURCE_SNAPSHOT_V2"
SONA_CATALOG_MANIFEST_KIND = "SONA_OWNER_CATALOG_SNAPSHOT_V1"


@dataclass(frozen=True, slots=True)
class SonaQualitySourceManifests:
    """Canonical review documents containing no raw owner or recording identifiers."""

    source_manifest: dict[str, JsonValue]
    source_manifest_sha256: str
    catalog_manifest: dict[str, JsonValue]
    catalog_manifest_sha256: str
    rekey_plan: dict[str, JsonValue]
    rekey_plan_sha256: str
    provenance_acceptance: dict[str, JsonValue]
    provenance_acceptance_sha256: str


def build_sona_quality_source_manifests(
    rekey_plan: SonaSourceSplitPlan,
    recording_ids: tuple[UUID, ...],
    *,
    embedding_snapshot_sha256: str,
    provenance_acceptance: SonaSourceProvenanceAcceptance,
) -> SonaQualitySourceManifests:
    """Build the exact source/catalog manifests accepted by the quality verifier."""

    verify_sona_source_split_plan(rekey_plan)
    verify_sona_source_provenance_acceptance(provenance_acceptance)
    if (
        rekey_plan.request_identity_kind != SONA_INFERENCE_REQUEST_SHA256_V1
        or rekey_plan.document.get("plan_kind") != SONA_SOURCE_REKEY_PLAN_KIND
    ):
        raise ValueError("Sona quality source requires a verified Sona re-key plan")
    _validate_sha256(embedding_snapshot_sha256, "embedding_snapshot_sha256")
    if (
        not recording_ids
        or len(recording_ids) > SONA_MAX_TOKENIZER_RECORDINGS
        or len(recording_ids) != len(set(recording_ids))
    ):
        raise ValueError("Sona quality source recording membership is invalid")
    owner_count = rekey_plan.document.get("owner_count")
    if isinstance(owner_count, bool) or not isinstance(owner_count, int) or owner_count < 1:
        raise ValueError("Sona quality source owner count is invalid")

    recording_set_sha256 = _set_sha256({str(value) for value in recording_ids})
    catalog_manifest: dict[str, JsonValue] = {
        "schema_version": 1,
        "manifest_kind": SONA_CATALOG_MANIFEST_KIND,
        "recording_count": len(recording_ids),
        "recording_set_sha256": recording_set_sha256,
        "contains_raw_owner_ids": False,
    }
    catalog_manifest_sha256 = sha256(rfc8785.dumps(catalog_manifest)).hexdigest()
    source_manifest: dict[str, JsonValue] = {
        "schema_version": 2,
        "manifest_kind": SONA_SOURCE_MANIFEST_KIND,
        "synthetic": False,
        "data_classification": SONA_DATA_CLASSIFICATION,
        "embedding_snapshot_sha256": embedding_snapshot_sha256,
        "catalog_snapshot_sha256": catalog_manifest_sha256,
        "owner_lineage_key_id": rekey_plan.owner_lineage_key_id,
        "owner_count": owner_count,
        "recording_count": len(recording_ids),
        "request_count": len(rekey_plan.selected),
        "request_set_sha256": rekey_plan.request_set_sha256,
        "recording_set_sha256": recording_set_sha256,
        "rekey_plan_sha256": rekey_plan.plan_sha256,
        "temporal_provenance_kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
        "provenance_acceptance_sha256": provenance_acceptance.acceptance_sha256,
        "original_persisted_temporal_snapshots_available": False,
        "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    }
    source_manifest_sha256 = sha256(rfc8785.dumps(source_manifest)).hexdigest()
    return SonaQualitySourceManifests(
        source_manifest=source_manifest,
        source_manifest_sha256=source_manifest_sha256,
        catalog_manifest=catalog_manifest,
        catalog_manifest_sha256=catalog_manifest_sha256,
        rekey_plan=_deep_json_copy(rekey_plan.document),
        rekey_plan_sha256=rekey_plan.plan_sha256,
        provenance_acceptance=_deep_json_copy(provenance_acceptance.document),
        provenance_acceptance_sha256=provenance_acceptance.acceptance_sha256,
    )


def materialize_sona_quality_source_manifests(
    manifests: SonaQualitySourceManifests,
    output_directory: Path,
) -> str:
    """Atomically publish the two verifier manifests and their reviewed re-key evidence."""

    _verify_manifest_bundle(manifests)
    if output_directory.exists():
        raise FileExistsError(f"Sona source manifest output already exists: {output_directory}")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        _write_envelope(
            temporary / "catalog-manifest.json",
            manifests.catalog_manifest,
            manifests.catalog_manifest_sha256,
        )
        _write_envelope(
            temporary / "source-manifest.json",
            manifests.source_manifest,
            manifests.source_manifest_sha256,
        )
        _write_envelope(
            temporary / "source-rekey-plan.json",
            manifests.rekey_plan,
            manifests.rekey_plan_sha256,
        )
        _write_acceptance_envelope(
            temporary / "source-provenance-acceptance.json",
            manifests.provenance_acceptance,
            manifests.provenance_acceptance_sha256,
        )
        os.replace(temporary, output_directory)
        return manifests.source_manifest_sha256
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _verify_manifest_bundle(manifests: SonaQualitySourceManifests) -> None:
    for document, expected in (
        (manifests.source_manifest, manifests.source_manifest_sha256),
        (manifests.catalog_manifest, manifests.catalog_manifest_sha256),
        (manifests.rekey_plan, manifests.rekey_plan_sha256),
        (manifests.provenance_acceptance, manifests.provenance_acceptance_sha256),
    ):
        _validate_sha256(expected, "manifest_sha256")
        if sha256(rfc8785.dumps(document)).hexdigest() != expected:
            raise ValueError("Sona quality source manifest bundle hash mismatch")


def _write_envelope(path: Path, document: dict[str, JsonValue], digest: str) -> None:
    envelope: dict[str, JsonValue] = {"manifest": document, "manifest_sha256": digest}
    path.write_bytes(rfc8785.dumps(envelope))


def _write_acceptance_envelope(path: Path, document: dict[str, JsonValue], digest: str) -> None:
    envelope: dict[str, JsonValue] = {
        "acceptance": document,
        "acceptance_sha256": digest,
    }
    path.write_bytes(rfc8785.dumps(envelope))


def _deep_json_copy(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    value: object = json.loads(rfc8785.dumps(document))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Sona quality source JSON copy is invalid")
    return cast(dict[str, JsonValue], value)


def _set_sha256(values: set[str]) -> str:
    return sha256(rfc8785.dumps(cast(list[JsonValue], sorted(values)))).hexdigest()


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona quality source {field} is invalid")


__all__ = (
    "SONA_CATALOG_MANIFEST_KIND",
    "SONA_SOURCE_MANIFEST_KIND",
    "SonaQualitySourceManifests",
    "build_sona_quality_source_manifests",
    "materialize_sona_quality_source_manifests",
)
