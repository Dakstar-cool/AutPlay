"""Owner export isolation and accepted erasure of portable authority and personal data."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models import (
    PortableAccountChallengeRow,
    PortableAccountRecordRow,
    PortableAccountRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.portable_taste import PortableTasteSignalRow
from autplay.application.portable_account_data import validate_portable_record
from autplay.application.portable_account_export import PortableAccountOwnerExportService
from autplay.domain.profile_pairing import ProfilePairingError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .test_account_deletion import PairingHarness
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_account_recovery import required
from .test_privacy_purge import overdue
from .test_social_s1c import _principal


def _identity(session: Session, owner: UUID, now: datetime) -> None:
    """Synthetic stored envelopes only; no real password, root key or recovery material."""
    session.add(
        PortableAccountRow(
            user_id=owner,
            public_key=b"p" * 91,
            key_thumbprint=b"k" * 32,
            document={
                "account_id": str(owner),
                "password_ciphertext_b64": "fixture-private-passport",
                "recovery_ciphertext_b64": "fixture-private-recovery",
            },
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )


def _records(session: Session, owner: UUID, now: datetime, count: int = 5) -> set[UUID]:
    track, playlist = str(uuid4()), str(uuid4())
    identifiers: set[UUID] = set()
    for index in range(count):
        identifier = uuid4()
        kind = ("LISTENING", "PREFERENCE", "PLAYLIST", "PLAYLIST_ENTRY", "TASTE")[index % 5]
        document: dict[str, Any] = {"version": 1, "updated_at_ms": 1000 + index}
        if kind == "LISTENING":
            document.update(
                event_ids=[str(identifier)],
                track_key=track,
                title="Fixture track",
                artist="Fixture artist",
                genres=["Rock"],
                started_at_ms=1000,
                played_ms=5000,
                duration_ms=10000,
                excluded_from_taste=False,
                event_origin="ORGANIC",
                context="GENERAL",
            )
        elif kind == "PREFERENCE":
            document.update(
                track_key=track,
                title=None,
                artist=None,
                preference="LIKED",
                rating=5,
                excluded_from_taste=False,
            )
        elif kind == "PLAYLIST":
            document.update(name="Fixture playlist", description=None, deleted=False)
        elif kind == "PLAYLIST_ENTRY":
            document.update(
                playlist_id=playlist,
                track_key=track,
                title=None,
                artist=None,
                position_key="a",
                deleted=False,
            )
        else:
            document.update(track_key=track, interaction="LIKE", weight=100)
        _, _, normalized, digest = validate_portable_record(
            {"record_id": str(identifier), "kind": kind, "document": document}
        )
        session.add(
            PortableAccountRecordRow(
                user_id=owner,
                record_id=identifier,
                kind=kind,
                document=normalized,
                content_hash=digest,
                created_at=now,
                updated_at=now + timedelta(microseconds=index),
            )
        )
        identifiers.add(identifier)
    return identifiers


def _challenge(
    session: Session,
    account: UUID,
    now: datetime,
    *,
    document: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    device: UUID | None = None,
    actor_session: UUID | None = None,
) -> UUID:
    identifier = uuid4()
    session.add(
        PortableAccountChallengeRow(
            challenge_id=identifier,
            account_id=account,
            purpose="LOGIN",
            source_key=b"s" * 32,
            actor_device_id=device,
            actor_session_id=actor_session,
            document=document or {"account_id": str(account)},
            created_at=now,
            expires_at=now + timedelta(minutes=2),
            receipt=receipt,
        )
    )
    return identifier


def _taste(session: Session, owner: UUID, now: datetime) -> UUID:
    track, identifier = uuid4(), uuid4()
    session.add(UserTrackRefRow(user_track_ref_id=track, user_id=owner))
    session.flush()
    session.add(
        PortableTasteSignalRow(
            user_id=owner,
            record_id=identifier,
            user_track_ref_id=track,
            interaction="LIKE",
            weight=100,
            occurred_at_ms=1000,
            updated_at=now,
        )
    )
    return identifier


def test_export_pages_exclude_passport_receipts_and_other_owner_and_revalidate_session(
    pair: PairingHarness,
) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        other = _principal(session, "Other export owner", now)
        _identity(session, pair.actor.user_id, now)
        _identity(session, other.user_id, now)
        owned = _records(session, pair.actor.user_id, now, 33)
        foreign = _records(session, other.user_id, now)
        owned_taste = _taste(session, pair.actor.user_id, now)
        foreign_taste = _taste(session, other.user_id, now)
        _challenge(session, pair.actor.user_id, now, receipt={"token": "fixture-private-receipt"})
    export = PortableAccountOwnerExportService(sessions)
    first = export.page(pair.actor)
    assert len(first["records"]) == 32 and first["next"] == first["cursor"]
    cursor = first["next"]
    second = export.page(
        pair.actor, after_time=datetime.fromisoformat(cursor["time"]), after_id=UUID(cursor["id"])
    )
    assert len(second["records"]) == 1 and second["next"] is None
    exported = first["records"] + second["records"]
    assert {UUID(row["record_id"]) for row in exported} == owned
    assert {row["kind"] for row in exported} == {
        "LISTENING",
        "PREFERENCE",
        "PLAYLIST",
        "PLAYLIST_ENTRY",
        "TASTE",
    }
    serialized = json.dumps([first, second])
    assert all(str(identifier) not in serialized for identifier in foreign)
    assert str(other.user_id) not in serialized
    assert "fixture-private" not in serialized
    assert all(word not in serialized for word in ("ciphertext", "recovery", "password", "receipt"))
    assert set(first["identity"]) == {
        "key_thumbprint_sha256",
        "revision",
        "created_at",
        "updated_at",
    }
    taste = export.page(pair.actor, source="taste")
    assert [row["record_id"] for row in taste["records"]] == [str(owned_taste)]
    assert str(foreign_taste) not in json.dumps(taste)
    assert taste["records"][0]["document"]["interaction"] == "LIKE"
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        export.page(replace(pair.actor, user_id=other.user_id))
    with sessions.begin() as session:
        required(session.get(UserSessionRow, pair.actor.session_id)).revoked_at = now
    with pytest.raises(ProfilePairingError, match="account_authentication_failed"):
        export.page(pair.actor)


def test_export_rejects_unrecognized_stored_secret_fields(pair: PairingHarness) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    with sessions.begin() as session:
        _records(session, pair.actor.user_id, datetime.now(UTC), 1)
        session.flush()
        row = required(session.scalar(select(PortableAccountRecordRow)))
        row.document = {**row.document, "private_passport": "fixture-private-corruption"}
    with pytest.raises(ProfilePairingError, match="account_export_invalid"):
        PortableAccountOwnerExportService(sessions).page(pair.actor)


def test_accepted_purge_cascades_portable_data_and_erases_all_challenge_references(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        other = _principal(session, "Surviving portable owner", now)
        _identity(session, pair.actor.user_id, now)
        _identity(session, other.user_id, now)
        _records(session, pair.actor.user_id, now)
        foreign = _records(session, other.user_id, now)
        _taste(session, pair.actor.user_id, now)
        surviving_taste = _taste(session, other.user_id, now)
        _challenge(session, pair.actor.user_id, now)
        _challenge(session, other.user_id, now, device=pair.actor.device_id)
        _challenge(session, other.user_id, now, actor_session=pair.actor.session_id)
        _challenge(
            session, other.user_id, now, document={"nested": {"owner": str(pair.actor.user_id)}}
        )
        _challenge(
            session, other.user_id, now, receipt={"nested": {"device": str(pair.actor.device_id)}}
        )
        surviving_challenge = _challenge(session, other.user_id, now)
    # Arbitrary account DELETE is never an authorized replacement for accepted privacy erasure.
    with pytest.raises(DBAPIError), pair.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM account.user_account WHERE user_id=:owner"),
            {"owner": pair.actor.user_id},
        )
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        connection.execute(
            text("SELECT account.verify_owner_absent(:owner, ARRAY[:device]::uuid[])"),
            {"owner": pair.actor.user_id, "device": pair.actor.device_id},
        )
    with sessions() as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
        assert session.get(PortableAccountRow, pair.actor.user_id) is None
        assert session.get(PortableAccountRow, other.user_id) is not None
        assert set(session.scalars(select(PortableAccountRecordRow.record_id))) == foreign
        assert list(session.scalars(select(PortableAccountChallengeRow.challenge_id))) == [
            surviving_challenge,
        ]
        assert session.get(UserAccountRow, other.user_id) is not None
        assert list(session.scalars(select(PortableTasteSignalRow.record_id))) == [surviving_taste]
