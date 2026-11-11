"""UNEXECUTED R1 qualification source; all keys/SQL/JWTs below are future actions.

Uses guarded disposable PostgreSQL and synthetic tmp_path ledgers only. No
import, hash, signature, runtime, migration or assertion ran while authoring.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    DeviceKeyBlockRow,
    DeviceRow,
    PortableAccountChallengeRow,
    PortableAccountRow,
    ServerInstanceRow,
    UserAccountRow,
    UserSessionRow,
)
from autplay.adapters.postgresql.portable_access import SqlAlchemyPortableAccessReader
from autplay.adapters.postgresql.sync_binding_v2 import BindingDatabaseRuntime, DatabaseDeadline
from autplay.application.auth import AuthService
from autplay.domain.auth import (
    AccessTokenClaims,
    InvalidAccessTokenError,
    PortableAccessAdmission,
    Principal,
)
from autplay.domain.profile_pairing import iso8601
from autplay.entrypoints.api import create_app
from autplay.entrypoints.composition import build_auth_service, build_sync_binding_authenticator
from autplay.entrypoints.privacy_deletion import DeletionEvidenceRuntime
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import set_committed_value
from typed_assertions import method_owner, present, require_instance

from .test_device_portable_v2 import PortableHarness, binding_snapshot
from .test_device_portable_v2 import portable as portable
from .test_portable_accounts import binding_body, passport, proof

SECRET = "portable-test-access-secret-distinct-32bytes"


@dataclass
class Grant:
    portable: PortableHarness
    settings: ApiSettings
    raw: bytes
    result: dict[str, Any]
    challenge_id: UUID
    purpose: str

    @property
    def engine(self) -> Engine:
        return require_instance(self.portable.legacy.sessions.kw["bind"], Engine)

    def auth(self) -> AuthService:
        # Rebuild the actual authenticator; no issuer-only decode substitutes.
        return build_auth_service(self.settings, self.engine, identity=self.portable.identity)

    def token(self) -> str:
        return require_instance(self.result["access_token"], str)


@pytest.fixture(params=["REGISTER", "LOGIN"])
def grant(
    portable: PortableHarness, request: pytest.FixtureRequest, database_url: str, tmp_path: Path
) -> Grant:
    body, raw, _, result = portable.register()
    if request.param == "LOGIN":
        body, raw, _ = portable.command("LOGIN")
        result = portable.service.login(raw, source="fixture-source")
    pem = portable.identity._key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")
    settings = ApiSettings(
        database_url=SecretStr(database_url),
        vault_root=tmp_path / "vault",
        auth_signing_secret=SecretStr(SECRET),
        auth_issuer="test",
        auth_audience="test",
        public_access_source_hmac_secret=SecretStr("portable-source-secret-fixture-32bytes"),
        access_token_ttl_seconds=60,
        portable_accounts_enabled=False,
        device_compatibility_v2_enabled=False,
        profile_identity_private_key_pem=SecretStr(pem),
        profile_label_hint=portable.identity._label,
        profile_api_origin=portable.identity._api,
        profile_stream_origin=portable.identity._stream,
        privacy_ledger_path=tmp_path / "portable-reservations.sqlite3",
        privacy_ledger_key=SecretStr("d1-portable-ledger-test-secret-32bytes"),
        privacy_ledger_key_id="fixture-v1",
    )
    return Grant(
        portable, settings, raw, result, UUID(body["challenge"]["challenge_id"]), request.param
    )


def reject_all(grant: Grant, token: str | None = None) -> None:
    auth = grant.auth()
    for operation in (
        auth.authenticate_access,
        auth.authenticate_access_with_claims,
        auth.decode_access,
    ):
        with pytest.raises(InvalidAccessTokenError) as caught:
            operation(token or grant.token())
        assert str(caught.value) == "invalid_access_token"


def mint(grant: Grant, **changes: Any) -> str:
    claims = grant.portable.legacy.access_tokens.decode(grant.token(), now=datetime.now(UTC))
    payload = {
        "iss": "test",
        "aud": "test",
        "sub": str(claims.user_id),
        "did": str(claims.device_id),
        "sid": str(claims.session_id),
        "jti": str(claims.token_id),
        "iat": int(claims.issued_at.timestamp()),
        "nbf": int(claims.not_before.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
        "token_type": "access",
    }
    return jwt.encode({**payload, **changes}, SECRET, algorithm="HS256", headers={"typ": "at+jwt"})


def test_600_to_60_rebuilt_actual_auth_and_mounted_protected_api(grant: Grant) -> None:
    before = binding_snapshot(grant.portable)
    auth = grant.auth()
    principal, claims = auth.authenticate_access_with_claims(grant.token())
    assert claims.expires_at - claims.issued_at == timedelta(seconds=600)
    assert claims.portable_admission is not None
    assert auth.authenticate_access(grant.token()) == principal
    assert auth.decode_access(grant.token()) == claims
    preflight: Callable[[str], object] = auth.preflight_access
    assert preflight(grant.token()) is None
    with TestClient(create_app(grant.settings)) as client:
        replay = client.post(
            "/api/v1/device-compatibility/v2/portable/" + grant.purpose.lower(),
            content=grant.raw,
            headers={"Content-Type": "application/json"},
        )
        assert replay.status_code == 200 and replay.json()["access_token"] == grant.token()
        response = client.get(
            "/api/v1/library/entries", headers={"Authorization": "Bearer " + grant.token()}
        )
        assert response.status_code == 200
        binding = client.get(
            "/api/v1/sync-binding/v2/capabilities",
            headers={"Authorization": "Bearer " + grant.token()},
        )
        assert (
            binding.status_code == 200 and binding.json()["payload"]["binding_available"] is False
        )
    assert binding_snapshot(grant.portable) == before


@pytest.mark.parametrize(
    "field", ["sub", "did", "sid", "jti", "iat", "nbf", "exp", "iss", "aud", "token_type"]
)
def test_same_valid_signature_wrong_original_tuple_cannot_enter_exception(
    grant: Grant, field: str
) -> None:
    claims = grant.portable.legacy.access_tokens.decode(grant.token(), now=datetime.now(UTC))
    value: Any = str(uuid4())
    if field in {"iat", "nbf"}:
        value = int(claims.issued_at.timestamp()) - 1
    elif field == "exp":
        value = int(claims.expires_at.timestamp()) + 1
    reject_all(grant, mint(grant, **{field: value}))


@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "signature",
        "expired",
        "future",
        "boolean_time",
        "uuid_alias",
        "over_900",
        "zero",
    ],
)
def test_generic_rejections_never_read_a_portable_receipt(
    grant: Grant, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = grant.portable.legacy.access_tokens.decode(grant.token(), now=datetime.now(UTC))
    issued = int(claims.issued_at.timestamp())
    if case == "malformed":
        token = "invalid.token"
    elif case == "signature":
        head, payload, signature = grant.token().split(".")
        token = head + "." + payload + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    elif case == "expired":
        expiry = int(datetime.now(UTC).timestamp()) - 1
        token = mint(grant, iat=expiry - 600, nbf=expiry - 600, exp=expiry)
    elif case == "future":
        token = mint(grant, iat=issued + 1200, nbf=issued + 1200, exp=issued + 1800)
    elif case == "boolean_time":
        token = mint(grant, iat=True)
    elif case == "uuid_alias":
        token = mint(grant, sub=claims.user_id.hex)
    elif case == "over_900":
        token = mint(grant, exp=issued + 901)
    else:
        token = mint(grant, exp=issued)
    reads = []

    def forbidden(self: SqlAlchemyPortableAccessReader, candidate: AccessTokenClaims) -> None:
        reads.append(candidate)
        raise AssertionError("generic JWT rejection entered receipt authorization")

    monkeypatch.setattr(SqlAlchemyPortableAccessReader, "authorize_original", forbidden)
    reject_all(grant, token)
    assert reads == []


@pytest.mark.parametrize(
    "case",
    [
        "pruned",
        "ambiguous",
        "request_digest",
        "original_digest",
        "proof_digest",
        "root_revision",
        "passport",
        "role",
        "authority",
        "disabled",
        "deleted",
        "epoch",
        "origin",
        "key",
        "platform",
        "generation",
        "family",
        "refresh_digest",
        "logout",
        "device_revoke",
        "block",
        "receipt_token",
        "receipt_time",
        "receipt_expiry",
    ],
)
def test_current_receipt_root_identity_role_lineage_and_revocation_fail_closed(
    grant: Grant, case: str
) -> None:
    with grant.portable.legacy.sessions.begin() as s:
        row = s.get(PortableAccountChallengeRow, grant.challenge_id)
        account = s.get(UserAccountRow, UUID(grant.result["account_id"]))
        root = s.get(PortableAccountRow, present(account).user_id)
        device = s.get(DeviceRow, UUID(grant.result["device_id"]))
        session = s.get(UserSessionRow, UUID(grant.result["session_id"]))
        if case == "pruned":
            s.delete(row)
        elif case == "ambiguous":
            duplicate_id = uuid4()
            duplicate_binding = uuid4()
            s.add(
                PortableAccountChallengeRow(
                    challenge_id=duplicate_id,
                    purpose=present(row).purpose,
                    source_key=present(row).source_key,
                    account_id=present(row).account_id,
                    document={**present(row).document, "challenge_id": str(duplicate_id)},
                    created_at=present(row).created_at,
                    expires_at=present(row).expires_at,
                    request_hash=present(row).request_hash,
                    receipt={
                        **present(present(row).receipt),
                        "binding_commit_id": str(duplicate_binding),
                    },
                    receipt_expires_at=present(row).receipt_expires_at,
                    contract_version="v2",
                    platform=present(row).platform,
                    original_document_sha256=present(row).original_document_sha256,
                    root_proof_sha256=present(row).root_proof_sha256,
                    original_request_evidence={
                        **present(present(row).original_request_evidence),
                        "binding_commit_id": str(duplicate_binding),
                    },
                    binding_commit_id=duplicate_binding,
                    authority_generation=present(row).authority_generation,
                    root_revision=present(row).root_revision,
                )
            )
        elif case in {"request_digest", "original_digest", "proof_digest"}:
            column = {
                "request_digest": "request_hash",
                "original_digest": "original_document_sha256",
                "proof_digest": "root_proof_sha256",
            }[case]
        elif case == "root_revision":
            present(root).revision += 1
        elif case == "passport":
            present(root).document = {
                **present(root).document,
                "password_nonce_b64": "enp6enp6enp6enp6",
            }
        elif case == "role":
            present(account).role = "ADMIN"
        elif case == "authority":
            present(account).authority_generation += 1
        elif case == "disabled":
            present(account).status = "DISABLED"
        elif case == "deleted":
            present(account).deleted_at = datetime.now(UTC)
        elif case in {"epoch", "origin"}:
            identity = s.scalar(select(ServerInstanceRow))
            if case == "epoch":
                present(identity).identity_epoch += 1
            else:
                present(identity).api_origin = "https://changed.invalid"
        elif case == "key":
            present(device).public_key_thumbprint_sha256 = bytes(32)
        elif case == "platform":
            present(device).platform = "OTHER"
        elif case == "generation":
            present(session).generation = 1
        elif case == "family":
            present(session).family_id = uuid4()
        elif case == "refresh_digest":
            present(session).refresh_token_hash = bytes(32)
        elif case == "logout":
            present(session).revoked_at = datetime.now(UTC)
        elif case == "device_revoke":
            present(device).revoked_at = datetime.now(UTC)
        elif case == "block":
            s.add(
                DeviceKeyBlockRow(
                    user_id=present(account).user_id,
                    device_key_thumbprint_sha256=present(device).public_key_thumbprint_sha256,
                    blocked_at=datetime.now(UTC),
                )
            )
    if case in {
        "request_digest",
        "original_digest",
        "proof_digest",
        "receipt_token",
        "receipt_time",
        "receipt_expiry",
    }:
        # Leave the persisted constraint valid; corrupt only the adapter's read.
        def loaded(session: Session, instance: object) -> None:
            if (
                isinstance(instance, PortableAccountChallengeRow)
                and instance.challenge_id == grant.challenge_id
            ):
                if case in {"request_digest", "original_digest", "proof_digest"}:
                    set_committed_value(instance, column, bytes(32))
                elif case == "receipt_token":
                    set_committed_value(
                        instance, "receipt", {**present(instance.receipt), "token_id": str(uuid4())}
                    )
                elif case == "receipt_time":
                    receipt = present(instance.receipt)
                    issued = datetime.fromisoformat(
                        str(receipt["issued_at"]).replace("Z", "+00:00")
                    )
                    set_committed_value(
                        instance,
                        "receipt",
                        {**receipt, "issued_at": iso8601(issued + timedelta(seconds=1))},
                    )
                else:
                    expiry = datetime.now(UTC) - timedelta(seconds=1)
                    set_committed_value(instance, "receipt_expires_at", expiry)
                    set_committed_value(
                        instance,
                        "receipt",
                        {**present(instance.receipt), "access_expires_at": iso8601(expiry)},
                    )

        event.listen(Session, "loaded_as_persistent", loaded)
        try:
            reject_all(grant)
        finally:
            event.remove(Session, "loaded_as_persistent", loaded)
    else:
        reject_all(grant)


def test_absent_receipt_and_real_v1_portable_grant_keep_small_ttl(grant: Grant) -> None:
    root = ec.generate_private_key(ec.SECP256R1())
    document = {**passport(root), "public_id": "portable_v1_other"}
    challenge = grant.portable.legacy.challenge("REGISTER", "r1-v1-fixture", document=document)
    body = binding_body(challenge, ec.generate_private_key(ec.SECP256R1()))
    v1 = grant.portable.legacy.complete(body, proof(root, body))
    reject_all(grant, str(v1["access_token"]))
    with grant.portable.legacy.sessions.begin() as s:
        s.delete(s.get(PortableAccountChallengeRow, grant.challenge_id))
    reject_all(grant)


def test_db_failure_and_final_clock_after_owned_release_reject(
    grant: Grant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(self: SqlAlchemyPortableAccessReader, claims: AccessTokenClaims) -> None:
        raise SQLAlchemyError("synthetic private SQL must not escape")

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyPortableAccessReader, "authorize_original", unavailable)
        reject_all(grant)
    real = SqlAlchemyPortableAccessReader.authorize_original
    auth = grant.auth()
    expiry = grant.portable.legacy.access_tokens.decode(
        grant.token(), now=datetime.now(UTC)
    ).expires_at

    class Clock:
        now_value = datetime.now(UTC)

        def now(self) -> datetime:
            return self.now_value

    clock = Clock()

    def delayed(
        self: SqlAlchemyPortableAccessReader, claims: AccessTokenClaims
    ) -> PortableAccessAdmission:
        admission = real(self, claims)
        clock.now_value = expiry
        return admission

    monkeypatch.setattr(SqlAlchemyPortableAccessReader, "authorize_original", delayed)
    with pytest.raises(InvalidAccessTokenError):
        replace(auth, clock=clock).authenticate_access(grant.token())


def test_r1_uses_actual_two_slot_b1_runtime_and_its_rebound_session(grant: Grant) -> None:
    import asyncio

    runtime = BindingDatabaseRuntime(grant.engine.url)
    runtime.start()
    owner = DeletionEvidenceRuntime(grant.settings, grant.engine)
    api_auth = grant.auth()
    seen = []

    def invoke(
        sessions: sessionmaker[Session], budget: DatabaseDeadline
    ) -> tuple[Principal, AccessTokenClaims]:
        rebound = build_sync_binding_authenticator(api_auth, sessions, deletion_evidence=owner)
        auth_owner = require_instance(method_owner(rebound), AuthService)
        factory = require_instance(auth_owner.unit_of_work_factory, SqlAlchemyAuthUnitOfWorkFactory)
        seen.append(require_instance(factory._session_factory, sessionmaker).kw["bind"])
        return rebound(grant.token())

    async def exercise() -> None:
        try:
            principal, claims = await runtime.run(invoke)
            assert claims.portable_admission is not None
            assert seen == [runtime.engine.sync_engine] and seen[0] is not grant.engine
            assert principal.session_id == UUID(grant.result["session_id"])
        finally:
            await runtime.shutdown()

    asyncio.run(exercise())


def test_m1_final_time_keeps_r1_evidence_and_never_reads_after_clock(
    grant: Grant, monkeypatch: pytest.MonkeyPatch
) -> None:
    import test_media_source as media
    from autplay.domain.media_source import MediaSourceError

    principal, claims = grant.auth().authenticate_access_with_claims(grant.token())
    monkeypatch.setattr(media, "T", claims.expires_at - timedelta(seconds=2))
    f = media.Fixture()
    f.principal, f.claims = principal, claims
    f.policy = replace(f.policy, access_token_ttl_seconds=60)
    actor = replace(
        present(f.capture.actor),
        user_id=principal.user_id,
        device_id=principal.device_id,
        session_id=principal.session_id,
        device_user_id=principal.user_id,
        session_user_id=principal.user_id,
        session_device_id=principal.device_id,
        role=principal.role.value,
        session_issued_at=claims.issued_at - timedelta(seconds=1),
        session_expires_at=claims.expires_at + timedelta(days=1),
    )
    f.capture = replace(f.capture, actor=actor)
    prepared = f.prepare()
    assert prepared.admit() and f.reads[-1] == "clock-released"
    f.claims = replace(claims, portable_admission=None)
    with pytest.raises(MediaSourceError, match="authentication_required"):
        f.prepare().admit()
    f.advance(3)
    before = list(f.reads)
    with pytest.raises(MediaSourceError, match="authentication_required"):
        prepared.admit()
    assert f.reads == before


def test_m1_mounted_r1_uses_only_owned_readonly_pool_without_identity_or_resource_locks(
    grant: Grant,
) -> None:
    # Production mount, actual JWT/R1 authenticator and the same committed M5
    # capture. A masked 404 is the positive auth control for an absent source;
    # no invented media fixture or fallback relationship is manufactured here.
    app = create_app(grant.settings.model_copy(update={"media_source_snapshot_v1_enabled": True}))
    path = "/api/v1/media-source/user-tracks/00000000-0000-0000-0000-000000000006/snapshot"
    before = binding_snapshot(grant.portable)
    with TestClient(app) as client:
        assert client.get("/api/v1/pairing/discovery").status_code == 200
        queries: list[str] = []
        shared: list[str] = []

        def owned_read(*args: Any) -> None:
            queries.append(args[2])

        def shared_read(*args: Any) -> None:
            shared.append(args[2])

        db = app.state.media_source_database.engine.sync_engine
        general = app.state.profile_pairing_service._sessions.kw["bind"]
        event.listen(db, "before_cursor_execute", owned_read)
        event.listen(general, "before_cursor_execute", shared_read)
        try:
            response = client.get(path, headers={"Authorization": "Bearer " + grant.token()})
        finally:
            event.remove(db, "before_cursor_execute", owned_read)
            event.remove(general, "before_cursor_execute", shared_read)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert response.headers["cache-control"] == "private, no-store"
        assert shared == []
        assert any("account.portable_challenge" in query for query in queries)
        assert any("FROM (SELECT 1) AS anchor" in query for query in queries)
        assert "SET TRANSACTION READ ONLY" in queries
        assert not any(
            "FOR UPDATE" in query
            or "pg_advisory" in query
            or "resource." in query
            or query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            for query in queries
        )
    assert binding_snapshot(grant.portable) == before
