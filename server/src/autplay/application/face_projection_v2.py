"""Pure, non-activating verification of a signed Face v2 projection lease."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from autplay.application.face_artifact_policy import (
    ROLES,
    ArtifactPolicyEntry,
    FaceArtifactPolicyError,
    RequiredArtifact,
    freeze_face_artifact_policy,
)
from autplay.domain.face_v2_identity import FaceTimelineIdentityV2, FaceV2IdentityError

PROJECTION_LEASE_DOMAIN = b"autplay.face.projection-lease.v2\0"
MAX_PROJECTION_ENVELOPE_BYTES = 65_536
_MAX_INTEGER = 9_007_199_254_740_991
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{86}\Z")
_FIELDS = frozenset(
    {
        "schema_version",
        "projection_id",
        "server_profile_id",
        "user_id",
        "timeline_identity",
        "semantic_key_sha256",
        "result_sha256",
        "byte_size",
        "activation_epoch",
        "policy_generation",
        "redirect_generation",
        "required_role_cardinality",
        "artifact_policy",
        "required_artifact_set_sha256",
        "artifact_policy_list_sha256",
        "offline_lease_ms",
        "derived_output_disposition",
        "source_presentation_map_sha256",
        "issued_at_ms",
        "authorized_until_ms",
        "server_instance_id",
        "server_identity_epoch",
        "server_identity_thumbprint_sha256",
        "server_key_id",
        "state",
        "signature_algorithm",
        "signature_b64url",
    }
)
_POLICY_FIELDS = frozenset(
    {
        "role",
        "artifact_sha256",
        "decision_sequence",
        "decision_generation",
        "max_offline_revocation_lag_ms",
        "disposition",
    }
)


class FaceProjectionV2Error(ValueError):
    def __init__(self, code: str = "face_projection_invalid") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedFaceProjectionV2:
    projection_id: UUID
    server_profile_id: UUID
    user_id: UUID
    identity: FaceTimelineIdentityV2
    result_sha256: bytes
    byte_size: int
    activation_epoch: int
    policy_generation: int
    redirect_generation: int
    issued_at_ms: int
    authorized_until_ms: int
    required_artifact_set_sha256: bytes
    artifact_policy_list_sha256: bytes
    server_instance_id: UUID
    server_identity_epoch: int
    server_identity_thumbprint_sha256: bytes
    server_key_id: str


def verify_face_projection_v2(
    raw: bytes,
    *,
    expected_server_profile_id: UUID,
    expected_user_id: UUID,
    expected_server_instance_id: UUID,
    expected_server_identity_epoch: int,
    expected_server_key_id: str,
    pinned_identity_spki: bytes,
    trusted_now_ms: int,
) -> VerifiedFaceProjectionV2:
    """Verify bytes, ancestry, live-signed policy facts and P-256 signature.

    The caller still has to check current source/activation/license authority and
    the local Media3 source proof before selecting a projection for playback.
    """

    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PROJECTION_ENVELOPE_BYTES:
            raise FaceProjectionV2Error()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if type(document) is not dict or set(document) != _FIELDS:
            raise FaceProjectionV2Error()
        if (
            document["schema_version"] != 2
            or type(document["schema_version"]) is not int
            or document["state"] != "ACTIVE"
            or document["signature_algorithm"] != "ES256-P1363"
        ):
            raise FaceProjectionV2Error()
        projection_id = _uuid(document["projection_id"])
        profile_id = _uuid(document["server_profile_id"])
        user_id = _uuid(document["user_id"])
        server_id = _uuid(document["server_instance_id"])
        if (
            profile_id != expected_server_profile_id
            or user_id != expected_user_id
            or server_id != expected_server_instance_id
            or _integer(document["server_identity_epoch"], minimum=1)
            != expected_server_identity_epoch
            or document["server_key_id"] != expected_server_key_id
        ):
            raise FaceProjectionV2Error()
        key = serialization.load_der_public_key(pinned_identity_spki)
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise FaceProjectionV2Error()
        if (
            _digest(document["server_identity_thumbprint_sha256"])
            != sha256(pinned_identity_spki).digest()
        ):
            raise FaceProjectionV2Error()
        identity_document = document["timeline_identity"]
        if type(identity_document) is not dict:
            raise FaceProjectionV2Error()
        identity = _identity(identity_document)
        if _digest(document["semantic_key_sha256"]) != bytes.fromhex(
            identity.semantic_key()
        ) or _digest(document["source_presentation_map_sha256"]) != bytes.fromhex(
            identity.source_presentation_map_sha256
        ):
            raise FaceProjectionV2Error()
        byte_size = _integer(document["byte_size"], minimum=1, maximum=1_048_576)
        epoch = _integer(document["activation_epoch"], minimum=1)
        policy_generation = _integer(document["policy_generation"], minimum=1)
        redirect_generation = _integer(document["redirect_generation"], minimum=0)
        issued = _integer(document["issued_at_ms"], minimum=0)
        authorized_until = _integer(document["authorized_until_ms"], minimum=1)
        now = _integer(trusted_now_ms, minimum=0)
        if issued > now + 300_000 or now >= authorized_until:
            raise FaceProjectionV2Error("face_projection_expired")
        cardinalities = _cardinalities(document["required_role_cardinality"])
        raw_policy = document["artifact_policy"]
        if type(raw_policy) is not list or not 2 <= len(raw_policy) <= 256:
            raise FaceProjectionV2Error()
        required: list[RequiredArtifact] = []
        decisions: list[ArtifactPolicyEntry] = []
        for item in raw_policy:
            if type(item) is not dict or set(item) != _POLICY_FIELDS:
                raise FaceProjectionV2Error()
            role = item["role"]
            artifact_hash = _digest(item["artifact_sha256"])
            required.append(RequiredArtifact(role, artifact_hash))
            decisions.append(
                ArtifactPolicyEntry(
                    role=role,
                    artifact_sha256=artifact_hash,
                    decision_sequence=_integer(item["decision_sequence"], minimum=1),
                    decision_generation=_integer(item["decision_generation"], minimum=1),
                    max_offline_revocation_lag_ms=_integer(
                        item["max_offline_revocation_lag_ms"], minimum=1
                    ),
                    disposition=item["disposition"],
                    state="APPROVED",
                )
            )
        ordered = sorted(
            required,
            key=lambda entry: (entry.role.encode("ascii"), entry.artifact_sha256),
        )
        if required != ordered:
            raise FaceProjectionV2Error()
        frozen = freeze_face_artifact_policy(required, decisions, cardinalities=cardinalities)
        if (
            frozen.required_set.sha256 != _digest(document["required_artifact_set_sha256"])
            or frozen.policy_list.sha256 != _digest(document["artifact_policy_list_sha256"])
            or frozen.offline_lease_ms
            != _integer(document["offline_lease_ms"], minimum=1, maximum=604_800_000)
            or frozen.derived_output_disposition != document["derived_output_disposition"]
            or authorized_until - issued != frozen.offline_lease_ms
        ):
            raise FaceProjectionV2Error()
        signature_text = document["signature_b64url"]
        if type(signature_text) is not str or _SIGNATURE.fullmatch(signature_text) is None:
            raise FaceProjectionV2Error()
        signature = base64.urlsafe_b64decode(signature_text + "==")
        canonical_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        if len(signature) != 64 or canonical_signature != signature_text:
            raise FaceProjectionV2Error()
        signed = rfc8785.dumps(
            {name: value for name, value in document.items() if name != "signature_b64url"}
        )
        key.verify(
            utils.encode_dss_signature(
                int.from_bytes(signature[:32], "big"),
                int.from_bytes(signature[32:], "big"),
            ),
            PROJECTION_LEASE_DOMAIN + signed,
            ec.ECDSA(hashes.SHA256()),
        )
        return VerifiedFaceProjectionV2(
            projection_id=projection_id,
            server_profile_id=profile_id,
            user_id=user_id,
            identity=identity,
            result_sha256=_digest(document["result_sha256"]),
            byte_size=byte_size,
            activation_epoch=epoch,
            policy_generation=policy_generation,
            redirect_generation=redirect_generation,
            issued_at_ms=issued,
            authorized_until_ms=authorized_until,
            required_artifact_set_sha256=frozen.required_set.sha256,
            artifact_policy_list_sha256=frozen.policy_list.sha256,
            server_instance_id=server_id,
            server_identity_epoch=expected_server_identity_epoch,
            server_identity_thumbprint_sha256=sha256(pinned_identity_spki).digest(),
            server_key_id=expected_server_key_id,
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        binascii.Error,
        InvalidSignature,
        UnsupportedAlgorithm,
        FaceArtifactPolicyError,
        FaceV2IdentityError,
    ) as error:
        if isinstance(error, FaceProjectionV2Error):
            raise
        raise FaceProjectionV2Error() from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FaceProjectionV2Error()
        result[key] = value
    return result


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise FaceProjectionV2Error()
    identifier = UUID(value)
    if str(identifier) != value:
        raise FaceProjectionV2Error()
    return identifier


def _digest(value: object) -> bytes:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise FaceProjectionV2Error()
    return bytes.fromhex(value)


def _integer(value: object, *, minimum: int, maximum: int = _MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FaceProjectionV2Error()
    return value


def _identity(document: dict[str, Any]) -> FaceTimelineIdentityV2:
    identity = FaceTimelineIdentityV2(
        recording_id=_uuid(document["recording_id"]),
        audio_variant_id=_uuid(document["audio_variant_id"]),
        source_sha256=document["source_sha256"],
        decoded_sample_rate=document["decoded_sample_rate"],
        decoded_sample_count=document["decoded_sample_count"],
        source_presentation_map_sha256=document["source_presentation_map_sha256"],
        embedding_model_id=_uuid(document["embedding_model_id"]),
        embedding_manifest_sha256=document["embedding_manifest_sha256"],
        semantic_interpreter_id=_uuid(document["semantic_interpreter_id"]),
        interpreter_manifest_sha256=document["interpreter_manifest_sha256"],
        preprocessing_sha256=document["preprocessing_sha256"],
        calibration_sha256=document["calibration_sha256"],
        execution_profile_sha256=document["execution_profile_sha256"],
    )
    if identity.document() != document:
        raise FaceProjectionV2Error()
    return identity


def _cardinalities(value: object) -> Mapping[str, int]:
    if type(value) is not dict or set(value) != ROLES:
        raise FaceProjectionV2Error()
    return {role: _integer(count, minimum=0, maximum=256) for role, count in value.items()}
