"""UNEXECUTED B1 deletion history gates, using real future D1 signers/services.

Authoring only: no import, SQL, key, digest, signature or test has run. All future
corruption is confined by the existing random disposable-database guard. Positive
REQUEST/CANCEL/RECOVER controls precede each negative; no shape-only fake proof.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.filesystem.deletion_ledger import FilesystemDeletionLedger
from autplay.adapters.postgresql.device_key_history import DEVICE_KEY_HISTORY_OWNERS
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionProofRow
from autplay.adapters.postgresql.retained_deletion_reservations import (
    configure_retained_deletion_reservations,
)
from autplay.application.account_deletion import AccountDeletionService
from autplay.application.account_recovery import database_now
from autplay.application.device_deletion_service_v2 import DeviceDeletionServiceV2
from autplay.domain.account_recovery import new_code
from autplay.domain.auth import Principal
from autplay.domain.device_deletion_v2 import (
    DeletionOperation,
    DeviceKeyEvidence,
    KeyEvidenceSource,
    parse_deletion_request,
    verify_deletion_proof,
)
from autplay.domain.device_recovery_v2 import (
    RecoveryOperation,
    parse_recovery_request,
    verify_recovery_proof,
)
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection, sql
from psycopg.types.json import Jsonb
from sqlalchemy.orm import Session
from typed_assertions import present, require_instance

from .test_account_deletion import PairingHarness as DeletionPair
from .test_account_deletion import request as legacy_request
from .test_account_recovery import configured
from .test_device_deletion_v2 import DeletionHarness
from .test_device_profile_v2 import ENROLL_DOMAIN, _legacy_pair, signed
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import _owner
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_sync_binding_v2 import BindingHarness
from .test_sync_binding_v2 import binding as binding
from .test_sync_binding_v2 import qualified as qualified
from .test_sync_binding_v2_history import (
    allow_disposable_id_corruption,
    assert_history_refusal_without_mutation,
    positive_binding_state,
    renew_entry_token,
    seed_matching_history,
)


def seed_deletion(
    h: BindingHarness,
    connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    *,
    matching: bool = True,
    legacy_parent: bool = False,
) -> DeletionHarness:
    opposite = "ANDROID" if h.profile.platform == "MACOS" else "MACOS"
    source_platform = (
        "ANDROID" if legacy_parent else opposite if kind == "CANCEL" else h.profile.platform
    )
    foreign = replace(h.profile, owner=_owner(connection), platform=source_platform)
    pair = _legacy_pair(foreign, connection)
    recovery, code, _ = configured(pair)
    # A real D1 enrollment supplies the REQUEST actor key, platform and session.
    # No device row key/platform is filled by SQL to make a fixture pass.
    source_key = h.key if matching and kind != "CANCEL" else ec.generate_private_key(ec.SECP256R1())
    original, source_key, _ = foreign.enrollment(key=source_key)
    enrolled, replayed = foreign.service.enrollment_exchange(
        signed(original, source_key, ENROLL_DOMAIN)
    )
    assert not replayed
    pair = replace(
        pair,
        actor=Principal(
            foreign.owner.user_id,
            UUID(require_instance(enrolled["device_id"], str)),
            UUID(require_instance(enrolled["session_id"], str)),
            foreign.owner.role,
        ),
        key=source_key,
        identity={
            name: value
            for name, value in original.items()
            if name.startswith("expected_") and name != "expected_user_id"
        },
    )
    ledger = FilesystemDeletionLedger(
        tmp_path / ("b1-history-" + uuid4().hex + ".sqlite3"),
        b"b1-deletion-synthetic-ledger-32bytes",
        "fixture-v1",
    )
    ledger.initialize(coverage_started_at=datetime.now(UTC) - timedelta(days=60))
    configure_retained_deletion_reservations(
        h.profile.identity._sessions, ledger=ledger, evidence_ready=lambda: True
    )
    legacy = AccountDeletionService(recovery, ledger)
    service = DeviceDeletionServiceV2(
        legacy,
        identity=h.profile.identity,
        enabled=lambda: True,
        deletion_enabled=lambda: True,
        recovery_enabled=lambda: True,
        evidence_ready=lambda: True,
    )
    foreign.clock["now"] = datetime.now(UTC)
    recipient = h.key if matching and kind == "CANCEL" else ec.generate_private_key(ec.SECP256R1())
    deletion = DeletionHarness(
        foreign,
        pair,
        recovery,
        service,
        ledger,
        code,
        new_code(),
        secrets.token_urlsafe(32),
        {"v2": True, "deletion": True, "recovery": True, "evidence": True},
        recipient,
    )

    def final_clock(session: Session) -> datetime:
        database_now(session)  # Real SQL sample; only the final returned clock is controlled.
        return deletion.now()

    monkeypatch.setattr("autplay.application.device_deletion_service_v2.database_now", final_clock)
    if kind == "SEALED":
        deletion.wire("request")
        foreign.clock["now"] += timedelta(seconds=121)
        response = deletion.send("resolve")
        assert response.status_code == 200 and response.json()["state"] == "NOT_ACCEPTED"
    elif legacy_parent:
        body = legacy_request(
            DeletionPair(**vars(deletion.pair), deletion_ledger=ledger),
            "request",
            source_key,
            confirmed_account_id=str(pair.actor.user_id),
            expected_code_generation=1,
            expected_authority_generation=1,
        )
        deletion.parent = legacy.request(pair.actor, code, body)
        # The v1 request commits at real DB time. Its v2 cancellation must not
        # inherit a frozen clock from before that accepted parent existed.
        parent_clock = present(
            connection.execute(
                "SELECT requested_at, clock_timestamp() FROM account.account_deletion_request "
                "WHERE deletion_request_id=%s",
                (UUID(deletion.parent["deletion_request_id"]),),
            ).fetchone()
        )
        connection.commit()
        assert foreign.clock["now"] < parent_clock[0] <= parent_clock[1]
        foreign.clock["now"] = require_instance(parent_clock[1], datetime)
    else:
        deletion.accept()
    if kind == "CANCEL":
        # REQUEST actor and CANCEL recipient are intentionally different platforms.
        deletion.profile = replace(foreign, platform=h.profile.platform)
        response = deletion.send("cancel")
        assert response.status_code == 200, response.text
    for name, raw in deletion.originals.items():
        operation = DeletionOperation.REQUEST if name == "request" else DeletionOperation.CANCEL
        continuation = (
            DeletionOperation.REQUEST_RECEIPT
            if name == "request"
            else DeletionOperation.CANCEL_OUTCOME
        )
        parsed = parse_deletion_request(operation, raw)
        verified = verify_deletion_proof(
            parse_deletion_request(continuation, raw),
            key_evidence=DeviceKeyEvidence(
                parsed.public_key_spki, parsed.platform, KeyEvidenceSource.HISTORICAL_APPROVED
            ),
            expected_trust=parsed.trust,
            retained_original=parsed,
        )
        assert verified.request.original_document_sha256 == parsed.original_document_sha256
    renew_entry_token(h)
    return deletion


def location(deletion: DeletionHarness, kind: str) -> tuple[str, str, UUID | bytes]:
    doc = json.loads(deletion.originals["cancel" if kind == "CANCEL" else "request"])
    return (
        "account_recovery_operation" if kind == "CANCEL" else "account_deletion_proof",
        "operation_id",
        UUID(doc["operation_id"]),
    )


def history_snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    rows = tuple(
        connection.execute(statement).fetchall()
        for statement in (
            "SELECT to_jsonb(r) FROM account.account_deletion_proof r ORDER BY operation_id",
            (
                "SELECT to_jsonb(r) FROM account.account_deletion_request r ORDER BY "
                "deletion_request_id"
            ),
            "SELECT to_jsonb(r) FROM account.account_recovery_operation r ORDER BY operation_id",
        )
    )
    connection.commit()
    return rows


def corrupt_one(
    h: BindingHarness,
    connection: Connection[Any],
    target: tuple[str, str, UUID | bytes],
    column: str,
    variant: str,
    established: bool,
) -> None:
    table, primary, identifier = target
    body, positive = positive_binding_state(h, connection, established)
    allow_disposable_id_corruption(connection, table, column)
    read = sql.SQL("SELECT to_jsonb(t) FROM account.{} t WHERE {}=%s").format(
        sql.Identifier(table), sql.Identifier(primary)
    )
    original = present(connection.execute(read, (identifier,)).fetchone())[0]
    old = original[column]
    changed = (
        None
        if variant == "NULL"
        else b"invalid-spki"
        if variant == "KEY"
        else b"w" * 32
        if variant == "HASH"
        else "ANDROID"
        if variant == "PLATFORM" and h.profile.platform == "MACOS"
        else "MACOS"
        if variant == "PLATFORM"
        else old + 1
        if variant == "INCREMENT"
        else str(uuid4())
        if variant == "UUID"
        else variant
    )
    assert changed != old
    if changed is None:
        connection.execute(
            sql.SQL("ALTER TABLE account.{} ALTER COLUMN {} DROP NOT NULL").format(
                sql.Identifier(table), sql.Identifier(column)
            )
        )
    write = sql.SQL("UPDATE account.{} SET {}=%s WHERE {}=%s").format(
        sql.Identifier(table), sql.Identifier(column), sql.Identifier(primary)
    )
    connection.execute(write, (changed, identifier))
    connection.commit()
    lookup = changed if primary == column else identifier
    corrupted = present(connection.execute(read, (lookup,)).fetchone())[0]
    expected = "\\x" + changed.hex() if type(changed) is bytes else changed
    assert corrupted == {**original, column: expected}
    # Every field except the selected column stays byte-identical. ID/code/key/
    # domain negatives preserve the full original signature and both digests;
    # dedicated missing-original/digest negatives change only their named field.
    connection.commit()
    before = history_snapshot(connection)
    assert_history_refusal_without_mutation(h, connection, body)
    assert history_snapshot(connection) == before
    restored = (
        bytes.fromhex(old[2:])
        if type(old) is str and old.startswith("\\x")
        else Jsonb(old)
        if type(old) is dict
        else old
    )
    connection.execute(write, (restored, lookup))
    connection.commit()
    response = h.post(body)
    assert response.status_code == 200 and response.json() == positive


@pytest.mark.parametrize("kind", ["REQUEST", "SEALED", "CANCEL", "RECOVER"])
@pytest.mark.parametrize("established", [False, True])
def test_valid_original_controls_and_expired_revoked_history_remain_readable(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    established: bool,
) -> None:
    h, connection = qualified, database_connection
    assert AccountDeletionProofRow in DEVICE_KEY_HISTORY_OWNERS
    if kind == "RECOVER":
        target = seed_matching_history(h, connection, "RECOVERY", monkeypatch)[:3]
        doc = present(
            connection.execute(
                (
                    "SELECT original_request_document FROM account.account_recovery_operation "
                    "WHERE operation_id=%s"
                ),
                (target[2],),
            ).fetchone()
        )[0]
        raw = rfc8785.dumps(doc)
        parsed = parse_recovery_request(RecoveryOperation.RECOVER, raw)
        verify_recovery_proof(
            parse_recovery_request(RecoveryOperation.OUTCOME, raw),
            key_evidence=DeviceKeyEvidence(
                parsed.public_key_spki, parsed.platform, KeyEvidenceSource.HISTORICAL_APPROVED
            ),
            expected_trust=parsed.trust,
            retained_original=parsed,
        )
        connection.commit()
    else:
        deletion = seed_deletion(h, connection, monkeypatch, tmp_path, kind)
        target = location(deletion, kind)
    # Move only the fixture clock: do not make signed evidence internally invalid
    # by shortening the original result's absolute expiry below its issue time.
    h.profile.clock["now"] += timedelta(days=2)
    renew_entry_token(h)
    if kind in {"RECOVER", "CANCEL"}:
        device_id, session_id = present(
            connection.execute(
                (
                    "SELECT result_device_id,result_session_id FROM "
                    "account.account_recovery_operation WHERE operation_id=%s"
                ),
                (target[2],),
            ).fetchone()
        )
        for table, column, identifier in (
            ("device", "device_id", device_id),
            ("user_session", "session_id", session_id),
        ):
            connection.execute(
                sql.SQL("UPDATE account.{} SET revoked_at=%s WHERE {}=%s").format(
                    sql.Identifier(table), sql.Identifier(column)
                ),
                (h.profile.clock["now"], identifier),
            )
        connection.commit()
    body, positive = positive_binding_state(h, connection, established)
    before = history_snapshot(connection)
    assert h.capabilities()["platform"] == h.profile.platform
    assert h.post(body).json() == positive
    assert history_snapshot(connection) == before


PROOF_CASES = (
    ("operation_id", "UUID"),
    ("user_id", "UUID"),
    ("server_instance_id", "UUID"),
    ("identity_epoch", "INCREMENT"),
    ("actor_device_id", "UUID"),
    ("actor_public_key_spki", "KEY"),
    ("actor_key_thumbprint_sha256", "HASH"),
    ("code_verifier_sha256", "HASH"),
    ("request_sha256", "HASH"),
    ("original_document_sha256", "HASH"),
    ("actor_public_key_spki", "NULL"),
    ("actor_key_thumbprint_sha256", "NULL"),
    ("code_verifier_sha256", "NULL"),
    ("contract_version", "v1"),
    ("platform", "PLATFORM"),
    ("decision", "SEALED"),
    ("signature_algorithm", "ECDSA-P256-SHA256"),
    ("proof_domain", "autplay:device-compatibility:recovery:recover:v2\n"),
)
COMMIT_CASES = (
    ("operation_id", "UUID"),
    ("user_id", "UUID"),
    ("server_instance_id", "UUID"),
    ("identity_epoch", "INCREMENT"),
    ("previous_generation", "INCREMENT"),
    ("result_generation", "INCREMENT"),
    ("binding_commit_id", "UUID"),
    ("result_device_id", "UUID"),
    ("result_session_id", "UUID"),
    ("spent_verifier_sha256", "NULL"),
    ("next_verifier_sha256", "HASH"),
    ("actor_device_id", "UUID"),
    ("actor_family_id", "UUID"),
    ("platform", "PLATFORM"),
    ("contract_version", "v1"),
    ("signature_algorithm", "ECDSA-P256-SHA256"),
    ("proof_domain", "wrong-domain\n"),
    ("request_sha256", "HASH"),
    ("original_document_sha256", "HASH"),
)


@pytest.mark.parametrize(
    "kind,column,variant",
    [("REQUEST", *case) for case in PROOF_CASES]
    + [
        ("SEALED", column, variant)
        for column, variant in PROOF_CASES
        if column != "decision" and (column, variant) != ("code_verifier_sha256", "HASH")
    ]
    + [(kind, *case) for kind in ("CANCEL", "RECOVER") for case in COMMIT_CASES]
    + [
        ("CANCEL", "authority_generation", "INCREMENT"),
        ("CANCEL", "kind", "RECOVER"),
        ("RECOVER", "kind", "DELETE_CANCEL"),
    ],
)
@pytest.mark.parametrize("established", [False, True])
def test_independent_history_columns_refuse_both_operations_with_intact_original_proof(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    column: str,
    variant: str,
    established: bool,
) -> None:
    if kind == "RECOVER":
        target = seed_matching_history(qualified, database_connection, "RECOVERY", monkeypatch)[:3]
    else:
        target = location(
            seed_deletion(qualified, database_connection, monkeypatch, tmp_path, kind), kind
        )
    corrupt_one(qualified, database_connection, target, column, variant, established)


@pytest.mark.parametrize(
    "column,variant",
    [
        ("identity_thumbprint_sha256", "HASH"),
        ("code_generation", "INCREMENT"),
        ("code_verifier_sha256", "HASH"),
        ("authority_generation", "INCREMENT"),
        ("revision", "INCREMENT"),
        ("cancel_operation_id", "UUID"),
    ],
)
def test_cancel_recipient_match_inherits_parent_identity_code_and_result_corruption(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    column: str,
    variant: str,
) -> None:
    deletion = seed_deletion(qualified, database_connection, monkeypatch, tmp_path, "CANCEL")
    target = (
        "account_deletion_request",
        "deletion_request_id",
        UUID(present(deletion.parent)["deletion_request_id"]),
    )
    corrupt_one(qualified, database_connection, target, column, variant, established=True)


@pytest.mark.parametrize("kind", ["REQUEST", "SEALED", "CANCEL", "RECOVER"])
@pytest.mark.parametrize("variant", ["NULL", "WRONG_DOMAIN_SIGNATURE"])
def test_partial_original_or_wrong_domain_signature_cannot_disappear_behind_raw_key(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    variant: str,
) -> None:
    h, connection = qualified, database_connection
    if kind == "RECOVER":
        target = seed_matching_history(h, connection, "RECOVERY", monkeypatch)[:3]
        signing_key = h.key
    else:
        deletion = seed_deletion(h, connection, monkeypatch, tmp_path, kind)
        target, signing_key = (
            location(deletion, kind),
            deletion.recipient if kind == "CANCEL" else deletion.pair.key,
        )
    if variant == "NULL":
        corrupt_one(h, connection, target, "original_request_document", "NULL", established=True)
        return
    body, positive = positive_binding_state(h, connection, established=True)
    table, primary, identifier = target
    allow_disposable_id_corruption(connection, table, "original_request_document")
    read = sql.SQL(
        "SELECT original_request_document,original_document_sha256,request_sha256 "
        "FROM account.{} WHERE {}=%s"
    ).format(sql.Identifier(table), sql.Identifier(primary))
    document, original, digest = present(connection.execute(read, (identifier,)).fetchone())
    # Still canonical, well-typed, correctly hashed and signed by the right key,
    # but under ANOTHER family's domain. Only real proof verification rejects it.
    wrong_domain = (
        "autplay:device-compatibility:"
        + ("deletion:cancel" if kind == "RECOVER" else "recovery:recover")
        + ":v2\n"
    )
    wrong = signed(dict(document), signing_key, wrong_domain)
    write = sql.SQL(
        "UPDATE account.{} SET original_request_document=%s,original_document_sha256=%s WHERE {}=%s"
    ).format(sql.Identifier(table), sql.Identifier(primary))
    connection.execute(
        write, (Jsonb(json.loads(wrong)), hashlib.sha256(wrong).digest(), identifier)
    )
    connection.commit()
    assert present(connection.execute(read, (identifier,)).fetchone())[2] == digest
    connection.commit()
    assert_history_refusal_without_mutation(h, connection, body)
    connection.execute(write, (Jsonb(document), original, identifier))
    connection.commit()
    assert h.post(body).json() == positive


def test_v2_cancel_of_valid_v1_request_preserves_legacy_parent_semantics(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seed_deletion(
        qualified, database_connection, monkeypatch, tmp_path, "CANCEL", legacy_parent=True
    )
    body, positive = positive_binding_state(qualified, database_connection, established=True)
    assert qualified.capabilities()["platform"] == qualified.profile.platform
    assert qualified.post(body).json() == positive


def test_unrelated_malformed_deletion_proof_is_not_global_denial(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    h, connection = qualified, database_connection
    deletion = seed_deletion(h, connection, monkeypatch, tmp_path, "REQUEST", matching=False)
    body, positive = positive_binding_state(h, connection, established=True)
    table, _primary, identifier = location(deletion, "REQUEST")
    allow_disposable_id_corruption(connection, table, "operation_id")
    connection.execute(
        "UPDATE account.account_deletion_proof SET operation_id=%s WHERE operation_id=%s",
        (uuid4(), identifier),
    )
    connection.commit()
    assert h.capabilities()["platform"] == h.profile.platform
    assert h.post(body).json() == positive


@pytest.mark.parametrize("kind", ["CANCEL", "RECOVER"])
@pytest.mark.parametrize(
    "column,variant",
    [
        ("user_id", "UUID"),
        ("device_id", "UUID"),
        ("family_id", "UUID"),
        ("generation", "INCREMENT"),
        ("session_mode", "LEGACY"),
        ("refresh_token_hash", "HASH"),
    ],
)
@pytest.mark.parametrize("established", [False, True])
def test_historical_result_session_must_still_describe_its_original_commit(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    column: str,
    variant: str,
    established: bool,
) -> None:
    h, connection = qualified, database_connection
    if kind == "RECOVER":
        target = seed_matching_history(h, connection, "RECOVERY", monkeypatch)[:3]
    else:
        target = location(seed_deletion(h, connection, monkeypatch, tmp_path, kind), kind)
    result_session = present(
        connection.execute(
            "SELECT result_session_id FROM account.account_recovery_operation "
            "WHERE operation_id=%s",
            (target[2],),
        ).fetchone()
    )[0]
    connection.commit()
    corrupt_one(
        h, connection, ("user_session", "session_id", result_session), column, variant, established
    )


@pytest.mark.parametrize("kind", ["REQUEST", "CANCEL"])
def test_multiple_missing_columns_keep_available_actor_or_session_key_projection(
    qualified: BindingHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    h, connection = qualified, database_connection
    deletion = seed_deletion(h, connection, monkeypatch, tmp_path, kind)
    body, positive = positive_binding_state(h, connection, established=True)
    table, primary, identifier = location(deletion, kind)
    columns = (
        ("actor_public_key_spki", "actor_key_thumbprint_sha256", "original_request_document")
        if kind == "REQUEST"
        else ("result_device_id", "original_request_document")
    )
    for column in columns:
        allow_disposable_id_corruption(connection, table, column)
        connection.execute(
            sql.SQL("ALTER TABLE account.{} ALTER COLUMN {} DROP NOT NULL").format(
                sql.Identifier(table), sql.Identifier(column)
            )
        )
    read = sql.SQL("SELECT to_jsonb(t) FROM account.{} t WHERE {}=%s").format(
        sql.Identifier(table), sql.Identifier(primary)
    )
    original = present(connection.execute(read, (identifier,)).fetchone())[0]
    assignments = sql.SQL(",").join(
        sql.SQL("{}=NULL").format(sql.Identifier(column)) for column in columns
    )
    connection.execute(
        sql.SQL("UPDATE account.{} SET {} WHERE {}=%s").format(
            sql.Identifier(table), assignments, sql.Identifier(primary)
        ),
        (identifier,),
    )
    connection.commit()
    assert present(connection.execute(read, (identifier,)).fetchone())[0] == {
        **original,
        **dict.fromkeys(columns),
    }
    connection.commit()
    assert_history_refusal_without_mutation(h, connection, body)
    for column in columns:
        value = original[column]
        value = (
            Jsonb(value)
            if type(value) is dict
            else bytes.fromhex(value[2:])
            if value.startswith("\\x")
            else value
        )
        connection.execute(
            sql.SQL("UPDATE account.{} SET {}=%s WHERE {}=%s").format(
                sql.Identifier(table), sql.Identifier(column), sql.Identifier(primary)
            ),
            (value, identifier),
        )
    connection.commit()
    assert h.post(body).json() == positive
