"""UNEXECUTED real API/PG B1 fixtures; both independent enrolled platforms.

Synthetic qualification is confined to an explicit test fixture. The shipped
registry remains EMPTY. No test/import/SQL/signature/hash was executed here.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.readiness import ReadinessResult
from autplay.application.sync_binding_v2 import SyncBindingV2Service
from autplay.domain.auth import Principal
from autplay.domain.device_capability_state import DeviceCapabilityPolicy
from autplay.domain.profile_pairing import (
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    verify_p1363,
)
from autplay.domain.sync_binding_v2 import (
    CAPABILITIES_DOMAIN,
    DISCOVERY_DOMAIN,
)
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_sync_binding_v2_service
from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from typed_assertions import require_instance

from .test_device_profile_v2 import ENROLL_DOMAIN, ProfileHarness, signed, snapshot
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service

PREFIX = "/api/v1/sync-binding/v2"
D1 = "/api/v1/device-compatibility/v2"


@dataclass
class BindingHarness:
    profile: ProfileHarness
    client: TestClient
    settings: ApiSettings
    engine: Engine
    actor: Principal
    token: str
    key: ec.EllipticCurvePrivateKey
    enrollment: dict[str, Any]
    binding: dict[str, Any]
    refresh: str
    policy_source: list[DeviceCapabilityPolicy]

    def policy(self) -> DeviceCapabilityPolicy:
        current = self.policy_source[0]
        owner = require_instance(self.client.app, FastAPI).state.deletion_evidence
        return replace(
            current, d1=replace(current.d1, deletion_evidence_ready=owner.evidence_ready())
        )

    def set_policy(
        self, *, binding_enabled: bool | None = None, d1_enabled: bool | None = None
    ) -> None:
        current = self.policy()
        self.policy_source[0] = replace(
            current,
            binding_enabled=current.binding_enabled if binding_enabled is None else binding_enabled,
            d1=current.d1 if d1_enabled is None else replace(current.d1, enabled=d1_enabled),
        )

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}

    def capabilities(self) -> dict[str, Any]:
        response = self.client.get(PREFIX + "/capabilities", headers=self.headers())
        assert response.status_code == 200, response.json()
        return require_instance(response.json()["payload"], dict)

    def request(self, **changes: Any) -> dict[str, Any]:
        view = self.capabilities()
        return {
            "contract_version": "v2",
            "schema_version": 2,
            "binding_contract_version": 2,
            "protocol_version": 1,
            "user_id": str(self.actor.user_id),
            "device_id": str(self.actor.device_id),
            "server_profile_id": str(uuid4()),
            "journal_epoch": str(uuid4()),
            "supported_event_schema_versions": [1],
            "expected_capability_revision": view["capability_revision"],
            **{
                "expected_" + name: view[name]
                for name in (
                    "server_instance_id",
                    "identity_epoch",
                    "identity_thumbprint_sha256",
                    "api_origin",
                    "stream_origin",
                )
            },
            **changes,
        }

    def post(self, body: dict[str, Any]) -> Response:
        return self.client.post(
            PREFIX + "/bind", content=rfc8785.dumps(body), headers=self.headers()
        )

    def service(self) -> SyncBindingV2Service:
        result = build_sync_binding_v2_service(
            self.settings,
            self.engine,
            identity=self.profile.identity,
            deletion_evidence=require_instance(self.client.app, FastAPI).state.deletion_evidence,
        )
        assert result is not None
        return result


@pytest.fixture
def binding(
    profile: ProfileHarness, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[BindingHarness]:
    set_device_qualification(monkeypatch)
    monkeypatch.setattr("autplay.application.sync_binding_v2._now", lambda: profile.clock["now"])
    monkeypatch.setattr(
        "autplay.application.device_compatibility._now", lambda: profile.clock["now"]
    )
    monkeypatch.setattr(
        "autplay.adapters.system.SystemClock.now", lambda self: profile.clock["now"]
    )
    # Use one original signature once; the resulting live principal is enrolled
    # by the real D1 source service, never by a keyless bootstrap or JWT-only stub.
    original, key, refresh = profile.enrollment()
    raw = signed(original, key, ENROLL_DOMAIN)
    enrolled, replayed = profile.service.enrollment_exchange(raw)
    assert not replayed
    actor = Principal(
        profile.owner.user_id,
        UUID(require_instance(enrolled["device_id"], str)),
        UUID(require_instance(enrolled["session_id"], str)),
        profile.owner.role,
    )
    # Enrollment samples PostgreSQL time for the actual credential. Continue
    # from that authority's clock, rather than predating it with the M5 fixture.
    with require_instance(profile.identity._sessions.kw["bind"], Engine).connect() as connection:
        now = connection.scalar(select(func.clock_timestamp()))
        assert isinstance(now, datetime)
        profile.clock["now"] = now
    token = require_instance(enrolled["access_token"], str)
    settings = ApiSettings(
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr("m5b-test-access-secret-at-least-thirty-two-bytes"),
        public_access_source_hmac_secret=SecretStr(
            "b1-synthetic-source-secret-at-least-thirty-two-bytes"
        ),
        auth_issuer="m5b",
        auth_audience="m5b",
        access_token_ttl_seconds=600,
        device_compatibility_v2_enabled=True,
        sync_binding_v2_enabled=True,
        privacy_ledger_path=tmp_path / "profile-reservations.sqlite3",
        privacy_ledger_key=SecretStr("d1-profile-ledger-test-secret-32bytes"),
        privacy_ledger_key_id="fixture-v1",
    )
    from autplay.entrypoints import composition

    original_policy = composition._device_capability_policy
    policy_source = [original_policy(settings)]

    # Both real GET producers and B1 bind read the SAME mutable policy source
    # already supported by their Callable port. ApiSettings stays frozen; no
    # client, DB, M5 identity, revision row or authentication path is replaced.
    def current_policy(
        current: ApiSettings, deletion_evidence: DeletionEvidenceRuntime | None = None
    ) -> DeviceCapabilityPolicy:
        live = original_policy(current, deletion_evidence)
        if current is not settings:
            return live
        controlled = policy_source[0]
        return replace(
            controlled,
            d1=replace(controlled.d1, deletion_evidence_ready=live.d1.deletion_evidence_ready),
        )

    monkeypatch.setattr(composition, "_device_capability_policy", current_policy)

    class Ready:
        def check(self) -> ReadinessResult:
            return ReadinessResult(True, "postgresql")

    application = create_app(
        settings, profile_pairing_service=profile.identity, readiness_probe=Ready()
    )
    engine = profile.identity._sessions.kw["bind"]
    with TestClient(application, follow_redirects=False) as client:
        yield BindingHarness(
            profile,
            client,
            settings,
            engine,
            actor,
            token,
            key,
            original,
            enrolled,
            refresh,
            policy_source,
        )


@pytest.fixture
def qualified(binding: BindingHarness, monkeypatch: pytest.MonkeyPatch) -> BindingHarness:
    monkeypatch.setattr(
        "autplay.domain.device_capability_state.IMPLEMENTED_AND_QUALIFIED_PLATFORMS",
        frozenset({"ANDROID", "MACOS"}),
    )
    monkeypatch.setattr(
        "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES",
        frozenset({"enrollment", "session"}),
    )
    monkeypatch.setattr(
        "autplay.domain.device_capability_state.IMPLEMENTED_AND_QUALIFIED_FAMILIES",
        frozenset({"enrollment", "session"}),
    )
    return binding


def cursors(connection: Connection[Any]) -> list[Any]:
    result = connection.execute(
        "SELECT * FROM sync.device_sync_cursor ORDER BY device_id"
    ).fetchall()
    connection.commit()
    return result


def assert_error(response: Any, code: str, status: int) -> None:
    assert response.status_code == status, response.json()
    detail = response.json()["error"]
    assert detail["code"] == code and detail["retryable"] is (
        code in {"sync_binding_unavailable", "sync_binding_capability_changed"}
    )
    expected = {"code", "message", "retryable", "request_id"}
    if code == "DEVICE_RESET_REQUIRED":
        expected.add("bootstrap_required")
        assert detail["bootstrap_required"] is True
    assert set(detail) == expected
    assert (
        response.headers["Cache-Control"] == "no-store" and response.headers["Pragma"] == "no-cache"
    )


def test_real_api_empty_qualification_false_signed_views_and_closed_registry(
    binding: BindingHarness,
) -> None:
    for path, domain in (("discovery", DISCOVERY_DOMAIN), ("capabilities", CAPABILITIES_DOMAIN)):
        response = binding.client.get(PREFIX + "/" + path, headers=binding.headers())
        assert response.status_code == 200
        envelope, payload = response.json(), response.json()["payload"]
        assert payload["binding_available"] is False and payload["platforms"] == []
        digest = canonical_sha256(payload)
        assert digest.hex() == envelope["payload_sha256"]
        verify_p1363(
            base64.b64decode(payload["identity_public_key_spki_b64"], validate=True),
            domain,
            digest,
            envelope["signature_b64url"],
        )
    body = binding.request()
    assert_error(binding.post(body), "sync_binding_unavailable", 503)
    for path in ("/bind/", "/unknown", "/capabilities/"):
        assert binding.client.post(PREFIX + path, json={}).status_code == 404
    assert binding.client.get(PREFIX + "/bind").status_code == 405


def test_first_commit_at_device_limit_readback_checkpoint_alias_disable_and_no_metadata_writes(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    database_connection.execute(
        "UPDATE account.resource_quota_policy SET default_devices=1,revision=revision+1"
    )
    database_connection.commit()
    body = h.request()
    before = snapshot(database_connection)
    first = h.post(body)
    assert first.status_code == 200 and first.json()["state"] == "BOUND"
    assert first.json()["platform"] == h.profile.platform
    assert set(first.json()) == {
        "contract_version",
        "schema_version",
        "binding_contract_version",
        "protocol_version",
        "state",
        "supported_event_schema_versions",
        "user_id",
        "device_id",
        "server_profile_id",
        "journal_epoch",
        "platform",
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
    }
    assert snapshot(database_connection) == before
    database_connection.execute(
        "UPDATE sync.device_sync_cursor SET "
        "last_acked_device_sequence=7,last_pulled_server_sequence=11,"
        "last_successful_sync_at=%s WHERE device_id=%s",
        (h.profile.clock["now"], h.actor.device_id),
    )
    database_connection.commit()
    established = cursors(database_connection)
    h.set_policy(binding_enabled=False, d1_enabled=False)
    view = h.capabilities()
    assert (
        not view["binding_available"]
        and view["capability_revision"] > body["expected_capability_revision"]
    )
    assert h.post(body).json() == first.json()
    alias = {**body, "server_profile_id": str(uuid4())}
    assert h.post(alias).json()["server_profile_id"] == alias["server_profile_id"]
    assert cursors(database_connection) == established and snapshot(database_connection) == before


@pytest.mark.parametrize("gate", ["B1", "D1"])
def test_live_policy_flip_blocks_first_bind_then_refreshes_shared_revision_and_preserves_readback(
    qualified: BindingHarness, database_connection: Connection[Any], gate: str
) -> None:
    h = qualified
    body = h.request()
    revision = body["expected_capability_revision"]
    before = snapshot(database_connection)
    flip = {"binding_enabled": False} if gate == "B1" else {"d1_enabled": False}
    h.set_policy(**flip)
    assert_error(h.post(body), "sync_binding_unavailable", 503)
    assert not cursors(database_connection) and snapshot(database_connection) == before
    off = h.capabilities()
    assert not off["binding_available"] and off["capability_revision"] == revision + 1
    assert (
        h.client.get(D1 + "/capabilities", headers=h.headers()).json()["payload"][
            "capability_revision"
        ]
        == revision + 1
    )
    h.set_policy(binding_enabled=True, d1_enabled=True)
    assert_error(h.post(body), "sync_binding_capability_changed", 409)
    assert not cursors(database_connection)
    on = h.capabilities()
    assert on["binding_available"] and on["capability_revision"] == revision + 2
    assert (
        h.client.get(D1 + "/capabilities", headers=h.headers()).json()["payload"][
            "capability_revision"
        ]
        == revision + 2
    )
    body["expected_capability_revision"] = on["capability_revision"]
    first = h.post(body)
    assert first.status_code == 200
    cursor = cursors(database_connection)
    h.set_policy(**flip)
    assert h.post(body).json() == first.json()
    assert cursors(database_connection) == cursor and snapshot(database_connection) == before
    assert h.capabilities()["capability_revision"] == revision + 3
    # Production settings never changed; only the supported policy Callable did.
    assert h.settings.sync_binding_v2_enabled and h.settings.device_compatibility_v2_enabled


@pytest.mark.usefixtures("replacement_owner")
def test_alternating_wire_views_share_revision_then_disable_role_limit_and_key_changes_advance(
    binding: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = binding
    revision = h.capabilities()["capability_revision"]
    for _ in range(3):
        d1 = h.client.get(D1 + "/capabilities", headers=h.headers())
        assert d1.status_code == 200 and d1.json()["payload"]["capability_revision"] == revision
        assert h.capabilities()["capability_revision"] == revision
        assert (
            "binding_available" not in d1.json()["payload"]
            and d1.json()["payload"]["families"] == []
        )
    h.set_policy(binding_enabled=False)
    assert h.capabilities()["capability_revision"] == revision + 1
    revision += 1
    for statement, params in (
        ("UPDATE account.user_account SET role='ADMIN' WHERE user_id=%s", (h.actor.user_id,)),
        (
            (
                "UPDATE account.resource_quota_policy SET "
                "default_devices=default_devices+1,revision=revision+1"
            ),
            (),
        ),
    ):
        database_connection.execute(statement, params)
        database_connection.commit()
        next_revision = h.capabilities()["capability_revision"]
        assert next_revision == revision + 1
        revision = next_revision
        assert (
            h.client.get(D1 + "/capabilities", headers=h.headers()).json()["payload"][
                "capability_revision"
            ]
            == revision
        )
    key = public_spki(ec.generate_private_key(ec.SECP256R1()))
    database_connection.execute(
        (
            "UPDATE account.device SET public_key=%s,public_key_thumbprint_sha256=%s "
            "WHERE device_id=%s"
        ),
        (key, public_key_thumbprint(key), h.actor.device_id),
    )
    database_connection.commit()
    assert h.capabilities()["capability_revision"] == revision + 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE account.user_account SET status='DISABLED' WHERE user_id=%s",
        "UPDATE account.user_account SET deleted_at=now() WHERE user_id=%s",
        "UPDATE account.device SET revoked_at=now() WHERE user_id=%s",
        "UPDATE account.device SET public_key=NULL WHERE user_id=%s",
        "UPDATE account.device SET public_key_thumbprint_sha256=NULL WHERE user_id=%s",
        (
            "UPDATE account.device SET public_key_thumbprint_sha256=decode(repeat('ff',32"
            "),'hex') WHERE user_id=%s"
        ),
        "UPDATE account.user_session SET revoked_at=now() WHERE user_id=%s",
        "UPDATE account.user_session SET expires_at=issued_at+interval '1 second' WHERE user_id=%s",
    ],
)
@pytest.mark.usefixtures("replacement_owner")
def test_current_authority_refuses_first_and_readback_without_writes(
    qualified: BindingHarness, database_connection: Connection[Any], statement: str
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    database_connection.execute(statement, (h.actor.user_id,))
    database_connection.commit()
    if "SET expires_at=" in statement:
        h.profile.clock["now"] += timedelta(seconds=2)
    before, cursor = snapshot(database_connection), cursors(database_connection)
    for request in (body, {**body, "user_id": str(uuid4()), "expected_identity_epoch": 999}):
        assert_error(h.post(request), "authentication_required", 401)
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor


def test_actual_configured_access_ttl_has_no_portable_replay_exception(
    binding: BindingHarness, database_url: str
) -> None:
    h = binding
    strict = h.settings.model_copy(update={"access_token_ttl_seconds": 60})
    with TestClient(create_app(strict, profile_pairing_service=h.profile.identity)) as client:
        for path, method in (("/capabilities", "GET"), ("/bind", "POST")):
            response = client.request(
                method,
                PREFIX + path,
                headers=h.headers(),
                content=b"{" if method == "POST" else b"",
            )
            assert_error(response, "authentication_required", 401)


@pytest.mark.parametrize(
    "checkpoint",
    [
        "last_acked_device_sequence=1",
        "last_pulled_server_sequence=1",
        "last_successful_sync_at=now()",
    ],
)
def test_null_epoch_prior_use_never_resets(
    qualified: BindingHarness, database_connection: Connection[Any], checkpoint: str
) -> None:
    h = qualified
    body = h.request()
    database_connection.execute(
        "INSERT INTO sync.device_sync_cursor(user_id,device_id) VALUES(%s,%s)",
        (h.actor.user_id, h.actor.device_id),
    )
    database_connection.execute(
        "UPDATE sync.device_sync_cursor SET " + checkpoint + " WHERE device_id=%s",
        (h.actor.device_id,),
    )
    database_connection.commit()
    before = cursors(database_connection)
    assert_error(h.post(body), "DEVICE_RESET_REQUIRED", 409)
    assert cursors(database_connection) == before


def test_identity_owner_epoch_gate_and_revision_precedence(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    before = cursors(database_connection)
    assert_error(
        h.post({**body, "user_id": str(uuid4()), "expected_identity_epoch": 99}),
        "binding_mismatch",
        409,
    )
    assert_error(
        h.post({**body, "expected_identity_epoch": 99}), "sync_binding_identity_changed", 409
    )
    assert_error(
        h.post({**body, "expected_capability_revision": body["expected_capability_revision"] + 1}),
        "sync_binding_capability_changed",
        409,
    )
    assert cursors(database_connection) == before
    assert h.post(body).status_code == 200
    h.set_policy(binding_enabled=False)
    assert_error(h.post({**body, "journal_epoch": str(uuid4())}), "DEVICE_RESET_REQUIRED", 409)
    assert h.post(body).status_code == 200


@pytest.mark.parametrize("endpoint", ["B1_GET", "D1_GET", "B1_BIND"])
def test_final_clock_is_after_the_last_real_locked_read(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    from autplay.adapters.postgresql.device_compatibility import (
        SqlAlchemyDeviceCompatibilityRepository as Repository,
    )
    from autplay.adapters.postgresql.sync_binding_v2 import (
        SqlAlchemySyncBindingRepository as Cursors,
    )

    h = qualified
    body = h.request()
    before, cursor = snapshot(database_connection), cursors(database_connection)
    owner, name = (
        (Cursors, "lock_cursor") if endpoint == "B1_BIND" else (Repository, "lock_revision")
    )
    original = getattr(owner, name)

    def delayed(self: Any, *args: Any, **kwargs: Any) -> object:
        result = original(self, *args, **kwargs)  # REAL PG read/lock completes first.
        h.profile.clock["now"] += timedelta(minutes=11)
        return result

    monkeypatch.setattr(owner, name, delayed)
    response = (
        h.post(body)
        if endpoint == "B1_BIND"
        else h.client.get(
            (D1 if endpoint == "D1_GET" else PREFIX) + "/capabilities", headers=h.headers()
        )
    )
    assert (
        response.status_code == 401
        and response.json()["error"]["code"] == "authentication_required"
    )
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor
