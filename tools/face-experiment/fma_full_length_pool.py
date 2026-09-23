"""Select disjoint full-length FMA tracks for a quarantined final-source pool.

This is a metadata/availability lead, not a sealed qualification collection or
license decision. Never tune, compare, or inspect model outputs on this pool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fma_inventory import ALLOWED_LICENSES, METADATA_SHA1

FILE_PREFIX = "https://files.freemusicarchive.org/storage-freemusicarchive-org/"


def _duration_seconds(value: str) -> int | None:
    parts = value.split(":")
    if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_pool(metadata_zip: Path, development_inventory: Path, limit: int = 40) -> dict[str, Any]:
    if type(limit) is not int or not 15 <= limit <= 100:
        raise ValueError("full-length pool size outside bounds")
    if metadata_zip.is_symlink() or _sha1(metadata_zip) != METADATA_SHA1:
        raise ValueError("unverified FMA metadata")
    if development_inventory.is_symlink():
        raise ValueError("linked development inventory")
    development_bytes = development_inventory.read_bytes()
    development = json.loads(development_bytes)
    if (
        development.get("schema") != "autplay.face.fma-development-candidates.v1"
        or development.get("metadata_archive_sha1") != METADATA_SHA1
    ):
        raise ValueError("unexpected development inventory")
    used_artists = {str(row["artist_id"]) for row in development["candidates"]}
    development_track_ids = {row["track_id"] for row in development["candidates"]}
    with zipfile.ZipFile(metadata_zip) as archive:
        with archive.open("fma_metadata/tracks.csv") as source:
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            top, lower = next(reader), next(reader)
            next(reader)
            columns = {
                (group, name): index
                for index, (group, name) in enumerate(zip(top, lower, strict=True))
            }
            artist_column = columns["artist", "id"]
            album_column = columns["album", "id"]
            genre_column = columns["track", "genre_top"]
            tracks = {
                int(row[0]): {
                    "artist_id": row[artist_column],
                    "album_id": row[album_column],
                    "genre": row[genre_column],
                }
                for row in reader
            }
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with archive.open("fma_metadata/raw_tracks.csv") as source:
            reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            for row in reader:
                track_id = int(row["track_id"])
                base = tracks.get(track_id)
                duration = _duration_seconds(row["track_duration"])
                relative_file = row["track_file"]
                if (
                    base is None
                    or track_id in development_track_ids
                    or str(base["artist_id"]) in used_artists
                    or row["license_url"] not in ALLOWED_LICENSES
                    or duration is None
                    or not 135 <= duration <= 600
                    or not base["genre"]
                    or not relative_file.startswith("music/")
                    or not relative_file.endswith(".mp3")
                    or any(part in ("", ".", "..") for part in relative_file.split("/"))
                ):
                    continue
                grouped[str(base["genre"])].append(
                    {
                        "track_id": track_id,
                        "artist_id": base["artist_id"],
                        "artist_name": row["artist_name"],
                        "album_id": base["album_id"],
                        "title": row["track_title"],
                        "genre": base["genre"],
                        "metadata_duration_seconds": duration,
                        "license_title": row["license_title"],
                        "license_url": row["license_url"],
                        "license_code": ALLOWED_LICENSES[row["license_url"]],
                        "track_url": row["track_url"],
                        "file_url": FILE_PREFIX + relative_file,
                    }
                )
    for rows in grouped.values():
        rows.sort(key=lambda item: (int(item["artist_id"]), item["track_id"]))
    pool: list[dict[str, Any]] = []
    chosen_artists: set[str] = set()
    for _ in range(limit):
        advanced = False
        for genre in sorted(grouped):
            candidate = next(
                (row for row in grouped[genre] if str(row["artist_id"]) not in chosen_artists),
                None,
            )
            if candidate is None:
                continue
            pool.append(candidate)
            chosen_artists.add(str(candidate["artist_id"]))
            grouped[genre].remove(candidate)
            advanced = True
            if len(pool) == limit:
                break
        if len(pool) == limit or not advanced:
            break
    if len(pool) < 15:
        raise ValueError("insufficient disjoint full-length leads")
    return {
        "schema": "autplay.face.fma-full-length-pool.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": "https://github.com/mdeff/fma",
        "metadata_archive_sha1": METADATA_SHA1,
        "development_inventory_sha256": hashlib.sha256(development_bytes).hexdigest(),
        "status": "QUARANTINED_FINAL_SOURCE_POOL_LICENSE_AND_DECODER_UNREVIEWED",
        "selection_rule": "genre_round_robin_distinct_artist_excluding_development",
        "candidates": pool,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata_zip", type=Path)
    parser.add_argument("development_inventory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to replace existing full-length pool")
    result = build_pool(args.metadata_zip, args.development_inventory, args.limit)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{args.output}: {len(result['candidates'])} disjoint full-length leads")


if __name__ == "__main__":
    main()
