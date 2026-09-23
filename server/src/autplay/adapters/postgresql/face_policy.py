"""Dormant, transaction-bound Face analysis policy authority.

The authenticated entrypoint must supply the actor and, for SELF, use that
same principal as the target. This writer does not expose an HTTP operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

import rfc8785
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

_REQUEST_DOMAIN = b"autplay.ml.face-policy-operation.v1\0"
_MAX_SAFE_GENERATION = 9_007_199_254_740_991


class FacePolicyError(ValueError):
    """The command cannot be applied under current Face policy authority."""


@dataclass(frozen=True, slots=True)
class FacePolicyCommand:
    actor_user_id: UUID
    owner_user_id: UUID
    operation_id: UUID
    expected_generation: int
    scope: str
    value: bool
    reason: str

    def __post_init__(self) -> None:
        if (
            type(self.actor_user_id) is not UUID
            or type(self.owner_user_id) is not UUID
            or type(self.operation_id) is not UUID
            or type(self.expected_generation) is not int
            or not 0 <= self.expected_generation < _MAX_SAFE_GENERATION
            or type(self.scope) is not str
            or self.scope not in {"SELF", "SAFETY"}
            or type(self.value) is not bool
            or type(self.reason) is not str
            or not 1 <= len(self.reason) <= 200
        ):
            raise FacePolicyError("face_policy_command_invalid")
        if self.scope == "SELF" and self.actor_user_id != self.owner_user_id:
            raise FacePolicyError("face_policy_self_target_mismatch")

    def request_sha256(self) -> bytes:
        document = {
            "actor_user_id": str(self.actor_user_id),
            "owner_user_id": str(self.owner_user_id),
            "operation_id": str(self.operation_id),
            "expected_generation": self.expected_generation,
            "scope": self.scope,
            "value": self.value,
            "reason": self.reason,
        }
        return sha256(_REQUEST_DOMAIN + rfc8785.dumps(cast(Any, document))).digest()


@dataclass(frozen=True, slots=True)
class FacePolicyReceipt:
    event_id: UUID
    owner_user_id: UUID
    policy_generation: int
    desired_enabled: bool
    admin_inhibited: bool
    activation_epoch: int
    enable_watermark: datetime | None
    request_sha256: bytes


class SqlAlchemyFacePolicyAuthority:
    """Append one exact policy event and its current projection atomically."""

    def apply(self, session: Session, command: FacePolicyCommand) -> FacePolicyReceipt:
        if not session.in_transaction():
            raise FacePolicyError("face_policy_transaction_required")
        if type(command) is not FacePolicyCommand:
            raise FacePolicyError("face_policy_command_invalid")

        principals = (
            session.execute(
                text("""
                SELECT user_id,role,status,deleted_at FROM account.user_account
                WHERE user_id IN (:actor,:owner) ORDER BY user_id FOR UPDATE
            """),
                {"actor": command.actor_user_id, "owner": command.owner_user_id},
            )
            .mappings()
            .all()
        )
        users = {row["user_id"]: row for row in principals}
        actor = users.get(command.actor_user_id)
        owner = users.get(command.owner_user_id)
        if (
            actor is None
            or actor["status"] != "ACTIVE"
            or actor["deleted_at"] is not None
            or actor["role"] not in {"OWNER", "ADMIN", "USER"}
            or owner is None
            or owner["status"] != "ACTIVE"
            or owner["deleted_at"] is not None
            or (command.scope == "SAFETY" and actor["role"] not in {"OWNER", "ADMIN"})
        ):
            raise FacePolicyError("face_policy_actor_forbidden")

        request_hash = command.request_sha256()
        prior = (
            session.execute(
                text("""
                SELECT face_analysis_policy_event_id,owner_user_id,policy_generation,
                  desired_enabled,admin_inhibited,activation_epoch,enable_watermark,
                  request_sha256
                FROM ml.face_analysis_policy_history
                WHERE actor_user_id=:actor AND operation_id=:operation
            """),
                {"actor": command.actor_user_id, "operation": command.operation_id},
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            if prior["request_sha256"] != request_hash:
                raise FacePolicyError("face_policy_operation_conflict")
            return _receipt(prior)

        current = (
            session.execute(
                text("""
                SELECT policy_generation,desired_enabled,admin_inhibited,enable_watermark
                FROM ml.face_analysis_policy_current
                WHERE owner_user_id=:owner FOR UPDATE
            """),
                {"owner": command.owner_user_id},
            )
            .mappings()
            .one_or_none()
        )
        generation = current["policy_generation"] if current is not None else 0
        if generation != command.expected_generation:
            raise FacePolicyError("face_policy_generation_conflict")
        activation_epoch = session.scalar(
            text("""
                SELECT activation_epoch FROM ml.face_activation_current
                WHERE singleton=1 FOR SHARE
            """)
        )
        if activation_epoch is None:
            raise FacePolicyError("face_policy_activation_authority_missing")

        desired_enabled = current["desired_enabled"] if current is not None else False
        admin_inhibited = current["admin_inhibited"] if current is not None else False
        enable_watermark = current["enable_watermark"] if current is not None else None
        if command.scope == "SELF":
            if command.value and not desired_enabled:
                enable_watermark = session.scalar(text("SELECT clock_timestamp()"))
            elif not command.value:
                enable_watermark = None
            desired_enabled = command.value
        else:
            admin_inhibited = command.value

        event = (
            session.execute(
                text("""
                INSERT INTO ml.face_analysis_policy_history
                  (owner_user_id,policy_generation,desired_enabled,admin_inhibited,
                   enable_watermark,activation_epoch,actor_user_id,actor_role,scope,
                   reason,operation_id,request_sha256)
                VALUES
                  (:owner,:generation,:desired,:inhibited,:watermark,:epoch,:actor,
                   :actor_role,:scope,:reason,:operation,:request_hash)
                RETURNING face_analysis_policy_event_id,owner_user_id,policy_generation,
                  desired_enabled,admin_inhibited,activation_epoch,enable_watermark,
                  request_sha256
            """),
                {
                    "owner": command.owner_user_id,
                    "generation": generation + 1,
                    "desired": desired_enabled,
                    "inhibited": admin_inhibited,
                    "watermark": enable_watermark,
                    "epoch": activation_epoch,
                    "actor": command.actor_user_id,
                    "actor_role": actor["role"],
                    "scope": command.scope,
                    "reason": command.reason,
                    "operation": command.operation_id,
                    "request_hash": request_hash,
                },
            )
            .mappings()
            .one()
        )
        if current is None:
            session.execute(
                text("""
                    INSERT INTO ml.face_analysis_policy_current
                      (owner_user_id,policy_generation,desired_enabled,admin_inhibited,
                       enable_watermark,activation_epoch,face_analysis_policy_event_id)
                    VALUES
                      (:owner,:generation,:desired,:inhibited,:watermark,:epoch,:event)
                """),
                {
                    "owner": command.owner_user_id,
                    "generation": generation + 1,
                    "desired": desired_enabled,
                    "inhibited": admin_inhibited,
                    "watermark": enable_watermark,
                    "epoch": activation_epoch,
                    "event": event["face_analysis_policy_event_id"],
                },
            )
        else:
            session.execute(
                text("""
                    UPDATE ml.face_analysis_policy_current
                    SET policy_generation=:generation,desired_enabled=:desired,
                      admin_inhibited=:inhibited,enable_watermark=:watermark,
                      activation_epoch=:epoch,face_analysis_policy_event_id=:event
                    WHERE owner_user_id=:owner
                """),
                {
                    "owner": command.owner_user_id,
                    "generation": generation + 1,
                    "desired": desired_enabled,
                    "inhibited": admin_inhibited,
                    "watermark": enable_watermark,
                    "epoch": activation_epoch,
                    "event": event["face_analysis_policy_event_id"],
                },
            )
        return _receipt(event)


def _receipt(values: RowMapping) -> FacePolicyReceipt:
    return FacePolicyReceipt(
        event_id=values["face_analysis_policy_event_id"],
        owner_user_id=values["owner_user_id"],
        policy_generation=values["policy_generation"],
        desired_enabled=values["desired_enabled"],
        admin_inhibited=values["admin_inhibited"],
        activation_epoch=values["activation_epoch"],
        enable_watermark=values["enable_watermark"],
        request_sha256=values["request_sha256"],
    )
