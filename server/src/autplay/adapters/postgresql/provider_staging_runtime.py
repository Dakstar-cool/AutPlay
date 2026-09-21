"""Stage registration and exit receipts share the existing admission transaction."""

from datetime import datetime

from sqlalchemy.orm import Session

from autplay.domain.resource_admission import ResourceAdmissionError

from .models.provider_staging import ProviderStagingRow
from .models.resource_admission import ResourceAdmissionRow, ResourceIoExecutionRow


def register_provider_staging(session: Session, execution: ResourceIoExecutionRow) -> None:
    """Called before a newly prepared provider execution can be given child GO."""
    if execution.kind != "PROVIDER":
        return
    operation = session.get(ResourceAdmissionRow, execution.operation_id)
    if (
        operation is None
        or operation.resource_type not in {"INTERNET_ACQUISITION", "DISCOVERY_ACQUISITION"}
        or operation.kind != "TRANSFER"
        or operation.job_id is None
        or operation.job_worker_id is None
        or operation.job_attempt is None
        or operation.resource_id != execution.actual_target_id
        or operation.target_id != execution.actual_target_id
        or operation.activation_id != execution.activation_id
        or operation.generation != execution.generation
    ):
        raise ResourceAdmissionError("resource_execution_stale")
    session.add(
        ProviderStagingRow(
            execution_id=execution.execution_id,
            user_id=operation.user_id,
            resource_type=operation.resource_type,
            acquisition_id=execution.actual_target_id,
            job_id=operation.job_id,
            job_worker_id=operation.job_worker_id,
            job_attempt=operation.job_attempt,
            operation_id=execution.operation_id,
            activation_id=execution.activation_id,
            generation=execution.generation,
            permit_id=execution.permit_id,
            owner_run_id=execution.owner_run_id,
            staging_key=f"provider-{execution.execution_id.hex}",
            state="OWNED",
            created_at=execution.created_at,
            updated_at=execution.created_at,
        )
    )


def preserve_provider_exit(
    session: Session, execution: ResourceIoExecutionRow, now: datetime
) -> None:
    """Copy closure atomically before accounting can be removed by close/sweep/rebind."""
    if execution.kind != "PROVIDER":
        return
    receipt = session.get(ProviderStagingRow, execution.execution_id, populate_existing=True)
    if receipt is None:
        # Historical executions did not own this namespace and grant no staging authority.
        return
    if receipt.state != "OWNED":
        if (
            receipt.closed_at,
            receipt.closure_kind,
            receipt.closure_evidence_sha256,
            receipt.exit_code,
            receipt.child_pid,
            receipt.child_identity_sha256,
        ) != (
            execution.closed_at,
            execution.closure_kind,
            execution.closure_evidence_sha256,
            execution.exit_code,
            execution.child_pid,
            execution.child_identity_sha256,
        ):
            raise ResourceAdmissionError("resource_execution_conflict")
        return
    receipt.closed_at = execution.closed_at
    receipt.closure_kind = execution.closure_kind
    receipt.closure_evidence_sha256 = execution.closure_evidence_sha256
    receipt.exit_code = execution.exit_code
    receipt.child_pid = execution.child_pid
    receipt.child_identity_sha256 = execution.child_identity_sha256
    receipt.state, receipt.updated_at = "EXITED", now


def require_provider_staging(session: Session, execution: ResourceIoExecutionRow) -> None:
    """Historical accounting without durable ownership can drain, but cannot receive GO."""
    if execution.kind != "PROVIDER":
        return
    receipt = session.get(ProviderStagingRow, execution.execution_id, populate_existing=True)
    if (
        receipt is None
        or receipt.state != "OWNED"
        or (
            receipt.operation_id,
            receipt.activation_id,
            receipt.generation,
            receipt.permit_id,
            receipt.owner_run_id,
            receipt.acquisition_id,
            receipt.staging_key,
        )
        != (
            execution.operation_id,
            execution.activation_id,
            execution.generation,
            execution.permit_id,
            execution.owner_run_id,
            execution.actual_target_id,
            f"provider-{execution.execution_id.hex}",
        )
    ):
        raise ResourceAdmissionError("resource_provider_staging_unavailable")
