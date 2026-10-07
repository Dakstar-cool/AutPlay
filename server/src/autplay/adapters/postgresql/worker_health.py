"""Publish observations without extending jobs' transaction or authority boundary."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from autplay.runtime.worker_health import WorkerSample

from .models.profile_pairing import ServerInstanceRow
from .models.worker_health import WorkerHealthRow


class PostgreSqlWorkerHealth:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions
        self._process_id = uuid4()
        self._server_id: UUID | None = None
        self._started_at: datetime | None = None

    def publish(self, sample: WorkerSample, *, running: bool = True) -> None:
        values = {
            "observed_at": func.clock_timestamp(),
            "running": running,
            "busy": sample.busy if running else False,
            "cpu_percent": sample.cpu_percent if running else None,
            "memory_bytes": sample.memory_bytes if running else None,
            "memory_limit_bytes": sample.memory_limit_bytes,
        }
        with self._sessions.begin() as session:
            if self._started_at is None:
                self._started_at = session.scalar(select(func.clock_timestamp()))
                assert self._started_at is not None
            if self._server_id is None:
                servers = tuple(
                    session.scalars(select(ServerInstanceRow.server_instance_id).limit(2))
                )
                # Personal-server identity must be unambiguous; never guess across servers.
                if len(servers) != 1 or not running:
                    return
                server_id = servers[0]
                statement = insert(WorkerHealthRow).values(
                    server_instance_id=server_id,
                    process_id=self._process_id,
                    started_at=self._started_at,
                    **values,
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[WorkerHealthRow.server_instance_id],
                        set_={
                            "process_id": self._process_id,
                            "started_at": self._started_at,
                            **values,
                        },
                        where=WorkerHealthRow.started_at <= self._started_at,
                    )
                )
            else:
                server_id = self._server_id
                session.execute(
                    update(WorkerHealthRow)
                    .where(
                        WorkerHealthRow.server_instance_id == server_id,
                        WorkerHealthRow.process_id == self._process_id,
                    )
                    .values(**values)
                )
        self._server_id = server_id
