"""Additional PA2 PostgreSQL race and lifecycle evidence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    FriendshipRow,
    PresenceSettingsRow,
    UserSessionRow,
)
from autplay.application.public_access import PublicAccessError
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from sqlalchemy import text
from sqlalchemy.orm import Session

from .test_public_access_pa2 import _owner_and_server, _request, _service


def test_account_cap_race_admits_only_one_last_slot(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        for index in range(18):
            database_connection.execute(
                "INSERT INTO account.user_account (display_name,role) VALUES (%s,'USER')",
                (f"existing-{index}",),
            )
        database_connection.commit()
        invitations = [
            service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": f"candidate-{index}",
                    "expires_in_seconds": 600,
                },
            )[0]
            for index in range(2)
        ]
        gate = Barrier(2)

        def redeem(invitation: dict[str, object]) -> bool:
            gate.wait(timeout=5)
            try:
                service.redeem(_request(invitation, ec.generate_private_key(ec.SECP256R1())), "cap")
                return True
            except PublicAccessError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(redeem, invitations))
        assert outcomes.count(True) == 1
        with Session(engine) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.user_account WHERE status='ACTIVE'")
                ).scalar_one()
                == 20
            )
    finally:
        engine.dispose()


def test_active_invitation_cap_race_admits_only_one_fifth_invitation(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        for index in range(4):
            service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": f"existing-invitation-{index}",
                    "expires_in_seconds": 600,
                },
            )
        gate = Barrier(2)

        def create(index: int) -> bool:
            gate.wait(timeout=5)
            try:
                service.create_invitation(
                    owner,
                    {
                        "contract_version": "v1",
                        "schema_version": 1,
                        "operation_id": str(uuid4()),
                        "account_display_name": f"racing-invitation-{index}",
                        "expires_in_seconds": 600,
                    },
                )
                return True
            except PublicAccessError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(create, range(2)))
        assert outcomes.count(True) == 1
        with Session(engine) as session:
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM account.account_invitation "
                        "WHERE cancelled_at IS NULL AND consumed_at IS NULL AND expires_at > now()"
                    )
                ).scalar_one()
                == 5
            )
    finally:
        engine.dispose()


def test_disable_revokes_session_and_exact_replay_returns_same_result(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "friend",
                "expires_in_seconds": 600,
            },
        )
        registration = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        response, _ = service.redeem(registration, "disable")
        command = {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "reason_code": "ACCESS_ENDED",
        }
        first = service.disable_account(owner, UUID(str(response["user_id"])), command)
        assert service.disable_account(owner, UUID(str(response["user_id"])), command) == first
        with Session(engine) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.user_session WHERE revoked_at IS NULL")
                ).scalar_one()
                == 0
            )
        # Exact replay must re-lock and reload mutable authority; a disabled account never
        # receives a freshly minted access token from the retained registration receipt.
        with pytest.raises(PublicAccessError):
            service.redeem(registration, "disable-replay")
    finally:
        engine.dispose()


def test_disable_and_registration_replay_are_serialized_before_token_issue(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "disable-race",
                "expires_in_seconds": 600,
            },
        )
        registration = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        response, _ = service.redeem(registration, "disable-race-first")
        command = {
            "contract_version": "v1",
            "schema_version": 1,
            "operation_id": str(uuid4()),
            "reason_code": "SECURITY",
        }
        target = UUID(str(response["user_id"]))
        gate, outcome_lock = Barrier(2), Lock()
        outcomes: list[tuple[str, bool]] = []

        def replay() -> None:
            gate.wait(timeout=5)
            try:
                service.redeem(registration, "disable-race-replay")
                result = True
            except PublicAccessError:
                result = False
            with outcome_lock:
                outcomes.append(("replay", result))

        def disable() -> None:
            gate.wait(timeout=5)
            service.disable_account(owner, target, command)
            with outcome_lock:
                outcomes.append(("disable", True))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(replay), pool.submit(disable))
            for future in futures:
                future.result(timeout=10)

        assert {name for name, _ in outcomes} == {"replay", "disable"}
        if outcomes[0][0] == "disable":
            assert outcomes[1] == ("replay", False)
        with pytest.raises(PublicAccessError):
            service.redeem(registration, "disable-race-after")
    finally:
        engine.dispose()


def test_session_retirement_and_registration_replay_are_serialized(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "session-race",
                "expires_in_seconds": 600,
            },
        )
        registration = _request(invitation, ec.generate_private_key(ec.SECP256R1()))
        response, _ = service.redeem(registration, "session-race-first")
        session_id = UUID(str(response["session_id"]))
        gate, outcome_lock = Barrier(2), Lock()
        outcomes: list[tuple[str, bool]] = []

        def replay() -> None:
            gate.wait(timeout=5)
            try:
                service.redeem(registration, "session-race-replay")
                result = True
            except PublicAccessError:
                result = False
            with outcome_lock:
                outcomes.append(("replay", result))

        def retire_session() -> None:
            gate.wait(timeout=5)
            with Session(engine) as session, session.begin():
                row = session.get(UserSessionRow, session_id, with_for_update=True)
                assert row is not None
                row.revoked_at = datetime.now(UTC)
            with outcome_lock:
                outcomes.append(("retire", True))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(replay), pool.submit(retire_session))
            for future in futures:
                future.result(timeout=10)

        assert {name for name, _ in outcomes} == {"replay", "retire"}
        if outcomes[0][0] == "retire":
            assert outcomes[1] == ("replay", False)
        with pytest.raises(PublicAccessError):
            service.redeem(registration, "session-race-after")
    finally:
        engine.dispose()


def test_pa2_account_disable_activates_existing_s1c_retirement_trigger(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation, _ = service.create_invitation(
            owner,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "account_display_name": "s1c-retirement",
                "expires_in_seconds": 600,
            },
        )
        response, _ = service.redeem(
            _request(invitation, ec.generate_private_key(ec.SECP256R1())),
            "s1c-retirement",
        )
        user_id = UUID(str(response["user_id"]))
        lower, higher = sorted((owner.user_id, user_id), key=lambda value: value.bytes)
        with Session(engine) as session, session.begin():
            session.add(
                PresenceSettingsRow(
                    user_id=user_id,
                    friend_presence_visibility_enabled=True,
                    room_activity_sharing_enabled=True,
                    invite_availability_enabled=True,
                    revision=1,
                    updated_at=datetime.now(UTC),
                )
            )
            session.add(
                FriendshipRow(
                    lower_user_id=lower,
                    higher_user_id=higher,
                    created_at=datetime.now(UTC),
                )
            )

        service.disable_account(
            owner,
            user_id,
            {
                "contract_version": "v1",
                "schema_version": 1,
                "operation_id": str(uuid4()),
                "reason_code": "ACCESS_ENDED",
            },
        )

        with Session(engine) as session:
            assert session.get(PresenceSettingsRow, user_id) is None
            assert (
                session.get(
                    FriendshipRow,
                    {"lower_user_id": lower, "higher_user_id": higher},
                )
                is None
            )
    finally:
        engine.dispose()


def test_invitation_cursor_has_no_gap_or_duplicate(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        for index in range(3):
            service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": f"cursor-{index}",
                    "expires_in_seconds": 600,
                },
            )
        first = service.list_invitations(owner, 2)
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        second = service.list_invitations(owner, 2, cursor)
        items = cast(list[object], first["items"]) + cast(list[object], second["items"])
        identifiers = [item["invitation_id"] for item in items if isinstance(item, dict)]
        assert len(identifiers) == len(set(identifiers)) == 3
    finally:
        engine.dispose()


def test_cleanup_has_one_global_bound_and_rejects_invalid_limits(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        for marker in range(3):
            database_connection.execute(
                """INSERT INTO account.account_provisioning_rate_window
                (rate_key_sha256,scope,window_started_at,expires_at,attempt_count)
                VALUES (%s,'REDEEM_SERVER',now()-interval '2 hours',now()-interval '1 hour',1)""",
                (bytes([marker + 1]) * 32,),
            )
        database_connection.commit()
        assert service.cleanup(limit=2) == 2
        with Session(engine) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM account.account_provisioning_rate_window")
                ).scalar_one()
                == 1
            )
        with pytest.raises(ValueError):
            service.cleanup(limit=0)
        with pytest.raises(ValueError):
            service.cleanup(limit=10_001)
    finally:
        engine.dispose()


def test_cleanup_removes_only_expired_or_cancelled_invitations_after_thirty_days(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        invitation_ids: list[UUID] = []
        for index in range(3):
            invitation, _ = service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": f"retention-{index}",
                    "expires_in_seconds": 600,
                },
            )
            invitation_ids.append(UUID(str(invitation["invitation_id"])))
        database_connection.execute(
            """UPDATE account.account_invitation
            SET issued_at=now()-interval '32 days', expires_at=now()-interval '31 days'
            WHERE invitation_id = ANY(%s)""",
            (invitation_ids[:2],),
        )
        database_connection.execute(
            """UPDATE account.account_invitation
            SET cancelled_at=now()-interval '31 days'
            WHERE invitation_id=%s""",
            (invitation_ids[1],),
        )
        database_connection.commit()

        assert service.cleanup(limit=1) == 1
        assert service.cleanup(limit=1) == 1
        with Session(engine) as session:
            remaining = set(
                session.scalars(text("SELECT invitation_id FROM account.account_invitation")).all()
            )
            assert remaining == {invitation_ids[2]}
    finally:
        engine.dispose()


def test_failed_active_invitation_cap_still_commits_owner_issue_rate_attempt(
    database_url: str, database_connection: Connection[object]
) -> None:
    service, engine = _service(database_url)
    try:
        owner, _ = _owner_and_server(database_connection)
        for index in range(5):
            service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": f"active-{index}",
                    "expires_in_seconds": 600,
                },
            )
        with pytest.raises(PublicAccessError) as failure:
            service.create_invitation(
                owner,
                {
                    "contract_version": "v1",
                    "schema_version": 1,
                    "operation_id": str(uuid4()),
                    "account_display_name": "over-cap",
                    "expires_in_seconds": 600,
                },
            )
        assert failure.value.code == "invitation_limit_reached"
        with Session(engine) as session:
            assert (
                session.execute(
                    text(
                        "SELECT attempt_count FROM account.account_provisioning_rate_window "
                        "WHERE scope='ISSUE_OWNER'"
                    )
                ).scalar_one()
                == 6
            )
    finally:
        engine.dispose()
