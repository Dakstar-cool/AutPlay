"""Read-only identifier reservations over ceremony rows and retained deletion evidence.

Callers hold lock_resource_admission before consulting these predicates and
through issuance/commit. A reservation is never proof or current authority;
each issuer must still verify its original request and exact replay itself.
Both predicates require the explicitly injected retained-ledger guard, even when
no SQL row survives owner purge. Missing/unready/untrusted evidence fails closed.
No retention rules or persistence owners are added here. SQL own-row exclusions
never exclude SEALED independent evidence.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from .models.account_deletion import AccountDeletionProofRow, AccountDeletionRequestRow
from .models.account_recovery import AccountRecoveryOperationRow
from .models.portable_account import PortableAccountChallengeRow
from .models.profile_pairing import (
    DeviceAdmissionExchangeReceiptRow,
    DeviceAdmissionRow,
    DeviceAdmissionWebOperationReceiptRow,
    EnrollmentExchangeReceiptRow,
    EnrollmentInvitationRow,
    ProfileLifecycleCommandRow,
    SessionRotationReceiptRow,
    TrustedDeviceKeyRow,
    TrustedDeviceReenrollmentChallengeRow,
)
from .models.public_access import (
    AccountInvitationRow,
    AccountProvisioningOperationReceiptRow,
    AccountRegistrationReceiptRow,
)
from .models.self_device_pairing import SelfDevicePairingRow, SelfPairingCommandRow
from .retained_deletion_reservations import (
    DeletionReservationReplay,
    retained_deletion_identifier_reserved,
)

ProfileReceiptType = (
    type[EnrollmentExchangeReceiptRow]
    | type[SessionRotationReceiptRow]
    | type[DeviceAdmissionExchangeReceiptRow]
)


def public_access_identifier_reserved(session: Session, identifier: UUID) -> bool:
    """Read PA2-owned IDs, never parent invitation/target references on receipts.

    PA2 callers use the v2-only predicate below, so their own exact v1 receipts
    need no exclusion here. A v2 receipt exclusion must never hide a PA2 row.
    Retained terminal/expired rows reserve IDs until their existing cleanup.
    """
    for primary_key, identifiers in (
        (
            AccountRegistrationReceiptRow.registration_id,
            (
                AccountRegistrationReceiptRow.registration_id,
                AccountRegistrationReceiptRow.binding_commit_id,
            ),
        ),
        (AccountInvitationRow.invitation_id, (AccountInvitationRow.invitation_id,)),
        (
            AccountProvisioningOperationReceiptRow.operation_id,
            (AccountProvisioningOperationReceiptRow.operation_id,),
        ),
    ):
        if (
            session.scalar(
                select(primary_key)
                .where(
                    or_(*(column == identifier for column in identifiers)),
                )
                .limit(1)
            )
            is not None
        ):
            return True
    return False


def device_v2_identifier_reserved(
    session: Session,
    identifier: UUID,
    *,
    deletion_replay: DeletionReservationReplay | None = None,
) -> bool:
    """Keep v1 ID writers and grants out of existing profile/portable v2 IDs.

    Check new ceremony/operation IDs before mutation, including non-grant commands.
    Check both incoming operation and binding IDs before granting a replay too.
    Preserve the caller's frozen v1 error/domain and positive request bytes.
    """
    if retained_deletion_identifier_reserved(session, identifier, replay=deletion_replay):
        return True
    for row_type, primary_key, identifiers in (
        (
            AccountDeletionProofRow,
            AccountDeletionProofRow.operation_id,
            (AccountDeletionProofRow.operation_id,),
        ),
        (
            AccountDeletionRequestRow,
            AccountDeletionRequestRow.deletion_request_id,
            (
                AccountDeletionRequestRow.deletion_request_id,
                AccountDeletionRequestRow.cancel_operation_id,
            ),
        ),
        (
            EnrollmentExchangeReceiptRow,
            EnrollmentExchangeReceiptRow.exchange_id,
            (
                EnrollmentExchangeReceiptRow.exchange_id,
                EnrollmentExchangeReceiptRow.binding_commit_id,
            ),
        ),
        (
            SessionRotationReceiptRow,
            SessionRotationReceiptRow.rotation_id,
            (SessionRotationReceiptRow.rotation_id,),
        ),
        (
            PortableAccountChallengeRow,
            PortableAccountChallengeRow.challenge_id,
            (
                PortableAccountChallengeRow.challenge_id,
                PortableAccountChallengeRow.binding_commit_id,
            ),
        ),
        (DeviceAdmissionRow, DeviceAdmissionRow.request_id, (DeviceAdmissionRow.request_id,)),
        (
            DeviceAdmissionExchangeReceiptRow,
            DeviceAdmissionExchangeReceiptRow.exchange_id,
            (
                DeviceAdmissionExchangeReceiptRow.exchange_id,
                DeviceAdmissionExchangeReceiptRow.binding_commit_id,
            ),
        ),
        (
            TrustedDeviceReenrollmentChallengeRow,
            TrustedDeviceReenrollmentChallengeRow.challenge_id,
            (TrustedDeviceReenrollmentChallengeRow.challenge_id,),
        ),
        (
            TrustedDeviceKeyRow,
            TrustedDeviceKeyRow.key_reference,
            (TrustedDeviceKeyRow.key_reference,),
        ),
        (
            DeviceAdmissionWebOperationReceiptRow,
            DeviceAdmissionWebOperationReceiptRow.operation_id,
            (DeviceAdmissionWebOperationReceiptRow.operation_id,),
        ),
        (
            SelfDevicePairingRow,
            SelfDevicePairingRow.ceremony_id,
            (
                SelfDevicePairingRow.ceremony_id,
                SelfDevicePairingRow.start_operation_id,
                SelfDevicePairingRow.claim_id,
                SelfDevicePairingRow.approval_operation_id,
                SelfDevicePairingRow.exchange_id,
                SelfDevicePairingRow.binding_commit_id,
            ),
        ),
        (
            SelfPairingCommandRow,
            SelfPairingCommandRow.operation_id,
            (SelfPairingCommandRow.operation_id,),
        ),
        (
            AccountRecoveryOperationRow,
            AccountRecoveryOperationRow.operation_id,
            (
                AccountRecoveryOperationRow.operation_id,
                AccountRecoveryOperationRow.binding_commit_id,
            ),
        ),
    ):
        if (
            session.scalar(
                select(primary_key)
                .where(
                    row_type.contract_version == "v2",
                    or_(*(column == identifier for column in identifiers)),
                )
                .limit(1)
            )
            is not None
        ):
            return True
    return False


def device_identifier_reserved(
    session: Session,
    identifier: UUID,
    *,
    own_profile_receipt: ProfileReceiptType | None = None,
    own_profile_operation_id: UUID | None = None,
    own_admission_request_id: UUID | None = None,
    own_trusted_challenge_id: UUID | None = None,
    own_self_ceremony_id: UUID | None = None,
    own_self_command_id: UUID | None = None,
    own_recovery_operation_id: UUID | None = None,
    own_deletion_request_id: UUID | None = None,
    own_portable_challenge_id: UUID | None = None,
    deletion_replay: DeletionReservationReplay | None = None,
) -> bool:
    """Reject new v2 ID use against every existing operation/binding family.

    Only the one expected profile receipt may be excluded for exact replay;
    the application separately verifies its version, proof, binding and current
    authority. Other rows (including bindings on them) remain reservations.
    Legacy portable bindings live in receipt JSON; self bindings live in the
    verified public exchange document. Neither projection authorizes issuance.
    """
    if retained_deletion_identifier_reserved(session, identifier, replay=deletion_replay):
        return True
    if public_access_identifier_reserved(session, identifier):
        return True
    for row_type, primary_key, identifiers in (
        (
            AccountDeletionProofRow,
            AccountDeletionProofRow.operation_id,
            (AccountDeletionProofRow.operation_id,),
        ),
        (
            EnrollmentExchangeReceiptRow,
            EnrollmentExchangeReceiptRow.exchange_id,
            (
                EnrollmentExchangeReceiptRow.exchange_id,
                EnrollmentExchangeReceiptRow.binding_commit_id,
            ),
        ),
        (
            SessionRotationReceiptRow,
            SessionRotationReceiptRow.rotation_id,
            (SessionRotationReceiptRow.rotation_id,),
        ),
        (
            DeviceAdmissionExchangeReceiptRow,
            DeviceAdmissionExchangeReceiptRow.exchange_id,
            (
                DeviceAdmissionExchangeReceiptRow.exchange_id,
                DeviceAdmissionExchangeReceiptRow.binding_commit_id,
            ),
        ),
        (
            EnrollmentInvitationRow,
            EnrollmentInvitationRow.invitation_id,
            (EnrollmentInvitationRow.invitation_id,),
        ),
        (DeviceAdmissionRow, DeviceAdmissionRow.request_id, (DeviceAdmissionRow.request_id,)),
        (
            DeviceAdmissionWebOperationReceiptRow,
            DeviceAdmissionWebOperationReceiptRow.operation_id,
            (DeviceAdmissionWebOperationReceiptRow.operation_id,),
        ),
        (
            TrustedDeviceReenrollmentChallengeRow,
            TrustedDeviceReenrollmentChallengeRow.challenge_id,
            (TrustedDeviceReenrollmentChallengeRow.challenge_id,),
        ),
        (
            TrustedDeviceKeyRow,
            TrustedDeviceKeyRow.key_reference,
            (TrustedDeviceKeyRow.key_reference,),
        ),
        (
            ProfileLifecycleCommandRow,
            ProfileLifecycleCommandRow.operation_id,
            (ProfileLifecycleCommandRow.operation_id,),
        ),
        (
            AccountRecoveryOperationRow,
            AccountRecoveryOperationRow.operation_id,
            (
                AccountRecoveryOperationRow.operation_id,
                AccountRecoveryOperationRow.binding_commit_id,
            ),
        ),
        (
            AccountDeletionRequestRow,
            AccountDeletionRequestRow.deletion_request_id,
            (
                AccountDeletionRequestRow.deletion_request_id,
                AccountDeletionRequestRow.cancel_operation_id,
            ),
        ),
        (
            SelfDevicePairingRow,
            SelfDevicePairingRow.ceremony_id,
            (
                SelfDevicePairingRow.ceremony_id,
                SelfDevicePairingRow.start_operation_id,
                SelfDevicePairingRow.claim_id,
                SelfDevicePairingRow.approval_operation_id,
                SelfDevicePairingRow.exchange_id,
                SelfDevicePairingRow.binding_commit_id,
            ),
        ),
        (
            SelfPairingCommandRow,
            SelfPairingCommandRow.operation_id,
            (SelfPairingCommandRow.operation_id,),
        ),
        (
            PortableAccountChallengeRow,
            PortableAccountChallengeRow.challenge_id,
            (
                PortableAccountChallengeRow.challenge_id,
                PortableAccountChallengeRow.binding_commit_id,
            ),
        ),
    ):
        query = select(primary_key).where(or_(*(column == identifier for column in identifiers)))
        if row_type is own_profile_receipt and own_profile_operation_id is not None:
            query = query.where(primary_key != own_profile_operation_id)
        # These exclusions are only the original parent being replayed. A
        # parent reference from an exchange is never passed as a new reservation.
        if row_type is DeviceAdmissionRow and own_admission_request_id is not None:
            query = query.where(
                ~(
                    (primary_key == own_admission_request_id)
                    & (DeviceAdmissionRow.contract_version == "v2")
                )
            )
        if (
            row_type is TrustedDeviceReenrollmentChallengeRow
            and own_trusted_challenge_id is not None
        ):
            query = query.where(
                ~(
                    (primary_key == own_trusted_challenge_id)
                    & (TrustedDeviceReenrollmentChallengeRow.contract_version == "v2")
                )
            )
        if row_type is SelfDevicePairingRow and own_self_ceremony_id is not None:
            query = query.where(
                ~(
                    (primary_key == own_self_ceremony_id)
                    & (SelfDevicePairingRow.contract_version == "v2")
                )
            )
        if row_type is SelfPairingCommandRow and own_self_command_id is not None:
            query = query.where(
                ~(
                    (primary_key == own_self_command_id)
                    & (SelfPairingCommandRow.contract_version == "v2")
                )
            )
        if row_type is AccountRecoveryOperationRow and own_recovery_operation_id is not None:
            query = query.where(
                ~(
                    (primary_key == own_recovery_operation_id)
                    & (AccountRecoveryOperationRow.contract_version == "v2")
                )
            )
        if row_type is AccountDeletionRequestRow and own_deletion_request_id is not None:
            # Only exact cancel replay excludes its parent. The service verifies
            # the parent/operation/original before passing this exclusion.
            query = query.where(primary_key != own_deletion_request_id)
        if row_type is PortableAccountChallengeRow and own_portable_challenge_id is not None:
            # Only the verified original portable challenge/result row. This
            # exclusion never reaches deletion proof rows or retained SEALED IDs.
            query = query.where(primary_key != own_portable_challenge_id)
        if session.scalar(query.limit(1)) is not None:
            return True
    return (
        session.scalar(
            select(SelfDevicePairingRow.ceremony_id)
            .where(
                SelfDevicePairingRow.exchange_document["binding_commit_id"].astext
                == str(identifier),
                SelfDevicePairingRow.ceremony_id != own_self_ceremony_id
                if own_self_ceremony_id is not None
                else true(),
            )
            .limit(1)
        )
        is not None
        or session.scalar(
            select(PortableAccountChallengeRow.challenge_id)
            .where(
                PortableAccountChallengeRow.receipt["binding_commit_id"].astext == str(identifier),
                PortableAccountChallengeRow.challenge_id != own_portable_challenge_id
                if own_portable_challenge_id is not None
                else true(),
            )
            .limit(1)
        )
        is not None
    )
