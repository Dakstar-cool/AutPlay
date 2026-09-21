"""Bounded search metadata only; downloaded candidates retain their own identities."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from dataclasses import asdict
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.extractor.bandcamp import BandcampIE
from yt_dlp.extractor.soundcloud import SoundcloudIE, SoundcloudSearchIE
from yt_dlp.globals import plugin_dirs
from yt_dlp.utils import DownloadError

from ..models import PlaylistItem
from ..related import RelatedCandidate, candidate_score, identity_fields
from ..source_catalog import track_url
from ..source_client import valid_client_id
from . import _music_sites_worker as music
from ._proxy_transport import socks_transport
from ._yt_dlp_worker import _options


def discover(request: dict[str, Any]) -> dict[str, Any]:
    provider, artist, title = (request.get(k) for k in ("provider", "artist", "title"))
    if provider not in {"yt_dlp", "soundcloud", "bandcamp"} or any(
        not isinstance(v, str) or not v.strip() or len(v) > 500 for v in (artist, title)
    ):
        return {"status": "failed", "code": "request_invalid"}
    assert isinstance(artist, str) and isinstance(title, str) and isinstance(provider, str)
    item = PlaylistItem(1, artist, title)
    options = _options(request.get("proxy_url")) | {
        "extract_flat": "in_playlist",
        "playlistend": 20,
        "skip_download": True,
    }
    candidates: list[RelatedCandidate] = []
    with socks_transport(request.get("proxy_url")), tempfile.TemporaryDirectory() as temporary:
        options["cachedir"] = temporary
        with YoutubeDL(options, auto_init=provider == "yt_dlp") as ydl:
            if provider != "yt_dlp":
                for extractor in (
                    (SoundcloudIE, SoundcloudSearchIE)
                    if provider == "soundcloud"
                    else (BandcampIE,)
                ):
                    ydl.add_info_extractor(extractor())
            client_id = request.get("soundcloud_client_id")
            if client_id is not None:
                if not valid_client_id(client_id):
                    return {"status": "failed", "code": "client_id_invalid"}
                ydl.cache.store("soundcloud", "client_id", client_id)
            entries: Any
            if provider == "bandcamp":
                entries = music._bandcamp_search(artist, title, request.get("proxy_url"))
            else:
                prefix = "ytsearch" if provider == "yt_dlp" else "scsearch"
                result = ydl.extract_info(f"{prefix}20:{artist} - {title}", download=False)
                entries = result.get("entries") if isinstance(result, dict) else None
                if not isinstance(entries, list):
                    return {"status": "failed", "code": "search_response_invalid"}
            hydrated = 0
            for raw in entries[:20]:
                if not isinstance(raw, dict):
                    continue
                identity = identity_fields(raw)
                if identity is None and hydrated < 3:
                    hint_artist, hint_title = raw.get("uploader"), raw.get("title")
                    if isinstance(hint_artist, str) and isinstance(hint_title, str):
                        hint = RelatedCandidate(provider, hint_artist, hint_title)
                        if candidate_score(item, hint) > 0:
                            url = raw.get("webpage_url")
                            if provider == "yt_dlp":
                                url = track_url(
                                    f"https://www.youtube.com/watch?v={raw.get('id')}", provider
                                )
                            else:
                                url = track_url(url, provider)
                            if url is not None:
                                hydrated += 1
                                detail = ydl.extract_info(url, download=False)
                                if isinstance(detail, dict):
                                    identity = identity_fields(detail)
                                    raw = detail
                if identity is None:
                    continue
                duration = raw.get("duration")
                if (
                    not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or not math.isfinite(duration)
                    or not 0 < duration <= 86400
                ):
                    duration = None
                candidates.append(RelatedCandidate(provider, *identity, duration))
    return {"status": "discovered", "candidates": [asdict(c) for c in candidates]}


def main() -> int:
    plugin_dirs.value = []
    try:
        raw = sys.stdin.read(32769)
        request = json.loads(raw)
        if len(raw) > 32768 or not isinstance(request, dict):
            raise ValueError("request_invalid")
        response = discover(request)
    except music._SourceError as error:
        response = {"status": "failed", "code": error.code}
    except DownloadError as error:
        response = {"status": "failed", "code": music._search_error(error).code}
    except (OSError, ValueError, TypeError):
        response = {"status": "failed", "code": "discovery_failed"}
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
