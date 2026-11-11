"""UNEXECUTED protocol-1 preservation, retained revision and refusal fixtures."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from autplay.domain.device_compatibility import MAX_REVISION
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from psycopg import Connection
from typed_assertions import require_instance

from .test_device_profile_v2 import ROTATE_DOMAIN, signed, snapshot
from .test_device_profile_v2 import profile as profile
from .test_profile_pairing_m5b import pairing_service as pairing_service
from .test_sync_binding_v2 import D1, PREFIX, BindingHarness, assert_error, cursors
from .test_sync_binding_v2 import binding as binding
from .test_sync_binding_v2 import qualified as qualified
from .test_sync_binding_v2_history import allow_disposable_corruption
from .test_sync_runtime import _event


def legacy_binding(body: dict[str, Any]) -> dict[str, Any]:
    return {
        name: body[name]
        for name in ("protocol_version", "device_id", "server_profile_id", "journal_epoch")
    }


def negotiation_rows(connection: Connection[Any]) -> object:
    result = (
        connection.execute(
            "SELECT * FROM account.server_instance ORDER BY server_instance_id"
        ).fetchall(),
        connection.execute(
            "SELECT * FROM account.device_capability_revision ORDER BY user_id,device_id"
        ).fetchall(),
    )
    connection.commit()
    return result


def test_protocol_one_push_pull_and_status_progress_survives_binding_readback(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    wire = legacy_binding(body)
    event = _event(
        h.actor,
        uuid4(),
        1,
        "USER_TRACK_REF_CREATED",
        "USER_TRACK_REF",
        {"title": "B1 synthetic offline fact"},
    )
    original_event = dict(event)
    pushed = h.client.post(
        "/api/v1/sync/push", json={**wire, "events": [event]}, headers=h.headers()
    )
    assert pushed.status_code == 200 and pushed.json()["acknowledged_through_device_sequence"] == 1
    pulled = h.client.get("/api/v1/sync/pull", params=wire, headers=h.headers())
    assert pulled.status_code == 200
    acknowledged = h.client.get(
        "/api/v1/sync/pull",
        params={**wire, "cursor": pulled.json()["next_cursor"]},
        headers=h.headers(),
    )
    assert acknowledged.status_code == 200
    status = h.client.get("/api/v1/sync/status", params=wire, headers=h.headers())
    assert status.status_code == 200 and status.json()["protocol_version"] == 1
    cursor, evidence = cursors(database_connection), negotiation_rows(database_connection)
    assert h.post(body).status_code == 200
    assert (
        cursors(database_connection) == cursor and negotiation_rows(database_connection) == evidence
    )
    assert event == original_event  # no rehash/profile/sequence rewrite by B1


def test_direct_unbound_bootstrap_is_retained_prior_use_and_never_binds(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    wire = legacy_binding(body)
    response = h.client.post(
        "/api/v1/sync/bootstrap",
        json={
            **wire,
            "reason": "FIRST_SYNC",
            "snapshot_id": None,
            "page_token": None,
            "pending_local_event_count": 0,
        },
        headers=h.headers(),
    )
    assert response.status_code == 200 and not cursors(database_connection)
    for path in ("pull", "status"):
        denied = h.client.get("/api/v1/sync/" + path, params=wire, headers=h.headers())
        assert (
            denied.status_code == 409 and denied.json()["error"]["code"] == "DEVICE_RESET_REQUIRED"
        )
    before = database_connection.execute(
        "SELECT * FROM sync.bootstrap_session ORDER BY snapshot_id"
    ).fetchall()
    database_connection.commit()
    assert before
    assert_error(h.post(body), "DEVICE_RESET_REQUIRED", 409)
    assert not cursors(database_connection)
    assert (
        database_connection.execute(
            "SELECT * FROM sync.bootstrap_session ORDER BY snapshot_id"
        ).fetchall()
        == before
    )
    database_connection.commit()


def test_real_session_rotation_preserves_exact_binding_but_old_bearer_is_revoked(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    cursor = cursors(database_connection)
    rotation, _refresh = h.profile.rotation(h.binding, h.enrollment, h.refresh)
    raw = signed(rotation, h.key, ROTATE_DOMAIN)
    result, replayed = h.profile.service.session_rotate(raw)
    assert not replayed
    # Rotation issues the successor at PostgreSQL time. Advance the controllable
    # consumer clock past that actual commit before authenticating either bearer.
    row = database_connection.execute(
        "SELECT issued_at, clock_timestamp() FROM account.user_session WHERE session_id=%s",
        (UUID(require_instance(result["session_id"], str)),),
    ).fetchone()
    database_connection.commit()
    assert row is not None and isinstance(row[1], datetime) and row[0] <= row[1]
    assert h.profile.clock["now"] == row[0]
    h.profile.clock["now"] = row[1]
    assert_error(h.post(body), "authentication_required", 401)
    h.actor = type(h.actor)(
        h.actor.user_id,
        h.actor.device_id,
        UUID(require_instance(result["session_id"], str)),
        h.actor.role,
    )
    h.token = require_instance(result["access_token"], str)
    h.set_policy(binding_enabled=False)
    evidence = negotiation_rows(database_connection)
    assert h.post(body).status_code == 200
    assert (
        cursors(database_connection) == cursor and negotiation_rows(database_connection) == evidence
    )


def test_revision_exhaustion_is_closed_for_both_gets_but_readback_never_allocates(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    discovery = h.client.get(PREFIX + "/discovery").json()["payload"]
    database_connection.execute(
        "INSERT INTO account.device_capability_revision(server_instance_"
        "id,identity_epoch,user_id,device_id,"
        "capability_revision,effective_content_sha256,identity_thumbprin"
        "t_sha256,device_key_thumbprint_sha256,created_at,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            UUID(discovery["server_instance_id"]),
            discovery["identity_epoch"],
            h.actor.user_id,
            h.actor.device_id,
            MAX_REVISION,
            b"z" * 32,
            bytes.fromhex(discovery["identity_thumbprint_sha256"]),
            public_key_thumbprint(public_spki(h.key)),
            h.profile.clock["now"],
            h.profile.clock["now"],
        ),
    )
    database_connection.commit()
    evidence = negotiation_rows(database_connection)
    for prefix in (PREFIX, D1):
        assert h.client.get(prefix + "/capabilities", headers=h.headers()).status_code == 503
    body = {
        "contract_version": "v2",
        "schema_version": 2,
        "binding_contract_version": 2,
        "protocol_version": 1,
        "user_id": str(h.actor.user_id),
        "device_id": str(h.actor.device_id),
        "server_profile_id": str(uuid4()),
        "journal_epoch": str(uuid4()),
        "supported_event_schema_versions": [1],
        "expected_capability_revision": MAX_REVISION,
        **{
            "expected_" + key: discovery[key]
            for key in (
                "server_instance_id",
                "identity_epoch",
                "identity_thumbprint_sha256",
                "api_origin",
                "stream_origin",
            )
        },
    }
    assert_error(h.post(body), "sync_binding_capability_changed", 409)
    database_connection.execute(
        "INSERT INTO sync.device_sync_cursor(user_id,device_id,journal_epoch) VALUES(%s,%s,%s)",
        (h.actor.user_id, h.actor.device_id, UUID(body["journal_epoch"])),
    )
    database_connection.commit()
    assert h.post(body).status_code == 200 and negotiation_rows(database_connection) == evidence


def test_blocked_key_precedes_owner_trust_history_and_replay(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    body = h.request()
    assert h.post(body).status_code == 200
    database_connection.execute(
        "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) VALUES(%s,%s)",
        (h.actor.user_id, public_key_thumbprint(public_spki(h.key))),
    )
    database_connection.commit()
    before, cursor, evidence = (
        snapshot(database_connection),
        cursors(database_connection),
        negotiation_rows(database_connection),
    )
    assert_error(
        h.post({**body, "user_id": str(uuid4()), "expected_identity_epoch": 99}),
        "authentication_required",
        401,
    )
    assert h.client.get(PREFIX + "/capabilities", headers=h.headers()).status_code == 401
    assert snapshot(database_connection) == before and cursors(database_connection) == cursor
    assert negotiation_rows(database_connection) == evidence


def test_complete_current_key_needs_no_matching_history(
    qualified: BindingHarness, database_connection: Connection[Any]
) -> None:
    h = qualified
    allow_disposable_corruption(database_connection, "enrollment_exchange_receipt")
    database_connection.execute(
        "DELETE FROM account.enrollment_exchange_receipt WHERE device_id=%s", (h.actor.device_id,)
    )
    database_connection.commit()
    body = h.request()
    assert h.post(body).status_code == 200
