"""Public passkey evidence, separate from application-device authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VerifiedPasskey:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    backup_eligible: bool
    backed_up: bool


@dataclass(frozen=True, slots=True)
class VerifiedPasskeyAssertion:
    sign_count: int
    backup_eligible: bool
    backed_up: bool


@dataclass(frozen=True, slots=True)
class PasskeyMetadata:
    passkey_id: UUID
    label: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PasskeyEvidence:
    passkey_id: UUID
    server_instance_id: UUID
    user_id: UUID
    user_handle: bytes
    credential_id: bytes
    public_key: bytes
    sign_count: int
    backup_eligible: bool


@dataclass(frozen=True, slots=True)
class PasskeyCeremony:
    ceremony_id: UUID
    operation_id: UUID
    purpose: str
    challenge: bytes
    binding_sha256: bytes
    origin: str
    rp_id: str
    expires_at: datetime
    user_handle: bytes | None = None
    user_id: UUID | None = None
    web_session_id: UUID | None = None
    token_generation: int | None = None
    completed_request_sha256: bytes | None = None
    result_id: UUID | None = None
