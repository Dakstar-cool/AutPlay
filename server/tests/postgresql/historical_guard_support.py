"""Real historical PostgreSQL fixtures and exact current-head retention proofs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import rfc8785
from psycopg import Connection, Error
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

from .conftest import DatabaseHarness

REV39: Final = "0039_internet_ingest_lineage"
REV40: Final = "0040_provider_staging"
REV41: Final = "0041_provider_scratch"
REV42: Final = "0042_provider_maintenance"
HEAD65: Final = "0065_internet_catalogue_context"
PAYLOAD = b"historical-provider-source-bytes-v1"
HistoricalRevision = Literal[
    "0039_internet_ingest_lineage",
    "0040_provider_staging",
    "0041_provider_scratch",
    "0042_provider_maintenance",
]
HistoricalStage = Literal["EXITED", "HANDED_OFF"]


@dataclass(frozen=True)
class HistoricalSource:
    user_id: UUID
    device_id: UUID
    recording_id: UUID
    search_id: UUID
    acquisition_id: UUID
    acquire_job_id: UUID
    ingest_job_id: UUID
    upload_id: UUID
    execution_id: UUID
    owner_run_id: UUID
    operation_id: UUID
    activation_id: UUID
    permit_id: UUID
    sha256: bytes


def revision(connection: Connection[Any]) -> str:
    row = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def seed_historical_source(
    connection: Connection[Any],
    expected_revision: HistoricalRevision,
    *,
    stage: HistoricalStage | None,
) -> HistoricalSource:
    """Seed the actual selected historical schema, through legal stage transitions.

    NULL acquisition authority is an explicitly supported pre-0037 persisted state.
    This is a historical data fixture, not a claim that an unbound source may start
    new work. The ordinary current-head runtime tests continue using live authority.
    All stored owner/device/search/job/upload FKs reference real fixture rows.
    """
    assert revision(connection) == expected_revision
    assert (stage is None) == (expected_revision == REV39)
    now = datetime.now(UTC)
    source = HistoricalSource(
        user_id=uuid4(),
        device_id=uuid4(),
        recording_id=uuid4(),
        search_id=uuid4(),
        acquisition_id=uuid4(),
        acquire_job_id=uuid4(),
        ingest_job_id=uuid4(),
        upload_id=uuid4(),
        execution_id=uuid4(),
        owner_run_id=uuid4(),
        operation_id=uuid4(),
        activation_id=uuid4(),
        permit_id=uuid4(),
        sha256=hashlib.sha256(PAYLOAD).digest(),
    )
    source = replace(
        source,
        upload_id=uuid5(NAMESPACE_URL, f"autplay:internet-ingest:v1:{source.acquisition_id}"),
    )
    candidate: dict[str, Any] = {
        "candidate_id": "candidate00",
        "provider": "YouTube",
        "title": "Song",
        "artist": "Artist",
        "duration_ms": 150000,
        "rank": 1,
    }
    candidates = [candidate]
    credit = uuid4()
    connection.execute(
        "INSERT INTO account.user_account(user_id,display_name,role) VALUES(%s,%s,'USER')",
        (source.user_id, "Historical guard owner"),
    )
    connection.execute(
        "INSERT INTO account.device(device_id,user_id,device_name,platform,app_version) "
        "VALUES(%s,%s,'Historical phone','ANDROID','fixture')",
        (source.device_id, source.user_id),
    )
    connection.execute(
        "INSERT INTO catalog.artist_credit(artist_credit_id,display_name,normalized_name) "
        "VALUES(%s,'Artist','artist')",
        (credit,),
    )
    connection.execute(
        "INSERT INTO catalog.recording(recording_id,artist_credit_id,title,normalized_title,"
        "duration_ms,recording_kind) VALUES(%s,%s,'Song','song',150000,'STUDIO')",
        (source.recording_id, credit),
    )
    connection.execute(
        "INSERT INTO jobs.job(job_id,job_type,schema_version,user_id,payload,state,"
        "attempt_count,lease_owner,lease_deadline,heartbeat_at,started_at) "
        "VALUES(%s,'music.internet.acquire',1,%s,%s,'RUNNING',1,'historical-worker',%s,%s,%s)",
        (
            source.acquire_job_id,
            source.user_id,
            Jsonb({"acquisition_id": str(source.acquisition_id)}),
            now + timedelta(minutes=1),
            now,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO discovery.internet_search(search_id,user_id,query,candidates,"
        "snapshot_sha256,expires_at) VALUES(%s,%s,'historical song',%s,%s,%s)",
        (
            source.search_id,
            source.user_id,
            Jsonb(candidates),
            hashlib.sha256(rfc8785.dumps(candidates)).digest(),
            now + timedelta(days=1),
        ),
    )
    connection.execute(
        "INSERT INTO discovery.internet_acquisition(acquisition_id,user_id,device_id,"
        "search_id,candidate_id,selected_snapshot,job_id,state) "
        "VALUES(%s,%s,%s,%s,'candidate00',%s,%s,'DOWNLOADING')",
        (
            source.acquisition_id,
            source.user_id,
            source.device_id,
            source.search_id,
            Jsonb(candidate),
            source.acquire_job_id,
        ),
    )
    if stage is not None:
        connection.execute(
            "INSERT INTO vault.provider_staging(execution_id,user_id,resource_type,"
            "acquisition_id,job_id,job_worker_id,job_attempt,operation_id,activation_id,"
            "generation,permit_id,owner_run_id,staging_key,state,created_at,updated_at) "
            "VALUES(%s,%s,'INTERNET_ACQUISITION',%s,%s,'historical-worker',1,%s,%s,1,%s,%s,"
            "'provider-'||replace(%s::text,'-',''),'OWNED',%s,%s)",
            (
                source.execution_id,
                source.user_id,
                source.acquisition_id,
                source.acquire_job_id,
                source.operation_id,
                source.activation_id,
                source.permit_id,
                source.owner_run_id,
                source.execution_id,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE vault.provider_staging SET state='EXITED',closed_at=%s,"
            "closure_kind='PROCESS_EXIT',closure_evidence_sha256=%s,exit_code=0,"
            "child_pid=34567,child_identity_sha256=%s,updated_at=%s WHERE execution_id=%s",
            (now, b"e" * 32, b"c" * 32, now, source.execution_id),
        )
    if stage != "EXITED":
        if stage == "HANDED_OFF":
            connection.execute(
                "UPDATE vault.provider_staging SET state='SEALED',byte_size=%s,sha256=%s,"
                "sealed_at=%s,updated_at=%s WHERE execution_id=%s",
                (len(PAYLOAD), source.sha256, now, now, source.execution_id),
            )
        connection.execute(
            "INSERT INTO jobs.job(job_id,job_type,schema_version,user_id,payload) "
            "VALUES(%s,'vault.ingest',1,%s,%s)",
            (
                source.ingest_job_id,
                source.user_id,
                Jsonb({"upload_session_id": str(source.upload_id)}),
            ),
        )
        request = {
            "source": str(source.acquisition_id),
            "execution": str(source.execution_id),
            "recording": str(source.recording_id),
            "size": len(PAYLOAD),
            "sha256": source.sha256.hex(),
        }
        connection.execute(
            "INSERT INTO vault.upload_session(upload_session_id,user_id,actor_kind,"
            "source_internet_acquisition_id,target_recording_id,idempotency_key,request_hash,"
            "declared_sha256,expected_size,received_size,chunk_size,max_chunks,chunk_count,"
            "staging_key,state,job_id,sealed_at,created_at,updated_at,expires_at) "
            "VALUES(%s,%s,'INTERNET',%s,%s,%s,%s,%s,%s,%s,1024,1,1,%s,'SEALED',%s,%s,%s,%s,%s)",
            (
                source.upload_id,
                source.user_id,
                source.acquisition_id,
                source.recording_id,
                f"internet:{source.acquisition_id}",
                hashlib.sha256(
                    json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
                ).digest(),
                source.sha256,
                len(PAYLOAD),
                len(PAYLOAD),
                f"provider-{source.execution_id.hex}",
                source.ingest_job_id,
                now,
                now,
                now,
                now + timedelta(days=1),
            ),
        )
        connection.execute(
            "UPDATE discovery.internet_acquisition SET upload_id=%s,state='PROCESSING' "
            "WHERE acquisition_id=%s",
            (source.upload_id, source.acquisition_id),
        )
        if stage == "HANDED_OFF":
            connection.execute(
                "UPDATE vault.provider_staging SET state='HANDED_OFF',upload_session_id=%s,"
                "handed_off_at=%s,updated_at=%s WHERE execution_id=%s",
                (source.upload_id, now, now, source.execution_id),
            )
    return source


def source_snapshot(connection: Connection[Any], source: HistoricalSource) -> tuple[object, ...]:
    """Persisted source identities, snapshots, job and recording are fully retained."""
    return tuple(
        connection.execute(statement, (key,)).fetchone()
        for statement, key in (
            (
                "SELECT to_jsonb(s) FROM discovery.internet_search s WHERE search_id=%s",
                source.search_id,
            ),
            (
                "SELECT to_jsonb(a) FROM discovery.internet_acquisition a WHERE acquisition_id=%s",
                source.acquisition_id,
            ),
            ("SELECT to_jsonb(j) FROM jobs.job j WHERE job_id=%s", source.acquire_job_id),
            (
                "SELECT to_jsonb(r) FROM catalog.recording r WHERE recording_id=%s",
                source.recording_id,
            ),
            (
                "SELECT to_jsonb(u) FROM vault.upload_session u WHERE upload_session_id=%s",
                source.upload_id,
            ),
        )
    )


def assert_exact_refusal(
    harness: DatabaseHarness,
    database_name: str,
    target: str,
    message: str,
) -> None:
    with pytest.raises(DBAPIError) as caught:
        harness.downgrade(database_name, target)
    # Exact primary reason, not a broad error regular expression.
    error = cast(Error, caught.value.orig)
    assert error.diag.message_primary == message
    assert error.sqlstate == (
        "55000"
        if message == "refusing to discard catalogue context or search request receipts"
        else "P0001"
    )


def stage_without_scratch(connection: Connection[Any], source: HistoricalSource) -> dict[str, Any]:
    row = connection.execute(
        "SELECT to_jsonb(p)-'scratch_claim_id'-'scratch_claimed_at'-'scratch_retired_at' "
        "FROM vault.provider_staging p WHERE execution_id=%s",
        (source.execution_id,),
    ).fetchone()
    assert row is not None
    assert isinstance(row[0], dict)
    return cast(dict[str, Any], row[0])


def assert_current_context_refusal(
    harness: DatabaseHarness,
    database_name: str,
    acquisition_id: UUID,
    target: str,
) -> None:
    """Prove the exact 0065 refusal preserves the current-service fixture.

    Capture the real selected search/None receipt and complete acquisition/upload/
    provider/run rows before attempting downgrade. Do not delete or rewrite them.
    Existing runtime assertions remain in the original test body.
    """

    def snapshot(connection: Connection[Any]) -> tuple[object, ...]:
        assert revision(connection) == HEAD65
        receipt = connection.execute(
            "SELECT to_jsonb(s),to_jsonb(c),to_jsonb(a),to_jsonb(d) "
            "FROM discovery.internet_acquisition a "
            "JOIN discovery.internet_search s ON (s.user_id,s.search_id)=(a.user_id,a.search_id) "
            "JOIN discovery.internet_search_context c "
            "ON (c.user_id,c.search_id)=(a.user_id,a.search_id) "
            "LEFT JOIN discovery.internet_catalogue_context d "
            "ON (d.user_id,d.context_id)=(c.user_id,c.context_id) "
            "WHERE a.acquisition_id=%s",
            (acquisition_id,),
        ).fetchone()
        assert receipt is not None
        related = tuple(
            connection.execute(statement, (acquisition_id,)).fetchall()
            for statement in (
                "SELECT to_jsonb(u) FROM vault.upload_session u "
                "WHERE source_internet_acquisition_id=%s ORDER BY upload_session_id",
                "SELECT to_jsonb(p) FROM vault.provider_staging p "
                "WHERE acquisition_id=%s ORDER BY execution_id",
                "SELECT to_jsonb(m) FROM vault.provider_maintenance m "
                "JOIN vault.provider_staging p ON p.execution_id=m.provider_execution_id "
                "WHERE p.acquisition_id=%s ORDER BY m.execution_id",
            )
        )
        return (receipt, *related)

    with harness.connect(database_name) as connection:
        before = snapshot(connection)
    assert_exact_refusal(
        harness,
        database_name,
        target,
        "refusing to discard catalogue context or search request receipts",
    )
    with harness.connect(database_name) as connection:
        assert snapshot(connection) == before


def internet_acquisition_for_execution(
    harness: DatabaseHarness, database_name: str, execution_id: UUID
) -> UUID:
    with harness.connect(database_name) as connection:
        row = connection.execute(
            "SELECT acquisition_id FROM vault.provider_staging "
            "WHERE execution_id=%s AND resource_type='INTERNET_ACQUISITION'",
            (execution_id,),
        ).fetchone()
    assert row is not None and row[0] is not None
    return UUID(str(row[0]))
