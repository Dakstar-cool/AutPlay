"""Enqueue snapshots cannot acquire replacement account or session authority."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import autplay.application.internet_music as internet_module
import pytest
from autplay.adapters.internet_music import InternetMusicProvider
from autplay.adapters.postgresql.discovery_runtime import PostgresBulkDiscoveryRepository
from autplay.adapters.postgresql.models import AcquisitionAttemptRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.internet_music import InternetAcquisitionRow
from autplay.adapters.postgresql.resource_authority import ResourceAuthorityGate
from autplay.application.internet_music import InternetMusicService
from autplay.application.music_library import MusicError
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.resource_admission import ResourceAdmissionError, ResourceKind, ResourceRequest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .conftest import DatabaseHarness
from .test_discovery_runtime import _seed_import, _sessions, _track
from .test_resource_admission_runtime import AdmissionHarness, admission, present

__all__ = ["admission"]


class Provider(InternetMusicProvider):
    def search(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": f"candidate0{i}",
                "provider": "YouTube",
                "title": f"Track {i}",
                "artist": "Artist",
                "duration_ms": 150000,
                "rank": i + 1,
            }
            for i in range(2)
        ]


def test_search_fk_publication_and_selection_authentication_do_not_deadlock(
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = admission.actor()
    service = InternetMusicService(admission.sessions, object(), Provider())
    search = UUID(service.search(actor, "original", uuid4())["search_id"])
    has_sync, allow_flush = threading.Event(), threading.Event()
    role = threading.local()
    original = _acquire_sync_owner_publish_lock

    def ordered_lock(session: Session, owner: UUID) -> None:
        if getattr(role, "kind", None) == "search":
            original(session, owner)
            has_sync.set()
            assert allow_flush.wait(5)
        else:
            # The selector has finished fresh authentication before reaching here.
            allow_flush.set()
            original(session, owner)

    def search_work() -> dict[str, Any]:
        role.kind = "search"
        return service.search(actor, "parallel", uuid4())

    monkeypatch.setattr(internet_module, "_acquire_sync_owner_publish_lock", ordered_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            publisher = pool.submit(search_work)
            assert has_sync.wait(5)
            selector = pool.submit(service.select, actor, search, "candidate00")
            assert publisher.result(timeout=5)["search_id"]
            assert selector.result(timeout=5)["acquisition_id"]
    finally:
        allow_flush.set()


@pytest.mark.parametrize("legacy", [False, True])
def test_internet_selection_snapshots_exact_authority_and_rejects_revoked_replay(
    admission: AdmissionHarness, legacy: bool
) -> None:
    actor = admission.actor(legacy=legacy)
    service = InternetMusicService(admission.sessions, object(), Provider())
    search = UUID(service.search(actor, "synthetic selection", uuid4())["search_id"])
    selected = service.select(actor, search, "candidate00")
    identity = UUID(selected["acquisition_id"])
    with admission.sessions() as session:
        row = present(session.get(InternetAcquisitionRow, identity))
        assert row.authority_generation == 1
        assert row.source_session_family_id == actor.session_id
        assert row.source_session_mode == ("LEGACY" if legacy else "V2")
    with (
        pytest.raises(DBAPIError, match="Acquisition authority is immutable"),
        admission.sessions.begin() as session,
    ):
        present(session.get(InternetAcquisitionRow, identity)).source_session_family_id = uuid4()
    with admission.sessions.begin() as session:
        present(session.get(UserSessionRow, actor.session_id)).revoked_at = session.scalar(
            select(func.clock_timestamp())
        )
    with pytest.raises(MusicError) as denied:
        service.select(actor, search, "candidate00")
    assert denied.value.status_code == 401


def test_new_account_generation_cannot_rebind_existing_internet_selection(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    service = InternetMusicService(admission.sessions, object(), Provider())
    search = UUID(service.search(actor, "synthetic generation", uuid4())["search_id"])
    first_id = UUID(service.select(actor, search, "candidate00")["acquisition_id"])
    with admission.sessions.begin() as session:
        present(session.get(UserAccountRow, actor.user_id)).authority_generation = 2
    second_id = UUID(service.select(actor, search, "candidate01")["acquisition_id"])
    with admission.sessions() as session:
        first = present(session.get(InternetAcquisitionRow, first_id))
        second = present(session.get(InternetAcquisitionRow, second_id))
        assert (first.authority_generation, second.authority_generation) == (1, 2)
        gate = ResourceAuthorityGate(session, automatic_acquisition_enabled=False)
        authority = gate.authenticate(
            actor, present(session.scalar(select(func.clock_timestamp())))
        )
        with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
            gate.target(
                replace(authority, job_id=first.job_id),
                ResourceRequest(
                    first_id, ResourceKind.TRANSFER, "INTERNET_ACQUISITION", first_id, first_id
                ),
                present(session.scalar(select(func.clock_timestamp()))),
            )


def test_manual_a1_attempt_snapshots_account_generation_immutably(database_url: str) -> None:
    engine, sessions = _sessions(database_url)
    try:
        with sessions.begin() as session:
            owner, _ = _seed_import(session, "Authority test")
            present(session.get(UserAccountRow, owner)).authority_generation = 7
        with sessions.begin() as session:
            PostgresBulkDiscoveryRepository(session).start_search_acquisition(
                owner_user_id=owner, operation_id=uuid4(), evidence=_track()
            )
            attempt = present(session.scalar(select(AcquisitionAttemptRow)))
            assert attempt.authority_generation == 7
            identity = attempt.acquisition_attempt_id
        with (
            pytest.raises(DBAPIError, match="Acquisition authority is immutable"),
            sessions.begin() as session,
        ):
            present(session.get(AcquisitionAttemptRow, identity)).authority_generation = 8
    finally:
        engine.dispose()


def test_migration_leaves_historical_authority_unbound_and_protects_new_evidence(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name, "0036_resource_io_execution")
    with database_harness.connect(empty_database_name) as connection:
        owner, device, search, job, acquisition = (uuid4() for _ in range(5))
        connection.execute(
            "INSERT INTO account.user_account(user_id,display_name,role) "
            "VALUES (%s,'Legacy','USER')",
            (owner,),
        )
        connection.execute(
            "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version) "
            "VALUES (%s,%s,'Phone','ANDROID','fixture')",
            (device, owner),
        )
        connection.execute(
            "INSERT INTO jobs.job(job_id,job_type,schema_version,user_id,priority,state,payload) "
            "VALUES (%s,'music.internet.acquire',1,%s,2,'QUEUED','{}')",
            (job, owner),
        )
        connection.execute(
            "INSERT INTO discovery.internet_search(search_id,user_id,query,candidates,"
            "snapshot_sha256,expires_at) VALUES (%s,%s,'old','[]',%s,now()+interval '1 day')",
            (search, owner, b"x" * 32),
        )
        connection.execute(
            "INSERT INTO discovery.internet_acquisition(acquisition_id,user_id,device_id,"
            "search_id,candidate_id,selected_snapshot,job_id) VALUES (%s,%s,%s,%s,'old','{}',%s)",
            (acquisition, owner, device, search, job),
        )
        connection.commit()
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute(
            "SELECT authority_generation,source_session_family_id,source_session_mode "
            "FROM discovery.internet_acquisition WHERE acquisition_id=%s",
            (acquisition,),
        ).fetchone() == (None, None, None)
        connection.commit()
    engine, sessions = _sessions(database_harness.database_url(empty_database_name))
    try:
        with sessions.begin() as session:
            # A newly selected intent may record real authority; legacy rows cannot be rebound.
            session.execute(
                text(
                    "INSERT INTO discovery.internet_acquisition(acquisition_id,user_id,device_id,"
                    "search_id,candidate_id,selected_snapshot,job_id,authority_generation,"
                    "source_session_family_id,source_session_mode) "
                    "VALUES (:id,:owner,:device,:search,'new','{}',:job,1,:family,'V2')"
                ),
                {
                    "id": uuid4(),
                    "owner": owner,
                    "device": device,
                    "search": search,
                    "job": job,
                    "family": uuid4(),
                },
            )
        with pytest.raises(DBAPIError, match="Refusing to discard acquisition authority"):
            database_harness.downgrade(empty_database_name, "0036_resource_io_execution")
    finally:
        engine.dispose()
