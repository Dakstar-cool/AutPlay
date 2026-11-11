"""B1's complete read-only typed device evidence inventory.

All accounts and terminal states participate. The v1/D1 exclusion helpers retain
their own historical error contracts. D1 deletion REQUEST and DELETE_CANCEL use
their own original parsers and proof domains. Future typed evidence owners MUST
join before B1 qualification. ROOT/passport keys and response aliases never participate.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import rfc8785
from cryptography.exceptions import UnsupportedAlgorithm
from sqlalchemy import Row, Select, Text, cast, select
from sqlalchemy.orm import Session, aliased

from autplay.domain.device_deletion_v2 import (
    DeletionOperation,
    ParsedDeletionRequest,
    VerifiedDeletionProof,
    parse_deletion_request,
    verify_deletion_proof,
)
from autplay.domain.device_profile_v2 import _SPECS, DeviceProfileOperation, _Invalid, _validate
from autplay.domain.device_recovery_v2 import (
    DeviceKeyEvidence,
    DevicePlatform,
    KeyEvidenceSource,
    ParsedRecoveryRequest,
    RecoveryOperation,
    RecoveryV2Error,
    VerifiedRecoveryProof,
    parse_recovery_request,
    verify_recovery_proof,
)
from autplay.domain.device_self_v2 import (
    ParsedSelfRequest,
    SelfOperation,
    SelfV2Error,
    _canonical_spki,
    parse_self_request,
    verify_recipient_proof,
)

from .models.account import DeviceRow, UserSessionRow
from .models.account_deletion import AccountDeletionProofRow, AccountDeletionRequestRow
from .models.account_recovery import AccountRecoveryOperationRow
from .models.portable_account import PortableAccountChallengeRow
from .models.profile_pairing import (
    DeviceAdmissionExchangeReceiptRow,
    DeviceAdmissionRow,
    EnrollmentExchangeReceiptRow,
    SessionRotationReceiptRow,
    TrustedDeviceKeyRow,
    TrustedDeviceReenrollmentChallengeRow,
)
from .models.self_device_pairing import SelfDevicePairingRow

_DIRECT_OWNERS = (DeviceAdmissionRow, TrustedDeviceKeyRow)
_PROFILE_OWNERS: dict[
    type[TrustedDeviceReenrollmentChallengeRow]
    | type[DeviceAdmissionExchangeReceiptRow]
    | type[EnrollmentExchangeReceiptRow]
    | type[SessionRotationReceiptRow],
    tuple[str, ...],
] = {
    TrustedDeviceReenrollmentChallengeRow: ("trusted.challenge",),
    DeviceAdmissionExchangeReceiptRow: ("admission.exchange", "trusted.exchange"),
    EnrollmentExchangeReceiptRow: ("enrollment.exchange",),
    SessionRotationReceiptRow: ("session.rotate",),
}
_PROFILE_IDS = {
    TrustedDeviceReenrollmentChallengeRow: ("user_id", "challenge_id"),
    DeviceAdmissionExchangeReceiptRow: (
        "exchange_id",
        "request_or_challenge_id",
        "binding_commit_id",
    ),
    EnrollmentExchangeReceiptRow: ("exchange_id", "invitation_id", "binding_commit_id"),
    SessionRotationReceiptRow: ("rotation_id", "parent_session_id"),
}
# This is the single strict B1 inventory, including two projections of SELF.
DEVICE_KEY_HISTORY_OWNERS = (
    DeviceRow,
    *_DIRECT_OWNERS,
    *_PROFILE_OWNERS,
    PortableAccountChallengeRow,
    SelfDevicePairingRow,
    AccountRecoveryOperationRow,
    AccountDeletionProofRow,
)


@dataclass(frozen=True, slots=True, repr=False)
class _Evidence:
    key: bytes | None
    thumb: bytes | None
    platform: str | None
    valid: bool = True


def binding_history_conflicts(
    session: Session,
    key: bytes,
    thumb: bytes,
    platform: str,
    *,
    deadline: Callable[[], None],
) -> bool:
    """Bounded streaming reads, never load or rewrite entire secret ORM rows.

    Decode typed device key encodings before comparing raw SPKI. This also sees
    an exact key with null/wrong thumb; an encoded-text equality filter would
    hide some malformed retained evidence. The caller holds resource admission
    through its last authority check. Read failure is raised, never absence.
    """
    with session.no_autoflush:
        for evidence in _evidence(session, deadline):
            deadline()
            if (evidence.key == key or evidence.thumb == thumb) and (
                not evidence.valid
                or evidence.key != key
                or evidence.platform != platform
                or (evidence.thumb is not None and evidence.thumb != thumb)
            ):
                return True
    return False


def _rows(
    session: Session, statement: Select[Any], deadline: Callable[[], None]
) -> Iterator[Row[Any]]:
    deadline()
    result = session.execute(statement.execution_options(yield_per=128))
    try:
        for row in result:
            deadline()
            yield row
    finally:
        result.close()


def _evidence(session: Session, deadline: Callable[[], None]) -> Iterator[_Evidence]:
    yield from (
        _Evidence(key, thumb, platform)
        for key, thumb, platform in _rows(
            session,
            select(
                DeviceRow.public_key, DeviceRow.public_key_thumbprint_sha256, DeviceRow.platform
            ),
            deadline,
        )
    )
    for model in _DIRECT_OWNERS:
        for key, thumb, platform, version in _rows(
            session,
            select(
                model.device_public_key_spki,
                model.device_key_thumbprint_sha256,
                model.platform,
                model.contract_version,
            ),
            deadline,
        ):
            yield _Evidence(key, thumb, platform, version in {"v1", "v2"})
    # Admissions carry an independent typed original as well as their raw key.
    for (
        key,
        thumb,
        platform,
        version,
        doc,
        original,
        request,
        request_id,
        instance,
        epoch,
        identity_thumb,
    ) in _rows(
        session,
        select(
            DeviceAdmissionRow.device_public_key_spki,
            DeviceAdmissionRow.device_key_thumbprint_sha256,
            DeviceAdmissionRow.platform,
            DeviceAdmissionRow.contract_version,
            DeviceAdmissionRow.original_request_evidence,
            DeviceAdmissionRow.original_document_sha256,
            DeviceAdmissionRow.request_sha256,
            DeviceAdmissionRow.request_id,
            DeviceAdmissionRow.server_instance_id,
            DeviceAdmissionRow.identity_epoch,
            DeviceAdmissionRow.identity_thumbprint_sha256,
        ),
        deadline,
    ):
        if version == "v2":
            evidence = _hex_evidence(
                doc, "device_public_key_spki_hex", "device_key_thumbprint_sha256", platform
            )
            valid = (
                _profile_document(doc, platform, ("admission.submit",), original, request)
                and _references(doc["fields"], (("request_id", str(request_id)),))
                and _references(
                    doc,
                    (
                        ("server_instance_id", str(instance)),
                        ("identity_epoch", epoch),
                        ("identity_thumbprint_sha256", identity_thumb.hex()),
                    ),
                )
                and evidence.valid
                and evidence.key == key
            )
            valid = valid and (thumb is None or evidence.thumb == thumb)
            yield _Evidence(key, thumb, platform, valid)
            yield _Evidence(evidence.key, evidence.thumb, platform, valid)
    for profile_model, operations in _PROFILE_OWNERS.items():
        for thumb, platform, version, doc, original, request, *identifiers in _rows(
            session,
            select(
                profile_model.device_key_thumbprint_sha256,
                profile_model.platform,
                profile_model.contract_version,
                profile_model.original_request_evidence,
                profile_model.original_document_sha256,
                profile_model.request_sha256,
                *(getattr(profile_model, name) for name in _PROFILE_IDS[profile_model]),
            ),
            deadline,
        ):
            evidence = _hex_evidence(
                doc, "device_public_key_spki_hex", "device_key_thumbprint_sha256", platform
            )
            valid = (
                version == "v2"
                and _profile_document(doc, platform, operations, original, request)
                and _profile_row_ids(
                    doc, dict(zip(_PROFILE_IDS[profile_model], identifiers, strict=True))
                )
            )
            # The independent row thumb must agree with the typed original too.
            yield _Evidence(
                evidence.key,
                thumb,
                platform,
                valid and evidence.valid and (thumb is None or evidence.thumb == thumb),
            )
            yield _Evidence(
                evidence.key,
                evidence.thumb,
                platform,
                valid and evidence.valid and (thumb is None or evidence.thumb == thumb),
            )
    portable_model = PortableAccountChallengeRow
    for (
        platform,
        version,
        doc,
        purpose,
        original,
        request,
        root_proof,
        account,
        binding,
        challenge_id,
        challenge,
    ) in _rows(
        session,
        select(
            portable_model.platform,
            portable_model.contract_version,
            portable_model.original_request_evidence,
            portable_model.purpose,
            portable_model.original_document_sha256,
            portable_model.request_hash,
            portable_model.root_proof_sha256,
            portable_model.account_id,
            portable_model.binding_commit_id,
            portable_model.challenge_id,
            portable_model.document,
        ),
        deadline,
    ):
        if doc is not None:
            evidence = _hex_evidence(doc, "device_spki_hex", "device_thumbprint_sha256", platform)
            yield _Evidence(
                evidence.key,
                evidence.thumb,
                platform,
                evidence.valid
                and version == "v2"
                and _portable_document(doc, platform)
                and doc.get("purpose") == purpose
                and _references(
                    doc, (("account_id", str(account)), ("binding_commit_id", str(binding)))
                )
                and _references(
                    challenge,
                    (
                        ("challenge_id", str(challenge_id)),
                        ("account_id", str(account)),
                        ("purpose", purpose),
                        ("platform", platform),
                        ("contract_version", "v2"),
                        ("schema_version", 2),
                    ),
                )
                and _stored_digest(doc, "original_document_sha256", original)
                and _stored_digest(doc, "request_sha256", request)
                and _stored_digest(doc, "root_proof_sha256", root_proof),
            )
    self_model = SelfDevicePairingRow
    for (
        key,
        thumb,
        source_platform,
        recipient_platform,
        version,
        start,
        claim,
        start_original,
        start_request,
        claim_original,
        claim_request,
        ceremony,
        start_operation,
        claim_id,
        instance,
        exchange,
        exchange_original,
        exchange_request,
        exchange_id,
        binding,
        account,
        approval,
    ) in _rows(
        session,
        select(
            self_model.source_public_key_spki,
            self_model.source_key_thumbprint_sha256,
            self_model.source_platform,
            self_model.recipient_platform,
            self_model.contract_version,
            self_model.start_document,
            self_model.claim_document,
            self_model.start_original_document_sha256,
            self_model.start_request_sha256,
            self_model.claim_original_document_sha256,
            self_model.claim_request_sha256,
            self_model.ceremony_id,
            self_model.start_operation_id,
            self_model.claim_id,
            self_model.server_instance_id,
            self_model.exchange_document,
            self_model.exchange_original_document_sha256,
            self_model.exchange_request_sha256,
            self_model.exchange_id,
            self_model.binding_commit_id,
            self_model.user_id,
            self_model.approval_operation_id,
        ),
        deadline,
    ):
        start_valid = (
            version == "v2"
            and _self_source_key_valid(key, thumb)
            and _full_document(
                start,
                source_platform,
                "SELF_START",
                start_original,
                start_request,
                references=(
                    ("ceremony_id", str(ceremony)),
                    ("operation_id", str(start_operation)),
                    ("expected_server_instance_id", str(instance)),
                ),
            )
        )
        # START has no source key/session/family members. Its retained raw key
        # is checked separately; only the actual shared trust members link the
        # originals. Source and recipient platforms may intentionally differ.
        trust = (
            tuple(
                (name, start[name])
                for name in (
                    "expected_server_instance_id",
                    "expected_identity_epoch",
                    "expected_identity_thumbprint_sha256",
                    "expected_api_origin",
                    "expected_stream_origin",
                )
            )
            if start_valid
            else ()
        )
        evidence = _base64_evidence(claim, recipient_platform)
        claim_valid = (
            all(
                value is None
                for value in (claim_id, recipient_platform, claim_original, claim_request)
            )
            if claim is None
            else evidence.valid
            and _full_document(
                claim,
                recipient_platform,
                "SELF_CLAIM",
                claim_original,
                claim_request,
                references=(*trust, ("ceremony_id", str(ceremony)), ("claim_id", str(claim_id))),
                recipient_key=evidence.key,
            )
        )
        exchange_valid = (
            all(
                value is None
                for value in (exchange_id, binding, exchange_original, exchange_request)
            )
            if exchange is None
            else claim is not None
            and evidence.key is not None
            and _full_document(
                exchange,
                recipient_platform,
                "SELF_EXCHANGE",
                exchange_original,
                exchange_request,
                references=(
                    *trust,
                    ("ceremony_id", str(ceremony)),
                    ("claim_id", str(claim_id)),
                    (
                        "claim_request_sha256",
                        claim_request.hex() if claim_request is not None else None,
                    ),
                    ("exchange_id", str(exchange_id)),
                    ("binding_commit_id", str(binding)),
                    ("confirmed_account_id", str(account)),
                    ("approval_operation_id", str(approval)),
                ),
                recipient_key=evidence.key,
            )
        )
        # One row consistency result MUST reach both participant projections.
        # Keep their raw keys/thumbs even on corruption so matching evidence
        # cannot disappear, while unrelated malformed rows remain unrelated.
        valid = start_valid and claim_valid and exchange_valid
        yield _Evidence(key, thumb, source_platform, valid)
        if claim is not None:
            yield _Evidence(evidence.key, evidence.thumb, recipient_platform, valid)
    yield from _deletion_history(session, deadline)
    yield from _recovery_history(session, deadline)


# Explicit read projections, never whole ORM rows or current code credentials.
_PROOF_FIELDS = (
    "operation_id",
    "user_id",
    "server_instance_id",
    "identity_epoch",
    "actor_device_id",
    "actor_public_key_spki",
    "actor_key_thumbprint_sha256",
    "code_verifier_sha256",
    "contract_version",
    "platform",
    "request_sha256",
    "original_document_sha256",
    "original_request_document",
    "signature_algorithm",
    "proof_domain",
    "decision",
    "decided_at",
)
_PARENT_FIELDS = (
    "deletion_request_id",
    "user_id",
    "server_instance_id",
    "identity_epoch",
    "identity_thumbprint_sha256",
    "actor_device_id",
    "actor_public_key_spki",
    "request_sha256",
    "code_generation",
    "code_verifier_sha256",
    "authority_generation",
    "requested_at",
    "cancel_before",
    "contract_version",
    "platform",
    "state",
    "revision",
    "cancel_operation_id",
)
_RESULT_FIELDS = ("device_id", "user_id", "public_key", "public_key_thumbprint_sha256", "platform")
_BINDING_FIELDS = (
    "session_id",
    "user_id",
    "device_id",
    "family_id",
    "generation",
    "session_mode",
    "refresh_token_hash",
    "issued_at",
    "expires_at",
)
_RECOVERY_FIELDS = (
    "operation_id",
    "kind",
    "user_id",
    "server_instance_id",
    "identity_epoch",
    "request_sha256",
    "actor_device_id",
    "actor_family_id",
    "previous_generation",
    "result_generation",
    "authority_generation",
    "spent_verifier_sha256",
    "next_verifier_sha256",
    "result_device_id",
    "result_session_id",
    "binding_commit_id",
    "created_at",
    "contract_version",
    "platform",
    "original_document_sha256",
    "original_request_document",
    "signature_algorithm",
    "proof_domain",
)


def _columns(model: Any, names: tuple[str, ...], prefix: str) -> tuple[Any, ...]:
    return tuple(getattr(model, name).label(prefix + name) for name in names)


def _projection(row: Row[Any], names: tuple[str, ...], prefix: str) -> dict[str, Any]:
    return {name: row._mapping[prefix + name] for name in names}


def _deletion_history(session: Session, deadline: Callable[[], None]) -> Iterator[_Evidence]:
    model, parent = AccountDeletionProofRow, AccountDeletionRequestRow
    statement = (
        select(
            *_columns(model, _PROOF_FIELDS, "proof_"),
            *_columns(parent, _PARENT_FIELDS, "parent_"),
            *_columns(DeviceRow, _RESULT_FIELDS, "device_"),
        )
        .select_from(model)
        .outerjoin(parent, parent.deletion_request_id == model.operation_id)
        .outerjoin(DeviceRow, DeviceRow.device_id == model.actor_device_id)
    )
    for row in _rows(session, statement, deadline):
        proof = _projection(row, _PROOF_FIELDS, "proof_")
        device = _projection(row, _RESULT_FIELDS, "device_")
        retained = _projection(row, _PARENT_FIELDS, "parent_")
        evidence = _history_key_evidence(proof["original_request_document"], proof["platform"])
        valid = evidence.valid and _request_history_valid(proof, retained, device)
        # Independent raw/original/actor key projections survive partial rows.
        yield _Evidence(
            proof["actor_public_key_spki"],
            proof["actor_key_thumbprint_sha256"],
            proof["platform"],
            valid,
        )
        yield _Evidence(evidence.key, evidence.thumb, proof["platform"], valid)
        yield _Evidence(
            device["public_key"], device["public_key_thumbprint_sha256"], device["platform"], valid
        )


def _history_key_evidence(doc: object, platform: str | None) -> _Evidence:
    if type(doc) is not dict:
        return _Evidence(None, None, platform, False)
    key, thumb = doc.get("device_public_key_spki_b64"), doc.get("device_key_thumbprint_sha256")
    # Corrupt storage must not cause an unbounded base64/hex decode. Preserve
    # either independently usable identifier for matching even if the other fails.
    return _base64_evidence(
        {
            "device_public_key_spki_b64": key if type(key) is str and len(key) <= 256 else None,
            "device_key_thumbprint_sha256": thumb
            if type(thumb) is str and len(thumb) == 64
            else None,
            "platform": doc.get("platform"),
        },
        platform,
    )


def _history_original(
    row: Mapping[str, Any], device: Mapping[str, Any], kind: str
) -> ParsedDeletionRequest | ParsedRecoveryRequest | None:
    """Reverify historical proof possession, never assert fresh service authority."""
    if row["contract_version"] != "v2" or row["platform"] not in {"ANDROID", "MACOS"}:
        return None
    document = row["original_request_document"]
    # All three original models are flat scalar objects with fewer than 32
    # members. Bound JCS work before the owner's stricter 8192-byte raw parser;
    # its exact allowlist, integer semantics and proof remain authoritative.
    if type(document) is not dict or len(document) > 32:
        return None
    for name, value in document.items():
        if (
            type(name) is not str
            or len(name) > 64
            or not (
                (type(value) is str and len(value) <= 2048)
                or (type(value) is int and 0 <= value <= 9007199254740991)
            )
        ):
            return None
    try:
        raw = rfc8785.dumps(document)
        key = DeviceKeyEvidence(
            device["public_key"],
            DevicePlatform(device["platform"]),
            KeyEvidenceSource.HISTORICAL_APPROVED,
        )
        parsed: ParsedRecoveryRequest | ParsedDeletionRequest
        verified: VerifiedRecoveryProof | VerifiedDeletionProof
        if kind == "RECOVER":
            parsed = parse_recovery_request(RecoveryOperation.RECOVER, raw)
            verified = verify_recovery_proof(
                parse_recovery_request(RecoveryOperation.OUTCOME, raw),
                key_evidence=key,
                expected_trust=parsed.trust,
                retained_original=parsed,
            )
        elif kind in {"REQUEST", "DELETE_CANCEL"}:
            parsed = parse_deletion_request(
                DeletionOperation.REQUEST if kind == "REQUEST" else DeletionOperation.CANCEL, raw
            )
            verified = verify_deletion_proof(
                parse_deletion_request(
                    DeletionOperation.REQUEST_RECEIPT
                    if kind == "REQUEST"
                    else DeletionOperation.CANCEL_OUTCOME,
                    raw,
                ),
                key_evidence=key,
                expected_trust=parsed.trust,
                retained_original=parsed,
            )
        else:
            return None
        doc = parsed.document
        if (
            row["original_document_sha256"] != parsed.original_document_sha256
            or row["request_sha256"] != parsed.request_sha256
            or row["signature_algorithm"] != verified.signature_algorithm
            or row["proof_domain"] != verified.proof_domain
            or device["user_id"] != row["user_id"]
            or row["platform"] != parsed.platform.value
            or device["public_key_thumbprint_sha256"]
            != bytes.fromhex(str(doc["device_key_thumbprint_sha256"]))
            or not _references(
                dict(doc),
                (
                    ("account_id", str(row["user_id"])),
                    ("confirmed_account_id", str(row["user_id"])),
                    ("operation_id", str(row["operation_id"])),
                    ("expected_server_instance_id", str(row["server_instance_id"])),
                    ("expected_identity_epoch", row["identity_epoch"]),
                ),
            )
        ):
            return None
        return parsed
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
        UnsupportedAlgorithm,
        RecoveryV2Error,
    ):
        return None


def _hash(value: object) -> bool:
    return type(value) is bytes and len(value) == 32


def _request_history_valid(
    proof: Mapping[str, Any], parent: Mapping[str, Any], device: Mapping[str, Any]
) -> bool:
    parsed = _history_original(proof, device, "REQUEST")
    if (
        parsed is None
        or proof["decision"] not in {"ATTEMPTED", "SEALED"}
        or not isinstance(proof["decided_at"], datetime)
    ):
        return False
    if (
        device["device_id"] != proof["actor_device_id"]
        or proof["actor_public_key_spki"] != parsed.public_key_spki
        or proof["actor_key_thumbprint_sha256"] != device["public_key_thumbprint_sha256"]
        or not _hash(proof["code_verifier_sha256"])
    ):
        return False
    if proof["decision"] == "SEALED":
        return parent["deletion_request_id"] is None
    doc = parsed.document
    return (
        parent["deletion_request_id"] == proof["operation_id"]
        and parent["contract_version"] == "v2"
        and all(
            parent[name] == proof[name]
            for name in (
                "user_id",
                "server_instance_id",
                "identity_epoch",
                "platform",
                "actor_device_id",
                "actor_public_key_spki",
                "request_sha256",
                "code_verifier_sha256",
            )
        )
        and parent["identity_thumbprint_sha256"]
        == bytes.fromhex(str(doc["expected_identity_thumbprint_sha256"]))
        and parent["code_generation"] == doc["expected_code_generation"]
        and parent["authority_generation"] == doc["expected_authority_generation"] + 1
        and parent["requested_at"] == proof["decided_at"]
    )


def _recovery_history(session: Session, deadline: Callable[[], None]) -> Iterator[_Evidence]:
    model, parent = AccountRecoveryOperationRow, AccountDeletionRequestRow
    proof, actor = AccountDeletionProofRow, aliased(DeviceRow)
    statement = (
        select(
            *_columns(model, _RECOVERY_FIELDS, "receipt_"),
            *_columns(DeviceRow, _RESULT_FIELDS, "device_"),
            *_columns(UserSessionRow, _BINDING_FIELDS, "binding_"),
            *_columns(parent, _PARENT_FIELDS, "parent_"),
            *_columns(proof, _PROOF_FIELDS, "parent_proof_"),
            *_columns(actor, _RESULT_FIELDS, "actor_"),
        )
        .select_from(model)
        .outerjoin(UserSessionRow, UserSessionRow.session_id == model.result_session_id)
        .outerjoin(
            DeviceRow,
            (DeviceRow.device_id == model.result_device_id)
            | (DeviceRow.device_id == UserSessionRow.device_id),
        )
        .outerjoin(
            parent,
            (model.kind == "DELETE_CANCEL")
            & (
                cast(parent.deletion_request_id, Text)
                == model.original_request_document["deletion_request_id"].astext
            ),
        )
        .outerjoin(proof, proof.operation_id == parent.deletion_request_id)
        .outerjoin(actor, actor.device_id == proof.actor_device_id)
    )
    for row in _rows(session, statement, deadline):
        receipt = _projection(row, _RECOVERY_FIELDS, "receipt_")
        # v1 has no typed original/platform. Do not reinterpret its old proof.
        if receipt["contract_version"] == "v1" and all(
            receipt[name] is None
            for name in (
                "platform",
                "original_document_sha256",
                "original_request_document",
                "signature_algorithm",
                "proof_domain",
            )
        ):
            continue
        device = _projection(row, _RESULT_FIELDS, "device_")
        binding = _projection(row, _BINDING_FIELDS, "binding_")
        retained = _projection(row, _PARENT_FIELDS, "parent_")
        evidence = _history_key_evidence(receipt["original_request_document"], receipt["platform"])
        valid = evidence.valid and _recovery_history_valid(
            receipt,
            device,
            binding,
            retained,
            _projection(row, _PROOF_FIELDS, "parent_proof_"),
            _projection(row, _RESULT_FIELDS, "actor_"),
        )
        # A missing/malformed original must not hide its retained result key.
        yield _Evidence(
            device["public_key"], device["public_key_thumbprint_sha256"], device["platform"], valid
        )
        yield _Evidence(evidence.key, evidence.thumb, receipt["platform"], valid)


def _recovery_history_valid(
    receipt: Mapping[str, Any],
    device: Mapping[str, Any],
    binding: Mapping[str, Any],
    parent: Mapping[str, Any],
    proof: Mapping[str, Any],
    actor: Mapping[str, Any],
) -> bool:
    if receipt["kind"] not in {"RECOVER", "DELETE_CANCEL"}:
        return False
    parsed = _history_original(receipt, device, receipt["kind"])
    if parsed is None:
        return False
    doc = parsed.document
    previous, result = receipt["previous_generation"], receipt["result_generation"]
    authority = receipt["authority_generation"]
    if (
        type(previous) is not int
        or previous < 1
        or type(result) is not int
        or result != previous + 1
        or type(authority) is not int
        or authority < 1
        or not _hash(receipt["spent_verifier_sha256"])
        or not _hash(receipt["next_verifier_sha256"])
        or receipt["spent_verifier_sha256"] == receipt["next_verifier_sha256"]
        or receipt["actor_device_id"] is not None
        or receipt["actor_family_id"] is not None
        or receipt["result_device_id"] is None
        or device["device_id"] != receipt["result_device_id"]
        or receipt["result_session_id"] is None
        or binding["session_id"] != receipt["result_session_id"]
        or binding["device_id"] != device["device_id"]
        or binding["user_id"] != receipt["user_id"]
        or binding["session_mode"] != "V2"
        or binding["family_id"] != binding["session_id"]
        or type(binding["generation"]) is not int
        or binding["generation"] != 0
        or binding["issued_at"] != receipt["created_at"]
        or binding["expires_at"] is None
        or binding["issued_at"] is None
        or binding["expires_at"] <= binding["issued_at"]
        or binding["refresh_token_hash"] != bytes.fromhex(str(doc["next_refresh_token_sha256"]))
        or receipt["next_verifier_sha256"] != bytes.fromhex(str(doc["next_code_verifier_sha256"]))
        or not _references(
            dict(doc),
            (
                ("expected_code_generation", previous),
                ("binding_commit_id", str(receipt["binding_commit_id"])),
            ),
        )
        or receipt["operation_id"] == receipt["binding_commit_id"]
    ):
        return False
    if receipt["kind"] == "RECOVER":
        return True
    # Compare immutable request identity/code facts, not current account/code
    # authority. The parent is CANCELLED after commit; PENDING was insertion-only.
    parent_valid = (
        _request_history_valid(proof, parent, actor)
        if parent["contract_version"] == "v2"
        else parent["contract_version"] == "v1"
        and parent["platform"] is None
        and proof["operation_id"] is None
    )
    return (
        parent_valid
        and parent["deletion_request_id"] is not None
        and str(parent["deletion_request_id"]) == doc["deletion_request_id"]
        and all(
            parent[name] == receipt[name]
            for name in ("user_id", "server_instance_id", "identity_epoch")
        )
        and parent["identity_thumbprint_sha256"]
        == bytes.fromhex(str(doc["expected_identity_thumbprint_sha256"]))
        and parent["code_generation"] == previous
        and parent["code_verifier_sha256"] == receipt["spent_verifier_sha256"]
        and type(parent["authority_generation"]) is int
        and parent["authority_generation"] + 1 == authority
        and parent["state"] == "CANCELLED"
        and type(parent["revision"]) is int
        and parent["revision"] == doc["expected_revision"] + 1
        and parent["cancel_operation_id"] == receipt["operation_id"]
        and parent["deletion_request_id"]
        not in {receipt["operation_id"], receipt["binding_commit_id"]}
        and isinstance(parent["requested_at"], datetime)
        and isinstance(parent["cancel_before"], datetime)
        and parent["requested_at"] <= receipt["created_at"] < parent["cancel_before"]
    )


def _self_source_key_valid(key: bytes | None, thumb: bytes | None) -> bool:
    if type(key) is not bytes:
        return False
    try:
        _canonical_spki(key)
        return thumb is None or (type(thumb) is bytes and hashlib.sha256(key).digest() == thumb)
    except ValueError, TypeError, UnsupportedAlgorithm:
        return False


def _hex(value: object) -> bytes | None:
    if type(value) is not str:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def _hex_evidence(doc: object, key_name: str, thumb_name: str, platform: str | None) -> _Evidence:
    if type(doc) is not dict:
        return _Evidence(None, None, platform, False)
    key, thumb = _hex(doc.get(key_name)), _hex(doc.get(thumb_name))
    return _Evidence(
        key,
        thumb,
        platform,
        key is not None
        and doc.get(key_name) == key.hex()
        and thumb is not None
        and len(thumb) == 32
        and doc.get(thumb_name) == thumb.hex()
        and doc.get("platform") == platform
        and platform in {"ANDROID", "MACOS"},
    )


def _base64_evidence(doc: object, platform: str | None) -> _Evidence:
    if type(doc) is not dict:
        return _Evidence(None, None, platform, False)
    encoded, thumb = (
        doc.get("device_public_key_spki_b64"),
        _hex(doc.get("device_key_thumbprint_sha256")),
    )
    try:
        key = base64.b64decode(encoded, validate=True) if type(encoded) is str else None
    except ValueError, binascii.Error:
        key = None
    return _Evidence(
        key,
        thumb,
        platform,
        key is not None
        and base64.b64encode(key).decode("ascii") == encoded
        and thumb is not None
        and len(thumb) == 32
        and doc.get("device_key_thumbprint_sha256") == thumb.hex()
        and doc.get("platform") == platform
        and platform in {"ANDROID", "MACOS"},
    )


def _stored_digest(doc: object, member: str, digest: bytes | None) -> bool:
    return (
        type(doc) is dict
        and type(digest) is bytes
        and len(digest) == 32
        and doc.get(member) == digest.hex()
    )


def _references(doc: object, references: tuple[tuple[str, object], ...]) -> bool:
    return type(doc) is dict and all(
        doc.get(name) == value and type(doc.get(name)) is type(value) for name, value in references
    )


def _profile_row_ids(doc: dict[str, object], identifiers: dict[str, object]) -> bool:
    # Only independently retained IDs with corresponding original allowlisted
    # members participate. Generated result device/session IDs are not
    # input IDs; secrets redacted by the owner are never reconstructed here.
    operation = doc["operation"]
    fields = doc["fields"]
    references = []
    for column, value in identifiers.items():
        member = (
            "account_id"
            if column == "user_id"
            else "challenge_request_id"
            if column == "challenge_id" and operation == "trusted.challenge"
            else ("request_id" if operation == "admission.exchange" else "challenge_id")
            if column == "request_or_challenge_id"
            else column
        )
        references.append((member, str(value)))
    return _references(fields, tuple(references))


def _profile_document(
    doc: object,
    platform: str | None,
    operations: tuple[str, ...],
    original_digest: bytes | None,
    request_digest: bytes | None,
) -> bool:
    if type(doc) is not dict:
        return False
    required = {
        "operation",
        "contract_version",
        "schema_version",
        "platform",
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
        "device_public_key_spki_hex",
        "device_key_thumbprint_sha256",
        "matched_body_key",
        "domain",
        "signature_algorithm",
        "proof_digest",
        "original_document_sha256",
        "signature_hex",
        "fields",
        "credential_hashes",
    }
    operation = doc.get("operation")
    return (
        set(doc) == required
        and type(operation) is str
        and operation in operations
        and doc.get("domain")
        == "autplay:device-compatibility:" + operation.replace(".", ":") + ":v2\n"
        and doc.get("contract_version") == "v2"
        and type(doc.get("schema_version")) is int
        and doc["schema_version"] == 2
        and doc.get("platform") == platform
        and type(doc.get("matched_body_key")) is bool
        and doc.get("signature_algorithm") == "ES256-P1363"
        and all(
            type(doc.get(name)) is str and re.fullmatch(r"[0-9a-f]{64}", doc[name])
            for name in ("proof_digest", "original_document_sha256", "identity_thumbprint_sha256")
        )
        and type(doc.get("signature_hex")) is str
        and re.fullmatch(r"[0-9a-f]{128}", doc["signature_hex"]) is not None
        and type(doc.get("fields")) is dict
        and type(doc.get("credential_hashes")) is dict
        and _profile_fields(doc)
        and _stored_digest(doc, "original_document_sha256", original_digest)
        and _stored_digest(doc, "proof_digest", request_digest)
    )


def _profile_fields(doc: dict[str, object]) -> bool:
    # Reuse the owner's exact typed replay allowlist and scalar validators;
    # do not grow another independent list of the six retained operation shapes.
    try:
        spec = _SPECS[DeviceProfileOperation(doc["operation"])]
        kinds = dict(spec.required + spec.optional)
        optional = {name for name, kind in spec.optional}
        fields = doc["fields"]
        if type(fields) is not dict:
            return False
        required = set(spec.replay_fields) - optional
        if not required.issubset(fields) or set(fields) - set(spec.replay_fields):
            return False
        for name, value in fields.items():
            _validate(value, kinds[name])
        for name, kind in (
            ("server_instance_id", "uuid"),
            ("identity_epoch", "positive"),
            ("api_origin", "origin"),
            ("stream_origin", "origin"),
        ):
            _validate(doc[name], kind)
        credentials = {} if spec.credential is None else {spec.credential + "_sha256": "hash"}
        credential_hashes = doc["credential_hashes"]
        if type(credential_hashes) is not dict:
            return False
        if set(credential_hashes) != set(credentials):
            return False
        for value in credential_hashes.values():
            _validate(value, "hash")
        if spec.own_hash and fields.get("request_sha256") != doc["proof_digest"]:
            return False
        return doc["matched_body_key"] is (doc["operation"] != "session.rotate")
    except ValueError, TypeError, KeyError, _Invalid:
        return False


def _portable_document(doc: object, platform: str | None) -> bool:
    required = {
        "purpose",
        "contract_version",
        "schema_version",
        "platform",
        "domain",
        "signature_algorithm",
        "signature_encoding",
        "request_sha256",
        "original_document_sha256",
        "root_proof_sha256",
        "account_id",
        "public_id",
        "root_spki_hex",
        "root_thumbprint_sha256",
        "device_spki_hex",
        "device_thumbprint_sha256",
        "binding_commit_id",
        "next_refresh_token_sha256",
        "device_name",
        "app_version",
        "account_role",
    }
    return (
        type(doc) is dict
        and set(doc) == required
        and doc.get("contract_version") == "v2"
        and type(doc.get("schema_version")) is int
        and doc["schema_version"] == 2
        and doc.get("platform") == platform
        and type(doc.get("purpose")) is str
        and doc.get("purpose") in {"REGISTER", "LOGIN"}
        and doc.get("signature_algorithm") == "ECDSA-P256-SHA256"
        and doc.get("signature_encoding") == "DER/standard-padded-base64"
        and doc.get("domain") == "autplay:portable-account:" + doc["purpose"].lower() + ":v2\n"
        and type(doc.get("account_role")) is str
        and doc.get("account_role") in {"USER", "ADMIN", "OWNER"}
        and _portable_fields(doc)
        and all(
            type(doc.get(name)) is str and re.fullmatch(r"[0-9a-f]{64}", doc[name])
            for name in (
                "request_sha256",
                "original_document_sha256",
                "root_proof_sha256",
                "root_thumbprint_sha256",
                "next_refresh_token_sha256",
            )
        )
    )


def _portable_fields(doc: dict[str, object]) -> bool:
    try:
        for name, kind in (
            ("account_id", "uuid"),
            ("binding_commit_id", "uuid"),
            ("device_name", "name"),
            ("app_version", "app"),
        ):
            _validate(doc[name], kind)
        return (
            type(doc["public_id"]) is str
            and re.fullmatch(r"[a-z0-9_]{3,24}", doc["public_id"]) is not None
            and type(doc["root_spki_hex"]) is str
            and re.fullmatch(r"[0-9a-f]{182}", doc["root_spki_hex"]) is not None
        )
    except ValueError, TypeError, KeyError, _Invalid:
        return False


def _full_document(
    doc: object,
    platform: str | None,
    kind: str,
    original_digest: bytes | None,
    request_digest: bytes | None,
    *,
    references: tuple[tuple[str, object], ...] = (),
    recipient_key: bytes | None = None,
) -> bool:
    if type(doc) is not dict or doc.get("platform") != platform:
        return False
    try:
        raw = rfc8785.dumps(doc)
        parsed: ParsedSelfRequest | ParsedRecoveryRequest
        if kind.startswith("SELF"):
            operation = {
                "SELF_START": SelfOperation.START,
                "SELF_CLAIM": SelfOperation.CLAIM,
                "SELF_EXCHANGE": SelfOperation.EXCHANGE,
            }[kind]
            parsed = parse_self_request(operation, raw)
            if recipient_key is not None:
                verify_recipient_proof(
                    parsed,
                    approved_public_key_spki=recipient_key,
                    approved_platform=parsed.platform,
                )
        else:
            parsed = parse_recovery_request(RecoveryOperation.RECOVER, raw)
        return (
            parsed.original_document_sha256 == original_digest
            and parsed.request_sha256 == request_digest
            and all(
                doc.get(name) == value and type(doc.get(name)) is type(value)
                for name, value in references
            )
        )
    except ValueError, TypeError, SelfV2Error, RecoveryV2Error:
        return False
