# ruff: noqa: E501
"""Transactional S1D guest capability service for one Wave Room."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.social import (
    GuestInvitationRow,
    GuestOperationReceiptRow,
    GuestRateWindowRow,
    GuestSessionRow,
)
from autplay.domain.auth import GuestPrincipal, Principal
from autplay.domain.profile_pairing import canonical_sha256, iso8601
from autplay.domain.wave import Availability, QueueEntry, WaveRoom

GUEST_ACTIONS = (
    "ROOM_SNAPSHOT",
    "ROOM_EVENTS",
    "ROOM_PRESENCE",
    "ROOM_PREFLIGHT",
    "ROOM_TIMING",
    "ROOM_LEAVE",
)
MAX_ROOM_PARTICIPANTS = 8
DEFAULT_GUEST_TTL_SECONDS = 900
MAX_GUEST_TTL_SECONDS = 21_600
MAX_GUEST_USES = 8
_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class GuestRoomError(RuntimeError):
    """One user-safe S1D failure with no secret-bearing representation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GuestRoomService:
    """PostgreSQL authority for issue, redemption and exact-room guest actions."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def issue(
        self,
        principal: Principal,
        room_id: UUID,
        operation_id: UUID,
        document_bearer: str,
        ttl_seconds: int,
        max_uses: int,
        now: datetime,
    ) -> dict[str, object]:
        secret_hash = _secret_hash(document_bearer)
        if not 60 <= ttl_seconds <= MAX_GUEST_TTL_SECONDS or not 1 <= max_uses <= MAX_GUEST_USES:
            raise GuestRoomError("guest_invalid")
        request = {
            "room_id": str(room_id),
            "document_secret_sha256": secret_hash.hex(),
            "ttl_seconds": ttl_seconds,
            "max_uses": max_uses,
        }
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as session:
            room = self._host_room(session, principal, room_id, now)
            replay = session.get(GuestOperationReceiptRow, operation_id, with_for_update=True)
            if replay is not None:
                return self._host_replay(replay, principal, "ISSUE", request_hash, room_id)
            self._rate(
                session,
                "ISSUE_HOST",
                hashlib.sha256(f"{principal.user_id}:{principal.device_id}".encode()).digest(),
                20,
                now,
            )
            if (
                session.query(GuestInvitationRow)
                .filter_by(document_secret_sha256=secret_hash)
                .first()
            ):
                raise GuestRoomError("operation_conflict")
            expires_at = min(now + timedelta(seconds=ttl_seconds), room.expires_at)
            if expires_at <= now:
                raise GuestRoomError("guest_unavailable")
            invitation_id = uuid4()
            invitation = GuestInvitationRow(
                invitation_id=invitation_id,
                room_id=room_id,
                room_epoch=room.room_epoch,
                host_user_id=principal.user_id,
                host_device_id=principal.device_id,
                host_session_id=principal.session_id,
                document_secret_sha256=secret_hash,
                role="GUEST",
                allowed_actions=list(GUEST_ACTIONS),
                state="PENDING",
                max_uses=max_uses,
                consumed_uses=0,
                expires_at=expires_at,
                revoked_at=None,
                terminal_at=None,
                terminal_reason=None,
                created_at=now,
            )
            session.add(invitation)
            result = self._invitation_view(invitation, operation_id)
            session.add(
                self._receipt(
                    operation_id,
                    "HOST",
                    "ISSUE",
                    request_hash,
                    result,
                    now,
                    actor=principal,
                    invitation_id=invitation_id,
                    room_id=room_id,
                )
            )
            return result

    def revoke(
        self,
        principal: Principal,
        invitation_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> dict[str, object]:
        request = {"invitation_id": str(invitation_id)}
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as session:
            invitation = session.get(GuestInvitationRow, invitation_id, with_for_update=True)
            if invitation is None:
                raise GuestRoomError("guest_unavailable")
            self._host_room(session, principal, invitation.room_id, now, allow_terminal=True)
            replay = session.get(GuestOperationReceiptRow, operation_id, with_for_update=True)
            if replay is not None:
                return self._host_replay(
                    replay, principal, "REVOKE", request_hash, invitation.room_id
                )
            if (
                invitation.host_user_id != principal.user_id
                or invitation.host_device_id != principal.device_id
            ):
                raise GuestRoomError("guest_unavailable")
            if invitation.state not in {"REVOKED", "EXPIRED", "ROOM_CLOSED"}:
                invitation.state = "REVOKED"
                invitation.revoked_at = now
                invitation.terminal_at = now
                invitation.terminal_reason = "HOST_REVOKED"
            result = self._invitation_view(invitation, operation_id)
            session.add(
                self._receipt(
                    operation_id,
                    "HOST",
                    "REVOKE",
                    request_hash,
                    result,
                    now,
                    actor=principal,
                    invitation_id=invitation_id,
                    room_id=invitation.room_id,
                )
            )
            return result

    def redeem(
        self,
        *,
        invitation_id: UUID,
        room_id: UUID,
        operation_id: UUID,
        document_bearer: str,
        session_bearer: str,
        display_name: str,
        source_rate_key: bytes,
        now: datetime,
    ) -> dict[str, object]:
        document_hash = _secret_hash(document_bearer)
        access_hash = _secret_hash(session_bearer)
        name = _display_name(display_name)
        request = {
            "invitation_id": str(invitation_id),
            "room_id": str(room_id),
            "document_secret_sha256": document_hash.hex(),
            "access_secret_sha256": access_hash.hex(),
            "display_name": name,
        }
        request_hash = canonical_sha256(request)
        # Failed guesses must consume a durable rate-window attempt even when the
        # authorization transaction below rolls back.
        with self._sessions.begin() as session:
            replay = session.get(GuestOperationReceiptRow, operation_id, with_for_update=True)
            if replay is not None:
                return self._document_replay(
                    session,
                    replay,
                    document_hash,
                    access_hash,
                    request_hash,
                    room_id,
                    now,
                )
            self._rate(session, "REDEEM_SOURCE", source_rate_key, 30, now)
            self._rate(session, "REDEEM_DOCUMENT", document_hash, 10, now)

        with self._sessions.begin() as session:
            replay = session.get(GuestOperationReceiptRow, operation_id, with_for_update=True)
            if replay is not None:
                return self._document_replay(
                    session,
                    replay,
                    document_hash,
                    access_hash,
                    request_hash,
                    room_id,
                    now,
                )
            invitation = (
                session.query(GuestInvitationRow)
                .filter_by(
                    invitation_id=invitation_id,
                    room_id=room_id,
                    document_secret_sha256=document_hash,
                )
                .with_for_update()
                .one_or_none()
            )
            if invitation is None:
                raise GuestRoomError("guest_unavailable")
            if invitation.expires_at <= now:
                self._expire_invitation(invitation, now)
                raise GuestRoomError("guest_expired")
            if invitation.state != "PENDING":
                raise GuestRoomError(_invitation_failure(invitation.state))
            room = self._open_room(session, room_id, now)
            if room.room_epoch != invitation.room_epoch:
                raise GuestRoomError("room_changed")
            participant_count = cast(
                int,
                session.execute(
                    text(
                        "SELECT (SELECT count(*) FROM wave.member WHERE room_id=:room AND status='JOINED') + "
                        "(SELECT count(*) FROM social.guest_session WHERE room_id=:room AND state='ACTIVE' AND expires_at>:now)"
                    ),
                    {"room": room_id, "now": now},
                ).scalar_one(),
            )
            if participant_count >= MAX_ROOM_PARTICIPANTS:
                raise GuestRoomError("room_full")
            if (
                session.query(GuestSessionRow).filter_by(access_secret_sha256=access_hash).first()
                is not None
            ):
                raise GuestRoomError("operation_conflict")
            guest_session_id = uuid4()
            expires_at = min(invitation.expires_at, room.expires_at)
            guest_session = GuestSessionRow(
                guest_session_id=guest_session_id,
                invitation_id=invitation_id,
                room_id=room_id,
                room_epoch=room.room_epoch,
                access_secret_sha256=access_hash,
                display_name=name,
                role="GUEST",
                allowed_actions=list(GUEST_ACTIONS),
                state="ACTIVE",
                expires_at=expires_at,
                last_present_at=now,
                left_at=None,
                revoked_at=None,
                terminal_at=None,
                terminal_reason=None,
                created_at=now,
            )
            session.add(guest_session)
            # Storage rows intentionally have no ORM relationships.  Flush the FK target
            # before adding its immutable operation receipt so ordering is explicit.
            session.flush((guest_session,))
            invitation.consumed_uses += 1
            if invitation.consumed_uses == invitation.max_uses:
                invitation.state = "DEPLETED"
                invitation.terminal_at = now
                invitation.terminal_reason = "USES_EXHAUSTED"
            result: dict[str, object] = {
                "operation_id": str(operation_id),
                "guest_session_id": str(guest_session_id),
                "invitation_id": str(invitation_id),
                "room_id": str(room_id),
                "room_epoch": room.room_epoch,
                "role": "GUEST",
                "allowed_actions": list(GUEST_ACTIONS),
                "display_name": name,
                "expires_at": iso8601(expires_at),
                "media_boundary": "INDEPENDENT_DEVICE_AUTHORIZATION_ONLY",
            }
            session.add(
                self._receipt(
                    operation_id,
                    "DOCUMENT",
                    "REDEEM",
                    request_hash,
                    result,
                    now,
                    actor_secret=document_hash,
                    invitation_id=invitation_id,
                    guest_session_id=guest_session_id,
                    room_id=room_id,
                )
            )
            return result

    def authenticate(
        self, session_bearer: str, room_id: UUID, action: str, now: datetime
    ) -> GuestPrincipal:
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            return self._guest(session, access_hash, room_id, action, now)

    def snapshot(self, session_bearer: str, room_id: UUID, now: datetime) -> WaveRoom:
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            principal = self._guest(session, access_hash, room_id, "ROOM_SNAPSHOT", now)
            return self._snapshot(session, principal, now)

    def presence(self, session_bearer: str, room_id: UUID, now: datetime) -> None:
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            self._guest(session, access_hash, room_id, "ROOM_PRESENCE", now)

    def preflight(
        self,
        session_bearer: str,
        room_id: UUID,
        queue_entry_id: UUID,
        recording_id: UUID,
        queue_version: int,
        availability: Availability,
        final_ready: bool,
        now: datetime,
    ) -> None:
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            principal = self._guest(session, access_hash, room_id, "ROOM_PREFLIGHT", now)
            valid = session.execute(
                text(
                    "SELECT 1 FROM wave.queue_entry q JOIN wave.room r USING(room_id) "
                    "WHERE q.room_id=:room AND q.queue_entry_id=:entry AND q.recording_id=:recording "
                    "AND q.removed_at IS NULL AND r.queue_version=:version AND r.state='OPEN'"
                ),
                {
                    "room": room_id,
                    "entry": queue_entry_id,
                    "recording": recording_id,
                    "version": queue_version,
                },
            ).first()
            if valid is None:
                raise GuestRoomError("room_changed")
            session.execute(
                text(
                    "INSERT INTO social.guest_preflight(room_id,guest_session_id,queue_entry_id,recording_id,queue_version,availability,final_ready,source_checked_at,expires_at) "
                    "VALUES(:room,:guest,:entry,:recording,:version,:availability,:ready,:now,:expiry) "
                    "ON CONFLICT(room_id,guest_session_id,queue_entry_id) DO UPDATE SET recording_id=excluded.recording_id,queue_version=excluded.queue_version,availability=excluded.availability,final_ready=excluded.final_ready,source_checked_at=excluded.source_checked_at,expires_at=excluded.expires_at"
                ),
                {
                    "room": room_id,
                    "guest": principal.guest_session_id,
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
        session_bearer: str,
        room_id: UUID,
        command_sequence: int,
        rtt_ms: int,
        offset_ms: int,
        uncertainty_ms: int,
        now: datetime,
        *,
        start_skew_ms: int | None = None,
        drift_ms: int | None = None,
    ) -> None:
        if not 0 <= rtt_ms <= 1_000 or not 0 <= uncertainty_ms <= 100:
            raise GuestRoomError("guest_invalid")
        if abs(offset_ms) > 86_400_000:
            raise GuestRoomError("guest_invalid")
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            principal = self._guest(session, access_hash, room_id, "ROOM_TIMING", now)
            session.execute(
                text(
                    "INSERT INTO social.guest_timing_report(room_id,guest_session_id,command_sequence,rtt_ms,offset_ms,uncertainty_ms,start_skew_ms,drift_ms,reported_at) "
                    "VALUES(:room,:guest,:sequence,:rtt,:offset,:uncertainty,:skew,:drift,:now) "
                    "ON CONFLICT(room_id,guest_session_id,command_sequence) DO UPDATE SET rtt_ms=excluded.rtt_ms,offset_ms=excluded.offset_ms,uncertainty_ms=excluded.uncertainty_ms,start_skew_ms=excluded.start_skew_ms,drift_ms=excluded.drift_ms,reported_at=excluded.reported_at"
                ),
                {
                    "room": room_id,
                    "guest": principal.guest_session_id,
                    "sequence": command_sequence,
                    "rtt": rtt_ms,
                    "offset": offset_ms,
                    "uncertainty": uncertainty_ms,
                    "skew": start_skew_ms,
                    "drift": drift_ms,
                    "now": now,
                },
            )

    def catch_up(
        self, session_bearer: str, room_id: UUID, after_sequence: int, now: datetime
    ) -> list[dict[str, object]]:
        access_hash = _secret_hash(session_bearer)
        with self._sessions.begin() as session:
            self._guest(session, access_hash, room_id, "ROOM_EVENTS", now)
            return [
                {
                    "sequence": row.command_sequence,
                    "kind": row.command_kind,
                    "payload": row.command_document,
                    "effective_at": iso8601(row.effective_at)
                    if row.effective_at is not None
                    else None,
                }
                for row in session.execute(
                    text(
                        "SELECT command_sequence,command_kind,command_document,effective_at "
                        "FROM wave.command WHERE room_id=:room AND command_sequence>:after "
                        "ORDER BY command_sequence LIMIT 100"
                    ),
                    {"room": room_id, "after": after_sequence},
                ).mappings()
            ]

    def leave(
        self,
        session_bearer: str,
        room_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> dict[str, object]:
        access_hash = _secret_hash(session_bearer)
        request_hash = canonical_sha256({"room_id": str(room_id)})
        with self._sessions.begin() as session:
            row = (
                session.query(GuestSessionRow)
                .filter_by(access_secret_sha256=access_hash, room_id=room_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise GuestRoomError("guest_unavailable")
            replay = session.get(GuestOperationReceiptRow, operation_id, with_for_update=True)
            if replay is not None:
                if (
                    replay.actor_kind != "GUEST"
                    or replay.actor_guest_session_id != row.guest_session_id
                    or replay.action != "LEAVE"
                    or not hmac.compare_digest(replay.request_sha256, request_hash)
                ):
                    raise GuestRoomError("operation_conflict")
                return _load_result(replay)
            principal = self._guest(session, access_hash, room_id, "ROOM_LEAVE", now)
            row.state = "LEFT"
            row.left_at = now
            row.terminal_at = now
            row.terminal_reason = "GUEST_LEFT"
            result: dict[str, object] = {
                "operation_id": str(operation_id),
                "guest_session_id": str(principal.guest_session_id),
                "room_id": str(room_id),
                "state": "LEFT",
            }
            session.add(
                self._receipt(
                    operation_id,
                    "GUEST",
                    "LEAVE",
                    request_hash,
                    result,
                    now,
                    actor_guest_session_id=principal.guest_session_id,
                    guest_session_id=principal.guest_session_id,
                    room_id=room_id,
                )
            )
            return result

    def cleanup(self, now: datetime, limit: int = 100) -> dict[str, int]:
        if not 1 <= limit <= 1_000:
            raise ValueError("guest cleanup limit must be between 1 and 1000")
        cutoff = now - timedelta(days=30)
        counts: dict[str, int] = {}
        with self._sessions.begin() as session:
            counts["expired_invitations"] = _rowcount(
                session.execute(
                    text(
                        "WITH due AS (SELECT invitation_id FROM social.guest_invitation WHERE state='PENDING' AND expires_at<=:now ORDER BY expires_at LIMIT :limit FOR UPDATE SKIP LOCKED) "
                        "UPDATE social.guest_invitation i SET state='EXPIRED',terminal_at=:now,terminal_reason='INVITATION_EXPIRED' FROM due WHERE i.invitation_id=due.invitation_id"
                    ),
                    {"now": now, "limit": limit},
                )
            )
            counts["expired_sessions"] = _rowcount(
                session.execute(
                    text(
                        "WITH due AS (SELECT guest_session_id FROM social.guest_session WHERE state='ACTIVE' AND expires_at<=:now ORDER BY expires_at LIMIT :limit FOR UPDATE SKIP LOCKED) "
                        "UPDATE social.guest_session s SET state='EXPIRED',terminal_at=:now,terminal_reason='SESSION_EXPIRED' FROM due WHERE s.guest_session_id=due.guest_session_id"
                    ),
                    {"now": now, "limit": limit},
                )
            )
            for key, table_name, column in (
                ("preflight", "guest_preflight", "expires_at"),
                ("timing", "guest_timing_report", "reported_at"),
                ("rates", "guest_rate_window", "expires_at"),
                ("receipts", "guest_operation_receipt", "expires_at"),
            ):
                counts[key] = _rowcount(
                    session.execute(
                        text(
                            f"DELETE FROM social.{table_name} WHERE ctid IN "
                            f"(SELECT ctid FROM social.{table_name} WHERE {column}<=:cutoff "
                            "ORDER BY " + column + " LIMIT :limit FOR UPDATE SKIP LOCKED)"
                        ),
                        {
                            "cutoff": now if key in {"preflight", "rates", "receipts"} else cutoff,
                            "limit": limit,
                        },
                    )
                )
            counts["sessions"] = _rowcount(
                session.execute(
                    text(
                        "DELETE FROM social.guest_session WHERE guest_session_id IN "
                        "(SELECT guest_session_id FROM social.guest_session "
                        "WHERE state<>'ACTIVE' AND terminal_at<=:cutoff "
                        "AND NOT EXISTS (SELECT 1 FROM social.guest_operation_receipt r "
                        "WHERE r.actor_guest_session_id=guest_session_id "
                        "OR r.result_guest_session_id=guest_session_id) "
                        "ORDER BY terminal_at LIMIT :limit FOR UPDATE SKIP LOCKED)"
                    ),
                    {"cutoff": cutoff, "limit": limit},
                )
            )
            counts["invitations"] = _rowcount(
                session.execute(
                    text(
                        "DELETE FROM social.guest_invitation WHERE invitation_id IN "
                        "(SELECT invitation_id FROM social.guest_invitation "
                        "WHERE state<>'PENDING' AND terminal_at<=:cutoff "
                        "AND NOT EXISTS (SELECT 1 FROM social.guest_session s "
                        "WHERE s.invitation_id=guest_invitation.invitation_id) "
                        "AND NOT EXISTS (SELECT 1 FROM social.guest_operation_receipt r "
                        "WHERE r.result_invitation_id=guest_invitation.invitation_id) "
                        "ORDER BY terminal_at LIMIT :limit FOR UPDATE SKIP LOCKED)"
                    ),
                    {"cutoff": cutoff, "limit": limit},
                )
            )
        return counts

    def _guest(
        self,
        session: Session,
        access_hash: bytes,
        room_id: UUID,
        action: str,
        now: datetime,
    ) -> GuestPrincipal:
        row = (
            session.query(GuestSessionRow)
            .filter_by(access_secret_sha256=access_hash)
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise GuestRoomError("guest_unavailable")
        if row.room_id != room_id:
            raise GuestRoomError("guest_scope_denied")
        invitation = session.get(GuestInvitationRow, row.invitation_id, with_for_update=True)
        if invitation is None:
            raise GuestRoomError("guest_unavailable")
        if row.expires_at <= now or invitation.expires_at <= now:
            if invitation.state in {"PENDING", "DEPLETED"}:
                self._expire_invitation(invitation, now)
            if row.state == "ACTIVE":
                row.state = "EXPIRED"
                row.terminal_at = now
                row.terminal_reason = "SESSION_EXPIRED"
            raise GuestRoomError("guest_expired")
        if invitation.state in {"REVOKED", "ROOM_CLOSED", "EXPIRED"}:
            raise GuestRoomError(_invitation_failure(invitation.state))
        host_active = session.execute(
            text(
                "SELECT 1 FROM account.user_account u "
                "JOIN account.device d ON d.user_id=u.user_id "
                "JOIN account.user_session s ON s.user_id=d.user_id AND s.device_id=d.device_id "
                "WHERE u.user_id=:user AND d.device_id=:device AND s.session_id=:host_session "
                "AND u.status='ACTIVE' AND u.deleted_at IS NULL AND d.revoked_at IS NULL "
                "AND s.revoked_at IS NULL AND s.expires_at>:now"
            ),
            {
                "user": invitation.host_user_id,
                "device": invitation.host_device_id,
                "host_session": invitation.host_session_id,
                "now": now,
            },
        ).scalar_one_or_none()
        if host_active is None:
            raise GuestRoomError("guest_revoked")
        if row.state != "ACTIVE":
            raise GuestRoomError(_session_failure(row.state))
        room = self._open_room(session, room_id, now)
        if row.room_epoch != room.room_epoch or invitation.room_epoch != room.room_epoch:
            raise GuestRoomError("room_changed")
        actions = frozenset(row.allowed_actions)
        if action not in actions:
            raise GuestRoomError("guest_scope_denied")
        row.last_present_at = now
        return GuestPrincipal(
            guest_session_id=row.guest_session_id,
            invitation_id=row.invitation_id,
            room_id=row.room_id,
            room_epoch=row.room_epoch,
            role=row.role,
            allowed_actions=actions,
            expires_at=row.expires_at,
        )

    def _snapshot(self, session: Session, principal: GuestPrincipal, now: datetime) -> WaveRoom:
        row = self._open_room(session, principal.room_id, now)
        queue = [
            QueueEntry(item.queue_entry_id, item.recording_id, item.position)
            for item in session.execute(
                text(
                    "SELECT queue_entry_id,recording_id,position FROM wave.queue_entry "
                    "WHERE room_id=:room AND removed_at IS NULL ORDER BY position"
                ),
                {"room": principal.room_id},
            ).mappings()
        ]
        preflight = {
            item.queue_entry_id: Availability(item.availability)
            for item in session.execute(
                text(
                    "SELECT queue_entry_id,availability FROM social.guest_preflight "
                    "WHERE room_id=:room AND guest_session_id=:guest AND queue_version=:version "
                    "AND expires_at>:now"
                ),
                {
                    "room": principal.room_id,
                    "guest": principal.guest_session_id,
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
            expires_at=min(row.expires_at, principal.expires_at),
            members={principal.guest_session_id},
            queue=queue,
            version=row.queue_version,
            command_sequence=row.command_sequence,
            closed_at=row.closed_at,
            host_lost_at=row.host_lost_at,
            room_epoch=row.room_epoch,
            state=row.state,
            playback_state=row.playback_state,
            self_role="GUEST",
            self_preflight=preflight,
        )

    def _host_room(
        self,
        session: Session,
        principal: Principal,
        room_id: UUID,
        now: datetime,
        *,
        allow_terminal: bool = False,
    ) -> Any:
        row = (
            session.execute(
                text(
                    "SELECT r.* FROM wave.room r JOIN wave.member m ON m.room_id=r.room_id "
                    "AND m.user_id=:user AND m.device_id=:device AND m.role='HOST' AND m.status='JOINED' "
                    "WHERE r.room_id=:room FOR UPDATE"
                ),
                {"room": room_id, "user": principal.user_id, "device": principal.device_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GuestRoomError("guest_unavailable")
        if not allow_terminal and (row.state != "OPEN" or row.expires_at <= now):
            raise GuestRoomError("guest_unavailable")
        return row

    def _open_room(self, session: Session, room_id: UUID, now: datetime) -> Any:
        row = (
            session.execute(
                text("SELECT * FROM wave.room WHERE room_id=:room FOR UPDATE"),
                {"room": room_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GuestRoomError("guest_unavailable")
        if row.state == "EXPIRED" or row.expires_at <= now:
            raise GuestRoomError("guest_expired")
        if row.state != "OPEN":
            raise GuestRoomError("guest_unavailable")
        return row

    def _host_replay(
        self,
        receipt: GuestOperationReceiptRow,
        principal: Principal,
        action: str,
        request_hash: bytes,
        room_id: UUID,
    ) -> dict[str, object]:
        if (
            receipt.actor_kind != "HOST"
            or receipt.actor_user_id != principal.user_id
            or receipt.actor_device_id != principal.device_id
            or receipt.action != action
            or receipt.result_room_id != room_id
            or not hmac.compare_digest(receipt.request_sha256, request_hash)
        ):
            raise GuestRoomError("operation_conflict")
        return _load_result(receipt)

    def _document_replay(
        self,
        session: Session,
        receipt: GuestOperationReceiptRow,
        document_hash: bytes,
        access_hash: bytes,
        request_hash: bytes,
        room_id: UUID,
        now: datetime,
    ) -> dict[str, object]:
        if (
            receipt.actor_kind != "DOCUMENT"
            or receipt.action != "REDEEM"
            or receipt.result_room_id != room_id
            or receipt.actor_secret_sha256 is None
            or not hmac.compare_digest(receipt.actor_secret_sha256, document_hash)
            or not hmac.compare_digest(receipt.request_sha256, request_hash)
        ):
            raise GuestRoomError("operation_conflict")
        if receipt.result_guest_session_id is None:
            raise GuestRoomError("guest_unavailable")
        principal = self._guest(session, access_hash, room_id, "ROOM_SNAPSHOT", now)
        if principal.guest_session_id != receipt.result_guest_session_id:
            raise GuestRoomError("operation_conflict")
        return _load_result(receipt)

    def _receipt(
        self,
        operation_id: UUID,
        actor_kind: str,
        action: str,
        request_hash: bytes,
        result: Mapping[str, object],
        now: datetime,
        *,
        actor: Principal | None = None,
        actor_secret: bytes | None = None,
        actor_guest_session_id: UUID | None = None,
        invitation_id: UUID | None = None,
        guest_session_id: UUID | None = None,
        room_id: UUID | None = None,
    ) -> GuestOperationReceiptRow:
        return GuestOperationReceiptRow(
            operation_id=operation_id,
            actor_kind=actor_kind,
            actor_user_id=actor.user_id if actor is not None else None,
            actor_device_id=actor.device_id if actor is not None else None,
            actor_secret_sha256=actor_secret,
            actor_guest_session_id=actor_guest_session_id,
            action=action,
            request_sha256=request_hash,
            result_code=str(result.get("state", "ACTIVE")),
            result_invitation_id=invitation_id,
            result_guest_session_id=guest_session_id,
            result_room_id=room_id,
            result_json=json.dumps(result, sort_keys=True, separators=(",", ":")),
            expires_at=now + timedelta(days=30),
            created_at=now,
        )

    def _invitation_view(
        self, invitation: GuestInvitationRow, operation_id: UUID
    ) -> dict[str, object]:
        return {
            "operation_id": str(operation_id),
            "invitation_id": str(invitation.invitation_id),
            "kind": "GUEST",
            "state": invitation.state,
            "room_id": str(invitation.room_id),
            "room_epoch": invitation.room_epoch,
            "role": invitation.role,
            "allowed_actions": list(invitation.allowed_actions),
            "max_uses": invitation.max_uses,
            "consumed_uses": invitation.consumed_uses,
            "expires_at": iso8601(invitation.expires_at),
            "media_boundary": "INDEPENDENT_DEVICE_AUTHORIZATION_ONLY",
        }

    def _rate(
        self,
        session: Session,
        scope: str,
        subject_hash: bytes,
        limit: int,
        now: datetime,
    ) -> None:
        key = hashlib.sha256(scope.encode() + b":" + subject_hash).digest()
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:value,0))"),
            {"value": key.hex()},
        )
        row = session.get(GuestRateWindowRow, key, with_for_update=True)
        if row is None:
            session.add(
                GuestRateWindowRow(
                    rate_key_sha256=key,
                    scope=scope,
                    window_started_at=now,
                    expires_at=now + timedelta(minutes=15),
                    attempt_count=1,
                )
            )
            return
        if row.expires_at <= now:
            row.scope = scope
            row.window_started_at = now
            row.expires_at = now + timedelta(minutes=15)
            row.attempt_count = 1
            return
        if row.attempt_count >= limit:
            raise GuestRoomError("rate_limited")
        row.attempt_count += 1

    @staticmethod
    def _expire_invitation(invitation: GuestInvitationRow, now: datetime) -> None:
        invitation.state = "EXPIRED"
        invitation.terminal_at = now
        invitation.terminal_reason = "INVITATION_EXPIRED"


def _secret_hash(value: str) -> bytes:
    if not _SECRET_PATTERN.fullmatch(value):
        raise GuestRoomError("guest_unavailable")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except ValueError as error:
        raise GuestRoomError("guest_unavailable") from error
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise GuestRoomError("guest_unavailable")
    return hashlib.sha256(decoded).digest()


def _display_name(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not 1 <= len(normalized) <= 40 or any(
        unicodedata.category(ch).startswith("C") for ch in normalized
    ):
        raise GuestRoomError("guest_invalid")
    return normalized


def _load_result(receipt: GuestOperationReceiptRow) -> dict[str, object]:
    value = json.loads(receipt.result_json)
    if not isinstance(value, dict):
        raise RuntimeError("guest operation receipt result must be an object")
    return cast(dict[str, object], value)


def _rowcount(result: Any) -> int:
    value = result.rowcount
    if not isinstance(value, int):
        raise RuntimeError("guest cleanup did not return a row count")
    return value


def _invitation_failure(state: str) -> str:
    return {
        "REVOKED": "guest_revoked",
        "EXPIRED": "guest_expired",
        "ROOM_CLOSED": "guest_unavailable",
        "DEPLETED": "guest_unavailable",
    }.get(state, "guest_unavailable")


def _session_failure(state: str) -> str:
    return {
        "REVOKED": "guest_revoked",
        "EXPIRED": "guest_expired",
        "LEFT": "guest_unavailable",
        "ROOM_CLOSED": "guest_unavailable",
    }.get(state, "guest_unavailable")


__all__ = (
    "DEFAULT_GUEST_TTL_SECONDS",
    "GUEST_ACTIONS",
    "MAX_GUEST_TTL_SECONDS",
    "MAX_GUEST_USES",
    "GuestRoomError",
    "GuestRoomService",
)
