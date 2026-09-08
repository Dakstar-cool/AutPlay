"""Explicit reconstructed-0026 provenance acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
import rfc8785

from autplay.application.sona_source_acceptance import (
    SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
    SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
    build_sona_source_provenance_acceptance,
    derive_reconstructed_server_profile_id,
    load_sona_source_provenance_acceptance,
    materialize_sona_source_provenance_acceptance,
)

ARCHIVE_SHA256 = "84f69171b92f7e2d8a5381b1965828ba8c788f197c3a8ffd730f5add9fcbc526"
OWNER = UUID("00000000-0000-7000-8000-000000000001")


def _acceptance():
    return build_sona_source_provenance_acceptance(
        generation_id="20260901T110359Z-ebb958b5e91e",
        encrypted_archive_sha256=ARCHIVE_SHA256,
        recorded_at_ms=1_788_800_000_000,
    )


def test_acceptance_is_exact_owner_safe_and_round_trips(tmp_path: Path) -> None:
    acceptance = _acceptance()
    path = tmp_path / "acceptance.json"

    assert acceptance.document["temporal_provenance_kind"] == (SONA_SOURCE_TEMPORAL_PROVENANCE_KIND)
    assert acceptance.document["server_profile_replacement_scheme"] == (
        SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME
    )
    assert acceptance.document["provenance_misrepresentation_forbidden"] is True
    assert str(OWNER) not in rfc8785.dumps(acceptance.document).decode("ascii")
    assert materialize_sona_source_provenance_acceptance(acceptance, path) == (
        acceptance.acceptance_sha256
    )
    assert load_sona_source_provenance_acceptance(path) == acceptance
    with pytest.raises(FileExistsError):
        materialize_sona_source_provenance_acceptance(acceptance, path)


def test_server_profile_replacement_is_deterministic_owner_scoped_and_accepted() -> None:
    acceptance = _acceptance()
    other_owner = UUID("00000000-0000-7000-8000-000000000002")

    assert derive_reconstructed_server_profile_id(acceptance, OWNER) == (
        derive_reconstructed_server_profile_id(acceptance, OWNER)
    )
    assert derive_reconstructed_server_profile_id(acceptance, OWNER) != (
        derive_reconstructed_server_profile_id(acceptance, other_owner)
    )


def test_tampered_acceptance_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.json"
    materialize_sona_source_provenance_acceptance(_acceptance(), path)
    envelope = json.loads(path.read_bytes())
    envelope["acceptance"]["owner_data_read_authorized"] = False
    path.write_bytes(rfc8785.dumps(envelope))

    with pytest.raises(ValueError, match="hash mismatch"):
        load_sona_source_provenance_acceptance(path)
