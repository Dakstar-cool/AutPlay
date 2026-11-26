"""One contained MusicBrainz GET after the parent commits an exact durable grant."""

import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from autplay.adapters.public_track_metadata import PublicMetadataHttp, is_cover_url
from autplay.domain.catalog_artwork import catalogue_api_url
from autplay.ports.track_metadata import MetadataProviderError

from .metadata_protocol import write_packet
from .vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)


def execute(command: dict[str, object], destination: BinaryIO) -> None:
    if (
        set(command) != {"version", "execution_id", "request_id", "url"}
        or type(command["version"]) is not int
        or command["version"] != 1
    ):
        raise ChildProtocolError()
    for key in ("execution_id", "request_id"):
        identity = command[key]
        if not isinstance(identity, str):
            raise ChildProtocolError()
        UUID(identity)
    url = command["url"]
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        raise ChildProtocolError()
    parsed = urlsplit(url)
    artist_ranking = (
        parsed.hostname == "api.listenbrainz.org"
        and not parsed.query
        and re.fullmatch(r"/1/popularity/top-recordings-for-artist/[0-9a-f-]{36}", parsed.path)
    )
    metadata = parsed.hostname == "musicbrainz.org" and re.fullmatch(
        r"/ws/2/(?:artist|recording|release|(?:artist|recording|release)/[0-9a-fA-F-]{36})",
        parsed.path,
    )
    cover = is_cover_url(url)
    if not (metadata or artist_ranking or cover or catalogue_api_url(url)):
        raise MetadataProviderError("metadata_url_rejected", retryable=False)
    sent = False

    @contextmanager
    def single_grant() -> Iterator[None]:
        nonlocal sent
        if sent:
            raise ChildProtocolError()
        sent = True
        yield

    http = PublicMetadataHttp(single_grant, proxy=os.environ.get("AUTPLAY_METADATA_PROXY"))
    try:
        if parsed.netloc == "api.discogs.com" and parsed.path == "/database/search":
            token = os.environ.get("AUTPLAY_DISCOGS_TOKEN")
            if not token:
                raise MetadataProviderError("discogs_search_unconfigured", retryable=False)
            # This child handles one validated GET and never follows metadata redirects.
            if token.startswith("{"):
                pair = json.loads(token)
                if (
                    not isinstance(pair, dict)
                    or set(pair) != {"consumer_key", "consumer_secret"}
                    or not all(
                        isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{10,128}", v)
                        for v in pair.values()
                    )
                ):
                    raise MetadataProviderError("metadata_discogs_auth_invalid", retryable=False)
                credential = f"key={pair['consumer_key']}, secret={pair['consumer_secret']}"
            else:
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token):
                    raise MetadataProviderError("metadata_discogs_auth_invalid", retryable=False)
                credential = f"token={token}"
            http.client.headers["Authorization"] = "Discogs " + credential
        payload = http.cover_hop(url) if cover else http.get(url)
        write_packet(destination, b"R", {}, payload)
    finally:
        http.client.close()


def main() -> int:
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    try:
        write_frame(destination, b"H", encode_document({"pid": os.getpid(), "nonce": uuid4().hex}))
        tag, payload = read_frame(source, maximum=16384)
        if tag != b"G":
            raise ChildProtocolError()
        execute(decode_document(payload), destination)
        return 0
    except MetadataProviderError as error:
        write_packet(
            destination,
            b"E",
            {
                "code": error.code,
                "retryable": error.retryable,
                "retry_after_seconds": error.retry_after_seconds,
            },
        )
        return 0
    except ValueError, TypeError, KeyError, OverflowError, OSError:
        with suppress(OSError, ValueError):
            write_packet(
                destination,
                b"E",
                {
                    "code": "metadata_child_failed",
                    "retryable": True,
                    "retry_after_seconds": 60,
                },
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
