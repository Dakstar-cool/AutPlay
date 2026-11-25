"""Diagnose missing Vault artwork and resume individual public catalogue lookups."""

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from vault_artwork_backfill import (
    Collector,
    _INVENTORY,
    cache_bytes,
    names,
    refresh_catalog_handles,
)
from autplay.domain.catalog_artwork import (
    album_label,
    label,
    match_artwork,
    recording_labels_match,
    search_url,
    versioned_titles,
)


def saved(path, value):
    cache_bytes(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def cache_entry(directory, url, suffix):
    return directory / (hashlib.sha256(url.encode()).hexdigest() + suffix)


def search_input(directory, url):
    urls = [url]
    if url.endswith("&limit=25"):
        urls.append(url[:-2] + "100")
    for key in urls:
        path = cache_entry(directory, key, ".json")
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            data = json.loads(path.read_bytes())
            if isinstance(data, list):
                return data
    return None


def diagnose(directory, target):
    title, artist, album = names(
        target.raw_title, target.raw_artist, target.raw_album, target.fields
    )
    duration = target.duration_ms
    if not title or not artist or not duration:
        return "missing_identity", None
    direct = search_input(directory, search_url(artist, title))
    if direct is None:
        direct = search_input(directory, search_url(artist, title, clean_title=False))
    broad = search_input(directory, search_url(artist))
    rows = (direct or []) + (broad or [])
    match = match_artwork(rows, title, artist, album, duration)
    if (target.fields or {}).get("mb_release_id"):
        return "selected_edition_without_cover", match
    if match and match.cover:
        cached = cache_entry(directory, match.cover, ".jpg").is_file()
        return ("matched_image_cached" if cached else "matched_image_not_downloaded"), match
    if direct is None:
        return "missing_search_response", match
    if not rows:
        return "catalog_returned_no_tracks", None
    artist_name, song_name = label(artist), label(title)
    exact, near = [], []
    for row in rows:
        primary = (row.get("artist") or {}).get("name")
        if not primary or not isinstance(row.get("title"), str):
            continue
        if any(
            recording_labels_match(artist_name, song_name, label(primary), label(n))
            for n in versioned_titles(row["title"], row.get("title_version"))
        ):
            exact.append(row)
            if type(row.get("duration")) is int and abs(row["duration"] * 1000 - duration) <= 3000:
                near.append(row)
    if not exact:
        return "artist_title_or_version_mismatch", None
    if not near:
        return "duration_mismatch", None
    if album_label(album) and not any(
        album_label((r.get("album") or {}).get("title")) == album_label(album) for r in near
    ):
        return "album_mismatch", match
    return "edition_artist_or_image_unconfirmed", match


def failure(error):
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return "http_" + str(error.response.status_code)
    if isinstance(error, ValueError):
        raw = str(error)
        if raw in {"response too large", "invalid catalog page", "invalid catalog response"}:
            return raw.replace(" ", "_")
    return getattr(error, "code", None) or type(error).__name__


def load_inventory(engine):
    with Session(engine) as session:
        all_rows = list(session.execute(text(_INVENTORY)))
    covered = {r.recording_id for r in all_rows if r.sha}
    return {str(r.ref): r for r in all_rows if r.recording_id not in covered}


def audit(directory, rows):
    records = {}
    for ref, target in rows.items():
        reason, _ = diagnose(directory, target)
        records[ref] = {"reason": reason, "recording": str(target.recording_id)}
    return records


def retry_deezer(collector, searches, target):
    title, artist, album = identity = names(
        target.raw_title, target.raw_artist, target.raw_album, target.fields
    )
    stage = "search"
    try:
        fresh = searches.fetch(search_url(artist, title))
        if (target.fields or {}).get("mb_release_id"):
            return "selected_edition"
        broad = search_input(collector.path, search_url(artist)) or []
        match = match_artwork(fresh + broad, title, artist, album, target.duration_ms)
        if not match or not match.cover:
            return "no_confirmed_cover" if fresh else "empty_response"
        stage = "image"
        art = collector.fetch(match.cover, picture=True)
        if not art:
            return "image_not_found"
        changed = collector.apply(target, identity, match, art, None)
        return "image_processed" if changed else "already_covered_or_guarded"
    except Exception as error:
        return stage + "_error_" + failure(error)


def interrupted(status):
    return status in {"artist_search_interrupted", "lookup_interrupted"} or any(
        code in status
        for code in (
            "ReadTimeout",
            "TimeoutError",
            "ConnectionError",
            "http_429",
            "metadata_network_unavailable",
            "metadata_provider_busy",
        )
    )


def run_items(root, name, entries, workers, task):
    dest = root / name
    dest.mkdir(exist_ok=True)
    counts = Counter()

    def run(pair):
        ref, row = pair
        path = dest / (ref + ".json")
        prior = json.loads(path.read_bytes()) if path.exists() else {}
        if prior and not interrupted(prior["status"]):
            return prior["status"]
        status = task(ref, row)
        saved(path, {"status": status, "attempts": prior.get("attempts", int(bool(prior))) + 1})
        return status

    print(name, "eligible", len(entries), flush=True)
    with ThreadPoolExecutor(workers) as pool:
        for i, status in enumerate(pool.map(run, entries), 1):
            counts[status] += 1
            if i % 500 == 0:
                print(name, i, dict(counts), flush=True)
    print(name, "complete", dict(counts), flush=True)
    return counts


def cached_yandex_editions(directory, title, artist, album, duration):
    from autplay.domain.web_artwork import yandex_editions, yandex_search_url

    pages = search_input(directory, yandex_search_url(artist, title)) or []
    return yandex_editions(pages, title, artist, album, duration)


def timing_recovery(collector, target):
    from autplay.domain.web_artwork import yandex_search_url, yandex_match

    title, artist, album = identity = names(
        target.raw_title, target.raw_artist, target.raw_album, target.fields
    )
    if (
        not title
        or not artist
        or not target.duration_ms
        or (target.fields or {}).get("mb_release_id")
    ):
        return "insufficient_or_selected"
    try:
        yandex = search_input(collector.path, yandex_search_url(artist, title)) or []
        broad = search_input(collector.path, search_url(artist)) or []
        rows = (search_input(collector.path, search_url(artist, title)) or []) + broad
        albums = cached_yandex_editions(collector.path, title, artist, album, target.duration_ms)
        match = match_artwork(
            rows, title, artist, album, target.duration_ms, confirmed_albums=albums
        )
        other = yandex_match(
            yandex,
            title,
            artist,
            album or (match.album if match and match.cover else None),
            target.duration_ms,
        )
        failed = None
        for option in (match, other):
            if not option or not option.cover:
                continue
            try:
                art = collector.fetch(option.cover, picture=True)
            except Exception as err:
                failed = failure(err)
                continue
            if art:
                changed = collector.apply(target, identity, option, art, None)
                return (
                    "image_processed_" + option.provider + "_" + option.basis
                    if changed
                    else "already_covered_or_guarded"
                )
        return "timing_error_" + failed if failed else "no_confirmed_cover"
    except Exception as err:
        return "timing_error_" + failure(err)


class PublicCache:
    def __init__(self, collector, root):
        import threading

        self.collector = collector
        self.path = root / "public-responses"
        self.path.mkdir(exist_ok=True)
        self.local = threading.local()
        self.gate = threading.Lock()
        self.next = {}

    def http(self):
        from autplay.adapters.public_track_metadata import PublicMetadataHttp

        if not hasattr(self.local, "client"):
            self.local.client = PublicMetadataHttp()
        return self.local.client

    def json(self, url, interval=1.05):
        from autplay.ports.track_metadata import MetadataProviderError

        host = url.split("/")[2]
        path = cache_entry(self.path, url, ".json")
        with self.collector.key_lock(str(path)):
            if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
                return json.loads(path.read_bytes())
            for attempt in range(3):
                while True:
                    with self.gate:
                        now = time.monotonic()
                        delay = self.next.get(host, 0) - now
                        if delay <= 0:
                            self.next[host] = now + interval
                            break
                    time.sleep(delay)
                try:
                    result = self.http().json(url)
                    if host == "api.deezer.com" and isinstance(error := result.get("error"), dict) and error.get("code") in (4, 700):
                        raise MetadataProviderError("metadata_provider_busy")
                    saved(path, result)
                    return result
                except MetadataProviderError as error:
                    if error.code == "metadata_provider_busy":
                        with self.gate:
                            self.next[host] = max(
                                self.next[host], time.monotonic() + error.retry_after_seconds
                            )
                    if not error.retryable or attempt == 2:
                        raise
                    time.sleep(2**attempt)


def recording_ids(target):
    from autplay.domain.recovery_artwork import mbid

    selected = mbid((target.fields or {}).get("mb_recording_id"))
    if selected:
        return {selected}
    ids = set()
    for r in target.candidates or []:
        f = r.get("fields") or {}
        if r.get("source_id") == "musicbrainz:release:" + str(mbid(f.get("mb_release_id"))):
            if recording := mbid(f.get("mb_recording_id")):
                ids.add(recording)
    return ids


def discover_recordings(cache, rows, *, plan_only=False, prepared=False):
    from urllib.parse import urlencode
    from autplay.domain.catalog_artwork import artwork_title, timing_limit
    from autplay.domain.track_metadata import version_markers

    pairs = {}
    for ref, row in rows.items():
        title, artist, _ = names(row.raw_title, row.raw_artist, row.raw_album, row.fields)
        if artist and title and row.duration_ms and not (row.fields or {}).get("mb_release_id"):
            pairs.setdefault((artist, artwork_title(title, artist)), []).append(ref)

    # Keep each public request bounded even for filenames with long credits.
    def phrase(name):
        return '"' + re.sub(r'(["\\])', r"\\\1", name) + '"'

    def query(keys):
        clauses = [
            "(artist:" + phrase(artist) + " AND recording:" + phrase(title) + ")"
            for artist, title in keys
        ]
        return "https://musicbrainz.org/ws/2/recording/?" + urlencode(
            {"query": " OR ".join(clauses), "limit": min(75, len(keys) * 6), "fmt": "json"}
        )

    groups, current = [], []
    for key in sorted(pairs):
        if current and (len(current) >= 8 or len(query(current + [key])) > 2900):
            groups.append(current)
            current = []
        current.append(key)
    if current:
        groups.append(current)
    plan = cache.path.parent / "musicbrainz-plan.json"
    if plan.exists():
        groups = [[tuple(pair) for pair in group] for group in json.loads(plan.read_bytes())]
    else:
        saved(plan, groups)
    if plan_only:
        saved(
            cache.path.parent / "musicbrainz-urls.json",
            [
                query(group)
                for group in groups
                if not cache_entry(cache.path, query(group), ".json").exists()
            ],
        )
        print("prepared MusicBrainz batches", len(groups), flush=True)
        return {}

    def find(keys):
        try:
            url = query(keys)
            if prepared:
                path = cache_entry(cache.path, url, ".json")
                if not path.exists():
                    return {}, "pending_response"
                data = json.loads(path.read_bytes())
            else:
                data = cache.json(url, 1.15)
        except Exception as error:
            return {}, failure(error)
        result = {}
        for artist, title in keys:
            for row in data.get("recordings") or []:
                if (
                    not isinstance(row, dict)
                    or not row.get("id")
                    or not isinstance(row.get("title"), str)
                ):
                    continue
                credit = "".join(
                    (part.get("name") or (part.get("artist") or {}).get("name") or "")
                    + (part.get("joinphrase") or "")
                    for part in row.get("artist-credit") or []
                    if isinstance(part, dict)
                )
                if not recording_labels_match(
                    label(artist), label(title), label(credit), label(row["title"])
                ):
                    continue
                explicit = version_markers(row.get("disambiguation") or "")
                if explicit and not explicit.issubset(version_markers(title)):
                    continue
                length = row.get("length")
                for ref in pairs.get((artist, title), ()):
                    dur = rows[ref].duration_ms
                    if type(length) is int and abs(length - dur) <= timing_limit(
                        dur, corroborated=True
                    ):
                        result.setdefault(ref, []).append(row["id"])
        return result, None

    found, errors = {}, Counter()
    path = cache.path.parent / "musicbrainz-index.json"
    if path.exists():
        return json.loads(path.read_bytes())
    print(
        "MusicBrainz search batches",
        len(groups),
        "recordings",
        sum(map(len, pairs.values())),
        flush=True,
    )
    with ThreadPoolExecutor(6) as pool:
        for i, (results, error) in enumerate(pool.map(find, groups), 1):
            found.update(results)
            if error:
                errors[error] += 1
            if i % 75 == 0:
                print(
                    "MusicBrainz search",
                    i,
                    "batches; identified",
                    len(found),
                    "errors",
                    dict(errors),
                    flush=True,
                )
    if not errors:
        saved(path, found)
    print("MusicBrainz search complete", len(found), dict(errors), flush=True)
    return found


def brainz_recovery(collector, root, rows, mode):
    from autplay.domain.recovery_artwork import listenbrainz_cover, listenbrainz_url

    cache = PublicCache(collector, root)
    if mode in {"musicbrainz", "prepared"}:
        found = discover_recordings(cache, rows, prepared=mode == "prepared")
        by_ref = {ref: set(found.get(ref, [])) for ref in rows}
    else:
        by_ref = {ref: recording_ids(target) for ref, target in rows.items()}
    ids = sorted(set().union(*by_ref.values())) if by_ref else []
    print("recordings to look up", len(ids), flush=True)
    catalog, interrupted = {}, set()
    single_cache = root / "listenbrainz-recordings"
    single_cache.mkdir(exist_ok=True)
    pending = []
    for recording in ids:
        path = single_cache / (recording + ".json")
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            value = json.loads(path.read_bytes())
            if value is not None:
                catalog[recording] = value
        else:
            pending.append(recording)

    def lookup(batch):
        try:
            response = cache.json(listenbrainz_url(batch), 0.35)
        except Exception as error:
            return batch, {}, failure(error)
        records = {key: response[key] for key in batch if isinstance(response.get(key), dict)}
        for key in batch:
            saved(single_cache / (key + ".json"), records.get(key))
        return batch, records, None

    with ThreadPoolExecutor(4) as pool:
        for batch, records, error in pool.map(
            lookup, [pending[i : i + 12] for i in range(0, len(pending), 12)]
        ):
            catalog.update(records)
            if error:
                interrupted.update(batch)
    print("metadata found", len(catalog), "temporarily unavailable", len(interrupted), flush=True)

    def process(ref, target):
        title, artist, album = identity = names(
            target.raw_title, target.raw_artist, target.raw_album, target.fields
        )
        dur = target.duration_ms
        if not title or not artist or not dur:
            return "insufficient"
        selected = (target.fields or {}).get("mb_release_id")
        records = {k: catalog[k] for k in by_ref[ref] if k in catalog}
        evidence = search_input(collector.path, search_url(artist, title)) or []
        codes = {
            r["isrc"]
            for r in evidence
            if isinstance(r.get("isrc"), str)
            and recording_labels_match(
                label(artist),
                label(title),
                label((r.get("artist") or {}).get("name") or ""),
                label(r.get("title") or ""),
            )
            and type(r.get("duration")) is int
            and abs(dur - r["duration"] * 1000) <= 3000
            and (
                not album_label(album)
                or album_label((r.get("album") or {}).get("title")) == album_label(album)
            )
        }
        match = listenbrainz_cover(
            records, title, artist, album, dur, selected_release=selected, isrcs=codes
        )
        if not match:
            return "lookup_interrupted" if by_ref[ref] & interrupted else "no_confirmed_cover"
        try:
            art = collector.release_cover(match.album_id, corroborated=True)
            if not art:
                return "front_unavailable"
            changed = collector.apply(target, identity, match, art, None, release=selected)
            return "image_processed" if changed else "already_covered_or_guarded"
        except Exception as error:
            return "image_error_" + failure(error)

    run_items(
        root,
        "musicbrainz" if mode == "prepared" else mode,
        [(ref, r) for ref, r in rows.items() if by_ref[ref]],
        collector.workers,
        process,
    )


def archive_recovery(collector, root, rows):
    from autplay.domain.recovery_artwork import ITEM, archive_batch_search, archive_cover

    cache = PublicCache(collector, root)
    artists = {}
    for ref, row in rows.items():
        _, artist, _ = names(row.raw_title, row.raw_artist, row.raw_album, row.fields)
        if artist and not (row.fields or {}).get("mb_release_id"):
            artists[label(artist)] = artist
    candidates = {}
    missing = set()

    def find(group):
        try:
            data = cache.json(archive_batch_search(group), 1.0).get("response") or {}
        except Exception:
            return {}, set(map(label, group))
        docs = data.get("docs") or []
        if len(group) > 1 and data.get("numFound", 0) > len(docs):
            middle = len(group) // 2
            left, a = find(group[:middle])
            right, b = find(group[middle:])
            return {**left, **right}, a | b
        matches = {label(a): [] for a in group}
        for doc in docs:
            identifier, creator = doc.get("identifier"), doc.get("creator")
            if not isinstance(identifier, str) or not ITEM.fullmatch(identifier):
                continue
            for author in creator if isinstance(creator, list) else [creator]:
                k = label(author) if isinstance(author, str) else ""
                if k in matches and len(matches[k]) < 24:
                    matches[k].append(identifier)
        return matches, set()

    all_names = sorted(artists.values())
    with ThreadPoolExecutor(5) as pool:
        for matches, interrupted in pool.map(
            find, [all_names[i : i + 10] for i in range(0, len(all_names), 10)]
        ):
            candidates.update(matches)
            missing.update(interrupted)
    print(
        "archive artist searches",
        len(artists),
        "with items",
        sum(bool(ids) for ids in candidates.values()),
        "interrupted",
        len(missing),
        flush=True,
    )

    def process(ref, target):
        title, artist, album = identity = names(
            target.raw_title, target.raw_artist, target.raw_album, target.fields
        )
        if (
            not title
            or not artist
            or not target.duration_ms
            or (target.fields or {}).get("mb_release_id")
        ):
            return "insufficient_or_selected"
        try:
            choices = []
            if label(artist) in missing:
                return "artist_search_interrupted"
            for identifier in candidates.get(label(artist), []):
                item = cache.json("https://archive.org/metadata/" + identifier, 1.0)
                if m := archive_cover(item, identifier, title, artist, album, target.duration_ms):
                    choices.append(m)
            if len({m.cover for m in choices}) != 1:
                return "no_confirmed_cover"
            match = choices[0]
            path = cache_entry(collector.path, match.cover, ".jpg")
            with collector.key_lock(str(path)):
                if not path.exists():
                    data = cache.http().get(match.cover, artwork=True)
                    if not data:
                        return "cover_not_found"
                    cache_bytes(path, collector.decoder.normalize_artwork(data, catalog_cover=True))
                art = path.read_bytes()
            changed = collector.apply(target, identity, match, art, None)
            return "image_processed" if changed else "already_covered_or_guarded"
        except Exception as error:
            return "archive_error_" + failure(error)

    run_items(root, "archive-batch", list(rows.items()), collector.workers, process)


def report_cohort(engine, cohort):
    from uuid import UUID

    ids = [UUID(key) for key in cohort]
    query = text("""
        SELECT u.user_track_ref_id,m.artwork_sha256,m.document->'artwork_source' AS source,
               a.sha256 IS NOT NULL AND encode(sha256(a.content),'hex')=m.artwork_sha256 AS intact
        FROM library.user_track_ref u LEFT JOIN library.track_metadata m USING(user_track_ref_id)
        LEFT JOIN library.metadata_artwork a ON a.sha256=m.artwork_sha256
        WHERE u.user_track_ref_id=ANY(:ids) AND u.deleted_at IS NULL""")
    total, recovered, sources = Counter(), Counter(), Counter()
    digests = set()
    with Session(engine) as session:
        for ref, sha, source, intact in session.execute(query, {"ids": ids}):
            reason = cohort[str(ref)]["reason"]
            total[reason] += 1
            if sha and intact:
                recovered[reason] += 1
                digests.add(sha)
                origin = (source or {}).get("source_id") or "unknown"
                sources[origin.split(":")[0]] += 1
    print(
        "original",
        len(cohort),
        "present",
        sum(total.values()),
        "with verified artwork",
        sum(recovered.values()),
        "distinct images",
        len(digests),
        flush=True,
    )
    print(
        "original reason / present / with cover",
        sorted((key, n, recovered[key]) for key, n in total.items()),
        flush=True,
    )
    print("cover sources", dict(sources), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url-file", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--cohort", required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "audit",
            "groups",
            "deezer",
            "timing",
            "brainz",
            "musicbrainz",
            "archive",
            "retry",
            "report",
            "plan-search",
            "retry-sources",
            "prepared",
            "originals",
        ),
        required=True,
    )
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    if not re.fullmatch(r"[\w-]{1,60}", args.cohort):
        parser.error("invalid cohort")
    root = args.cache / args.cohort
    root.mkdir(exist_ok=True)
    engine = create_engine(args.database_url_file.read_text().strip(), pool_size=4)
    rows = load_inventory(engine)
    manifest = root / "baseline.json"
    if args.mode == "audit":
        if not manifest.exists():
            saved(manifest, audit(args.cache, rows))
        cohort = json.loads(manifest.read_bytes())
        print(
            "cohort",
            len(cohort),
            dict(Counter(item["reason"] for item in cohort.values())),
            flush=True,
        )
        return
    cohort = json.loads(manifest.read_bytes())
    collector = Collector(args.cache, engine, args.workers)
    rows = {ref: r for ref, r in rows.items() if ref in cohort}
    if args.mode == "originals":
        from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider
        from autplay.domain.track_metadata import MetadataQuery

        provider = MusicBrainzMetadataProvider(PublicCache(collector, root))

        def original_cover(ref, row):
            identity = names(row.raw_title, row.raw_artist, row.raw_album, row.fields)
            title, artist, album = identity
            if not title or not artist:
                return "missing_identity"
            query = MetadataQuery(title, artist, album, row.duration_ms, (row.fields or {}).get("mb_recording_id"))
            try:
                match = provider.find_original_artwork(query, row.performance, row.recording_kind, row.version_text)
                if not match:
                    return "no_confirmed_original"
                if match.provider == "COVER_ART_ARCHIVE":
                    cover = collector.release_cover(match.album_id, corroborated=True)
                else:
                    cover = collector.fetch(match.cover, picture=True)
                if not cover:
                    return "no_original_image"
                return "image_processed" if collector.apply(row, identity, match, cover, None) else "already_covered_or_guarded"
            except Exception as err:
                return "image_error_" + failure(err)

        run_items(root, "originals", list(rows.items()), collector.workers, original_cover)
        refresh_catalog_handles(engine)
        return
    if args.mode == "groups":
        from autplay.domain.recovery_artwork import selected_group_art

        def group_cover(ref, row):
            title, artist, album = identity = names(
                row.raw_title, row.raw_artist, row.raw_album, row.fields
            )
            match = selected_group_art(row.fields or {}, title, artist)
            if not match:
                return "no_confirmed_group"
            try:
                edition = row.fields["mb_release_id"]
                exact = collector.release_cover(edition)
                if exact:
                    changed = collector.apply(row, identity, None, exact, None, release=edition)
                else:
                    cover = collector.release_cover(
                        match.album_id, "release-group", corroborated=True
                    )
                    if not cover:
                        return "no_group_front"
                    changed = collector.apply(row, identity, match, cover, None)
                return "image_processed" if changed else "already_covered_or_guarded"
            except Exception as err:
                return "image_error_" + failure(err)

        run_items(
            root,
            "groups",
            [(ref, row) for ref, row in rows.items() if (row.fields or {}).get("mb_release_id")],
            collector.workers,
            group_cover,
        )
        refresh_catalog_handles(engine)
        return
    if args.mode == "retry-sources":
        for phase in ("brainz", "archive-batch"):
            targets = {
                f.stem: rows[f.stem]
                for f in (root / phase).glob("*.json")
                if f.stem in rows and interrupted(json.loads(f.read_bytes())["status"])
            }
            if targets:
                if phase == "brainz":
                    brainz_recovery(collector, root, targets, phase)
                else:
                    archive_recovery(collector, root, targets)
        refresh_catalog_handles(engine)
        return
    if args.mode == "plan-search":
        discover_recordings(PublicCache(collector, root), rows, plan_only=True)
        return
    if args.mode == "report":
        report_cohort(engine, cohort)
        for operation in ("deezer", "timing", "retry", "brainz", "archive-batch", "musicbrainz"):
            print(
                operation,
                dict(
                    Counter(
                        json.loads(f.read_bytes())["status"]
                        for f in (root / operation).glob("*.json")
                    )
                ),
                flush=True,
            )
        return
    if args.mode == "retry":
        searches = Collector(root / "deezer-search-r1", engine, args.workers)
        failed = {}
        for operation in ("deezer", "timing"):
            for f in (root / operation).glob("*.json"):
                state = json.loads(f.read_bytes())["status"]
                if "_error_" in state and f.stem in rows:
                    failed.setdefault(f.stem, set()).add(operation)

        def retry(ref, row):
            if "deezer" in failed[ref]:
                outcome = retry_deezer(collector, searches, row)
                if "error_" in outcome or outcome == "image_processed":
                    return outcome
            return timing_recovery(collector, row)

        run_items(root, "retry", [(ref, rows[ref]) for ref in failed], args.workers, retry)
        refresh_catalog_handles(engine)
        return
    if args.mode in {"timing", "brainz", "musicbrainz", "archive", "prepared"}:
        if args.mode == "timing":
            run_items(
                root,
                "timing",
                list(rows.items()),
                args.workers,
                lambda ref, r: timing_recovery(collector, r),
            )
        elif args.mode == "archive":
            archive_recovery(collector, root, rows)
        else:
            brainz_recovery(collector, root, rows, args.mode)
        refresh_catalog_handles(engine)
        return
    searches = Collector(root / "deezer-search-r1", engine, args.workers)
    entries = [
        (ref, rows[ref])
        for ref, entry in cohort.items()
        if ref in rows and entry["reason"] != "missing_identity"
    ]
    run_items(
        root,
        args.mode,
        entries,
        args.workers,
        lambda ref, row: retry_deezer(collector, searches, row),
    )
    refresh_catalog_handles(engine)


if __name__ == "__main__":
    main()
