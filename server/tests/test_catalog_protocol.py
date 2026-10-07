"""The catalog child admits one fixed-host GET and bounds replies before reading them."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from autplay.adapters.filesystem import catalog_child
from autplay.adapters.filesystem.catalog_protocol import read_catalog_packet
from autplay.adapters.filesystem.vault_child import ChildProtocolError, encode_document
from autplay.ports.track_metadata import MetadataProviderError


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/ws/2/artist",
        "https://musicbrainz.org/ws/2/annotation",
        "https://musicbrainz.org/login",
    ],
)
def test_wrong_host_or_endpoint_rejected_before_http(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("ungranted client construction")

    monkeypatch.setattr(catalog_child, "PublicMetadataHttp", forbidden)
    with pytest.raises(MetadataProviderError, match="metadata_url_rejected"):
        catalog_child.execute(
            {"version": 1, "execution_id": str(uuid4()), "request_id": str(uuid4()), "url": url},
            BytesIO(),
        )


def test_second_network_request_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class Http:
        def __init__(
            self, gate: Callable[[], AbstractContextManager[Any]], *, proxy: str | None
        ) -> None:
            self.gate, self.client = gate, self

        def get(self, url: str) -> bytes:
            with self.gate():
                calls.append(1)
            with self.gate():
                calls.append(2)
            return b"{}"

        def close(self) -> None:
            pass

    monkeypatch.setattr(catalog_child, "PublicMetadataHttp", Http)
    with pytest.raises(ChildProtocolError):
        catalog_child.execute(
            {
                "version": 1,
                "execution_id": str(uuid4()),
                "request_id": str(uuid4()),
                "url": "https://musicbrainz.org/ws/2/artist",
            },
            BytesIO(),
        )
    assert calls == [1]


@pytest.mark.parametrize("size", [-1, True, 1048577, "12"])
def test_catalog_bound_rejected_before_payload_read(size: object) -> None:
    with pytest.raises(ChildProtocolError):
        read_catalog_packet(BytesIO(), encode_document({"document": {}, "size": size}))
