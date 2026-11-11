"""UNEXECUTED 0072 preservation, typed evidence, immutability and downgrade gates."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from autplay.domain.device_self_v2 import SelfOperation, parse_self_request
from psycopg import Connection
from psycopg.errors import CheckViolation
from sqlalchemy.exc import DBAPIError
from typed_assertions import require_instance

from .conftest import DatabaseHarness
from .schema_contract import snapshot_schema
from .test_device_profile_v2 import ProfileHarness, _legacy_pair
from .test_device_profile_v2 import profile as profile
from .test_device_self_v2 import SelfHarness, wire
from .test_device_self_v2 import self_v2 as self_v2
from .test_profile_pairing_m5b import pairing_service as pairing_service

# Explicit ORIGINAL projections across additive columns; never SELECT * here.
LEGACY_CEREMONY = (
    "SELECT ceremony_id,server_instance_id,user_id,authority_generat"
    "ion,source_device_id,source_family_id,"
    "start_operation_id,start_document,state,revision,created_at,expires_at,claim_id,claim_document,"
    "approval_operation_id,last_polled_at,exchange_id,exchange_document,result_device_id,result_session_id,"
    "receipt_expires_at FROM account.self_device_pairing ORDER BY ceremony_id"
)
LEGACY_COMMAND = (
    "SELECT operation_id,ceremony_id,user_id,device_id,family_id,request_hash,result,created_at "
    "FROM account.self_pairing_command ORDER BY operation_id"
)


def _stored_self_rows(connection: Connection[Any]) -> tuple[list[Any], list[Any]]:
    return (
        connection.execute(
            "SELECT to_jsonb(s) FROM account.self_device_pairing s ORDER BY ceremony_id"
        ).fetchall(),
        connection.execute(
            "SELECT to_jsonb(c) FROM account.self_pairing_command c ORDER BY operation_id"
        ).fetchall(),
    )


def _guard_rejects(
    connection: Connection[Any], statement: str, parameters: tuple[Any, ...], error: str
) -> None:
    before = _stored_self_rows(connection)
    connection.commit()
    with pytest.raises(CheckViolation) as caught, connection.transaction():
        connection.execute(statement, parameters)
    assert caught.value.sqlstate == "23514"
    assert caught.value.diag.message_primary == error
    assert _stored_self_rows(connection) == before
    connection.commit()


def _terminal_decision(h: SelfHarness, action: str) -> dict[str, Any]:
    # A real source command, including the valid no-claim CANCEL shape.
    start = json.loads(h.originals["start"])
    claim = json.loads(h.originals["claim"]) if "claim" in h.outcomes else None
    current = h.outcomes.get("decision", h.outcomes.get("claim", h.outcomes["start"]))
    body = {
        name: value
        for name, value in start.items()
        if name not in {"operation_id", "rendezvous_secret_sha256", "request_sha256"}
    }
    body.update(
        operation_id=str(uuid4()),
        action=action,
        expected_revision=current["revision"],
        claim_id=claim["claim_id"] if claim else None,
        claim_request_sha256=claim["request_sha256"] if claim else None,
        comparison_code=None,
    )
    response = h.send("decision", wire("decision", body, h.key))
    assert response.status_code == 200, response.json()
    assert response.json()["state"] == {"CANCEL": "CANCELLED", "REJECT": "REJECTED"}[action]
    return require_instance(response.json(), dict)


def test_legacy_values_and_opaque_hashes_survive_upgrade_and_downgrade(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    pair = _legacy_pair(profile, database_connection)
    _, exchange = pair.ready()
    pair.service.exchange(pair.poll_secret, exchange)
    before = (
        database_connection.execute(LEGACY_CEREMONY).fetchall(),
        database_connection.execute(LEGACY_COMMAND).fetchall(),
    )
    database_connection.commit()
    database_harness.downgrade(database_name, "0071_device_admission_v2")
    assert (
        database_connection.execute(LEGACY_CEREMONY).fetchall(),
        database_connection.execute(LEGACY_COMMAND).fetchall(),
    ) == before
    database_connection.commit()
    database_harness.upgrade(database_name)
    assert (
        database_connection.execute(LEGACY_CEREMONY).fetchall(),
        database_connection.execute(LEGACY_COMMAND).fetchall(),
    ) == before
    assert database_connection.execute(
        "SELECT DISTINCT contract_version,start_request_sha256,start_original_documen"
        "t_sha256 FROM account.self_device_pairing"
    ).fetchall() == [("v1", None, None)]


@pytest.mark.parametrize("stage", ["start", "claim", "decision", "exchange"])
def test_any_retained_v2_stage_blocks_downgrade_before_any_drop(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
    stage: str,
) -> None:
    self_v2.through(stage)
    before = snapshot_schema(database_connection)
    rows = database_connection.execute(
        "SELECT to_jsonb(s) FROM account.self_device_pairing s ORDER BY ceremony_id"
    ).fetchall()
    commands = database_connection.execute(
        "SELECT to_jsonb(c) FROM account.self_pairing_command c ORDER BY operation_id"
    ).fetchall()
    database_connection.commit()
    with pytest.raises(DBAPIError, match="Refusing to discard retained SELF v2 evidence"):
        database_harness.downgrade(database_name, "0071_device_admission_v2")
    assert snapshot_schema(database_connection) == before
    assert (
        database_connection.execute(
            "SELECT to_jsonb(s) FROM account.self_device_pairing s ORDER BY ceremony_id"
        ).fetchall()
        == rows
    )
    assert (
        database_connection.execute(
            "SELECT to_jsonb(c) FROM account.self_pairing_command c ORDER BY operation_id"
        ).fetchall()
        == commands
    )
    assert database_connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0074_device_deletion_v2",
    )


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange"])
def test_full_originals_two_digests_and_exact_signatures_without_credentials(
    self_v2: SelfHarness, database_connection: Connection[Any], kind: str
) -> None:
    h = self_v2
    h.through(kind)
    document = json.loads(h.originals[kind])
    if kind == "decision":
        row = database_connection.execute(
            (
                "SELECT original_document,request_hash,original_document_sha256,result FROM "
                "account.self_pairing_command WHERE operation_id=%s"
            ),
            (document["operation_id"],),
        ).fetchone()
    else:
        row = database_connection.execute(
            f"SELECT {kind}_document,{kind}_request_sha256,{kind}_original_document_sha256,"
            "to_jsonb(s) FROM account.self_device_pairing s WHERE ceremony_id=%s",
            (h.ceremony_id,),
        ).fetchone()
    assert row is not None and row[0] == document
    from autplay.domain.device_self_v2 import SelfOperation, parse_self_request

    parsed = parse_self_request(SelfOperation("self." + kind), h.originals[kind])
    assert (
        bytes(row[1]) == parsed.request_sha256 and bytes(row[2]) == parsed.original_document_sha256
    )
    serialized = json.dumps(row[3], default=str)
    for secret in (h.rendezvous, h.poll_secret):
        assert secret not in serialized
    for forbidden in (
        'rendezvous_secret"',
        'poll_secret"',
        'refresh_token"',
        "access_token",
        "encrypted_response",
        "qr_body",
        "authorization",
    ):
        assert forbidden not in serialized.lower()
    if kind in {"claim", "exchange"}:
        assert row[0]["device_signature_b64url"] == document["device_signature_b64url"]


@pytest.mark.parametrize(
    "field,expression",
    [
        ("contract_version", "'v1'"),
        ("source_platform", "'OTHER'"),
        ("source_public_key_spki", "decode(repeat('00',91),'hex')"),
        ("source_key_thumbprint_sha256", "decode(repeat('00',32),'hex')"),
        ("start_document", 'start_document || \'{"rendezvous_secret":"forbidden"}\'::jsonb'),
        ("claim_document", "claim_document - 'device_signature_b64url'"),
        ("exchange_original_document_sha256", "decode(repeat('00',32),'hex')"),
        ("receipt_expires_at", "receipt_expires_at+interval '1 day'"),
        ("state", "'OPEN'"),
    ],
)
def test_original_evidence_and_terminal_binding_are_immutable(
    self_v2: SelfHarness, database_connection: Connection[Any], field: str, expression: str
) -> None:
    h = self_v2
    h.through("exchange")
    before = database_connection.execute(
        "SELECT to_jsonb(s) FROM account.self_device_pairing s WHERE ceremony_id=%s",
        (h.ceremony_id,),
    ).fetchone()
    database_connection.commit()
    with pytest.raises(CheckViolation), database_connection.transaction():
        database_connection.execute(
            f"UPDATE account.self_device_pairing SET {field}={expression} WHERE ceremony_id=%s",
            (h.ceremony_id,),
        )
    assert (
        database_connection.execute(
            "SELECT to_jsonb(s) FROM account.self_device_pairing s WHERE ceremony_id=%s",
            (h.ceremony_id,),
        ).fetchone()
        == before
    )


@pytest.mark.parametrize(
    "boundary", ["cancelled_before_claim", "same_state", "same_revision", "skipped_revision"]
)
def test_first_claim_group_requires_exact_open_to_claimed_transition(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    boundary: str,
) -> None:
    h = self_v2
    h.through("start")
    # Freeze an otherwise valid, actually signed claim for THIS ceremony. Its
    # complete evidence group has valid hashes, platform, scope and fresh IDs.
    parsed = parse_self_request(SelfOperation.CLAIM, h.command("claim"))
    document = dict(parsed.document)
    state, revision = "CLAIMED", h.outcomes["start"]["revision"] + 1
    if boundary == "cancelled_before_claim":
        terminal = _terminal_decision(h, "CANCEL")
        assert terminal["recipient_platform"] is None and "claim_id" not in terminal
        state, revision = terminal["state"], terminal["revision"]
    elif boundary == "same_state":
        state, revision = "OPEN", h.outcomes["start"]["revision"]
    elif boundary == "same_revision":
        revision -= 1
    else:
        revision += 1
    _guard_rejects(
        database_connection,
        "UPDATE account.self_device_pairing SET "
        "claim_id=%s,claim_document=%s::jsonb,recipient_platform=%s,"
        "claim_request_sha256=%s,claim_original_document_sha256=%s,state"
        "=%s,revision=%s WHERE ceremony_id=%s",
        (
            document["claim_id"],
            json.dumps(document),
            document["platform"],
            parsed.request_sha256,
            parsed.original_document_sha256,
            state,
            revision,
            h.ceremony_id,
        ),
        "self_v2_claim_transition",
    )
    # Real service/DB positive control exercises all three permitted first groups
    # and exact replay; no disabled trigger, missing hash or invalid FK masks it.
    control = replace(h, ceremony_id=str(uuid4()), originals={}, outcomes={})
    assert control.through("start")["state"] == "OPEN"
    assert control.through("claim")["state"] == "CLAIMED"
    assert control.through("decision")["state"] == "APPROVED"
    result = control.through("exchange")
    assert control.send("exchange").json()["session_id"] == result["session_id"]


@pytest.mark.parametrize("action", ["CANCEL", "REJECT"])
def test_unexchanged_terminal_cannot_add_approval_or_rewrite_start_and_claim(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    action: str,
) -> None:
    h = self_v2
    h.through("claim")
    terminal = _terminal_decision(h, action)
    _guard_rejects(
        database_connection,
        "UPDATE account.self_device_pairing SET approval_operation_id=%s WHERE ceremony_id=%s",
        (str(uuid4()), h.ceremony_id),
        "self_v2_approval_transition",
    )
    for kind, changes, error in (
        (
            "start",
            {"requested_at": (h.now() + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")},
            "self_v2_source_immutable",
        ),
        ("claim", {"app_version": "changed-storage-fixture"}, "self_v2_claim_immutable"),
    ):
        parsed = parse_self_request(SelfOperation("self." + kind), h.command(kind, **changes))
        _guard_rejects(
            database_connection,
            f"UPDATE account.self_device_pairing SET {kind}_document=%s::jsonb,"
            f"{kind}_request_sha256=%s,"
            f"{kind}_original_document_sha256=%s WHERE ceremony_id=%s",
            (
                json.dumps(dict(parsed.document)),
                parsed.request_sha256,
                parsed.original_document_sha256,
                h.ceremony_id,
            ),
            error,
        )
    # Terminal recipient poll may still update only last_polled_at.
    assert h.send("poll").status_code == 200
    stored = database_connection.execute(
        (
            "SELECT state,revision,last_polled_at FROM account.self_device_pairing WHERE "
            "ceremony_id=%s"
        ),
        (h.ceremony_id,),
    ).fetchone()
    assert stored == (terminal["state"], terminal["revision"], h.now())


def test_cancelled_after_approval_cannot_add_complete_exchange_evidence(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
) -> None:
    h = self_v2
    h.through("decision")
    # Create a real result session for the same owner/key/platform, so the full
    # failed UPDATE below has valid result FKs and fresh binding/exchange IDs.
    control = replace(h, ceremony_id=str(uuid4()), originals={}, outcomes={})
    binding = control.through("exchange")
    refresh_hash = json.loads(control.originals["exchange"])["next_refresh_token_sha256"]
    parsed = parse_self_request(
        SelfOperation.EXCHANGE, h.command("exchange", next_refresh_token_sha256=refresh_hash)
    )
    document = dict(parsed.document)
    terminal = _terminal_decision(h, "CANCEL")
    _guard_rejects(
        database_connection,
        "UPDATE account.self_device_pairing SET "
        "exchange_id=%s,exchange_document=%s::jsonb,exchange_request_sha"
        "256=%s,"
        "exchange_original_document_sha256=%s,binding_commit_id=%s,result_device_id=%s,result_session_id=%s,"
        "receipt_expires_at=%s,state='EXCHANGED',revision=revision+1 WHERE ceremony_id=%s",
        (
            document["exchange_id"],
            json.dumps(document),
            parsed.request_sha256,
            parsed.original_document_sha256,
            document["binding_commit_id"],
            binding["device_id"],
            binding["session_id"],
            binding["receipt_expires_at"],
            h.ceremony_id,
        ),
        "self_v2_exchange_transition",
    )
    assert h.send("poll").json()["state"] == terminal["state"]


@pytest.mark.parametrize("kind", ["start", "claim", "poll", "decision", "exchange", "status"])
def test_every_allowed_scalar_rejects_nested_secret_objects_arrays_and_unknown_root(
    self_v2: SelfHarness, database_connection: Connection[Any], kind: str
) -> None:
    h = self_v2
    h.through("exchange")
    body = h.send("status").json() if kind == "status" else json.loads(h.command(kind))
    assert database_connection.execute(
        "SELECT app_private.valid_self_document_v2(%s::jsonb,%s)", (json.dumps(body), kind)
    ).fetchone() == (True,)
    for name in body:
        for nested in ({"access_token": "forbidden"}, ["recoverable-secret"]):
            bad = {**body, name: nested}
            assert database_connection.execute(
                "SELECT app_private.valid_self_document_v2(%s::jsonb,%s)", (json.dumps(bad), kind)
            ).fetchone() == (False,)
    assert database_connection.execute(
        "SELECT app_private.valid_self_document_v2(%s::jsonb,%s)",
        (json.dumps({**body, "encrypted_response": "forbidden"}), kind),
    ).fetchone() == (False,)


def test_actual_insert_check_rejects_root_and_nested_aliases(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    h.through("start")

    def copy(addition: dict[str, Any]) -> tuple[str, str]:
        ceremony_id, operation_id = str(uuid4()), str(uuid4())
        # Synthetic storage copy only: D is retained, never claimed as a newly
        # signed request or an authorization. Fresh PK/start IDs on EVERY attempt;
        # OPEN has no claim/exchange/binding IDs to collide with other rows.
        database_connection.execute(
            "INSERT INTO account.self_device_pairing SELECT "
            "(jsonb_populate_record(NULL::account.self_device_pairing,to_jsonb(s)||"
            "jsonb_build_object('ceremony_id',%s::text,'start_operation_id',%s::text,'start_document',"
            "start_document||jsonb_build_object('ceremony_id',%s::text,'oper"
            "ation_id',%s::text)||%s::jsonb))).* "
            "FROM account.self_device_pairing s WHERE ceremony_id=%s",
            (
                ceremony_id,
                operation_id,
                ceremony_id,
                operation_id,
                json.dumps(addition),
                h.ceremony_id,
            ),
        )
        return ceremony_id, operation_id

    ceremony_id, operation_id = copy({})
    assert database_connection.execute(
        "SELECT state,contract_version,start_document->>'ceremony_id',"
        "start_document->>'operation_id',source_platform,claim_id,exchange_id,binding_commit_id "
        "FROM account.self_device_pairing WHERE ceremony_id=%s",
        (ceremony_id,),
    ).fetchone() == ("OPEN", "v2", ceremony_id, operation_id, h.profile.platform, None, None, None)
    before = _stored_self_rows(database_connection)
    database_connection.commit()
    for addition in (
        {"encrypted_response": "forbidden"},
        {"rendezvous_secret_sha256": {"rendezvous_secret": h.rendezvous}},
        {"expected_api_origin": ["recoverable-secret"]},
    ):
        with pytest.raises(CheckViolation) as caught, database_connection.transaction():
            copy(addition)
        assert caught.value.sqlstate == "23514"
        assert caught.value.diag.constraint_name == "self_pairing_v2_evidence_check"
        assert _stored_self_rows(database_connection) == before
        database_connection.commit()


def test_command_original_and_result_cannot_be_rewritten(
    self_v2: SelfHarness, database_connection: Connection[Any]
) -> None:
    h = self_v2
    h.through("decision")
    operation = json.loads(h.originals["decision"])["operation_id"]
    before = database_connection.execute(
        "SELECT to_jsonb(c) FROM account.self_pairing_command c WHERE operation_id=%s", (operation,)
    ).fetchone()
    database_connection.commit()
    for assignment in (
        'original_document=original_document || \'{"access_token":"forbidden"}\'::jsonb',
        "original_document_sha256=decode(repeat('00',32),'hex')",
        'result=result || \'{"encrypted_response":"forbidden"}\'::jsonb',
    ):
        with pytest.raises(CheckViolation), database_connection.transaction():
            database_connection.execute(
                f"UPDATE account.self_pairing_command SET {assignment} WHERE operation_id=%s",
                (operation,),
            )
    assert (
        database_connection.execute(
            "SELECT to_jsonb(c) FROM account.self_pairing_command c WHERE operation_id=%s",
            (operation,),
        ).fetchone()
        == before
    )


@pytest.mark.parametrize("partial_table", ["self_device_pairing", "self_pairing_command"])
def test_downgrade_preflight_catches_partial_legacy_labelled_fields_in_each_table(
    profile: ProfileHarness,
    database_connection: Connection[Any],
    database_harness: DatabaseHarness,
    database_name: str,
    partial_table: str,
) -> None:
    old = _legacy_pair(profile, database_connection)
    old.ready()
    # Deliberately emulate damaged/future partial evidence in a DISPOSABLE DB.
    # No real data or v2 proof is changed; this exercises preflight independently
    # from the normal INSERT CHECK that already rejects the same partial shape.
    constraint = (
        "self_pairing_v2_evidence_check"
        if partial_table == "self_device_pairing"
        else "self_pairing_command_v2_evidence_check"
    )
    database_connection.execute(f"ALTER TABLE account.{partial_table} DROP CONSTRAINT {constraint}")
    database_connection.execute(f"UPDATE account.{partial_table} SET source_platform='ANDROID'")
    database_connection.commit()
    before = snapshot_schema(database_connection)
    database_connection.commit()
    with pytest.raises(DBAPIError, match="Refusing to discard retained SELF v2 evidence"):
        database_harness.downgrade(database_name, "0071_device_admission_v2")
    assert snapshot_schema(database_connection) == before
    assert database_connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "0074_device_deletion_v2",
    )
