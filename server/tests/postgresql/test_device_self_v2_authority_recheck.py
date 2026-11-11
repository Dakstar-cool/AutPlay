"""UNEXECUTED final-read/clock/first-write SELF regressions on disposable PostgreSQL.

Authoring only: no imports, hashes, signatures, SQL, HTTP or checks were executed.
The imported harness supplies real M5, source V2 family and recipient proofs for
both source/recipient platforms, including mixed pairs. Hooks only move the shared
fixture clock AFTER the real final source read; crypto and authentication stay real.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.self_device_pairing import (
    SqlAlchemySelfPairingRepositoryV2,
    SqlAlchemySelfPairingV2UnitOfWorkFactory,
)
from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import AccessTokenClaims, Principal
from autplay.domain.device_compatibility import NegotiationIdentity
from autplay.domain.device_self_v2 import CurrentSourceAuthoritySnapshot, SelfCeremonyV2
from autplay.domain.profile_pairing import public_spki
from autplay.ports.auth import AccessTokenCodec
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg import Connection
from sqlalchemy import Select, event, func, select
from sqlalchemy.orm import Session

from .test_device_profile_v2 import ROTATE_DOMAIN, signed
from .test_device_profile_v2 import profile as profile
from .test_device_self_v2 import PREFIX, SelfHarness
from .test_device_self_v2 import self_v2 as self_v2
from .test_profile_pairing_m5b import pairing_service as pairing_service


def _snapshot(connection: Connection[Any]) -> tuple[object, ...]:
    # Request rate accounting intentionally remains shared with v1. No pairing,
    # authority, identity, audit or capability row may change on a denied effect.
    result = tuple(
        connection.execute(statement).fetchall()
        for statement in (
            "SELECT * FROM account.server_instance ORDER BY server_instance_id",
            "SELECT * FROM account.user_account ORDER BY user_id",
            "SELECT * FROM account.device ORDER BY device_id",
            "SELECT * FROM account.user_session ORDER BY session_id",
            "SELECT * FROM account.self_device_pairing ORDER BY ceremony_id",
            "SELECT * FROM account.self_pairing_command ORDER BY operation_id",
            (
                "SELECT * FROM account.device_capability_revision ORDER BY "
                "server_instance_id,identity_epoch,user_id,device_id"
            ),
            "SELECT * FROM audit.audit_event ORDER BY audit_event_id",
        )
    )
    connection.commit()
    return result


@dataclass
class GrantProbe:
    delegate: AccessTokenCodec
    issued: list[tuple[Principal, datetime, datetime]] = field(default_factory=list)

    def issue(
        self, principal: Principal, *, token_id: UUID, issued_at: datetime, expires_at: datetime
    ) -> str:
        self.issued.append((principal, issued_at, expires_at))
        return self.delegate.issue(
            principal, token_id=token_id, issued_at=issued_at, expires_at=expires_at
        )

    def decode(self, token: str, *, now: datetime) -> AccessTokenClaims:
        return self.delegate.decode(token, now=now)


@dataclass
class LastReadProbe:
    facts: CurrentSourceAuthoritySnapshot | None = None
    clock_sampled: bool = False
    final_clock_reads: int = 0
    first_write: bool = False
    order: list[str] = field(default_factory=list)


@contextmanager
def _after_last_read(
    h: SelfHarness,
    monkeypatch: pytest.MonkeyPatch,
    final_time: datetime,
    *,
    disable: bool = False,
) -> Iterator[tuple[LastReadProbe, GrantProbe]]:
    probe, grants = LastReadProbe(), GrantProbe(h.service._access)
    original = SqlAlchemySelfPairingRepositoryV2.current_source_authority
    original_database_now = SqlAlchemySelfPairingRepositoryV2.database_now
    engine = h.profile.identity._sessions.kw["bind"]

    def load(
        repo: SqlAlchemySelfPairingRepositoryV2,
        row: SelfCeremonyV2,
        *,
        actor: Principal | None = None,
    ) -> CurrentSourceAuthoritySnapshot:
        facts = original(repo, row, actor=actor)  # real final SQL read and locks
        assert probe.facts is None
        probe.facts = facts
        probe.order.append("loaded")
        h.profile.clock["now"] = final_time
        if disable:
            h.family_gate["enabled"] = False
        return facts

    def clock(repo: SqlAlchemySelfPairingRepositoryV2) -> datetime:
        now = original_database_now(repo)  # includes the real PostgreSQL sample
        if probe.facts is not None and not probe.clock_sampled:
            assert probe.final_clock_reads == 1
            probe.clock_sampled = True
            probe.order.append("clock")
        return now

    def observe(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if probe.first_write:
            return
        verb = statement.lstrip().split(None, 1)[0].upper()
        compiled = getattr(context, "compiled", None)
        table = getattr(getattr(compiled, "statement", None), "table", None)
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            if getattr(table, "name", None) == "self_pairing_rate":
                return  # shared request accounting runs before the service
            assert probe.facts is not None and probe.clock_sampled, (
                "write before final authority validation"
            )
            probe.first_write = True
            probe.order.append("write")
            return
        if probe.facts is None:
            return
        query = getattr(compiled, "statement", None)
        if (
            not probe.clock_sampled
            and isinstance(query, Select)
            and query.compare(select(func.clock_timestamp()))
        ):
            probe.final_clock_reads += 1
            assert probe.final_clock_reads == 1
            return
        # A targeted SQL sentinel catches Session.get/lazy loads and hidden
        # require_source calls after the snapshot/clock, before any first effect.
        assert verb not in {"SELECT", "WITH", "SHOW"}, "storage read after final source snapshot"

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(SqlAlchemySelfPairingRepositoryV2, "current_source_authority", load)
            patch.setattr(SqlAlchemySelfPairingRepositoryV2, "database_now", clock)
            patch.setattr(h.service, "_access", grants)
            yield probe, grants
    finally:
        event.remove(engine, "before_cursor_execute", observe)


def _prepare(h: SelfHarness, kind: str) -> None:
    if kind != "start":
        h.through({"claim": "start", "decision": "claim", "exchange": "decision"}[kind])


def _outcome(
    response: Any, *, accepted: bool, code: str = "self_pairing_unavailable", status: int = 403
) -> None:
    assert response.status_code == (200 if accepted else status), response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    if not accepted:
        assert set(response.json()) == {"error"}
        assert response.json()["error"]["code"] == code
        assert set(response.json()["error"]) == {"code", "message", "request_id", "retryable"}


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange"])
@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("edge_us", [-1, 0, 1])
def test_freshness_both_edges_are_sampled_after_last_source_read(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    direction: int,
    edge_us: int,
) -> None:
    h = self_v2
    _prepare(h, kind)
    raw = h.command(kind)  # valid digest and ORIGINAL real recipient proof
    before = _snapshot(database_connection)
    final_time = h.now() + direction * (timedelta(seconds=120) + timedelta(microseconds=edge_us))
    accepted = edge_us <= 0  # exactly +/-120s is still valid; no safety margin
    with _after_last_read(h, monkeypatch, final_time) as (probe, grants):
        response = h.send(kind, raw)
    _outcome(response, accepted=accepted)
    assert probe.order == (["loaded", "clock", "write"] if accepted else ["loaded", "clock"])
    assert len(grants.issued) == int(accepted and kind == "exchange")
    if not accepted:
        assert _snapshot(database_connection) == before
    elif kind == "start":
        times = database_connection.execute(
            "SELECT created_at,expires_at FROM account.self_device_pairing WHERE ceremony_id=%s",
            (h.ceremony_id,),
        ).fetchone()
        database_connection.commit()
        # START has no pre-existing ceremony deadline; its fixed 900s begins at
        # the final accepted sample, not at an earlier storage-read timestamp.
        assert times == (final_time, final_time + timedelta(seconds=900))
    elif kind == "exchange":
        times = database_connection.execute(
            "SELECT issued_at,expires_at FROM account.user_session WHERE session_id=%s",
            (response.json()["session_id"],),
        ).fetchone()
        database_connection.commit()
        assert times == (final_time, final_time + timedelta(days=90))


@pytest.mark.parametrize("kind", ["claim", "decision", "exchange"])
@pytest.mark.parametrize("edge_us", [-1, 0, 1])
def test_ceremony_deadline_crosses_last_read_while_body_and_source_stay_live(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    edge_us: int,
) -> None:
    h = self_v2
    _prepare(h, kind)
    deadline = datetime.fromisoformat(h.outcomes["start"]["expires_at"].replace("Z", "+00:00"))
    h.profile.clock["now"] = deadline - timedelta(seconds=1)
    raw = h.command(kind)
    before = _snapshot(database_connection)
    with _after_last_read(h, monkeypatch, deadline + timedelta(microseconds=edge_us)) as (
        probe,
        grants,
    ):
        response = h.send(kind, raw)  # JWT issue/decode both use h.now(), before the hook
    accepted = edge_us < 0
    _outcome(response, accepted=accepted)
    assert probe.facts is not None and probe.facts.session_expires_at > h.now()
    assert probe.order == (["loaded", "clock", "write"] if accepted else ["loaded", "clock"])
    assert len(grants.issued) == int(accepted and kind == "exchange")
    if not accepted:
        assert _snapshot(database_connection) == before


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange"])
@pytest.mark.parametrize("edge_us", [-1, 0, 1])
def test_source_session_deadline_crosses_last_read_while_other_gates_stay_live(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    edge_us: int,
) -> None:
    h = self_v2
    _prepare(h, kind)
    deadline = h.now() + timedelta(seconds=1)
    database_connection.execute(
        "UPDATE account.user_session SET expires_at=%s WHERE session_id=%s",
        (deadline, h.actor.session_id),
    )
    database_connection.commit()
    raw = h.command(kind)
    before = _snapshot(database_connection)
    with _after_last_read(h, monkeypatch, deadline + timedelta(microseconds=edge_us)) as (
        probe,
        grants,
    ):
        response = h.send(kind, raw)  # valid bearer reaches service BEFORE its source expires
    accepted = edge_us < 0
    _outcome(response, accepted=accepted)
    assert probe.facts is not None and probe.facts.session_expires_at == deadline
    assert probe.facts.session_id == h.actor.session_id
    assert probe.order == (["loaded", "clock", "write"] if accepted else ["loaded", "clock"])
    assert len(grants.issued) == int(accepted and kind == "exchange")
    if not accepted:
        assert _snapshot(database_connection) == before


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange"])
def test_gate_change_after_final_read_denies_without_any_pairing_write_or_grant(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    h = self_v2
    _prepare(h, kind)
    raw, before = h.command(kind), _snapshot(database_connection)
    with _after_last_read(h, monkeypatch, h.now(), disable=True) as (probe, grants):
        response = h.send(kind, raw)
    _outcome(response, accepted=False, code="device_contract_unavailable", status=503)
    assert probe.order == ["loaded", "clock"] and grants.issued == []
    assert _snapshot(database_connection) == before


@pytest.mark.parametrize("kind", ["start", "claim", "decision", "exchange"])
def test_historical_exact_replay_skips_first_effect_freshness_and_snapshot(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    h = self_v2
    first = h.through(kind)
    h.profile.clock["now"] += timedelta(seconds=121)
    h.family_gate["enabled"] = False
    before = _snapshot(database_connection)

    def forbidden_snapshot(*args: Any, **kwargs: Any) -> CurrentSourceAuthoritySnapshot:
        raise AssertionError("historical replay entered first-effect authority snapshot")

    with monkeypatch.context() as patch:
        patch.setattr(
            SqlAlchemySelfPairingRepositoryV2, "current_source_authority", forbidden_snapshot
        )
        response = h.send(kind, h.originals[kind])
    _outcome(response, accepted=True)
    if kind == "exchange":
        assert response.json()["replayed"] is True
        assert response.json()["session_id"] == first["session_id"]
    else:
        assert response.json() == first
    assert _snapshot(database_connection) == before


@pytest.mark.parametrize("action", ["REJECT", "CANCEL"])
def test_gate_off_closure_and_terminal_receipt_remain_available(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    h = self_v2
    h.through("claim")
    raw = h.command("decision", action=action, comparison_code=None)
    h.profile.gate["enabled"] = h.family_gate["enabled"] = False
    with _after_last_read(h, monkeypatch, h.now()) as (probe, grants):
        response = h.send("decision", raw)
    _outcome(response, accepted=True)
    assert response.json()["state"] == {"REJECT": "REJECTED", "CANCEL": "CANCELLED"}[action]
    assert probe.order == ["loaded", "clock", "write"] and grants.issued == []
    before = _snapshot(database_connection)
    h.profile.clock["now"] += timedelta(seconds=121)
    replay = h.send("decision", raw)
    _outcome(replay, accepted=True)
    assert replay.json() == response.json() and _snapshot(database_connection) == before


def test_rotated_result_session_is_never_resurrected_by_exact_exchange_replay(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = self_v2
    h.through("decision")
    refresh = secrets.token_urlsafe(32)
    raw = h.command(
        "exchange", next_refresh_token_sha256=hashlib.sha256(refresh.encode("ascii")).hexdigest()
    )
    response = h.send("exchange", raw)
    _outcome(response, accepted=True)
    binding = response.json()
    rotation, _ = h.profile.rotation(binding, h.source, refresh)
    rotation["platform"] = h.recipient_platform
    successor, replayed = h.profile.service.session_rotate(signed(rotation, h.key, ROTATE_DOMAIN))
    assert replayed is False and successor["session_id"] != binding["session_id"]
    before, grants = _snapshot(database_connection), GrantProbe(h.service._access)
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "_access", grants)
        denied = h.send("exchange", raw)
    _outcome(denied, accepted=False)
    assert grants.issued == [] and _snapshot(database_connection) == before


def test_real_m5_new_configured_key_vs_stored_old_key_is_safe_503(
    self_v2: SelfHarness,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = self_v2
    h.through("start")  # establishes persisted OLD M5 and independently valid source
    assert h.send("status").status_code == 200
    raw = h.command("start", ceremony_id=str(uuid4()), operation_id=str(uuid4()))
    token = h.token()
    claims = h.profile.identity._access.decode(token, now=h.now())
    assert (claims.user_id, claims.device_id, claims.session_id) == (
        h.actor.user_id,
        h.actor.device_id,
        h.actor.session_id,
    )
    old = h.profile.identity
    new_key = ec.generate_private_key(ec.SECP256R1())
    configured = ProfilePairingService(
        old._sessions,
        private_key=new_key,
        label_hint=old._label,
        api_origin=old._api,
        stream_origin=old._stream,
        access_tokens=old._access,
        access_ttl=old._access_ttl,
    )
    assert public_spki(new_key) != public_spki(old._key)
    calls: list[str] = []

    def actual_m5(session: Session) -> NegotiationIdentity:
        calls.append("M5")
        # Real _instance raises RuntimeError on the persisted/configured key
        # disagreement; no fake ValueError callback or crypto bypass.
        return configured.device_contract_identity(session, h.now())

    before, grants = _snapshot(database_connection), GrantProbe(h.service._access)
    with monkeypatch.context() as patch:
        patch.setattr(
            h.service, "_units", SqlAlchemySelfPairingV2UnitOfWorkFactory(old._sessions, actual_m5)
        )
        patch.setattr(h.service, "_access", grants)
        response = h.client().post(
            PREFIX,
            content=raw,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        )
    _outcome(response, accepted=False, code="device_contract_unavailable", status=503)
    assert calls == ["M5"] and grants.issued == []
    assert _snapshot(database_connection) == before
    for private in (
        token,
        h.rendezvous,
        h.poll_secret,
        old._api,
        old._stream,
        base64.b64encode(public_spki(old._key)).decode("ascii"),
        base64.b64encode(public_spki(new_key)).decode("ascii"),
        "profile identity key differs",
        "RuntimeError",
        "Traceback",
    ):
        assert private not in response.text
