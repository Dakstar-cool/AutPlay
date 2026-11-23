"""Create private immutable receipts from server-hydrated catalogue entities."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
from autplay.adapters.postgresql.confirmed_edition import musicbrainz_reference
from autplay.adapters.postgresql.models import (
    SyncEventRow,
    UserTrackRefExternalReferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.catalogue_context import InternetCatalogueContextRow
from autplay.adapters.postgresql.resource_authority import ResourceAuthorityGate
from autplay.adapters.postgresql.resource_limits import lock_resource_admission
from autplay.adapters.public_catalogue_context import MusicBrainzCatalogueContextProvider
from autplay.application.music_library import MusicError
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.domain.auth import Principal
from autplay.domain.catalogue_context import CatalogueContextEntity
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.ports.music_discovery import MusicDiscoveryHttp
from autplay.ports.track_metadata import MetadataProviderError

type CatalogueHttpFactory = Callable[[Principal], AbstractContextManager[MusicDiscoveryHttp]]


class CatalogueContextService:
    def __init__(self, sessions: sessionmaker[Session], http_factory: CatalogueHttpFactory) -> None:
        self.sessions, self.http_factory = sessions, http_factory

    def create(
        self,
        principal: Principal,
        entity_type: CatalogueContextEntity,
        entity_id: UUID,
        release_id: UUID | None = None,
    ) -> dict[str, object]:
        try:
            return self._create(principal, entity_type, entity_id, release_id)
        except SQLAlchemyError as error:
            raise MusicError(
                "music_catalogue_unavailable",
                "The catalogue is temporarily unavailable.",
                503,
                retryable=True,
            ) from error

    def _create(
        self,
        principal: Principal,
        entity_type: CatalogueContextEntity,
        entity_id: UUID,
        release_id: UUID | None = None,
    ) -> dict[str, object]:
        try:
            entity_type = CatalogueContextEntity(entity_type)
            if (entity_type == CatalogueContextEntity.RELEASE_TRACK) != (release_id is not None):
                raise ValueError("invalid release")
        except ValueError as error:
            raise MusicError(
                "music_catalogue_context_invalid", "Check the catalogue selection.", 422
            ) from error
        with self.sessions() as session:
            now = self._now(session)
            count = session.scalar(
                select(func.count())
                .select_from(InternetCatalogueContextRow)
                .where(
                    InternetCatalogueContextRow.user_id == principal.user_id,
                    InternetCatalogueContextRow.observed_at > now - timedelta(minutes=1),
                )
            )
            if count is not None and count >= 15:
                raise MusicError(
                    "music_catalogue_busy",
                    "Please wait before selecting again.",
                    429,
                    retryable=True,
                )
        try:
            with self.http_factory(principal) as http:
                card = MusicBrainzCatalogueContextProvider(http).hydrate(
                    entity_type, entity_id, release_id
                )
        except MetadataProviderError as error:
            if error.code == "metadata_entity_missing":
                raise MusicError(
                    "music_catalogue_entity_not_found",
                    "The catalogue selection is unavailable.",
                    404,
                ) from error
            raise MusicError(
                "music_catalogue_busy"
                if error.code == "metadata_provider_busy"
                else "music_catalogue_unavailable",
                "The catalogue is temporarily unavailable.",
                503,
                retryable=error.retryable,
            ) from error
        with self.sessions.begin() as session:
            lock_resource_admission(session)
            now = self._now(session)
            try:
                ResourceAuthorityGate(session, automatic_acquisition_enabled=False).authenticate(
                    principal, now, lock_rows=False
                )
            except ResourceAdmissionError as error:
                raise MusicError("auth_invalid", "Authentication is unavailable.", 401) from error
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            context = PostgresCatalogueContextRepository(session).create(
                principal.user_id, uuid4(), card, now
            )
            return {
                "contract_version": "music-catalogue-context-v1",
                "catalogue_context_id": str(context.context_id),
                "expires_at": (now + timedelta(hours=24)).isoformat(),
                "source": "MusicBrainz",
                "availability": "METADATA_ONLY",
                "acquisition_allowed": False,
                "lookup_metadata": context.card.document(),
            }

    def track_reference(self, principal: Principal, context_id: UUID) -> dict[str, object]:
        """Materialize the owner's playlist target from a verified metadata receipt."""
        with self.sessions.begin() as session:
            _acquire_sync_owner_publish_lock(session, principal.user_id)
            context = PostgresCatalogueContextRepository(session).load(
                principal.user_id, context_id, admission=True
            )
            card = context.card
            external = musicbrainz_reference(session, "RECORDING", str(card.recording_mbid))
            linked = select(UserTrackRefExternalReferenceRow.user_track_ref_id).where(
                UserTrackRefExternalReferenceRow.external_reference_id
                == external.external_reference_id
            )
            identity: ColumnElement[bool] = UserTrackRefRow.user_track_ref_id.in_(linked)
            if external.recording_id is not None:
                identity = or_(identity, UserTrackRefRow.recording_id == external.recording_id)
            ref = session.scalar(
                select(UserTrackRefRow)
                .where(
                    UserTrackRefRow.user_id == principal.user_id,
                    UserTrackRefRow.deleted_at.is_(None),
                    identity,
                )
                .order_by(UserTrackRefRow.user_track_ref_id)
                .limit(1)
            )
            if ref is None:
                ref = UserTrackRefRow(
                    user_id=principal.user_id,
                    raw_title=card.recording_title or card.title,
                    raw_artist=card.artist,
                    raw_album=card.album,
                    raw_duration_ms=card.duration_ms,
                )
                session.add(ref)
                session.flush()
                session.add(
                    UserTrackRefExternalReferenceRow(
                        user_track_ref_id=ref.user_track_ref_id,
                        external_reference_id=external.external_reference_id,
                        relation_role="IMPORT_EVIDENCE",
                    )
                )
                session.add(
                    SyncEventRow(
                        user_id=principal.user_id,
                        event_type="USER_TRACK_REF_PATCHED",
                        aggregate_type="USER_TRACK_REF",
                        aggregate_id=ref.user_track_ref_id,
                        schema_version=1,
                        server_row_version=ref.row_version,
                        payload={
                            "title": ref.raw_title,
                            "artist": ref.raw_artist,
                            "album": ref.raw_album,
                            "duration_ms": ref.raw_duration_ms,
                            "resolution_status": ref.resolution_status,
                        },
                    )
                )
            from autplay.adapters.postgresql.shared_catalog import playlist_catalog_handles

            session.flush()
            handle = playlist_catalog_handles(session, principal.user_id).get(
                ref.user_track_ref_id, {}
            )
            return {
                "user_track_ref_id": str(ref.user_track_ref_id),
                "row_version": ref.row_version,
                "recording_id": str(ref.recording_id) if ref.recording_id else None,
                "title": ref.raw_title or card.title,
                "artist": ref.raw_artist,
                "album": ref.raw_album,
                "duration_ms": ref.raw_duration_ms,
                "resolution_status": ref.resolution_status,
                **handle,
            }

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise MusicError("music_catalogue_unavailable", "The catalogue is unavailable.", 503)
        return now.astimezone(UTC)
