"""Owner-scoped, revisioned watch auto-download consent commands."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from autplay.application.discovery_automation import AUTO_IMPORT_CONFIRMATION
from autplay.domain.auth import OwnedObjectNotFoundError

from .models import WatchAutoDownloadConsentRevisionRow, WatchAutoDownloadConsentRow
from .watch_auto_download_authority import consent_state, gate_state


class WatchAutoDownloadError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WatchAutoDownloadState:
    enabled: bool
    revision: int
    execution_enabled: bool


class WatchAutoDownloadRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, owner_id: UUID) -> WatchAutoDownloadState:
        self._require_active_owner(owner_id)
        gate_enabled, gate_revision = gate_state(self._session)
        enabled, revision = consent_state(self._session, owner_id, gate_revision=gate_revision)
        return WatchAutoDownloadState(enabled, revision, gate_enabled)

    def set(
        self,
        owner_id: UUID,
        *,
        enabled: bool,
        expected_revision: int,
        confirmation_code: str | None,
        now: datetime,
    ) -> WatchAutoDownloadState:
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("watch auto-download command is invalid")
        if now.tzinfo is None:
            raise ValueError("watch auto-download time must be aware")
        if enabled:
            if confirmation_code != AUTO_IMPORT_CONFIRMATION:
                raise WatchAutoDownloadError("release_watch_auto_download_confirmation_invalid")
        elif confirmation_code is not None:
            raise WatchAutoDownloadError("release_watch_auto_download_confirmation_invalid")
        gate_enabled, gate_revision = gate_state(self._session, lock=True)
        if enabled and not gate_enabled:
            raise WatchAutoDownloadError("release_watch_auto_download_unavailable")
        # Owner share lock keeps account purge and follow revocation serialized.
        self._require_active_owner(owner_id, lock=True)
        scope = b"m11-watch-auto-consent-v1" + owner_id.bytes
        lock_key = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        row = self._session.scalar(
            select(WatchAutoDownloadConsentRow)
            .where(WatchAutoDownloadConsentRow.user_id == owner_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        revision = row.revision if row is not None else 0
        if revision != expected_revision:
            raise WatchAutoDownloadError("release_watch_auto_download_revision_stale")
        if row is None and not enabled:
            return WatchAutoDownloadState(False, 0, gate_enabled)
        if (
            row is not None
            and row.enabled == enabled
            and (not enabled or row.gate_revision == gate_revision)
        ):
            return WatchAutoDownloadState(enabled, revision, gate_enabled)
        next_revision = revision + 1
        granted_gate_revision = gate_revision if enabled else None
        confirmed = AUTO_IMPORT_CONFIRMATION if enabled else None
        if row is None:
            row = WatchAutoDownloadConsentRow(user_id=owner_id)
            self._session.add(row)
        row.enabled = enabled
        row.revision = next_revision
        row.gate_revision = granted_gate_revision
        row.confirmation_code = confirmed
        row.changed_at = now
        self._session.add(
            WatchAutoDownloadConsentRevisionRow(
                user_id=owner_id,
                revision=next_revision,
                enabled=enabled,
                gate_revision=granted_gate_revision,
                confirmation_code=confirmed,
                changed_at=now,
            )
        )
        self._session.flush()
        return WatchAutoDownloadState(enabled, next_revision, gate_enabled)

    def _require_active_owner(self, owner_id: UUID, *, lock: bool = False) -> None:
        suffix = " FOR SHARE" if lock else ""
        found = self._session.scalar(
            text(
                "SELECT user_id FROM account.user_account WHERE user_id=:owner "
                "AND status='ACTIVE' AND deleted_at IS NULL" + suffix
            ),
            {"owner": owner_id},
        )
        if found is None:
            raise OwnedObjectNotFoundError()


__all__ = (
    "WatchAutoDownloadError",
    "WatchAutoDownloadRepository",
    "WatchAutoDownloadState",
)
