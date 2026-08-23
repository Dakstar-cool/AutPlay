"""P10 Source Adapter boundary with explicit capability and safety contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from autplay.domain.import_identity import IdentityTrack, ParsedImport


class SourceCapability(StrEnum):
    """Closed capability vocabulary for initial adapters."""

    IMPORT = "IMPORT"
    LOCAL_METADATA = "LOCAL_METADATA"
    PUBLIC_METADATA = "PUBLIC_METADATA"
    RELEASE_DISCOVERY = "RELEASE_DISCOVERY"
    PLAYABLE_ACQUISITION = "PLAYABLE_ACQUISITION"


class CredentialRequirement(StrEnum):
    """Whether an adapter can operate without a private account credential."""

    NONE = "NONE"
    OPTIONAL_USER_SECRET = "OPTIONAL_USER_SECRET"
    REQUIRED_USER_SECRET = "REQUIRED_USER_SECRET"
    REQUIRED_OPERATOR_SECRET = "REQUIRED_OPERATOR_SECRET"


class NetworkPolicy(StrEnum):
    """Allowed transport class; private-network access is never implicit."""

    OFFLINE = "OFFLINE"
    PUBLIC_ALLOWLIST_ONLY = "PUBLIC_ALLOWLIST_ONLY"


@dataclass(frozen=True, slots=True)
class AdapterLimits:
    """Bounded execution limits declared before an adapter is invoked."""

    max_input_bytes: int
    max_items: int
    max_results_per_query: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if self.max_input_bytes < 0 or not 1 <= self.max_items <= 100_000:
            raise ValueError("adapter input limits are invalid")
        if not 0 <= self.max_results_per_query <= 100:
            raise ValueError("adapter result limit must be between zero and one hundred")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("adapter timeout must be between zero and sixty seconds")


@dataclass(frozen=True, slots=True)
class SourceAdapterManifest:
    """Versioned capability, credential, provenance, and network declaration."""

    adapter_id: str
    adapter_version: str
    capabilities: tuple[SourceCapability, ...]
    credential_requirement: CredentialRequirement
    network_policy: NetworkPolicy
    limits: AdapterLimits
    provenance_schema_version: str = "1"

    def __post_init__(self) -> None:
        if not 1 <= len(self.adapter_id) <= 200 or not 1 <= len(self.adapter_version) <= 100:
            raise ValueError("adapter identity is invalid")
        if not self.capabilities or len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("adapter capabilities must be non-empty and unique")
        if self.provenance_schema_version != "1":
            raise ValueError("unsupported provenance schema version")
        if (
            self.network_policy is NetworkPolicy.PUBLIC_ALLOWLIST_ONLY
            and self.credential_requirement is CredentialRequirement.REQUIRED_USER_SECRET
        ):
            raise ValueError("initial public adapter cannot require a private credential")


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """Sanitized metadata candidate with stable, non-secret provenance."""

    track: IdentityTrack
    provider_key: str
    external_id: str | None
    market_scope: str
    provenance_code: str

    def __post_init__(self) -> None:
        if not self.provider_key or not self.market_scope or not self.provenance_code:
            raise ValueError("source candidate provenance is incomplete")


class UserExportAdapter(Protocol):
    """Parse one bounded user-owned export without network access."""

    @property
    def manifest(self) -> SourceAdapterManifest: ...

    def parse(self, payload: bytes, *, format_name: str, schema_version: str) -> ParsedImport: ...


class LocalMetadataAdapter(Protocol):
    """Accept client-authorized metadata; server paths and URIs stay out of the contract."""

    @property
    def manifest(self) -> SourceAdapterManifest: ...

    def normalize(self, metadata: dict[str, object]) -> IdentityTrack: ...


class PublicMetadataTransport(Protocol):
    """Injected allowlisted transport; no provider or credential is selected by P10."""

    def search(
        self,
        query: IdentityTrack,
        *,
        limit: int,
        timeout_seconds: float,
    ) -> tuple[SourceCandidate, ...]: ...


class PublicMetadataAdapter(Protocol):
    """Provider-neutral public metadata lookup boundary."""

    @property
    def manifest(self) -> SourceAdapterManifest: ...

    def search(self, query: IdentityTrack, *, limit: int) -> tuple[SourceCandidate, ...]: ...


__all__ = (
    "AdapterLimits",
    "CredentialRequirement",
    "LocalMetadataAdapter",
    "NetworkPolicy",
    "PublicMetadataAdapter",
    "PublicMetadataTransport",
    "SourceAdapterManifest",
    "SourceCandidate",
    "SourceCapability",
    "UserExportAdapter",
)
