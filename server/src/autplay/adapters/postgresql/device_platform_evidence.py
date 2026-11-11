"""Persisted platform conflicts for legacy device-ceremony issuers."""

from __future__ import annotations

import base64

from sqlalchemy import exists, false, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Exists

from .models.account import DeviceRow
from .models.account_deletion import AccountDeletionProofRow
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


def has_macos_key_evidence(
    session: Session, thumbprint: bytes | None, *, public_key: bytes | None = None
) -> bool:
    """Reject reclassification even after revocation, expiry or another account binding.

    Issuers hold the resource-admission lock before calling this read-only helper.
    No state filter is intentional: revocation does not change the key's platform.
    Exact SPKI also covers historical rows with a missing or conflicting thumbprint.
    This is a v1 exclusion, not a validator for a future versioned device proof.
    """
    if thumbprint is None and public_key is None:
        return False
    with session.no_autoflush:
        return bool(
            session.scalar(
                select(
                    exists().where(
                        or_(
                            DeviceRow.public_key_thumbprint_sha256 == thumbprint
                            if thumbprint is not None
                            else false(),
                            DeviceRow.public_key == public_key
                            if public_key is not None
                            else false(),
                        ),
                        DeviceRow.platform == "MACOS",
                    )
                    | exists().where(
                        or_(
                            DeviceAdmissionRow.device_key_thumbprint_sha256 == thumbprint
                            if thumbprint is not None
                            else false(),
                            DeviceAdmissionRow.device_public_key_spki == public_key
                            if public_key is not None
                            else false(),
                        ),
                        DeviceAdmissionRow.platform == "MACOS",
                    )
                    | exists().where(
                        or_(
                            TrustedDeviceKeyRow.device_key_thumbprint_sha256 == thumbprint
                            if thumbprint is not None
                            else false(),
                            TrustedDeviceKeyRow.device_public_key_spki == public_key
                            if public_key is not None
                            else false(),
                        ),
                        TrustedDeviceKeyRow.platform == "MACOS",
                    )
                    | _stored_key_match(
                        TrustedDeviceReenrollmentChallengeRow, thumbprint, public_key, "MACOS"
                    )
                    | _stored_key_match(
                        DeviceAdmissionExchangeReceiptRow, thumbprint, public_key, "MACOS"
                    )
                    | _stored_key_match(
                        EnrollmentExchangeReceiptRow, thumbprint, public_key, "MACOS"
                    )
                    | _stored_key_match(SessionRotationReceiptRow, thumbprint, public_key, "MACOS")
                    | _portable_key_match(thumbprint, public_key, "MACOS")
                    | _self_key_match(thumbprint, public_key, "MACOS")
                    | _recovery_key_match(thumbprint, public_key, "MACOS")
                    | _deletion_key_match(thumbprint, public_key, "MACOS")
                )
            )
        )


def device_has_macos_evidence(session: Session, device: DeviceRow) -> bool:
    """Keep legacy ANDROID/WEB/OTHER semantics while refusing Mac session issuance."""
    return device.platform == "MACOS" or has_macos_key_evidence(
        session, device.public_key_thumbprint_sha256, public_key=device.public_key
    )


def has_v2_key_evidence_conflict(
    session: Session,
    thumbprint: bytes,
    *,
    public_key: bytes,
    platform: str,
) -> bool:
    """Caller acquires shared admission lock before account/device/session locks.

    Inspect every account and historical state. Matching raw SPKI must still
    expose contradictory platform evidence when the stored thumbprint is NULL.
    Missing evidence permits a new invitation binding, never trusted admission.
    Legacy issuers continue to use their unchanged Mac-only exclusions above.
    """
    if platform not in {"ANDROID", "MACOS"}:
        return True
    with session.no_autoflush:
        return bool(
            session.scalar(
                select(
                    exists().where(
                        or_(
                            DeviceRow.public_key_thumbprint_sha256 == thumbprint,
                            DeviceRow.public_key == public_key,
                        ),
                        or_(
                            DeviceRow.platform != platform,
                            DeviceRow.public_key.is_distinct_from(public_key),
                            DeviceRow.public_key_thumbprint_sha256.is_distinct_from(thumbprint),
                        ),
                    )
                    | exists().where(
                        or_(
                            DeviceAdmissionRow.device_key_thumbprint_sha256 == thumbprint,
                            DeviceAdmissionRow.device_public_key_spki == public_key,
                        ),
                        or_(
                            DeviceAdmissionRow.platform != platform,
                            DeviceAdmissionRow.device_public_key_spki.is_distinct_from(public_key),
                            DeviceAdmissionRow.device_key_thumbprint_sha256.is_distinct_from(
                                thumbprint
                            ),
                        ),
                    )
                    | exists().where(
                        or_(
                            TrustedDeviceKeyRow.device_key_thumbprint_sha256 == thumbprint,
                            TrustedDeviceKeyRow.device_public_key_spki == public_key,
                        ),
                        or_(
                            TrustedDeviceKeyRow.device_public_key_spki.is_distinct_from(public_key),
                            TrustedDeviceKeyRow.device_key_thumbprint_sha256.is_distinct_from(
                                thumbprint
                            ),
                            (TrustedDeviceKeyRow.contract_version == "v2")
                            & TrustedDeviceKeyRow.platform.is_distinct_from(platform),
                        ),
                    )
                    | _stored_key_match(
                        TrustedDeviceReenrollmentChallengeRow,
                        thumbprint,
                        public_key,
                        platform,
                        conflict=True,
                    )
                    | _stored_key_match(
                        DeviceAdmissionExchangeReceiptRow,
                        thumbprint,
                        public_key,
                        platform,
                        conflict=True,
                    )
                    | _stored_key_match(
                        EnrollmentExchangeReceiptRow,
                        thumbprint,
                        public_key,
                        platform,
                        conflict=True,
                    )
                    | _stored_key_match(
                        SessionRotationReceiptRow, thumbprint, public_key, platform, conflict=True
                    )
                    | _portable_key_match(thumbprint, public_key, platform, conflict=True)
                    | _self_key_match(thumbprint, public_key, platform, conflict=True)
                    | _recovery_key_match(thumbprint, public_key, platform, conflict=True)
                    | _deletion_key_match(thumbprint, public_key, platform, conflict=True)
                )
            )
        )


def _stored_key_match(
    row: type[TrustedDeviceReenrollmentChallengeRow]
    | type[DeviceAdmissionExchangeReceiptRow]
    | type[EnrollmentExchangeReceiptRow]
    | type[SessionRotationReceiptRow],
    thumbprint: bytes | None,
    public_key: bytes | None,
    platform: str,
    *,
    conflict: bool = False,
) -> Exists:
    """Historical safe evidence survives a device/admission cleanup; never infer OS."""
    match = exists().where(
        row.contract_version == "v2",
        or_(
            row.device_key_thumbprint_sha256 == thumbprint if thumbprint is not None else false(),
            row.original_request_evidence["device_public_key_spki_hex"].astext == public_key.hex()
            if public_key is not None
            else false(),
        ),
    )
    if conflict:
        if public_key is None:
            raise ValueError("incomplete device evidence")
        return match.where(
            or_(
                row.platform.is_distinct_from(platform),
                row.device_key_thumbprint_sha256.is_distinct_from(thumbprint),
                row.original_request_evidence["device_public_key_spki_hex"].astext.is_distinct_from(
                    public_key.hex()
                ),
            )
        )
    return match.where(row.platform == platform)


def _portable_key_match(
    thumb: bytes | None, key: bytes | None, platform: str, *, conflict: bool = False
) -> Exists:
    # These are the DEVICE fields, never the passport ROOT identity key.
    row = PortableAccountChallengeRow
    evidence = row.original_request_evidence
    match = exists().where(
        row.contract_version == "v2",
        or_(
            evidence["device_thumbprint_sha256"].astext == thumb.hex()
            if thumb is not None
            else false(),
            evidence["device_spki_hex"].astext == key.hex() if key is not None else false(),
        ),
    )
    if conflict:
        if thumb is None or key is None:
            raise ValueError("incomplete device evidence")
        return match.where(
            or_(
                row.platform.is_distinct_from(platform),
                evidence["device_thumbprint_sha256"].astext.is_distinct_from(thumb.hex()),
                evidence["device_spki_hex"].astext.is_distinct_from(key.hex()),
            )
        )
    return match.where(row.platform == platform)


def _self_key_match(
    thumb: bytes | None, key: bytes | None, platform: str, *, conflict: bool = False
) -> ColumnElement[bool]:
    row = SelfDevicePairingRow
    claim = row.claim_document
    encoded = base64.b64encode(key).decode("ascii") if key is not None else None
    source = exists().where(
        row.contract_version == "v2",
        or_(
            row.source_key_thumbprint_sha256 == thumb if thumb is not None else false(),
            row.source_public_key_spki == key if key is not None else false(),
        ),
    )
    recipient = exists().where(
        row.contract_version == "v2",
        or_(
            claim["device_key_thumbprint_sha256"].astext == thumb.hex()
            if thumb is not None
            else false(),
            claim["device_public_key_spki_b64"].astext == encoded if key is not None else false(),
        ),
    )
    if conflict:
        if thumb is None or key is None:
            raise ValueError("incomplete device evidence")
        source = source.where(
            or_(
                row.source_platform.is_distinct_from(platform),
                row.source_key_thumbprint_sha256.is_distinct_from(thumb),
                row.source_public_key_spki.is_distinct_from(key),
            )
        )
        recipient = recipient.where(
            or_(
                row.recipient_platform.is_distinct_from(platform),
                claim["device_key_thumbprint_sha256"].astext.is_distinct_from(thumb.hex()),
                claim["device_public_key_spki_b64"].astext.is_distinct_from(encoded),
            )
        )
    else:
        source, recipient = (
            source.where(row.source_platform == platform),
            recipient.where(row.recipient_platform == platform),
        )
    return source | recipient


def _recovery_key_match(
    thumb: bytes | None,
    key: bytes | None,
    platform: str,
    *,
    conflict: bool = False,
) -> Exists:
    # Retained full originals cover revoked/expired bindings and every owner.
    row = AccountRecoveryOperationRow
    doc = row.original_request_document
    encoded = base64.b64encode(key).decode("ascii") if key is not None else None
    match = exists().where(
        row.contract_version == "v2",
        or_(
            doc["device_key_thumbprint_sha256"].astext == thumb.hex()
            if thumb is not None
            else false(),
            doc["device_public_key_spki_b64"].astext == encoded if key is not None else false(),
        ),
    )
    if conflict:
        if thumb is None or key is None:
            raise ValueError("incomplete device evidence")
        return match.where(
            or_(
                row.platform.is_distinct_from(platform),
                doc["device_key_thumbprint_sha256"].astext.is_distinct_from(thumb.hex()),
                doc["device_public_key_spki_b64"].astext.is_distinct_from(encoded),
            )
        )
    return match.where(row.platform == platform)


def _deletion_key_match(
    thumb: bytes | None,
    key: bytes | None,
    platform: str,
    *,
    conflict: bool = False,
) -> Exists:
    row = AccountDeletionProofRow
    match = exists().where(
        or_(
            row.actor_key_thumbprint_sha256 == thumb if thumb is not None else false(),
            row.actor_public_key_spki == key if key is not None else false(),
        )
    )
    if conflict:
        if thumb is None or key is None:
            raise ValueError("incomplete device evidence")
        return match.where(
            or_(
                row.platform.is_distinct_from(platform),
                row.actor_key_thumbprint_sha256.is_distinct_from(thumb),
                row.actor_public_key_spki.is_distinct_from(key),
            )
        )
    return match.where(row.platform == platform)
