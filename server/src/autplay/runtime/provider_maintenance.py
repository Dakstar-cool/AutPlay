"""Retain one maintenance child and its durable reservation through uncertain cleanup."""

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from autplay.adapters.child_process import provider_maintenance_child_launch
from autplay.adapters.filesystem.maintenance_protocol import result_identity
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import ChildLaunch, RetainedVaultProcess
from autplay.application.orphan_object_retirement import OrphanObjectClaim
from autplay.application.provider_cleanup import ProviderCleanupClaim
from autplay.application.provider_scratch import ProviderScratchClaim
from autplay.application.upload_cleanup import UploadCleanupClaim
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState, ProcessExitEvidence, ProcessIdentity
from autplay.domain.vault import OpaqueStorageKey
from autplay.runtime.resource_io_deadline import ResourceIoDeadline


class MaintenanceRepository(Protocol):
    def prepare(self, ticket: MaintenanceTicket) -> MaintenanceStatus: ...

    def start(self, ticket: MaintenanceTicket, child: ProcessIdentity) -> MaintenanceStatus: ...

    def status(self, ticket: MaintenanceTicket) -> MaintenanceStatus | None: ...

    def reconcile(self, ticket: MaintenanceTicket) -> MaintenanceStatus | None: ...

    def confirm(
        self, ticket: MaintenanceTicket, proof: ProcessExitEvidence
    ) -> MaintenanceStatus: ...


@dataclass
class _Retained:
    child: RetainedVaultProcess[MaintenanceTicket]
    done: threading.Event = field(default_factory=threading.Event)
    launch_gate: threading.Event = field(default_factory=threading.Event)
    launch_permitted: bool = False
    error: ResourceAdmissionError | None = None


class ProcessProviderMaintenanceStorage:
    """Provider service storage port; the configured root is touched only by the child.

    A five-second caller deadline requests stop. The sole pipe/DB owner remains
    retained after that deadline until exact exit and durable acknowledgement.
    Crashing the parent leaves the database reservation occupied; no TTL takeover.
    """

    def __init__(
        self,
        repository: MaintenanceRepository,
        root: Path,
        *,
        launch: ChildLaunch = provider_maintenance_child_launch,
    ) -> None:
        self._repository = repository
        self._root = Path(os.path.abspath(root))
        self._launch = launch
        self._owner = uuid4()
        self._lock = threading.Lock()
        self._entry: _Retained | None = None
        self._closing = False

    def retire(self, claim: ProviderCleanupClaim) -> None:
        self._execute(claim.execution_id, claim.claim_id, MaintenanceAction.CLEANUP)

    def retire_scratch(self, claim: ProviderScratchClaim) -> None:
        self._execute(claim.execution_id, claim.claim_id, MaintenanceAction.SCRATCH)

    def retire_upload(self, claim: UploadCleanupClaim) -> UUID:
        return self._execute(
            None, claim.claim_id, MaintenanceAction.UPLOAD_CLEANUP, claim.storage_key
        )

    def retire_orphan_object(self, claim: OrphanObjectClaim) -> UUID:
        return self._execute(
            None, claim.claim_id, MaintenanceAction.ORPHAN_OBJECT, claim.storage_key
        )

    def confirm_orphan_missing(self, claim: OrphanObjectClaim) -> UUID:
        return self._execute(
            None, claim.claim_id, MaintenanceAction.ORPHAN_MISSING, claim.storage_key
        )

    def pending(self) -> tuple[UUID, ...]:
        with self._lock:
            return () if self._entry is None else (self._entry.child.ticket.execution_id,)

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError("maintenance_shutdown_invalid")
        with self._lock:
            self._closing = True
            entry = self._entry
        if entry is not None:
            entry.child.request_stop()
            entry.done.wait(timeout)
        return self.pending()

    def _execute(
        self,
        execution_id: UUID | None,
        claim_id: UUID,
        action: MaintenanceAction,
        storage_key: OpaqueStorageKey | None = None,
    ) -> UUID:
        ticket = MaintenanceTicket(
            uuid4(), self._owner, execution_id, claim_id, action, storage_key
        )
        child = RetainedVaultProcess(ticket, ResourceIoDeadline(monotonic()), launch=self._launch)
        entry = _Retained(child)
        with self._lock:
            if self._closing or self._entry is not None:
                raise ResourceAdmissionError("maintenance_busy")
            self._entry = entry
        try:
            try:
                worker = threading.Thread(
                    target=self._work, args=(entry,), name="vault-maintenance", daemon=True
                )
                worker.start()
            except BaseException:
                raise ResourceAdmissionError("maintenance_unavailable") from None
            entry.launch_permitted = True
            entry.launch_gate.set()
            while not entry.done.wait(min(0.05, child.deadline.response_remaining())):
                if child.deadline.response_remaining() <= 0:
                    raise ResourceAdmissionError("maintenance_deadline_expired")
            if entry.error is not None:
                raise entry.error
            return ticket.execution_id
        finally:
            # Abnormal caller exit cannot strand a live child or a launch-gated worker.
            if not entry.done.is_set():
                child.request_stop()
            if not entry.launch_permitted:
                child.seal_without_spawn()
                self._forget(entry)
            entry.launch_gate.set()

    def _work(self, entry: _Retained) -> None:
        entry.launch_gate.wait()
        if not entry.launch_permitted:
            return
        child, result = entry.child, False
        ticket = child.ticket
        try:
            prepared = self._repository.prepare(ticket)
            if prepared.ticket != ticket or prepared.state != ExecutionState.PREPARED:
                raise ResourceAdmissionError("maintenance_execution_stale")
            child.deadline.check()
            identity = child.spawn()
            child.allow_go(self._repository.start(ticket, identity))
            child.go(
                {
                    "version": 1,
                    "root": str(self._root),
                    **result_identity(ticket),
                }
            )
            tag, payload = child.read_result()
            if tag != b"R" or decode_document(payload) != result_identity(ticket):
                raise ResourceAdmissionError("maintenance_storage_failed")
            while (proof := child.exit_evidence(identity)) is None:
                child.deadline.check()
                sleep(0.01)
            if proof.exit_code != 0:
                raise ResourceAdmissionError("maintenance_storage_failed")
            result = True
        except ResourceAdmissionError as error:
            entry.error = error
        except SQLAlchemyError, OSError, ValueError, RuntimeError:
            entry.error = ResourceAdmissionError("maintenance_unavailable")
        except BaseException:
            entry.error = ResourceAdmissionError("maintenance_unavailable")
        finally:
            if not result:
                child.deadline.freeze_error()
                child.seal_without_spawn()
                child.request_stop()
            self._drain(entry)

    def _drain(self, entry: _Retained) -> None:
        child = entry.child
        while True:
            try:
                status = self._repository.reconcile(child.ticket)
                identity = None if status is None else status.child
                proof = child.exit_evidence(identity)
                if proof is not None:
                    # Absence is acceptable only when spawning provably never began.
                    if status is None:
                        if proof.kind != "NOT_STARTED":
                            raise ResourceAdmissionError("maintenance_execution_unconfirmed")
                    else:
                        acknowledged = self._repository.confirm(child.ticket, proof)
                        if (
                            acknowledged.ticket != child.ticket
                            or acknowledged.state != ExecutionState.CLOSED
                        ):
                            raise ResourceAdmissionError("maintenance_execution_unconfirmed")
                    child.close_pipes_after_worker_exit()
                    child.close_tree_after_acknowledgement()
                    self._forget(entry)
                    return
            except SQLAlchemyError, OSError, ResourceAdmissionError:
                # Keep exactly this handle/reservation. A later acknowledgement may recover.
                pass
            sleep(0.1)

    def _forget(self, entry: _Retained) -> None:
        with self._lock:
            if self._entry is entry:
                self._entry = None
        entry.done.set()
