"""Closed v2 negotiation policy and exact, bounded server envelopes."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import ec

from autplay.domain.canonical_json import canonical_json_bytes
from autplay.domain.profile_pairing import canonical_sha256, iso8601, sign_p1363

MAX_REVISION: Final = 9_007_199_254_740_991
DISCOVERY_MAX_BYTES: Final = 16_384
CAPABILITIES_MAX_BYTES: Final = 65_536
DISCOVERY_TTL: Final = timedelta(seconds=600)
CAPABILITIES_TTL: Final = timedelta(seconds=3600)
DISCOVERY_DOMAIN: Final = "autplay:device-compatibility:negotiation:discovery:v2\n"
CAPABILITIES_DOMAIN: Final = "autplay:device-compatibility:negotiation:capabilities:v2\n"
V2_PLATFORMS: Final = ("ANDROID", "MACOS")
# Source-owned qualification registry. A v1 implementation or an operator flag
# cannot add an entry. Each entry requires the entire v2 path and qualification.
IMPLEMENTED_AND_QUALIFIED_FAMILIES: Final[frozenset[str]] = frozenset()


class DeviceCompatibilityError(RuntimeError):
    """Sanitized negotiation failure; never carries tokens or identity material."""

    def __init__(self, code: str = "device_contract_unavailable", status: int = 503) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NegotiationIdentity:
    """A view of the existing M5 owner, never another mutable identity owner."""

    server_instance_id: UUID
    identity_epoch: int
    public_spki: bytes
    thumbprint: bytes
    api_origin: str
    stream_origin: str

    def fields(self) -> dict[str, object]:
        if (
            not isinstance(self.server_instance_id, UUID)
            or type(self.identity_epoch) is not int
            or not 1 <= self.identity_epoch <= MAX_REVISION
            or len(self.thumbprint) != 32
        ):
            raise DeviceCompatibilityError()
        for origin in (self.api_origin, self.stream_origin):
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError as error:
                raise DeviceCompatibilityError() from error
            if (
                not 1 <= len(origin) <= 2048
                or not origin.isascii()
                or any(ord(character) <= 32 or ord(character) == 127 for character in origin)
                or parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "?" in origin
                or "#" in origin
                or (port is not None and not 1 <= port <= 65535)
            ):
                raise DeviceCompatibilityError()
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "server_instance_id": str(self.server_instance_id),
            "identity_epoch": self.identity_epoch,
            "identity_public_key_spki_b64": base64.b64encode(self.public_spki).decode("ascii"),
            "identity_thumbprint_sha256": self.thumbprint.hex(),
            "api_origin": self.api_origin,
            "stream_origin": self.stream_origin,
            "api_major": 1,
        }


@dataclass(frozen=True, slots=True)
class NegotiationPolicy:
    enabled: bool = False
    portable_enabled: bool = False
    self_enabled: bool = False
    recovery_enabled: bool = False
    deletion_enabled: bool = False
    deletion_evidence_ready: bool = False

    def gates(self) -> dict[str, object]:
        return {
            "device_compatibility_v2_enabled": self.enabled,
            "portable_accounts_enabled": self.portable_enabled,
            "self_device_pairing_enabled": self.self_enabled,
            "account_recovery_enabled": self.recovery_enabled,
            "account_deletion_enabled": self.deletion_enabled,
            "deletion_evidence_ready": self.deletion_evidence_ready,
        }

    def families(self) -> list[str]:
        if not self.enabled:
            return []
        permitted = {
            "enrollment": True,
            "admission": True,
            "trusted": True,
            "session": True,
            "portable": self.portable_enabled,
            "self": self.self_enabled,
            "recovery": self.recovery_enabled,
            "deletion": self.deletion_enabled
            and self.recovery_enabled
            and self.deletion_evidence_ready,
        }
        return sorted(
            family for family in IMPLEMENTED_AND_QUALIFIED_FAMILIES if permitted.get(family, False)
        )


def issued_payload(
    effective: dict[str, object], now: datetime, ttl: timedelta
) -> dict[str, object]:
    return {**effective, "issued_at": iso8601(now), "expires_at": iso8601(now + ttl)}


def signed_discovery(
    key: ec.EllipticCurvePrivateKey, payload: dict[str, object]
) -> dict[str, object]:
    return _envelope(key, payload, DISCOVERY_DOMAIN, DISCOVERY_MAX_BYTES)


def signed_capabilities(
    key: ec.EllipticCurvePrivateKey, payload: dict[str, object]
) -> dict[str, object]:
    return _envelope(key, payload, CAPABILITIES_DOMAIN, CAPABILITIES_MAX_BYTES)


def _envelope(
    key: ec.EllipticCurvePrivateKey, payload: dict[str, object], domain: str, maximum: int
) -> dict[str, object]:
    # Reuse the established RFC8785 -> SHA256 -> ECDSA message/P1363 recipe.
    digest = canonical_sha256(payload)
    envelope: dict[str, object] = {
        "contract_version": "v2",
        "schema_version": 2,
        "payload": payload,
        "payload_sha256": digest.hex(),
        "signature_algorithm": "ES256-P1363",
        "signature_b64url": sign_p1363(key, domain, digest),
    }
    if len(canonical_json_bytes(envelope)) > maximum:
        raise DeviceCompatibilityError()
    return envelope
