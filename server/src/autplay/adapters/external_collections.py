"""Read provider metadata through fixed API origins, without following provider-supplied URLs."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import requests

from autplay.domain.external_collections import (
    CollectionError,
    CollectionLink,
    CollectionPage,
    SourceTrack,
    parse_collection_link,
)

ORIGINS = {
    "yandex": "https://api.music.yandex.net",
    "spotify": "https://api.spotify.com",
    "soundcloud": "https://api.soundcloud.com",
}


def resolve_link(value: str) -> CollectionLink:
    short_hosts = {"spotify.link", "on.soundcloud.com", "music.yandex.link"}
    for _ in range(4):
        try:
            return parse_collection_link(value)
        except CollectionError:
            try:
                url = urlsplit(value)
            except ValueError as error:
                raise CollectionError("unsupported_link") from error
            if (
                url.scheme != "https"
                or url.netloc.lower() not in short_hosts
                or not re.fullmatch(r"/[A-Za-z0-9_-]{1,160}", url.path)
            ):
                raise CollectionError("unsupported_link") from None
        try:
            with requests.head(
                "https://" + url.netloc.lower() + url.path, timeout=(3, 5), allow_redirects=False
            ) as response:
                if response.status_code not in (301, 302, 303, 307, 308):
                    raise CollectionError("unsupported_link")
                value = urljoin(value, response.headers.get("location", ""))
        except requests.RequestException as error:
            raise CollectionError("source_unavailable", 503) from error
    raise CollectionError("unsupported_link")


def retry_delay(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,8}", value):
        return max(1, int(value))
    try:
        until = parsedate_to_datetime(value)
        return max(1, ceil((until - datetime.now(UTC)).total_seconds()))
    except ValueError, TypeError, OverflowError:
        return 60


def read_metadata(provider: str, path: str, token: str | None) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = ("Bearer " if provider == "spotify" else "OAuth ") + token
    try:
        with requests.get(
            ORIGINS[provider] + path,
            headers=headers,
            timeout=(4, 12),
            allow_redirects=False,
            stream=True,
        ) as response:
            if (
                provider == "soundcloud"
                and path.startswith("/resolve?")
                and response.status_code == 302
            ):
                target = urlsplit(response.headers.get("location", ""))
                resolved = unquote(target.path)
                if (
                    target.scheme != "https"
                    or target.netloc != "api.soundcloud.com"
                    or target.query
                    or not re.fullmatch(
                        r"/(?:playlists|users)/(?:soundcloud:(?:users|playlists):)?[0-9]+", resolved
                    )
                ):
                    raise CollectionError("invalid_source_redirect", 502)
                return read_metadata(provider, resolved, token)
            if response.status_code == 429:
                delay = response.headers.get("Retry-After", "")
                raise CollectionError("rate_limited", 429, retry_delay(delay))
            if response.status_code in {401, 403}:
                raise CollectionError("source_access_required", 403)
            if response.status_code == 404:
                raise CollectionError("source_not_found", 404)
            if response.status_code != 200:
                raise CollectionError("source_unavailable", 503)
            content = bytearray()
            for chunk in response.iter_content(65536):
                if len(content) + len(chunk) > 32 * 1024 * 1024:
                    raise CollectionError("collection_too_large", 413)
                content.extend(chunk)
            return json.loads(content)
    except requests.RequestException as error:
        raise CollectionError("source_unavailable", 503) from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CollectionError("invalid_source_response", 502) from error


def document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CollectionError("invalid_source_response", 502)
    return value


def label(value: object, limit: int = 500) -> str | None:
    if isinstance(value, str):
        return value.replace("\0", "").strip()[:limit] or None
    return None


def identifier(value: object) -> str | None:
    if type(value) not in (int, str):
        return None
    value = str(value)
    return value if re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", value) and value != "None" else None


def number(value: object, minimum: int = 0) -> int | None:
    return value if type(value) is int and minimum <= value < 2**31 else None


def timestamp(value: object) -> str | None:
    if isinstance(value, str) and len(value) <= 50:
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return None


def artists(items: Any) -> str | None:
    return label(", ".join(v for i in (items or []) if (v := label(i.get("name")))))


def yandex_track(
    item: dict[str, Any],
    album: dict[str, Any] | None = None,
    disc: int | None = None,
    index: int | None = None,
) -> SourceTrack:
    track = item.get("track") or item
    albums = track.get("albums") or []
    album_id = identifier(item.get("albumId"))
    edition = album or next((a for a in albums if identifier(a.get("id")) == album_id), None)
    if edition is None:
        edition = albums[0] if len(albums) == 1 and not album_id else {}
    pos = edition.get("trackPosition") or {}
    explicit = track.get("contentWarning")
    aliases = [
        track.get("realId"),
        *(d.get("id") for d in (track.get("duplicates") or []) if isinstance(d, dict)),
    ]
    return SourceTrack(
        identifier(track.get("id") or item.get("id")),
        label(track.get("title")),
        artists(track.get("artists")),
        number(track.get("durationMs"), 1),
        label(edition.get("title")),
        identifier(edition.get("id")) or album_id,
        artists(edition.get("artists")),
        disc or number(pos.get("volume"), 1),
        index or number(pos.get("index"), 1),
        label(track.get("version")),
        True if explicit == "explicit" else None,
        label(track.get("isrc"), 32),
        timestamp(item.get("timestamp")),
        track.get("available") is False or track.get("error") == "no-rights",
        tuple(dict.fromkeys(v for a in aliases if (v := identifier(a)))),
    )


class CollectionReader:
    def __init__(self, fetch: Callable[[str, str, str | None], Any] = read_metadata) -> None:
        self.fetch = fetch

    def check_spotify_access(self, link: CollectionLink, token: str, revision: str | None) -> None:
        if link.kind == "LIKES":
            self.fetch("spotify", "/v1/me/tracks?limit=1", token)
        else:
            header = self._spotify_playlist(link, token)
            if revision is not None and header.get("snapshot_id") != revision:
                raise CollectionError("stale_preview", 409)
            self.fetch("spotify", f"/v1/playlists/{link.key}/items?limit=1", token)

    def page(
        self, link: CollectionLink, favorites: bool, cursor: str | None, token: str | None = None
    ) -> CollectionPage:
        try:
            if link.provider == "yandex":
                return self._yandex(link, favorites, token)
            if token is None:
                raise CollectionError("source_access_required", 403)
            if link.provider == "spotify":
                return self._spotify(link, cursor, token)
            return self._soundcloud(link, cursor, token)
        except (AttributeError, KeyError, TypeError, IndexError) as error:
            raise CollectionError("invalid_source_response", 502) from error

    def _yandex(self, link: CollectionLink, favorites: bool, token: str | None) -> CollectionPage:
        def read(path: str) -> dict[str, Any]:
            return document(self.fetch("yandex", path, token)["result"])

        if link.kind == "ALBUM":
            album = read(f"/albums/{link.key}/with-tracks")
            tracks = [
                yandex_track(t or {}, album, d, i)
                for d, volume in enumerate(album["volumes"], 1)
                for i, t in enumerate(volume, 1)
            ]
            return CollectionPage(
                label(album["title"], 120) or link.key, number(album.get("trackCount")), tracks
            )
        if link.key == "me":
            if not token:
                raise CollectionError("source_access_required", 403)
            uid = identifier(read("/account/status")["account"]["uid"])
            if not uid:
                raise CollectionError("source_access_required", 403)
            playlist = read(f"/users/{uid}/playlists/3")
        elif "/" in link.key:
            user, kind = link.key.split("/")
            playlist = read(f"/users/{user}/playlists/{kind}")
        else:
            playlist = read(f"/playlist/{link.key}")
        tracks = [yandex_track(item or {}) for item in playlist["tracks"]]
        expected = number(playlist.get("trackCount"))
        page = CollectionPage(
            label(playlist["title"], 120) or link.key,
            expected,
            tracks,
            source_revision=identifier(playlist.get("revision")),
        )
        if favorites and str(playlist.get("kind")) == "3":
            owner_id = identifier((playlist.get("owner") or {}).get("uid") or playlist.get("uid"))
            if owner_id:
                marks = read(f"/users/{owner_id}/likes/tracks")["library"]["tracks"]
                by_id = {identifier(m["id"]): m for m in marks}
                known = {t.track_id for t in tracks}
                page.tracks = [
                    replace(
                        t,
                        liked_at=timestamp(by_id.get(t.track_id, {}).get("timestamp"))
                        or t.liked_at,
                    )
                    for t in tracks
                ]
                page.tracks.extend(
                    yandex_track(m) for k, m in by_id.items() if k and k not in known
                )
                page.likes_count = len(marks)
        return page

    def _spotify_playlist(self, link: CollectionLink, token: str) -> dict[str, Any]:
        header = document(
            self.fetch(
                "spotify",
                f"/v1/playlists/{link.key}?fields=name,snapshot_id,owner(id),collaborative",
                token,
            )
        )
        owner = document(header.get("owner")).get("id")
        user = document(self.fetch("spotify", "/v1/me", token)).get("id")
        # Development Mode's items endpoint checks that the caller actually collaborates.
        delegated = (
            os.getenv("AUTPLAY_IMPORT_SPOTIFY_QUOTA_MODE") == "development"
            and header.get("collaborative") is True
        )
        if not owner or not user or (owner != user and not delegated):
            raise CollectionError("playlist_ownership_required", 403)
        return header

    def _spotify(self, link: CollectionLink, cursor: str | None, token: str) -> CollectionPage:
        def read(path: str) -> dict[str, Any]:
            return document(self.fetch("spotify", "/v1" + path, token))

        offset = int(cursor or 0)
        if link.kind == "ALBUM":
            # Album metadata is looked up independently in the AutPlay catalog.
            raise CollectionError("catalog_album_lookup_required", 422)
        name, revision, endpoint = "Liked Songs", None, "/me/tracks"
        if link.kind == "PLAYLIST":
            header = self._spotify_playlist(link, token)
            name = label(header["name"], 120) or link.key
            revision = identifier(header["snapshot_id"]) or label(header["snapshot_id"], 128)
            endpoint = f"/playlists/{link.key}/items"
        data = read(endpoint + f"?limit=50&offset={offset}")
        tracks = []
        for row in data["items"]:
            t = (row or {}).get("item") or (row or {}).get("track") or {}
            if t.get("type") != "track":
                t = {}
            a = t.get("album") or {}
            tracks.append(
                SourceTrack(
                    identifier(t.get("id")),
                    label(t.get("name")),
                    artists(t.get("artists")),
                    number(t.get("duration_ms"), 1),
                    label(a.get("name")),
                    identifier(a.get("id")),
                    artists(a.get("artists")),
                    number(t.get("disc_number"), 1),
                    number(t.get("track_number"), 1),
                    explicit=t.get("explicit"),
                    isrc=label((t.get("external_ids") or {}).get("isrc"), 32),
                    liked_at=timestamp((row or {}).get("added_at"))
                    if link.kind == "LIKES"
                    else None,
                    unavailable=not t or t.get("is_playable") is False,
                )
            )
        if data.get("next") and not tracks:
            raise CollectionError("invalid_source_response", 502)
        return CollectionPage(
            name,
            number(data.get("total")),
            tracks,
            str(offset + len(tracks)) if data.get("next") else None,
            revision,
        )

    def _soundcloud(self, link: CollectionLink, cursor: str | None, token: str) -> CollectionPage:
        def read(path: str) -> Any:
            return self.fetch("soundcloud", path, token)

        obj = read("/resolve?" + urlencode({"url": "https://soundcloud.com/" + link.key}))
        if link.kind == "SET":
            tracks = [self._sc_track(t) for t in obj["tracks"]]
            return CollectionPage(
                label(obj["title"], 120) or link.key,
                number(obj.get("track_count")),
                tracks,
                kind="ALBUM"
                if obj.get("is_album")
                or (obj.get("set_type") or obj.get("playlist_type") or obj.get("type")) == "album"
                else "PLAYLIST",
                release_key=identifier(obj.get("urn") or obj.get("id")),
            )
        owner = identifier(obj.get("urn") or obj.get("id"))
        if not owner:
            raise CollectionError("invalid_source_response", 502)
        phase, _, paging = (cursor or "tracks").partition(":")
        path = f"/users/{owner}/likes/{phase}"
        data = read(
            path
            + "?"
            + urlencode(
                {
                    "limit": 200,
                    "linked_partitioning": "true",
                    **({"cursor": paging} if paging else {}),
                }
            )
        )
        next_cursor = "playlists" if phase == "tracks" else None
        if data.get("next_href"):
            url = urlsplit(data["next_href"])
            value = parse_qs(url.query).get("cursor", [""])[0]
            if (
                url.netloc != "api.soundcloud.com"
                or unquote(url.path) != path
                or not re.fullmatch(r"[A-Za-z0-9=+/_-]{1,512}", value)
            ):
                raise CollectionError("invalid_source_cursor", 502)
            next_cursor = phase + ":" + value
        tracks = (
            [self._sc_track(item.get("track") or item) for item in data["collection"]]
            if phase == "tracks"
            else []
        )
        sets = [
            (public_set_url(s.get("permalink_url")), label(s.get("title")) or "")
            for item in data["collection"]
            if phase == "playlists"
            for s in [item.get("playlist") or item]
        ]
        return CollectionPage(
            label(obj["username"], 120) or link.key,
            number(obj.get("public_favorites_count", obj.get("likes_count"))),
            tracks,
            next_cursor,
            sets=sets,
            expected_sets=number(obj.get("playlist_likes_count")),
        )

    @staticmethod
    def _sc_track(t: dict[str, Any]) -> SourceTrack:
        return SourceTrack(
            identifier(t.get("urn") or t.get("id")),
            label(t.get("title")),
            label(t.get("metadata_artist")) or label((t.get("user") or {}).get("username")),
            number(t.get("duration"), 1),
        )


def public_set_url(value: object) -> str:
    if isinstance(value, str):
        try:
            link = parse_collection_link(value)
            if link.provider == "soundcloud" and link.kind == "SET":
                return "https://soundcloud.com/" + link.key
        except CollectionError:
            pass
    return ""
