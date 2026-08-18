# ruff: noqa: E501
"""Durable device-bound Wave repository; each operation uses one SQL transaction."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autplay.domain.auth import Principal
from autplay.domain.wave import (
    Availability,
    QueueEntry,
    WaveConflict,
    WaveForbidden,
    WaveRoom,
    new_room_code,
)


class SqlAlchemyWaveService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(
        self, principal: Principal, now: datetime, allow_users: tuple[UUID, ...] = ()
    ) -> WaveRoom:
        room_id, code = uuid4(), new_room_code()
        expiry = now + timedelta(hours=6)
        with self._sessions.begin() as s:
            s.execute(
                text(
                    "INSERT INTO wave.room(room_id,room_code_sha256,host_user_id,host_device_id,expires_at,created_at) VALUES(:id,:hash,:user,:device,:expiry,:now)"
                ),
                {
                    "id": room_id,
                    "hash": sha256(code.encode()).digest(),
                    "user": principal.user_id,
                    "device": principal.device_id,
                    "expiry": expiry,
                    "now": now,
                },
            )
            s.execute(
                text(
                    "INSERT INTO wave.member(room_id,user_id,device_id,role,status,joined_at,last_present_at) VALUES(:room,:user,:device,'HOST','JOINED',:now,:now)"
                ),
                {
                    "room": room_id,
                    "user": principal.user_id,
                    "device": principal.device_id,
                    "now": now,
                },
            )
            if len(allow_users) > 7:
                raise ValueError("Wave allowlist exceeds seven invitees")
            for user_id in allow_users:
                if user_id == principal.user_id:
                    continue
                s.execute(
                    text("INSERT INTO wave.invitation(room_id,user_id) VALUES(:room,:user)"),
                    {"room": room_id, "user": user_id},
                )
        return WaveRoom(room_id, code, principal.user_id, now, expiry, {principal.user_id})

    def join(self, code: str, principal: Principal, now: datetime) -> WaveRoom:
        with self._sessions.begin() as s:
            room = (
                s.execute(
                    text(
                        "SELECT room_id FROM wave.room WHERE room_code_sha256=:hash AND state='OPEN' AND expires_at>:now FOR UPDATE"
                    ),
                    {"hash": sha256(code.upper().encode()).digest(), "now": now},
                )
                .mappings()
                .one()
            )
            s.execute(
                text(
                    "INSERT INTO wave.member(room_id,user_id,device_id,role,status,joined_at,last_present_at) SELECT :room,:user,:device,'MEMBER','JOINED',:now,:now WHERE EXISTS(SELECT 1 FROM wave.invitation WHERE room_id=:room AND user_id=:user) ON CONFLICT(room_id,device_id) DO UPDATE SET status='JOINED',last_present_at=excluded.last_present_at,left_at=NULL"
                ),
                {
                    "device": principal.device_id,
                    "now": now,
                    "room": room.room_id,
                    "user": principal.user_id,
                },
            )
            joined = s.execute(
                text(
                    "SELECT 1 FROM wave.member WHERE room_id=:room AND user_id=:user AND device_id=:device AND status='JOINED'"
                ),
                {"room": room.room_id, "user": principal.user_id, "device": principal.device_id},
            ).first()
            if joined is None:
                raise WaveForbidden()
            return self._snapshot(s, room.room_id, principal, now)

    def snapshot(self, room_id: UUID, principal: Principal, now: datetime) -> WaveRoom:
        with self._sessions.begin() as s:
            self._snapshot(s, room_id, principal, now)
        self.recover_host(room_id, now)
        with self._sessions.begin() as s:
            return self._snapshot(s, room_id, principal, now)

    def leave(self, room_id: UUID, principal: Principal, now: datetime) -> None:
        with self._sessions.begin() as s:
            self._snapshot(s, room_id, principal, now)
            host_device_id = cast(
                UUID,
                s.execute(
                    text("SELECT host_device_id FROM wave.room WHERE room_id=:room"),
                    {"room": room_id},
                ).scalar_one(),
            )
            active_count = cast(
                int,
                s.execute(
                    text(
                        "SELECT count(*) FROM wave.member WHERE room_id=:room AND status='JOINED'"
                    ),
                    {"room": room_id},
                ).scalar_one(),
            )
            if principal.device_id == host_device_id and active_count > 1:
                raise WaveConflict()
            kind = "CLOSE" if principal.device_id == host_device_id else "LEAVE"
            self._append_lifecycle_command(
                s,
                room_id,
                principal.user_id,
                principal.device_id,
                kind,
                {"device_left": True},
                now,
            )
            s.execute(
                text(
                    "UPDATE wave.member SET status='LEFT',left_at=:now WHERE room_id=:room AND device_id=:device"
                ),
                {"now": now, "room": room_id, "device": principal.device_id},
            )
            if kind == "CLOSE":
                s.execute(
                    text(
                        "UPDATE wave.room SET state='CLOSED',playback_state='PAUSED',closed_at=:now WHERE room_id=:room"
                    ),
                    {"now": now, "room": room_id},
                )

    def close(self, room_id: UUID, principal: Principal, now: datetime) -> None:
        with self._sessions.begin() as s:
            self._host(s, room_id, principal, now)
            self._append_lifecycle_command(
                s,
                room_id,
                principal.user_id,
                principal.device_id,
                "CLOSE",
                {},
                now,
            )
            s.execute(
                text(
                    "UPDATE wave.room SET state='CLOSED',playback_state='PAUSED',closed_at=:now WHERE room_id=:room"
                ),
                {"now": now, "room": room_id},
            )

    def transfer_host(
        self, room_id: UUID, principal: Principal, target_device_id: UUID, now: datetime
    ) -> None:
        with self._sessions.begin() as s:
            self._host(s, room_id, principal, now)
            target = (
                s.execute(
                    text(
                        "SELECT user_id FROM wave.member WHERE room_id=:room AND device_id=:device AND status='JOINED' FOR UPDATE"
                    ),
                    {"room": room_id, "device": target_device_id},
                )
                .mappings()
                .first()
            )
            if target is None:
                raise WaveForbidden()
            self._append_lifecycle_command(
                s,
                room_id,
                principal.user_id,
                principal.device_id,
                "TRANSFER",
                {"target_device_id": str(target_device_id)},
                now,
            )
            s.execute(
                text(
                    "UPDATE wave.room SET host_user_id=:user,host_device_id=:device,room_epoch=room_epoch+1 WHERE room_id=:room"
                ),
                {"user": target.user_id, "device": target_device_id, "room": room_id},
            )
            s.execute(
                text(
                    "UPDATE wave.member SET role=CASE WHEN device_id=:device THEN 'HOST' ELSE 'MEMBER' END WHERE room_id=:room"
                ),
                {"device": target_device_id, "room": room_id},
            )

    def recover_host(self, room_id: UUID, now: datetime) -> UUID | None:
        """After a 30-second host loss, elect the smallest present member deterministically."""
        with self._sessions.begin() as s:
            room = (
                s.execute(
                    text(
                        "SELECT host_user_id,host_device_id,host_lost_at,state FROM wave.room WHERE room_id=:room AND state IN ('OPEN','ORPHANED') FOR UPDATE"
                    ),
                    {"room": room_id},
                )
                .mappings()
                .one()
            )
            present = s.execute(
                text(
                    "SELECT 1 FROM wave.member WHERE room_id=:room AND device_id=:device AND status='JOINED' AND last_present_at>=:cutoff"
                ),
                {
                    "room": room_id,
                    "device": room.host_device_id,
                    "cutoff": now - timedelta(seconds=30),
                },
            ).first()
            if present is not None:
                if room.host_lost_at is not None or room.state == "ORPHANED":
                    self._append_lifecycle_command(
                        s,
                        room_id,
                        room.host_user_id,
                        room.host_device_id,
                        "TRANSFER",
                        {"recovered": True},
                        now,
                    )
                    s.execute(
                        text(
                            "UPDATE wave.room SET host_lost_at=NULL,state='OPEN',room_epoch=room_epoch+1 WHERE room_id=:room"
                        ),
                        {"room": room_id},
                    )
                    return cast(UUID, room.host_user_id)
                return None
            lost_at = room.host_lost_at or now
            s.execute(
                text(
                    "UPDATE wave.room SET host_lost_at=COALESCE(host_lost_at,:now) WHERE room_id=:room"
                ),
                {"now": now, "room": room_id},
            )
            if now < lost_at + timedelta(seconds=30):
                return None
            winner = (
                s.execute(
                    text(
                        "SELECT user_id,device_id FROM wave.member WHERE room_id=:room AND status='JOINED' AND last_present_at>=:cutoff ORDER BY joined_at,device_id LIMIT 1"
                    ),
                    {"room": room_id, "cutoff": now - timedelta(seconds=30)},
                )
                .mappings()
                .first()
            )
            if winner is None:
                if room.state != "ORPHANED":
                    self._append_lifecycle_command(
                        s,
                        room_id,
                        room.host_user_id,
                        room.host_device_id,
                        "PAUSE",
                        {"reason": "host_unavailable"},
                        now,
                    )
                    s.execute(
                        text(
                            "UPDATE wave.room SET state='ORPHANED',playback_state='PAUSED' WHERE room_id=:room"
                        ),
                        {"room": room_id},
                    )
                return None
            self._append_lifecycle_command(
                s,
                room_id,
                room.host_user_id,
                room.host_device_id,
                "TRANSFER",
                {"target_device_id": str(winner.device_id), "automatic": True},
                now,
            )
            s.execute(
                text(
                    "UPDATE wave.room SET host_user_id=:user,host_device_id=:device,host_lost_at=NULL,state='OPEN',room_epoch=room_epoch+1 WHERE room_id=:room"
                ),
                {"user": winner.user_id, "device": winner.device_id, "room": room_id},
            )
            s.execute(
                text(
                    "UPDATE wave.member SET role=CASE WHEN device_id=:device THEN 'HOST' ELSE 'MEMBER' END WHERE room_id=:room"
                ),
                {"device": winner.device_id, "room": room_id},
            )
            return cast(UUID, winner.user_id)

    def expire_due(self, now: datetime, limit: int = 100) -> int:
        """Materialize bounded terminal expiry transitions and durable EXPIRE commands."""
        if not 1 <= limit <= 100:
            raise ValueError("Wave expiry batch must be between 1 and 100")
        with self._sessions.begin() as s:
            due = list(
                s.execute(
                    text(
                        "SELECT room_id,host_user_id,host_device_id FROM wave.room "
                        "WHERE state IN ('OPEN','ORPHANED') AND expires_at<=:now "
                        "ORDER BY expires_at LIMIT :limit FOR UPDATE SKIP LOCKED"
                    ),
                    {"now": now, "limit": limit},
                ).mappings()
            )
            for room in due:
                self._append_lifecycle_command(
                    s,
                    room.room_id,
                    room.host_user_id,
                    room.host_device_id,
                    "EXPIRE",
                    {},
                    now,
                )
                s.execute(
                    text(
                        "UPDATE wave.room SET state='EXPIRED',playback_state='PAUSED',closed_at=:now WHERE room_id=:room"
                    ),
                    {"now": now, "room": room.room_id},
                )
            return len(due)

    def command(
        self,
        room_id: UUID,
        principal: Principal,
        kind: str,
        idempotency_key: str,
        request_hash: bytes,
        expected_queue_version: int,
        expected_sequence: int,
        recording_id: UUID | None,
        now: datetime,
    ) -> dict[str, object]:
        with self._sessions.begin() as s:
            prior = (
                s.execute(
                    text(
                        "SELECT command_sequence,request_sha256 FROM wave.command WHERE room_id=:room AND idempotency_key=:key"
                    ),
                    {"room": room_id, "key": idempotency_key},
                )
                .mappings()
                .first()
            )
            if prior is not None:
                if bytes(prior.request_sha256) != request_hash:
                    raise WaveConflict()
                return {"sequence": prior.command_sequence, "idempotent": True}
            room = self._host(s, room_id, principal, now)
            if room.version != expected_queue_version or room.command_sequence != expected_sequence:
                raise WaveConflict()
            sequence = expected_sequence + 1
            version = expected_queue_version
            if kind == "QUEUE" and recording_id is not None:
                s.execute(
                    text(
                        "INSERT INTO wave.queue_entry(queue_entry_id,room_id,recording_id,position) VALUES(:entry,:room,:recording,:position)"
                    ),
                    {
                        "entry": uuid4(),
                        "room": room_id,
                        "recording": recording_id,
                        "position": len(room.queue),
                    },
                )
                version += 1
            elif kind == "PLAY":
                raise ValueError("PLAY requires strict Wave start")
            elif kind not in {"PAUSE", "SEEK", "SKIP", "TRANSFER"}:
                raise ValueError("unsupported Wave command")
            s.execute(
                text(
                    "UPDATE wave.room SET queue_version=:version,command_sequence=:sequence WHERE room_id=:room"
                ),
                {"version": version, "sequence": sequence, "room": room_id},
            )
            s.execute(
                text(
                    "INSERT INTO wave.command(room_id,command_sequence,actor_user_id,actor_device_id,idempotency_key,request_sha256,expected_queue_version,expected_sequence,command_kind,command_document,created_at) VALUES(:room,:sequence,:user,:device,:key,:hash,:version,:expected,:kind,'{}'::jsonb,:now)"
                ),
                {
                    "room": room_id,
                    "sequence": sequence,
                    "user": principal.user_id,
                    "device": principal.device_id,
                    "key": idempotency_key,
                    "hash": request_hash,
                    "version": expected_queue_version,
                    "expected": expected_sequence,
                    "kind": kind,
                    "now": now,
                },
            )
            return {"sequence": sequence, "queue_version": version, "idempotent": False}

    def preflight(
        self,
        room_id: UUID,
        principal: Principal,
        queue_entry_id: UUID,
        recording_id: UUID,
        queue_version: int,
        availability: Availability,
        final_ready: bool,
        now: datetime,
    ) -> None:
        with self._sessions.begin() as s:
            self._member(s, room_id, principal, now)
            valid_entry = s.execute(
                text(
                    "SELECT 1 FROM wave.queue_entry q JOIN wave.room r ON r.room_id=q.room_id "
                    "WHERE q.room_id=:room AND q.queue_entry_id=:entry "
                    "AND q.recording_id=:recording AND q.removed_at IS NULL "
                    "AND r.queue_version=:version AND r.state='OPEN'"
                ),
                {
                    "room": room_id,
                    "entry": queue_entry_id,
                    "recording": recording_id,
                    "version": queue_version,
                },
            ).first()
            if valid_entry is None:
                raise WaveConflict()
            s.execute(
                text(
                    "INSERT INTO wave.preflight(room_id,user_id,device_id,queue_entry_id,recording_id,queue_version,availability,final_ready,source_checked_at,expires_at) VALUES(:room,:user,:device,:entry,:recording,:version,:availability,:ready,:now,:expiry) ON CONFLICT(room_id,device_id,queue_entry_id) DO UPDATE SET recording_id=excluded.recording_id,queue_version=excluded.queue_version,availability=excluded.availability,final_ready=excluded.final_ready,source_checked_at=excluded.source_checked_at,expires_at=excluded.expires_at"
                ),
                {
                    "room": room_id,
                    "user": principal.user_id,
                    "device": principal.device_id,
                    "entry": queue_entry_id,
                    "recording": recording_id,
                    "version": queue_version,
                    "availability": availability.value,
                    "ready": final_ready,
                    "now": now,
                    "expiry": now + timedelta(seconds=15 if final_ready else 30),
                },
            )

    def timing(
        self,
        room_id: UUID,
        principal: Principal,
        sequence: int,
        rtt_ms: int,
        offset_ms: int,
        uncertainty_ms: int,
        now: datetime,
        *,
        start_skew_ms: int | None = None,
        drift_ms: int | None = None,
    ) -> None:
        with self._sessions.begin() as s:
            self._member(s, room_id, principal, now)
            s.execute(
                text(
                    "INSERT INTO wave.timing_report(room_id,device_id,command_sequence,rtt_ms,offset_ms,uncertainty_ms,start_skew_ms,drift_ms,reported_at) VALUES(:room,:device,:sequence,:rtt,:offset,:uncertainty,:skew,:drift,:now) ON CONFLICT(room_id,device_id,command_sequence) DO UPDATE SET rtt_ms=excluded.rtt_ms,offset_ms=excluded.offset_ms,uncertainty_ms=excluded.uncertainty_ms,start_skew_ms=excluded.start_skew_ms,drift_ms=excluded.drift_ms,reported_at=excluded.reported_at"
                ),
                {
                    "room": room_id,
                    "device": principal.device_id,
                    "sequence": sequence,
                    "rtt": rtt_ms,
                    "offset": offset_ms,
                    "uncertainty": uncertainty_ms,
                    "skew": start_skew_ms,
                    "drift": drift_ms,
                    "now": now,
                },
            )

    def start(
        self,
        room_id: UUID,
        principal: Principal,
        queue_entry_id: UUID,
        recording_id: UUID,
        expected_queue_version: int,
        expected_sequence: int,
        now: datetime,
    ) -> dict[str, object]:
        """Durably emit PLAY only when every present member has current final readiness."""
        with self._sessions.begin() as s:
            room = self._host(s, room_id, principal, now)
            if room.version != expected_queue_version or room.command_sequence != expected_sequence:
                raise WaveConflict()
            gate = (
                s.execute(
                    text(
                        "SELECT count(*) AS present_count, "
                        "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM wave.preflight p "
                        "WHERE p.room_id=m.room_id AND p.device_id=m.device_id "
                        "AND p.queue_entry_id=:entry AND p.recording_id=:recording "
                        "AND p.queue_version=:version AND p.final_ready=true "
                        "AND p.availability<>'UNAVAILABLE' AND p.expires_at>:now)) AS ready_count, "
                        "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM wave.timing_report t "
                        "WHERE t.room_id=m.room_id AND t.device_id=m.device_id "
                        "AND t.command_sequence=:expected AND t.rtt_ms<=1000 "
                        "AND t.uncertainty_ms<=100 AND t.reported_at>=:clock_cutoff)) AS clock_count, "
                        "coalesce(max((SELECT t.rtt_ms FROM wave.timing_report t "
                        "WHERE t.room_id=m.room_id AND t.device_id=m.device_id "
                        "AND t.command_sequence=:expected ORDER BY t.reported_at DESC LIMIT 1)),0) AS max_rtt, "
                        "coalesce(max((SELECT t.uncertainty_ms FROM wave.timing_report t "
                        "WHERE t.room_id=m.room_id AND t.device_id=m.device_id "
                        "AND t.command_sequence=:expected ORDER BY t.reported_at DESC LIMIT 1)),0) AS max_uncertainty "
                        "FROM wave.member m WHERE m.room_id=:room AND m.status='JOINED' "
                        "AND m.last_present_at>=:presence_cutoff"
                    ),
                    {
                        "room": room_id,
                        "entry": queue_entry_id,
                        "recording": recording_id,
                        "version": expected_queue_version,
                        "expected": expected_sequence,
                        "now": now,
                        "presence_cutoff": now - timedelta(seconds=30),
                        "clock_cutoff": now - timedelta(seconds=60),
                    },
                )
                .mappings()
                .one()
            )
            valid_entry = s.execute(
                text(
                    "SELECT 1 FROM wave.queue_entry WHERE room_id=:room AND queue_entry_id=:entry "
                    "AND recording_id=:recording AND removed_at IS NULL"
                ),
                {
                    "room": room_id,
                    "entry": queue_entry_id,
                    "recording": recording_id,
                },
            ).first()
            sequence = expected_sequence + 1
            gate_ready = (
                valid_entry is not None
                and gate.present_count > 0
                and gate.ready_count == gate.present_count
                and gate.clock_count == gate.present_count
            )
            lead_ms = max(2_000, 3 * gate.max_rtt + 2 * gate.max_uncertainty + 250)
            kind = "PLAY" if gate_ready and lead_ms <= 8_000 else "START_ABORTED"
            effective_at = now + timedelta(milliseconds=lead_ms) if kind == "PLAY" else None
            s.execute(
                text(
                    "UPDATE wave.room SET command_sequence=:sequence,playback_state=:state,timeline_effective_at=:effective WHERE room_id=:room"
                ),
                {
                    "sequence": sequence,
                    "state": "PLAYING" if kind == "PLAY" else "PAUSED",
                    "effective": effective_at,
                    "room": room_id,
                },
            )
            s.execute(
                text(
                    "INSERT INTO wave.command(room_id,command_sequence,actor_user_id,actor_device_id,idempotency_key,request_sha256,expected_queue_version,expected_sequence,command_kind,command_document,effective_at,created_at) VALUES(:room,:sequence,:user,:device,:key,:hash,:version,:expected,:kind,jsonb_build_object('queue_entry_id',CAST(:entry AS text),'recording_id',CAST(:recording AS text),'effective_at',CAST(:effective_text AS text)),CAST(:effective_at AS timestamptz),:now)"
                ),
                {
                    "room": room_id,
                    "sequence": sequence,
                    "user": principal.user_id,
                    "device": principal.device_id,
                    "key": f"start-{sequence}",
                    "hash": sha256(f"{room_id}:{sequence}".encode()).digest(),
                    "version": expected_queue_version,
                    "expected": expected_sequence,
                    "kind": kind,
                    "entry": queue_entry_id,
                    "recording": recording_id,
                    "effective_text": effective_at.isoformat()
                    if effective_at is not None
                    else None,
                    "effective_at": effective_at,
                    "now": now,
                },
            )
            return {
                "sequence": sequence,
                "state": "PLAYING" if kind == "PLAY" else "PAUSED",
                "started": kind == "PLAY",
                "effective_at": effective_at.isoformat() if effective_at is not None else None,
            }

    def catch_up(
        self, room_id: UUID, principal: Principal, after_sequence: int, now: datetime
    ) -> list[dict[str, object]]:
        with self._sessions.begin() as s:
            self._member(s, room_id, principal, now)
            return [
                {
                    "sequence": x.command_sequence,
                    "kind": x.command_kind,
                    "payload": x.command_document,
                    "effective_at": x.effective_at.isoformat()
                    if x.effective_at is not None
                    else None,
                }
                for x in s.execute(
                    text(
                        "SELECT command_sequence,command_kind,command_document,effective_at FROM wave.command WHERE room_id=:room AND command_sequence>:after ORDER BY command_sequence LIMIT 100"
                    ),
                    {"room": room_id, "after": after_sequence},
                ).mappings()
            ]

    def _member(self, s: Session, room: UUID, p: Principal, now: datetime) -> None:
        if (
            s.execute(
                text(
                    "SELECT 1 FROM wave.member WHERE room_id=:room AND user_id=:user AND device_id=:device AND status='JOINED'"
                ),
                {"room": room, "user": p.user_id, "device": p.device_id},
            ).first()
            is None
        ):
            raise WaveForbidden()
        s.execute(
            text(
                "UPDATE wave.member SET last_present_at=:now WHERE room_id=:room AND device_id=:device"
            ),
            {"now": now, "room": room, "device": p.device_id},
        )

    def _host(self, s: Session, room: UUID, p: Principal, now: datetime) -> WaveRoom:
        value = self._snapshot(s, room, p, now)
        row = (
            s.execute(
                text("SELECT host_device_id FROM wave.room WHERE room_id=:room"), {"room": room}
            )
            .mappings()
            .one()
        )
        if (
            value.host_user_id != p.user_id
            or row.host_device_id != p.device_id
            or value.host_lost_at is not None
            or value.state != "OPEN"
        ):
            raise WaveForbidden()
        return value

    def _append_lifecycle_command(
        self,
        s: Session,
        room_id: UUID,
        actor_user_id: UUID,
        actor_device_id: UUID,
        kind: str,
        document: dict[str, object],
        now: datetime,
    ) -> int:
        """Append and materialize one server/user lifecycle mutation under the room lock."""
        room = (
            s.execute(
                text(
                    "SELECT queue_version,command_sequence FROM wave.room WHERE room_id=:room FOR UPDATE"
                ),
                {"room": room_id},
            )
            .mappings()
            .one()
        )
        sequence = cast(int, room.command_sequence) + 1
        serialized = json.dumps(document, sort_keys=True, separators=(",", ":"))
        request_hash = sha256(f"{room_id}:{sequence}:{kind}:{serialized}".encode()).digest()
        s.execute(
            text(
                "INSERT INTO wave.command(room_id,command_sequence,actor_user_id,actor_device_id,idempotency_key,request_sha256,expected_queue_version,expected_sequence,command_kind,command_document,created_at) "
                "VALUES(:room,:sequence,:user,:device,:key,:hash,:version,:expected,:kind,CAST(:document AS jsonb),:now)"
            ),
            {
                "room": room_id,
                "sequence": sequence,
                "user": actor_user_id,
                "device": actor_device_id,
                "key": f"lifecycle-{kind.lower()}-{sequence}",
                "hash": request_hash,
                "version": room.queue_version,
                "expected": room.command_sequence,
                "kind": kind,
                "document": serialized,
                "now": now,
            },
        )
        s.execute(
            text("UPDATE wave.room SET command_sequence=:sequence WHERE room_id=:room"),
            {"sequence": sequence, "room": room_id},
        )
        return sequence

    def _snapshot(self, s: Session, room: UUID, p: Principal, now: datetime) -> WaveRoom:
        row = (
            s.execute(
                text(
                    "SELECT room_id,host_user_id,created_at,expires_at,queue_version,command_sequence,closed_at,host_lost_at,room_epoch,state,playback_state FROM wave.room WHERE room_id=:room AND expires_at>:now AND state NOT IN ('CLOSED','EXPIRED') FOR UPDATE"
                ),
                {"room": room, "now": now},
            )
            .mappings()
            .one()
        )
        self._member(s, room, p, now)
        self_role = cast(
            str,
            s.execute(
                text(
                    "SELECT role FROM wave.member WHERE room_id=:room AND user_id=:user AND device_id=:device"
                ),
                {"room": room, "user": p.user_id, "device": p.device_id},
            ).scalar_one(),
        )
        queue = [
            QueueEntry(x.queue_entry_id, x.recording_id, x.position)
            for x in s.execute(
                text(
                    "SELECT queue_entry_id,recording_id,position FROM wave.queue_entry WHERE room_id=:room AND removed_at IS NULL ORDER BY position"
                ),
                {"room": room},
            ).mappings()
        ]
        members = {
            x.user_id
            for x in s.execute(
                text("SELECT user_id FROM wave.member WHERE room_id=:room AND status='JOINED'"),
                {"room": room},
            )
        }
        self_preflight = {
            x.queue_entry_id: Availability(x.availability)
            for x in s.execute(
                text(
                    "SELECT queue_entry_id,availability FROM wave.preflight "
                    "WHERE room_id=:room AND device_id=:device AND queue_version=:version "
                    "AND expires_at>:now"
                ),
                {
                    "room": room,
                    "device": p.device_id,
                    "version": row.queue_version,
                    "now": now,
                },
            ).mappings()
        }
        return WaveRoom(
            room_id=row.room_id,
            code="",
            host_user_id=row.host_user_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            members=members,
            queue=queue,
            version=row.queue_version,
            command_sequence=row.command_sequence,
            closed_at=row.closed_at,
            host_lost_at=row.host_lost_at,
            room_epoch=row.room_epoch,
            state=row.state,
            playback_state=row.playback_state,
            self_role=self_role,
            self_preflight=self_preflight,
        )
