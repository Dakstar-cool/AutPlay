"""UNEXECUTED strict inventory gates with genuine positive history first.

Corruption is introduced ONLY in the guarded disposable fixture database, after
the owning source service creates valid proofs/evidence. No production repair,
schema change, runtime probe or key calculation was performed while authoring.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.self_device_pairing import (
    SqlAlchemySelfPairingUnitOfWorkFactory,
    SqlAlchemySelfPairingV2UnitOfWorkFactory,
)
from autplay.application.device_admission_service_v2 import DeviceAdmissionServiceV2
from autplay.application.device_portable_service_v2 import DevicePortableServiceV2
from autplay.application.device_recovery_service_v2 import DeviceRecoveryServiceV2
from autplay.application.device_self_service_v2 import DeviceSelfServiceV2
from autplay.application.portable_account import PortableAccountService
from autplay.application.self_device_pairing import SelfDevicePairingService
from autplay.domain.account_recovery import new_code
from autplay.domain.auth import Principal
from autplay.domain.device_self_v2 import SelfOperation, parse_self_request, verify_recipient_proof
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection, sql
from psycopg.types.json import Jsonb
from typed_assertions import present, require_instance

from .test_account_recovery import configured
from .test_device_admission_s1b import _Clock, _Runtime, _web_actor
from .test_device_admission_v2 import Admission, AdmissionHarness, proof
from .test_device_portable_v2 import PortableHarness
from .test_device_profile_v2 import ENROLL_DOMAIN, ROTATE_DOMAIN, _legacy_pair, signed, snapshot
from .test_device_profile_v2 import profile as profile
from .test_device_recovery_v2 import RecoveryHarness
from .test_device_self_v2 import SOURCE_SECRET, SelfHarness
from .test_device_self_v2 import wire as self_wire
from .test_portable_accounts import passport
from .test_profile_pairing_m5b import _owner
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_sync_binding_v2 import PREFIX, BindingHarness, assert_error, cursors
from .test_sync_binding_v2 import binding as binding
from .test_sync_binding_v2 import qualified as qualified

OWNERS = (
    "DEVICE",
    "ADMISSION",
    "TRUST",
    "TRUSTED_CHALLENGE",
    "ADMISSION_EXCHANGE",
    "ENROLLMENT",
    "ROTATION",
    "PORTABLE",
    "SELF_SOURCE",
    "SELF_CLAIM",
    "RECOVERY",
)


def renew_entry_token(h: BindingHarness) -> None:
    h.token = h.profile.identity._access.issue(
        h.actor,
        token_id=uuid4(),
        issued_at=h.profile.clock["now"],
        expires_at=h.profile.clock["now"] + timedelta(minutes=10),
    )


def seed_self_history(
    h: BindingHarness,
    connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    participant: str,
    stage: str,
    mixed: bool = False,
) -> SelfHarness:
    identity, sessions = h.profile.identity, h.profile.identity._sessions
    opposite = "ANDROID" if h.profile.platform == "MACOS" else "MACOS"
    foreign_profile = replace(
        h.profile,
        owner=_owner(connection),
        platform=opposite if mixed and participant == "RECIPIENT" else h.profile.platform,
    )
    source, source_key, source_refresh = foreign_profile.enrollment(
        key=h.key if participant == "SOURCE" else None
    )
    source_binding, replayed = foreign_profile.service.enrollment_exchange(
        signed(source, source_key, ENROLL_DOMAIN)
    )
    assert not replayed
    source_actor = Principal(
        foreign_profile.owner.user_id,
        UUID(require_instance(source_binding["device_id"], str)),
        UUID(require_instance(source_binding["session_id"], str)),
        foreign_profile.owner.role,
    )
    rates = SelfDevicePairingService(
        SqlAlchemySelfPairingUnitOfWorkFactory(sessions),
        identity._access,
        identity._access_ttl,
        SOURCE_SECRET,
    )
    service = DeviceSelfServiceV2(
        SqlAlchemySelfPairingV2UnitOfWorkFactory(
            sessions, lambda s: identity.device_contract_identity(s, h.profile.clock["now"])
        ),
        enabled=lambda: True,
        access_tokens=identity._access,
        access_ttl=identity._access_ttl,
        rates=rates,
    )
    monkeypatch.setattr(
        "autplay.application.device_self_service_v2._now", lambda: h.profile.clock["now"]
    )
    recipient_key = h.key if participant == "RECIPIENT" else ec.generate_private_key(ec.SECP256R1())
    assert (public_spki(source_key) == public_spki(h.key)) is (participant == "SOURCE")
    assert (public_spki(recipient_key) == public_spki(h.key)) is (participant == "RECIPIENT")
    harness = SelfHarness(
        foreign_profile,
        service,
        source_actor,
        source,
        source_binding,
        source_key,
        source_refresh,
        opposite if mixed and participant == "SOURCE" else h.profile.platform,
        recipient_key,
    )
    harness.through(stage)
    return harness


def seed_matching_history(
    h: BindingHarness, connection: Connection[Any], owner: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, UUID | bytes, str, str | None, str]:
    """Return table, PK, ID, row platform, document column, key encoding kind."""
    identity, sessions = h.profile.identity, h.profile.identity._sessions
    if owner == "DEVICE":
        foreign = present(
            connection.execute(
                "INSERT INTO account.user_account(display_name) VALUES('B1 foreign history') "
                "RETURNING user_id"
            ).fetchone()
        )[0]
        identifier = uuid4()
        connection.execute(
            "INSERT INTO account.device(device_id,user_id,device_name,platfo"
            "rm,app_version,public_key,"
            "public_key_thumbprint_sha256,revoked_at) "
            "VALUES(%s,%s,'Retained historical key',%s,'fixture',%s,%s,%s)",
            (
                identifier,
                foreign,
                h.profile.platform,
                public_spki(h.key),
                public_key_thumbprint(public_spki(h.key)),
                h.profile.clock["now"],
            ),
        )
        connection.commit()
        return "device", "device_id", identifier, "platform", None, "RAW_DEVICE"
    if owner in {
        "ADMISSION",
        "TRUST",
        "TRUSTED_CHALLENGE",
        "ADMISSION_EXCHANGE",
        "TRUSTED_EXCHANGE",
    }:
        clock = _Clock(h.profile.clock["now"])
        runtime = _Runtime(identity, sessions, clock)
        monkeypatch.setattr(identity, "_device_v2_enabled", lambda: True)
        monkeypatch.setattr(
            "autplay.application.device_admission_service_v2._now", lambda: clock.value
        )
        web = _web_actor(runtime, connection)
        service = DeviceAdmissionServiceV2(sessions, identity=identity, enabled=lambda: True)
        harness = AdmissionHarness(runtime, service, {"enabled": True}, h.profile.platform, web)
        body, key = harness.submit_body(h.key)
        raw = proof(body, key, "admission.submit")
        admission = Admission(
            body, key, raw, service.admission_submit(raw, source="b1-history-fixture")
        )
        if owner == "ADMISSION":
            return (
                "device_admission",
                "request_id",
                UUID(body["request_id"]),
                "platform",
                "original_request_evidence",
                "RAW_ADMISSION",
            )
        harness.bind(admission)
        if owner == "TRUST":
            return (
                "trusted_device_key",
                "device_key_thumbprint_sha256",
                public_key_thumbprint(public_spki(h.key)),
                "platform",
                None,
                "RAW_TRUST",
            )
        if owner == "ADMISSION_EXCHANGE":
            identifier = present(
                connection.execute(
                    "SELECT exchange_id FROM account.device_admission_exchange_receipt ORDER BY "
                    "created_at DESC LIMIT 1"
                ).fetchone()
            )[0]
            connection.commit()
            return (
                "device_admission_exchange_receipt",
                "exchange_id",
                identifier,
                "platform",
                "original_request_evidence",
                "HEX",
            )
        challenge = service.trusted_challenge(
            proof(harness.challenge_body(admission), h.key, "trusted.challenge")
        )
        if owner == "TRUSTED_EXCHANGE":
            exchange = harness.trusted_body(admission, challenge)
            service.trusted_exchange(proof(exchange, h.key, "trusted.exchange"))
            return (
                "device_admission_exchange_receipt",
                "exchange_id",
                UUID(exchange["exchange_id"]),
                "platform",
                "original_request_evidence",
                "HEX",
            )
        return (
            "trusted_device_reenrollment_challenge",
            "challenge_id",
            UUID(require_instance(challenge["challenge_id"], str)),
            "platform",
            "original_request_evidence",
            "HEX",
        )
    if owner in {"ENROLLMENT", "ROTATION"}:
        foreign_profile = replace(h.profile, owner=_owner(connection))
        original, key, refresh = foreign_profile.enrollment(key=h.key)
        enrolled, replayed = foreign_profile.service.enrollment_exchange(
            signed(original, key, ENROLL_DOMAIN)
        )
        assert not replayed
        if owner == "ENROLLMENT":
            return (
                "enrollment_exchange_receipt",
                "exchange_id",
                UUID(original["exchange_id"]),
                "platform",
                "original_request_evidence",
                "HEX",
            )
        rotation, refresh = foreign_profile.rotation(enrolled, original, refresh)
        _result, replayed = foreign_profile.service.session_rotate(
            signed(rotation, key, ROTATE_DOMAIN)
        )
        assert not replayed
        return (
            "session_rotation_receipt",
            "rotation_id",
            UUID(rotation["rotation_id"]),
            "platform",
            "original_request_evidence",
            "HEX",
        )
    if owner == "PORTABLE":
        root = ec.generate_private_key(ec.SECP256R1())
        legacy = PortableAccountService(
            sessions, identity._access, identity._access_ttl, b"b1-history-portable-source-32bytes"
        )
        portable_service = DevicePortableServiceV2(
            legacy, identity=identity, enabled=lambda: True, portable_enabled=lambda: True
        )
        monkeypatch.setattr(
            "autplay.application.device_portable_service_v2._now", lambda: h.profile.clock["now"]
        )
        portable_harness = PortableHarness(
            portable_service,
            legacy,
            identity,
            h.profile.platform,
            {"v2": True, "portable": True},
            h.profile.clock,
            root,
            h.key,
            passport(root),
        )
        body, raw, refresh, _result = portable_harness.register()
        return (
            "portable_challenge",
            "challenge_id",
            UUID(body["challenge"]["challenge_id"]),
            "platform",
            "original_request_evidence",
            "PORTABLE_HEX",
        )
    if owner in {"SELF_SOURCE", "SELF_CLAIM", "SELF_EXCHANGE"}:
        self_harness = seed_self_history(
            h,
            connection,
            monkeypatch,
            participant="SOURCE" if owner == "SELF_SOURCE" else "RECIPIENT",
            stage="start"
            if owner == "SELF_SOURCE"
            else "exchange"
            if owner == "SELF_EXCHANGE"
            else "claim",
        )
        return (
            "self_device_pairing",
            "ceremony_id",
            UUID(self_harness.ceremony_id),
            ("source_platform" if owner == "SELF_SOURCE" else "recipient_platform"),
            ("start_document" if owner == "SELF_SOURCE" else "claim_document"),
            ("RAW_SELF" if owner == "SELF_SOURCE" else "BASE64"),
        )
    assert owner == "RECOVERY"
    # Configure under a distinct ordinary Android source, then use a valid new
    # v2 device proof with the B1 key. Recovery owns all its revocations/grants.
    foreign_profile = replace(h.profile, owner=_owner(connection))
    pair = _legacy_pair(foreign_profile, connection)
    v1, code, _ = configured(pair)
    pair = replace(pair, key=h.key)
    h.profile.clock["now"] = datetime.now(UTC)
    recovery_service = DeviceRecoveryServiceV2(v1, identity=identity, enabled=lambda: True)
    recovery_harness = RecoveryHarness(
        foreign_profile,
        pair,
        recovery_service,
        v1,
        code,
        new_code(),
        secrets.token_urlsafe(32),
        {"v2": True, "recovery": True},
    )
    raw = recovery_harness.wire("commit")  # Cache ONE original, never re-sign comparisons.
    response = recovery_harness.send("commit", raw=raw)
    assert response.status_code == 200, response.json()
    renew_entry_token(h)
    return (
        "account_recovery_operation",
        "operation_id",
        UUID(json.loads(raw)["operation_id"]),
        "platform",
        "original_request_document",
        "BASE64",
    )


def allow_disposable_corruption(connection: Connection[Any], table: str) -> None:
    # The outer canonical fixture creates only a random autplay_p02_* database.
    database = present(connection.execute("SELECT current_database()").fetchone())[0]
    assert database.startswith("autplay_p02_")
    checks = connection.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",
        ("account." + table,),
    ).fetchall()
    connection.execute(
        sql.SQL("ALTER TABLE account.{} DISABLE TRIGGER USER").format(sql.Identifier(table))
    )
    for (name,) in checks:
        connection.execute(
            sql.SQL("ALTER TABLE account.{} DROP CONSTRAINT {}").format(
                sql.Identifier(table), sql.Identifier(name)
            )
        )
    connection.commit()


def mutate_history(
    connection: Connection[Any], location: tuple[Any, ...], variant: str, h: BindingHarness
) -> None:
    table, primary, identifier, platform_column, document_column, kind = location
    allow_disposable_corruption(connection, table)
    row = present(
        connection.execute(
            sql.SQL("SELECT to_jsonb(t) FROM account.{} t WHERE {}=%s").format(
                sql.Identifier(table), sql.Identifier(primary)
            ),
            (identifier,),
        ).fetchone()
    )[0]
    platform = "ANDROID" if h.profile.platform == "MACOS" else "MACOS"
    value: object
    if variant in {"PLATFORM", "ROW_DOC_PLATFORM"}:
        column, value = platform_column, platform
    elif variant == "MALFORMED_TYPED":
        assert document_column is not None
        column, value = document_column, {**row[document_column], "schema_version": True}
    else:
        raw = kind.startswith("RAW")
        key_column = (
            "public_key"
            if kind == "RAW_DEVICE"
            else "source_public_key_spki"
            if kind == "RAW_SELF"
            else "device_public_key_spki"
        )
        thumb_column = (
            "public_key_thumbprint_sha256"
            if kind == "RAW_DEVICE"
            else "source_key_thumbprint_sha256"
            if kind == "RAW_SELF"
            else "device_key_thumbprint_sha256"
        )
        if raw:
            column = thumb_column if variant in {"NULL_THUMB", "WRONG_THUMB"} else key_column
            value = (
                None
                if variant == "NULL_THUMB"
                else b"w" * 32
                if variant == "WRONG_THUMB"
                else b"malformed-spki"
            )
        else:
            key_name = (
                "device_spki_hex"
                if kind == "PORTABLE_HEX"
                else "device_public_key_spki_hex"
                if kind == "HEX"
                else "device_public_key_spki_b64"
            )
            thumb_name = (
                "device_thumbprint_sha256"
                if kind == "PORTABLE_HEX"
                else "device_key_thumbprint_sha256"
            )
            doc = dict(row[document_column])
            doc[thumb_name if variant in {"NULL_THUMB", "WRONG_THUMB"} else key_name] = (
                None
                if variant == "NULL_THUMB"
                else "77" * 32
                if variant == "WRONG_THUMB"
                else "invalid"
            )
            column, value = document_column, doc
    if type(value) is dict:
        value = Jsonb(value)
    connection.execute(
        sql.SQL("UPDATE account.{} SET {}=%s WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(column), sql.Identifier(primary)
        ),
        (value, identifier),
    )
    connection.commit()


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("variant", ["PLATFORM", "WRONG_THUMB", "MALFORMED_KEY"])
def test_exclusive_historical_owner_conflict_refuses_with_no_mutation(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    variant: str,
) -> None:
    h = qualified
    location = seed_matching_history(h, database_connection, owner, monkeypatch)
    renew_entry_token(h)
    body = h.request()  # positive authority/history control, before corruption
    assert body["expected_capability_revision"] >= 1
    mutate_history(database_connection, location, variant, h)
    before, cursor = snapshot(database_connection), cursors(database_connection)
    response = h.post(body)
    assert_error(response, "sync_binding_platform_conflict", 409)
    # The refusal must come from this owner, never an expired entry JWT, stale
    # digest, disabled gate or unrelated current device mismatch.
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor


@pytest.mark.parametrize("owner", ["DEVICE", "SELF_SOURCE", "SELF_CLAIM", "RECOVERY"])
def test_null_thumb_raw_history_and_required_v2_document_have_distinct_rules(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    h = qualified
    location = seed_matching_history(h, database_connection, owner, monkeypatch)
    renew_entry_token(h)
    body = h.request()
    mutate_history(database_connection, location, "NULL_THUMB", h)
    response = h.post(body)
    if owner in {"DEVICE", "SELF_SOURCE"}:
        assert response.status_code == 200
    else:
        assert_error(response, "sync_binding_platform_conflict", 409)


@pytest.mark.parametrize("owner", ["SELF_SOURCE", "SELF_CLAIM", "RECOVERY"])
@pytest.mark.parametrize("variant", ["ROW_DOC_PLATFORM", "MALFORMED_TYPED"])
def test_self_and_recovery_typed_originals_are_not_deferred_future_history(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    variant: str,
) -> None:
    h = qualified
    location = seed_matching_history(h, database_connection, owner, monkeypatch)
    renew_entry_token(h)
    body = h.request()
    mutate_history(database_connection, location, variant, h)
    before, cursor = snapshot(database_connection), cursors(database_connection)
    assert_error(h.post(body), "sync_binding_platform_conflict", 409)
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor


@pytest.mark.parametrize("owner", OWNERS)
def test_foreign_revoked_expired_or_terminal_history_still_participates(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    h = qualified
    location = seed_matching_history(h, database_connection, owner, monkeypatch)
    renew_entry_token(h)
    table, primary, identifier, _platform_column, _document_column, _kind = location
    allow_disposable_corruption(database_connection, table)
    past = h.profile.clock["now"] - timedelta(days=2)
    updates: dict[str, object]
    if owner == "DEVICE":
        updates = {"revoked_at": past}
    elif owner == "TRUST":
        updates = {"removed_at": past}
    elif owner in {"ENROLLMENT", "ROTATION", "ADMISSION_EXCHANGE"}:
        updates = {"receipt_expires_at": past}
    else:
        updates = {"expires_at": past}
        if owner in {"ADMISSION", "SELF_SOURCE", "SELF_CLAIM"}:
            updates["state"] = "EXPIRED"
        if owner == "TRUSTED_CHALLENGE":
            updates["consumed_at"] = past
    assignments = sql.SQL(",").join(
        sql.SQL("{}=%s").format(sql.Identifier(name)) for name in updates
    )
    database_connection.execute(
        sql.SQL("UPDATE account.{} SET {} WHERE {}=%s").format(
            sql.Identifier(table), assignments, sql.Identifier(primary)
        ),
        (*updates.values(), identifier),
    )
    database_connection.commit()
    body = h.request()  # agreeing expired/terminal foreign evidence is still valid
    mutate_history(database_connection, location, "PLATFORM", h)
    before, cursor = snapshot(database_connection), cursors(database_connection)
    assert_error(h.post(body), "sync_binding_platform_conflict", 409)
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor


# Device/TrustedKey have no retained original with an owner/device ID to compare.
# The other eight owners and BOTH exchange shapes expose the following IDs.
# Generated output device/session IDs and redacted credentials are intentionally
# absent here; SELF source device/family IDs are not members of START or CLAIM.
ROW_IDS = (
    ("ADMISSION", "request_id"),
    ("ADMISSION", "server_instance_id"),
    ("ADMISSION", "identity_epoch"),
    ("TRUSTED_CHALLENGE", "user_id"),
    ("TRUSTED_CHALLENGE", "challenge_id"),
    *(
        (owner, column)
        for owner in ("ADMISSION_EXCHANGE", "TRUSTED_EXCHANGE")
        for column in ("exchange_id", "request_or_challenge_id", "binding_commit_id")
    ),
    *(("ENROLLMENT", column) for column in ("exchange_id", "invitation_id", "binding_commit_id")),
    ("ROTATION", "rotation_id"),
    ("ROTATION", "parent_session_id"),
    *(("PORTABLE", column) for column in ("account_id", "binding_commit_id", "challenge_id")),
    *(
        ("SELF_SOURCE", column)
        for column in ("ceremony_id", "start_operation_id", "server_instance_id")
    ),
    ("SELF_CLAIM", "claim_id"),
    ("SELF_CLAIM", "start_operation_id"),
    *(
        ("SELF_EXCHANGE", column)
        for column in (
            "start_operation_id",
            "claim_id",
            "exchange_id",
            "binding_commit_id",
            "user_id",
            "approval_operation_id",
        )
    ),
    *(
        ("RECOVERY", column)
        for column in (
            "operation_id",
            "user_id",
            "server_instance_id",
            "identity_epoch",
            "previous_generation",
            "binding_commit_id",
        )
    ),
)


def allow_disposable_id_corruption(connection: Connection[Any], table: str, column: str) -> None:
    allow_disposable_corruption(connection, table)
    # Only the FK edges involving THIS corrupted column may be removed, after
    # real services proved the complete positive state. Other B1 FK/rotation/
    # independent connection schedules retain their original constraints.
    edges = connection.execute(
        "SELECT n.nspname,r.relname,c.conname FROM pg_constraint c "
        "JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace "
        "JOIN pg_attribute a ON a.attrelid=%s::regclass AND a.attname=%s "
        "WHERE c.contype='f' AND ((c.conrelid=a.attrelid AND a.attnum=ANY(c.conkey)) "
        "OR (c.confrelid=a.attrelid AND a.attnum=ANY(c.confkey)))",
        ("account." + table, column),
    ).fetchall()
    for schema, relation, constraint in edges:
        connection.execute(
            sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {}").format(
                sql.Identifier(schema), sql.Identifier(relation), sql.Identifier(constraint)
            )
        )
    connection.commit()


def assert_history_refusal_without_mutation(
    h: BindingHarness, connection: Connection[Any], body: dict[str, Any]
) -> None:
    before, cursor = snapshot(connection), cursors(connection)
    revisions = connection.execute(
        "SELECT * FROM account.device_capability_revision ORDER BY user_id,device_id"
    ).fetchall()
    connection.commit()
    for response in (h.client.get(PREFIX + "/capabilities", headers=h.headers()), h.post(body)):
        assert_error(response, "sync_binding_platform_conflict", 409)
    assert snapshot(connection) == before and cursors(connection) == cursor
    assert (
        connection.execute(
            "SELECT * FROM account.device_capability_revision ORDER BY user_id,device_id"
        ).fetchall()
        == revisions
    )
    connection.commit()


def positive_binding_state(
    h: BindingHarness, connection: Connection[Any], established: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    renew_entry_token(h)
    body = h.request()
    positive = h.post(body)
    assert positive.status_code == 200, positive.json()
    if not established:
        connection.execute(
            "DELETE FROM sync.device_sync_cursor WHERE device_id=%s", (h.actor.device_id,)
        )
        connection.commit()  # unused positive bind had no inbox/events/bootstrap
    return body, positive.json()


def assert_independent_row_corruption(
    h: BindingHarness,
    connection: Connection[Any],
    location: tuple[Any, ...],
    column: str,
    established: bool,
    *,
    value: object = None,
) -> None:
    table, primary, identifier, _, _, _ = location
    body, positive = positive_binding_state(h, connection, established)
    allow_disposable_id_corruption(connection, table, column)
    read = sql.SQL("SELECT to_jsonb(t) FROM account.{} t WHERE {}=%s").format(
        sql.Identifier(table), sql.Identifier(primary)
    )
    original = present(connection.execute(read, (identifier,)).fetchone())[0]
    old = original[column]
    changed = value if value is not None else old + 1 if type(old) is int else str(uuid4())
    connection.execute(
        sql.SQL("UPDATE account.{} SET {}=%s WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(column), sql.Identifier(primary)
        ),
        (changed, identifier),
    )
    connection.commit()
    lookup = changed if primary == column else identifier
    corrupted = present(connection.execute(read, (lookup,)).fetchone())[0]
    stored = "\\x" + changed.hex() if type(changed) is bytes else changed
    assert corrupted == {**original, column: stored}  # full originals/proofs/digests identical
    connection.commit()
    assert_history_refusal_without_mutation(h, connection, body)
    connection.execute(
        sql.SQL("UPDATE account.{} SET {}=%s WHERE {}=%s").format(
            sql.Identifier(table), sql.Identifier(column), sql.Identifier(primary)
        ),
        (old, lookup),
    )
    connection.commit()
    restored = h.post(body)
    assert restored.status_code == 200 and restored.json() == positive


@pytest.mark.parametrize("owner,column", ROW_IDS)
@pytest.mark.parametrize("established", [False, True])
def test_only_independent_row_id_changes_while_original_proof_and_digests_stay_valid(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    column: str,
    established: bool,
) -> None:
    location = seed_matching_history(qualified, database_connection, owner, monkeypatch)
    assert_independent_row_corruption(qualified, database_connection, location, column, established)


@pytest.mark.parametrize(
    "stage,column",
    [
        ("claim", "claim_id"),
        ("exchange", "claim_id"),
        ("exchange", "exchange_id"),
        ("exchange", "binding_commit_id"),
        ("exchange", "user_id"),
        ("exchange", "approval_operation_id"),
    ],
)
@pytest.mark.parametrize("established", [False, True])
def test_self_source_match_inherits_claim_and_exchange_row_id_corruption(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    column: str,
    established: bool,
) -> None:
    h = qualified
    ceremony = seed_self_history(
        h, database_connection, monkeypatch, participant="SOURCE", stage=stage, mixed=True
    )
    location = (
        "self_device_pairing",
        "ceremony_id",
        UUID(ceremony.ceremony_id),
        "source_platform",
        "start_document",
        "RAW_SELF",
    )
    assert_independent_row_corruption(h, database_connection, location, column, established)


@pytest.mark.parametrize("stage", ["claim", "exchange"])
@pytest.mark.parametrize(
    "column", ["source_public_key_spki", "source_key_thumbprint_sha256", "source_platform"]
)
@pytest.mark.parametrize("established", [False, True])
def test_self_recipient_match_inherits_retained_raw_source_corruption(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    column: str,
    established: bool,
) -> None:
    h = qualified
    ceremony = seed_self_history(
        h, database_connection, monkeypatch, participant="RECIPIENT", stage=stage, mixed=True
    )
    location = (
        "self_device_pairing",
        "ceremony_id",
        UUID(ceremony.ceremony_id),
        "recipient_platform",
        "claim_document",
        "BASE64",
    )
    changed = (
        b"malformed-spki"
        if column == "source_public_key_spki"
        else b"w" * 32
        if column == "source_key_thumbprint_sha256"
        else h.profile.platform
    )
    assert_independent_row_corruption(
        h, database_connection, location, column, established, value=changed
    )


@pytest.mark.parametrize("participant", ["SOURCE", "RECIPIENT"])
@pytest.mark.parametrize(
    "kind,member",
    [
        ("claim", "expected_identity_epoch"),
        ("exchange", "expected_stream_origin"),
        ("exchange", "claim_request_sha256"),
        ("exchange", "recipient_key"),
    ],
)
@pytest.mark.parametrize("established", [False, True])
def test_self_individually_valid_originals_must_agree_as_one_row(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    participant: str,
    kind: str,
    member: str,
    established: bool,
) -> None:
    h, connection = qualified, database_connection
    ceremony = seed_self_history(
        h, connection, monkeypatch, participant=participant, stage=kind, mixed=True
    )
    body, positive = positive_binding_state(h, connection, established)
    original = json.loads(ceremony.originals[kind])
    changed = dict(original)
    signing_key = ceremony.key
    if member == "expected_identity_epoch":
        changed[member] += 1
    elif member == "expected_stream_origin":
        changed[member] = "https://contradictory.invalid"
    elif member == "claim_request_sha256":
        changed[member] = "00" * 32 if original[member] != "00" * 32 else "11" * 32
    else:
        signing_key = ec.generate_private_key(ec.SECP256R1())
    # These structural negatives have a fresh, individually valid original,
    # digest and proof. Row-only fixtures above NEVER alter any original bytes.
    raw = self_wire(kind, changed, signing_key)
    parsed = parse_self_request(
        SelfOperation.CLAIM if kind == "claim" else SelfOperation.EXCHANGE, raw
    )
    verify_recipient_proof(
        parsed, approved_public_key_spki=public_spki(signing_key), approved_platform=parsed.platform
    )
    allow_disposable_corruption(connection, "self_device_pairing")
    update = sql.SQL(
        "UPDATE account.self_device_pairing SET {}=%s,{}=%s,{}=%s WHERE ceremony_id=%s"
    ).format(
        sql.Identifier(kind + "_document"),
        sql.Identifier(kind + "_original_document_sha256"),
        sql.Identifier(kind + "_request_sha256"),
    )
    identifier = UUID(ceremony.ceremony_id)
    connection.execute(
        update,
        (
            Jsonb(dict(parsed.document)),
            parsed.original_document_sha256,
            parsed.request_sha256,
            identifier,
        ),
    )
    connection.commit()
    assert_history_refusal_without_mutation(h, connection, body)
    restored = parse_self_request(parsed.operation, ceremony.originals[kind])
    connection.execute(
        update,
        (Jsonb(original), restored.original_document_sha256, restored.request_sha256, identifier),
    )
    connection.commit()
    response = h.post(body)
    assert response.status_code == 200 and response.json() == positive


@pytest.mark.parametrize(
    "participant,stage",
    [
        ("SOURCE", "start"),
        ("SOURCE", "claim"),
        ("SOURCE", "exchange"),
        ("RECIPIENT", "claim"),
        ("RECIPIENT", "exchange"),
    ],
)
@pytest.mark.parametrize("mixed", [False, True])
def test_self_partial_and_exchanged_controls_preserve_actual_optional_fields(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    participant: str,
    stage: str,
    mixed: bool,
) -> None:
    h = qualified
    seed_self_history(
        h, database_connection, monkeypatch, participant=participant, stage=stage, mixed=mixed
    )
    body, positive = positive_binding_state(h, database_connection, established=True)
    before, cursor = snapshot(database_connection), cursors(database_connection)
    assert h.capabilities()["platform"] == h.profile.platform
    response = h.post(body)
    assert response.status_code == 200 and response.json() == positive
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor


@pytest.mark.parametrize("stage", ["start", "claim", "exchange"])
@pytest.mark.parametrize("established", [False, True])
def test_self_malformed_unrelated_row_is_not_global_denial(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    established: bool,
) -> None:
    h, connection = qualified, database_connection
    ceremony = seed_self_history(h, connection, monkeypatch, participant="UNRELATED", stage=stage)
    body, positive = positive_binding_state(h, connection, established)
    allow_disposable_id_corruption(connection, "self_device_pairing", "start_operation_id")
    connection.execute(
        "UPDATE account.self_device_pairing SET start_operation_id=%s WHERE ceremony_id=%s",
        (uuid4(), UUID(ceremony.ceremony_id)),
    )
    connection.commit()
    before = snapshot(connection)
    assert h.capabilities()["platform"] == h.profile.platform
    response = h.post(body)
    assert response.status_code == 200 and response.json() == positive
    assert snapshot(connection) == before
