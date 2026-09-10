"""Explicit content-addressed acceptance of reconstructed 0026 Sona provenance."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid4, uuid5

import rfc8785

from autplay.domain.recommendations import JsonValue

SONA_SOURCE_PROVENANCE_ACCEPTANCE_KIND: Final = "SONA_0026_RECONSTRUCTION_ACCEPTANCE_V1"
SONA_SOURCE_TEMPORAL_PROVENANCE_KIND: Final = "RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1"
SONA_SOURCE_EXPECTED_ALEMBIC_HEAD: Final = "0026_s1d_guest_room_access"
SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME: Final = "UUID5_ACCEPTANCE_SHA256_OWNER_UUID_HEX_V1"
SONA_RECONSTRUCTED_SERVER_PROFILE_NAMESPACE: Final = UUID("94591d65-2db2-5bd9-99f5-c6c4ee77509f")
SONA_SOURCE_ACCEPTANCE_MAX_BYTES: Final = 65_536

_ACCEPTANCE_KEYS = frozenset(
    {
        "schema_version",
        "acceptance_kind",
        "decision",
        "purpose",
        "generation_id",
        "encrypted_archive_sha256",
        "source_schema_head",
        "temporal_provenance_kind",
        "original_persisted_temporal_snapshots_available",
        "original_server_profile_binding_available",
        "server_profile_replacement_scheme",
        "server_profile_replacement_namespace",
        "owner_data_read_authorized",
        "provenance_misrepresentation_forbidden",
        "contains_owner_identifiers",
        "recorded_at_ms",
    }
)


@dataclass(frozen=True, slots=True)
class SonaSourceProvenanceAcceptance:
    """Exact operator decision permitting only the documented reconstruction."""

    generation_id: str
    encrypted_archive_sha256: str
    recorded_at_ms: int
    document: dict[str, JsonValue]
    acceptance_sha256: str


def build_sona_source_provenance_acceptance(
    *,
    generation_id: str,
    encrypted_archive_sha256: str,
    recorded_at_ms: int,
) -> SonaSourceProvenanceAcceptance:
    """Build the narrow acceptance authorized for one encrypted backup generation."""

    _validate_label(generation_id, "generation_id")
    _validate_sha256(encrypted_archive_sha256, "encrypted_archive_sha256")
    if (
        isinstance(recorded_at_ms, bool)
        or not isinstance(recorded_at_ms, int)
        or recorded_at_ms < 0
    ):
        raise ValueError("Sona source acceptance recorded_at_ms is invalid")
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "acceptance_kind": SONA_SOURCE_PROVENANCE_ACCEPTANCE_KIND,
        "decision": "ACCEPTED",
        "purpose": "R1B_SHADOW_TRAINING_EVALUATION_ONLY",
        "generation_id": generation_id,
        "encrypted_archive_sha256": encrypted_archive_sha256,
        "source_schema_head": SONA_SOURCE_EXPECTED_ALEMBIC_HEAD,
        "temporal_provenance_kind": SONA_SOURCE_TEMPORAL_PROVENANCE_KIND,
        "original_persisted_temporal_snapshots_available": False,
        "original_server_profile_binding_available": False,
        "server_profile_replacement_scheme": SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME,
        "server_profile_replacement_namespace": str(SONA_RECONSTRUCTED_SERVER_PROFILE_NAMESPACE),
        "owner_data_read_authorized": True,
        "provenance_misrepresentation_forbidden": True,
        "contains_owner_identifiers": False,
        "recorded_at_ms": recorded_at_ms,
    }
    acceptance_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaSourceProvenanceAcceptance(
        generation_id=generation_id,
        encrypted_archive_sha256=encrypted_archive_sha256,
        recorded_at_ms=recorded_at_ms,
        document=document,
        acceptance_sha256=acceptance_sha256,
    )


def verify_sona_source_provenance_acceptance(
    acceptance: SonaSourceProvenanceAcceptance,
) -> None:
    """Rebuild an acceptance and require exact semantic and content identity."""

    expected = build_sona_source_provenance_acceptance(
        generation_id=acceptance.generation_id,
        encrypted_archive_sha256=acceptance.encrypted_archive_sha256,
        recorded_at_ms=acceptance.recorded_at_ms,
    )
    if acceptance != expected:
        raise ValueError("Sona source provenance acceptance integrity verification failed")


def derive_reconstructed_server_profile_id(
    acceptance: SonaSourceProvenanceAcceptance,
    owner_user_id: UUID,
) -> UUID:
    """Derive the accepted non-authoritative profile guard for one source owner."""

    verify_sona_source_provenance_acceptance(acceptance)
    return uuid5(
        SONA_RECONSTRUCTED_SERVER_PROFILE_NAMESPACE,
        f"{acceptance.acceptance_sha256}:{owner_user_id.hex}",
    )


def materialize_sona_source_provenance_acceptance(
    acceptance: SonaSourceProvenanceAcceptance,
    path: Path,
) -> str:
    """Publish one flushed acceptance envelope without replacing an existing file."""

    verify_sona_source_provenance_acceptance(acceptance)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    envelope: dict[str, JsonValue] = {
        "acceptance": acceptance.document,
        "acceptance_sha256": acceptance.acceptance_sha256,
    }
    try:
        with temporary.open("xb") as handle:
            handle.write(rfc8785.dumps(envelope))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return acceptance.acceptance_sha256


def load_sona_source_provenance_acceptance(path: Path) -> SonaSourceProvenanceAcceptance:
    """Load, bound and fully rederive one acceptance envelope."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("Sona source acceptance path is not a regular file")
    if path.stat().st_size > SONA_SOURCE_ACCEPTANCE_MAX_BYTES:
        raise ValueError("Sona source acceptance artifact exceeds the accepted bound")
    parsed = cast(object, json.loads(path.read_bytes()))
    envelope = _object(parsed, "acceptance envelope")
    if set(envelope) != {"acceptance", "acceptance_sha256"}:
        raise ValueError("Sona source acceptance envelope keys are invalid")
    document = _object(envelope.get("acceptance"), "acceptance document")
    if set(document) != _ACCEPTANCE_KEYS:
        raise ValueError("Sona source acceptance document keys are invalid")
    acceptance_sha256 = _string(envelope, "acceptance_sha256")
    _validate_sha256(acceptance_sha256, "acceptance_sha256")
    if sha256(rfc8785.dumps(cast(JsonValue, document))).hexdigest() != acceptance_sha256:
        raise ValueError("Sona source acceptance artifact hash mismatch")
    acceptance = build_sona_source_provenance_acceptance(
        generation_id=_string(document, "generation_id"),
        encrypted_archive_sha256=_string(document, "encrypted_archive_sha256"),
        recorded_at_ms=_integer(document, "recorded_at_ms"),
    )
    if acceptance.document != document or acceptance.acceptance_sha256 != acceptance_sha256:
        raise ValueError("Sona source acceptance artifact contents are invalid")
    return acceptance


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"Sona source {field} is invalid")
    return cast(dict[str, object], value)


def _string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ValueError(f"Sona source {field} is invalid")
    return candidate


def _integer(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if not isinstance(candidate, int) or isinstance(candidate, bool):
        raise ValueError(f"Sona source {field} is invalid")
    return candidate


def _validate_label(value: str, field: str) -> None:
    if not 1 <= len(value) <= 200 or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise ValueError(f"Sona source {field} is invalid")


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona source {field} is not a lowercase SHA-256 digest")


__all__ = (
    "SONA_RECONSTRUCTED_SERVER_PROFILE_NAMESPACE",
    "SONA_RECONSTRUCTED_SERVER_PROFILE_SCHEME",
    "SONA_SOURCE_EXPECTED_ALEMBIC_HEAD",
    "SONA_SOURCE_PROVENANCE_ACCEPTANCE_KIND",
    "SONA_SOURCE_TEMPORAL_PROVENANCE_KIND",
    "SonaSourceProvenanceAcceptance",
    "build_sona_source_provenance_acceptance",
    "derive_reconstructed_server_profile_id",
    "load_sona_source_provenance_acceptance",
    "materialize_sona_source_provenance_acceptance",
    "verify_sona_source_provenance_acceptance",
)
