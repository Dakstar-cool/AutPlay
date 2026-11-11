"""One pure effective policy for the shared D1/B1 revision owner.

Wire views deliberately differ. Neither view's payload digest may be retained as
the shared revision digest. Every serving producer must use this calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autplay.domain.device_compatibility import (
    CAPABILITIES_MAX_BYTES,
    CAPABILITIES_TTL,
    DISCOVERY_MAX_BYTES,
    DISCOVERY_TTL,
    IMPLEMENTED_AND_QUALIFIED_FAMILIES,
    NegotiationIdentity,
    NegotiationPolicy,
)
from autplay.domain.profile_pairing import canonical_sha256
from autplay.domain.resource_admission import AccountLimits
from autplay.domain.sync_binding_v2 import (
    IMPLEMENTED_AND_QUALIFIED_PLATFORMS,
    MAX_BODY_BYTES,
    MAX_DEPTH,
    MAX_INTEGER,
)


@dataclass(frozen=True, slots=True)
class DeviceCapabilityPolicy:
    d1: NegotiationPolicy = field(default_factory=NegotiationPolicy)
    binding_enabled: bool = False
    identity_ready: bool = True
    storage_ready: bool = True

    def binding_available(self) -> bool:
        # D1 qualification means the full path for BOTH supported platforms.
        return (
            self.binding_enabled
            and self.d1.enabled
            and self.identity_ready
            and self.storage_ready
            and frozenset({"ANDROID", "MACOS"}) == IMPLEMENTED_AND_QUALIFIED_PLATFORMS
            and {"enrollment", "session"}.issubset(self.d1.families())
        )


@dataclass(frozen=True, slots=True, repr=False)
class DeviceCapabilityState:
    identity: NegotiationIdentity
    user_id: str
    device_id: str
    platform: str
    role: str
    device_thumbprint: bytes
    limits: AccountLimits
    binding_history_consistent: bool
    policy: DeviceCapabilityPolicy

    def digest(self) -> bytes:
        """No clock, storage, issue/expiry, allocated revision or view kind."""
        return canonical_sha256(
            {
                "effective_policy_version": 1,
                "identity": self.identity.fields(),
                "actor": {
                    "user_id": self.user_id,
                    "device_id": self.device_id,
                    "platform": self.platform,
                    "role": self.role,
                    "device_key_thumbprint_sha256": self.device_thumbprint.hex(),
                },
                "limits": {
                    "devices": self.limits.devices,
                    "playbacks": self.limits.playbacks,
                    "transfers": self.limits.transfers,
                },
                "d1": {
                    "gates": self.policy.d1.gates(),
                    "families": self.policy.d1.families(),
                    "qualified_families": sorted(IMPLEMENTED_AND_QUALIFIED_FAMILIES),
                    "contract_version": "v2",
                    "schema_version": 2,
                    "platforms": ["ANDROID", "MACOS"],
                    "discovery_max_bytes": DISCOVERY_MAX_BYTES,
                    "capabilities_max_bytes": CAPABILITIES_MAX_BYTES,
                    "discovery_ttl_seconds": int(DISCOVERY_TTL.total_seconds()),
                    "capabilities_ttl_seconds": int(CAPABILITIES_TTL.total_seconds()),
                },
                "b1": {
                    "enabled": self.policy.binding_enabled,
                    "identity_ready": self.policy.identity_ready,
                    "storage_ready": self.policy.storage_ready,
                    "qualified_platforms": sorted(IMPLEMENTED_AND_QUALIFIED_PLATFORMS),
                    "binding_available": self.policy.binding_available(),
                    "history_consistent": self.binding_history_consistent,
                    "contract_version": "v2",
                    "schema_version": 2,
                    "binding_contract_version": 2,
                    "protocol_version": 1,
                    "supported_event_schema_versions": [1],
                    "max_body_bytes": MAX_BODY_BYTES,
                    "max_depth": MAX_DEPTH,
                    "max_integer": MAX_INTEGER,
                    "max_origin_characters": 2048,
                    "future_issue_skew_seconds": 120,
                    "lock_timeout_seconds": 5,
                    "statement_timeout_seconds": 5,
                    "database_deadline_seconds": 15,
                    "history_inventory_version": 1,
                    "discovery_max_bytes": 16384,
                    "capabilities_max_bytes": 65536,
                    "discovery_ttl_seconds": 600,
                    "capabilities_ttl_seconds": 3600,
                },
            }
        )
