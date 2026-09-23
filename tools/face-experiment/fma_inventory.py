"""Select diverse, license-labeled FMA-small development *candidates*.

Metadata labels are not an authorization. The caller must inspect each live
track page, review the exact license and verify/decode the audio before adding
any track to a Face fixture collection. Nothing here selects a final set.
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

METADATA_SHA1 = "f0df49ffe5f2a6008d7dc83c6915b31835dfe733"
ALLOWED_LICENSES = {
    "http://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0",
    "http://creativecommons.org/licenses/by/4.0/": "CC-BY-4.0",
}


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_inventory(metadata_zip: Path, *, limit: int = 70) -> dict[str, object]:
    if type(limit) is not int or not 30 <= limit <= 200:
        raise ValueError("candidate limit must be between 30 and 200")
    if metadata_zip.is_symlink() or metadata_zip.stat().st_size != 358_412_441:
        raise ValueError("unexpected metadata archive")
    if _sha1(metadata_zip) != METADATA_SHA1:
        raise ValueError("FMA metadata SHA-1 differs from upstream release")
    with zipfile.ZipFile(metadata_zip) as archive:
        with archive.open("fma_metadata/tracks.csv") as source:
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            top = next(reader)
            lower = next(reader)
            next(reader)
            columns = {
                (group, name): index
                for index, (group, name) in enumerate(zip(top, lower, strict=True))
            }
            needed = (
                ("set", "subset"),
                ("artist", "id"),
                ("artist", "name"),
                ("album", "id"),
                ("track", "genre_top"),
                ("track", "duration"),
            )
            if any(key not in columns for key in needed):
                raise ValueError("FMA metadata columns differ from expected release")
            small: dict[int, dict[str, str]] = {}
            for row in reader:
                if row[columns["set", "subset"]] != "small":
                    continue
                track_id = int(row[0])
                if track_id in small:
                    raise ValueError("duplicate FMA track ID")
                small[track_id] = {
                    "artist_id": row[columns["artist", "id"]],
                    "artist_name": row[columns["artist", "name"]],
                    "album_id": row[columns["album", "id"]],
                    "genre": row[columns["track", "genre_top"]],
                    "original_duration_seconds": row[columns["track", "duration"]],
                }
        if len(small) != 8_000:
            raise ValueError("unexpected FMA-small track count")
        groups: dict[str, list[dict[str, str | int]]] = defaultdict(list)
        with archive.open("fma_metadata/raw_tracks.csv") as source:
            reader = csv.DictReader(io.TextIOWrapper(source, encoding="utf-8", newline=""))
            for row in reader:
                track_id = int(row["track_id"])
                if track_id not in small or row["license_url"] not in ALLOWED_LICENSES:
                    continue
                base = small[track_id]
                genre = base["genre"]
                groups[genre].append(
                    {
                        "track_id": track_id,
                        "archive_member": f"fma_small/{track_id // 1000:03d}/{track_id:06d}.mp3",
                        "genre": genre,
                        "artist_id": base["artist_id"],
                        "artist_name": base["artist_name"],
                        "album_id": base["album_id"],
                        "title": row["track_title"],
                        "track_url": row["track_url"],
                        "license_title": row["license_title"],
                        "license_url": row["license_url"],
                        "license_code": ALLOWED_LICENSES[row["license_url"]],
                        "original_duration_seconds": base["original_duration_seconds"],
                    }
                )
    for rows in groups.values():
        rows.sort(key=lambda item: (item["artist_id"], item["track_id"]))
    selected: list[dict[str, str | int]] = []
    used_artists: set[str | int] = set()
    genres = sorted(groups)
    while len(selected) < limit:
        advanced = False
        for genre in genres:
            available = next(
                (row for row in groups[genre] if row["artist_id"] not in used_artists), None
            )
            if available is None:
                continue
            selected.append(available)
            used_artists.add(available["artist_id"])
            groups[genre].remove(available)
            advanced = True
            if len(selected) == limit:
                break
        if not advanced:
            break
    if len(selected) < 30:
        raise ValueError("insufficient distinct-artist CC0/CC-BY FMA candidates")
    return {
        "schema": "autplay.face.fma-development-candidates.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": "https://github.com/mdeff/fma",
        "metadata_archive_path": str(metadata_zip.resolve(strict=True)),
        "metadata_archive_sha1": METADATA_SHA1,
        "audio_archive_url": "https://os.unil.cloud.switch.ch/fma/fma_small.zip",
        "audio_archive_expected_sha1": "ade154f733639d52e35e32f5593efe5be76c6d70",
        "status": "METADATA_ONLY_NOT_A_QUALIFIED_FIXTURE_COLLECTION",
        "disposition": "DEVELOPMENT_CANDIDATES_ONLY_NOT_FINAL_QUALIFICATION",
        "selection_rule": "genre_round_robin_distinct_artist_then_artist_id_track_id",
        "eligible_metadata_count": sum(len(rows) for rows in groups.values()) + len(selected),
        "candidates": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata_zip", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=70)
    args = parser.parse_args()
    inventory = build_inventory(args.metadata_zip, limit=args.limit)
    if args.output.exists():
        raise ValueError("refusing to replace an existing candidate inventory")
    args.output.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{args.output}: {len(inventory['candidates'])} candidate tracks")


if __name__ == "__main__":
    main()
