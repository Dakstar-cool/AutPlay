"""D1 authority reads and serialized, digest-only capability high-water storage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from autplay.domain.auth import AccessTokenClaims, Principal
from autplay.domain.device_compatibility import (
    MAX_REVISION,
    V2_PLATFORMS,
    DeviceCompatibilityError,
    NegotiationIdentity,
)
from autplay.domain.profile_pairing import ProfilePairingError, public_key_thumbprint
from autplay.ports.sync_binding_v2 import CapabilityRevision, DeviceActorEvidence

from .device_key_history import binding_history_conflicts
from .models.account import DeviceRow, UserAccountRow, UserSessionRow
from .models.device_compatibility import DeviceCapabilityRevisionRow
from .models.profile_pairing import DeviceAdmissionRow, DeviceKeyBlockRow
from .resource_limits import lock_resource_admission


class SqlAlchemyDeviceCompatibilityRepository:
    def __init__(self, session: Session) -> None:
        self._s = session
        self._revision: DeviceCapabilityRevisionRow | None = None

    def lock_actor(self, principal: Principal) -> tuple[UserAccountRow, DeviceRow, UserSessionRow]:
        # The application acquires the M5 identity owner first. Follow the same
        # admission -> account -> device -> session order as every authority writer.
        lock_resource_admission(self._s)
        # key_share=True WITHOUT read=True emits FOR NO KEY UPDATE. Both inbox
        # foreign keys need KEY SHARE while push holds the cursor. Never upgrade.
        account = self._s.scalar(
            select(UserAccountRow)
            .where(UserAccountRow.user_id == principal.user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        device = self._s.scalar(
            select(DeviceRow)
            .where(DeviceRow.device_id == principal.device_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        credential = self._s.get(
            UserSessionRow, principal.session_id, with_for_update=True, populate_existing=True
        )
        if account is None or device is None or credential is None:
            raise DeviceCompatibilityError("authentication_required", 401)
        return account, device, credential

    def read_actor_evidence(
        self,
        account: UserAccountRow,
        device: DeviceRow,
        *,
        deadline: Callable[[], None],
    ) -> DeviceActorEvidence:
        """Load every potentially blocking fact BEFORE the final trusted clock."""
        deadline()
        try:
            if device.public_key is None:
                raise ValueError("missing device key")
            thumbprint = public_key_thumbprint(device.public_key)
            public_key = serialization.load_der_public_key(device.public_key)
            canonical_spki = public_key.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            valid = (
                device.public_key == canonical_spki
                and thumbprint == device.public_key_thumbprint_sha256
            )
        except TypeError, ValueError, UnsupportedAlgorithm, ProfilePairingError:
            return DeviceActorEvidence(None, False, False, False, False)
        block = self._s.get(
            DeviceKeyBlockRow, (account.user_id, thumbprint), populate_existing=True
        )
        conflict = bool(
            self._s.scalar(
                select(
                    exists().where(
                        or_(
                            DeviceRow.public_key == device.public_key,
                            DeviceRow.public_key_thumbprint_sha256 == thumbprint,
                        ),
                        DeviceRow.platform != device.platform,
                    )
                    | exists().where(
                        or_(
                            DeviceAdmissionRow.device_public_key_spki == device.public_key,
                            DeviceAdmissionRow.device_key_thumbprint_sha256 == thumbprint,
                        ),
                        DeviceAdmissionRow.platform != device.platform,
                    )
                )
            )
        )
        history_conflict = binding_history_conflicts(
            self._s, device.public_key, thumbprint, device.platform, deadline=deadline
        )
        return DeviceActorEvidence(
            thumbprint,
            valid,
            block is not None and block.unblocked_at is None,
            conflict,
            history_conflict,
        )

    def require_current_actor(
        self,
        principal: Principal,
        claims: AccessTokenClaims,
        account: UserAccountRow,
        device: DeviceRow,
        credential: UserSessionRow,
        now: datetime,
        *,
        evidence: DeviceActorEvidence,
        binding: bool = False,
    ) -> bytes:
        """Pure validation only: no storage or hidden clock reads after now."""
        if (
            (claims.user_id, claims.device_id, claims.session_id)
            != (principal.user_id, principal.device_id, principal.session_id)
            or claims.not_before > now
            or claims.issued_at > now
            or claims.expires_at <= now
            or account.status != "ACTIVE"
            or account.deleted_at is not None
            or account.role not in {"OWNER", "ADMIN", "USER"}
            or device.user_id != account.user_id
            or device.revoked_at is not None
            or credential.user_id != account.user_id
            or credential.device_id != device.device_id
            or credential.revoked_at is not None
            or credential.expires_at <= now
            or credential.issued_at > now
            or claims.expires_at <= claims.issued_at
            or claims.issued_at < credential.issued_at.replace(microsecond=0)
            or credential.expires_at <= credential.issued_at
            or claims.not_before >= claims.expires_at
        ):
            raise DeviceCompatibilityError("authentication_required", 401)
        if not binding and device.platform not in V2_PLATFORMS:
            raise DeviceCompatibilityError()
        if not evidence.current_key_valid or evidence.blocked or evidence.thumbprint is None:
            raise DeviceCompatibilityError("authentication_required", 401)
        if not binding and evidence.d1_platform_conflict:
            raise DeviceCompatibilityError()
        return evidence.thumbprint

    def lock_revision(
        self, identity: NegotiationIdentity, principal: Principal
    ) -> CapabilityRevision | None:
        scope = (
            identity.server_instance_id,
            identity.identity_epoch,
            principal.user_id,
            principal.device_id,
        )
        self._revision = self._s.get(
            DeviceCapabilityRevisionRow, scope, with_for_update=True, populate_existing=True
        )
        row = self._revision
        return (
            None
            if row is None
            else CapabilityRevision(
                row.capability_revision,
                row.effective_content_sha256,
                row.identity_thumbprint_sha256,
                row.device_key_thumbprint_sha256,
            )
        )

    def publish_revision(
        self,
        identity: NegotiationIdentity,
        principal: Principal,
        device_thumbprint: bytes,
        content_digest: bytes,
        now: datetime,
    ) -> int:
        # Admission/account locks serialize a first insert as well as updates.
        # lock_revision has already completed before the final clock. This
        # allocator performs no authority reads and is NEVER used by bind.
        row = self._revision
        if row is None:
            row = DeviceCapabilityRevisionRow(
                server_instance_id=identity.server_instance_id,
                identity_epoch=identity.identity_epoch,
                user_id=principal.user_id,
                device_id=principal.device_id,
                capability_revision=1,
                effective_content_sha256=content_digest,
                identity_thumbprint_sha256=identity.thumbprint,
                device_key_thumbprint_sha256=device_thumbprint,
                created_at=now,
                updated_at=now,
            )
            self._s.add(row)
            self._revision = row
        else:
            if (
                row.identity_thumbprint_sha256 != identity.thumbprint
                or type(row.capability_revision) is not int
                or not 1 <= row.capability_revision <= MAX_REVISION
            ):
                raise DeviceCompatibilityError()
            if row.effective_content_sha256 != content_digest:
                if row.capability_revision == MAX_REVISION:
                    raise DeviceCompatibilityError()
                row.capability_revision += 1
                row.effective_content_sha256 = content_digest
                row.device_key_thumbprint_sha256 = device_thumbprint
                row.updated_at = max(row.updated_at, now)
            elif row.device_key_thumbprint_sha256 != device_thumbprint:
                raise DeviceCompatibilityError()
        self._s.flush()
        return row.capability_revision
