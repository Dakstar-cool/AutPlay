"""UNEXECUTED common D1 integration gates, using the real mounted API.

All imports, signatures, ledger/SQL operations and assertions are FUTURE work.
The existing guarded PostgreSQL fixture owns disposable databases. tmp_path owns
the synthetic independent ledger; no real settings, media or credentials are used.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.postgresql.device_identifier_reservations import device_v2_identifier_reserved
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.postgresql.retained_deletion_reservations import (
    RETAINED_DELETION_RESERVATIONS_INFO_KEY,
    RetainedDeletionReservations,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec
from autplay.domain import device_compatibility, sync_binding_v2
from autplay.domain.canonical_json import JsonValue
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.domain.profile_pairing import public_spki
from autplay.entrypoints import composition
from autplay.entrypoints.api import create_app
from autplay.entrypoints.device_portable_v2_http import build_device_portable_v2_service
from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
from autplay.runtime.settings import ApiSettings, StreamSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from device_qualification import set_device_qualification
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from psycopg import Connection
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker
from typed_assertions import method_owner, present, require_instance

from .test_device_deletion_v2 import PREFIX, ROUTES, DeletionHarness, snapshot
from .test_device_deletion_v2 import deletion_v2 as deletion_v2
from .test_device_portable_v2 import original as portable_original
from .test_device_profile_v2 import ENROLL_DOMAIN, ROTATE_DOMAIN, signed
from .test_device_profile_v2 import profile as profile
from .test_portable_accounts import passport
from .test_profile_pairing_m5b import _exchange_request
from .test_profile_pairing_m5b import pairing_service as pairing_service


def settings_for(h: DeletionHarness, database_url: str, tmp_path: Path) -> ApiSettings:
    pem = h.profile.identity._key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    return ApiSettings(
        database_url=SecretStr(database_url),
        vault_root=tmp_path / "vault",
        auth_signing_secret=SecretStr("recovery-test-access-secret-distinct-32bytes"),
        public_access_source_hmac_secret=SecretStr("recovery-test-source-secret-distinct-32bytes"),
        auth_issuer="test",
        auth_audience="test",
        access_token_ttl_seconds=600,
        profile_identity_private_key_pem=SecretStr(pem),
        profile_label_hint=h.profile.identity._label,
        profile_api_origin=h.profile.identity._api,
        profile_stream_origin=h.profile.identity._stream,
        privacy_ledger_path=h.ledger.path,
        privacy_ledger_key=SecretStr("d1-synthetic-ledger-secret-32bytes"),
        privacy_ledger_key_id="fixture-v1",
        portable_accounts_enabled=True,
        device_compatibility_v2_enabled=True,
        self_device_pairing_enabled=True,
        account_recovery_enabled=True,
        account_deletion_enabled=True,
        public_access_trusted_proxy_ip="198.51.100.8",
    )


@dataclass
class Mounted:
    harness: DeletionHarness
    app: FastAPI
    settings: ApiSettings
    client: TestClient
    stack: ExitStack

    def rebuild(self, **changes: object) -> None:
        self.settings = ApiSettings(**{**self.settings.model_dump(), **changes})
        self.app = create_app(self.settings)
        self.client = self.stack.enter_context(TestClient(self.app, client=("198.51.100.8", 45123)))

    def send(self, kind: str, *, raw: bytes | None = None) -> Response:
        h = self.harness
        name = "X-AutPlay-Recovery-Refresh" if kind == "outcome" else "X-AutPlay-Recovery-Code"
        secret = h.refresh if kind == "outcome" else h.code
        headers = {
            "Content-Type": "application/json",
            name: secret,
            "X-Forwarded-For": "203.0.113.19",
        }
        if kind == "request":
            headers["Authorization"] = "Bearer " + self.app.state.auth_service.access_tokens.issue(
                h.pair.actor,
                token_id=uuid4(),
                issued_at=h.now() - timedelta(seconds=1),
                expires_at=h.now() + timedelta(minutes=9),
            )
        return self.client.post(
            PREFIX + "/" + ROUTES[kind], content=raw or h.wire(kind), headers=headers
        )


@pytest.fixture
def mounted(
    deletion_v2: DeletionHarness, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Mounted]:
    # Explicit synthetic policy for exercising implementation before qualification.
    monkeypatch.setattr(
        "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES",
        frozenset({"enrollment", "session", "portable", "deletion"}),
    )
    settings = settings_for(deletion_v2, database_url, tmp_path)
    app = create_app(settings)
    owner = app.state.deletion_evidence
    assert owner.evidence_ready() is False  # Constructor did not validate storage.
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(app, client=("198.51.100.8", 45123)))
        assert owner.evidence_ready() is True  # Real same-ledger restore guard completed.
        yield Mounted(deletion_v2, app, settings, client, stack)
    assert owner.evidence_ready() is False


def assert_unavailable(
    reply: Response, caplog: pytest.LogCaptureFixture, private: tuple[str, ...]
) -> None:
    assert reply.status_code == 503
    error = reply.json()["error"]
    assert error["code"] == "deletion_evidence_unavailable" and error["retryable"] is True
    assert set(error) == {"code", "message", "retryable", "request_id"}
    assert reply.headers["Cache-Control"] == "no-store" and reply.headers["Pragma"] == "no-cache"
    for value in private:
        assert value not in reply.text and value not in caplog.text
    assert "unhandled_request_error" not in caplog.text


def test_every_issuer_builder_shares_the_actual_owned_guard(mounted: Mounted) -> None:
    m, owner = mounted, mounted.app.state.deletion_evidence
    engine = m.harness.pair.engine
    factories: list[sessionmaker[Session]] = []
    original = owner.configure

    def capture(sessions: sessionmaker[Session]) -> None:
        original(sessions)
        factories.append(sessions)

    # Capture the public composition boundary, not a guessed private factory list.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(owner, "configure", capture)
        auth = composition.build_auth_service(m.settings, engine, deletion_evidence=owner)
        identity = composition.build_profile_pairing_service(
            m.settings, engine, deletion_evidence=owner
        )
        for builder in (
            composition.build_public_access_service,
            composition.build_portable_account_service,
            composition.build_self_device_pairing_service,
            composition.build_account_recovery_service,
            composition.build_account_deletion_service,
        ):
            before = len(factories)
            assert builder(m.settings, engine, deletion_evidence=owner) is not None
            assert len(factories) > before
        for device_builder in (
            composition.build_device_profile_v2_service,
            composition.build_device_admission_v2_service,
            composition.build_device_self_v2_service,
            composition.build_device_recovery_v2_service,
            composition.build_device_deletion_v2_service,
            build_device_portable_v2_service,
        ):
            before = len(factories)
            assert (
                device_builder(m.settings, engine, identity=identity, deletion_evidence=owner)
                is not None
            )
            assert len(factories) > before
        stream_settings = StreamSettings(
            database_url=m.settings.database_url,
            auth_signing_secret=m.settings.auth_signing_secret,
            auth_issuer="test",
            auth_audience="test",
            vault_root=m.settings.vault_root,
            privacy_ledger_path=m.settings.privacy_ledger_path,
            privacy_ledger_key=m.settings.privacy_ledger_key,
            privacy_ledger_key_id="fixture-v1",
        )
        composition.build_stream_auth_service(stream_settings, engine, deletion_evidence=owner)
        transient = sessionmaker(engine, class_=Session, info={"unrelated.synthetic": "preserved"})
        composition.build_sync_binding_authenticator(auth, transient, deletion_evidence=owner)
        assert transient.kw["info"]["unrelated.synthetic"] == "preserved"
    assert len(factories) == 16
    for sessions in factories:
        guard = sessions.kw["info"][RETAINED_DELETION_RESERVATIONS_INFO_KEY]
        assert type(guard) is RetainedDeletionReservations and guard.ledger is owner.ledger
        assert method_owner(guard.evidence_ready) is owner
        with sessions.begin() as session:
            lock_resource_admission(session)
            assert device_v2_identifier_reserved(session, uuid4()) is False
    deletion = composition.build_account_deletion_service(
        m.settings, engine, deletion_evidence=owner
    )
    assert deletion is not None and deletion.ledger is owner.ledger


@pytest.mark.parametrize("failure", ["missing", "corrupt", "notready"])
def test_real_mounted_issuers_fail_closed_with_flags_off(
    mounted: Mounted,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    m, h = mounted, mounted.harness
    m.rebuild(device_compatibility_v2_enabled=False, account_deletion_enabled=False)
    owner = m.app.state.deletion_evidence
    body, key, _ = h.profile.enrollment()
    raw = signed(body, key, ENROLL_DOMAIN)
    before = snapshot(database_connection)
    if failure == "notready":
        owner.reset()
    elif failure == "missing":
        owner.ledger.path.rename(owner.ledger.path.with_suffix(".missing-fixture"))
    else:
        owner.ledger.path.write_bytes(b"synthetic corrupt independent evidence")
    reply = m.client.post(
        "/api/v1/device-compatibility/v2/enrollment/exchanges",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert_unavailable(reply, caplog, (str(h.ledger.path), h.code, h.profile.identity._api))
    assert owner.evidence_ready() is False
    assert snapshot(database_connection) == before


def test_source_mount_keeps_d1_and_b1_separate_and_qualification_empty(
    mounted: Mounted, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_device_qualification(monkeypatch)
    paths = set(mounted.app.openapi()["paths"])
    assert {PREFIX + "/" + route for route in ROUTES.values()} <= paths
    d1 = {path for path in paths if path.startswith("/api/v1/device-compatibility/v2/")}
    b1 = {path for path in paths if path.startswith("/api/v1/sync-binding/v2/")}
    assert len(d1) == 29 and len(b1) == 3
    assert not device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES
    assert not sync_binding_v2.IMPLEMENTED_AND_QUALIFIED_PLATFORMS
    owner = mounted.app.state.deletion_evidence
    policy = composition._device_capability_policy(mounted.settings, owner)
    assert policy.d1.deletion_evidence_ready is True
    owner.reset()
    assert (
        composition._device_capability_policy(mounted.settings, owner).d1.deletion_evidence_ready
        is False
    )


def test_constructor_and_readiness_are_pure_and_failed_same_owner_restore_resets(
    mounted: Mounted,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m, owner = mounted, mounted.app.state.deletion_evidence

    def unavailable(*args: object, **kwargs: object) -> None:
        raise DeletionEvidenceError()

    with monkeypatch.context() as patch:
        patch.setattr(m.harness.pair.engine, "connect", unavailable)
        patch.setattr(type(owner.ledger), "read", unavailable)
        patch.setattr(type(owner.ledger), "request_read", unavailable)
        candidate = DeletionEvidenceRuntime(m.settings, m.harness.pair.engine)
        assert candidate.evidence_ready() is False
    monkeypatch.setattr(owner.ledger, "read", unavailable)
    monkeypatch.setattr(owner.ledger, "request_read", unavailable)
    assert owner.evidence_ready() is True  # A pure snapshot performs no validation.
    with pytest.raises(DeletionEvidenceError):
        owner.restore_guard()  # The actual owner ledger must be the one that fails.
    assert owner.evidence_ready() is False
    reply = m.client.get("/health/ready")
    assert (
        reply.status_code == 503
        and reply.json()["error"]["code"] == "deletion_evidence_unavailable"
    )


def test_real_mounted_deletion_terminal_replay_survives_all_admission_gates(
    mounted: Mounted,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m, h = mounted, mounted.harness
    raw = h.wire("request")
    accepted = m.send("request", raw=raw)
    assert accepted.status_code == 200
    h.parent = accepted.json()
    monkeypatch.setattr(
        "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES", frozenset()
    )
    m.rebuild(
        device_compatibility_v2_enabled=False,
        account_deletion_enabled=False,
        account_recovery_enabled=False,
    )
    assert m.send("receipt").json() == {**h.parent, "replayed": True}
    assert m.send("resolve").status_code == 200
    assert m.send("preview").status_code == 200
    cancelled = m.send("cancel")
    assert cancelled.status_code == 200
    assert m.send("cancel").status_code == 200
    assert m.send("outcome").status_code == 200


def test_mounted_source_qualification_blocks_first_effects_but_preserves_exact_replay(
    mounted: Mounted,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m, profile = mounted, mounted.harness.profile
    registry = "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES"
    prefix = "/api/v1/device-compatibility/v2/"

    def post(route: str, raw: bytes) -> Response:
        return m.client.post(
            prefix + route, content=raw, headers={"Content-Type": "application/json"}
        )

    def denied(reply: Response) -> None:
        assert reply.status_code == 503
        assert reply.json()["error"]["code"] == "device_contract_unavailable"

    body, key, refresh = profile.enrollment()
    raw = signed(body, key, ENROLL_DOMAIN)
    monkeypatch.setattr(registry, frozenset())
    before = snapshot(database_connection)
    denied(post("enrollment/exchanges", raw))
    denied(m.send("request"))
    assert snapshot(database_connection) == before

    monkeypatch.setattr(registry, frozenset({"enrollment"}))
    first = post("enrollment/exchanges", raw)
    assert first.status_code in {200, 201}
    binding = first.json()
    rotation, _ = profile.rotation(binding, body, refresh)
    rotate_raw = signed(rotation, key, ROTATE_DOMAIN)
    before = snapshot(database_connection)
    denied(post("sessions/rotate", rotate_raw))
    assert snapshot(database_connection) == before

    monkeypatch.setattr(registry, frozenset())
    replay = post("enrollment/exchanges", raw)
    assert replay.status_code == 200 and replay.json()["session_id"] == binding["session_id"]

    monkeypatch.setattr(registry, frozenset({"session"}))
    rotated = post("sessions/rotate", rotate_raw)
    assert rotated.status_code == 200
    monkeypatch.setattr(registry, frozenset())
    replay = post("sessions/rotate", rotate_raw)
    assert replay.status_code == 200
    assert replay.json()["session_id"] == rotated.json()["session_id"]


def test_delayed_owned_ledger_rechecks_request_before_any_effect_or_grant(
    mounted: Mounted,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m, h, owner = mounted, mounted.harness, mounted.app.state.deletion_evidence
    raw, read = h.wire("request"), owner.ledger.request_read
    before = snapshot(database_connection)
    calls = 0

    def delayed_read() -> tuple[DeletionRequestEvidence, ...]:
        nonlocal calls
        result = read()  # Actual keyed independent history validation.
        calls += 1
        if calls == 1:
            h.profile.clock["now"] += timedelta(minutes=3)
        return tuple(require_instance(item, DeletionRequestEvidence) for item in result)

    monkeypatch.setattr(owner.ledger, "request_read", delayed_read)
    # Existing fixture's final-clock adapter performs the actual PG clock query,
    # then returns controlled time; no parser/proof/authority check is bypassed.
    reply = m.send("request", raw=raw)
    assert reply.status_code == 403 and calls > 0
    assert snapshot(database_connection) == before


def test_configured_ordinary_android_v1_still_uses_unchanged_original(mounted: Mounted) -> None:
    h, m = mounted.harness, mounted
    h.profile.platform = "ANDROID"
    invitation = h.profile.invitation()
    original, _, _ = _exchange_request(invitation)
    reply = m.client.post("/api/v1/pairing/enrollment/exchanges", json=original)
    assert reply.status_code == 201
    assert reply.json()["contract_version"] == "v1" and reply.json()["schema_version"] == 1


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_delayed_enrollment_replay_never_reissues_an_expired_binding(
    mounted: Mounted,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    m, h, owner = mounted, mounted.harness, mounted.app.state.deletion_evidence
    if version == "v1":
        command, _, _ = _exchange_request(h.profile.invitation())
        route = "/api/v1/pairing/enrollment/exchanges"

        def send() -> Response:
            return m.client.post(route, json=command)

        clock_module = "autplay.application.profile_pairing"
    else:
        command, key, _ = h.profile.enrollment()
        raw = signed(command, key, ENROLL_DOMAIN)  # Freeze the complete original once.
        route = "/api/v1/device-compatibility/v2/enrollment/exchanges"

        def send() -> Response:
            return m.client.post(route, content=raw, headers={"Content-Type": "application/json"})

        clock_module = "autplay.application.device_profile_service_v2"
    assert send().status_code in {200, 201}
    before = snapshot(database_connection)
    read, offset, grants = owner.ledger.request_read, {"elapsed": timedelta(0)}, []
    from autplay.application.profile_pairing import _database_now as real_clock

    def delayed_read() -> tuple[DeletionRequestEvidence, ...]:
        result = read()
        offset["elapsed"] = timedelta(days=91)
        return tuple(require_instance(item, DeletionRequestEvidence) for item in result)

    def final_clock(session: Session) -> datetime:
        return real_clock(session) + offset["elapsed"]

    def forbidden_grant(*args: object, **kwargs: object) -> None:
        grants.append(True)
        pytest.fail("expired replay reached token issuance")

    monkeypatch.setattr(owner.ledger, "request_read", delayed_read)
    monkeypatch.setattr(clock_module + "._database_now", final_clock)
    monkeypatch.setattr(Hs256AccessTokenCodec, "issue", forbidden_grant)
    expired = send()
    assert expired.status_code == 401, expired.text
    assert expired.json()["error"]["code"] == "enrollment_invitation_unavailable"
    assert grants == [] and snapshot(database_connection) == before


def test_mounted_portable_delayed_replay_keeps_original_ttl_and_hash_only_proof(
    mounted: Mounted,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m, owner = mounted, mounted.app.state.deletion_evidence
    root, device = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    document = passport(root)
    prefix = "/api/v1/device-compatibility/v2/portable"
    challenge = m.client.post(
        prefix + "/register-challenge",
        json={
            "contract_version": "v2",
            "schema_version": 2,
            "platform": m.harness.profile.platform,
            "account": document,
        },
    )
    assert challenge.status_code == 200
    body = {
        **challenge.json(),
        "device_public_key_spki_b64": base64.b64encode(public_spki(device)).decode(),
        "device_name": "Synthetic portable integration",
        "app_version": "d1-fixture",
        "next_refresh_token_sha256": hashlib.sha256(
            b"synthetic-portable-refresh-integration"
        ).hexdigest(),
        "binding_commit_id": str(uuid4()),
    }
    raw = portable_original(root, body)
    first = m.client.post(
        prefix + "/register", content=raw, headers={"Content-Type": "application/json"}
    )
    assert first.status_code == 200
    m.settings = ApiSettings(
        **{
            **m.settings.model_dump(),
            "device_compatibility_v2_enabled": False,
            "portable_accounts_enabled": False,
            "access_token_ttl_seconds": 60,
        }
    )
    # Rebuild the actual API and codec with a lower NEW-grant TTL; editing only
    # settings on an existing service would not exercise the restart regression.
    rebuilt = create_app(m.settings)
    with TestClient(rebuilt) as restarted:
        owner = rebuilt.state.deletion_evidence
        replay = restarted.post(
            prefix + "/register", content=raw, headers={"Content-Type": "application/json"}
        )
        assert replay.status_code == 200
        assert replay.json()["access_expires_at"] == first.json()["access_expires_at"]
        assert replay.json()["access_token"] == first.json()["access_token"]
        # Real storage inspection, without retaining/reconstructing executable ROOT proof.
        sessions = composition.build_identifier_sessions(
            m.settings, m.harness.pair.engine, deletion_evidence=owner
        )
        from autplay.adapters.postgresql.models.portable_account import PortableAccountChallengeRow

        with sessions() as session:
            row = session.get(PortableAccountChallengeRow, UUID(body["challenge"]["challenge_id"]))
            assert row is not None and len(present(row.root_proof_sha256)) == 32
            retained = rfc8785.dumps(cast(JsonValue, row.original_request_evidence))
            assert b"root_proof_b64" not in retained and b"root_signature" not in retained
        read, elapsed = owner.ledger.request_read, {"value": timedelta(0)}
        from autplay.application.portable_account import _database_now as real_clock

        def delayed_read() -> tuple[DeletionRequestEvidence, ...]:
            records = read()
            elapsed["value"] = timedelta(minutes=11)
            return tuple(require_instance(item, DeletionRequestEvidence) for item in records)

        monkeypatch.setattr(owner.ledger, "request_read", delayed_read)
        monkeypatch.setattr(
            "autplay.application.device_portable_service_v2._database_now",
            lambda session: real_clock(session) + elapsed["value"],
        )
        # The exact expired original retires its original family, returns its existing
        # terminal error and never obtains a new TTL. This effect is intentional.
        expired = restarted.post(
            prefix + "/register", content=raw, headers={"Content-Type": "application/json"}
        )
        assert (
            expired.status_code == 403
            and expired.json()["error"]["code"] == "account_receipt_expired"
        ), expired.text
        with sessions() as session:
            from autplay.adapters.postgresql.models.account import UserSessionRow

            credential = session.get(UserSessionRow, UUID(first.json()["session_id"]))
            assert credential is not None and credential.revoked_at is not None


def test_standalone_auth_builder_has_no_implicit_readiness(mounted: Mounted) -> None:
    m = mounted
    auth = composition.build_auth_service(m.settings, m.harness.pair.engine)
    # A fresh empty synthetic DB is required for bootstrap positive qualification;
    # the negative here exercises the actual repository guard before session writes.
    sessions = composition.build_identifier_sessions(m.settings, m.harness.pair.engine)
    with sessions.begin() as session:
        lock_resource_admission(session)
        with pytest.raises(DeletionEvidenceError):
            device_v2_identifier_reserved(session, uuid4())
    assert auth is not None
