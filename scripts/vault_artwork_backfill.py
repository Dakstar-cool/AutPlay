"""Fill missing Vault covers and performer portraits from corroborated public catalog entries."""

import argparse
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid5

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from autplay.adapters.media.tools import SubprocessExecutableRunner
from autplay.adapters.media.track_metadata import FfmpegMetadataReader
from autplay.adapters.postgresql.models import UserTrackRefRow
from autplay.adapters.postgresql.models.track_metadata import TrackMetadataRow
from autplay.application.catalog_artwork import attach_images, store_image
from autplay.adapters.public_track_metadata import PublicMetadataHttp, retry_after_seconds
from autplay.ports.track_metadata import MetadataProviderError
from autplay.application.sync import _acquire_sync_owner_publish_lock
from autplay.application.track_metadata import TrackMetadataService
from autplay.domain.track_metadata import metadata_search_terms
from autplay.domain.catalog_artwork import ArtMatch, catalogue_api_url, image_url, label, match_artwork, native_image_host, search_url
from autplay.domain.web_artwork import (BANDCAMP_SEARCH, archived_editions, bandcamp_query, discography_url, indexed_publisher_cover, publisher_candidates,
                                        publisher_match, release_lookup_url, yandex_match, yandex_search_url)
from autplay.domain.featured_artists import featured_names
from autplay.domain.track_names import track_names
from autplay.domain.original_artwork import ORIGINAL_BASES, original_artwork_source

_INVENTORY = """
SELECT DISTINCT u.user_track_ref_id AS ref, u.user_id AS owner, u.recording_id,c.audio_variant_id,
       u.raw_title, u.raw_artist, u.raw_album, v.duration_ms,
       m.document->'fields' AS fields, m.artwork_sha256 AS sha, m.candidates,
       m.document->'artwork_source' AS artwork_source, m.document->'musicbrainz_performance_v1' AS performance,
       r.recording_kind, r.version_text,
       m.document->'catalog_artist_image_v1' AS portrait
FROM vault.recording_canonical_variant c JOIN vault.audio_variant v USING(recording_id,audio_variant_id)
JOIN vault.vault_object o USING(vault_object_id)
JOIN catalog.recording r USING(recording_id)
JOIN library.user_track_ref u USING(recording_id) JOIN library.library_entry l USING(user_id,user_track_ref_id)
LEFT JOIN library.track_metadata m USING(user_track_ref_id)
WHERE v.validation_status='VALID' AND v.deleted_at IS NULL AND o.commit_status='COMMITTED'
AND r.deleted_at IS NULL AND u.deleted_at IS NULL AND l.removed_at IS NULL
AND NOT EXISTS (SELECT FROM identity.recording_redirect rr WHERE rr.source_recording_id=c.recording_id)
"""


def names(raw_title, raw_artist, raw_album, fields):
    fields = fields or {}
    title, artist = track_names(raw_title, fields.get("artist", raw_artist), move_featured=False)
    title, artist = fields.get("title", title), fields.get("artist", artist)
    if title and artist:
        title, artist = featured_names(title, artist)
    return title, artist, fields.get("album", raw_album)


def cache_bytes(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as output:
        tmp = Path(output.name)
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class Collector:
    def __init__(self, directory, engine, workers, *, cached_searches=False, offline=False, yandex=False, publisher=False, archive=False):
        self.path, self.engine, self.workers = directory, engine, workers
        self.offline = offline
        self.cached_searches = cached_searches or offline
        self.yandex = yandex
        self.publisher = publisher
        self.archive = archive
        directory.mkdir(exist_ok=True, parents=True)
        self.gate = threading.Lock()
        self.locks = defaultdict(threading.Lock)
        self.cooldown = {}
        self.search_interval = 0.28 if yandex else 0.17
        self.fastest_search = 0.15 if yandex else 0.12
        self.search_successes = 0
        self.next_request = 0.0
        self.next_archive = 0.0
        self.publisher_responses = deque(maxlen=24)
        self.publisher_paused = threading.Event()
        self.retry_items = []
        self.counts = Counter()
        self.local = threading.local()
        self.decoder = FfmpegMetadataReader(SubprocessExecutableRunner())

    def count(self, key, amount=1):
        with self.gate:
            self.counts[key] += amount

    def key_lock(self, key):
        with self.gate:
            return self.locks[key]

    def wait_for_host(self, host, picture):
        while True:
            with self.gate:
                now = time.monotonic()
                ready = self.cooldown.get(host, 0.0)
                if not picture:
                    ready = max(ready, self.next_request)
                if ready <= now:
                    if not picture:
                        self.next_request = now + self.search_interval
                    return
            time.sleep(ready - now)

    def fetch(self, url, *, picture=False):
        allowed = (image_url(url, "cover") or image_url(url, "artist") or native_image_host(url)) if picture else (
            catalogue_api_url(url) or release_lookup_url(url))
        if not allowed:
            raise ValueError("unexpected image provider URL")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.path / (key + (".jpg" if picture else ".json"))
        host = url.split("/", 3)[2]
        with self.key_lock(key):
            if path.exists() and (picture or time.time() - path.stat().st_mtime < 86400 * 7):
                data = path.read_bytes()
                return data if picture else json.loads(data)
            # Earlier imports requested 100 results per recording. Reuse the full response while fresh.
            if not picture and url.endswith("&limit=25"):
                old_key = hashlib.sha256((url[:-2] + "100").encode()).hexdigest()
                old = self.path / (old_key + ".json")
                if old.exists() and time.time() - old.stat().st_mtime < 86400 * 7:
                    return json.loads(old.read_bytes())
            if self.offline and picture:
                return None
            if self.cached_searches and not picture:
                return []
            if not hasattr(self.local, "http"):
                self.local.http = requests.Session()
                self.local.http.headers["User-Agent"] = "AutPlay/1.0 (personal library artwork; https://github.com/Dakstar-cool/AutPlay)"
            for attempt in range(4):
                self.wait_for_host(host, picture)
                try:
                    with self.local.http.get(url, timeout=(4, 9 + 2 * attempt), stream=True, allow_redirects=False) as response:
                        if response.status_code == 404:
                            return None if picture else []
                        if response.status_code == 429 or response.status_code >= 500:
                            delay = retry_after_seconds(response.headers.get("Retry-After") or "5")
                        else:
                            response.raise_for_status()
                            if response.status_code != 200:
                                raise ValueError("invalid catalog response")
                            buf = bytearray()
                            deadline = time.monotonic() + 40
                            for block in response.iter_content(65536):
                                if time.monotonic() > deadline:
                                    raise requests.Timeout("artwork response deadline")
                                buf.extend(block)
                                if len(buf) > (2097152 if picture else 4194304):
                                    raise ValueError("response too large")
                            if picture:
                                catalog_cover = (bool(image_url(url, "cover")) or native_image_host(url) == "BANDCAMP" or
                                                 (native_image_host(url) == "YANDEX" and ".a." in url))
                                result = self.decoder.normalize_artwork(bytes(buf), catalog_cover=catalog_cover)
                            else:
                                value = json.loads(buf)
                                if url.startswith("https://api.music.yandex.net/") and isinstance(value.get("result"), dict):
                                    tracks = value["result"].get("tracks") or []
                                    tracks = tracks.get("results", []) if isinstance(tracks, dict) else tracks
                                    if not isinstance(tracks, list):
                                        raise ValueError("invalid catalog page")
                                    # Retain public matching fields only. Audio locators and account details do not enter the cache.
                                    rows = []
                                    for record in tracks:
                                        if not isinstance(record, dict):
                                            continue
                                        rows.append({k:record[k] for k in ("id","title","version","durationMs","artists","albums") if k in record})
                                    value = {"data": rows}
                                if "error" in value and isinstance(value["error"],dict) and value["error"].get("code") in (4, 700):
                                    delay = 5
                                elif not isinstance(value.get("data"), list):
                                    raise ValueError("invalid catalog page")
                                else:
                                    if catalogue_api_url(url):
                                        value["data"] = [
                                            {**{k: item[k] for k in ("id","duration","isrc","title","title_version") if k in item},
                                             "artist": {k: (item.get("artist") or {}).get(k) for k in ("id","name","picture_big")},
                                             "album": {k: (item.get("album") or {}).get(k) for k in ("id","title","cover_big")}}
                                            for item in value["data"] if isinstance(item, dict)]
                                    result = json.dumps(value["data"], ensure_ascii=False).encode()
                            if picture or "error" not in value:
                                cache_bytes(path, result)
                                if not picture:
                                    with self.gate:
                                        self.search_successes += 1
                                        if self.search_successes >= 32:
                                            self.search_interval = max(self.fastest_search, self.search_interval * 0.85)
                                            self.search_successes = 0
                                return result if picture else value["data"]
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                    if attempt == 3:
                        raise
                    self.count("network_retries")
                    time.sleep(2 ** attempt)
                    continue
                with self.gate:
                    self.cooldown[host] = max(self.cooldown.get(host, 0.0), time.monotonic() + max(delay, 2**attempt))
                    if not picture:
                        self.search_interval = min(2.0, self.search_interval * 2)
                        self.search_successes = 0
                    self.counts["provider_backoffs"] += 1
        raise RuntimeError("catalog temporarily busy")

    @contextmanager
    def publisher_gate(self):
        while True:
            with self.gate:
                if self.publisher_paused.is_set():
                    raise MetadataProviderError("publisher_batch_deferred", retryable=True)
                now = time.monotonic()
                delay = self.next_request - now
                if delay <= 0:
                    self.next_request = now + 0.5
                    break
            time.sleep(delay)
        yield

    def publisher_lookup(self, title, artist, album, duration_ms):
        key = json.dumps([title, artist, album, duration_ms], ensure_ascii=False).encode()
        path = self.path / ("bandcamp-track-v2-" + hashlib.sha256(key).hexdigest() + ".json")
        if path.exists() and time.time() - path.stat().st_mtime < 86400 * 7:
            stored = json.loads(path.read_bytes())
            return ArtMatch(**stored) if stored else None
        if self.cached_searches:
            return None
        if not hasattr(self.local, "publisher"):
            self.local.publisher = PublicMetadataHttp(self.publisher_gate)
        http = self.local.publisher
        try:
            page = http.json(BANDCAMP_SEARCH, post=bandcamp_query(artist, title))
            candidates = publisher_candidates(page, artist, title, album)
            results = []
            for url, candidate in candidates[:3]:
                try:
                    payload = http.get(url)
                except MetadataProviderError as error:
                    if error.code == "metadata_provider_busy":
                        raise
                    payload = None
                match = publisher_match(payload, candidate, url, title, artist, album, duration_ms)
                if not match and (not payload or b"data-tralbum" not in payload):
                    match = indexed_publisher_cover(candidates, title, artist, album)
                if match:
                    results.append(match)
        except MetadataProviderError as error:
            if error.code == "publisher_batch_deferred":
                raise
            self.count(error.code)
            with self.gate:
                busy = error.code == "metadata_provider_busy"
                self.publisher_responses.append(busy)
                if busy:
                    self.next_request = max(self.next_request, time.monotonic() + error.retry_after_seconds)
                    if sum(self.publisher_responses) >= 8:
                        self.publisher_paused.set()
            raise
        with self.gate:
            self.publisher_responses.append(False)
        result = results[0] if len({(r.artist_id, r.cover) for r in results}) == 1 else None
        cache_bytes(path, json.dumps(asdict(result) if result else None, ensure_ascii=False).encode())
        return result

    def release_cover(self, release, kind="release", *, corroborated=False):
        if kind not in {"release", "release-group"}:
            raise ValueError("invalid archive entity")
        release = UUID(release)
        path = self.path / (f"caa-{release}.jpg" if kind == "release" else f"caa-group-{release}.jpg")
        if corroborated:
            path = path.with_suffix(".cover.jpg")
        with self.key_lock(path.name):
            if path.exists() and (path.stat().st_size or time.time() - path.stat().st_mtime < 7 * 86400):
                return path.read_bytes() or None
            if self.cached_searches:
                return None
            if not hasattr(self.local, "archive"):
                self.local.archive = PublicMetadataHttp()
            with self.gate:
                at = max(time.monotonic(), self.next_archive)
                self.next_archive = at + 1
            time.sleep(max(0, at - time.monotonic()))
            data = self.local.archive.get(f"https://coverartarchive.org/{kind}/{release}/front-500", artwork=True)
            result = self.decoder.normalize_artwork(data, catalog_cover=corroborated) if data else b""
            cache_bytes(path, result)
            return result or None

    def apply(self, target, identity, match, cover, portrait, *, release=None):
        with self.key_lock(target.owner), Session(self.engine) as session, session.begin(), session.no_autoflush:
            _acquire_sync_owner_publish_lock(session, target.owner)
            ref = session.get(UserTrackRefRow, target.ref, with_for_update=True)
            if not ref or ref.deleted_at or ref.user_id != target.owner or ref.recording_id != target.recording_id:
                return
            valid = session.scalar(text("""
                SELECT 1 FROM library.library_entry l JOIN vault.recording_canonical_variant c ON c.recording_id=:record
                JOIN vault.audio_variant v USING(recording_id,audio_variant_id)
                JOIN vault.vault_object o USING(vault_object_id) JOIN catalog.recording r USING(recording_id)
                WHERE c.audio_variant_id=:variant AND r.deleted_at IS NULL
                AND NOT EXISTS (SELECT FROM identity.recording_redirect rr WHERE rr.source_recording_id=c.recording_id)
                AND l.user_id=:owner AND l.user_track_ref_id=:ref AND l.removed_at IS NULL
                AND v.duration_ms=:duration AND v.validation_status='VALID' AND v.deleted_at IS NULL AND o.commit_status='COMMITTED'
                """), {"owner": ref.user_id, "ref": target.ref, "record": ref.recording_id, "variant": target.audio_variant_id, "duration": target.duration_ms})
            if not valid:
                return
            row = session.get(TrackMetadataRow, target.ref, with_for_update=True)
            doc = row.document if row else {}
            if names(ref.raw_title, ref.raw_artist, ref.raw_album, doc.get("fields")) != identity:
                return
            if row is None:
                row = TrackMetadataRow(user_track_ref_id=target.ref, revision=0, generation=1, candidates=[], state="READY", document={})
            prior_sha, prior_portrait = row.artwork_sha256, doc.get("catalog_artist_image_v1")
            replaceable = not prior_sha or original_artwork_source(doc.get("artwork_source"))
            if release:
                if (not cover or not replaceable or (doc.get("fields") or {}).get("mb_release_id") != release
                        or (doc.get("artwork_source") or {}).get("locked")):
                    return
                row.artwork_sha256 = store_image(session, cover)
                evidence = (doc.get("provenance") or {}).get("mb_release_id") or {}
                row.document = {**doc, "artwork_source": {"source": "MUSICBRAINZ", "source_id": evidence.get("source_id") or release,
                                "observed_at": datetime.now(UTC).isoformat(), "locked": bool(evidence.get("locked"))}}
            else:
                if match.basis in ORIGINAL_BASES and (
                    doc.get("selection") or (doc.get("fields") or {}).get("mb_recording_id") != (target.fields or {}).get("mb_recording_id")
                    or doc.get("musicbrainz_performance_v1") != target.performance
                ):
                    return
                if not attach_images(session, row, match, cover, portrait):
                    return
            session.add(row)
            TrackMetadataService._publish(session, ref, row)
            new_cover = bool(row.artwork_sha256 and (not prior_sha or (
                replaceable and not original_artwork_source(row.document.get("artwork_source")))))
            new_portrait = bool(not prior_portrait and row.document.get("catalog_artist_image_v1"))
        self.count("covers", int(new_cover))
        self.count("portraits_on_tracks", int(new_portrait))
        return new_cover

    def group(self, items, *, retry=True):
        artist = items[0][1][1]
        search, match = (yandex_search_url, yandex_match) if self.yandex else (search_url, match_artwork)
        try:
            batch = self.fetch(search(artist)) if len(items) > 1 and not self.publisher and not self.archive else []
            if self.yandex and len(items) > 2:
                seeds = {str(p["id"]) for record in batch for p in record.get("artists") or []
                         if isinstance(p, dict) and str(p.get("id", "")).isdigit() and label(p.get("name") or "") == label(artist)}
                if len(seeds) == 1:
                    aid = seeds.pop()
                    batch = list(batch)
                    for page in range(min(6, 1 + len(items)//12)):
                        rows = self.fetch(discography_url(aid, page))
                        batch.extend(rows)
                        if len(rows) < 70:
                            break
        except Exception as exc:
            self.count(type(exc).__name__)
            batch = []
        if self.publisher or self.archive:
            with Session(self.engine) as session:
                done = set(session.scalars(text("""SELECT user_track_ref_id FROM library.track_metadata
                      WHERE user_track_ref_id=ANY(:ids) AND artwork_sha256 IS NOT NULL
                      AND coalesce(document#>>'{artwork_source,match_basis}', '') NOT IN ('original_cover','original_remix')"""), {"ids":[t.ref for t,_ in items]}))
            items = [(target, identity) for target, identity in items if target.ref not in done]
        for target, identity in items:
            if self.publisher and self.publisher_paused.is_set():
                self.count("deferred_until_next_import")
                continue
            title, credited, album = identity
            had_error = False
            needs_cover = not target.sha or original_artwork_source(target.artwork_source)
            release = (target.fields or {}).get("mb_release_id")
            if release and needs_cover and not self.yandex and not self.publisher and not self.archive:
                try:
                    if image := self.release_cover(release):
                        self.apply(target, identity, None, image, None, release=release)
                except (RuntimeError, ValueError) as exc:
                    self.count(type(exc).__name__)
                    had_error = True
            try:
                if self.archive:
                    hit = None
                    for candidate in archived_editions(target.candidates, title, credited, album, target.duration_ms):
                        try:
                            cover = self.release_cover(candidate.album_id, candidate.entity, corroborated=True)
                        except RuntimeError as exc:
                            self.count(type(exc).__name__)
                            had_error = True
                            continue
                        if cover:
                            self.apply(target, identity, candidate, cover, None)
                            hit = candidate
                            break
                elif self.publisher:
                    hit = self.publisher_lookup(title, credited, album, target.duration_ms)
                else:
                    hit = match(batch, title, credited, album, target.duration_ms)
                    if not hit or (not hit.cover and needs_cover and not release):
                        for term, artist_term in metadata_search_terms(title, credited):
                            direct = self.fetch(search(artist_term, term, clean_title=False))
                            hit = match(direct + batch, title, credited, album, target.duration_ms) or hit
                            if hit and hit.cover:
                                break
                if hit and not self.archive:
                    def read_image(url):
                        nonlocal had_error
                        try:
                            return self.fetch(url, picture=True) if url else None
                        except (requests.RequestException, RuntimeError, ValueError) as exc:
                            self.count(type(exc).__name__)
                            had_error = True
                            return None

                    cover = read_image(hit.cover) if needs_cover and not release else None
                    portrait = read_image(hit.portrait) if not target.portrait else None
                    if cover or portrait:
                        self.apply(target, identity, hit, cover, portrait)
                elif not hit:
                    self.count("no_match")
            except Exception as exc:
                self.count(type(exc).__name__)
                had_error = True
            if had_error and retry:
                with self.gate:
                    self.retry_items.append((target, identity))
            self.count("checked")
            with self.gate:
                if self.counts["checked"] % 250 == 0:
                    print(dict(self.counts), flush=True)

    def run(self, *, missing_metadata=False):
        groups = defaultdict(list)
        with Session(self.engine) as session:
            inventory = _INVENTORY + (" AND m.user_track_ref_id IS NULL" if missing_metadata else "")
            for target in session.execute(text(inventory)):
                identity = names(target.raw_title, target.raw_artist, target.raw_album, target.fields)
                title, artist, _ = identity
                own_cover = target.sha and not original_artwork_source(target.artwork_source)
                if (self.publisher or self.archive) and (own_cover or (target.fields or {}).get("mb_release_id")):
                    continue
                if self.archive and not archived_editions(target.candidates, title, artist, identity[2], target.duration_ms):
                    continue
                if not (own_cover and target.portrait) and artist and title and target.duration_ms:
                    groups[label(artist)].append((target, identity))
        batches = sorted(groups.values(), key=len, reverse=True)
        # Interleave large discographies so the long tail does not serialize on one thread.
        batches = [group[i:i+60] for group in batches for i in range(0, len(group), 60)]
        print("eligible", sum(map(len, batches)), "artists", len(groups), "workers", self.workers, flush=True)
        with ThreadPoolExecutor(self.workers) as pool:
            for _ in pool.map(self.group, batches):
                pass
            if self.retry_items and not self.publisher_paused.is_set():
                print("retrying", len(self.retry_items), "interrupted records", flush=True)
                for _ in pool.map(lambda item: self.group([item], retry=False), self.retry_items):
                    pass
        status = "publisher quota exceeded; resume with the same cache" if self.publisher_paused.is_set() else "complete"
        print(status, dict(self.counts), flush=True)


def refresh_catalog_handles(engine):
    from autplay.adapters.postgresql.shared_catalog import PUBLISHED_AUDIO, ensure_catalog_refs

    # The publication link is checked again inside the owner's short write transaction.
    with Session(engine) as session:
        owners = session.scalars(text("""
            SELECT DISTINCT u.user_id FROM library.user_track_ref u
            JOIN library.user_track_ref_external_reference link USING(user_track_ref_id)
            JOIN identity.external_reference x USING(external_reference_id)
            JOIN identity.source_provider p USING(provider_id)
            WHERE u.recording_id IS NULL AND u.deleted_at IS NULL AND link.relation_role='PRIMARY_SOURCE'
              AND p.provider_key='autplay.server-catalog' AND p.enabled AND p.deleted_at IS NULL
        """)).all()
    visited = 0
    for owner in owners:
        after = UUID(int=0)
        while True:
            with Session(engine) as session, session.begin():
                _acquire_sync_owner_publish_lock(session, owner)
                picks = session.execute(text(f"""
                    SELECT u.user_track_ref_id, p.*, u.raw_title AS title, u.raw_artist AS artist,
                           u.raw_duration_ms AS duration_ms FROM library.user_track_ref u
                    JOIN library.user_track_ref_external_reference link USING(user_track_ref_id)
                    JOIN LATERAL (SELECT * FROM ({PUBLISHED_AUDIO}) published
                        WHERE published.external_reference_id=link.external_reference_id LIMIT 1) p ON true
                    WHERE u.user_id=:owner AND u.recording_id IS NULL AND u.deleted_at IS NULL
                      AND link.relation_role='PRIMARY_SOURCE' AND u.user_track_ref_id>:after
                    ORDER BY u.user_track_ref_id LIMIT 64
                """), {"owner": owner, "after": after}).mappings().all()
                if not picks:
                    break
                after = picks[-1]["user_track_ref_id"]
                current = [p for p in picks if uuid5(owner, f"autplay-server-catalog:{p['external_reference_id']}") == p["user_track_ref_id"]]
                if current:
                    ensure_catalog_refs(session, owner, current)
                visited += len(current)
    print("catalog handles refreshed", visited, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-file", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=0, help="Concurrent I/O workers; 0 sizes the pool for the available CPUs")
    parser.add_argument("--archive-candidates", action="store_true", help="Find covers of independently corroborated editions in metadata review")
    parser.add_argument("--publisher", action="store_true", help="Search Bandcamp and verify the publisher recording page")
    parser.add_argument("--yandex", action="store_true", help="Search Yandex Music and verified artist discographies")
    parser.add_argument("--cached-searches", action="store_true", help="Reevaluate cached catalog results without searching again")
    parser.add_argument("--offline", action="store_true", help="Use only previously downloaded images and catalog results")
    parser.add_argument("--catalog-handles-only", action="store_true", help="Publish available images for existing account catalog references")
    parser.add_argument("--missing-metadata", action="store_true", help="Only index recordings without a metadata row")
    args = parser.parse_args()
    engine = create_engine(args.database_url_file.read_text().strip(), pool_size=4)
    if not args.catalog_handles_only:
        available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
        workers = min(64, max(1, args.workers or available * 6))
        Collector(args.cache, engine, workers, cached_searches=args.cached_searches, offline=args.offline, yandex=args.yandex, publisher=args.publisher, archive=args.archive_candidates).run(missing_metadata=args.missing_metadata)
    if not args.missing_metadata:
        refresh_catalog_handles(engine)
