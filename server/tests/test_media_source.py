"""Future M1 pure fault cases. SOURCE ONLY: not imported or executed at authoring."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from autplay.application.media_source import MediaSourceService, PreparedMediaSourceReply
from autplay.domain.auth import AccessTokenClaims, AccountRole, Principal
from autplay.domain.media_source import (
    MediaSourceError,
    MediaSourceSnapshotRequestV1,
    parse_snapshot,
)
from autplay.ports.media_source import (
    ActorFacts,
    ConfiguredM5Identity,
    DatabaseClockSample,
    ElapsedMark,
    MediaSourcePolicy,
    RelationshipCapture,
    SourceFacts,
)
from typed_assertions import present, replaced

T = datetime(2026, 10, 16, tzinfo=UTC)


class Fixture:
    """One coupled DB anchor/elapsed domain. Structural bytes are not crypto proof."""

    def __init__(self) -> None:
        self.ns = 0
        self.watch_offset = 0
        self.read_delay = 0
        self.settlement_delay = 0
        self.clock_delay: float = 0
        self.reads: list[str] = []
        self.principal = Principal(UUID(int=1), UUID(int=2), UUID(int=3), AccountRole.USER)
        self.claims = AccessTokenClaims(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            UUID(int=4),
            T - timedelta(seconds=1),
            T - timedelta(seconds=1),
            T + timedelta(seconds=60),
        )
        self.identity = ConfiguredM5Identity(
            UUID(int=5),
            1,
            b"structural-public-identity",
            bytes(32),
            "https://api.invalid",
            "https://stream.invalid",
        )
        self.policy = MediaSourcePolicy(True, self.identity, m5_capability_revision=3)
        actor = ActorFacts(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            UUID(int=1),
            UUID(int=1),
            UUID(int=2),
            "USER",
            "ACTIVE",
            None,
            T - timedelta(days=1),
            None,
            T - timedelta(seconds=2),
            T + timedelta(seconds=60),
            None,
            False,
        )
        source = SourceFacts(
            UUID(int=6),
            7,
            UUID(int=8),
            UUID(int=9),
            11,
            bytes(32),
            2048,
            "audio/flac",
            "flac",
            "flac",
        )
        self.capture = RelationshipCapture(actor, self.identity, 3, source)
        self.service = MediaSourceService(self, policy=lambda: self.policy, clock=self)

    def mark(self) -> ElapsedMark:
        return ElapsedMark(self.ns, self.ns + self.watch_offset)

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1_000_000_000)

    def read_relationship(
        self, principal: Principal, user_track_ref_id: UUID, *, started: ElapsedMark
    ) -> RelationshipCapture:
        self.reads.append("relationship")
        self.advance(self.read_delay)
        self.reads.append("read-transaction-settled")
        self.advance(self.settlement_delay)
        return self.capture

    def final_database_clock(self, *, started: ElapsedMark) -> DatabaseClockSample:
        self.reads.append("clock-only")
        before = self.mark()
        anchor = T + timedelta(microseconds=self.ns // 1000)
        self.advance(self.clock_delay)
        self.reads.append("clock-released")
        return DatabaseClockSample(anchor, before, self.mark())

    def prepare(self) -> PreparedMediaSourceReply:
        return self.service.prepare(
            self.principal,
            self.claims,
            MediaSourceSnapshotRequestV1(str(UUID(int=6))),
            started=self.mark(),
        )


def failure(fixture: Fixture, code: str) -> None:
    with pytest.raises(MediaSourceError) as caught:
        fixture.prepare().admit()
    assert caught.value.code == code


def test_exact_tuple_and_m5_revision_independent_of_d1() -> None:
    f = Fixture()
    document = parse_snapshot(f.prepare().admit())
    assert document.user_track_ref_row_version == 7
    assert document.audio_variant_row_version == 11
    assert document.recording_id == str(UUID(int=8))
    assert document.capability_revision == 3
    assert f.reads == ["relationship", "read-transaction-settled", "clock-only", "clock-released"]
    # No D1/B1 revision exists on this port; independently advancing it to 90/91
    # cannot alter the M5 response or mint a protected high-water publication.
    f.capture = replace(f.capture, m5_capability_revision=4)
    failure(f, "media_source_unavailable")
    f.policy = replace(f.policy, m5_capability_revision=4)
    assert parse_snapshot(f.prepare().admit()).capability_revision == 4


@pytest.mark.parametrize("delay_owner", ["read_delay", "settlement_delay"])
@pytest.mark.parametrize("expires", ["jwt", "session"])
def test_valid_at_dispatch_expired_after_last_blocking_read(delay_owner: str, expires: str) -> None:
    f = Fixture()
    if expires == "jwt":
        f.claims = replace(f.claims, expires_at=T + timedelta(seconds=2))
    else:
        f.capture = replace(
            present(f.capture),
            actor=replace(present(f.capture.actor), session_expires_at=T + timedelta(seconds=2)),
        )
    setattr(f, delay_owner, 5)
    failure(f, "authentication_required" if expires == "jwt" else "session_revoked")
    positive = Fixture()
    setattr(positive, delay_owner, 5)
    assert parse_snapshot(positive.prepare().admit()).wire_version == 1


def test_send_delay_and_clock_settlement_are_inside_conservative_bracket() -> None:
    f = Fixture()
    f.claims = replace(f.claims, expires_at=T + timedelta(seconds=2))
    f.clock_delay = 0.9
    prepared = f.prepare()
    f.advance(1.2)
    with pytest.raises(MediaSourceError, match="authentication_required"):
        prepared.admit()
    assert f.reads[-1] == "clock-released"


def test_issue_time_uses_lower_bound_while_expiry_uses_upper_bound() -> None:
    positive = Fixture()
    positive.clock_delay = 0.9
    assert parse_snapshot(positive.prepare().admit()).wire_version == 1
    future = Fixture()
    future.clock_delay = 0.9
    # Issue falls within the clock uncertainty bracket; upper expiry bound must
    # not be reused as evidence that the issue already happened.
    issued = T + timedelta(milliseconds=100)
    future.claims = replace(future.claims, issued_at=issued, not_before=issued)
    failure(future, "authentication_required")


@pytest.mark.parametrize("fault", ["uncertainty", "jump", "backwards", "total", "settings"])
def test_unusable_time_or_replaced_settings_refuses_success(fault: str) -> None:
    f = Fixture()
    if fault == "uncertainty":
        f.clock_delay = 1.001
    prepared = f.prepare()
    if fault == "jump":
        f.watch_offset += 1_000_000_000
    elif fault == "backwards":
        f.ns -= 1
    elif fault == "total":
        f.advance(15.001)
    elif fault == "settings":
        f.policy = replace(f.policy)  # equal content, changed reference still closes admission
    with pytest.raises(MediaSourceError, match="media_source_unavailable"):
        prepared.admit()


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_status", "DISABLED"),
        ("account_deleted_at", T),
        ("role", "GUEST"),
        ("device_revoked_at", T),
        ("device_key_blocked", True),
        ("device_user_id", UUID(int=20)),
        ("session_device_id", UUID(int=20)),
        ("session_user_id", UUID(int=20)),
        ("device_created_at", T + timedelta(seconds=1)),
        ("session_issued_at", T + timedelta(seconds=1)),
    ],
)
def test_all_captured_actor_gates_precede_masked_miss(field: str, value: object) -> None:
    f = Fixture()
    f.capture = replace(
        present(f.capture), source=None, actor=replaced(present(f.capture.actor), **{field: value})
    )
    failure(f, "authentication_required")


def test_gate_config_bounds_and_absent_source() -> None:
    f = Fixture()
    assert MediaSourcePolicy().media_source_snapshot_v1_enabled is False
    f.policy = replace(f.policy, media_source_snapshot_v1_enabled=False)
    failure(f, "media_source_unavailable")
    assert not f.reads
    for identity in (None, replace(present(f.identity), stream_origin="https://other.invalid")):
        f = Fixture()
        f.capture = replace(f.capture, identity=identity)
        failure(f, "media_source_unavailable")
    f = Fixture()
    f.capture = replace(f.capture, source=None)
    failure(f, "not_found")
    for field, value in (
        ("user_track_ref_row_version", 9007199254740992),
        ("total_bytes", 2147483649),
        ("media_type", "\u00e9" * 101),
    ):
        f = Fixture()
        f.capture = replace(
            present(f.capture), source=replaced(present(f.capture.source), **{field: value})
        )
        failure(f, "media_source_unavailable")


@pytest.mark.parametrize(
    "field,token",
    [
        ("wire_version", "true"),
        ("wire_version", "1.0"),
        ("wire_version", "1e0"),
        ("total_bytes", "null"),
        ("total_bytes", '"2048"'),
        ("identity_epoch", "9007199254740992"),
    ],
)
def test_wire_numbers_cannot_coerce(field: str, token: str) -> None:
    raw = Fixture().prepare().admit()
    document = json.loads(raw)
    old = json.dumps(document[field], separators=(",", ":")).encode()
    bad = raw.replace(
        b'"' + field.encode() + b'":' + old, b'"' + field.encode() + b'":' + token.encode()
    )
    with pytest.raises(ValueError):
        parse_snapshot(bad)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unknown", "nested", "utf8", "oversize", "uppercase"]
)
def test_raw_snapshot_rejects_untrusted_shapes(mutation: str) -> None:
    raw = Fixture().prepare().admit()
    if mutation == "duplicate":
        raw = raw[:-1] + b',"wire_version":1}'
    elif mutation == "unknown":
        raw = raw[:-1] + b',"url":"https://private.invalid"}'
    elif mutation == "nested":
        raw = raw.replace(b'"codec":"flac"', b'"codec":{"codec":"flac"}')
    elif mutation == "utf8":
        raw += b"\xff"
    elif mutation == "oversize":
        raw += b" " * 4096
    else:
        raw = raw.replace(str(UUID(int=9)).encode(), b"00000000-0000-0000-0000-00000000000A")
    with pytest.raises(ValueError):
        parse_snapshot(raw)
