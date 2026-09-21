"""Versioned quota edits and inheritance snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from uuid import UUID

from autplay.domain.resource_admission import (
    AccountLimitOverride,
    AccountLimits,
    ResourceAdmissionError,
    positive_limit,
)


@dataclass(frozen=True, slots=True)
class GlobalResourceLimits:
    playbacks: int
    transfers: int

    def __post_init__(self) -> None:
        positive_limit(self.playbacks)
        positive_limit(self.transfers)


class QuotaAction(StrEnum):
    DEFAULTS = "DEFAULTS"
    OVERRIDE = "OVERRIDE"
    BUDGET = "BUDGET"


@dataclass(frozen=True, slots=True)
class QuotaChange:
    operation_id: UUID
    expected_global_revision: int
    values: AccountLimits | AccountLimitOverride | GlobalResourceLimits
    target_user_id: UUID | None = None
    expected_account_revision: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.expected_global_revision) is not int
            or not 1 <= self.expected_global_revision < 2**53
        ):
            raise ResourceAdmissionError("resource_revision_invalid")
        if isinstance(self.values, AccountLimitOverride):
            if (
                self.target_user_id is None
                or type(self.expected_account_revision) is not int
                or not 0 <= self.expected_account_revision < 2**53
            ):
                raise ResourceAdmissionError("resource_revision_invalid")
        elif (
            not isinstance(self.values, (AccountLimits, GlobalResourceLimits))
            or self.target_user_id is not None
            or self.expected_account_revision is not None
        ):
            raise ResourceAdmissionError("resource_request_invalid")

    @property
    def action(self) -> QuotaAction:
        if isinstance(self.values, AccountLimitOverride):
            return QuotaAction.OVERRIDE
        return (
            QuotaAction.DEFAULTS if isinstance(self.values, AccountLimits) else QuotaAction.BUDGET
        )

    @property
    def digest(self) -> bytes:
        body = {**asdict(self), "action": self.action.value}
        return hashlib.sha256(
            json.dumps(
                body,
                default=str,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).digest()


@dataclass(frozen=True, slots=True)
class QuotaPolicySnapshot:
    global_revision: int
    defaults: AccountLimits
    global_playbacks: int | None
    global_transfers: int | None
    playback_ceiling: int | None
    transfer_ceiling: int | None
    target_user_id: UUID | None
    account_revision: int
    override: AccountLimitOverride

    @property
    def effective(self) -> AccountLimits:
        return self.override.effective(self.defaults)


@dataclass(frozen=True, slots=True)
class QuotaUsageSummary:
    devices: int
    playbacks: int
    transfers: int
    waiting_playbacks: int
    waiting_transfers: int


@dataclass(frozen=True, slots=True)
class QuotaAccountItem:
    user_id: UUID
    display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class QuotaAccountPage:
    items: tuple[QuotaAccountItem, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class QuotaEditorSnapshot:
    policy: QuotaPolicySnapshot
    usage: QuotaUsageSummary
    target: QuotaAccountItem | None
