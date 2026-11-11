"""C1 bounded read transaction port. No persistence or acquisition commands."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from autplay.domain.catalog_view import CatalogViewQuery


class CatalogViewRead(Protocol):
    def read(self, query: CatalogViewQuery) -> dict[str, Any]: ...


CatalogViewTransaction = Callable[[str], AbstractContextManager[CatalogViewRead]]
