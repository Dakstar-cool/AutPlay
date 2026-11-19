"""Administrative schedule for the shared Sona skip report."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.training_consent import SonaCollectionWindowRow
from autplay.adapters.postgresql.models.web_admin import WebTerminalReceiptRow
from autplay.adapters.postgresql.web_admin import SqlAlchemyWebAdminRepository
from autplay.domain.web_admin import WebActor, WebAdminError


@dataclass(frozen=True)
class CollectionSchedule:
    starts_at: datetime
    ends_at: datetime | None
    stopped_at: datetime | None
    revision: int
    state: str


class SonaCollectionService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, actor: WebActor) -> CollectionSchedule:
        with self._sessions.begin() as session:
            _, now = SqlAlchemyWebAdminRepository(session).require_current_admin_session(
                actor, mutation=False
            )
            row = session.get_one(SonaCollectionWindowRow, True)
            return CollectionSchedule(
                row.starts_at, row.ends_at, row.stopped_at, row.revision, row.state_at(now)
            )

    def change(
        self,
        actor: WebActor,
        operation_id: UUID,
        request_hash: bytes,
        revision: int,
        *,
        stop: bool,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            web, now = SqlAlchemyWebAdminRepository(session).require_current_admin_session(
                actor, mutation=True
            )
            row = session.get_one(SonaCollectionWindowRow, True, with_for_update=True)
            action = "web.sona_collection_stopped" if stop else "web.sona_collection_scheduled"
            receipt = session.get(WebTerminalReceiptRow, operation_id)
            if receipt is not None:
                if (
                    receipt.action != action
                    or receipt.request_sha256 != request_hash
                    or receipt.web_session_id != actor.web_session_id
                    or receipt.user_id != actor.user_id
                    or receipt.receipt_expires_at <= now
                ):
                    raise WebAdminError("sona_schedule_conflict")
                return
            if row.revision != revision:
                raise WebAdminError("sona_schedule_conflict")
            state = row.state_at(now)
            if stop:
                if state not in {"scheduled", "active"}:
                    raise WebAdminError("sona_schedule_conflict")
                row.stopped_at = now
            else:
                # Resuming after a gap must never include listening from the gap.
                earliest = row.starts_at if state == "active" else now
                start = max(starts_at or now, earliest)
                if ends_at is not None and ends_at <= max(start, now):
                    raise WebAdminError("sona_schedule_invalid")
                row.starts_at, row.ends_at, row.stopped_at = start, ends_at, None
            row.revision += 1
            session.add(
                WebTerminalReceiptRow(
                    operation_id=operation_id,
                    server_instance_id=actor.server_instance_id,
                    user_id=actor.user_id,
                    web_session_id=actor.web_session_id,
                    token_generation=actor.token_generation,
                    token_sha256=web.token_sha256,
                    action=action,
                    target_type="SERVER_INSTANCE",
                    target_id=actor.server_instance_id,
                    request_sha256=request_hash,
                    outcome="APPLIED",
                    terminal_at=now,
                    receipt_expires_at=now + timedelta(hours=24),
                )
            )
            session.add(
                AuditEventRow(
                    actor_type="ADMIN",
                    actor_user_id=actor.user_id,
                    action=action,
                    target_type="SERVER_INSTANCE",
                    target_id=actor.server_instance_id,
                    request_id=operation_id,
                    occurred_at=now,
                    metadata_sanitized={"revision": row.revision},
                )
            )
