"""Signed projection bytes bind exact Face lineage and complete license policy."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
import rfc8785
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from autplay.application.face_artifact_policy import (
    ArtifactPolicyEntry,
    RequiredArtifact,
    freeze_face_artifact_policy,
)
from autplay.application.face_projection_v2 import (
    PROJECTION_LEASE_DOMAIN,
    FaceProjectionV2Error,
    VerifiedFaceProjectionV2,
    verify_face_projection_v2,
)
from autplay.domain.face_v2_identity import FaceTimelineIdentityV2

_ROOT = Path(__file__).resolve().parents[2]
_NOW = 1_700_000_001_000
_PROFILE = UUID("50000000-0000-4000-8000-000000000005")
_USER = UUID("60000000-0000-4000-8000-000000000006")
_SERVER = UUID("70000000-0000-4000-8000-000000000007")
_KEY = ec.derive_private_key(7, ec.SECP256R1())
_SPKI = _KEY.public_key().public_bytes(
    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
)


def _identity() -> FaceTimelineIdentityV2:
    value = json.loads((_ROOT / "tests/fixtures/face/v2-identity.json").read_text())
    return FaceTimelineIdentityV2(
        recording_id=UUID(value["recording_id"]),
        audio_variant_id=UUID(value["audio_variant_id"]),
        source_sha256=value["source_sha256"],
        decoded_sample_rate=value["decoded_sample_rate"],
        decoded_sample_count=value["decoded_sample_count"],
        source_presentation_map_sha256=value["source_presentation_map_sha256"],
        embedding_model_id=UUID(value["embedding_model_id"]),
        embedding_manifest_sha256=value["embedding_manifest_sha256"],
        semantic_interpreter_id=UUID(value["semantic_interpreter_id"]),
        interpreter_manifest_sha256=value["interpreter_manifest_sha256"],
        preprocessing_sha256=value["preprocessing_sha256"],
        calibration_sha256=value["calibration_sha256"],
        execution_profile_sha256=value["execution_profile_sha256"],
    )


def _document() -> dict[str, object]:
    identity = _identity()
    required = (
        RequiredArtifact("ENCODER_WEIGHTS", b"a" * 32),
        RequiredArtifact("INTERPRETER_EXPORT", b"b" * 32),
    )
    decisions = (
        ArtifactPolicyEntry(
            "ENCODER_WEIGHTS", b"a" * 32, 2, 2, 172_800_000, "RETAIN_NON_DISTRIBUTABLE", "APPROVED"
        ),
        ArtifactPolicyEntry(
            "INTERPRETER_EXPORT", b"b" * 32, 4, 4, 86_400_000, "DELETE_AFTER_LEASE", "APPROVED"
        ),
    )
    cardinalities = {
        "ENCODER_WEIGHTS": 1,
        "INTERPRETER_EXPORT": 1,
        "CALIBRATION": 0,
        "PREPROCESSING_EXECUTABLE": 0,
        "DECODER_PROBE": 0,
        "TIMELINE_CODEC": 0,
    }
    frozen = freeze_face_artifact_policy(required, decisions, cardinalities=cardinalities)
    issued = _NOW - 1_000
    return {
        "schema_version": 2,
        "projection_id": "80000000-0000-4000-8000-000000000008",
        "server_profile_id": str(_PROFILE),
        "user_id": str(_USER),
        "timeline_identity": identity.document(),
        "semantic_key_sha256": identity.semantic_key(),
        "result_sha256": "c" * 64,
        "byte_size": 2048,
        "activation_epoch": 5,
        "policy_generation": 3,
        "redirect_generation": 0,
        "required_role_cardinality": cardinalities,
        "artifact_policy": [
            {
                "role": entry.role,
                "artifact_sha256": entry.artifact_sha256.hex(),
                "decision_sequence": entry.decision_sequence,
                "decision_generation": entry.decision_generation,
                "max_offline_revocation_lag_ms": entry.max_offline_revocation_lag_ms,
                "disposition": entry.disposition,
            }
            for entry in frozen.policy_entries
        ],
        "required_artifact_set_sha256": frozen.required_set.sha256.hex(),
        "artifact_policy_list_sha256": frozen.policy_list.sha256.hex(),
        "offline_lease_ms": frozen.offline_lease_ms,
        "derived_output_disposition": frozen.derived_output_disposition,
        "source_presentation_map_sha256": identity.source_presentation_map_sha256,
        "issued_at_ms": issued,
        "authorized_until_ms": issued + frozen.offline_lease_ms,
        "server_instance_id": str(_SERVER),
        "server_identity_epoch": 2,
        "server_identity_thumbprint_sha256": sha256(_SPKI).hexdigest(),
        "server_key_id": "fixture-key",
        "state": "ACTIVE",
        "signature_algorithm": "ES256-P1363",
    }


def _signed(document: dict[str, object]) -> bytes:
    payload = deepcopy(document)
    signature_der = _KEY.sign(
        PROJECTION_LEASE_DOMAIN + rfc8785.dumps(cast(Any, payload)),
        ec.ECDSA(hashes.SHA256()),
    )
    r, s = utils.decode_dss_signature(signature_der)
    payload["signature_b64url"] = (
        base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return rfc8785.dumps(cast(Any, payload))


def _verify(
    raw: bytes,
    *,
    expected_user_id: UUID = _USER,
    pinned_identity_spki: bytes = _SPKI,
    trusted_now_ms: int = _NOW,
) -> VerifiedFaceProjectionV2:
    return verify_face_projection_v2(
        raw,
        expected_server_profile_id=_PROFILE,
        expected_user_id=expected_user_id,
        expected_server_instance_id=_SERVER,
        expected_server_identity_epoch=2,
        expected_server_key_id="fixture-key",
        pinned_identity_spki=pinned_identity_spki,
        trusted_now_ms=trusted_now_ms,
    )


def test_signed_face_projection_binds_minimum_lease_and_strictest_disposition() -> None:
    result = _verify(_signed(_document()))
    assert result.identity.semantic_key() == _identity().semantic_key()
    assert result.authorized_until_ms - result.issued_at_ms == 86_400_000
    assert result.result_sha256 == bytes.fromhex("c" * 64)


def test_checked_in_projection_signature_vector() -> None:
    fixture = json.loads(
        (_ROOT / "tests/fixtures/face/v2-projection-lease.json").read_text(encoding="utf-8")
    )
    assert base64.urlsafe_b64decode(fixture["pinned_spki_b64url"] + "==") == _SPKI
    verified = _verify(fixture["envelope_json"].encode("utf-8"))
    assert verified.activation_epoch == 5
    assert verified.authorized_until_ms - verified.issued_at_ms == 86_400_000
    assert verified.server_instance_id == _SERVER
    assert verified.server_identity_epoch == 2
    assert verified.server_identity_thumbprint_sha256 == sha256(_SPKI).digest()


def test_face_projection_rejects_tamper_stale_binding_and_duplicate_keys() -> None:
    original = _document()
    raw = _signed(original)
    for changed in (
        {**original, "projection_id": "90000000-0000-4000-8000-000000000009"},
        {**original, "offline_lease_ms": 604_800_000},
        {
            **original,
            "artifact_policy": list(reversed(cast(list[object], original["artifact_policy"]))),
        },
    ):
        candidate = json.loads(raw)
        candidate.update(changed)
        with pytest.raises(FaceProjectionV2Error):
            _verify(rfc8785.dumps(candidate))
    with pytest.raises(FaceProjectionV2Error):
        _verify(raw.replace(b'"schema_version":2', b'"schema_version":2,"schema_version":2', 1))
    with pytest.raises(FaceProjectionV2Error):
        _verify(raw, expected_user_id=_SERVER)
    with pytest.raises(FaceProjectionV2Error):
        wrong_spki = (
            ec.derive_private_key(8, ec.SECP256R1())
            .public_key()
            .public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        _verify(raw, pinned_identity_spki=wrong_spki)


def test_face_projection_rejects_signed_but_inconsistent_policy() -> None:
    altered_lease = _document()
    altered_lease["offline_lease_ms"] = 172_800_000
    with pytest.raises(FaceProjectionV2Error):
        _verify(_signed(altered_lease))
    missing_role = _document()
    roles = cast(dict[str, int], missing_role["required_role_cardinality"])
    roles["INTERPRETER_EXPORT"] = 0
    with pytest.raises(FaceProjectionV2Error):
        _verify(_signed(missing_role))
    wrong_map = _document()
    wrong_map["source_presentation_map_sha256"] = "0" * 64
    with pytest.raises(FaceProjectionV2Error):
        _verify(_signed(wrong_map))


def test_face_projection_rejects_expired_or_future_signed_lease() -> None:
    raw = _signed(_document())
    with pytest.raises(FaceProjectionV2Error, match="face_projection_expired"):
        _verify(raw, trusted_now_ms=_NOW + 86_400_000)
    future = _document()
    future["issued_at_ms"] = _NOW + 300_001
    future["authorized_until_ms"] = _NOW + 300_001 + 86_400_000
    with pytest.raises(FaceProjectionV2Error, match="face_projection_expired"):
        _verify(_signed(future))
