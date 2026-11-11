"""Connected v2 negotiation; signed views grant no mutation or session authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.device_compatibility import (
    SqlAlchemyDeviceCompatibilityRepository,
)
from autplay.adapters.postgresql.resource_limits import effective_account_limits
from autplay.adapters.postgresql.sync_binding_v2 import bounded_transaction, require_binding_storage
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccessTokenClaims, Principal
from autplay.domain.device_capability_state import DeviceCapabilityPolicy, DeviceCapabilityState
from autplay.domain.device_compatibility import (
    CAPABILITIES_TTL,
    DISCOVERY_TTL,
    V2_PLATFORMS,
    DeviceCompatibilityError,
    NegotiationPolicy,
    issued_payload,
)
from autplay.domain.resource_admission import ResourceAdmissionError


class DeviceCompatibilityService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        identity: ProfilePairingService,
        policy: NegotiationPolicy,
        combined_policy: Callable[[], DeviceCapabilityPolicy] | None = None,
    ) -> None:
        self._sessions, self._identity, self._policy = sessions, identity, policy
        self._combined_policy = combined_policy or (lambda: DeviceCapabilityPolicy(self._policy))

    def discovery(self) -> dict[str, object]:
        try:
            with self._sessions.begin() as session:
                identity = self._identity.device_contract_identity(session, _now())
                families = self._combined_policy().d1.families()
                effective = {
                    **identity.fields(),
                    "families": families,
                    "platforms": list(V2_PLATFORMS) if families else [],
                }
                return self._identity.sign_device_contract_discovery(
                    issued_payload(effective, _now(), DISCOVERY_TTL)
                )
        except (SQLAlchemyError, RuntimeError, ValueError) as error:
            if isinstance(error, DeviceCompatibilityError):
                raise
            raise DeviceCompatibilityError() from error

    def capabilities(self, principal: Principal, claims: AccessTokenClaims) -> dict[str, object]:
        try:
            with self._sessions() as session, bounded_transaction(session) as budget:
                identity = self._identity.device_contract_identity(session, _now())
                repository = SqlAlchemyDeviceCompatibilityRepository(session)
                account, device, credential = repository.lock_actor(principal)
                require_binding_storage(session)
                evidence = repository.read_actor_evidence(account, device, deadline=budget.check)
                limits = effective_account_limits(session, principal.user_id)
                repository.lock_revision(identity, principal)
                policy = self._combined_policy()
                # All authority/history/limit/revision reads and waits finish
                # before this clock. The remaining authority checks are PURE.
                now = _now()
                device_thumbprint = repository.require_current_actor(
                    principal, claims, account, device, credential, now, evidence=evidence
                )
                effective: dict[str, object] = {
                    **identity.fields(),
                    "platform": device.platform,
                    "user_id": str(account.user_id),
                    "device_id": str(device.device_id),
                    "families": policy.d1.families(),
                }
                digest = DeviceCapabilityState(
                    identity,
                    str(account.user_id),
                    str(device.device_id),
                    device.platform,
                    account.role,
                    device_thumbprint,
                    limits,
                    not evidence.binding_history_conflict,
                    policy,
                ).digest()
                revision = repository.publish_revision(
                    identity, principal, device_thumbprint, digest, now
                )
                result = self._identity.sign_device_contract_capabilities(
                    issued_payload(
                        {**effective, "capability_revision": revision}, now, CAPABILITIES_TTL
                    )
                )
            return result
        except (SQLAlchemyError, RuntimeError, ValueError, ResourceAdmissionError) as error:
            if isinstance(error, DeviceCompatibilityError):
                raise
            raise DeviceCompatibilityError() from error


def _now() -> datetime:
    return datetime.now(UTC)
