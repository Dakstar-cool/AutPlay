"""Bounded owner export of portable data, excluding credential envelopes by construction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models import (
    PortableAccountRecordRow,
    PortableAccountRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.portable_taste import PortableTasteSignalRow
from autplay.adapters.postgresql.models.public_id import PublicIdRegistrationRow
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.application.portable_account import PortableAccountService
from autplay.application.portable_account_data import validate_portable_record
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import ProfilePairingError, iso8601

_INITIAL_ID = UUID(int=0)


class PortableAccountOwnerExportService:
    """Internal operator-assisted projection; this is not a recovery backup or HTTP API.

    Callers supply an authenticated owner principal, never a requested owner UUID. A complete
    archive also requires the existing owner-scoped library/Vault export procedure. Run against
    a quiesced isolated copy for a consistent archive across pages.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def page(
        self,
        actor: Principal,
        *,
        source: str = "records",
        after_time: datetime = datetime(1970, 1, 1, tzinfo=UTC),
        after_id: UUID = _INITIAL_ID,
    ) -> dict[str, Any]:
        """Export at most 32 strict data documents and an explicit public identity projection."""
        if (
            source not in {"records", "taste"}
            or after_time.tzinfo is None
            or after_time < datetime(1970, 1, 1, tzinfo=UTC)
        ):
            raise ValueError("export cursor invalid")
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            PortableAccountService._actor(session, actor, datetime.now(UTC))
            # Never SELECT the identity row/document: future secret columns remain excluded.
            identity = session.execute(
                select(
                    PortableAccountRow.key_thumbprint,
                    PortableAccountRow.revision,
                    PortableAccountRow.created_at,
                    PortableAccountRow.updated_at,
                ).where(PortableAccountRow.user_id == actor.user_id)
            ).one_or_none()
            public_id = session.scalar(
                select(PublicIdRegistrationRow.public_id).where(
                    PublicIdRegistrationRow.user_id == actor.user_id
                )
            )
            candidates: list[tuple[UUID, str, dict[str, Any], datetime]] = []
            if source == "records":
                rows = session.scalars(
                    select(PortableAccountRecordRow)
                    .where(
                        PortableAccountRecordRow.user_id == actor.user_id,
                        tuple_(
                            PortableAccountRecordRow.updated_at, PortableAccountRecordRow.record_id
                        )
                        > tuple_(literal(after_time), literal(after_id)),
                    )
                    .order_by(
                        PortableAccountRecordRow.updated_at, PortableAccountRecordRow.record_id
                    )
                    .limit(32)
                ).all()
                candidates = [
                    (row.record_id, row.kind, row.document, row.updated_at) for row in rows
                ]
            else:
                tastes = session.execute(
                    select(PortableTasteSignalRow, UserTrackRefRow)
                    .join(
                        UserTrackRefRow,
                        (
                            UserTrackRefRow.user_track_ref_id
                            == PortableTasteSignalRow.user_track_ref_id
                        )
                        & (UserTrackRefRow.user_id == PortableTasteSignalRow.user_id),
                    )
                    .where(
                        PortableTasteSignalRow.user_id == actor.user_id,
                        tuple_(PortableTasteSignalRow.updated_at, PortableTasteSignalRow.record_id)
                        > tuple_(literal(after_time), literal(after_id)),
                    )
                    .order_by(PortableTasteSignalRow.updated_at, PortableTasteSignalRow.record_id)
                    .limit(32)
                ).all()
                candidates = [
                    (
                        taste.record_id,
                        "TASTE",
                        {
                            "version": 1,
                            "updated_at_ms": taste.occurred_at_ms,
                            "track_key": str(track.recording_id or track.user_track_ref_id),
                            "interaction": taste.interaction,
                            "weight": taste.weight,
                        },
                        taste.updated_at,
                    )
                    for taste, track in tastes
                ]
            records: list[dict[str, Any]] = []
            for identifier, stored_kind, stored_document, _ in candidates:
                try:
                    _, kind, document, _ = validate_portable_record(
                        {
                            "record_id": str(identifier),
                            "kind": stored_kind,
                            "document": stored_document,
                        }
                    )
                except ValueError, TypeError, KeyError, OverflowError, RecursionError:
                    # An unrecognized or corrupt stored document must not leak arbitrary fields.
                    raise ProfilePairingError("account_export_invalid") from None
                records.append({"record_id": str(identifier), "kind": kind, "document": document})
            cursor = (
                {"time": iso8601(candidates[-1][3]), "id": str(candidates[-1][0])}
                if candidates
                else None
            )
            return {
                "format": "autplay-portable-owner-export",
                "version": 1,
                "source": source,
                "account_id": str(actor.user_id),
                "public_id": public_id,
                "identity": (
                    {
                        "key_thumbprint_sha256": identity.key_thumbprint.hex(),
                        "revision": identity.revision,
                        "created_at": iso8601(identity.created_at),
                        "updated_at": iso8601(identity.updated_at),
                    }
                    if identity is not None
                    else None
                ),
                "records": records,
                "cursor": cursor,
                "next": cursor if len(candidates) == 32 else None,
            }
