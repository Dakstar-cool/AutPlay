"""Real account-policy concurrency, rollback, exact replay and deletion evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import pytest
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.models.account import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.audit import AuditEventRow
from autplay.adapters.postgresql.models.training_consent import (
    TrainingConsentOperationRow,
    TrainingConsentRow,
)
from autplay.application.training_consent import TrainingConsentError, TrainingConsentService
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_account_deletion import PairingHarness as DeletionPairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_self_device_pairing import PairingHarness as BasePairingHarness


@dataclass
class PairingHarness(DeletionPairingHarness):
    consent_ledger: FilesystemTrainingConsentLedger


@pytest.fixture
def pair(base_pair: BasePairingHarness, tmp_path: Path) -> PairingHarness:
    deletion_ledger = FilesystemDeletionLedger(
        tmp_path / "deletion.sqlite3", b"d" * 32, "fixture-v1"
    )
    deletion_ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=2))
    deletion_pair = DeletionPairingHarness(**vars(base_pair), deletion_ledger=deletion_ledger)
    ledger = FilesystemTrainingConsentLedger(tmp_path / "consent.sqlite3", b"s" * 32, "fixture-v1")
    ledger.initialize()
    return PairingHarness(**vars(deletion_pair), consent_ledger=ledger)


def service(pair: PairingHarness) -> TrainingConsentService:
    return TrainingConsentService(
        sessionmaker(pair.engine, expire_on_commit=False), pair.consent_ledger
    )


def command(pair: PairingHarness, decision: str, revision: int = 0) -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "account_id": str(pair.actor.user_id),
        "decision": decision,
        "expected_revision": revision,
        "policy_version": 1,
    }


def test_sona_skip_collection_checks_window_current_device_and_owner_consent(
    pair: PairingHarness,
) -> None:
    from .test_social_s1c import _device_principal

    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    now = int(datetime.now(UTC).timestamp() * 1000)

    active = service(pair)
    with sessions.begin() as session:
        second = _device_principal(session, pair.actor.user_id, "Other phone", datetime.now(UTC))
    second = replace(second, role=pair.actor.role)

    def developer_mode(enabled: bool) -> None:
        with sessions.begin() as session:
            session.add(
                AuditEventRow(
                    actor_type="ADMIN",
                    actor_user_id=pair.actor.user_id,
                    action="profile.developer_mode_enabled"
                    if enabled
                    else "profile.developer_mode_disabled",
                    target_type="DEVICE",
                    target_id=pair.actor.device_id,
                    metadata_sanitized={},
                )
            )

    developer_mode(True)
    with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
        active.sona_variation_skips(pair.actor)
    active.decide(pair.actor, command(pair, "GRANTED"))

    def window(start: int, end: int | None) -> None:
        with sessions.begin() as session:
            session.execute(
                text("UPDATE ml.sona_collection_window SET starts_at=:start, ends_at=:end"),
                {
                    "start": datetime.fromtimestamp(start / 1000, UTC),
                    "end": datetime.fromtimestamp(end / 1000, UTC) if end is not None else None,
                },
            )

    assert active.sona_variation_skips(pair.actor)["remaining_ms"] == 15_000
    for start, end in ((now - 6000, now - 3000), (now + 90_000, now + 120_000)):
        window(start, end)
        with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
            active.sona_variation_skips(pair.actor)
    window(now - 60_000, now + 120_000)
    with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
        active.sona_variation_skips(second)
    report = active.sona_variation_skips(pair.actor)
    assert report["account_id"] == str(pair.actor.user_id)
    assert report["variation_skips"] == []
    assert 0 < int(str(report["remaining_ms"])) <= 120_000
    developer_mode(False)
    with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
        active.sona_variation_skips(pair.actor)
    developer_mode(True)
    active.decide(second, command(pair, "WITHDRAWN", 1))
    with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
        active.sona_variation_skips(pair.actor)


def test_admin_schedule_controls_the_mobile_report_across_sessions(pair: PairingHarness) -> None:
    import re

    from autplay.adapters.postgresql.admin_commands import SqlAlchemyAdminCommandRepository
    from autplay.adapters.postgresql.admin_views_runtime import SqlAlchemyAdminViewService
    from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
    from autplay.application.admin_commands import AdminCommandService
    from autplay.application.sona_collection import SonaCollectionService
    from autplay.application.web_admin import WebAdminService
    from autplay.entrypoints.admin_web_http import create_admin_web_router
    from autplay.runtime.web_security import WebCookieProfile
    from autplay.web.renderer import AdminTemplateRenderer
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    admin = uuid4()
    with sessions.begin() as session:
        session.add(UserAccountRow(user_id=admin, display_name="Sona operator", role="ADMIN"))
        session.add(
            AuditEventRow(
                actor_type="ADMIN",
                action="profile.developer_mode_enabled",
                target_type="DEVICE",
                target_id=pair.actor.device_id,
                metadata_sanitized={},
            )
        )
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    web = WebAdminService(SqlAlchemyWebAdminUnitOfWorkFactory(sessions), csrf_secret=b"c" * 32)
    credentials = web.login(web.begin_login(), web.issue_invitation(admin).bearer, b"r" * 32)
    origin = "https://admin.test"
    app = FastAPI()
    app.include_router(
        create_admin_web_router(
            web=web,
            views=SqlAlchemyAdminViewService(sessions),
            commands=AdminCommandService(SqlAlchemyAdminCommandRepository(sessions)),
            sona_collection=SonaCollectionService(sessions),
            renderer=AdminTemplateRenderer(),
            origin=origin,
            source_secret=b"s" * 32,
        )
    )
    path = "/admin/sona-collection"
    with TestClient(app, base_url=origin, follow_redirects=False) as client:
        assert client.get(path).status_code == 303
        client.cookies.set(
            WebCookieProfile.for_origin(origin).session_name, credentials.bearer.decode()
        )
        assert path in client.get("/admin/server").text

        def form(action: str) -> dict[str, str]:
            page = client.get(path + "?lang=en")
            assert page.status_code == 200
            found = re.search(f'action="{path}/{action}[^"]*">(.*?)</form>', page.text, re.S)
            assert found is not None
            return dict(re.findall(r'type="hidden" name="([^"]+)" value="([^"]+)"', found[1]))

        headers = {"Origin": origin}
        schedule = SonaCollectionService(sessions).get(credentials.actor)
        page = client.get(path + "?lang=ru")
        assert page.status_code == 200
        assert "Сбор идёт" in page.text and "Без даты окончания" in page.text  # noqa: RUF001
        assert f'datetime="{schedule.starts_at.astimezone(UTC).isoformat()}"' in page.text
        moscow = timezone(timedelta(hours=3))
        moscow_start = schedule.starts_at.astimezone(moscow).replace(tzinfo=None)
        assert f'value="{moscow_start.isoformat(timespec="milliseconds")}"' in page.text
        assert 'name="clock" value="UTC+03:00"' in page.text
        assert schedule.ends_at is None
        assert "Collection is active" in client.get(path + "?lang=en").text
        assert policy.sona_variation_skips(pair.actor)["remaining_ms"] == 15_000
        stale = form("save") | {"starts_at": "", "ends_at": ""}
        stop = form("stop")
        for bad_headers, bad_form in (
            ({"Origin": "https://other.test"}, stop),
            (headers, stop | {"csrf_token": "bad"}),
        ):
            assert (
                client.post(path + "/stop", headers=bad_headers, data=bad_form).status_code == 403
            )
        for _ in range(2):
            assert client.post(path + "/stop", headers=headers, data=stop).status_code == 303
        with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
            service(pair).sona_variation_skips(pair.actor)
        assert client.post(path + "/save", headers=headers, data=stale).status_code == 409
        assert SonaCollectionService(sessions).get(credentials.actor).state == "stopped"

        dates = form("save")
        bad = dates | {"starts_at": "2030-01-02T12:00", "ends_at": "2030-01-01T12:00"}
        invalid = client.post(path + "/save", headers=headers, data=bad)
        assert invalid.status_code == 422 and bad["ends_at"] in invalid.text
        tomorrow = datetime.now(UTC).date() + timedelta(days=1)
        future = dates | {"starts_at": f"{tomorrow}T12:00", "ends_at": f"{tomorrow}T13:30"}
        assert client.post(path + "/save", headers=headers, data=future).status_code == 303
        scheduled = SonaCollectionService(sessions).get(credentials.actor)
        assert scheduled.starts_at.hour == 9
        assert scheduled.ends_at is not None and scheduled.ends_at.hour == 10
        assert scheduled.state == "scheduled"
        with pytest.raises(TrainingConsentError, match="sona_collection_unavailable"):
            policy.sona_variation_skips(pair.actor)
        assert client.post(path + "/stop", headers=headers, data=form("stop")).status_code == 303

        restart = form("save") | {"starts_at": "2020-01-01T00:00", "ends_at": ""}
        before = datetime.now(UTC)
        assert client.post(path + "/save", headers=headers, data=restart).status_code == 303
        reopened = SonaCollectionService(sessions).get(credentials.actor)
        assert reopened.state == "active" and reopened.starts_at >= before - timedelta(seconds=1)
        assert service(pair).sona_variation_skips(pair.actor)["remaining_ms"] == 15_000
        with sessions.begin() as session:
            session.execute(
                text("UPDATE ml.sona_collection_window SET ends_at='9999-12-31 23:59:59+00'")
            )
        assert form("save")["clock"] == "UTC"


def test_default_private_decision_replay_does_not_restore_old_grant(pair: PairingHarness) -> None:
    policy = service(pair)
    assert policy.get(pair.actor)["decision"] == "UNKNOWN"
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0
    grant = command(pair, "GRANTED")
    assert policy.decide(pair.actor, grant)["revision"] == 1
    assert policy.decide(pair.actor, grant)["revision"] == 1
    policy.decide(pair.actor, command(pair, "WITHDRAWN", 0))
    replay = policy.decide(pair.actor, grant)
    assert (
        replay["decision"],
        replay["revision"],
        replay["applied_decision"],
        replay["applied_revision"],
    ) == ("WITHDRAWN", 2, "GRANTED", 1)
    assert policy.get(pair.actor)["decision"] == "WITHDRAWN"
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentOperationRow)) == 2
    with pytest.raises(TrainingConsentError, match="consent_revision_conflict"):
        policy.decide(pair.actor, command(pair, "GRANTED", 1))
    assert policy.decide(pair.actor, command(pair, "GRANTED", 2))["revision"] == 3
    with Session(pair.engine) as session, session.begin():
        with pytest.raises(TrainingConsentError, match="training_consent_required"):
            policy.require_granted(session, pair.actor.user_id, 1)
        policy.require_granted(session, pair.actor.user_id, 3)


def test_refusal_from_another_device_never_reprompts_or_changes_personal_data(
    pair: PairingHarness,
) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "DENIED"))
    assert policy.get(pair.actor)["decision"] == "DENIED"
    from .test_social_s1c import _device_principal

    with Session(pair.engine) as session, session.begin():
        second = _device_principal(session, pair.actor.user_id, "Second", policy._now(session))
    second = replace(second, role=pair.actor.role)
    assert policy.get(second)["revision"] == 1
    assert policy.get(second)["decision"] == "DENIED"
    assert policy.decide(second, command(pair, "GRANTED", 1))["revision"] == 2
    assert policy.get(pair.actor)["decision"] == "GRANTED"


@pytest.mark.parametrize("fault", ["account", "device", "session", "role", "wrong_account"])
def test_current_actor_is_rechecked_under_account_lock(pair: PairingHarness, fault: str) -> None:
    principal = pair.actor
    with Session(pair.engine) as session, session.begin():
        if fault == "account":
            session.execute(
                text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:u"),
                {"u": principal.user_id},
            )
        elif fault == "device":
            row = session.get(DeviceRow, principal.device_id)
            assert row is not None
            row.revoked_at = service(pair)._now(session)
        elif fault == "session":
            row2 = session.get(UserSessionRow, principal.session_id)
            assert row2 is not None
            row2.revoked_at = service(pair)._now(session)
        elif fault == "role":
            session.execute(
                text("UPDATE account.user_account SET role='ADMIN' WHERE user_id=:u"),
                {"u": principal.user_id},
            )
    body = command(pair, "GRANTED")
    if fault == "wrong_account":
        body["account_id"] = str(uuid4())
    with pytest.raises(TrainingConsentError):
        service(pair).decide(principal, body)
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0


def test_grant_refusal_race_finishes_private_and_old_revision_stays_invalid(
    pair: PairingHarness,
) -> None:
    barrier = Barrier(2)
    policy = service(pair)

    def run(decision: str) -> str:
        barrier.wait(timeout=5)
        try:
            return str(policy.decide(pair.actor, command(pair, decision))["decision"])
        except TrainingConsentError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["GRANTED", "WITHDRAWN"]))
    assert "WITHDRAWN" in results
    assert policy.get(pair.actor)["decision"] == "WITHDRAWN"
    with Session(pair.engine) as session, session.begin(), pytest.raises(TrainingConsentError):
        policy.require_granted(session, pair.actor.user_id, 1)


def test_changed_replay_is_rejected_and_receipts_are_immutable(pair: PairingHarness) -> None:
    policy = service(pair)
    body = command(pair, "DENIED")
    policy.decide(pair.actor, body)
    with pytest.raises(TrainingConsentError, match="operation_conflict"):
        policy.decide(pair.actor, {**body, "decision": "GRANTED", "expected_revision": 1})
    with (
        Session(pair.engine) as session,
        session.begin(),
        pytest.raises(DBAPIError, match="training_consent_operation_immutable"),
    ):
        session.execute(text("DELETE FROM account.training_consent_operation"))


def test_transaction_failure_does_not_leave_a_policy_or_success_receipt(
    pair: PairingHarness,
) -> None:
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text("""CREATE FUNCTION account.test_consent_abort()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'consent fixture abort'; END $$;
        CREATE TRIGGER test_consent_abort BEFORE INSERT ON account.training_consent_operation
        FOR EACH ROW EXECUTE FUNCTION account.test_consent_abort();""")
        )
    with pytest.raises(DBAPIError, match="consent fixture abort"):
        service(pair).decide(pair.actor, command(pair, "GRANTED"))
    with Session(pair.engine) as session:
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0


def test_final_purge_removes_policy_and_exact_receipts(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_privacy_purge import overdue

    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    request_id = overdue(pair, monkeypatch)
    with Session(pair.engine) as session, session.begin():
        removed = session.scalar(
            text("SELECT account.purge_account(:u,:r)"),
            {"u": pair.actor.user_id, "r": request_id},
        )
        assert isinstance(removed, int) and removed > 0
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
        assert session.scalar(select(func.count()).select_from(TrainingConsentRow)) == 0
        assert session.scalar(select(func.count()).select_from(TrainingConsentOperationRow)) == 0


def test_repeatable_read_snapshot_cannot_authorize_after_withdrawal(pair: PairingHarness) -> None:
    policy = service(pair)
    policy.decide(pair.actor, command(pair, "GRANTED"))
    with (
        pair.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        Session(connection) as session,
        session.begin(),
    ):
        assert session.scalar(select(TrainingConsentRow.revision)) == 1
        policy.decide(pair.actor, command(pair, "WITHDRAWN", 1))
        with pytest.raises(DBAPIError) as failure:
            policy.require_granted(session, pair.actor.user_id, 1)
        assert getattr(failure.value.orig, "sqlstate", None) == "40001"


def test_downgrade_cannot_destroy_a_concurrently_committed_decision(
    pair: PairingHarness, database_harness: DatabaseHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_name = pair.engine.url.database
    assert database_name is not None
    database_harness.downgrade(database_name, "0054_training_consent")
    flushed, release = Event(), Event()
    original = TrainingConsentService._result

    def pause(cls: type[TrainingConsentService], *args: Any) -> dict[str, object]:
        result = original(*args)
        flushed.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(TrainingConsentService, "_result", classmethod(pause))
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(service(pair).decide, pair.actor, command(pair, "DENIED"))
        assert flushed.wait(5)
        downgrade = executor.submit(database_harness.downgrade, database_name, "0053_privacy_purge")
        try:
            deadline = monotonic() + 5
            blocked = False
            while monotonic() < deadline:
                with pair.engine.connect() as connection:
                    blocked = bool(
                        connection.scalar(
                            text("""SELECT EXISTS(SELECT 1 FROM pg_locks
                     WHERE relation IN ('account.training_consent'::regclass,
                       'account.user_account'::regclass)
                     AND mode='AccessExclusiveLock' AND NOT granted)""")
                        )
                    )
                if blocked:
                    break
                sleep(0.02)
            assert blocked
        finally:
            release.set()
        assert writer.result(timeout=5)["decision"] == "DENIED"
        with pytest.raises(DBAPIError, match="training_consent_evidence_retained"):
            downgrade.result(timeout=10)
    assert service(pair).get(pair.actor)["decision"] == "DENIED"
