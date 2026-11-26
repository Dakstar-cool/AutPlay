"""Canonical provider collection handles and bounded, metadata-only snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

from autplay.domain.import_identity import MAX_IMPORT_ROWS

YANDEX_HOSTS = {"music.yandex.ru", "music.yandex.com", "music.yandex.kz", "music.yandex.by"}


class CollectionError(ValueError):
    def __init__(self, code: str, status: int = 422, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


@dataclass(frozen=True)
class CollectionLink:
    provider: str
    kind: str
    key: str

    def action(self, favorites: bool) -> str:
        if self.kind == "LIKES" and not favorites:
            raise CollectionError("choose_favorites")
        if self.kind == "ALBUM" and favorites:
            raise CollectionError("album_is_not_favorites")
        return "FAVORITES" if favorites else self.kind


def parse_collection_link(value: str) -> CollectionLink:
    if len(value) > 2048 or any(ord(c) < 33 or c == "\\" for c in value):
        raise CollectionError("unsupported_link")
    try:
        url = urlsplit(value)
    except ValueError as error:
        raise CollectionError("unsupported_link") from error
    if url.scheme != "https":
        raise CollectionError("unsupported_link")
    host, path = url.netloc.lower(), url.path.rstrip("/")
    if any(part in {".", ".."} for part in path.split("/")):
        raise CollectionError("unsupported_link")
    if host in YANDEX_HOSTS:
        if path in {"/collection", "/collection/tracks"}:
            return CollectionLink("yandex", "LIKES", "me")
        if match := re.fullmatch(r"/album/([0-9]{1,19})", path):
            return CollectionLink("yandex", "ALBUM", match[1])
        if match := re.fullmatch(r"/users/([A-Za-z0-9.@_-]{1,100})/playlists/([0-9]{1,19})", path):
            return CollectionLink("yandex", "PLAYLIST", f"{match[1]}/{match[2]}")
        if match := re.fullmatch(r"/playlists/((?:lk\.|ch\.)?)([0-9a-fA-F-]{36})", path):
            try:
                return CollectionLink("yandex", "PLAYLIST", match[1] + str(UUID(match[2])))
            except ValueError:
                pass
        if match := re.fullmatch(r"/users/([A-Za-z0-9.@_-]{1,100})/likes", path):
            return CollectionLink("yandex", "LIKES", f"{match[1]}/3")
    if host == "open.spotify.com":
        path = re.sub(r"^/intl-[a-zA-Z-]+/", "/", path)
        if path == "/collection/tracks":
            return CollectionLink("spotify", "LIKES", "me")
        if match := re.fullmatch(r"/(album|playlist)/([a-zA-Z0-9]{22})", path):
            return CollectionLink("spotify", match[1].upper(), match[2])
    if host in {"soundcloud.com", "www.soundcloud.com"}:
        if match := re.fullmatch(r"/([a-zA-Z0-9_-]+/sets/[a-zA-Z0-9_-]+)", path):
            return CollectionLink("soundcloud", "SET", match[1])
        if match := re.fullmatch(r"/([a-zA-Z0-9_-]+)(?:/likes)?", path):
            return CollectionLink("soundcloud", "LIKES", match[1])
    raise CollectionError("unsupported_link")


@dataclass(frozen=True)
class SourceTrack:
    track_id: str | None
    title: str | None
    artist: str | None
    duration_ms: int | None = None
    album: str | None = None
    album_id: str | None = None
    album_artist: str | None = None
    disc: int | None = None
    number: int | None = None
    version: str | None = None
    explicit: bool | None = None
    isrc: str | None = None
    liked_at: str | None = None
    unavailable: bool = False
    aliases: tuple[str, ...] = ()


@dataclass
class CollectionPage:
    name: str
    expected: int | None
    tracks: list[SourceTrack]
    next_cursor: str | None = None
    source_revision: str | None = None
    sets: list[tuple[str, str]] = field(default_factory=list)
    likes_count: int | None = None
    expected_sets: int | None = None
    kind: str | None = None
    release_key: str | None = None

    def __post_init__(self) -> None:
        if len(self.tracks) + len(self.sets) > MAX_IMPORT_ROWS:
            raise CollectionError("collection_too_large", 413)
