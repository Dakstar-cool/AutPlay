"""Owner context receipts survive admission expiry through their original search link."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

import rfc8785
from sqlalchemy.orm import Session

from autplay.application.music_library import MusicError
from autplay.domain.catalogue_context import (
    MAX_CATALOGUE_CONTEXT_BYTES,
    CatalogueLookupContext,
    CatalogueTrackCard,
    parse_catalogue_card,
)

from .models.catalogue_context import InternetCatalogueContextRow, InternetSearchContextRow
from .models.internet_music import InternetAcquisitionRow, InternetSearchRow


def request_digest(query: str, context: CatalogueLookupContext | None) -> bytes:
    return hashlib.sha256(
        rfc8785.dumps(
            {
                "query": query,
                "catalogue_context_id": str(context.context_id) if context else None,
                "catalogue_context_sha256": context.context_sha256.hex() if context else None,
            }
        )
    ).digest()


class PostgresCatalogueContextRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        user_id: UUID,
        context_id: UUID,
        card: CatalogueTrackCard,
        observed_at: datetime,
    ) -> CatalogueLookupContext:
        document = card.document()
        payload = rfc8785.dumps(document)
        if len(payload) > MAX_CATALOGUE_CONTEXT_BYTES:
            raise MusicError("music_catalogue_context_invalid", "The selection is too large.", 422)
        digest = hashlib.sha256(payload).digest()
        self.session.add(
            InternetCatalogueContextRow(
                context_id=context_id,
                user_id=user_id,
                card=document,
                context_sha256=digest,
                observed_at=observed_at,
                expires_at=observed_at + timedelta(hours=24),
            )
        )
        self.session.flush()
        return CatalogueLookupContext(context_id, card, observed_at, digest)

    def load(
        self,
        user_id: UUID,
        context_id: UUID,
        *,
        admission: bool,
        now: datetime | None = None,
    ) -> CatalogueLookupContext:
        row = self.session.get(InternetCatalogueContextRow, context_id)
        if row is None or row.user_id != user_id:
            raise MusicError(
                "music_catalogue_context_not_found", "The catalogue selection is unavailable.", 404
            )
        if admission and row.expires_at <= (now or datetime.now(UTC)):
            raise MusicError(
                "music_catalogue_context_expired", "Select the catalogue track again.", 409
            )
        try:
            card = parse_catalogue_card(row.card)
            payload = rfc8785.dumps(card.document())
            if len(payload) > MAX_CATALOGUE_CONTEXT_BYTES or not hmac.compare_digest(
                row.context_sha256, hashlib.sha256(payload).digest()
            ):
                raise ValueError("invalid receipt digest")
        except (ValueError, TypeError) as error:
            raise MusicError(
                "music_catalogue_context_unavailable",
                "The catalogue selection is unavailable.",
                503,
            ) from error
        return CatalogueLookupContext(row.context_id, card, row.observed_at, row.context_sha256)

    def replay(
        self,
        user_id: UUID,
        search_id: UUID,
        query: str,
        context_id: UUID | None,
    ) -> None:
        link = self.session.get(InternetSearchContextRow, search_id)
        if link is None:
            # Historical searches have immutable no-context semantics.
            if context_id is not None:
                raise MusicError("music_operation_conflict", "The operation conflicts.", 409)
            return
        if link.user_id != user_id or link.context_id != context_id:
            raise MusicError("music_operation_conflict", "The operation conflicts.", 409)
        context = self.load(user_id, context_id, admission=False) if context_id else None
        if not hmac.compare_digest(link.request_sha256, request_digest(query, context)):
            raise MusicError("music_operation_conflict", "The operation conflicts.", 409)

    def bind_search(
        self,
        user_id: UUID,
        search_id: UUID,
        query: str,
        context: CatalogueLookupContext | None,
    ) -> None:
        self.session.add(
            InternetSearchContextRow(
                search_id=search_id,
                user_id=user_id,
                context_id=context.context_id if context else None,
                request_sha256=request_digest(query, context),
            )
        )
        self.session.flush()

    def for_acquisition(self, user_id: UUID, acquisition_id: UUID) -> CatalogueLookupContext | None:
        source = self.session.get(InternetAcquisitionRow, acquisition_id)
        if source is None or source.user_id != user_id:
            return None
        link = self.session.get(InternetSearchContextRow, source.search_id)
        if link is None or link.user_id != user_id or link.context_id is None:
            return None
        # The original acquisition association is immutable; reused READY/active
        # provider candidates keep this original search context without reassociation.
        context = self.load(user_id, link.context_id, admission=False)
        search = self.session.get(InternetSearchRow, source.search_id)
        if (
            search is None
            or search.user_id != user_id
            or not hmac.compare_digest(link.request_sha256, request_digest(search.query, context))
        ):
            raise MusicError(
                "music_catalogue_context_unavailable",
                "The catalogue selection is unavailable.",
                503,
            )
        return context
