"""Retain a contained trainer until exact tree exit and durable PostgreSQL acknowledgement."""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic, sleep
from typing import Protocol
from uuid import UUID

from autplay.adapters.child_process import training_child_launch
from autplay.adapters.filesystem.vault_process import (
    ChildLaunch,
    ProcessTreeFactory,
    RetainedVaultProcess,
)
from autplay.domain.ingest_execution import IngestExecutionStatus
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.training_execution import TrainingExecutionCleanupPlan, TrainingExecutionTicket
from autplay.domain.training_work import TrainingInputProvenance
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from sqlalchemy.exc import SQLAlchemyError

from .authority import SonaSharedTrainingAuthority, SonaTrainingInputBinding
from .execution_protocol import (
    SonaCheckpointReservation,
    SonaControlledTrainingCommand,
    SonaControlledTrainingResult,
    finalize_checkpoint_cleanup,
    finalize_checkpoint_output,
    finalize_training_input,
    finalize_training_input_cleanup,
    inventory_matches,
    parse_authorize_request,
    parse_check_request,
    parse_seal_request,
    reserve_checkpoint_output,
    validate_training_storage_roots,
)
from .root_inventory import SonaTrainingRootInventory, inspect_sona_training_root

type TrainingExecutionStatus = IngestExecutionStatus[TrainingExecutionTicket]


class TrainingExecutionRepository(Protocol):
    def prepare(self, ticket: TrainingExecutionTicket) -> TrainingExecutionStatus: ...
    def start(
        self, ticket: TrainingExecutionTicket, child: ProcessIdentity
    ) -> TrainingExecutionStatus: ...
    def renew(
        self, ticket: TrainingExecutionTicket, child: ProcessIdentity
    ) -> TrainingExecutionStatus: ...
    def bind_checkpoint(
        self,
        ticket: TrainingExecutionTicket,
        child: ProcessIdentity,
        *,
        device: str,
        inode: str,
    ) -> TrainingExecutionStatus: ...
    def reconcile(self, ticket: TrainingExecutionTicket) -> TrainingExecutionStatus | None: ...
    def cleanup_plan(
        self, ticket: TrainingExecutionTicket
    ) -> TrainingExecutionCleanupPlan | None: ...
    def pending_cleanups(self, *, limit: int = 100) -> tuple[TrainingExecutionCleanupPlan, ...]: ...
    def begin_cleanup(
        self,
        ticket: TrainingExecutionTicket,
        proof: ProcessExitEvidence,
        *,
        retain_checkpoint: bool | None,
        checkpoint_manifest_sha256: str | None,
        checkpoint_weights_sha256: str | None = None,
        checkpoint_optimizer_steps: int | None = None,
        checkpoint_device_type: str | None = None,
        publication_seal_sha256: str | None = None,
    ) -> TrainingExecutionStatus: ...
    def decide_checkpoint(
        self, ticket: TrainingExecutionTicket, *, retain_checkpoint: bool
    ) -> TrainingExecutionCleanupPlan: ...
    def confirm(
        self, ticket: TrainingExecutionTicket, input_cleanup_sha256: str
    ) -> TrainingExecutionStatus: ...


@dataclass(frozen=True, slots=True)
class SonaTrainingStorageScopes:
    input_root: Path
    output_root: Path

    def __post_init__(self) -> None:
        try:
            common = os.path.commonpath((str(self.input_root), str(self.output_root)))
        except ValueError:
            common = ""
        if (
            not self.input_root.is_absolute()
            or not self.output_root.is_absolute()
            or str(self.input_root) != os.path.abspath(self.input_root)
            or str(self.output_root) != os.path.abspath(self.output_root)
            or os.path.normcase(common)
            in {
                os.path.normcase(str(self.input_root)),
                os.path.normcase(str(self.output_root)),
            }
        ):
            raise ValueError("training_storage_scopes_invalid")

    def validate(self, ticket: TrainingExecutionTicket) -> None:
        if (
            Path(ticket.input_root).parent != self.input_root
            or Path(ticket.output_root) != self.output_root
        ):
            raise ValueError("training_storage_scope_mismatch")
        validate_training_storage_roots(ticket)


class SonaTrainingCleanupRecovery:
    """Replay durable exact-exit cleanup only inside configured exclusive roots."""

    def __init__(
        self,
        repository: TrainingExecutionRepository,
        storage_scopes: SonaTrainingStorageScopes,
    ) -> None:
        self._repository, self._storage_scopes = repository, storage_scopes
        self._lock = threading.Lock()

    def recover_pending(self, *, limit: int = 100) -> tuple[UUID, ...]:
        completed: list[UUID] = []
        with self._lock:
            for plan in self._repository.pending_cleanups(limit=limit):
                self._storage_scopes.validate(plan.ticket)
                if plan.retain_checkpoint is None:
                    retain = True
                    try:
                        verified = finalize_checkpoint_cleanup(
                            replace(plan, retain_checkpoint=True)
                        )
                        if (
                            verified is None
                            or verified.weights_sha256 != plan.checkpoint_weights_sha256
                            or verified.optimizer_steps != plan.checkpoint_optimizer_steps
                            or verified.device_type != plan.checkpoint_device_type
                        ):
                            raise ValueError("controlled training result does not match checkpoint")
                    except ValueError:
                        retain = False
                    plan = self._repository.decide_checkpoint(
                        plan.ticket,
                        retain_checkpoint=retain,
                    )
                finalize_checkpoint_cleanup(plan)
                evidence = finalize_training_input_cleanup(plan.ticket)
                status = self._repository.confirm(plan.ticket, evidence)
                if status.state != ExecutionState.CLOSED:
                    raise ResourceAdmissionError("training_execution_unconfirmed")
                completed.append(plan.ticket.execution_id)
        return tuple(completed)


@dataclass
class _Retained:
    child: RetainedVaultProcess[TrainingExecutionTicket]
    command: SonaControlledTrainingCommand
    authority: SonaSharedTrainingAuthority
    input_inventory: SonaTrainingRootInventory
    before_cleanup: Callable[[Path, SonaControlledTrainingResult], None] | None
    done: threading.Event = field(default_factory=threading.Event)
    launch_gate: threading.Event = field(default_factory=threading.Event)
    io_done: threading.Event = field(default_factory=threading.Event)
    freeze_requested: threading.Event = field(default_factory=threading.Event)
    renew_lock: threading.Lock = field(default_factory=threading.Lock)
    launch_permitted: bool = False
    result: SonaControlledTrainingResult | None = None
    error: BaseException | None = None
    authorized: TrainingInputProvenance | None = None
    binding: SonaTrainingInputBinding | None = None
    sealed: bool = False
    sealed_manifest_sha256: str | None = None
    prepared_owned: bool = False
    identity: ProcessIdentity | None = None
    registered: bool = False
    checkpoint_reservation: SonaCheckpointReservation | None = None
    checkpoint_bound: bool = False
    retain_checkpoint: bool = False
    output_finalized: bool = False
    input_cleanup_sha256: str | None = None
    cleanup_registered: bool = False
    publication_attempted: bool = False


class SonaTrainingProcessCoordinator:
    """Own the sole pipe worker, renewal watchdog and unreconciled process handle."""

    def __init__(
        self,
        repository: TrainingExecutionRepository,
        *,
        tree_factory: ProcessTreeFactory,
        storage_scopes: SonaTrainingStorageScopes,
        maximum: int = 1,
        launch: ChildLaunch = training_child_launch,
        on_drained: Callable[[], None] | None = None,
    ) -> None:
        if type(maximum) is not int or not 1 <= maximum <= 4096:
            raise ValueError("training_supervisor_bound_invalid")
        self._repository = repository
        self._storage_scopes = storage_scopes
        self._recovery = SonaTrainingCleanupRecovery(repository, storage_scopes)
        self._tree_factory, self._launch, self._maximum = tree_factory, launch, maximum
        self._on_drained = on_drained
        self._lock = threading.Lock()
        self._entries: dict[UUID, _Retained] = {}
        self._closing = False

    def recover_pending(self, *, limit: int = 100) -> tuple[UUID, ...]:
        """Resume only durable STOPPING plans that already contain exact exit proof."""

        return self._recovery.recover_pending(limit=limit)

    def pending(self) -> tuple[UUID, ...]:
        with self._lock:
            return tuple(self._entries)

    def shutdown(self, *, timeout: float = 5) -> tuple[UUID, ...]:
        if not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError("training_shutdown_invalid")
        with self._lock:
            self._closing = True
            entries = tuple(self._entries.values())
        for entry in entries:
            entry.freeze_requested.set()
            entry.child.request_stop()
        until = monotonic() + timeout
        for entry in entries:
            entry.done.wait(max(0, until - monotonic()))
        return self.pending()

    def run(
        self,
        ticket: TrainingExecutionTicket,
        command: SonaControlledTrainingCommand,
        authority: SonaSharedTrainingAuthority,
        *,
        before_cleanup: Callable[[Path, SonaControlledTrainingResult], None] | None = None,
    ) -> SonaControlledTrainingResult:
        try:
            self._storage_scopes.validate(ticket)
            inventory = inspect_sona_training_root(command.input_root)
        except OSError, ValueError:
            raise ResourceAdmissionError("training_execution_stale") from None
        if (
            command.execution_id != ticket.execution_id
            or command.run_id != ticket.run_id
            or str(command.input_root) != ticket.input_root
            or str(command.output_root) != ticket.output_root
            or inventory.root_device != ticket.input_root_device
            or inventory.root_inode != ticket.input_root_inode
            or command.input_inventory_sha256 != ticket.input_inventory_sha256.hex
            or command.root_inventory_sha256 != ticket.root_inventory_sha256.hex
            or command.input_bytes != ticket.input_bytes
            or command.maximum_output_bytes != ticket.maximum_output_bytes
            or not inventory_matches(command, inventory)
        ):
            raise ResourceAdmissionError("training_execution_stale")
        child = RetainedVaultProcess(
            ticket,
            ResourceIoDeadline(monotonic()),
            tree_factory=self._tree_factory,
            launch=self._launch,
        )
        entry = _Retained(child, command, authority, inventory, before_cleanup)
        with self._lock:
            if self._closing or len(self._entries) >= self._maximum:
                raise ResourceAdmissionError("training_execution_busy")
            if ticket.execution_id in self._entries:
                raise ResourceAdmissionError("training_execution_conflict")
            self._entries[ticket.execution_id] = entry
        try:
            try:
                for name, target in (
                    ("training-watchdog", self._watch),
                    ("training-work", self._work),
                ):
                    threading.Thread(target=target, args=(entry,), name=name, daemon=True).start()
            except BaseException:
                raise ResourceAdmissionError("training_execution_unavailable") from None
            entry.launch_permitted = True
            entry.launch_gate.set()
            while not entry.done.wait(0.05):
                if not entry.io_done.is_set() and child.deadline.response_remaining() <= 0:
                    raise ResourceAdmissionError("training_io_deadline_expired")
            if entry.error is not None:
                raise entry.error
            if entry.result is None:
                raise ResourceAdmissionError("training_child_failed")
            return entry.result
        finally:
            if not entry.done.is_set():
                entry.freeze_requested.set()
                child.request_stop()
            if not entry.launch_permitted:
                child.seal_without_spawn()
                self._forget(entry)
            entry.launch_gate.set()

    @staticmethod
    def _watch(entry: _Retained) -> None:
        entry.launch_gate.wait()
        if not entry.launch_permitted:
            return
        while not entry.io_done.wait(0.025):
            if entry.child.deadline.stopped():
                entry.freeze_requested.set()
                entry.child.request_stop()
                return

    @staticmethod
    def _accepted(
        status: TrainingExecutionStatus,
        ticket: TrainingExecutionTicket,
        identity: ProcessIdentity,
    ) -> bool:
        return (
            status.ticket == ticket
            and status.state == ExecutionState.RUNNING
            and status.child == identity
        )

    def _accept(
        self,
        entry: _Retained,
        sequence: int,
        status: TrainingExecutionStatus,
        identity: ProcessIdentity,
    ) -> None:
        if not entry.child.deadline.finish_renewal(
            sequence,
            succeeded=self._accepted(status, entry.child.ticket, identity),
            authorized_seconds=status.authorized_seconds,
        ):
            raise ResourceAdmissionError("training_execution_stale")

    def _renew(self, entry: _Retained, identity: ProcessIdentity) -> None:
        while not entry.freeze_requested.wait(0.5):
            try:
                with entry.renew_lock:
                    if entry.freeze_requested.is_set():
                        return
                    sequence = entry.child.deadline.begin_renewal()
                    try:
                        status = self._repository.renew(entry.child.ticket, identity)
                    except BaseException:
                        entry.child.deadline.finish_renewal(sequence, succeeded=False)
                        raise
                    self._accept(entry, sequence, status, identity)
            except BaseException:
                entry.freeze_requested.set()
                entry.child.deadline.freeze_error()
                entry.child.request_stop()
                return

    def _handle(
        self,
        entry: _Retained,
        identity: ProcessIdentity,
        tag: bytes,
        document: dict[str, object],
    ) -> dict[str, object]:
        with entry.renew_lock:
            if entry.freeze_requested.is_set():
                raise ResourceAdmissionError("training_execution_stale")
            sequence = entry.child.deadline.begin_renewal()
            try:
                if tag == b"A":
                    if entry.authorized is not None or entry.sealed:
                        raise ResourceAdmissionError("training_child_failed")
                    binding = parse_authorize_request(document)
                    provenance = entry.authority.authorize(binding)
                    if provenance.run_id != entry.child.ticket.run_id:
                        raise ResourceAdmissionError("training_execution_stale")
                    response: dict[str, object] = {
                        "version": 1,
                        "provenance": provenance.document(),
                    }
                elif tag == b"C":
                    if entry.authorized is None:
                        raise ResourceAdmissionError("training_child_failed")
                    parse_check_request(document)
                    entry.authority.check_running()
                    response = {"version": 1}
                elif tag == b"S":
                    provenance, manifest = parse_seal_request(document)
                    if entry.authorized != provenance or entry.sealed:
                        raise ResourceAdmissionError("training_child_failed")
                    entry.authority.seal_checkpoint(provenance, manifest)
                    response = {"version": 1}
                else:
                    raise ResourceAdmissionError("training_child_failed")
                status = self._repository.renew(entry.child.ticket, identity)
            except BaseException:
                entry.child.deadline.finish_renewal(sequence, succeeded=False)
                raise
            self._accept(entry, sequence, status, identity)
            if tag == b"A":
                entry.authorized = provenance
                entry.binding = binding
            elif tag == b"S":
                entry.sealed = True
                entry.sealed_manifest_sha256 = manifest
            return response

    def _work(self, entry: _Retained) -> None:
        entry.launch_gate.wait()
        if not entry.launch_permitted:
            return
        child = entry.child
        success = False
        detached = False
        try:
            prepared = self._repository.prepare(child.ticket)
            if prepared.ticket != child.ticket or prepared.state != ExecutionState.PREPARED:
                entry.error = ResourceAdmissionError("training_execution_stale")
                child.seal_without_spawn()
                child.close_pipes_after_worker_exit()
                child.close_tree_after_acknowledgement()
                detached = True
                self._forget(entry)
                return
            entry.prepared_owned = True
            identity = child.spawn()
            entry.identity = identity
            sequence = child.deadline.begin_renewal()
            try:
                running = self._repository.start(child.ticket, identity)
            except BaseException:
                child.deadline.finish_renewal(sequence, succeeded=False)
                raise
            self._accept(entry, sequence, running, identity)
            entry.registered = True
            entry.checkpoint_reservation = reserve_checkpoint_output(
                entry.command,
                entry.input_inventory,
            )
            sequence = child.deadline.begin_renewal()
            try:
                bound = self._repository.bind_checkpoint(
                    child.ticket,
                    identity,
                    device=str(entry.checkpoint_reservation.device),
                    inode=str(entry.checkpoint_reservation.inode),
                )
            except BaseException:
                child.deadline.finish_renewal(sequence, succeeded=False)
                raise
            self._accept(entry, sequence, bound, identity)
            entry.checkpoint_bound = True
            child.allow_go(bound)
            threading.Thread(
                target=self._renew,
                args=(entry, identity),
                name="training-renewal",
                daemon=True,
            ).start()
            child.go(entry.command.document())
            tag, document = child.training_exchange(
                lambda request_tag, request: self._handle(entry, identity, request_tag, request)
            )
            if tag != b"R":
                raise ResourceAdmissionError("training_child_failed")
            entry.result = SonaControlledTrainingResult.parse(document)
            if (
                entry.authorized is None
                or entry.binding is None
                or not entry.sealed
                or entry.sealed_manifest_sha256 is None
                or entry.result.dataset_manifest_sha256 != entry.binding.dataset_sha256
                or entry.result.checkpoint_manifest_sha256 != entry.sealed_manifest_sha256
                or (
                    entry.command.publication_operation_id is None
                    and entry.result.publication_seal_sha256 is not None
                )
                or (
                    entry.command.publication_operation_id is not None
                    and entry.result.publication_seal_sha256 is None
                )
            ):
                raise ResourceAdmissionError("training_child_failed")
            entry.freeze_requested.set()
            with entry.renew_lock:
                child.deadline.check()
            while (proof := child.exit_evidence(identity)) is None:
                child.deadline.check()
                sleep(0.01)
            if proof.exit_code != 0:
                raise ResourceAdmissionError("training_child_failed")
            success = True
            entry.retain_checkpoint = True
        except BaseException as error:
            entry.error = (
                ResourceAdmissionError("training_execution_unavailable")
                if isinstance(error, SQLAlchemyError)
                else error
            )
        finally:
            if not detached:
                entry.freeze_requested.set()
                if not success:
                    child.deadline.freeze_error()
                    child.seal_without_spawn()
                    child.request_stop()
                self._drain(entry)

    def _drain(self, entry: _Retained) -> None:
        child = entry.child
        while True:
            if entry.renew_lock.acquire(blocking=False):
                try:
                    if not entry.prepared_owned:
                        proof = child.exit_evidence(entry.identity)
                        if proof is not None:
                            entry.io_done.set()
                            child.close_pipes_after_worker_exit()
                            child.close_tree_after_acknowledgement()
                            self._forget(entry)
                            return
                        continue
                    status = self._repository.reconcile(child.ticket)
                    if not entry.registered:
                        if (
                            status is not None
                            and status.child == entry.identity
                            and status.state in {ExecutionState.RUNNING, ExecutionState.CLOSED}
                        ):
                            entry.registered = True
                        elif status is not None and status.state == ExecutionState.PREPARED:
                            proof = child.exit_evidence(None)
                            if proof is not None:
                                cleanup = self._repository.begin_cleanup(
                                    child.ticket,
                                    proof,
                                    retain_checkpoint=False,
                                    checkpoint_manifest_sha256=None,
                                )
                                if cleanup.state != ExecutionState.STOPPING:
                                    raise ResourceAdmissionError("training_execution_unconfirmed")
                                entry.cleanup_registered = True
                                if entry.input_cleanup_sha256 is None:
                                    entry.input_cleanup_sha256 = finalize_training_input(
                                        entry.command,
                                        entry.input_inventory,
                                        child.ticket,
                                    )
                                acknowledged = self._repository.confirm(
                                    child.ticket,
                                    entry.input_cleanup_sha256,
                                )
                                if (
                                    acknowledged.ticket != child.ticket
                                    or acknowledged.state != ExecutionState.CLOSED
                                    or acknowledged.child is not None
                                ):
                                    raise ResourceAdmissionError("training_execution_unconfirmed")
                                entry.io_done.set()
                                child.close_pipes_after_worker_exit()
                                child.close_tree_after_acknowledgement()
                                self._forget(entry)
                                return
                            continue
                        else:
                            proof = child.exit_evidence(entry.identity)
                            if proof is not None:
                                entry.io_done.set()
                                child.close_pipes_after_worker_exit()
                                child.close_tree_after_acknowledgement()
                                self._forget(entry)
                                return
                            continue
                    proof = child.exit_evidence(None if status is None else status.child)
                    if proof is not None:
                        entry.io_done.set()
                        if status is not None and status.state == ExecutionState.CLOSED:
                            self._reconcile_closed_result(entry)
                            child.close_pipes_after_worker_exit()
                            child.close_tree_after_acknowledgement()
                            self._forget(entry)
                            return
                        if status is not None and not entry.cleanup_registered:
                            result = entry.result if entry.retain_checkpoint else None
                            cleanup = self._repository.begin_cleanup(
                                child.ticket,
                                proof,
                                retain_checkpoint=(None if result is not None else False),
                                checkpoint_manifest_sha256=(
                                    result.checkpoint_manifest_sha256
                                    if result is not None
                                    else None
                                ),
                                checkpoint_weights_sha256=(
                                    result.weights_sha256 if result is not None else None
                                ),
                                checkpoint_optimizer_steps=(
                                    result.optimizer_steps if result is not None else None
                                ),
                                checkpoint_device_type=(
                                    result.device_type if result is not None else None
                                ),
                                publication_seal_sha256=(
                                    result.publication_seal_sha256 if result is not None else None
                                ),
                            )
                            if cleanup.state not in {
                                ExecutionState.STOPPING,
                                ExecutionState.CLOSED,
                            }:
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                            entry.cleanup_registered = True
                        if (
                            entry.checkpoint_reservation is not None
                            and entry.retain_checkpoint
                            and not entry.output_finalized
                        ):
                            try:
                                verified = finalize_checkpoint_output(
                                    entry.command,
                                    entry.checkpoint_reservation,
                                    retain=True,
                                    manifest_sha256=entry.sealed_manifest_sha256,
                                    publication_seal_sha256=(
                                        None
                                        if entry.result is None
                                        else entry.result.publication_seal_sha256
                                    ),
                                )
                                if (
                                    verified is None
                                    or entry.result is None
                                    or verified.weights_sha256 != entry.result.weights_sha256
                                    or verified.optimizer_steps != entry.result.optimizer_steps
                                    or verified.device_type != entry.result.device_type
                                ):
                                    raise ValueError(
                                        "controlled training result does not match checkpoint"
                                    )
                            except ValueError:
                                entry.retain_checkpoint = False
                                entry.result = None
                                entry.error = ResourceAdmissionError("training_child_failed")
                            plan = self._repository.decide_checkpoint(
                                child.ticket,
                                retain_checkpoint=entry.retain_checkpoint,
                            )
                            if plan.retain_checkpoint != entry.retain_checkpoint:
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                            if entry.retain_checkpoint:
                                entry.output_finalized = True
                        if (
                            entry.output_finalized
                            and entry.retain_checkpoint
                            and entry.result is not None
                            and entry.before_cleanup is not None
                            and not entry.publication_attempted
                        ):
                            entry.publication_attempted = True
                            try:
                                entry.before_cleanup(
                                    entry.command.checkpoint_path,
                                    entry.result,
                                )
                            except BaseException as error:
                                entry.error = error
                        elif (
                            status is not None
                            and entry.cleanup_registered
                            and not entry.retain_checkpoint
                        ):
                            plan = self._repository.decide_checkpoint(
                                child.ticket,
                                retain_checkpoint=False,
                            )
                            if plan.retain_checkpoint is not False:
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                        if entry.checkpoint_reservation is not None and not entry.output_finalized:
                            finalize_checkpoint_output(
                                entry.command,
                                entry.checkpoint_reservation,
                                retain=False,
                                manifest_sha256=None,
                            )
                            entry.output_finalized = True
                            if entry.input_cleanup_sha256 is None:
                                entry.input_cleanup_sha256 = finalize_training_input(
                                    entry.command,
                                    entry.input_inventory,
                                    child.ticket,
                                )
                        if entry.registered and entry.input_cleanup_sha256 is None:
                            entry.input_cleanup_sha256 = finalize_training_input(
                                entry.command,
                                entry.input_inventory,
                                child.ticket,
                            )
                        if status is None:
                            if proof.kind != ExitKind.NOT_STARTED:
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                        else:
                            if entry.input_cleanup_sha256 is None:
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                            acknowledged = self._repository.confirm(
                                child.ticket,
                                entry.input_cleanup_sha256,
                            )
                            if (
                                acknowledged.ticket != child.ticket
                                or acknowledged.state != ExecutionState.CLOSED
                                or acknowledged.child != status.child
                            ):
                                raise ResourceAdmissionError("training_execution_unconfirmed")
                        child.close_pipes_after_worker_exit()
                        child.close_tree_after_acknowledgement()
                        self._forget(entry)
                        return
                except SQLAlchemyError, OSError, ResourceAdmissionError, ValueError:
                    pass
                finally:
                    entry.renew_lock.release()
            sleep(0.1)

    def _reconcile_closed_result(self, entry: _Retained) -> None:
        """Return success only for the exact checkpoint decision persisted by recovery."""

        plan = self._repository.cleanup_plan(entry.child.ticket)
        result, reservation = entry.result, entry.checkpoint_reservation
        accepted = (
            plan is not None
            and plan.retain_checkpoint is True
            and result is not None
            and reservation is not None
            and plan.checkpoint_device == str(reservation.device)
            and plan.checkpoint_inode == str(reservation.inode)
            and plan.checkpoint_manifest_sha256 == result.checkpoint_manifest_sha256
            and plan.checkpoint_weights_sha256 == result.weights_sha256
            and plan.checkpoint_optimizer_steps == result.optimizer_steps
            and plan.checkpoint_device_type == result.device_type
        )
        if accepted and plan is not None and result is not None:
            try:
                self._storage_scopes.validate(plan.ticket)
                verified = finalize_checkpoint_cleanup(plan)
                accepted = (
                    verified is not None
                    and verified.weights_sha256 == result.weights_sha256
                    and verified.optimizer_steps == result.optimizer_steps
                    and verified.device_type == result.device_type
                )
            except OSError, ValueError:
                accepted = False
        if not accepted:
            entry.result = None
            if entry.error is None:
                entry.error = ResourceAdmissionError("training_child_failed")

    def _forget(self, entry: _Retained) -> None:
        with self._lock:
            if self._entries.get(entry.child.ticket.execution_id) is entry:
                del self._entries[entry.child.ticket.execution_id]
        entry.io_done.set()
        entry.done.set()
        if self._on_drained is not None:
            self._on_drained()


__all__ = (
    "SonaTrainingCleanupRecovery",
    "SonaTrainingProcessCoordinator",
    "SonaTrainingStorageScopes",
    "TrainingExecutionRepository",
)
