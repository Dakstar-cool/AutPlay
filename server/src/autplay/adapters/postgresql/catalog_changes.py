"""Auditable, reversible P10 catalog change-set commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.domain.auth import AccountRole, Principal

from .models import (
    AuditEventRow,
    CatalogChangeItemRow,
    CatalogChangeSetRow,
    RecordingRedirectRow,
    RecordingRow,
    ReleaseTrackRow,
)


class CatalogChangeError(RuntimeError):
    """A change set cannot be safely proposed or transitioned."""

    code: ClassVar[str] = "catalog_change.invalid"

    def __init__(self, code: str | None = None) -> None:
        super().__init__(code or self.code)


class CatalogChangeNotFound(CatalogChangeError):
    code = "catalog_change.not_found"


class CatalogChangeConflict(CatalogChangeError):
    code = "catalog_change.conflict"


@dataclass(frozen=True, slots=True)
class CatalogChangeResult:
    """Stable state returned after proposal, apply, or undo."""

    change_set_id: UUID
    operation_type: str
    status: str
    inverse_change_set_id: UUID | None = None


class PostgresCatalogChangeRepository:
    """Apply only explicit admin commands; the matcher never calls this adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def propose_recording_change(
        self,
        *,
        principal: Principal,
        operation_type: str,
        source_recording_id: UUID,
        target_recording_id: UUID,
        reason: str,
        now: datetime,
    ) -> CatalogChangeResult:
        """Propose an explicit reversible MERGE or SPLIT redirect change."""

        _require_admin(principal)
        _require_reason(reason)
        if operation_type not in {"MERGE", "SPLIT"} or source_recording_id == target_recording_id:
            raise CatalogChangeConflict
        source, target = self._lock_recordings(source_recording_id, target_recording_id)
        redirect = self._session.get(RecordingRedirectRow, source_recording_id)
        if operation_type == "MERGE":
            if (
                source.identity_status not in {"ACTIVE", "PROVISIONAL"}
                or target.identity_status not in {"ACTIVE", "PROVISIONAL"}
                or redirect is not None
            ):
                raise CatalogChangeConflict
            before: dict[str, object] = {
                "identity_status": source.identity_status,
                "redirect_target_id": None,
            }
            after: dict[str, object] = {
                "identity_status": "MERGED",
                "redirect_target_id": str(target_recording_id),
            }
        else:
            if (
                source.identity_status != "MERGED"
                or redirect is None
                or redirect.target_recording_id != target_recording_id
            ):
                raise CatalogChangeConflict
            before = {
                "identity_status": "MERGED",
                "redirect_target_id": str(target_recording_id),
            }
            after = {"identity_status": "ACTIVE", "redirect_target_id": None}
        change_set = CatalogChangeSetRow(
            operation_type=operation_type,
            actor_type="ADMIN",
            actor_user_id=principal.user_id,
            reason=reason,
            confidence=None,
            reversible_until=now + timedelta(days=30),
            status="PLANNED",
        )
        self._session.add(change_set)
        self._session.flush([change_set])
        self._session.add(
            CatalogChangeItemRow(
                change_set_id=change_set.change_set_id,
                entity_type="RECORDING_REDIRECT",
                entity_id=source_recording_id,
                action=operation_type,
                from_snapshot=before,
                to_snapshot=after,
                sequence_no=1,
            )
        )
        self._audit(principal, "catalog_change.proposed", change_set.change_set_id, operation_type)
        return CatalogChangeResult(change_set.change_set_id, operation_type, "PLANNED")

    def propose_release_track_reassign(
        self,
        *,
        principal: Principal,
        release_track_id: UUID,
        target_recording_id: UUID,
        reason: str,
        now: datetime,
    ) -> CatalogChangeResult:
        """Propose one explicit release-position reassignment."""

        _require_admin(principal)
        _require_reason(reason)
        current_recording_id = self._session.scalar(
            select(ReleaseTrackRow.recording_id).where(
                ReleaseTrackRow.release_track_id == release_track_id
            )
        )
        if current_recording_id is None:
            raise CatalogChangeConflict
        recordings = self._lock_recording_ids((current_recording_id, target_recording_id))
        release_track = self._session.scalar(
            select(ReleaseTrackRow)
            .where(ReleaseTrackRow.release_track_id == release_track_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        target = recordings[target_recording_id]
        if (
            release_track is None
            or release_track.recording_id != current_recording_id
            or target.identity_status not in {"ACTIVE", "PROVISIONAL"}
            or release_track.recording_id == target_recording_id
        ):
            raise CatalogChangeConflict
        change_set = CatalogChangeSetRow(
            operation_type="REASSIGN",
            actor_type="ADMIN",
            actor_user_id=principal.user_id,
            reason=reason,
            confidence=None,
            reversible_until=now + timedelta(days=30),
            status="PLANNED",
        )
        self._session.add(change_set)
        self._session.flush([change_set])
        self._session.add(
            CatalogChangeItemRow(
                change_set_id=change_set.change_set_id,
                entity_type="RELEASE_TRACK",
                entity_id=release_track_id,
                action="REASSIGN",
                from_snapshot={"recording_id": str(release_track.recording_id)},
                to_snapshot={"recording_id": str(target_recording_id)},
                sequence_no=1,
            )
        )
        self._audit(principal, "catalog_change.proposed", change_set.change_set_id, "REASSIGN")
        return CatalogChangeResult(change_set.change_set_id, "REASSIGN", "PLANNED")

    def apply(
        self, *, principal: Principal, change_set_id: UUID, now: datetime
    ) -> CatalogChangeResult:
        """Apply a reviewed proposal only if its before-snapshot still matches."""

        _require_admin(principal)
        change_set, item = self._lock_change(change_set_id)
        if change_set.status != "PLANNED":
            raise CatalogChangeConflict
        if change_set.reversible_until is not None and change_set.reversible_until <= now:
            raise CatalogChangeConflict("catalog_change.reversibility_expired")
        self._apply_item(change_set, item, forward=True)
        change_set.status = "APPLIED"
        self._audit(
            principal,
            "catalog_change.applied",
            change_set.change_set_id,
            change_set.operation_type,
        )
        return CatalogChangeResult(
            change_set.change_set_id, change_set.operation_type, change_set.status
        )

    def undo(
        self, *, principal: Principal, change_set_id: UUID, now: datetime
    ) -> CatalogChangeResult:
        """Apply the stored inverse snapshot and append a separate UNDO audit set."""

        _require_admin(principal)
        original, original_item = self._lock_change(change_set_id)
        if original.status != "APPLIED":
            raise CatalogChangeConflict
        if original.reversible_until is not None and original.reversible_until <= now:
            raise CatalogChangeConflict("catalog_change.reversibility_expired")
        inverse = CatalogChangeSetRow(
            operation_type="UNDO",
            actor_type="ADMIN",
            actor_user_id=principal.user_id,
            reason=f"Undo change set {change_set_id}",
            confidence=None,
            reversible_until=None,
            status="PLANNED",
        )
        self._session.add(inverse)
        self._session.flush([inverse])
        inverse_item = CatalogChangeItemRow(
            change_set_id=inverse.change_set_id,
            entity_type=original_item.entity_type,
            entity_id=original_item.entity_id,
            action=f"UNDO_{original_item.action}"[:100],
            from_snapshot=original_item.to_snapshot,
            to_snapshot=original_item.from_snapshot,
            sequence_no=1,
        )
        self._session.add(inverse_item)
        self._apply_item(inverse, inverse_item, forward=True)
        original.status = "REVERTED"
        inverse.status = "APPLIED"
        self._audit(principal, "catalog_change.undone", inverse.change_set_id, "UNDO")
        return CatalogChangeResult(
            original.change_set_id,
            original.operation_type,
            original.status,
            inverse_change_set_id=inverse.change_set_id,
        )

    def _apply_item(
        self,
        change_set: CatalogChangeSetRow,
        item: CatalogChangeItemRow,
        *,
        forward: bool,
    ) -> None:
        before = _snapshot(item.from_snapshot if forward else item.to_snapshot)
        after = _snapshot(item.to_snapshot if forward else item.from_snapshot)
        if item.entity_type == "RECORDING_REDIRECT":
            expected_target = _snapshot_uuid(before, "redirect_target_id")
            target = _snapshot_uuid(after, "redirect_target_id")
            recording_ids = tuple(
                value for value in (item.entity_id, expected_target, target) if value is not None
            )
            recordings = self._lock_recording_ids(recording_ids)
            source = recordings[item.entity_id]
            current_redirect = self._session.scalar(
                select(RecordingRedirectRow)
                .where(RecordingRedirectRow.source_recording_id == item.entity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_target = (
                current_redirect.target_recording_id if current_redirect is not None else None
            )
            if (
                source.identity_status != before.get("identity_status")
                or current_target != expected_target
            ):
                raise CatalogChangeConflict
            source.identity_status = str(after["identity_status"])
            if target is None:
                if current_redirect is not None:
                    self._session.delete(current_redirect)
            else:
                inbound_redirect = self._session.scalar(
                    select(RecordingRedirectRow.source_recording_id)
                    .where(RecordingRedirectRow.target_recording_id == source.recording_id)
                    .with_for_update()
                    .limit(1)
                )
                if inbound_redirect is not None:
                    raise CatalogChangeConflict(
                        "catalog_change.inbound_redirect_requires_explicit_plan"
                    )
                target_row = recordings[target]
                if target_row.identity_status not in {
                    "ACTIVE",
                    "PROVISIONAL",
                }:
                    raise CatalogChangeConflict
                self._session.add(
                    RecordingRedirectRow(
                        source_recording_id=source.recording_id,
                        target_recording_id=target,
                        change_set_id=change_set.change_set_id,
                        reason=change_set.reason,
                    )
                )
        elif item.entity_type == "RELEASE_TRACK":
            expected = _snapshot_uuid(before, "recording_id")
            target = _snapshot_uuid(after, "recording_id")
            if expected is None or target is None:
                raise CatalogChangeConflict
            recordings = self._lock_recording_ids((expected, target))
            release_track = self._session.scalar(
                select(ReleaseTrackRow)
                .where(ReleaseTrackRow.release_track_id == item.entity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if release_track is None:
                raise CatalogChangeConflict
            if release_track.recording_id != expected:
                raise CatalogChangeConflict
            target_row = recordings[target]
            if target_row.identity_status not in {"ACTIVE", "PROVISIONAL"}:
                raise CatalogChangeConflict
            release_track.recording_id = target
        else:
            raise CatalogChangeConflict("catalog_change.item_type_unsupported")

    def _lock_change(self, change_set_id: UUID) -> tuple[CatalogChangeSetRow, CatalogChangeItemRow]:
        change_set = self._session.scalar(
            select(CatalogChangeSetRow)
            .where(CatalogChangeSetRow.change_set_id == change_set_id)
            .with_for_update()
        )
        if change_set is None:
            raise CatalogChangeNotFound
        items = tuple(
            self._session.scalars(
                select(CatalogChangeItemRow)
                .where(CatalogChangeItemRow.change_set_id == change_set_id)
                .order_by(CatalogChangeItemRow.sequence_no)
            ).all()
        )
        if len(items) != 1:
            raise CatalogChangeConflict("catalog_change.item_count_invalid")
        return change_set, items[0]

    def _lock_recordings(
        self, first_id: UUID, second_id: UUID
    ) -> tuple[RecordingRow, RecordingRow]:
        by_id = self._lock_recording_ids((first_id, second_id))
        return by_id[first_id], by_id[second_id]

    def _lock_recording_ids(self, recording_ids: tuple[UUID, ...]) -> dict[UUID, RecordingRow]:
        ordered_ids = tuple(sorted(set(recording_ids)))
        rows = tuple(
            self._session.scalars(
                select(RecordingRow)
                .where(RecordingRow.recording_id.in_(ordered_ids))
                .order_by(RecordingRow.recording_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        if len(rows) != len(ordered_ids):
            raise CatalogChangeNotFound
        return {row.recording_id: row for row in rows}

    def _audit(
        self,
        principal: Principal,
        action: str,
        change_set_id: UUID,
        operation_type: str,
    ) -> None:
        self._session.add(
            AuditEventRow(
                actor_type="ADMIN",
                actor_user_id=principal.user_id,
                actor_device_id=principal.device_id,
                action=action,
                target_type="CATALOG_CHANGE_SET",
                target_id=change_set_id,
                request_id=None,
                reason_code=operation_type,
                metadata_sanitized={"schema_version": 1, "operation_type": operation_type},
            )
        )


def _require_admin(principal: Principal) -> None:
    if principal.role not in {AccountRole.OWNER, AccountRole.ADMIN}:
        raise CatalogChangeError("catalog_change.admin_required")


def _require_reason(reason: str) -> None:
    if not 1 <= len(reason.strip()) <= 4_000:
        raise ValueError("catalog change reason is invalid")


def _snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogChangeConflict("catalog_change.snapshot_invalid")
    return {str(key): item for key, item in value.items()}


def _snapshot_uuid(snapshot: dict[str, object], key: str) -> UUID | None:
    value = snapshot.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogChangeConflict("catalog_change.snapshot_invalid")
    try:
        return UUID(value)
    except ValueError as error:
        raise CatalogChangeConflict("catalog_change.snapshot_invalid") from error


__all__ = (
    "CatalogChangeConflict",
    "CatalogChangeError",
    "CatalogChangeNotFound",
    "CatalogChangeResult",
    "PostgresCatalogChangeRepository",
)
