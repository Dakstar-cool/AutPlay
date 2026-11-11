"""D1 SELF transactions; full nonsecret originals, independent gates and M5 authority.

No client custody, header/QR storage or token response cache. Every incoming retry
uses its own strict v2 parser and proof before comparison with retained originals.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain.auth import Principal
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.domain.device_self_v2 import (
    ParsedSelfRequest,
    SelfCeremonyV2,
    SelfCommandV2,
    SelfPlatform,
    SelfV2Error,
    parse_self_request,
    verify_recipient_proof,
)
from autplay.domain.device_self_v2 import (
    SelfOperation as Op,
)
from autplay.domain.profile_pairing import iso8601
from autplay.domain.self_device_pairing import PairingAccount, PairingBinding, SelfPairingError
from autplay.ports.auth import AccessTokenCodec
from autplay.ports.self_device_pairing import (
    SelfPairingRepositoryV2,
    SelfPairingUnitOfWorkFactoryV2,
)


class DeviceSelfServiceV2:
    def __init__(
        self,
        units: SelfPairingUnitOfWorkFactoryV2,
        *,
        enabled: Callable[[], bool],
        access_tokens: AccessTokenCodec,
        access_ttl: timedelta,
        rates: SelfDevicePairingService,
    ) -> None:
        if not timedelta(seconds=1) <= access_ttl <= timedelta(minutes=15):
            raise ValueError("self access TTL")
        self._units, self._enabled, self._access, self._ttl, self._rates = (
            units,
            enabled,
            access_tokens,
            access_ttl,
            rates,
        )

    def rate_gate(self, source: str) -> None:
        # SAME v1 global constant, source HMAC, windows and table. No v2 partition.
        self._rates.rate_gate(source)

    def _gate(self) -> None:
        if not self._enabled():
            raise DeviceCompatibilityError()

    def start(self, actor: Principal, raw: bytes) -> dict[str, Any]:
        parsed = parse_self_request(Op.START, raw)
        request = parsed.document
        identifier = UUID(request["ceremony_id"])
        with self._units() as unit:
            repo = unit.pairing
            identity = repo.lock_identity(UUID(request["expected_server_instance_id"]))
            account = repo.lock_account(actor.user_id)
            now = repo.database_now()
            family = repo.actor_family(actor, now)
            platform, key, thumb = repo.source_key(actor)
            discovered = repo.find(identifier)
            if discovered is not None:
                self._source(repo, discovered, actor, now)
            existing = repo.find(identifier, lock=True)
            now = repo.database_now()
            if repo.actor_family(actor, now) != family:
                raise SelfPairingError()
            if not identity.matches(dict(request)):
                raise SelfV2Error(proof=True)
            if platform != parsed.platform.value:
                raise DeviceCompatibilityError("device_platform_conflict", 409)
            repo.reserve(
                {"ceremony_id": identifier, "start_operation_id": UUID(request["operation_id"])},
                ceremony_id=identifier,
            )
            now = repo.database_now()
            if existing is not None:
                self._source(repo, existing, actor, now)
                if existing.authority_generation != account.authority_generation:
                    raise SelfPairingError("operation_conflict")
                _exact(
                    parsed,
                    existing.start_document,
                    existing.start_request_sha256,
                    existing.start_original_document_sha256,
                )
                _live(existing, now)
                return _status(existing, account, source=True, now=now)
            _fresh(dict(request), now)
            if repo.pending_count(actor.user_id, now) >= 3:
                raise SelfPairingError("self_pairing_rate_limited")
            ceremony = SelfCeremonyV2(
                identifier,
                identity.server_instance_id,
                actor.user_id,
                account.authority_generation,
                actor.device_id,
                family,
                UUID(request["operation_id"]),
                dict(request),
                "OPEN",
                1,
                now,
                now + timedelta(seconds=900),
                source_platform=platform,
                source_public_key_spki=key,
                source_key_thumbprint_sha256=thumb,
                start_request_sha256=parsed.request_sha256,
                start_original_document_sha256=parsed.original_document_sha256,
            )
            authority = repo.current_source_authority(ceremony, actor=actor)
            now = repo.database_now()  # after ALL storage reads; only pure checks before save
            authority.require_current(ceremony, now, actor=actor)
            _fresh(dict(request), now)
            self._gate()
            ceremony.created_at, ceremony.expires_at = now, now + timedelta(seconds=900)
            repo.save(ceremony)
            repo.audit(ceremony, "self_pairing.started", ceremony.start_operation_id, now)
            unit.commit()
            return _status(ceremony, account, source=True, now=now)

    def status(self, actor: Principal, ceremony_id: UUID) -> dict[str, Any]:
        with self._units() as unit:
            row, account, now = self._context(unit.pairing, ceremony_id, actor=actor)
            return _status(row, account, source=True, now=now)

    def claim(self, secret: str, raw: bytes) -> dict[str, Any]:
        parsed = parse_self_request(Op.CLAIM, raw)
        request = parsed.document
        verify_recipient_proof(
            parsed,
            approved_public_key_spki=base64.b64decode(
                request["device_public_key_spki_b64"], validate=True
            ),
            approved_platform=parsed.platform,
        )
        with self._units() as unit:
            repo = unit.pairing
            row, account, now = self._context(
                repo, UUID(request["ceremony_id"]), require_source=True, candidate=parsed
            )
            _trust(row, dict(request))
            _possession(secret, row.start_document["rendezvous_secret_sha256"])
            repo.reserve({"claim_id": UUID(request["claim_id"])}, ceremony_id=row.ceremony_id)
            if row.claim_document is not None:
                _exact(
                    parsed,
                    row.claim_document,
                    row.claim_request_sha256,
                    row.claim_original_document_sha256,
                )
                _live(row, now)
                repo.require_recipient_key(row)
                return _status(row, account, now=now)
            _live(row, now)
            _fresh(dict(request), now)
            if row.state != "OPEN":
                raise SelfPairingError()
            row.claim_id, row.claim_document = UUID(request["claim_id"]), dict(request)
            row.recipient_platform = parsed.platform.value
            row.claim_request_sha256, row.claim_original_document_sha256 = (
                parsed.request_sha256,
                parsed.original_document_sha256,
            )
            repo.require_recipient_key(row)
            authority = repo.current_source_authority(row)
            now = repo.database_now()
            authority.require_current(row, now)
            _live(row, now)
            _fresh(dict(request), now)
            self._gate()
            row.state, row.revision = "CLAIMED", row.revision + 1
            repo.save(row)
            repo.audit(row, "self_pairing.claimed", row.claim_id, now)
            unit.commit()
            return _status(row, account, now=now)

    def poll(self, secret: str, raw: bytes) -> dict[str, Any]:
        parsed = parse_self_request(Op.POLL, raw)
        request = parsed.document
        with self._units() as unit:
            repo = unit.pairing
            row, account, now = self._context(
                repo, UUID(request["ceremony_id"]), pending_source=True
            )
            self._recipient(repo, row, secret, parsed)
            _fresh(dict(request), now)  # EVERY poll, including terminal and gate-off status.
            if row.last_polled_at is not None and now < row.last_polled_at + timedelta(seconds=2):
                raise SelfPairingError("self_pairing_rate_limited")
            row.last_polled_at = now
            repo.save(row)
            unit.commit()
            return _status(row, account, now=now)

    def decide(self, actor: Principal, raw: bytes) -> dict[str, Any]:
        parsed = parse_self_request(Op.DECISION, raw)
        request, operation_id = parsed.document, UUID(parsed.document["operation_id"])
        with self._units() as unit:
            repo = unit.pairing
            row, account, now = self._context(
                repo,
                UUID(request["ceremony_id"]),
                actor=actor,
                recipient=request["action"] == "APPROVE",
            )
            _trust(row, dict(request))
            if parsed.platform.value != row.source_platform:
                raise DeviceCompatibilityError("device_platform_conflict", 409)
            repo.reserve(
                {"decision_operation_id": operation_id},
                ceremony_id=row.ceremony_id,
                command_id=operation_id,
            )
            previous = repo.command(operation_id)
            self._source(repo, row, actor, now)
            now = repo.database_now()
            if previous is not None:
                if (
                    previous.ceremony_id != row.ceremony_id
                    or previous.user_id != actor.user_id
                    or previous.device_id != actor.device_id
                    or previous.family_id != row.source_family_id
                    or previous.source_platform != row.source_platform
                ):
                    raise SelfPairingError("operation_conflict")
                _exact(
                    parsed,
                    previous.original_document,
                    previous.request_hash,
                    previous.original_document_sha256,
                )
                # Receipt is historical status only; a terminal state never reopens.
                return previous.result
            _live(row, now)
            _fresh(dict(request), now)
            if request["expected_revision"] != row.revision:
                raise SelfPairingError("self_pairing_revision_conflict")
            if (
                request["claim_id"] != (str(row.claim_id) if row.claim_id else None)
                or request["claim_request_sha256"]
                != (row.claim_document["request_sha256"] if row.claim_document else None)
                or row.state == "EXCHANGED"
            ):
                raise SelfPairingError("operation_conflict")
            action = request["action"]
            if action == "APPROVE":
                if row.state != "CLAIMED" or request["comparison_code"] != row.comparison_code():
                    raise SelfPairingError()
                repo.require_recipient_key(row)
                next_state = "APPROVED"
            elif action == "REJECT":
                if row.state != "CLAIMED":
                    raise SelfPairingError()
                next_state = "REJECTED"
            else:
                next_state = "CANCELLED"
            authority = repo.current_source_authority(row, actor=actor)
            now = repo.database_now()
            authority.require_current(row, now, actor=actor)
            _live(row, now)
            _fresh(dict(request), now)
            if action == "APPROVE":
                self._gate()
                row.approval_operation_id = operation_id
            row.state = next_state
            row.revision += 1
            repo.save(row)
            result = _status(row, account, source=True, now=now)
            repo.save_command(
                SelfCommandV2(
                    operation_id,
                    row.ceremony_id,
                    actor.user_id,
                    actor.device_id,
                    row.source_family_id,
                    parsed.request_sha256,
                    result,
                    now,
                    source_platform=row.source_platform,
                    original_document=dict(request),
                    original_document_sha256=parsed.original_document_sha256,
                )
            )
            repo.audit(row, "self_pairing." + action.lower(), operation_id, now)
            unit.commit()
            return result

    def exchange(self, secret: str, raw: bytes) -> dict[str, Any]:
        parsed = parse_self_request(Op.EXCHANGE, raw)
        request = parsed.document
        with self._units() as unit:
            repo = unit.pairing
            row, account, now = self._context(
                repo, UUID(request["ceremony_id"]), pending_source=True, binding=True
            )
            self._recipient(repo, row, secret, parsed)
            if request["confirmed_account_id"] != str(account.user_id) or request[
                "approval_operation_id"
            ] != str(row.approval_operation_id):
                raise SelfPairingError()
            repo.reserve(
                {
                    "exchange_id": UUID(request["exchange_id"]),
                    "binding_commit_id": UUID(request["binding_commit_id"]),
                },
                ceremony_id=row.ceremony_id,
            )
            replayed = row.state == "EXCHANGED"
            if replayed:
                _exact(
                    parsed,
                    row.exchange_document,
                    row.exchange_request_sha256,
                    row.exchange_original_document_sha256,
                )
                binding = repo.active_binding(row, now)
            else:
                _live(row, now)
                if row.state != "APPROVED":
                    raise SelfPairingError()
                _fresh(dict(request), now)
                self._gate()
                row.exchange_id, row.exchange_document = UUID(request["exchange_id"]), dict(request)
                row.binding_commit_id = UUID(request["binding_commit_id"])
                row.exchange_request_sha256, row.exchange_original_document_sha256 = (
                    parsed.request_sha256,
                    parsed.original_document_sha256,
                )

                def before_write() -> datetime:
                    nonlocal now
                    authority = repo.current_source_authority(row)
                    now = repo.database_now()
                    authority.require_current(row, now)
                    _live(row, now)
                    _fresh(dict(request), now)
                    self._gate()
                    return now

                binding = repo.create_binding(row, now, before_write=before_write)
                row.result_device_id, row.result_session_id = binding.device_id, binding.session_id
                row.receipt_expires_at = binding.expires_at + timedelta(seconds=300)
                row.state, row.revision = "EXCHANGED", row.revision + 1
                repo.save(row)
                repo.audit(row, "self_pairing.exchanged", row.exchange_id, now)
            now = repo.database_now()
            result = self._binding(row, account, binding, now, replayed)
            unit.commit()
            return result

    def _context(
        self,
        repo: SelfPairingRepositoryV2,
        ceremony_id: UUID,
        *,
        actor: Principal | None = None,
        require_source: bool = False,
        pending_source: bool = False,
        binding: bool = False,
        candidate: ParsedSelfRequest | None = None,
        recipient: bool = False,
    ) -> tuple[SelfCeremonyV2, PairingAccount, datetime]:
        discovered = repo.find(ceremony_id)
        if discovered is None:
            raise SelfPairingError()
        identity = repo.lock_identity(discovered.server_instance_id)
        account = repo.lock_account(discovered.user_id)  # admission -> account
        # The unlocked locator may have waited behind another first commit.
        # Refresh under admission/account serialization BEFORE choosing the
        # existing device/session locks, particularly APPROVED -> EXCHANGED.
        discovered = repo.find(ceremony_id)
        if (
            discovered is None
            or discovered.user_id != account.user_id
            or discovered.server_instance_id != identity.server_instance_id
        ):
            raise SelfPairingError()
        now = repo.database_now()
        if not identity.matches(discovered.start_document):
            raise SelfV2Error(proof=True)
        if discovered.authority_generation != account.authority_generation:
            raise SelfPairingError("operation_conflict")
        # Existing device/session locks precede the ceremony lock. All writers share
        # admission/account serialization; there is no cursor or sync/B1 mutation.
        if actor is not None:
            self._source(repo, discovered, actor, now)
        elif require_source or (
            pending_source
            and discovered.state in {"OPEN", "CLAIMED", "APPROVED"}
            and discovered.expires_at > now
        ):
            repo.require_source(discovered, now)
        if (
            pending_source or require_source or recipient
        ) and discovered.claim_document is not None:
            repo.require_recipient_key(discovered)
        elif candidate is not None:
            repo.require_recipient_key(
                replace(
                    discovered,
                    claim_document=dict(candidate.document),
                    recipient_platform=candidate.platform.value,
                )
            )
        if binding and discovered.state == "EXCHANGED":
            repo.active_binding(discovered, now)
        row = repo.find(ceremony_id, lock=True)
        if row is None:
            raise SelfPairingError()
        if row.authority_generation != account.authority_generation:
            raise SelfPairingError("operation_conflict")
        now = repo.database_now()
        if actor is not None:
            self._source(repo, row, actor, now)
        elif require_source or (
            pending_source and row.state in {"OPEN", "CLAIMED", "APPROVED"} and row.expires_at > now
        ):
            repo.require_source(row, now)
        if binding and row.state == "EXCHANGED":
            repo.active_binding(row, now)
        now = repo.database_now()
        deadline = (
            row.receipt_expires_at
            if row.state == "EXCHANGED"
            else row.expires_at + timedelta(days=1)
        )
        if deadline is None or deadline <= now:
            raise SelfPairingError()
        return row, account, now

    @staticmethod
    def _source(
        repo: SelfPairingRepositoryV2, row: SelfCeremonyV2, actor: Principal, now: datetime
    ) -> None:
        if actor.user_id != row.user_id or actor.device_id != row.source_device_id:
            raise SelfPairingError()
        if repo.actor_family(actor, now) != row.source_family_id:
            raise SelfPairingError()
        repo.require_source(row, now)

    @staticmethod
    def _recipient(
        repo: SelfPairingRepositoryV2, row: SelfCeremonyV2, secret: str, parsed: ParsedSelfRequest
    ) -> None:
        request, claim = parsed.document, row.claim_document
        _trust(row, dict(request))
        if claim is None:
            raise SelfPairingError()
        if parsed.platform.value != row.recipient_platform:
            raise DeviceCompatibilityError("device_platform_conflict", 409)
        if (
            request["claim_id"] != str(row.claim_id)
            or request["claim_request_sha256"] != claim["request_sha256"]
        ):
            raise SelfPairingError()
        _possession(secret, claim["poll_secret_sha256"])
        verify_recipient_proof(
            parsed,
            approved_public_key_spki=base64.b64decode(
                claim["device_public_key_spki_b64"], validate=True
            ),
            approved_platform=SelfPlatform(row.recipient_platform),
        )
        repo.require_recipient_key(row)

    def _binding(
        self,
        row: SelfCeremonyV2,
        account: PairingAccount,
        binding: PairingBinding,
        now: datetime,
        replayed: bool,
    ) -> dict[str, Any]:
        # Preserve SELF's fresh short JWT on exact replay, never resurrect a rotated
        # original session and never retain/re-sign the recipient's pending command.
        expiry = min(now + self._ttl, binding.expires_at)
        if expiry <= now or row.receipt_expires_at is None or row.receipt_expires_at <= now:
            raise SelfPairingError()
        principal = Principal(account.user_id, binding.device_id, binding.session_id, account.role)
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": row.recipient_platform,
            "exchange_id": str(row.exchange_id),
            "binding_commit_id": str(row.binding_commit_id),
            "server_instance_id": str(row.server_instance_id),
            "user_id": str(account.user_id),
            "device_id": str(binding.device_id),
            "session_id": str(binding.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(binding.expires_at),
            "receipt_expires_at": iso8601(row.receipt_expires_at),
            "access_token": self._access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=expiry
            ),
            "access_expires_at": iso8601(expiry),
            "replayed": replayed,
        }

    def cleanup(self, limit: int = 1000) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("cleanup bound")
        with self._units() as unit:
            count = unit.pairing.cleanup(_now(), limit)
            unit.commit()
            return count


def _exact(
    parsed: ParsedSelfRequest,
    original: dict[str, Any] | None,
    digest: bytes | None,
    full_digest: bytes | None,
) -> None:
    if (
        dict(parsed.document) != original
        or parsed.request_sha256 != digest
        or parsed.original_document_sha256 != full_digest
    ):
        raise SelfPairingError("operation_conflict")


def _trust(row: SelfCeremonyV2, request: dict[str, Any]) -> None:
    if request["ceremony_id"] != str(row.ceremony_id) or any(
        request[name] != value
        for name, value in row.start_document.items()
        if name.startswith("expected_")
    ):
        raise SelfV2Error(proof=True)


def _possession(secret: str, expected: str) -> None:
    try:
        SelfDevicePairingService._possession(secret, expected)
    except SelfPairingError:
        raise SelfV2Error(proof=True) from None


def _fresh(request: dict[str, Any], now: datetime) -> None:
    # Preserve the parsed original's nine fractional digits at the +/-120s edge.
    value = request["requested_at"]
    seconds = datetime.fromisoformat(value[:19]).replace(tzinfo=UTC)
    delta = now - seconds
    nanos = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
    fraction = value[20:-1] if value[19:20] == "." else ""
    nanos -= int(fraction.ljust(9, "0")) if fraction else 0
    if abs(nanos) > 120_000_000_000:
        raise SelfPairingError()


def _live(row: SelfCeremonyV2, now: datetime) -> None:
    SelfDevicePairingService._live(row, now)


def _status(
    row: SelfCeremonyV2, account: PairingAccount, *, source: bool = False, now: datetime
) -> dict[str, Any]:
    result = SelfDevicePairingService._status(row, account, source=source, now=now)
    result.update(
        contract_version="v2",
        schema_version=2,
        source_platform=row.source_platform,
        recipient_platform=row.recipient_platform,
    )
    if "account_label" in result:
        result["account_label"] = (
            "".join(
                char for char in result["account_label"] if ord(char) >= 32 and ord(char) != 127
            )[:240]
            or "Account"
        )
    return result


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ("DeviceSelfServiceV2",)
