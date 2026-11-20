"""Release regression for the production tables added before the R15 schema."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.internet_music import InternetSearchRow
from autplay.domain.auth import Principal

from .test_account_deletion import PairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_portable_account_privacy import _challenge, _identity, _records, _taste
from .test_privacy_purge import overdue
from .test_social_s1c import _principal

_TABLES = (
    ("account.portable_identity", "user_id"),
    ("account.portable_record", "user_id"),
    ("account.portable_challenge", "account_id"),
    ("library.portable_taste_signal", "user_id"),
    ("discovery.internet_search_context", "user_id"),
    ("discovery.internet_catalogue_context", "user_id"),
    ("library.catalog_execution", "user_id"),
)


def _seed(session: Session, actor: Principal, now: datetime) -> UUID:
    _identity(session, actor.user_id, now)
    _records(session, actor.user_id, now, 1)
    _taste(session, actor.user_id, now)
    _challenge(session, actor.user_id, now)
    context_id, search_id, execution = uuid4(), uuid4(), uuid4()
    session.add(
        InternetSearchRow(
            search_id=search_id,
            user_id=actor.user_id,
            query="fixture",
            candidates=[],
            snapshot_sha256=b"s" * 32,
            created_at=now,
            expires_at=now + timedelta(hours=24),
        )
    )
    session.flush()
    values = dict(
        owner=actor.user_id,
        device=actor.device_id,
        session=actor.session_id,
        context=context_id,
        search=search_id,
        execution=execution,
        now=now,
        run=uuid4(),
        request=uuid4(),
        digest=b"x" * 32,
    )
    session.execute(
        text("""
        INSERT INTO discovery.internet_catalogue_context
        (context_id,user_id,card,context_sha256,observed_at,expires_at) VALUES
        (:context,:owner,'{"schema_version": 1}',:digest,:now,
         CAST(:now AS timestamptz)+interval '24 hours')
    """),
        values,
    )
    session.execute(
        text("""
        INSERT INTO discovery.internet_search_context (search_id,user_id,context_id,request_sha256)
        VALUES (:search,:owner,:context,:digest)
    """),
        values,
    )
    session.execute(
        text("""
        INSERT INTO library.catalog_execution
        (execution_id,owner_run_id,user_id,device_id,session_id,authority_generation,request_id,
         state,created_at,deadline_at) SELECT :execution,:run,:owner,:device,:session,
         authority_generation,:request,'PREPARED',statement_timestamp(),
         statement_timestamp()+interval '30 seconds' FROM account.user_account WHERE user_id=:owner
    """),
        values,
    )
    return execution


def _counts(session: Session, owner: UUID) -> list[int]:
    return [
        session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column}=:owner"), {"owner": owner}
        ).scalar_one()
        for table, column in _TABLES
    ]


def _close_execution(session: Session, execution: UUID) -> None:
    session.execute(
        text("""
        UPDATE library.catalog_execution SET state='CLOSED',closed_at=clock_timestamp(),
        closure_kind='NOT_STARTED',closure_evidence_sha256=:digest WHERE execution_id=:execution
    """),
        {"execution": execution, "digest": b"q" * 32},
    )


def test_production_and_source3_share_an_owner_purge(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    with Session(pair.engine) as session, session.begin():
        other = _principal(session, "Surviving compatibility fixture", now)
        target_execution = _seed(session, pair.actor, now)
        _close_execution(session, _seed(session, other, now))
        assert _counts(session, pair.actor.user_id) == [1] * 7
        assert _counts(session, other.user_id) == [1] * 7
    operation = overdue(pair, monkeypatch)
    with Session(pair.engine) as session, session.begin():
        with pytest.raises(DBAPIError) as caught, session.begin_nested():
            session.execute(
                text("SELECT account.purge_account(:owner,:operation)"),
                {"owner": pair.actor.user_id, "operation": operation},
            )
        assert caught.value.orig.sqlstate == "55000"
        assert _counts(session, pair.actor.user_id) == [1] * 7
        _close_execution(session, target_execution)
        session.execute(
            text("SELECT account.purge_account(:owner,:operation)"),
            {"owner": pair.actor.user_id, "operation": operation},
        )
        session.execute(
            text("SELECT account.verify_owner_absent(:owner,ARRAY[:device]::uuid[])"),
            {"owner": pair.actor.user_id, "device": pair.actor.device_id},
        )
    with Session(pair.engine) as session:
        assert _counts(session, pair.actor.user_id) == [0] * 7
        assert _counts(session, other.user_id) == [1] * 7
