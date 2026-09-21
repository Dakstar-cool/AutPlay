"""Exact quota changes preserving active operations and account inheritance."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_policy import (
    GlobalResourceLimits,
    QuotaAccountPage,
    QuotaChange,
    QuotaEditorSnapshot,
    QuotaPolicySnapshot,
)
from autplay.domain.web_admin import WebActor
from autplay.ports.resource_policy import ResourcePolicyUnitOfWorkFactory


class ResourcePolicyService:
    def __init__(self, units: ResourcePolicyUnitOfWorkFactory) -> None:
        self._units = units

    def view(self, actor: WebActor, target: UUID | None = None) -> QuotaPolicySnapshot:
        with self._units() as unit:
            unit.policy.lock_actor(actor, target, mutation=False)
            return unit.policy.snapshot(target)

    def editor(self, actor: WebActor, target: UUID | None = None) -> QuotaEditorSnapshot:
        with self._units() as unit:
            now = unit.policy.lock_actor(actor, target, mutation=False)
            return unit.policy.editor(target, now)

    def accounts(self, actor: WebActor, after: UUID | None = None) -> QuotaAccountPage:
        with self._units() as unit:
            unit.policy.lock_actor(actor, None, mutation=False)
            return unit.policy.accounts(actor.user_id, after)

    def apply(self, actor: WebActor, change: QuotaChange) -> dict[str, object]:
        with self._units() as unit:
            repo = unit.policy
            now = repo.lock_actor(actor, change.target_user_id, mutation=True)
            replay = repo.replay(actor, change)
            if replay is not None:
                return replay
            before = repo.snapshot(change.target_user_id)
            if before.global_revision != change.expected_global_revision or (
                change.target_user_id is not None
                and before.account_revision != change.expected_account_revision
            ):
                raise ResourceAdmissionError("resource_revision_stale")
            if isinstance(change.values, GlobalResourceLimits):
                if before.playback_ceiling is None or before.transfer_ceiling is None:
                    raise ResourceAdmissionError("resource_budget_unconfigured")
                if (
                    change.values.playbacks > before.playback_ceiling
                    or change.values.transfers > before.transfer_ceiling
                ):
                    raise ResourceAdmissionError("resource_budget_exceeded")
            repo.save(change, now)
            snapshot = repo.snapshot(change.target_user_id)
            result: dict[str, object] = {
                "operation_id": str(change.operation_id),
                "outcome": "APPLIED",
                "global_revision": snapshot.global_revision,
                "target_user_id": str(snapshot.target_user_id) if snapshot.target_user_id else None,
                "account_revision": snapshot.account_revision,
                "defaults": asdict(snapshot.defaults),
                "override": asdict(snapshot.override),
                "effective": asdict(snapshot.effective),
                "global_playbacks": snapshot.global_playbacks,
                "global_transfers": snapshot.global_transfers,
                "applied_at": now.isoformat().replace("+00:00", "Z"),
            }
            repo.receipt(actor, change, result, now)
            repo.advance(now)
            unit.commit()
            return result
