"""Hydration always runs through an explicitly supplied principal-bound provider."""

from typing import Protocol
from uuid import UUID

from autplay.domain.catalogue_context import CatalogueContextEntity, CatalogueTrackCard


class CatalogueContextProvider(Protocol):
    def hydrate(
        self, entity_type: CatalogueContextEntity, entity_id: UUID, release_id: UUID | None
    ) -> CatalogueTrackCard: ...
