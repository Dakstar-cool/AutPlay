"""Create private immutable receipts from server-hydrated catalogue entities."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.catalogue_context import PostgresCatalogueContextRepository
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

    @staticmethod
    def _now(session: Session) -> datetime:
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise MusicError("music_catalogue_unavailable", "The catalogue is unavailable.", 503)
        return now.astimezone(UTC)
