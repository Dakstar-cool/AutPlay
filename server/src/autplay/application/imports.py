"""P10 application services for user imports, review, and durable execution."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from autplay.adapters.postgresql.import_runtime import (
    ImportJobReport,
    ImportReviewResult,
    ImportStartResult,
    PostgresImportRepository,
)
from autplay.application.job_worker import JobExecutionContext
from autplay.application.source_adapters import GenericUserExportSourceAdapter
from autplay.domain.auth import Principal
from autplay.domain.jobs import CancelRequestResult, JobLease, JsonValue
from autplay.ports.source_adapters import UserExportAdapter


class ImportService:
    """Own short transactions around the P10 PostgreSQL import adapter."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        parser: UserExportAdapter | None = None,
    ) -> None:
        self._sessions = sessions
        self._parser = parser or GenericUserExportSourceAdapter()

    def start(
        self,
        principal: Principal,
        *,
        payload: bytes,
        format_name: str,
        schema_version: str,
        mode: str,
    ) -> ImportStartResult:
        """Parse before side effects, then atomically persist and enqueue every row."""

        parsed = self._parser.parse(
            payload,
            format_name=format_name,
            schema_version=schema_version,
        )
        with self._sessions() as session:
            result = PostgresImportRepository(session).start(
                principal=principal,
                parsed=parsed,
                mode=mode,
            )
            session.commit()
            return result

    def report(
        self,
        principal: Principal,
        import_job_id: UUID,
        *,
        limit: int = 200,
        after: str | None = None,
    ) -> ImportJobReport:
        """Read a redacted, owner-scoped report page."""

        with self._sessions() as session:
            return PostgresImportRepository(session).report(
                owner_user_id=principal.user_id,
                import_job_id=import_job_id,
                limit=limit,
                after=after,
            )

    def cancel(self, principal: Principal, import_job_id: UUID) -> CancelRequestResult:
        """Request cancellation and commit its durable safe-point marker."""

        with self._sessions() as session:
            result = PostgresImportRepository(session).cancel(
                owner_user_id=principal.user_id,
                import_job_id=import_job_id,
            )
            session.commit()
            return result

    def resume(self, principal: Principal, import_job_id: UUID) -> ImportStartResult:
        """Resume from the import checkpoint through a new fenced job delivery."""

        with self._sessions() as session:
            result = PostgresImportRepository(session).resume(
                owner_user_id=principal.user_id,
                import_job_id=import_job_id,
            )
            session.commit()
            return result

    def review(
        self,
        principal: Principal,
        import_job_id: UUID,
        import_entry_id: UUID,
        *,
        predecessor_decision_id: UUID,
        action: str,
        selected_rank: int | None,
        idempotency_key: str,
    ) -> ImportReviewResult:
        """Apply one explicit owner decision without resolving its UserTrackRef lineage."""

        with self._sessions() as session:
            result = PostgresImportRepository(session).review(
                principal=principal,
                import_job_id=import_job_id,
                import_entry_id=import_entry_id,
                predecessor_decision_id=predecessor_decision_id,
                action=action,
                selected_rank=selected_rank,
                idempotency_key=idempotency_key,
            )
            session.commit()
            return result


class ImportJobHandler:
    """Process one row per transaction and checkpoint after every safe boundary."""

    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    def __call__(self, context: JobExecutionContext, lease: JobLease) -> None:
        """Resume from PostgreSQL until every row has a terminal/shadow decision."""

        import_job_id = _import_job_id(lease.payload)
        with self._sessions() as session:
            PostgresImportRepository(session).ensure_matcher_release()
            session.commit()
        while True:
            context.raise_if_cancelled()
            with self._sessions() as session:
                progress = PostgresImportRepository(session).process_next(
                    import_job_id=import_job_id
                )
                session.commit()
            checkpoint: dict[str, JsonValue] = {
                "schema_version": 1,
                "import_job_id": str(import_job_id),
                "last_source_row_key": progress.source_row_key or "",
                "processed_rows": progress.processed_rows,
            }
            context.checkpoint(
                checkpoint,
                progress_current=progress.processed_rows,
                progress_total=progress.total_rows,
            )
            if progress.finished:
                return


def _import_job_id(payload: dict[str, JsonValue]) -> UUID:
    value = payload.get("import_job_id")
    if not isinstance(value, str):
        raise ValueError("import job payload is missing import_job_id")
    try:
        return UUID(value)
    except ValueError as error:
        raise ValueError("import job payload has invalid import_job_id") from error


__all__ = (
    "ImportJobHandler",
    "ImportService",
)
