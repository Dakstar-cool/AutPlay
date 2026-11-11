"""B1: three separate operations, current authority, one existing cursor only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.device_compatibility import SqlAlchemyDeviceCompatibilityRepository
from autplay.adapters.postgresql.models.resource_admission import ResourceQuotaPolicyRow
from autplay.adapters.postgresql.resource_limits import (
    effective_account_limits,
    lock_resource_admission,
)
from autplay.adapters.postgresql.sync_binding_v2 import (
    DatabaseDeadline,
    SqlAlchemySyncBindingRepository,
    bounded_transaction,
    current_deadline,
    require_binding_storage,
)
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccessTokenClaims, Principal
from autplay.domain.canonical_json import canonical_json_bytes
from autplay.domain.device_capability_state import DeviceCapabilityPolicy, DeviceCapabilityState
from autplay.domain.device_compatibility import (
    MAX_REVISION,
    DeviceCompatibilityError,
    NegotiationIdentity,
    issued_payload,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.sync_binding_v2 import BindingRequest, SyncBindingV2Error, parse_binding_result
from autplay.ports.sync_binding_v2 import SyncBindingRepository


class SyncBindingV2Service:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        identity: ProfilePairingService,
        policy: Callable[[], DeviceCapabilityPolicy],
    ) -> None:
        self._sessions, self._identity, self._policy = sessions, identity, policy

    def using_sessions(self, sessions: sessionmaker[Session]) -> SyncBindingV2Service:
        """Same identity/policy and authoritative DB, request-owned connection."""
        return SyncBindingV2Service(sessions, identity=self._identity, policy=self._policy)

    def discovery(self) -> dict[str, object]:
        try:
            with self._sessions() as session, bounded_transaction(session) as budget:
                identity = self._identity.current_device_contract_identity(session)
                # Missing resource policy/storage is unavailable, never a synthetic
                # empty/qualified result. No quota slot is charged by these reads.
                lock_resource_admission(session)
                if session.get(ResourceQuotaPolicyRow, 1, populate_existing=True) is None:
                    raise SyncBindingV2Error("sync_binding_unavailable", 503)
                require_binding_storage(session)
                policy = self._policy()
                budget.check()
                result = self._identity.sign_sync_binding_discovery(
                    issued_payload(
                        _view(identity, policy.binding_available()), _now(), timedelta(seconds=600)
                    )
                )
            return result
        except (SQLAlchemyError, RuntimeError, ValueError, ResourceAdmissionError) as error:
            raise _failure(error) from None

    def capabilities(self, principal: Principal, claims: AccessTokenClaims) -> dict[str, object]:
        try:
            with self._sessions() as session, bounded_transaction(session) as budget:
                identity = self._identity.current_device_contract_identity(session)
                authority = SqlAlchemyDeviceCompatibilityRepository(session)
                account, device, credential = authority.lock_actor(principal)
                require_binding_storage(session)
                evidence = authority.read_actor_evidence(account, device, deadline=budget.check)
                limits = effective_account_limits(session, principal.user_id)
                authority.lock_revision(identity, principal)
                policy = self._policy()
                # ALL blocking reads (including revision/history/limits) precede
                # this sample. Validation, digest and signing below are pure.
                now = _now()
                thumb = authority.require_current_actor(
                    principal,
                    claims,
                    account,
                    device,
                    credential,
                    now,
                    evidence=evidence,
                    binding=True,
                )
                _require_platform(device.platform, evidence.binding_history_conflict)
                state = DeviceCapabilityState(
                    identity,
                    str(account.user_id),
                    str(device.device_id),
                    device.platform,
                    account.role,
                    thumb,
                    limits,
                    not evidence.binding_history_conflict,
                    policy,
                )
                digest = state.digest()
                budget.check()
                revision = authority.publish_revision(identity, principal, thumb, digest, now)
                result = self._identity.sign_sync_binding_capabilities(
                    issued_payload(
                        {
                            **_view(identity, policy.binding_available()),
                            "user_id": str(account.user_id),
                            "device_id": str(device.device_id),
                            "platform": device.platform,
                            "capability_revision": revision,
                        },
                        now,
                        timedelta(seconds=3600),
                    )
                )
            return result
        except (SQLAlchemyError, RuntimeError, ValueError, ResourceAdmissionError) as error:
            raise _failure(error) from None

    def bind(
        self, principal: Principal, claims: AccessTokenClaims, request: BindingRequest
    ) -> dict[str, object]:
        document, budget = request.document, current_deadline()
        # A single unique-race readback reexecutes ALL checks under the same total
        # deadline. Never return success merely because a conflicting row exists.
        for attempt in range(2):
            try:
                with self._sessions() as session, bounded_transaction(session, budget=budget):
                    result = self._bind(session, principal, claims, document, budget)
                return result  # commit completed; a lost response retries this tuple.
            except IntegrityError as error:
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                if attempt == 0 and constraint == "device_sync_cursor_pkey":
                    continue
                raise SyncBindingV2Error("sync_binding_unavailable", 503) from None
            except (SQLAlchemyError, RuntimeError, ValueError, ResourceAdmissionError) as error:
                raise _failure(error) from None
        raise SyncBindingV2Error("sync_binding_unavailable", 503)

    def _bind(
        self,
        session: Session,
        principal: Principal,
        claims: AccessTokenClaims,
        document: dict[str, object],
        budget: DatabaseDeadline,
    ) -> dict[str, object]:
        identity = self._identity.current_device_contract_identity(session)
        # This repository takes admission -> account/device NO KEY UPDATE ->
        # session UPDATE. It never calls require_device_capacity or legacy bind.
        authority = SqlAlchemyDeviceCompatibilityRepository(session)
        account, device, credential = authority.lock_actor(principal)
        require_binding_storage(session)
        evidence = authority.read_actor_evidence(account, device, deadline=budget.check)
        limits = effective_account_limits(session, principal.user_id)
        revision = authority.lock_revision(identity, principal)
        cursors: SyncBindingRepository = SqlAlchemySyncBindingRepository(session)
        cursor = cursors.lock_cursor(principal.device_id)
        prior_use = (
            False
            if cursor is not None and cursor.journal_epoch is not None
            else cursors.has_prior_use(principal.device_id)
        )
        policy = self._policy()
        # A cursor wait can outlive the bearer. No authority query may be added
        # below this clock; new authority owners must join the reads above it.
        now = _now()
        thumb = authority.require_current_actor(
            principal, claims, account, device, credential, now, evidence=evidence, binding=True
        )
        if document["user_id"] != str(principal.user_id) or document["device_id"] != str(
            principal.device_id
        ):
            raise SyncBindingV2Error("binding_mismatch", 409)
        fields = _identity_fields(identity)
        if any(document["expected_" + key] != value for key, value in fields.items()):
            raise SyncBindingV2Error("sync_binding_identity_changed", 409)
        _require_platform(device.platform, evidence.binding_history_conflict)
        epoch = UUID(cast(str, document["journal_epoch"]))
        if cursor is not None and (
            cursor.user_id != principal.user_id or cursor.device_id != principal.device_id
        ):
            raise SyncBindingV2Error("binding_mismatch", 409)
        if (
            cursor is not None
            and cursor.journal_epoch is not None
            and cursor.journal_epoch != epoch
        ):
            raise SyncBindingV2Error("DEVICE_RESET_REQUIRED", 409)
        established = cursor is not None and cursor.journal_epoch == epoch
        if not established:
            if prior_use or (
                cursor is not None
                and (
                    cursor.last_acked != 0
                    or cursor.last_pulled != 0
                    or cursor.last_successful_sync_at is not None
                )
            ):
                raise SyncBindingV2Error("DEVICE_RESET_REQUIRED", 409)
            if not policy.binding_available():
                raise SyncBindingV2Error("sync_binding_unavailable", 503)
            state = DeviceCapabilityState(
                identity,
                str(account.user_id),
                str(device.device_id),
                device.platform,
                account.role,
                thumb,
                limits,
                not evidence.binding_history_conflict,
                policy,
            )
            if (
                revision is None
                or type(revision.value) is not int
                or not 1 <= revision.value <= MAX_REVISION
                or revision.value != document["expected_capability_revision"]
                or revision.identity_thumbprint != identity.thumbprint
                or revision.device_thumbprint != thumb
                or revision.effective_digest != state.digest()
            ):
                raise SyncBindingV2Error("sync_binding_capability_changed", 409)
            # Refresh pure authority/time/gates immediately before the only write.
            final_now = _now()
            authority.require_current_actor(
                principal,
                claims,
                account,
                device,
                credential,
                final_now,
                evidence=evidence,
                binding=True,
            )
            if self._policy() != policy:
                raise SyncBindingV2Error("sync_binding_unavailable", 503)
            budget.check()
            cursors.bind_unused(principal, epoch, final_now)
        else:
            # Readback never allocates or even updates a capability revision, nor
            # does it assign any cursor timestamp/checkpoint/classification.
            authority.require_current_actor(
                principal,
                claims,
                account,
                device,
                credential,
                _now(),
                evidence=evidence,
                binding=True,
            )
        result = {
            "contract_version": "v2",
            "schema_version": 2,
            "binding_contract_version": 2,
            "protocol_version": 1,
            "state": "BOUND",
            "supported_event_schema_versions": [1],
            "user_id": str(principal.user_id),
            "device_id": str(principal.device_id),
            "server_profile_id": document["server_profile_id"],
            "journal_epoch": str(epoch),
            "platform": device.platform,
            **fields,
        }
        return parse_binding_result(canonical_json_bytes(result)).document


def _identity_fields(identity: NegotiationIdentity) -> dict[str, object]:
    fields = identity.fields()
    return {
        key: fields[key]
        for key in (
            "server_instance_id",
            "identity_epoch",
            "identity_thumbprint_sha256",
            "api_origin",
            "stream_origin",
        )
    }


def _view(identity: NegotiationIdentity, available: bool) -> dict[str, object]:
    return {
        **identity.fields(),
        "binding_contract_version": 2,
        "protocol_version": 1,
        "binding_available": available,
        "platforms": ["ANDROID", "MACOS"] if available else [],
    }


def _require_platform(platform: str, history_conflict: bool) -> None:
    if platform not in {"ANDROID", "MACOS"} or history_conflict:
        raise SyncBindingV2Error("sync_binding_platform_conflict", 409)


def _failure(error: Exception) -> SyncBindingV2Error:
    if isinstance(error, SyncBindingV2Error):
        return error
    if isinstance(error, DeviceCompatibilityError) and error.status == 401:
        return SyncBindingV2Error("authentication_required", 401)
    return SyncBindingV2Error("sync_binding_unavailable", 503)


def _now() -> datetime:
    return datetime.now(UTC)
