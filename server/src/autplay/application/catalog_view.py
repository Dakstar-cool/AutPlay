"""C1 owns a single read transaction and refuses oversized complete replies."""

import json
from collections.abc import Callable

from autplay.domain.catalog_view import MAX_REPLY_BYTES, CatalogViewError, CatalogViewQuery
from autplay.ports.catalog_view import CatalogViewTransaction


class CatalogViewService:
    def __init__(self, transaction: CatalogViewTransaction, check: Callable[[], None]) -> None:
        self._transaction, self._check = transaction, check

    def prepare(self, token: str, query: CatalogViewQuery) -> bytes:
        self._check()
        with self._transaction(token) as reader:
            document = reader.read(query)
            # Incremental serialization refuses capacity before assembling an oversized body.
            body = bytearray()
            for fragment in json.JSONEncoder(
                ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).iterencode(document):
                self._check()
                encoded = fragment.encode("utf-8")
                if len(body) + len(encoded) > MAX_REPLY_BYTES:
                    raise CatalogViewError("catalog_view_capacity")
                body.extend(encoded)
            self._check()
        self._check()  # includes transaction rollback/connection release
        return bytes(body)
