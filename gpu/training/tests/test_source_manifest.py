"""Owner-safe quality source-manifest construction and publication."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
import rfc8785
from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    SonaSourceProvenanceAcceptance,
    build_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_planning import (
    P11_CANONICAL_REQUEST_SHA256_V1,
    SonaSourceRequestIdentity,
    SonaSourceRequestRekey,
    SonaSourceSplitPlan,
    plan_sona_quality_source_splits,
    rekey_sona_quality_source_plan,
)
from autplay_sona_training.source_manifest import (
    SONA_CATALOG_MANIFEST_KIND,
    SONA_SOURCE_MANIFEST_KIND,
    build_sona_quality_source_manifests,
    materialize_sona_quality_source_manifests,
)

DAY_MS = 24 * 60 * 60 * 1_000
ARCHIVE_SHA256 = "a" * 64


def _acceptance() -> SonaSourceProvenanceAcceptance:
    return build_sona_source_provenance_acceptance(
        generation_id="source-test-generation",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        recorded_at_ms=1_788_800_000_000,
    )


def _rekey_plan() -> SonaSourceSplitPlan:
    requests = tuple(
        SonaSourceRequestIdentity(
            request_sha256=f"{index:064x}",
            owner_lineage_token="a" * 64,
            cutoff_at_ms=day * DAY_MS,
            observed_at_ms=(day + 1) * DAY_MS,
        )
        for index, day in enumerate((0, 8, 17, 19, 27, 30), 1)
    )
    p11_plan = plan_sona_quality_source_splits(
        requests,
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )
    return rekey_sona_quality_source_plan(
        p11_plan,
        tuple(
            SonaSourceRequestRekey(
                p11_request_sha256=value.request_sha256,
                sona_request_sha256=f"{index + 1000:064x}",
                owner_lineage_token=value.owner_lineage_token,
                cutoff_at_ms=value.cutoff_at_ms,
                observed_at_ms=value.observed_at_ms,
            )
            for index, value in enumerate(p11_plan.selected, 1)
        ),
    )


def test_source_manifests_are_exact_content_addressed_and_owner_safe(tmp_path: Path) -> None:
    recording_ids = (
        UUID("00000000-0000-7000-8000-000000000001"),
        UUID("00000000-0000-7000-8000-000000000002"),
    )
    plan = _rekey_plan()

    manifests = build_sona_quality_source_manifests(
        plan,
        recording_ids,
        embedding_snapshot_sha256="b" * 64,
        provenance_acceptance=_acceptance(),
    )

    assert manifests.source_manifest["manifest_kind"] == SONA_SOURCE_MANIFEST_KIND
    assert manifests.catalog_manifest["manifest_kind"] == SONA_CATALOG_MANIFEST_KIND
    assert manifests.source_manifest["request_set_sha256"] == plan.request_set_sha256
    assert manifests.source_manifest["rekey_plan_sha256"] == plan.plan_sha256
    assert manifests.source_manifest["temporal_provenance_kind"] == (
        SONA_SOURCE_TEMPORAL_PROVENANCE_KIND
    )
    assert manifests.source_manifest["provenance_acceptance_sha256"] == (
        _acceptance().acceptance_sha256
    )
    assert manifests.source_manifest["server_profile_replacement_scheme"] == (
        SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME
    )
    assert manifests.source_manifest["catalog_snapshot_sha256"] == (
        manifests.catalog_manifest_sha256
    )
    assert (
        manifests.source_manifest_sha256
        == sha256(rfc8785.dumps(manifests.source_manifest)).hexdigest()
    )
    output = tmp_path / "source"
    assert (
        materialize_sona_quality_source_manifests(manifests, output)
        == manifests.source_manifest_sha256
    )
    assert {path.name for path in output.iterdir()} == {
        "catalog-manifest.json",
        "source-manifest.json",
        "source-rekey-plan.json",
        "source-provenance-acceptance.json",
    }
    serialized = b"".join(path.read_bytes() for path in output.iterdir())
    assert all(str(recording_id).encode() not in serialized for recording_id in recording_ids)
    with pytest.raises(FileExistsError):
        materialize_sona_quality_source_manifests(manifests, output)


def test_source_manifest_rejects_p11_plan_or_duplicate_recordings() -> None:
    plan = _rekey_plan()
    p11_plan = plan_sona_quality_source_splits(
        tuple(
            SonaSourceRequestIdentity(
                request_sha256=f"{index:064x}",
                owner_lineage_token="a" * 64,
                cutoff_at_ms=day * DAY_MS,
                observed_at_ms=(day + 1) * DAY_MS,
            )
            for index, day in enumerate((0, 8, 17, 19, 27, 30), 1)
        ),
        request_identity_kind=P11_CANONICAL_REQUEST_SHA256_V1,
        owner_lineage_key_id="owner-key-v1",
    )
    recording_id = UUID("00000000-0000-7000-8000-000000000001")

    with pytest.raises(ValueError, match="re-key plan"):
        build_sona_quality_source_manifests(
            p11_plan,
            (recording_id,),
            embedding_snapshot_sha256="b" * 64,
            provenance_acceptance=_acceptance(),
        )
    with pytest.raises(ValueError, match="recording membership"):
        build_sona_quality_source_manifests(
            plan,
            (recording_id, recording_id),
            embedding_snapshot_sha256="b" * 64,
            provenance_acceptance=_acceptance(),
        )
