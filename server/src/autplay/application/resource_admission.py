"""Idempotent admission and short-lived I/O fences for authenticated device operations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from autplay.domain.auth import Principal
from autplay.domain.resource_admission import (
    LEASE_TTL,
    MAX_WAITING_PER_ACCOUNT,
    WAITING_TTL,
    ActivationFence,
    AdmissionState,
    AdmissionStatus,
    IoPermit,
    ResourceAdmission,
    ResourceAdmissionError,
    ResourceKind,
    ResourceRequest,
)
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionState,
    ExecutionStatus,
    ExecutionTicket,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.ports.resource_admission import (
    ResourceAdmissionRepository,
    ResourceAdmissionUnitOfWorkFactory,
)


class ResourceAdmissionService:
    def __init__(self, units: ResourceAdmissionUnitOfWorkFactory) -> None:
        self._units = units

    def acquire(self, actor: Principal, request: ResourceRequest) -> AdmissionStatus:
        if request.resource_type not in {"PLAY_INSTANCE", "DOWNLOAD_INTENT", "UPLOAD_INTENT"}:
            raise ResourceAdmissionError("resource_request_invalid")
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            authority = repo.authenticate(actor, now)
            repo.server_limit(request.kind)
            digest = request.digest(authority)
            operation = repo.find(request.operation_id)
            if operation is not None:
                if operation.request_sha256 != digest:
                    raise ResourceAdmissionError("resource_operation_conflict")
                operation.expire(now)
                if operation.state == AdmissionState.RELEASED:
                    return repo.status(operation, now)
            repo.require_target(authority, request, now)
            if operation is None or operation.state == AdmissionState.EXPIRED:
                if repo.waiting_count(actor.user_id, request.kind, now) >= MAX_WAITING_PER_ACCOUNT:
                    raise ResourceAdmissionError("resource_queue_full")
                if operation is None:
                    operation = ResourceAdmission(
                        authority,
                        request,
                        digest,
                        AdmissionState.WAITING,
                        None,
                        0,
                        now,
                        now,
                        now,
                    )
                # An old activation may still be draining. The scheduler waits for its permits.
                operation.state, operation.terminal_at = AdmissionState.WAITING, None
                operation.enqueued_at = now
                operation.current_recording_id = operation.next_recording_id = None
            if operation.state == AdmissionState.WAITING:
                operation.waiting_until, operation.updated_at = now + WAITING_TTL, now
            repo.save(operation)
            repo.advance(now)
            result = repo.status(self._found(repo, request.operation_id), now)
            unit.commit()
            return result

    def poll(self, actor: Principal, operation_id: UUID) -> AdmissionStatus:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, operation_id, now)
            operation.expire(now)
            if operation.state == AdmissionState.WAITING:
                operation.waiting_until, operation.updated_at = now + WAITING_TTL, now
            repo.save(operation)
            repo.advance(now)
            result = repo.status(self._found(repo, operation_id), now)
            unit.commit()
            return result

    def renew(self, actor: Principal, fence: ActivationFence) -> AdmissionStatus:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, fence.operation_id, now)
            self._active(operation, fence, now)
            repo.require_target(operation.authority, operation.request, now)
            operation.claimed_at = operation.claimed_at or now
            operation.lease_until, operation.updated_at = now + LEASE_TTL, now
            repo.save(operation)
            repo.advance(now)
            result = repo.status(operation, now)
            unit.commit()
            return result

    def release(self, actor: Principal, fence: ActivationFence) -> AdmissionStatus:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, fence.operation_id, now)
            if operation.fence != fence or operation.state == AdmissionState.WAITING:
                raise ResourceAdmissionError("resource_activation_stale")
            operation.state = AdmissionState.RELEASED
            operation.terminal_at = operation.terminal_at or now
            operation.updated_at = now
            repo.save(operation)
            repo.advance(now)
            result = repo.status(operation, now)
            unit.commit()
            return result

    def cancel_waiting(
        self, actor: Principal, operation_id: UUID, digest: bytes
    ) -> AdmissionStatus:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, operation_id, now)
            if operation.request_sha256 != digest:
                raise ResourceAdmissionError("resource_operation_conflict")
            if operation.state not in {AdmissionState.WAITING, AdmissionState.RELEASED}:
                raise ResourceAdmissionError("resource_activation_stale")
            operation.state = AdmissionState.RELEASED
            operation.terminal_at = operation.terminal_at or now
            operation.updated_at = now
            repo.save(operation)
            repo.advance(now)
            result = repo.status(operation, now)
            unit.commit()
            return result

    def attach(
        self,
        actor: Principal,
        fence: ActivationFence,
        expected_revision: int,
        current_recording_id: UUID,
        next_recording_id: UUID | None,
    ) -> AdmissionStatus:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, fence.operation_id, now)
            self._active(operation, fence, now)
            if operation.request.kind != ResourceKind.PLAYBACK:
                raise ResourceAdmissionError("resource_purpose_mismatch")
            requested = (current_recording_id, next_recording_id)
            current = (operation.current_recording_id, operation.next_recording_id)
            if type(expected_revision) is not int or expected_revision < 0:
                raise ResourceAdmissionError("resource_revision_stale")
            if operation.attachment_revision != expected_revision:
                if operation.attachment_revision == expected_revision + 1 and requested == current:
                    return repo.status(operation, now)
                raise ResourceAdmissionError("resource_revision_stale")
            attached = {value for value in requested if value is not None}
            for recording in attached:
                repo.require_recording(operation.authority, recording)
            draining = {permit.target_id for permit in repo.permits(fence.operation_id, now)}
            if len(attached | draining) > 2:
                raise ResourceAdmissionError("resource_attachment_draining")
            operation.current_recording_id, operation.next_recording_id = requested
            operation.attachment_revision += 1
            operation.updated_at = now
            repo.save(operation)
            result = repo.status(operation, now)
            unit.commit()
            return result

    def open_io(
        self,
        actor: Principal,
        fence: ActivationFence,
        target_id: UUID,
        *,
        resource_type: str | None = None,
    ) -> IoPermit:
        """HTTP adapters pass the declared type and the actual target from their URL.

        For PLAY_INSTANCE the HTTP target is an audio variant; only the repository
        may map it to the authorized recording attachment. Omitted type is reserved
        for internal callers that already hold a domain recording/transfer target.
        """
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, fence.operation_id, now)
            self._active(operation, fence, now)
            if resource_type is not None:
                target_id = self._device_io_target(repo, operation, resource_type, target_id)
            self._target(repo, operation, target_id, now)
            maximum = 3 if operation.request.kind == ResourceKind.PLAYBACK else 1
            if len(repo.permits(fence.operation_id, now)) >= maximum:
                raise ResourceAdmissionError("resource_io_busy")
            operation.claimed_at = operation.claimed_at or now
            operation.lease_until, operation.updated_at = now + LEASE_TTL, now
            repo.save(operation)
            permit = repo.open_permit(operation, target_id, now)
            unit.commit()
            return permit

    def renew_io(
        self,
        actor: Principal,
        permit: IoPermit,
        *,
        resource_type: str | None = None,
        target_id: UUID | None = None,
    ) -> IoPermit:
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            operation = self._owned(repo, actor, permit.fence.operation_id, now)
            self._active(operation, permit.fence, now)
            if resource_type is not None and (
                target_id is None
                or self._device_io_target(repo, operation, resource_type, target_id)
                != permit.target_id
            ):
                raise ResourceAdmissionError("resource_target_mismatch")
            # Removed playback attachments may drain, but cannot extend their I/O deadline.
            self._target(repo, operation, permit.target_id, now)
            result = repo.renew_permit(permit, now)
            unit.commit()
            return result

    @staticmethod
    def _device_io_target(
        repo: ResourceAdmissionRepository,
        operation: ResourceAdmission,
        resource_type: str,
        target_id: UUID,
    ) -> UUID:
        if (
            resource_type not in {"PLAY_INSTANCE", "DOWNLOAD_INTENT", "UPLOAD_INTENT"}
            or operation.request.resource_type != resource_type
        ):
            raise ResourceAdmissionError("resource_purpose_mismatch")
        if resource_type == "PLAY_INSTANCE":
            return repo.stream_recording(operation.authority, target_id)
        return target_id

    def close_io(self, permit: IoPermit) -> None:
        """Internal adapter acknowledgement; random permit ID is never an HTTP capability."""
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            repo.close_permit(permit)
            repo.advance(now)
            unit.commit()

    def prepare_execution(self, actor: Principal, ticket: ExecutionTicket) -> ExecutionStatus:
        """Commit registration before spawning or allowing a child to touch bytes."""
        with self._units() as unit:
            now = unit.admissions.lock()
            self._execution_authority(unit.admissions, actor, ticket, now)
            result = unit.executions.prepare(ticket, now)
            unit.commit()
            return result

    def start_execution(
        self, actor: Principal, ticket: ExecutionTicket, child: ProcessIdentity
    ) -> ExecutionStatus:
        """Register the spawned, still waiting child before the adapter may send GO.

        The adapter must also enforce its monotonic deadline after this transaction;
        a late database response cannot authorize starting an expired execution.
        """
        with self._units() as unit:
            now = unit.admissions.lock()
            self._execution_authority(unit.admissions, actor, ticket, now)
            result = unit.executions.started(ticket, child, now)
            unit.commit()
            return result

    @classmethod
    def _execution_authority(
        cls,
        repo: ResourceAdmissionRepository,
        actor: Principal,
        ticket: ExecutionTicket,
        now: datetime,
    ) -> None:
        operation = cls._owned(repo, actor, ticket.permit.fence.operation_id, now)
        cls._active(operation, ticket.permit.fence, now)
        expected = {
            "PLAY_INSTANCE": ExecutionKind.VAULT_STREAM,
            "DOWNLOAD_INTENT": ExecutionKind.VAULT_STREAM,
            "UPLOAD_INTENT": ExecutionKind.VAULT_UPLOAD,
        }.get(operation.request.resource_type)
        if expected != ticket.kind:
            raise ResourceAdmissionError("resource_purpose_mismatch")
        target = cls._device_io_target(
            repo,
            operation,
            operation.request.resource_type,
            ticket.actual_target_id,
        )
        if target != ticket.permit.target_id:
            raise ResourceAdmissionError("resource_target_mismatch")
        cls._target(repo, operation, target, now)

    def inspect_execution(self, ticket: ExecutionTicket) -> ExecutionStatus:
        """Trusted local reconciliation only; no client-accessible execution endpoint."""
        with self._units() as unit:
            unit.admissions.lock()
            return unit.executions.inspect(ticket)

    def renew_execution_io(self, actor: Principal, ticket: ExecutionTicket) -> IoPermit:
        """Renew permission and owner heartbeat atomically for one exact execution.

        Callers still anchor their local deadline at the start of this RPC. This
        does not renew the device's logical operation lease or resume an orphan.
        """
        with self._units() as unit:
            now = unit.admissions.lock()
            self._execution_authority(unit.admissions, actor, ticket, now)
            status = unit.executions.inspect(ticket)
            if status.state not in {ExecutionState.PREPARED, ExecutionState.RUNNING}:
                raise ResourceAdmissionError("resource_io_stale")
            result = unit.admissions.renew_permit(ticket.permit, now)
            unit.executions.heartbeat(ticket, now)
            unit.commit()
            return result

    def heartbeat_execution(self, ticket: ExecutionTicket) -> ExecutionStatus:
        """Diagnostic liveness only. This does not renew any I/O permission."""
        with self._units() as unit:
            now = unit.admissions.lock()
            result = unit.executions.heartbeat(ticket, now)
            unit.commit()
            return result

    def stop_execution(self, ticket: ExecutionTicket) -> ExecutionStatus:
        with self._units() as unit:
            now = unit.admissions.lock()
            result = unit.executions.stop(ticket, now)
            unit.commit()
            return result

    def confirm_execution_exit(
        self,
        ticket: ExecutionTicket,
        proof: ProcessExitEvidence,
    ) -> ExecutionStatus:
        """Called only after verified exit, even if original user authority was revoked.

        Retains the closed receipt until close_io/cleanup removes its permit. The
        adapter may retry this exact acknowledgement after an uncertain commit.
        ABSENT explicitly reports missing accounting after cleanup, without any
        historical exit evidence. It never substitutes for the adapter's own proof.
        """
        with self._units() as unit:
            now = unit.admissions.lock()
            result = unit.executions.confirm_exit(ticket, proof, now)
            unit.commit()
            return result

    def sweep(self, maximum: int = 128) -> int:
        if type(maximum) is not int or not 1 <= maximum <= 512:
            raise ValueError("invalid admission sweep bound")
        with self._units() as unit:
            repo = unit.admissions
            now = repo.lock()
            changed = (
                unit.executions.orphan_stale(now, maximum)
                + repo.cleanup(now, maximum)
                + repo.advance(now, maximum)
            )
            unit.commit()
            return changed

    @staticmethod
    def _found(repo: ResourceAdmissionRepository, operation_id: UUID) -> ResourceAdmission:
        operation = repo.find(operation_id)
        if operation is None:
            raise ResourceAdmissionError()
        return operation

    @classmethod
    def _owned(
        cls,
        repo: ResourceAdmissionRepository,
        actor: Principal,
        operation_id: UUID,
        now: datetime,
    ) -> ResourceAdmission:
        authority = repo.authenticate(actor, now)
        operation = cls._found(repo, operation_id)
        if operation.authority != authority:
            raise ResourceAdmissionError()
        return operation

    @staticmethod
    def _active(
        operation: ResourceAdmission,
        fence: ActivationFence,
        now: datetime,
    ) -> None:
        if operation.fence != fence or not operation.active_at(now):
            raise ResourceAdmissionError("resource_activation_stale")

    @staticmethod
    def _target(
        repo: ResourceAdmissionRepository,
        operation: ResourceAdmission,
        target_id: UUID,
        now: datetime,
    ) -> None:
        if operation.request.kind == ResourceKind.PLAYBACK:
            if target_id not in {operation.current_recording_id, operation.next_recording_id}:
                raise ResourceAdmissionError("resource_target_mismatch")
            repo.require_recording(operation.authority, target_id)
        else:
            if target_id != operation.request.target_id:
                raise ResourceAdmissionError("resource_target_mismatch")
            repo.require_target(operation.authority, operation.request, now)
