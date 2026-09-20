"""Real PostgreSQL proof: identity, owner access, revision replay, fencing and history."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import LibraryEntryRow, SyncEventRow, UserTrackRefRow
from autplay.adapters.postgresql.models.track_metadata import (
    TrackMetadataRevisionRow,
    TrackMetadataRow,
)
from autplay.application.job_worker import JobExecutionContext, JobLeaseLost
from autplay.application.music_library import MusicError
from autplay.application.sync import _bootstrap_projections
from autplay.application.track_metadata import METADATA_JOB, TrackMetadataService
from autplay.domain.auth import Principal
from autplay.domain.track_metadata import FieldEvidence
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_music_library import owner


def setup(
    database_url: str,
) -> tuple[Engine, sessionmaker[Session], Principal, UUID, TrackMetadataService]:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    principal = owner(sessions)
    with sessions.begin() as session:
        ref = UserTrackRefRow(
            user_id=principal.user_id,
            raw_title="Original",
            raw_artist="Artist",
            resolution_status="UNRESOLVED",
        )
        session.add(ref)
        session.flush()
        session.add(
            LibraryEntryRow(
                user_id=principal.user_id,
                user_track_ref_id=ref.user_track_ref_id,
                source="IMPORT",
                availability_status="LOCAL",
            )
        )
    return engine, sessions, principal, ref.user_track_ref_id, TrackMetadataService(sessions)


def test_owner_revision_replay_immutable_history_and_identity(database_url: str) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    operation_id = uuid4()
    fields: dict[str, Any] = {"album": "Chosen", "release_date": "1998"}
    result = service.command(
        principal,
        ref_id,
        operation_id=operation_id,
        expected_revision=0,
        action="EDIT",
        fields=fields,
    )
    assert result["fields"] == fields
    assert result["provenance"]["album"]["locked"] is True
    assert (
        service.command(
            principal,
            ref_id,
            operation_id=operation_id,
            expected_revision=0,
            action="EDIT",
            fields=fields,
        )
        == result
    )
    with pytest.raises(MusicError, match="metadata_operation_conflict"):
        service.command(
            principal,
            ref_id,
            operation_id=operation_id,
            expected_revision=0,
            action="EDIT",
            fields={"album": "Different"},
        )
    with pytest.raises(MusicError, match="metadata_revision_conflict"):
        service.command(
            principal,
            ref_id,
            operation_id=uuid4(),
            expected_revision=0,
            action="EDIT",
            fields=fields,
        )
    with pytest.raises(MusicError) as foreign:
        service.get(owner(sessions), ref_id)
    assert foreign.value.status_code == 404
    with sessions() as session:
        ref = session.get(UserTrackRefRow, ref_id)
        assert ref is not None
        assert ref.raw_title == "Original" and ref.recording_id is None
        assert len(list(session.scalars(select(TrackMetadataRevisionRow)))) == 1
        event = session.scalar(select(SyncEventRow))
        assert event is not None
        event_payload = cast(dict[str, Any], event.payload)
        assert (
            event.event_type == "USER_TRACK_REF_PATCHED" and event_payload["metadata_v1"] == result
        )
        boot = _bootstrap_projections(session, principal.user_id)
        assert (
            next(item[3] for item in boot if item[0] == "USER_TRACK_REF")["metadata_v1"] == result
        )
    with pytest.raises(DBAPIError), sessions.begin() as session:
        session.execute(text("UPDATE library.track_metadata_revision SET snapshot='{}'"))
    engine.dispose()


def test_background_merge_preserves_manual_and_stale_generation_is_fenced(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    assert service.enqueue_missing() == 1
    assert service.enqueue_missing() == 0
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="metadata-test",
            supported=(METADATA_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        fence=lease.fence,
        lease_interval=timedelta(seconds=60),
    )
    view = service.get(principal, ref_id)
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=view["revision"],
        action="EDIT",
        fields={"album": "Manual", "release_date": None},
    )
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"album": "Provider", "release_date": "2001", "label": "Label"},
        evidence=FieldEvidence("MUSICBRAINZ", "release:test", datetime.now(UTC).isoformat()),
        state="READY",
    )
    after = service.get(principal, ref_id)
    assert after["fields"] == {"album": "Manual", "release_date": None, "label": "Label"}
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=after["revision"],
        action="REFRESH",
    )
    with pytest.raises(JobLeaseLost):
        service.apply_worker(ref_id, 1, context, state="NOT_FOUND")
    with sessions() as session:
        metadata = session.get(TrackMetadataRow, ref_id)
        assert metadata is not None and metadata.generation == 2
    engine.dispose()


def test_downgrade_preserves_nonempty_metadata(
    database_harness: DatabaseHarness, database_name: str, database_url: str
) -> None:
    engine, _sessions, principal, ref_id, service = setup(database_url)
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=0,
        action="EDIT",
        fields={"album": "Keep"},
    )
    with pytest.raises(DBAPIError, match="retention review"):
        database_harness.downgrade(database_name, "0031_music_library")
    assert service.get(principal, ref_id)["fields"]["album"] == "Keep"
    engine.dispose()


def test_chosen_edition_replaces_automatic_fields_and_cover_atomically(
    database_url: str,
) -> None:
    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="selection",
            supported=(METADATA_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
    context = JobExecutionContext(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
        fence=lease.fence,
        lease_interval=timedelta(seconds=60),
    )
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"album": "Old", "label": "Old label"},
        evidence=FieldEvidence("EMBEDDED", "file", datetime.now(UTC).isoformat()),
        artwork=b"old-image",
    )
    view = service.get(principal, ref_id)
    service.command(
        principal,
        ref_id,
        operation_id=uuid4(),
        expected_revision=view["revision"],
        action="EDIT",
        fields={"release_date": None},
    )
    service.apply_worker(
        ref_id,
        1,
        context,
        fields={"album": "New", "release_date": "2000"},
        evidence=FieldEvidence(
            "MUSICBRAINZ", "release", datetime.now(UTC).isoformat(), locked=True
        ),
        explicit_selection=True,
        state="READY",
    )
    selected = service.get(principal, ref_id)
    assert selected["fields"] == {"album": "New", "release_date": None}
    assert selected["artwork_sha256"] is None
    assert selected["provenance"]["album"]["locked"]
    engine.dispose()


def test_terminal_job_reconciliation_and_retry_after(database_url: str) -> None:
    from autplay.adapters.postgresql.models import JobRow
    from autplay.domain.jobs import JobError, RetryPolicy

    engine, sessions, principal, ref_id, service = setup(database_url)
    service.enqueue_missing()
    with sessions.begin() as session:
        jobs = PostgresJobRepository(session)
        lease = jobs.claim(
            worker_id="retry",
            supported=(METADATA_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
        jobs.fail_retryable(
            lease.fence, JobError("provider_busy", {"retry_after_seconds": 300}), RetryPolicy()
        )
    with sessions() as session:
        job = session.get(JobRow, lease.fence.job_id)
        assert job is not None
        assert job.scheduled_at > datetime.now(UTC) + timedelta(seconds=290)
    with sessions.begin() as session:
        session.execute(
            text("UPDATE jobs.job SET state='FAILED' WHERE job_id=:id"), {"id": lease.fence.job_id}
        )
    service.reconcile_finished(principal.user_id)
    assert service.get(principal, ref_id)["state"] == "FAILED"
    engine.dispose()


def test_interactive_refresh_runs_before_backfill(database_url: str) -> None:
    engine, sessions, _principal, _ref_id, service = setup(database_url)
    service.enqueue_missing()
    other_engine, _, other, other_ref, other_service = setup(database_url)
    other_service.command(
        other, other_ref, operation_id=uuid4(), expected_revision=0, action="REFRESH"
    )
    with sessions.begin() as session:
        lease = PostgresJobRepository(session).claim(
            worker_id="priority",
            supported=(METADATA_JOB,),
            lease_interval=timedelta(seconds=60),
            limit=1,
        )[0]
        assert lease.payload["user_track_ref_id"] == str(other_ref)
    other_engine.dispose()
    engine.dispose()


def test_http_owner_auth_validation_and_private_cache_headers(database_url: str) -> None:
    from autplay.entrypoints.metadata_http import create_metadata_router
    from autplay.runtime.http import ApiError, install_error_handlers
    from fastapi import FastAPI, Request
    from starlette.testclient import TestClient

    engine, sessions, principal, ref_id, service = setup(database_url)
    foreign = owner(sessions)

    def authenticate(request: Request) -> None:
        token = request.headers.get("Authorization")
        if token not in {"Bearer owner", "Bearer foreign"}:
            raise ApiError("unauthorized", "Authentication required.", 401)
        request.state.principal = principal if token == "Bearer owner" else foreign

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_metadata_router(service, authenticated=authenticate))
    path = f"/music/user-tracks/{ref_id}/metadata"
    body = {
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "action": "EDIT",
        "fields": {"album": "Owner album"},
    }
    with TestClient(app) as client:
        outcomes = [
            (client.get(path), 401),
            (client.get(path, headers={"Authorization": "Bearer foreign"}), 404),
            (
                client.post(
                    path,
                    headers={"Authorization": "Bearer owner"},
                    json={**body, "fields": {"release_date": "2001-02-31"}},
                ),
                422,
            ),
            (client.post(path, headers={"Authorization": "Bearer owner"}, json=body), 200),
            (client.get(path, headers={"Authorization": "Bearer owner"}), 200),
            (
                client.get(
                    f"/music/user-tracks/{ref_id}/artwork/" + "a" * 64,
                    headers={"Authorization": "Bearer foreign"},
                ),
                404,
            ),
        ]
        for response, status in outcomes:
            assert response.status_code == status
            assert "private" in response.headers["cache-control"]
            assert "no-store" in response.headers["cache-control"]
            assert response.headers["vary"] == "Authorization"
        assert outcomes[-2][0].json()["fields"] == {"album": "Owner album"}
    engine.dispose()
