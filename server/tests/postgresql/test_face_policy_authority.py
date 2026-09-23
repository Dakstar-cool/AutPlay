"""Face self choice and separate safety inhibition stay generation fenced."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.face_policy import (
    FacePolicyCommand,
    FacePolicyError,
    SqlAlchemyFacePolicyAuthority,
)

from .conftest import DatabaseHarness


def _users(database_harness: DatabaseHarness, name: str) -> tuple[UUID, UUID]:
    with database_harness.connect(name) as connection:
        owner = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('policy-participant','USER') RETURNING user_id"
        ).fetchone()
        administrator = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('policy-administrator','ADMIN') RETURNING user_id"
        ).fetchone()
        assert owner is not None and administrator is not None
        connection.commit()
        return owner[0], administrator[0]


def test_face_policy_self_and_safety_are_separate_and_idempotent(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    participant, admin = _users(database_harness, empty_database_name)
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    authority = SqlAlchemyFacePolicyAuthority()
    enable = FacePolicyCommand(participant, participant, uuid4(), 0, "SELF", True, "opt in")
    try:
        with sessions.begin() as session:
            first = authority.apply(session, enable)
            assert (first.policy_generation, first.desired_enabled, first.admin_inhibited) == (
                1,
                True,
                False,
            )
            assert first.enable_watermark is not None

        inhibit = FacePolicyCommand(admin, participant, uuid4(), 1, "SAFETY", True, "safety")
        with sessions.begin() as session:
            second = authority.apply(session, inhibit)
            assert (second.policy_generation, second.desired_enabled, second.admin_inhibited) == (
                2,
                True,
                True,
            )
            assert second.enable_watermark == first.enable_watermark

        with sessions.begin() as session:
            replay = authority.apply(session, enable)
            assert replay == first
            with pytest.raises(FacePolicyError, match="operation_conflict"):
                authority.apply(session, replace(enable, value=False))
            with pytest.raises(FacePolicyError, match="generation_conflict"):
                authority.apply(
                    session,
                    FacePolicyCommand(participant, participant, uuid4(), 1, "SELF", False, "stale"),
                )
            with pytest.raises(FacePolicyError, match="actor_forbidden"):
                authority.apply(
                    session,
                    FacePolicyCommand(
                        participant, participant, uuid4(), 2, "SAFETY", False, "forbidden"
                    ),
                )
            assert session.scalar(text("SELECT count(*) FROM ml.face_analysis_policy_history")) == 2

        with pytest.raises(FacePolicyError, match="self_target_mismatch"):
            FacePolicyCommand(admin, participant, uuid4(), 2, "SELF", True, "cross user")

        with sessions.begin() as session:
            disabled = authority.apply(
                session,
                FacePolicyCommand(participant, participant, uuid4(), 2, "SELF", False, "opt out"),
            )
            assert disabled.policy_generation == 3
            assert disabled.desired_enabled is False
            assert disabled.admin_inhibited is True
            assert disabled.enable_watermark is None
            assert (
                session.scalar(
                    text("""
                    SELECT effective_enabled FROM ml.face_analysis_policy_current
                    WHERE owner_user_id=:owner
                """),
                    {"owner": participant},
                )
                is False
            )

        with sessions.begin() as session:
            lifted = authority.apply(
                session,
                FacePolicyCommand(admin, participant, uuid4(), 3, "SAFETY", False, "clear"),
            )
            assert (lifted.desired_enabled, lifted.admin_inhibited) == (False, False)
            assert lifted.enable_watermark is None
    finally:
        engine.dispose()
