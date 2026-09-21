"""Real PostgreSQL and P-256 proof tests for one-time browser passkeys."""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
from autplay.adapters.postgresql.web_passkeys import SqlAlchemyWebPasskeyUnitOfWorkFactory
from autplay.adapters.webauthn import DuoWebPasskeyVerifier
from autplay.application.web_admin import WebAdminService
from autplay.application.web_passkeys import PasskeyOptions, WebPasskeyService
from autplay.domain.web_admin import WebAdminError, WebSessionCredentials
from autplay.entrypoints.admin import run_web_passkey_recovery
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from test_webauthn import ORIGIN, _vector

from .test_web_admin_m6 import _seed_owner

SECRET = b"passkey-tests-csrf-secret-distinct-and-long"
PREAUTH = b"p" * 43
NONCE = b"n" * 43


@dataclass
class Harness:
    engine: Engine
    web: WebAdminService
    passkeys: WebPasskeyService
    bootstrap: WebSessionCredentials


@pytest.fixture
def harness(database_url: str, database_connection: Connection[object]) -> Iterator[Harness]:
    user_id, _ = _seed_owner(database_connection)
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    web = WebAdminService(SqlAlchemyWebAdminUnitOfWorkFactory(sessions), SECRET)
    invitation = web.issue_invitation(user_id)
    bootstrap = web.login(web.begin_login(), invitation.bearer, b"b" * 32)
    passkeys = WebPasskeyService(
        SqlAlchemyWebPasskeyUnitOfWorkFactory(sessions),
        DuoWebPasskeyVerifier(ORIGIN),
        SECRET,
    )
    try:
        yield Harness(engine, web, passkeys, bootstrap)
    finally:
        engine.dispose()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _register(harness: Harness) -> tuple[UUID, ec.EllipticCurvePrivateKey, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    options = harness.passkeys.begin_registration(harness.bootstrap.actor, uuid4())
    document = json.loads(options.options_json)
    handle = _decode(document["user"]["id"])
    payload = _vector(key, registration=True, challenge=_decode(document["challenge"]))
    passkey_id = harness.passkeys.finish_registration(
        harness.bootstrap.actor,
        options.ceremony_id,
        options.operation_id,
        payload,
        "Laptop",
    )
    repeated = harness.passkeys.finish_registration(
        harness.bootstrap.actor,
        options.ceremony_id,
        options.operation_id,
        payload,
        "Laptop",
    )
    assert repeated == passkey_id
    return passkey_id, key, handle


def _assertion(
    harness: Harness,
    key: ec.EllipticCurvePrivateKey,
    handle: bytes,
) -> tuple[PasskeyOptions, str]:
    options = harness.passkeys.begin_login(PREAUTH, NONCE)
    challenge = _decode(json.loads(options.options_json)["challenge"])
    return options, _vector(key, registration=False, challenge=challenge, handle=handle, counter=2)


def _finish(harness: Harness, options: PasskeyOptions, payload: str) -> WebSessionCredentials:
    return harness.passkeys.finish_login(
        options.ceremony_id,
        options.operation_id,
        PREAUTH,
        NONCE,
        payload,
    )


def test_passkey_login_uses_m6_csrf_rotation_and_bound_revocation(harness: Harness) -> None:
    passkey_id, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    logged_in = _finish(harness, options, payload)
    authenticated = harness.web.authenticate(logged_in.bearer, mutation=True)
    assert authenticated.actor.user_id == harness.bootstrap.actor.user_id
    assert authenticated.csrf == logged_in.csrf
    harness.web.validate_csrf(authenticated.actor, logged_in.csrf, uuid4())
    rotated = harness.web.authenticate_safe_get(
        logged_in.bearer,
        now=datetime.now(UTC) + timedelta(minutes=16),
    )
    assert rotated.rotated_bearer is not None
    operation = uuid4()
    harness.passkeys.revoke(harness.bootstrap.actor, passkey_id, operation, b"r" * 32)
    harness.passkeys.revoke(harness.bootstrap.actor, passkey_id, operation, b"r" * 32)
    with pytest.raises(WebAdminError, match="authentication_required"):
        harness.web.authenticate(rotated.rotated_bearer, mutation=True)
    assert harness.web.authenticate(harness.bootstrap.bearer, mutation=True)


def test_concurrent_login_and_replay_create_exactly_one_session(harness: Harness) -> None:
    _, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)

    def attempt(_: int) -> str:
        try:
            _finish(harness, options, payload)
            return "created"
        except WebAdminError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["browser_login_outcome_unknown", "created"]
    with harness.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.web_session")) == 2
        assert connection.scalar(text("SELECT sign_count FROM account.web_passkey")) == 2


def test_registration_start_replays_concurrently_and_binds_the_browser(harness: Harness) -> None:
    operation = uuid4()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: harness.passkeys.begin_registration(harness.bootstrap.actor, operation),
                range(2),
            )
        )
    assert results[0] == results[1]
    invitation = harness.web.issue_invitation(harness.bootstrap.actor.user_id)
    other = harness.web.login(harness.web.begin_login(), invitation.bearer, b"o" * 32)
    with pytest.raises(WebAdminError, match="operation_conflict"):
        harness.passkeys.begin_registration(other.actor, operation)


def test_passkey_revocation_conflicts_with_other_browser_operations(harness: Harness) -> None:
    passkey_id, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    logged_in = _finish(harness, options, payload)
    operation = uuid4()
    harness.web.revoke_browser_session(
        harness.bootstrap.actor,
        logged_in.actor.web_session_id,
        operation,
        b"x" * 32,
    )
    with pytest.raises(WebAdminError, match="operation_conflict"):
        harness.passkeys.revoke(harness.bootstrap.actor, passkey_id, operation, b"r" * 32)
    assert harness.passkeys.list_local(harness.bootstrap.actor.user_id)[0].revoked_at is None
    new_operation = uuid4()
    harness.passkeys.revoke(harness.bootstrap.actor, passkey_id, new_operation, b"r" * 32)
    with pytest.raises(WebAdminError, match="operation_conflict"):
        harness.web.logout_current(harness.bootstrap.actor, new_operation, b"x" * 32)
    assert harness.web.authenticate(harness.bootstrap.bearer, mutation=True)


def test_active_key_remains_visible_after_long_revocation_history(harness: Harness) -> None:
    passkey_id, _, _ = _register(harness)
    with harness.engine.begin() as connection:
        for index in range(40):
            connection.execute(
                text("""
                INSERT INTO account.web_passkey
                  (passkey_id, server_instance_id, user_id, user_handle, credential_id, public_key,
                   sign_count, backup_eligible, backed_up, label, created_at, revoked_at)
                SELECT :id, server_instance_id, user_id, user_handle, :credential, public_key,
                   0, false, false, 'Revoked fixture', now(), now()
                FROM account.web_passkey WHERE passkey_id = :active
            """),
                {"id": uuid4(), "active": passkey_id, "credential": index.to_bytes(32, "big")},
            )
    rows = harness.passkeys.list_local(harness.bootstrap.actor.user_id)
    assert len(rows) == 32 and rows[0].passkey_id == passkey_id and rows[0].revoked_at is None


def test_local_passkey_recovery_lists_revokes_and_cleans_bounded_evidence(harness: Harness) -> None:
    passkey_id, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    signed_in = _finish(harness, options, payload)
    user_id = str(harness.bootstrap.actor.user_id)

    def command(arguments: list[str]) -> dict[str, object]:
        output, error = io.StringIO(), io.StringIO()
        assert (
            run_web_passkey_recovery(
                harness.passkeys,
                arguments,
                stdout=output,
                stderr=error,
            )
            == 0
        )
        assert not error.getvalue()
        assert "public_key" not in output.getvalue() and "credential_id" not in output.getvalue()
        result: dict[str, object] = json.loads(output.getvalue())
        return result

    listed = command(["web-passkey-list", "--user-id", user_id])
    assert isinstance(listed["passkeys"], list) and len(listed["passkeys"]) == 1
    revoke = [
        "web-passkey-revoke",
        "--user-id",
        user_id,
        "--passkey-id",
        str(passkey_id),
        "--operation-id",
        str(uuid4()),
    ]
    assert command(revoke) == command(revoke) == {"outcome": "PASSKEY_REVOKED"}
    with pytest.raises(WebAdminError, match="authentication_required"):
        harness.web.authenticate(signed_in.bearer, mutation=True)
    with harness.engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE account.web_passkey_ceremony
            SET created_at=created_at-interval '2 days', expires_at=expires_at-interval '2 days'
        """)
        )
    assert command(["web-passkey-cleanup", "--limit", "1"]) == {"deleted": 1}
    assert command(["web-passkey-cleanup", "--limit", "1"]) == {"deleted": 1}
    assert command(["web-passkey-cleanup", "--limit", "1"]) == {"deleted": 0}


def test_wrong_preauth_does_not_spend_valid_challenge(harness: Harness) -> None:
    _, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    with pytest.raises(WebAdminError, match="passkey_invalid"):
        harness.passkeys.finish_login(
            options.ceremony_id,
            options.operation_id,
            b"q" * 43,
            NONCE,
            payload,
        )
    assert _finish(harness, options, payload).actor.user_id == harness.bootstrap.actor.user_id


def test_revoked_bootstrap_cannot_complete_registration(harness: Harness) -> None:
    options = harness.passkeys.begin_registration(harness.bootstrap.actor, uuid4())
    challenge = _decode(json.loads(options.options_json)["challenge"])
    key = ec.generate_private_key(ec.SECP256R1())
    harness.web.revoke_all_browser_sessions_local(harness.bootstrap.actor.user_id, uuid4())
    with pytest.raises(WebAdminError, match="authentication_required"):
        harness.passkeys.finish_registration(
            harness.bootstrap.actor,
            options.ceremony_id,
            options.operation_id,
            _vector(key, registration=True, challenge=challenge),
            "Phone",
        )
    with harness.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.web_passkey")) == 0


def test_account_disable_and_expired_ceremony_reject_login(harness: Harness) -> None:
    _, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    with pytest.raises(WebAdminError, match="passkey_invalid"):
        harness.passkeys.finish_login(
            options.ceremony_id,
            options.operation_id,
            PREAUTH,
            NONCE,
            payload,
            now=datetime.now(UTC) + timedelta(minutes=6),
        )
    with harness.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO account.user_account(user_id,display_name,role) "
                "VALUES (:id,'Replacement owner','OWNER')"
            ),
            {"id": uuid4()},
        )
        connection.execute(
            text("UPDATE account.user_account SET status='DISABLED' WHERE user_id=:user"),
            {"user": harness.bootstrap.actor.user_id},
        )
    with pytest.raises(WebAdminError, match="passkey_invalid"):
        _finish(harness, options, payload)


def test_registration_revocation_and_credential_uniqueness(harness: Harness) -> None:
    passkey_id, key, _ = _register(harness)
    harness.passkeys.revoke_local(harness.bootstrap.actor.user_id, passkey_id, uuid4())
    with pytest.raises(WebAdminError, match="passkey_invalid"):
        harness.passkeys.finish_login(
            uuid4(),
            uuid4(),
            PREAUTH,
            NONCE,
            _vector(key, registration=False, counter=2),
        )
    options = harness.passkeys.begin_registration(harness.bootstrap.actor, uuid4())
    challenge = _decode(json.loads(options.options_json)["challenge"])
    with pytest.raises(WebAdminError, match="passkey_invalid"):
        harness.passkeys.finish_registration(
            harness.bootstrap.actor,
            options.ceremony_id,
            options.operation_id,
            _vector(key, registration=True, challenge=challenge),
            "Same credential",
        )
    assert harness.passkeys.list_passkeys(harness.bootstrap.actor)[0].revoked_at is not None


def test_failed_session_admission_rolls_back_counter_and_challenge(harness: Harness) -> None:
    _, key, handle = _register(harness)
    options, payload = _assertion(harness, key, handle)
    with harness.engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION account.reject_passkey_session() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test injected failure'; END $$;
            CREATE TRIGGER test_reject_session BEFORE INSERT ON account.web_session
            FOR EACH ROW EXECUTE FUNCTION account.reject_passkey_session();
        """)
        )
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        _finish(harness, options, payload)
    with harness.engine.begin() as connection:
        assert connection.scalar(text("SELECT sign_count FROM account.web_passkey")) == 1
        connection.execute(text("DROP TRIGGER test_reject_session ON account.web_session"))
    assert _finish(harness, options, payload).actor.user_id == harness.bootstrap.actor.user_id
