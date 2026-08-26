"""Transactional S1C friendship, coarse presence and friend Room invitations."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.account import UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.social import (
    FriendRequestRow,
    FriendRoomInvitationRow,
    FriendshipRow,
    PresenceHeartbeatRow,
    PresenceSettingsRow,
    ProfileStatisticsSettingsRow,
    SocialOperationReceiptRow,
    SocialRateWindowRow,
    UserBlockRow,
)
from autplay.adapters.postgresql.models.wave import WaveMemberRow, WaveRoomRow
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    iso8601,
    sign_p1363,
    verify_p1363,
)


class SocialError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details
        super().__init__(code)


class SocialService:
    """PostgreSQL-only authoritative S1C transitions; no social cache or broker."""

    def __init__(
        self, sessions: sessionmaker[Session], private_key: ec.EllipticCurvePrivateKey | None
    ) -> None:
        self._sessions, self._private_key = sessions, private_key

    def contact_card(self, principal: Principal, now: datetime) -> dict[str, object]:
        if self._private_key is None:
            raise SocialError("friend_request_unavailable")
        with self._sessions.begin() as s:
            account = self._account(s, principal.user_id)
            self._rate(s, "CONTACT_CARD", str(principal.user_id), 60, now)
            instance = s.scalar(select(ServerInstanceRow))
            if instance is None:
                raise SocialError("friend_request_unavailable")
            payload: dict[str, object] = {
                "server_instance_id": str(instance.server_instance_id),
                "account_id": str(account.user_id),
                "display_name_hint": account.display_name[:120],
                "issued_at": iso8601(now),
                "expires_at": iso8601(now + timedelta(days=30)),
            }
            payload["signature_b64url"] = sign_p1363(
                self._private_key, "autplay:s1c:social-contact-card:v1\n", canonical_sha256(payload)
            )
            return payload

    def snapshot(self, principal: Principal, now: datetime) -> dict[str, object]:
        with self._sessions.begin() as s:
            self._account(s, principal.user_id)
            self._rate(s, "SNAPSHOT", str(principal.user_id), 60, now)
            friend_ids = (
                s.execute(
                    text(
                        "SELECT CASE WHEN lower_user_id=:u THEN higher_user_id ELSE lower_user_id END AS user_id FROM social.friendship WHERE lower_user_id=:u OR higher_user_id=:u ORDER BY user_id LIMIT 100"
                    ),
                    {"u": principal.user_id},
                )
                .scalars()
                .all()
            )
            incoming = (
                s.execute(
                    text(
                        "SELECT request_id,requester_user_id,target_user_id,state,expires_at FROM social.friend_request WHERE target_user_id=:u AND state='PENDING' AND expires_at>:n ORDER BY created_at,request_id LIMIT 100"
                    ),
                    {"u": principal.user_id, "n": now},
                )
                .mappings()
                .all()
            )
            outgoing = (
                s.execute(
                    text(
                        "SELECT request_id,requester_user_id,target_user_id,state,expires_at FROM social.friend_request WHERE requester_user_id=:u AND state='PENDING' AND expires_at>:n ORDER BY created_at,request_id LIMIT 100"
                    ),
                    {"u": principal.user_id, "n": now},
                )
                .mappings()
                .all()
            )
            pending = [*incoming, *outgoing]
            settings = self._settings(s, principal.user_id, now)
            blocked_rows = s.scalars(
                select(UserBlockRow)
                .where(
                    UserBlockRow.blocker_user_id == principal.user_id,
                    UserBlockRow.unblocked_at.is_(None),
                )
                .limit(100)
            ).all()
            sent_invitations = s.scalars(
                select(FriendRoomInvitationRow)
                .where(FriendRoomInvitationRow.host_user_id == principal.user_id)
                .order_by(
                    (
                        (FriendRoomInvitationRow.state == "PENDING")
                        & (FriendRoomInvitationRow.expires_at > now)
                    ).desc(),
                    FriendRoomInvitationRow.created_at.desc(),
                    FriendRoomInvitationRow.invitation_id,
                )
                .limit(100)
            ).all()
            received_invitations = s.scalars(
                select(FriendRoomInvitationRow)
                .where(FriendRoomInvitationRow.target_user_id == principal.user_id)
                .order_by(
                    (
                        (FriendRoomInvitationRow.state == "PENDING")
                        & (FriendRoomInvitationRow.expires_at > now)
                    ).desc(),
                    FriendRoomInvitationRow.created_at.desc(),
                    FriendRoomInvitationRow.invitation_id,
                )
                .limit(100)
            ).all()
            visible_ids = set(friend_ids)
            visible_ids.update(
                x.requester_user_id if x.target_user_id == principal.user_id else x.target_user_id
                for x in pending
            )
            visible_ids.update(x.blocked_user_id for x in blocked_rows)
            visible_ids.update(
                x.target_user_id if x.host_user_id == principal.user_id else x.host_user_id
                for x in [*sent_invitations, *received_invitations]
            )
            accounts = {
                row.user_id: row
                for row in s.scalars(
                    select(UserAccountRow)
                    .where(
                        UserAccountRow.user_id.in_(visible_ids),
                        UserAccountRow.status == "ACTIVE",
                        UserAccountRow.deleted_at.is_(None),
                    )
                    .limit(500)
                )
            }

            def account_view(user_id: UUID, *, friend: bool = False) -> dict[str, object]:
                row = accounts[user_id]
                return {
                    "account_id": str(user_id),
                    "display_name_hint": row.display_name[:120],
                    "presence": self._aggregate(s, user_id, now) if friend else "OFFLINE",
                }

            return {
                "friends": [account_view(x, friend=True) for x in friend_ids if x in accounts],
                "incoming_requests": [
                    account_view(x.requester_user_id)
                    for x in pending
                    if x.target_user_id == principal.user_id and x.requester_user_id in accounts
                ],
                "outgoing_requests": [
                    account_view(x.target_user_id)
                    for x in pending
                    if x.requester_user_id == principal.user_id and x.target_user_id in accounts
                ],
                "blocked": [
                    account_view(x.blocked_user_id)
                    for x in blocked_rows
                    if x.blocked_user_id in accounts
                ],
                "sent_room_invitations": [
                    self._snapshot_invite(x, now)
                    for x in sent_invitations
                    if x.target_user_id in accounts
                ],
                "received_room_invitations": [
                    self._snapshot_invite(x, now)
                    for x in received_invitations
                    if x.host_user_id in accounts
                ],
                "presence_settings": self._settings_view(settings),
            }

    def command(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, object]:
        op, action = UUID(str(body["operation_id"])), str(body["action"])
        request: dict[str, object] = {"body": body}
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as s:
            replay = s.get(SocialOperationReceiptRow, op)
            if replay is not None:
                self._active_principal(s, principal, now)
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != action
                    or replay.request_sha256 != request_hash
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            target = self._target(s, body, action, now)
            if target == principal.user_id:
                raise SocialError("friend_request_unavailable")
            self._lock_accounts(s, principal.user_id, target)
            self._active_principal(s, principal, now)
            self._pair_lock(s, principal.user_id, target)
            if action != "BLOCK_USER":
                self._rate(s, "FRIEND_ACCOUNT", str(principal.user_id), 30, now)
                pair_key = ":".join(sorted((str(principal.user_id), str(target))))
                self._rate(s, "FRIEND_PAIR", pair_key, 10, now)
            if action == "SEND_REQUEST":
                self._expire_pair_requests(s, principal.user_id, target, now)
                if self._blocked(s, principal.user_id, target):
                    raise SocialError("user_blocked")
                if self._is_friend(s, principal.user_id, target):
                    result: dict[str, object] = {"operation_id": str(op), "state": "MUTUAL"}
                else:
                    existing = s.scalar(
                        select(FriendRequestRow).where(
                            FriendRequestRow.requester_user_id == principal.user_id,
                            FriendRequestRow.target_user_id == target,
                            FriendRequestRow.state == "PENDING",
                            FriendRequestRow.expires_at > now,
                        )
                    )
                    if existing is None:
                        s.add(
                            FriendRequestRow(
                                request_id=uuid4(),
                                requester_user_id=principal.user_id,
                                target_user_id=target,
                                state="PENDING",
                                expires_at=now + timedelta(days=14),
                                terminal_at=None,
                                created_at=now,
                            )
                        )
                    result = {"operation_id": str(op), "state": "PENDING_OUTGOING"}
            elif action == "ACCEPT_REQUEST":
                row = s.scalar(
                    select(FriendRequestRow)
                    .where(
                        FriendRequestRow.requester_user_id == target,
                        FriendRequestRow.target_user_id == principal.user_id,
                        FriendRequestRow.state == "PENDING",
                    )
                    .with_for_update()
                )
                if (
                    row is None
                    or row.expires_at <= now
                    or self._blocked(s, principal.user_id, target)
                ):
                    raise SocialError("friendship_required")
                row.state = "ACCEPTED"
                row.terminal_at = now
                s.execute(
                    text(
                        "UPDATE social.friend_request SET state='ACCEPTED',terminal_at=:n WHERE state='PENDING' AND ((requester_user_id=:a AND target_user_id=:b) OR (requester_user_id=:b AND target_user_id=:a))"
                    ),
                    {"a": principal.user_id, "b": target, "n": now},
                )
                self._friend(s, principal.user_id, target, now)
                result = {"operation_id": str(op), "state": "MUTUAL"}
            elif action in {"DECLINE_REQUEST", "CANCEL_REQUEST"}:
                requester, target_user = (
                    (target, principal.user_id)
                    if action == "DECLINE_REQUEST"
                    else (principal.user_id, target)
                )
                row = s.scalar(
                    select(FriendRequestRow)
                    .where(
                        FriendRequestRow.requester_user_id == requester,
                        FriendRequestRow.target_user_id == target_user,
                        FriendRequestRow.state == "PENDING",
                    )
                    .with_for_update()
                )
                if row is not None:
                    row.state = "DECLINED" if action == "DECLINE_REQUEST" else "CANCELLED"
                    row.terminal_at = now
                result = {
                    "operation_id": str(op),
                    "state": "DECLINED" if action == "DECLINE_REQUEST" else "CANCELLED",
                }
            elif action == "REMOVE_FRIEND":
                self._remove_friend(s, principal.user_id, target)
                self._cancel_invites(s, principal.user_id, target, now, "CANCELLED")
                result = {"operation_id": str(op), "state": "REMOVED"}
            elif action == "BLOCK_USER":
                shared_rooms = self._shared_active_room_ids(s, principal.user_id, target, now)
                if shared_rooms:
                    raise SocialError(
                        "active_room_exit_required",
                        details={"room_count": min(len(shared_rooms), 8)},
                    )
                block = s.get(
                    UserBlockRow, {"blocker_user_id": principal.user_id, "blocked_user_id": target}
                )
                if block is None:
                    s.add(
                        UserBlockRow(
                            blocker_user_id=principal.user_id,
                            blocked_user_id=target,
                            blocked_at=now,
                            unblocked_at=None,
                        )
                    )
                else:
                    block.blocked_at = now
                    block.unblocked_at = None
                self._remove_friend(s, principal.user_id, target)
                self._cancel_requests(s, principal.user_id, target, now)
                self._cancel_invites(s, principal.user_id, target, now, "BLOCKED")
                result = {"operation_id": str(op), "state": "BLOCKED"}
            elif action == "UNBLOCK_USER":
                block = s.get(
                    UserBlockRow, {"blocker_user_id": principal.user_id, "blocked_user_id": target}
                )
                if block is not None:
                    block.unblocked_at = now
                result = {"operation_id": str(op), "state": "UNBLOCKED"}
            else:
                raise SocialError("friend_request_unavailable")
            self._receipt(s, op, principal, action, result, request, now)
            return result

    def set_settings(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, object]:
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            operation_id = UUID(str(body["operation_id"]))
            request: dict[str, object] = {
                key: body[key]
                for key in (
                    "friend_presence_visibility_enabled",
                    "room_activity_sharing_enabled",
                    "invite_availability_enabled",
                )
            }
            replay = s.get(SocialOperationReceiptRow, operation_id)
            if replay is not None:
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != "SET_PRESENCE_SETTINGS"
                    or replay.request_sha256 != canonical_sha256(request)
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            self._rate(s, "SETTINGS", str(principal.user_id), 10, now)
            row = self._settings(s, principal.user_id, now)
            row.friend_presence_visibility_enabled = bool(
                body["friend_presence_visibility_enabled"]
            )
            row.room_activity_sharing_enabled = bool(body["room_activity_sharing_enabled"])
            row.invite_availability_enabled = bool(body["invite_availability_enabled"])
            row.revision += 1
            row.updated_at = now
            result = cast(dict[str, object], self._settings_view(row))
            self._receipt(s, operation_id, principal, "SET_PRESENCE_SETTINGS", result, request, now)
            return result

    def get_settings(self, principal: Principal, now: datetime) -> dict[str, bool]:
        with self._sessions.begin() as s:
            self._account(s, principal.user_id)
            return self._settings_view(self._settings(s, principal.user_id, now))

    def get_profile_statistics_settings(
        self, principal: Principal, now: datetime
    ) -> dict[str, object]:
        """Return the caller-owned default-private policy without materializing an absent row."""
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            self._rate(s, "PROFILE_STATISTICS_SETTINGS_READ", str(principal.user_id), 60, now)
            row = s.get(ProfileStatisticsSettingsRow, principal.user_id)
            return self._profile_statistics_settings_view(row)

    def set_profile_statistics_settings(
        self, principal: Principal, body: dict[str, object], now: datetime
    ) -> dict[str, object]:
        """Apply one exact idempotent opt-in or fail-safe privacy opt-out command."""
        operation_id = UUID(str(body["operation_id"]))
        expected_revision_value = body["expected_revision"]
        enabled_value = body["friends_can_view_statistics"]
        if (
            not isinstance(expected_revision_value, int)
            or isinstance(expected_revision_value, bool)
            or not 0 <= expected_revision_value <= 9_223_372_036_854_775_807
            or not isinstance(enabled_value, bool)
        ):
            raise ValueError("invalid profile statistics policy command")
        expected_revision = expected_revision_value
        enabled = enabled_value
        request: dict[str, object] = {
            "expected_revision": expected_revision,
            "friends_can_view_statistics": enabled,
        }
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            replay = s.get(SocialOperationReceiptRow, operation_id)
            if replay is not None:
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != "SET_PROFILE_STATISTICS_SETTINGS"
                    or replay.request_sha256 != request_hash
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            self._rate(s, "PROFILE_STATISTICS_SETTINGS_WRITE", str(principal.user_id), 10, now)
            row = s.get(ProfileStatisticsSettingsRow, principal.user_id, with_for_update=True)
            current_revision = 0 if row is None else row.revision
            if enabled and expected_revision != current_revision:
                raise SocialError("operation_conflict")
            if current_revision >= 9_223_372_036_854_775_807:
                raise SocialError("operation_conflict")
            next_revision = current_revision + 1
            if row is None:
                row = ProfileStatisticsSettingsRow(
                    user_id=principal.user_id,
                    friends_can_view_statistics=enabled,
                    revision=next_revision,
                    updated_at=now,
                )
                s.add(row)
            else:
                row.friends_can_view_statistics = enabled
                row.revision = next_revision
                row.updated_at = now
            result = {
                "schema_version": 1,
                "operation_id": str(operation_id),
                "friends_can_view_statistics": enabled,
                "revision": next_revision,
            }
            self._receipt(
                s,
                operation_id,
                principal,
                "SET_PROFILE_STATISTICS_SETTINGS",
                result,
                request,
                now,
            )
            return result

    def friend_profile_statistics(
        self, principal: Principal, target: UUID, now: datetime
    ) -> dict[str, object]:
        """Compute the fixed friend-visible projection after live transactional rechecks."""
        denied = False
        result: dict[str, object] | None = None
        with self._sessions.begin() as s:
            target_active = self._lock_profile_statistics_accounts(s, principal, target, now)
            self._pair_lock(s, principal.user_id, target)
            self._rate(s, "PROFILE_STATISTICS_READ_VIEWER", str(principal.user_id), 30, now)
            pair_key = ":".join(sorted((str(principal.user_id), str(target))))
            self._rate(s, "PROFILE_STATISTICS_READ_PAIR", pair_key, 10, now)
            setting = (
                s.get(ProfileStatisticsSettingsRow, target, with_for_update=True)
                if target_active
                and target != principal.user_id
                and self._is_friend(s, principal.user_id, target)
                and not self._blocked(s, principal.user_id, target)
                else None
            )
            if setting is None or not setting.friends_can_view_statistics:
                denied = True
            else:
                result = self._friend_profile_statistics_projection(s, target, now)
        if denied:
            raise SocialError("profile_statistics_unavailable")
        return result or _unreachable()

    def heartbeat(self, principal: Principal, operation_id: UUID, now: datetime) -> None:
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            row = s.get(
                PresenceHeartbeatRow,
                {"user_id": principal.user_id, "device_id": principal.device_id},
            )
            if row is not None and row.operation_id == operation_id:
                return
            if row is not None and row.last_heartbeat_at > now - timedelta(seconds=30):
                raise SocialError("rate_limited")
            if row is None:
                s.add(
                    PresenceHeartbeatRow(
                        user_id=principal.user_id,
                        device_id=principal.device_id,
                        session_id=principal.session_id,
                        operation_id=operation_id,
                        request_sha256=canonical_sha256({"heartbeat": str(principal.device_id)}),
                        last_heartbeat_at=now,
                        fresh_until=now + timedelta(seconds=90),
                    )
                )
            else:
                row.session_id = principal.session_id
                row.operation_id = operation_id
                row.request_sha256 = canonical_sha256({"heartbeat": str(principal.device_id)})
                row.last_heartbeat_at = now
                row.fresh_until = now + timedelta(seconds=90)

    def presence(self, principal: Principal, target: UUID, now: datetime) -> dict[str, str]:
        with self._sessions.begin() as s:
            self._account(s, principal.user_id)
            self._account(s, target)
            self._rate(s, "PRESENCE_READ", str(principal.user_id), 120, now)
            if not self._is_friend(s, principal.user_id, target) or self._blocked(
                s, principal.user_id, target
            ):
                raise SocialError("presence_private")
            return {"presence": self._aggregate(s, target, now)}

    def presence_page(self, principal: Principal, now: datetime) -> dict[str, object]:
        with self._sessions.begin() as s:
            self._account(s, principal.user_id)
            self._rate(s, "PRESENCE_READ", str(principal.user_id), 120, now)
            friend_ids = (
                s.execute(
                    text(
                        "SELECT CASE WHEN f.lower_user_id=:u THEN f.higher_user_id ELSE f.lower_user_id END FROM social.friendship f JOIN account.user_account a ON a.user_id=CASE WHEN f.lower_user_id=:u THEN f.higher_user_id ELSE f.lower_user_id END WHERE (f.lower_user_id=:u OR f.higher_user_id=:u) AND a.status='ACTIVE' AND a.deleted_at IS NULL ORDER BY 1 LIMIT 100"
                    ),
                    {"u": principal.user_id},
                )
                .scalars()
                .all()
            )
            return {
                "items": [
                    {"account_id": str(user_id), "presence": self._aggregate(s, user_id, now)}
                    for user_id in friend_ids
                    if not self._blocked(s, principal.user_id, user_id)
                ]
            }

    def create_invitation(
        self,
        principal: Principal,
        room_id: UUID,
        target: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> dict[str, object]:
        request: dict[str, object] = {"room_id": str(room_id), "target": str(target)}
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            replay = s.get(SocialOperationReceiptRow, operation_id)
            if replay is not None:
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != "CREATE_ROOM_INVITATION"
                    or replay.request_sha256 != request_hash
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            self._lock_accounts(s, principal.user_id, target)
            self._pair_lock(s, principal.user_id, target)
            self._rate(s, "ROOM_INVITE", str(principal.user_id), 20, now)
            if not self._is_friend(s, principal.user_id, target) or self._blocked(
                s, principal.user_id, target
            ):
                raise SocialError("friendship_required")
            if not self._settings(s, target, now).invite_availability_enabled:
                raise SocialError("presence_private")
            room = s.get(WaveRoomRow, room_id, with_for_update=True)
            if (
                room is None
                or room.state != "OPEN"
                or room.expires_at <= now
                or room.host_user_id != principal.user_id
                or room.host_device_id != principal.device_id
                or room.host_lost_at is not None
            ):
                raise SocialError("room_invitation_unavailable")
            host_member = s.get(
                WaveMemberRow, {"room_id": room_id, "device_id": principal.device_id}
            )
            if host_member is None or host_member.status != "JOINED" or host_member.role != "HOST":
                raise SocialError("room_invitation_unavailable")
            self._expire_room_invitations(s, room_id, target, now)
            if s.scalar(
                select(func.count())
                .select_from(WaveMemberRow)
                .where(
                    WaveMemberRow.room_id == room_id,
                    WaveMemberRow.user_id == target,
                    WaveMemberRow.status == "JOINED",
                )
            ):
                raise SocialError("room_invitation_unavailable")
            existing = s.scalar(
                select(FriendRoomInvitationRow)
                .where(
                    FriendRoomInvitationRow.room_id == room_id,
                    FriendRoomInvitationRow.target_user_id == target,
                    FriendRoomInvitationRow.state == "PENDING",
                    FriendRoomInvitationRow.expires_at > now,
                )
                .with_for_update()
            )
            if existing is not None:
                raise SocialError("room_invitation_unavailable")
            pending_count = (
                s.scalar(
                    select(func.count())
                    .select_from(FriendRoomInvitationRow)
                    .where(
                        FriendRoomInvitationRow.room_id == room_id,
                        FriendRoomInvitationRow.state == "PENDING",
                        FriendRoomInvitationRow.expires_at > now,
                    )
                )
                or 0
            )
            if pending_count >= 8:
                raise SocialError("room_full")
            invite = FriendRoomInvitationRow(
                invitation_id=uuid4(),
                create_operation_id=operation_id,
                room_id=room_id,
                room_epoch=room.room_epoch,
                host_user_id=principal.user_id,
                host_device_id=principal.device_id,
                target_user_id=target,
                state="PENDING",
                expires_at=min(room.expires_at, now + timedelta(minutes=10)),
                terminal_at=None,
                terminal_reason=None,
                accepted_device_id=None,
                accepting_session_id=None,
                created_at=now,
            )
            s.add(invite)
            result = self._invite_view(invite, now)
            self._receipt(
                s,
                operation_id,
                principal,
                "CREATE_ROOM_INVITATION",
                result,
                request,
                now,
            )
            return result

    def accept_invitation(
        self, principal: Principal, invitation_id: UUID, operation_id: UUID, now: datetime
    ) -> dict[str, object]:
        request: dict[str, object] = {"invitation_id": str(invitation_id)}
        request_hash = canonical_sha256(request)
        result: dict[str, object] | None = None
        terminal_error: str | None = None
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            replay = s.get(SocialOperationReceiptRow, operation_id)
            if replay is not None:
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != "ACCEPT_ROOM_INVITATION"
                    or replay.request_sha256 != request_hash
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            invite = s.get(FriendRoomInvitationRow, invitation_id, with_for_update=True)
            if invite is None or invite.target_user_id != principal.user_id:
                raise SocialError("room_invitation_unavailable")
            self._lock_accounts(s, invite.host_user_id, principal.user_id)
            self._pair_lock(s, invite.host_user_id, principal.user_id)
            room = s.get(WaveRoomRow, invite.room_id, with_for_update=True)
            if (
                invite.state != "PENDING"
                or invite.expires_at <= now
                or room is None
                or room.state != "OPEN"
                or room.expires_at <= now
                or room.room_epoch != invite.room_epoch
                or room.host_user_id != invite.host_user_id
                or room.host_device_id != invite.host_device_id
                or room.host_lost_at is not None
                or not self._is_friend(s, invite.host_user_id, principal.user_id)
                or self._blocked(s, invite.host_user_id, principal.user_id)
                or not self._settings(s, principal.user_id, now).invite_availability_enabled
            ):
                raise SocialError("room_changed")
            host_member = s.get(
                WaveMemberRow, {"room_id": room.room_id, "device_id": invite.host_device_id}
            )
            if host_member is None or host_member.status != "JOINED" or host_member.role != "HOST":
                raise SocialError("room_changed")
            if not self._has_active_device_session(
                s, invite.host_user_id, invite.host_device_id, now
            ):
                raise SocialError("room_changed")
            existing_target = s.scalar(
                select(WaveMemberRow)
                .where(
                    WaveMemberRow.room_id == room.room_id,
                    WaveMemberRow.user_id == principal.user_id,
                    WaveMemberRow.status == "JOINED",
                )
                .with_for_update()
            )
            if existing_target is not None:
                raise SocialError("room_invitation_unavailable")
            member_count = (
                s.scalar(
                    select(func.count())
                    .select_from(WaveMemberRow)
                    .where(
                        WaveMemberRow.room_id == room.room_id,
                        WaveMemberRow.status == "JOINED",
                    )
                )
                or 0
            )
            if member_count >= 8:
                invite.state = "FULL"
                invite.terminal_at = now
                invite.terminal_reason = "FULL"
                terminal_error = "room_full"
            else:
                member = s.get(
                    WaveMemberRow, {"room_id": room.room_id, "device_id": principal.device_id}
                )
                if member is None:
                    s.add(
                        WaveMemberRow(
                            room_id=room.room_id,
                            user_id=principal.user_id,
                            device_id=principal.device_id,
                            role="MEMBER",
                            status="JOINED",
                            joined_at=now,
                            left_at=None,
                            last_present_at=now,
                        )
                    )
                else:
                    member.user_id = principal.user_id
                    member.status = "JOINED"
                    member.left_at = None
                    member.last_present_at = now
                invite.state = "ACCEPTED"
                invite.accepted_device_id = principal.device_id
                invite.accepting_session_id = principal.session_id
                invite.terminal_at = now
                result = {
                    "operation_id": str(operation_id),
                    "invitation_id": str(invitation_id),
                    "room_id": str(room.room_id),
                    "room_epoch": room.room_epoch,
                    "membership_state": "MEMBER",
                }
                self._receipt(
                    s,
                    operation_id,
                    principal,
                    "ACCEPT_ROOM_INVITATION",
                    result,
                    request,
                    now,
                )
        if terminal_error is not None:
            raise SocialError(terminal_error)
        return result or _unreachable()

    def cancel_invitation(
        self, principal: Principal, invitation_id: UUID, operation_id: UUID, now: datetime
    ) -> dict[str, object]:
        request: dict[str, object] = {"invitation_id": str(invitation_id)}
        request_hash = canonical_sha256(request)
        with self._sessions.begin() as s:
            self._active_principal(s, principal, now)
            replay = s.get(SocialOperationReceiptRow, operation_id)
            if replay is not None:
                if (
                    replay.actor_user_id != principal.user_id
                    or replay.actor_device_id != principal.device_id
                    or replay.action != "CANCEL_ROOM_INVITATION"
                    or replay.request_sha256 != request_hash
                ):
                    raise SocialError("operation_conflict")
                return cast(dict[str, object], json.loads(replay.result_json))
            row = s.get(FriendRoomInvitationRow, invitation_id, with_for_update=True)
            if row is None or row.host_user_id != principal.user_id:
                raise SocialError("room_invitation_unavailable")
            if row.state == "PENDING":
                row.state = "CANCELLED"
                row.terminal_at = now
                row.terminal_reason = "CANCELLED"
            result = self._invite_view(row, now)
            self._receipt(
                s,
                operation_id,
                principal,
                "CANCEL_ROOM_INVITATION",
                result,
                request,
                now,
            )
            return result

    def cleanup(self, now: datetime, limit: int = 10_000) -> int:
        with self._sessions.begin() as s:
            result = s.execute(
                text(
                    "UPDATE social.friend_request SET state='EXPIRED',terminal_at=:n WHERE request_id IN (SELECT request_id FROM social.friend_request WHERE state='PENDING' AND expires_at<=:n ORDER BY expires_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"n": now, "l": limit},
            )
            s.execute(
                text(
                    "UPDATE social.friend_room_invitation SET state='EXPIRED',terminal_at=:n,terminal_reason='EXPIRED' WHERE invitation_id IN (SELECT invitation_id FROM social.friend_room_invitation WHERE state='PENDING' AND expires_at<=:n ORDER BY expires_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"n": now, "l": limit},
            )
            s.execute(
                text(
                    "DELETE FROM social.presence_heartbeat WHERE (user_id,device_id) IN (SELECT user_id,device_id FROM social.presence_heartbeat WHERE fresh_until<:cut ORDER BY fresh_until LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"cut": now - timedelta(minutes=10), "l": limit},
            )
            s.execute(
                text(
                    "DELETE FROM social.operation_receipt WHERE operation_id IN (SELECT operation_id FROM social.operation_receipt WHERE expires_at<=:n ORDER BY expires_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"n": now, "l": limit},
            )
            s.execute(
                text(
                    "DELETE FROM social.rate_window WHERE rate_key_sha256 IN (SELECT rate_key_sha256 FROM social.rate_window WHERE expires_at<=:n ORDER BY expires_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"n": now, "l": limit},
            )
            s.execute(
                text(
                    "DELETE FROM social.friend_request WHERE request_id IN (SELECT request_id FROM social.friend_request WHERE terminal_at<=:cut ORDER BY terminal_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"cut": now - timedelta(days=30), "l": limit},
            )
            s.execute(
                text(
                    "DELETE FROM social.friend_room_invitation WHERE invitation_id IN (SELECT invitation_id FROM social.friend_room_invitation WHERE terminal_at<=:cut ORDER BY terminal_at LIMIT :l FOR UPDATE SKIP LOCKED)"
                ),
                {"cut": now - timedelta(days=30), "l": limit},
            )
            return int(getattr(result, "rowcount", 0) or 0)

    def _target(self, s: Session, b: dict[str, object], action: str, now: datetime) -> UUID:
        if action == "SEND_REQUEST":
            card = b.get("contact_card")
            if not isinstance(card, dict):
                raise SocialError("friend_request_unavailable")
            try:
                if set(card) != {
                    "server_instance_id",
                    "account_id",
                    "display_name_hint",
                    "issued_at",
                    "expires_at",
                    "signature_b64url",
                }:
                    raise SocialError("friend_request_unavailable")
                instance = s.scalar(select(ServerInstanceRow))
                if (
                    instance is None
                    or UUID(str(card["server_instance_id"])) != instance.server_instance_id
                ):
                    raise SocialError("friend_request_unavailable")
                expires_at = datetime.fromisoformat(str(card["expires_at"]).replace("Z", "+00:00"))
                issued_at = datetime.fromisoformat(str(card["issued_at"]).replace("Z", "+00:00"))
                if (
                    issued_at.tzinfo is None
                    or expires_at.tzinfo is None
                    or issued_at > now
                    or expires_at <= now
                    or expires_at - issued_at > timedelta(days=30)
                    or not 1 <= len(str(card["display_name_hint"])) <= 120
                ):
                    raise SocialError("friend_request_unavailable")
                signed = dict(card)
                signature = str(signed.pop("signature_b64url"))
                verify_p1363(
                    instance.identity_public_key_spki,
                    "autplay:s1c:social-contact-card:v1\n",
                    canonical_sha256(signed),
                    signature,
                )
                return UUID(str(card["account_id"]))
            except (KeyError, TypeError, ValueError, ProfilePairingError) as error:
                raise SocialError("friend_request_unavailable") from error
        return UUID(str(b["target_account_id"]))

    def _account(self, s: Session, u: UUID) -> UserAccountRow:
        row = s.get(UserAccountRow, u, with_for_update=True)
        if row is None or row.status != "ACTIVE" or row.deleted_at is not None:
            raise SocialError("friend_request_unavailable")
        return row

    def _active_principal(self, s: Session, principal: Principal, now: datetime) -> UserSessionRow:
        self._account(s, principal.user_id)
        row = s.get(UserSessionRow, principal.session_id, with_for_update=True)
        if (
            row is None
            or row.user_id != principal.user_id
            or row.device_id != principal.device_id
            or row.revoked_at is not None
            or row.expires_at <= now
        ):
            raise SocialError("auth_attention_required")
        return row

    def _has_active_device_session(
        self, s: Session, user_id: UUID, device_id: UUID, now: datetime
    ) -> bool:
        return (
            s.scalar(
                select(UserSessionRow.session_id)
                .where(
                    UserSessionRow.user_id == user_id,
                    UserSessionRow.device_id == device_id,
                    UserSessionRow.revoked_at.is_(None),
                    UserSessionRow.expires_at > now,
                )
                .order_by(UserSessionRow.session_id)
                .limit(1)
                .with_for_update()
            )
            is not None
        )

    def _expire_pair_requests(self, s: Session, a: UUID, b: UUID, now: datetime) -> None:
        s.execute(
            text(
                "UPDATE social.friend_request SET state='EXPIRED',terminal_at=:n WHERE state='PENDING' AND expires_at<=:n AND ((requester_user_id=:a AND target_user_id=:b) OR (requester_user_id=:b AND target_user_id=:a))"
            ),
            {"a": a, "b": b, "n": now},
        )

    def _expire_room_invitations(
        self, s: Session, room_id: UUID, target: UUID, now: datetime
    ) -> None:
        s.execute(
            text(
                "UPDATE social.friend_room_invitation SET state='EXPIRED',terminal_at=:n,terminal_reason='EXPIRED' WHERE room_id=:r AND target_user_id=:u AND state='PENDING' AND expires_at<=:n"
            ),
            {"r": room_id, "u": target, "n": now},
        )

    def _lock_accounts(self, s: Session, a: UUID, b: UUID) -> None:
        rows = self._lock_account_rows(s, a, b)
        for user_id in sorted({a, b}, key=lambda value: value.bytes):
            row = rows.get(user_id)
            if row is None or row.status != "ACTIVE" or row.deleted_at is not None:
                raise SocialError("friend_request_unavailable")

    def _lock_account_rows(self, s: Session, a: UUID, b: UUID) -> dict[UUID, UserAccountRow]:
        """Lock a social account pair in one UUID-byte order before any session lock."""
        rows: dict[UUID, UserAccountRow] = {}
        for user_id in sorted({a, b}, key=lambda value: value.bytes):
            row = s.get(UserAccountRow, user_id, with_for_update=True)
            if row is not None:
                rows[user_id] = row
        return rows

    def _lock_profile_statistics_accounts(
        self, s: Session, principal: Principal, target: UUID, now: datetime
    ) -> bool:
        by_id = self._lock_account_rows(s, principal.user_id, target)
        actor = by_id.get(principal.user_id)
        if actor is None or actor.status != "ACTIVE" or actor.deleted_at is not None:
            raise SocialError("auth_attention_required")
        session = s.get(UserSessionRow, principal.session_id, with_for_update=True)
        if (
            session is None
            or session.user_id != principal.user_id
            or session.device_id != principal.device_id
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            raise SocialError("auth_attention_required")
        target_account = by_id.get(target)
        return (
            target_account is not None
            and target_account.status == "ACTIVE"
            and target_account.deleted_at is None
        )

    def _pair_lock(self, s: Session, a: UUID, b: UUID) -> None:
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:v,0))"),
            {"v": ": ".join(sorted((str(a), str(b))))},
        )

    def _pair(self, a: UUID, b: UUID) -> tuple[UUID, UUID]:
        return (a, b) if a.bytes < b.bytes else (b, a)

    def _is_friend(self, s: Session, a: UUID, b: UUID) -> bool:
        x, y = self._pair(a, b)
        return s.get(FriendshipRow, {"lower_user_id": x, "higher_user_id": y}) is not None

    def _friend(self, s: Session, a: UUID, b: UUID, n: datetime) -> None:
        x, y = self._pair(a, b)
        if s.get(FriendshipRow, {"lower_user_id": x, "higher_user_id": y}) is None:
            s.add(FriendshipRow(lower_user_id=x, higher_user_id=y, created_at=n))

    def _remove_friend(self, s: Session, a: UUID, b: UUID) -> None:
        x, y = self._pair(a, b)
        row = s.get(FriendshipRow, {"lower_user_id": x, "higher_user_id": y})
        if row is not None:
            s.delete(row)

    def _blocked(self, s: Session, a: UUID, b: UUID) -> bool:
        return (
            s.scalar(
                select(UserBlockRow)
                .where(
                    UserBlockRow.blocker_user_id.in_((a, b)),
                    UserBlockRow.blocked_user_id.in_((a, b)),
                    UserBlockRow.unblocked_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    def _shared_active_room_ids(self, s: Session, a: UUID, b: UUID, n: datetime) -> list[UUID]:
        return list(
            s.execute(
                text(
                    "SELECT r.room_id FROM wave.member ma JOIN wave.member mb ON ma.room_id=mb.room_id JOIN wave.room r ON r.room_id=ma.room_id WHERE ma.user_id=:a AND mb.user_id=:b AND ma.status='JOINED' AND mb.status='JOINED' AND r.state='OPEN' AND r.expires_at>:n ORDER BY r.room_id LIMIT 9 FOR UPDATE OF r"
                ),
                {"a": a, "b": b, "n": n},
            ).scalars()
        )

    def _cancel_requests(self, s: Session, a: UUID, b: UUID, n: datetime) -> None:
        s.execute(
            text(
                "UPDATE social.friend_request SET state='BLOCKED',terminal_at=:n WHERE state='PENDING' AND ((requester_user_id=:a AND target_user_id=:b) OR (requester_user_id=:b AND target_user_id=:a))"
            ),
            {"a": a, "b": b, "n": n},
        )

    def _cancel_invites(self, s: Session, a: UUID, b: UUID, n: datetime, state: str) -> None:
        s.execute(
            text(
                "UPDATE social.friend_room_invitation SET state=:x,terminal_at=:n WHERE state='PENDING' AND ((host_user_id=:a AND target_user_id=:b) OR (host_user_id=:b AND target_user_id=:a))"
            ),
            {"a": a, "b": b, "n": n, "x": state},
        )

    def _settings(self, s: Session, u: UUID, n: datetime) -> PresenceSettingsRow:
        row = s.get(PresenceSettingsRow, u)
        if row is None:
            row = PresenceSettingsRow(
                user_id=u,
                friend_presence_visibility_enabled=False,
                room_activity_sharing_enabled=False,
                invite_availability_enabled=False,
                updated_at=n,
            )
            s.add(row)
            s.flush()
        return row

    def _settings_view(self, r: PresenceSettingsRow) -> dict[str, bool]:
        return {
            "friend_presence_visibility_enabled": r.friend_presence_visibility_enabled,
            "room_activity_sharing_enabled": r.room_activity_sharing_enabled,
            "invite_availability_enabled": r.invite_availability_enabled,
        }

    def _profile_statistics_settings_view(
        self, row: ProfileStatisticsSettingsRow | None
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "friends_can_view_statistics": (
                False if row is None else row.friends_can_view_statistics
            ),
            "revision": 0 if row is None else row.revision,
        }

    def _friend_profile_statistics_projection(
        self, s: Session, user_id: UUID, now: datetime
    ) -> dict[str, object]:
        if now.tzinfo is None:
            raise ValueError("profile statistics clock must be timezone-aware")
        current_utc_day = now.astimezone(UTC).date()
        current_day_start = datetime.combine(current_utc_day, datetime.min.time(), tzinfo=UTC)
        start_7 = current_day_start - timedelta(days=7)
        start_30 = current_day_start - timedelta(days=30)
        start_365 = current_day_start - timedelta(days=365)
        maximum = 9_223_372_036_854_775_807
        aggregate = (
            s.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (WHERE e.started_at >= :start_7) AS plays_7,
                      least(coalesce(sum(e.played_ms) FILTER (WHERE e.started_at >= :start_7),0),:maximum) AS listened_7,
                      count(DISTINCT coalesce(utr.recording_id,e.recording_id,e.user_track_ref_id)) FILTER (WHERE e.started_at >= :start_7) AS unique_7,
                      count(*) FILTER (WHERE e.started_at >= :start_30) AS plays_30,
                      least(coalesce(sum(e.played_ms) FILTER (WHERE e.started_at >= :start_30),0),:maximum) AS listened_30,
                      count(DISTINCT coalesce(utr.recording_id,e.recording_id,e.user_track_ref_id)) FILTER (WHERE e.started_at >= :start_30) AS unique_30,
                      count(*) AS plays_365,
                      least(coalesce(sum(e.played_ms),0),:maximum) AS listened_365,
                      count(DISTINCT coalesce(utr.recording_id,e.recording_id,e.user_track_ref_id)) AS unique_365
                    FROM library.listening_event e
                    LEFT JOIN library.user_track_ref utr
                      ON utr.user_track_ref_id=e.user_track_ref_id AND utr.user_id=e.user_id
                    WHERE e.user_id=:user_id
                      AND e.started_at>=:start_365 AND e.started_at<:current_day_start
                      AND e.played_ms>0 AND e.excluded_from_taste=false
                    """
                ),
                {
                    "user_id": user_id,
                    "start_7": start_7,
                    "start_30": start_30,
                    "start_365": start_365,
                    "current_day_start": current_day_start,
                    "maximum": maximum,
                },
            )
            .mappings()
            .one()
        )

        def window(name: str, suffix: str) -> dict[str, object]:
            return {
                "window": name,
                "play_session_count": int(aggregate[f"plays_{suffix}"] or 0),
                "listened_ms": int(aggregate[f"listened_{suffix}"] or 0),
                "unique_track_count": int(aggregate[f"unique_{suffix}"] or 0),
            }

        return {
            "schema_version": 1,
            "through_utc_date": (current_utc_day - timedelta(days=1)).isoformat(),
            "windows": [
                window("LAST_7_COMPLETE_DAYS", "7"),
                window("LAST_30_COMPLETE_DAYS", "30"),
                window("LAST_365_COMPLETE_DAYS", "365"),
            ],
        }

    def _invite_view(self, r: FriendRoomInvitationRow, now: datetime) -> dict[str, object]:
        return {
            "invitation_id": str(r.invitation_id),
            "operation_id": str(r.create_operation_id),
            "kind": "FRIEND",
            "state": self._public_invite_state(r, now),
            "creator_account_id": str(r.host_user_id),
            "room_id": str(r.room_id),
            "room_epoch": r.room_epoch,
            "target_account_id": str(r.target_user_id),
            "expires_at": iso8601(r.expires_at),
        }

    def _snapshot_invite(self, row: FriendRoomInvitationRow, now: datetime) -> dict[str, object]:
        return {
            "invitation_id": str(row.invitation_id),
            "state": self._public_invite_state(row, now),
            "room_id": str(row.room_id),
            "room_epoch": row.room_epoch,
            "expires_at": iso8601(row.expires_at),
        }

    def _public_invite_state(self, row: FriendRoomInvitationRow, now: datetime) -> str:
        if row.state == "PENDING" and row.expires_at <= now:
            return "EXPIRED"
        if row.state in {"BLOCKED", "ROOM_CHANGED"}:
            return "UNAVAILABLE"
        return row.state

    def _aggregate(self, s: Session, user_id: UUID, now: datetime) -> str:
        account = s.get(UserAccountRow, user_id)
        if account is None or account.status != "ACTIVE" or account.deleted_at is not None:
            return "OFFLINE"
        settings = self._settings(s, user_id, now)
        if not settings.friend_presence_visibility_enabled:
            return "OFFLINE"
        fresh = s.scalar(
            select(PresenceHeartbeatRow)
            .join(UserSessionRow, UserSessionRow.session_id == PresenceHeartbeatRow.session_id)
            .where(
                PresenceHeartbeatRow.user_id == user_id,
                PresenceHeartbeatRow.fresh_until > now,
                UserSessionRow.user_id == user_id,
                UserSessionRow.device_id == PresenceHeartbeatRow.device_id,
                UserSessionRow.revoked_at.is_(None),
                UserSessionRow.expires_at > now,
            )
            .limit(1)
        )
        if fresh is None:
            return "OFFLINE"
        if (
            settings.room_activity_sharing_enabled
            and s.execute(
                text(
                    "SELECT 1 FROM social.presence_heartbeat h JOIN account.user_session us ON us.session_id=h.session_id AND us.user_id=h.user_id AND us.device_id=h.device_id JOIN wave.member m ON m.user_id=h.user_id AND m.device_id=h.device_id JOIN wave.room r ON r.room_id=m.room_id WHERE h.user_id=:u AND h.fresh_until>:n AND us.revoked_at IS NULL AND us.expires_at>:n AND m.status='JOINED' AND r.state='OPEN' AND r.expires_at>:n LIMIT 1"
                ),
                {"u": user_id, "n": now},
            ).first()
            is not None
        ):
            return "IN_ROOM"
        return "AVAILABLE_TO_INVITE" if settings.invite_availability_enabled else "ONLINE"

    def _rate(self, s: Session, scope: str, subject: str, limit: int, now: datetime) -> None:
        key = hashlib.sha256(f"{scope}:{subject}".encode()).digest()
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:v,0))"),
            {"v": key.hex()},
        )
        row = s.get(SocialRateWindowRow, key, with_for_update=True)
        if row is None:
            s.add(
                SocialRateWindowRow(
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
            raise SocialError("rate_limited")
        row.attempt_count += 1

    def _receipt(
        self,
        s: Session,
        op: UUID,
        principal: Principal,
        action: str,
        result: Mapping[str, object],
        request: Mapping[str, object],
        n: datetime,
    ) -> None:
        s.add(
            SocialOperationReceiptRow(
                operation_id=op,
                actor_user_id=principal.user_id,
                actor_device_id=principal.device_id,
                action=action,
                request_sha256=canonical_sha256(request),
                result_code=str(result.get("state", result.get("outcome", "APPLIED"))),
                result_target_id=_result_uuid(result, "request_id", "invitation_id"),
                result_room_id=_result_uuid(result, "room_id"),
                result_json=json.dumps(result, sort_keys=True),
                expires_at=n + timedelta(days=30),
                created_at=n,
            )
        )


__all__ = ("SocialError", "SocialService")


def _result_uuid(result: Mapping[str, object], *keys: str) -> UUID | None:
    for key in keys:
        value = result.get(key)
        if value is not None:
            return UUID(str(value))
    return None


def _unreachable() -> dict[str, object]:
    raise RuntimeError("social acceptance produced no terminal result")
