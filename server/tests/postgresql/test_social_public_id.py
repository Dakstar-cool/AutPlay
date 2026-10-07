"""Real PostgreSQL proof of opt-in exact public IDs and bounded private lookup."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    DeviceRow,
    FriendshipRow,
    PublicIdRegistrationRow,
    SocialOperationReceiptRow,
    SocialRateWindowRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionRequestRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import canonical_sha256, verify_p1363
from autplay.entrypoints.social_http import create_social_router
from autplay.runtime.http import ApiError, error_response
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from .conftest import DatabaseHarness
from .test_social_s1c import _device_principal, _instance, _principal


@dataclass
class Harness:
    sessions: sessionmaker[Session]
    service: SocialService
    first: Principal
    second: Principal
    other_device: Principal
    now: datetime


@pytest.fixture
def social(database_url: str) -> Iterator[Harness]:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    try:
        with sessions.begin() as s:
            first = _principal(s, "First private display name", now)
            second = _principal(s, "Second private display name", now)
            other = _device_principal(s, first.user_id, "First B", now)
            _instance(s, key, now)
        yield Harness(sessions, SocialService(sessions, key), first, second, other, now)
    finally:
        engine.dispose()


def _register(
    h: Harness, actor: Principal, public_id: str, operation_id: UUID | None = None
) -> dict[str, object]:
    return h.service.register_public_id(
        actor, {"operation_id": str(operation_id or uuid4()), "public_id": public_id}, h.now
    )


def test_registration_is_durable_canonical_and_bound_to_account_device(social: Harness) -> None:
    h = social
    assert h.service.get_public_id(h.first, h.now) == {
        "public_id": None,
        "status": "unregistered",
    }
    operation_id = uuid4()
    expected = {"public_id": "alice_1", "status": "confirmed"}
    assert _register(h, h.first, "Alice_1", operation_id) == expected
    fresh = SocialService(h.sessions, None)
    assert fresh.get_public_id(h.first, h.now) == expected
    assert (
        fresh.register_public_id(
            h.first, {"operation_id": str(operation_id), "public_id": "ALICE_1"}, h.now
        )
        == expected
    )
    assert _register(h, h.first, "alice_1") == expected
    for actor, public_id in (
        (h.first, "other_id"),
        (h.second, "alice_1"),
        (h.other_device, "alice_1"),
    ):
        with pytest.raises(SocialError, match=r"^operation_conflict$"):
            _register(h, actor, public_id, operation_id)
    with pytest.raises(SocialError, match=r"^public_id_already_registered$"):
        _register(h, h.first, "other_id")
    denied_operation = uuid4()
    for _ in range(2):
        with pytest.raises(SocialError, match=r"^public_id_taken$"):
            _register(h, h.second, "ALICE_1", denied_operation)
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(PublicIdRegistrationRow)) == 1
        receipt = s.get(SocialOperationReceiptRow, operation_id)
        assert receipt is not None
        assert receipt.expires_at == h.now + timedelta(days=30)
        denied = s.get(SocialOperationReceiptRow, denied_operation)
        assert denied is not None and denied.result_json == '{"error": "public_id_taken"}'
        assert str(h.first.user_id) not in denied.result_json


@pytest.mark.parametrize("race", ["two_accounts", "two_aliases", "same_operation"])
def test_concurrent_registration_has_one_authoritative_claim(social: Harness, race: str) -> None:
    h = social
    barrier = Barrier(2)
    shared_operation = uuid4()
    actors = (h.first, h.second) if race == "two_accounts" else (h.first, h.first)
    handles = ("Racer_1", "RACER_1") if race != "two_aliases" else ("racer_1", "racer_2")

    def claim(index: int) -> object:
        barrier.wait(timeout=10)
        try:
            return _register(
                h,
                actors[index],
                handles[index],
                shared_operation if race == "same_operation" else uuid4(),
            )
        except SocialError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (0, 1)))
    successes = [result for result in results if isinstance(result, dict)]
    assert len(successes) == (2 if race == "same_operation" else 1)
    if race != "same_operation":
        assert (
            "public_id_taken" if race == "two_accounts" else "public_id_already_registered"
        ) in results
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(PublicIdRegistrationRow)) == 1
        assert s.scalar(select(func.count()).select_from(SocialOperationReceiptRow)) == (
            1 if race == "same_operation" else 2
        )


def test_database_constraints_remain_final_authority(social: Harness) -> None:
    h = social
    _register(h, h.first, "canonical_1")
    for public_id in ("canonical_1", "CANONICAL_1", "@canonical_1", "ab", "\u0430lice_1"):
        with pytest.raises(IntegrityError), h.sessions.begin() as s:
            s.add(PublicIdRegistrationRow(user_id=h.second.user_id, public_id=public_id))
    with h.sessions.begin() as s:
        first_session = s.get(UserSessionRow, h.first.session_id)
        assert first_session is not None
        first_session.revoked_at = h.now
    with pytest.raises(SocialError, match=r"^auth_attention_required$"):
        h.service.lookup_public_id(h.first, "canonical_1", h.now)


@pytest.mark.parametrize("action", ["SEND_REQUEST", "SET_PRESENCE_SETTINGS"])
@pytest.mark.parametrize("same_actor", [True, False])
def test_shared_operation_id_serializes_cross_action_and_cross_owner_races(
    social: Harness, action: str, same_actor: bool
) -> None:
    h = social
    operation_id = uuid4()
    barrier = Barrier(2)
    actor = h.first if same_actor else h.second
    target = h.second if same_actor else h.first
    card = h.service.contact_card(target, h.now)

    def command(index: int) -> object:
        barrier.wait(timeout=10)
        try:
            if index == 0:
                return _register(h, h.first, "first_1", operation_id)
            if action == "SEND_REQUEST":
                return h.service.command(
                    actor,
                    {"operation_id": str(operation_id), "action": action, "contact_card": card},
                    h.now,
                )
            return h.service.set_settings(
                actor,
                {
                    "operation_id": str(operation_id),
                    "friend_presence_visibility_enabled": True,
                    "room_activity_sharing_enabled": False,
                    "invite_availability_enabled": False,
                },
                h.now,
            )
        except SocialError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(command, (0, 1)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "operation_conflict" in results
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(SocialOperationReceiptRow)) == 1
        count = s.scalar(select(func.count()).select_from(PublicIdRegistrationRow))
        assert count == (1 if isinstance(results[0], dict) else 0)


def test_exact_lookup_returns_signed_card_and_preserves_explicit_friend_acceptance(
    social: Harness,
) -> None:
    h = social
    _register(h, h.first, "first_1")
    _register(h, h.second, "second_1")
    card = h.service.lookup_public_id(h.first, "SECOND_1", h.now)
    signed = dict(card)
    signature = str(signed.pop("signature_b64url"))
    with h.sessions() as s:
        instance = s.scalar(select(ServerInstanceRow))
        assert instance is not None
        verify_p1363(
            instance.identity_public_key_spki,
            "autplay:s1c:social-contact-card:v1\n",
            canonical_sha256(signed),
            signature,
        )
    assert set(card) == {
        "server_instance_id",
        "account_id",
        "display_name_hint",
        "issued_at",
        "expires_at",
        "signature_b64url",
    }
    assert card["account_id"] == str(h.second.user_id)
    assert h.service.snapshot(h.first, h.now)["friends"] == []
    for actor, target_id in ((h.first, "second_1"), (h.second, "first_1")):
        result = h.service.command(
            actor,
            {
                "operation_id": str(uuid4()),
                "action": "SEND_REQUEST",
                "contact_card": h.service.lookup_public_id(actor, target_id, h.now),
            },
            h.now,
        )
        assert result["state"] == "PENDING_OUTGOING"
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(FriendshipRow)) == 0
    accepted = h.service.command(
        h.second,
        {
            "operation_id": str(uuid4()),
            "action": "ACCEPT_REQUEST",
            "target_account_id": str(h.first.user_id),
        },
        h.now,
    )
    assert accepted["state"] == "MUTUAL"


@pytest.mark.parametrize("blocker", ["viewer", "target"])
def test_blocks_in_either_direction_hide_exact_lookup(social: Harness, blocker: str) -> None:
    h = social
    _register(h, h.second, "second_1")
    actor, target = (h.first, h.second) if blocker == "viewer" else (h.second, h.first)
    h.service.command(
        actor,
        {
            "operation_id": str(uuid4()),
            "action": "BLOCK_USER",
            "target_account_id": str(target.user_id),
        },
        h.now,
    )
    for public_id in ("second_1", "missing_1", "second", "second*", "@second_1"):
        with pytest.raises(SocialError, match=r"^public_id_not_found$") as failure:
            h.service.lookup_public_id(h.first, public_id, h.now)
        assert failure.value.details is None


@pytest.mark.parametrize("state", ["DISABLED", "DELETION_PENDING", "deleted"])
def test_inactive_targets_are_hidden_and_id_is_not_reassigned(social: Harness, state: str) -> None:
    h = social
    _register(h, h.second, "second_1")
    with h.sessions.begin() as s:
        row = s.get(UserAccountRow, h.second.user_id)
        assert row is not None
        if state == "deleted":
            row.deleted_at = h.now
        else:
            row.status = state
            if state == "DELETION_PENDING":
                instance = s.scalar(select(ServerInstanceRow))
                assert instance is not None
                row.authority_generation += 1
                s.add(
                    AccountDeletionRequestRow(
                        deletion_request_id=uuid4(),
                        user_id=h.second.user_id,
                        server_instance_id=instance.server_instance_id,
                        identity_epoch=instance.identity_epoch,
                        identity_thumbprint_sha256=instance.identity_thumbprint_sha256,
                        request_sha256=b"r" * 32,
                        actor_device_id=h.second.device_id,
                        actor_public_key_spki=instance.identity_public_key_spki,
                        code_generation=1,
                        code_verifier_sha256=b"v" * 32,
                        authority_generation=row.authority_generation,
                        requested_at=h.now,
                        cancel_before=h.now + timedelta(days=30),
                        state="PENDING",
                        revision=1,
                    )
                )
    with pytest.raises(SocialError, match=r"^public_id_not_found$"):
        h.service.lookup_public_id(h.first, "second_1", h.now)
    with pytest.raises(SocialError, match=r"^public_id_taken$"):
        _register(h, h.first, "second_1")


@pytest.mark.parametrize(
    "invalid", ["disabled", "session_revoked", "expired", "device_revoked", "foreign_session"]
)
def test_mutable_auth_is_revalidated_for_get_put_lookup_and_replay(
    social: Harness, invalid: str
) -> None:
    h = social
    operation_id = uuid4()
    _register(h, h.first, "first_1", operation_id)
    actor = h.first
    with h.sessions.begin() as s:
        account = s.get(UserAccountRow, h.first.user_id)
        session = s.get(UserSessionRow, h.first.session_id)
        device = s.get(DeviceRow, h.first.device_id)
        assert account is not None and session is not None and device is not None
        if invalid == "disabled":
            account.status = "DISABLED"
        elif invalid == "session_revoked":
            session.revoked_at = h.now
        elif invalid == "expired":
            session.expires_at = h.now + timedelta(seconds=1)
            h.now += timedelta(seconds=1)
        elif invalid == "device_revoked":
            device.revoked_at = h.now
        else:
            actor = Principal(h.first.user_id, h.first.device_id, h.second.session_id, h.first.role)
    actions: tuple[Callable[[], object], ...] = (
        lambda: h.service.get_public_id(actor, h.now),
        lambda: _register(h, actor, "first_1", operation_id),
        lambda: h.service.lookup_public_id(actor, "missing_1", h.now),
    )
    for action in actions:
        with pytest.raises(SocialError, match=r"^auth_attention_required$"):
            action()


def test_negative_lookup_budget_is_durable_and_expires(social: Harness) -> None:
    h = social
    for _ in range(30):
        with pytest.raises(SocialError, match=r"^public_id_not_found$"):
            h.service.lookup_public_id(h.first, "missing_1", h.now)
    with pytest.raises(SocialError, match=r"^rate_limited$"):
        h.service.lookup_public_id(h.first, "missing_2", h.now)
    with h.sessions() as s:
        row = s.scalar(
            select(SocialRateWindowRow).where(SocialRateWindowRow.scope == "PUBLIC_ID_LOOKUP")
        )
        assert row is not None and row.attempt_count == 30
    with pytest.raises(SocialError, match=r"^public_id_not_found$"):
        h.service.lookup_public_id(h.first, "missing_2", h.now + timedelta(minutes=15))


def test_denied_registration_budget_and_successful_replay(social: Harness) -> None:
    h = social
    operation_id = uuid4()
    _register(h, h.first, "first_1", operation_id)
    for _ in range(9):
        with pytest.raises(SocialError, match=r"^public_id_already_registered$"):
            _register(h, h.first, "other_1")
    with pytest.raises(SocialError, match=r"^rate_limited$"):
        _register(h, h.first, "other_2")
    assert _register(h, h.first, "first_1", operation_id)["status"] == "confirmed"


def test_own_registration_read_budget_is_bounded(social: Harness) -> None:
    h = social
    for _ in range(60):
        assert h.service.get_public_id(h.first, h.now)["status"] == "unregistered"
    with pytest.raises(SocialError, match=r"^rate_limited$"):
        h.service.get_public_id(h.first, h.now)


def test_account_hard_delete_cascades_only_registration(social: Harness) -> None:
    h = social
    _register(h, h.second, "second_1")
    with h.sessions.begin() as s:
        s.execute(
            delete(SocialOperationReceiptRow).where(
                SocialOperationReceiptRow.actor_user_id == h.second.user_id
            )
        )
        s.execute(delete(UserSessionRow).where(UserSessionRow.user_id == h.second.user_id))
        s.execute(delete(DeviceRow).where(DeviceRow.user_id == h.second.user_id))
        s.execute(delete(UserAccountRow).where(UserAccountRow.user_id == h.second.user_id))
    with h.sessions() as s:
        assert s.get(PublicIdRegistrationRow, h.second.user_id) is None
        assert s.get(UserAccountRow, h.first.user_id) is not None
    with pytest.raises(SocialError, match=r"^public_id_not_found$"):
        h.service.lookup_public_id(h.first, "second_1", h.now)


def test_forward_migration_preserves_accounts_and_guards_data_bearing_downgrade(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    harness, db = database_harness, empty_database_name
    harness.upgrade(db, "0062_cpu_worker_health")
    with harness.connect(db) as c:
        row = c.execute(
            "INSERT INTO account.user_account(display_name) VALUES ('Existing') "
            "RETURNING user_id,display_name,row_version"
        ).fetchone()
        assert row is not None
        c.commit()
    harness.upgrade(db, "0063_social_public_id")
    with harness.connect(db) as c:
        assert (
            c.execute(
                "SELECT user_id,display_name,row_version FROM account.user_account"
            ).fetchone()
            == row
        )
        assert c.execute("SELECT count(*) FROM social.public_id_registration").fetchone() == (0,)
        c.execute(
            "INSERT INTO social.public_id_registration(user_id,public_id) VALUES (%s,'existing_1')",
            (row[0],),
        )
        c.commit()
    with pytest.raises(DBAPIError, match="refusing public ID downgrade"):
        harness.downgrade(db, "0062_cpu_worker_health")
    with harness.connect(db) as c:
        assert c.execute("SELECT public_id FROM social.public_id_registration").fetchone() == (
            "existing_1",
        )
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0063_social_public_id",
        )


def _client(h: Harness) -> TestClient:
    app = FastAPI()

    def authenticated(request: Request) -> None:
        if request.headers.get("Authorization") != "Bearer test-session":
            raise ApiError("auth_attention_required", "Authentication required.", 401)
        request.state.principal = h.first

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, error: ApiError) -> object:
        return error_response(
            request_id="test-request",
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            retryable=error.retryable,
            headers=error.headers,
            details=error.details,
        )

    app.include_router(
        create_social_router(h.service, authenticated=authenticated), prefix="/api/v1"
    )
    return TestClient(app)


def test_http_auth_exact_errors_privacy_and_no_account_directory(social: Harness) -> None:
    h = social
    client = _client(h)
    prefix = "/api/v1/social"
    for method, path, body in (
        ("GET", "/public-id", None),
        ("PUT", "/public-id", {"operation_id": str(uuid4()), "public_id": "first_1"}),
        ("GET", "/accounts/by-public-id/second_1", None),
    ):
        r = client.request(method, prefix + path, json=body)
        assert r.status_code == 401
        assert r.headers["cache-control"] == "private, no-store, max-age=0"
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(SocialRateWindowRow)) == 0
    client.headers["Authorization"] = "Bearer test-session"
    own = client.get(prefix + "/public-id")
    assert own.json() == {"public_id": None, "status": "unregistered"}
    assert client.put(
        prefix + "/public-id", json={"operation_id": str(uuid4()), "public_id": "First_1"}
    ).json() == {"public_id": "first_1", "status": "confirmed"}
    _register(h, h.second, "second_1")
    assert client.get(prefix + "/accounts/by-public-id/SECOND_1").status_code == 200
    h.service.command(
        h.second,
        {
            "operation_id": str(uuid4()),
            "action": "BLOCK_USER",
            "target_account_id": str(h.first.user_id),
        },
        h.now,
    )
    errors = [
        client.get(prefix + "/accounts/by-public-id/" + value)
        for value in ("second_1", "missing_1", "second", "second*", "@second_1")
    ]
    assert all(r.status_code == 404 for r in errors)
    assert all(r.json() == errors[0].json() for r in errors)
    assert errors[0].json()["error"]["code"] == "public_id_not_found"
    assert set(errors[0].json()["error"]) == {"code", "message", "retryable", "request_id"}
    for response in [own, *errors]:
        assert response.headers["cache-control"] == "private, no-store, max-age=0"
        assert response.headers["vary"] == "Authorization"
        assert str(h.second.user_id) not in response.text
        assert "Second private display name" not in response.text
    for path in ("/accounts", "/accounts/by-public-id", "/public-ids", "/accounts/search"):
        assert client.get(prefix + path).status_code == 404
    invalid = client.put(
        prefix + "/public-id",
        json={"operation_id": str(uuid4()), "public_id": "x", "user_id": str(h.second.user_id)},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_validation_failed"
    assert str(h.second.user_id) not in invalid.text


def test_http_public_id_rejects_whitespace_unicode_and_non_string_values(social: Harness) -> None:
    client = _client(social)
    client.headers["Authorization"] = "Bearer test-session"
    for value in (
        "abc\n",
        "abc ",
        " abc",
        "@abc",
        "ab",
        "a" * 25,
        "\u0430lice",
        "abc-1",
        123,
        None,
    ):
        response = client.put(
            "/api/v1/social/public-id", json={"operation_id": str(uuid4()), "public_id": value}
        )
        assert response.status_code == 422, value
        assert response.json()["error"]["code"] == "request_validation_failed"
    with social.sessions() as s:
        assert s.scalar(select(func.count()).select_from(PublicIdRegistrationRow)) == 0
        assert s.scalar(select(func.count()).select_from(SocialRateWindowRow)) == 0
