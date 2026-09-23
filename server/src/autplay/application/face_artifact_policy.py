"""Canonical Face artifact identities and fail-closed tuple license derivation."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, cast

import rfc8785

FACE_ARTIFACT_POLICY_LIST_CODEC: Final = "FACE_ARTIFACT_POLICY_LIST_V1"
ROLES: Final = frozenset(
    {
        "ENCODER_WEIGHTS",
        "INTERPRETER_EXPORT",
        "CALIBRATION",
        "PREPROCESSING_EXECUTABLE",
        "DECODER_PROBE",
        "TIMELINE_CODEC",
    }
)
DISPOSITIONS: Final = frozenset({"DELETE_AFTER_LEASE", "RETAIN_NON_DISTRIBUTABLE"})
LICENSE_STATES: Final = frozenset({"LEGACY_UNREVIEWED", "APPROVED", "DENIED", "REVOKED"})
MAX_ENTRIES: Final = 256
MAX_DOCUMENT_BYTES: Final = 65_536
MAX_JSON_INTEGER: Final = 2**53 - 1
MAX_OFFLINE_LEASE_MS: Final = 604_800_000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_DOMAIN = b"autplay.face.required-artifact-set.v1\0"
_POLICY_DOMAIN = b"autplay.face.artifact-policy-list.v1\0"
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class FaceArtifactPolicyError(ValueError):
    """Stable code without artifact payload or licensing text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RequiredArtifact:
    role: str
    artifact_sha256: bytes


@dataclass(frozen=True, slots=True)
class ArtifactPolicyEntry:
    role: str
    artifact_sha256: bytes
    decision_sequence: int
    decision_generation: int
    max_offline_revocation_lag_ms: int
    disposition: str
    state: str


@dataclass(frozen=True, slots=True)
class CanonicalPolicyDocument:
    canonical_bytes: bytes
    sha256: bytes


@dataclass(frozen=True, slots=True)
class FrozenFaceArtifactPolicy:
    required_entries: tuple[RequiredArtifact, ...]
    policy_entries: tuple[ArtifactPolicyEntry, ...]
    required_set: CanonicalPolicyDocument
    policy_list: CanonicalPolicyDocument
    offline_lease_ms: int
    derived_output_disposition: str


def _identity(role: object, artifact_sha256: object) -> tuple[str, bytes]:
    if type(role) is not str or role not in ROLES:
        raise FaceArtifactPolicyError("ml.face.artifact_role_invalid")
    if type(artifact_sha256) is not bytes or len(artifact_sha256) != 32:
        raise FaceArtifactPolicyError("ml.face.artifact_hash_invalid")
    return role, artifact_sha256


def _unsigned_integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_JSON_INTEGER:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    return value


def _sorted_required(entries: Sequence[RequiredArtifact]) -> tuple[RequiredArtifact, ...]:
    if len(entries) > MAX_ENTRIES:
        raise FaceArtifactPolicyError("ml.face.artifact_set_too_large")
    seen: set[tuple[str, bytes]] = set()
    for entry in entries:
        if type(entry) is not RequiredArtifact:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
        identity = _identity(entry.role, entry.artifact_sha256)
        if identity in seen:
            raise FaceArtifactPolicyError("ml.face.artifact_duplicate")
        seen.add(identity)
    return tuple(
        sorted(entries, key=lambda entry: (entry.role.encode("ascii"), entry.artifact_sha256))
    )


def _sorted_policy(entries: Sequence[ArtifactPolicyEntry]) -> tuple[ArtifactPolicyEntry, ...]:
    if len(entries) > MAX_ENTRIES:
        raise FaceArtifactPolicyError("ml.face.artifact_set_too_large")
    seen: set[tuple[str, bytes]] = set()
    for entry in entries:
        if type(entry) is not ArtifactPolicyEntry:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
        identity = _identity(entry.role, entry.artifact_sha256)
        if identity in seen:
            raise FaceArtifactPolicyError("ml.face.artifact_duplicate")
        seen.add(identity)
        for value in (
            entry.decision_sequence,
            entry.decision_generation,
            entry.max_offline_revocation_lag_ms,
        ):
            _unsigned_integer(value)
        if entry.disposition not in DISPOSITIONS or entry.state not in LICENSE_STATES:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    return tuple(
        sorted(entries, key=lambda entry: (entry.role.encode("ascii"), entry.artifact_sha256))
    )


def _canonical(document: dict[str, object], domain: bytes) -> CanonicalPolicyDocument:
    raw = rfc8785.dumps(cast(JsonValue, document))
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise FaceArtifactPolicyError("ml.face.artifact_set_too_large")
    return CanonicalPolicyDocument(raw, sha256(domain + raw).digest())


def required_artifact_set(entries: Sequence[RequiredArtifact]) -> CanonicalPolicyDocument:
    """Freeze the sorted role/content set independently of current license decisions."""

    ordered = _sorted_required(entries)
    return _canonical(
        {
            "v": 1,
            "entries": [
                {"role": entry.role, "artifact_sha256": entry.artifact_sha256.hex()}
                for entry in ordered
            ],
        },
        _REQUIRED_DOMAIN,
    )


def policy_list(entries: Sequence[ArtifactPolicyEntry]) -> CanonicalPolicyDocument:
    """Freeze reviewed policy facts; state remains separately checked as live authority."""

    ordered = _sorted_policy(entries)
    return _canonical(
        {
            "v": 1,
            "entries": [
                {
                    "role": entry.role,
                    "artifact_sha256": entry.artifact_sha256.hex(),
                    "decision_sequence": entry.decision_sequence,
                    "decision_generation": entry.decision_generation,
                    "max_offline_revocation_lag_ms": entry.max_offline_revocation_lag_ms,
                    "disposition": entry.disposition,
                }
                for entry in ordered
            ],
        },
        _POLICY_DOMAIN,
    )


def _validate_cardinalities(
    entries: tuple[RequiredArtifact, ...], cardinalities: Mapping[str, int]
) -> None:
    if set(cardinalities) != ROLES or any(
        type(count) is not int or not 0 <= count <= MAX_ENTRIES for count in cardinalities.values()
    ):
        raise FaceArtifactPolicyError("ml.face.artifact_cardinality")
    if (
        cardinalities["ENCODER_WEIGHTS"] < 1
        or cardinalities["INTERPRETER_EXPORT"] < 1
        or dict(Counter(entry.role for entry in entries))
        != {role: count for role, count in cardinalities.items() if count > 0}
    ):
        raise FaceArtifactPolicyError("ml.face.artifact_cardinality")


def freeze_face_artifact_policy(
    entries: Sequence[RequiredArtifact],
    decisions: Sequence[ArtifactPolicyEntry],
    *,
    cardinalities: Mapping[str, int],
) -> FrozenFaceArtifactPolicy:
    """Validate a complete signed-manifest role set and derive its strictest live lease."""

    required = _sorted_required(entries)
    _validate_cardinalities(required, cardinalities)
    current = _sorted_policy(decisions)
    if tuple((entry.role, entry.artifact_sha256) for entry in required) != tuple(
        (entry.role, entry.artifact_sha256) for entry in current
    ):
        raise FaceArtifactPolicyError("ml.face.artifact_policy_mismatch")
    if any(entry.state != "APPROVED" for entry in current):
        raise FaceArtifactPolicyError("ml.face.artifact_not_approved")
    if any(entry.max_offline_revocation_lag_ms == 0 for entry in current):
        raise FaceArtifactPolicyError("ml.face.offline_lease_unavailable")
    lease_ms = min(
        MAX_OFFLINE_LEASE_MS,
        *(entry.max_offline_revocation_lag_ms for entry in current),
    )
    disposition = (
        "DELETE_AFTER_LEASE"
        if any(entry.disposition == "DELETE_AFTER_LEASE" for entry in current)
        else "RETAIN_NON_DISTRIBUTABLE"
    )
    return FrozenFaceArtifactPolicy(
        required,
        current,
        required_artifact_set(required),
        policy_list(current),
        lease_ms,
        disposition,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
        result[key] = value
    return result


def _parse(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or len(raw) > MAX_DOCUMENT_BYTES:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
            ),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid") from error
    if type(document) is not dict or set(document) != {"v", "entries"}:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    if type(document["v"]) is not int or document["v"] != 1:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    if type(document["entries"]) is not list:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    return cast(dict[str, object], document)


def _hex(value: object) -> bytes:
    if type(value) is not str or not _HEX.fullmatch(value):
        raise FaceArtifactPolicyError("ml.face.artifact_hash_invalid")
    return bytes.fromhex(value)


def decode_required_set(raw: bytes) -> CanonicalPolicyDocument:
    document = _parse(raw)
    entries = document["entries"]
    assert isinstance(entries, list)
    parsed: list[RequiredArtifact] = []
    for item in entries:
        if type(item) is not dict or set(item) != {"role", "artifact_sha256"}:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
        parsed.append(RequiredArtifact(item["role"], _hex(item["artifact_sha256"])))
    frozen = required_artifact_set(parsed)
    if raw != frozen.canonical_bytes:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    return frozen


def decode_policy_list(raw: bytes) -> CanonicalPolicyDocument:
    document = _parse(raw)
    entries = document["entries"]
    assert isinstance(entries, list)
    parsed: list[ArtifactPolicyEntry] = []
    for item in entries:
        if type(item) is not dict or set(item) != {
            "role",
            "artifact_sha256",
            "decision_sequence",
            "decision_generation",
            "max_offline_revocation_lag_ms",
            "disposition",
        }:
            raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
        parsed.append(
            ArtifactPolicyEntry(
                role=item["role"],
                artifact_sha256=_hex(item["artifact_sha256"]),
                decision_sequence=item["decision_sequence"],
                decision_generation=item["decision_generation"],
                max_offline_revocation_lag_ms=item["max_offline_revocation_lag_ms"],
                disposition=item["disposition"],
                state="APPROVED",
            )
        )
    frozen = policy_list(parsed)
    if raw != frozen.canonical_bytes:
        raise FaceArtifactPolicyError("ml.face.artifact_policy_invalid")
    return frozen
