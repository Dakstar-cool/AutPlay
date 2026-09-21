"""Two-phone approval and recoverable account-bound device enrollment."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import iso8601
from autplay.domain.self_device_pairing import (
    PairingAccount,
    PairingBinding,
    PairingCommand,
    SelfDevicePairing,
    SelfPairingError,
    fresh,
    parse_request,
    public_document,
    secret_hash,
    verify_proof,
)
from autplay.ports.auth import AccessTokenCodec
from autplay.ports.self_device_pairing import SelfPairingRepository, SelfPairingUnitOfWorkFactory


class SelfDevicePairingService:
    def __init__(
        self,
        units: SelfPairingUnitOfWorkFactory,
        access: AccessTokenCodec,
        access_ttl: timedelta,
        source_secret: bytes,
    ) -> None:
        if len(source_secret) < 32 or not timedelta(seconds=1) <= access_ttl <= timedelta(
            minutes=15
        ):
            raise ValueError("invalid self-pairing configuration")
        self._units, self._access, self._ttl, self._secret = (
            units,
            access,
            access_ttl,
            source_secret,
        )

    def rate_gate(self, source: str) -> None:
        now = datetime.now(UTC)
        source = source if 1 <= len(source) <= 255 else "unknown"
        key = hmac.digest(self._secret, b"self-pairing-source\x00" + source.encode(), "sha256")
        with self._units() as unit:
            allowed = unit.pairing.rate_gate(
                hashlib.sha256(b"self-pairing-global-v1").digest(), 6000, now
            )
            allowed = unit.pairing.rate_gate(key, 1200, now) and allowed
            unit.commit()
        if not allowed:
            raise SelfPairingError("self_pairing_rate_limited")

    def start(self, actor: Principal, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("start", document)
        now = datetime.now(UTC)
        identifier = UUID(request["ceremony_id"])
        with self._units() as unit:
            repo = unit.pairing
            identity = repo.lock_identity(UUID(request["expected_server_instance_id"]))
            account = repo.lock_account(actor.user_id)
            existing = repo.find(identifier, lock=True)
            family = repo.actor_family(actor, now)
            if not identity.matches(request):
                raise SelfPairingError()
            repo.require_unique("start_operation_id", UUID(request["operation_id"]), identifier)
            if existing is not None:
                if (
                    existing.user_id != actor.user_id
                    or existing.source_device_id != actor.device_id
                    or existing.source_family_id != family
                    or existing.authority_generation != account.authority_generation
                    or existing.start_document != request
                ):
                    raise SelfPairingError("operation_conflict")
                self._live(existing, now)
                return self._status(existing, account, source=True)
            fresh(request, now)
            if repo.pending_count(actor.user_id, now) >= 3:
                raise SelfPairingError("self_pairing_rate_limited")
            ceremony = SelfDevicePairing(
                identifier,
                identity.server_instance_id,
                actor.user_id,
                account.authority_generation,
                actor.device_id,
                family,
                UUID(request["operation_id"]),
                request,
                "OPEN",
                1,
                now,
                now + timedelta(minutes=15),
            )
            repo.save(ceremony)
            repo.audit(ceremony, "self_pairing.started", ceremony.start_operation_id, now)
            unit.commit()
            return self._status(ceremony, account, source=True)

    def status(self, actor: Principal, ceremony_id: UUID) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._units() as unit:
            ceremony, account = self._context(unit.pairing, ceremony_id, now, terminal=True)
            self._source(unit.pairing, ceremony, actor, now)
            return self._status(ceremony, account, source=True, now=now)

    def claim(self, secret: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("claim", document)
        verify_proof("claim", request, request)
        now = datetime.now(UTC)
        with self._units() as unit:
            repo = unit.pairing
            ceremony, account = self._context(repo, UUID(request["ceremony_id"]), now)
            self._identity_request(ceremony, request)
            self._possession(secret, ceremony.start_document["rendezvous_secret_sha256"])
            repo.require_source(ceremony, now)
            repo.require_unique("claim_id", UUID(request["claim_id"]), ceremony.ceremony_id)
            if ceremony.claim_document is not None:
                if ceremony.claim_document != public_document(request):
                    raise SelfPairingError("operation_conflict")
                return self._status(ceremony, account)
            fresh(request, now)
            if ceremony.state != "OPEN":
                raise SelfPairingError()
            ceremony.claim_id = UUID(request["claim_id"])
            ceremony.claim_document = public_document(request)
            ceremony.state, ceremony.revision = "CLAIMED", ceremony.revision + 1
            repo.save(ceremony)
            repo.audit(ceremony, "self_pairing.claimed", ceremony.claim_id, now)
            unit.commit()
            return self._status(ceremony, account)

    def poll(self, secret: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("poll", document)
        now = datetime.now(UTC)
        fresh(request, now)
        with self._units() as unit:
            repo = unit.pairing
            ceremony, account = self._context(
                repo, UUID(request["ceremony_id"]), now, terminal=True
            )
            self._new_phone("poll", ceremony, secret, request)
            if ceremony.state not in {"EXCHANGED", "CANCELLED", "REJECTED"}:
                repo.require_source(ceremony, now)
            if ceremony.last_polled_at is not None and now < ceremony.last_polled_at + timedelta(
                seconds=2
            ):
                raise SelfPairingError("self_pairing_rate_limited")
            ceremony.last_polled_at = now
            repo.save(ceremony)
            unit.commit()
            return self._status(ceremony, account, now=now)

    def decide(self, actor: Principal, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("decision", document)
        now = datetime.now(UTC)
        operation_id = UUID(request["operation_id"])
        with self._units() as unit:
            repo = unit.pairing
            ceremony, account = self._context(
                repo, UUID(request["ceremony_id"]), now, terminal=True
            )
            self._identity_request(ceremony, request)
            self._source(repo, ceremony, actor, now)
            previous = repo.command(operation_id)
            if previous is not None:
                if (
                    previous.ceremony_id != ceremony.ceremony_id
                    or previous.user_id != actor.user_id
                    or previous.device_id != actor.device_id
                    or previous.family_id != ceremony.source_family_id
                    or previous.request_hash.hex() != request["request_sha256"]
                ):
                    raise SelfPairingError("operation_conflict")
                return previous.result
            self._live(ceremony, now)
            fresh(request, now)
            if request["expected_revision"] != ceremony.revision:
                raise SelfPairingError("self_pairing_revision_conflict")
            claim_hash = (
                ceremony.claim_document["request_sha256"] if ceremony.claim_document else None
            )
            if (
                request["claim_id"] != (str(ceremony.claim_id) if ceremony.claim_id else None)
                or request["claim_request_sha256"] != claim_hash
                or ceremony.state == "EXCHANGED"
            ):
                raise SelfPairingError("operation_conflict")
            action = request["action"]
            if action == "APPROVE":
                if (
                    ceremony.state != "CLAIMED"
                    or request["comparison_code"] != ceremony.comparison_code()
                ):
                    raise SelfPairingError()
                ceremony.state = "APPROVED"
                ceremony.approval_operation_id = operation_id
            elif action == "REJECT":
                if ceremony.state != "CLAIMED" or request["comparison_code"] is not None:
                    raise SelfPairingError()
                ceremony.state = "REJECTED"
            else:
                if request["comparison_code"] is not None:
                    raise SelfPairingError()
                ceremony.state = "CANCELLED"
            ceremony.revision += 1
            result = self._status(ceremony, account, source=True)
            repo.save(ceremony)
            repo.save_command(
                PairingCommand(
                    operation_id,
                    ceremony.ceremony_id,
                    actor.user_id,
                    actor.device_id,
                    ceremony.source_family_id,
                    bytes.fromhex(request["request_sha256"]),
                    result,
                    now,
                )
            )
            repo.audit(ceremony, "self_pairing." + action.lower(), operation_id, now)
            unit.commit()
            return result

    def exchange(self, secret: str, document: dict[str, Any]) -> dict[str, Any]:
        request = parse_request("exchange", document)
        now = datetime.now(UTC)
        with self._units() as unit:
            repo = unit.pairing
            ceremony, account = self._context(repo, UUID(request["ceremony_id"]), now)
            self._new_phone("exchange", ceremony, secret, request)
            if request["confirmed_account_id"] != str(account.user_id) or request[
                "approval_operation_id"
            ] != str(ceremony.approval_operation_id):
                raise SelfPairingError()
            repo.require_unique("exchange_id", UUID(request["exchange_id"]), ceremony.ceremony_id)
            replayed = ceremony.state == "EXCHANGED"
            if replayed:
                if ceremony.exchange_document != public_document(request):
                    raise SelfPairingError("operation_conflict")
                binding = repo.active_binding(ceremony, now)
            else:
                if ceremony.state != "APPROVED":
                    raise SelfPairingError()
                fresh(request, now)
                repo.require_source(ceremony, now)
                ceremony.exchange_id = UUID(request["exchange_id"])
                ceremony.exchange_document = public_document(request)
                binding = repo.create_binding(ceremony, now)
                ceremony.result_device_id, ceremony.result_session_id = (
                    binding.device_id,
                    binding.session_id,
                )
                ceremony.receipt_expires_at = binding.expires_at + timedelta(minutes=5)
                ceremony.state, ceremony.revision = "EXCHANGED", ceremony.revision + 1
                repo.save(ceremony)
                repo.audit(ceremony, "self_pairing.exchanged", ceremony.exchange_id, now)
            result = self._exchange_response(ceremony, account, binding, now, replayed)
            unit.commit()
            return result

    def cleanup(self, limit: int = 1000) -> int:
        if not 1 <= limit <= 10_000:
            raise ValueError("cleanup bound")
        with self._units() as unit:
            count = unit.pairing.cleanup(datetime.now(UTC), limit)
            unit.commit()
            return count

    @staticmethod
    def _context(
        repo: SelfPairingRepository,
        identifier: UUID,
        now: datetime,
        *,
        terminal: bool = False,
    ) -> tuple[SelfDevicePairing, PairingAccount]:
        discovered = repo.find(identifier)
        if discovered is None:
            raise SelfPairingError()
        identity = repo.lock_identity(discovered.server_instance_id)
        account = repo.lock_account(discovered.user_id)
        ceremony = repo.find(identifier, lock=True)
        if (
            ceremony is None
            or ceremony.authority_generation != account.authority_generation
            or not identity.matches(ceremony.start_document)
        ):
            raise SelfPairingError()
        if not terminal or ceremony.state == "EXCHANGED":
            SelfDevicePairingService._live(ceremony, now)
        elif ceremony.expires_at + timedelta(days=1) <= now:
            raise SelfPairingError()
        return ceremony, account

    @staticmethod
    def _live(ceremony: SelfDevicePairing, now: datetime) -> None:
        deadline = (
            ceremony.receipt_expires_at if ceremony.state == "EXCHANGED" else ceremony.expires_at
        )
        if deadline is None or deadline <= now or ceremony.state in {"CANCELLED", "REJECTED"}:
            raise SelfPairingError()

    @staticmethod
    def _source(
        repo: SelfPairingRepository, ceremony: SelfDevicePairing, actor: Principal, now: datetime
    ) -> None:
        if ceremony.user_id != actor.user_id or ceremony.source_device_id != actor.device_id:
            raise SelfPairingError()
        if repo.actor_family(actor, now) != ceremony.source_family_id:
            raise SelfPairingError()

    @staticmethod
    def _identity_request(ceremony: SelfDevicePairing, request: dict[str, Any]) -> None:
        if any(
            request[key] != value
            for key, value in ceremony.start_document.items()
            if key.startswith("expected_")
        ):
            raise SelfPairingError()

    @staticmethod
    def _possession(secret: str, expected: str) -> None:
        if not hmac.compare_digest(secret_hash(secret).hex(), expected):
            raise SelfPairingError()

    @staticmethod
    def _new_phone(
        kind: str, ceremony: SelfDevicePairing, secret: str, request: dict[str, Any]
    ) -> None:
        SelfDevicePairingService._identity_request(ceremony, request)
        claim = ceremony.claim_document
        if (
            claim is None
            or request["claim_id"] != str(ceremony.claim_id)
            or request["claim_request_sha256"] != claim["request_sha256"]
        ):
            raise SelfPairingError()
        SelfDevicePairingService._possession(secret, claim["poll_secret_sha256"])
        verify_proof(kind, request, claim)

    @staticmethod
    def _status(
        ceremony: SelfDevicePairing,
        account: PairingAccount,
        *,
        source: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        expired = (
            now is not None
            and ceremony.expires_at <= now
            and ceremony.state in {"OPEN", "CLAIMED", "APPROVED"}
        )
        result: dict[str, Any] = {
            "contract_version": "v1",
            "schema_version": 1,
            "ceremony_id": str(ceremony.ceremony_id),
            "state": "EXPIRED" if expired else ceremony.state,
            "revision": ceremony.revision,
            "expires_at": iso8601(ceremony.expires_at),
            "retry_after_seconds": 2,
        }
        if ceremony.claim_document is not None:
            claim = ceremony.claim_document
            result.update(
                {
                    "claim_id": str(ceremony.claim_id),
                    "claim_request_sha256": claim["request_sha256"],
                    "device_name": claim["device_name"],
                    "device_key_thumbprint_sha256": claim["device_key_thumbprint_sha256"],
                    "comparison_code": ceremony.comparison_code(),
                }
            )
        if source or (not expired and ceremony.state in {"APPROVED", "EXCHANGED"}):
            result.update({"account_id": str(account.user_id), "account_label": account.label})
        if ceremony.approval_operation_id is not None:
            result["approval_operation_id"] = str(ceremony.approval_operation_id)
        if ceremony.result_device_id is not None:
            result["device_id"] = str(ceremony.result_device_id)
        return result

    def _exchange_response(
        self,
        ceremony: SelfDevicePairing,
        account: PairingAccount,
        binding: PairingBinding,
        now: datetime,
        replayed: bool,
    ) -> dict[str, Any]:
        if ceremony.exchange_document is None or ceremony.receipt_expires_at is None:
            raise SelfPairingError()
        expiry = min(now + self._ttl, binding.expires_at)
        principal = Principal(account.user_id, binding.device_id, binding.session_id, account.role)
        return {
            "contract_version": "v1",
            "schema_version": 1,
            "exchange_id": str(ceremony.exchange_id),
            "binding_commit_id": ceremony.exchange_document["binding_commit_id"],
            "server_instance_id": str(ceremony.server_instance_id),
            "user_id": str(account.user_id),
            "device_id": str(binding.device_id),
            "session_id": str(binding.session_id),
            "refresh_generation": 0,
            "refresh_absolute_expires_at": iso8601(binding.expires_at),
            "receipt_expires_at": iso8601(ceremony.receipt_expires_at),
            "access_token": self._access.issue(
                principal, token_id=uuid4(), issued_at=now, expires_at=expiry
            ),
            "access_expires_at": iso8601(expiry),
            "replayed": replayed,
        }
