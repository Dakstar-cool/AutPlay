"""Queue one Vault display-name pass and publish each revision using the normal sync path."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.models import JobRow, UserAccountRow
from autplay.application.job_worker import JobExecutionContext
from autplay.application.vault_names import refresh_owner, refresh_recording, targets
from autplay.domain.auth import AccountRole
from autplay.domain.jobs import JobKey, JobLease, RetryableJobError, TerminalJobError
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.ports.jobs import EnqueueJob

NORMALIZE_NAMES = JobKey("vault.normalize_names", 1)
_ACTIVE = ("QUEUED", "RUNNING", "RETRY_WAIT", "PAUSED")


class VaultNameJobs:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def request(self, actor: WebActor, operation: UUID) -> None:
        if actor.role != AccountRole.OWNER:
            raise WebAdminError("forbidden")
        scope = f"vault.normalize_names:{actor.user_id}"
        with self.sessions.begin() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended('autplay.enqueue-vault-names',0))"
                )
            )
            jobs = select(JobRow.job_id).where(JobRow.job_type == NORMALIZE_NAMES.job_type)
            if session.scalar(
                jobs.where(
                    JobRow.idempotency_scope == scope, JobRow.idempotency_key == str(operation)
                )
            ):
                return
            if session.scalar(jobs.where(JobRow.state.in_(_ACTIVE)).limit(1)):
                raise WebAdminError("normalization_active")
            PostgresJobRepository(session).enqueue(
                EnqueueJob(
                    key=NORMALIZE_NAMES,
                    user_id=actor.user_id,
                    priority=4,
                    payload={},
                    idempotency_scope=scope,
                    idempotency_key=str(operation),
                )
            )

    def latest(self) -> dict[str, object] | None:
        with self.sessions() as session:
            row = (
                session.execute(
                    select(JobRow.state, JobRow.progress_current, JobRow.progress_total)
                    .where(JobRow.job_type == NORMALIZE_NAMES.job_type)
                    .order_by(JobRow.created_at.desc(), JobRow.job_id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def run(self, context: JobExecutionContext, lease: JobLease) -> None:
        try:
            with self.sessions.begin() as scan:
                account = scan.get(UserAccountRow, lease.user_id) if lease.user_id else None
                if not account or account.role != AccountRole.OWNER or account.status != "ACTIVE":
                    raise TerminalJobError("vault.normalization_forbidden")
                # The standalone backfill takes the same lock in its database connection.
                if not scan.scalar(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "hashtextextended('autplay.vault-display-v1',0))"
                    )
                ):
                    raise RetryableJobError("vault.normalization_busy")
                pending = []
                total = 0
                for kind, target, owner, status in targets(scan):
                    context.raise_if_cancelled()
                    total += 1
                    if status == "pending":
                        pending.append((kind, target, owner))
                checked = total - len(pending)
                context.checkpoint({}, progress_current=checked, progress_total=total)
                for kind, target, owner in pending:
                    context.raise_if_cancelled()
                    with self.sessions.begin() as session:
                        session.execute(text("SET LOCAL lock_timeout='1500ms'"))
                        if kind == "recording":
                            refresh_recording(session, UUID(target), True)
                        else:
                            refresh_owner(session, UUID(target), UUID(owner), True)
                    checked += 1
                    if checked % 25 == 0 or checked == total:
                        context.checkpoint({}, progress_current=checked, progress_total=total)
        except SQLAlchemyError as error:
            raise RetryableJobError("vault.normalization_database") from error
