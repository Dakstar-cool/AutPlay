"""Check artwork metadata sequentially from an exported snapshot. No account or Vault access."""

import argparse
import csv
import re
import time
from contextlib import contextmanager
from pathlib import Path

from autplay.adapters.public_track_metadata import MusicBrainzMetadataProvider, PublicMetadataHttp
from autplay.domain.catalog_artwork import (
    artwork_search_title,
    artwork_title,
    match_artwork,
    search_url,
)
from autplay.domain.music_variations import variation_kind
from autplay.domain.track_metadata import MetadataQuery
from autplay.ports.track_metadata import MetadataProviderError


def candidates(snapshot):
    with snapshot.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source, delimiter=";"):
            if row["Итог в Vault"] != "Пока без обложки":
                continue
            title, artist = row["Трек"], row["Исполнитель"]
            kind = variation_kind(None, False, None, None, artwork_title(title, artist))
            cleaned = artwork_search_title(title, artist)
            cleaned = re.sub(
                r"\s*[\[(](?:official (?:hd |4k )?(?:music )?video|премьера клипа(?:,\s*\d{4})?|(?:www\.)?[\w-]+\.(?:net|ru|com))[\])]\s*$",
                "",
                cleaned,
                flags=re.I,
            )
            cleaned = re.sub(
                r"\s*[\[(]music video[\])]\s*[-|]\s*(?:GRM Daily|Pressplay)\s*$",
                "",
                cleaned,
                flags=re.I,
            )
            if artist and (kind in {"REMIX", "COVER"} or cleaned != title):
                yield row, kind, cleaned or title


def run(snapshot, output, retry_from=None):
    @contextmanager
    def pace():
        time.sleep(1.05)
        yield

    http = PublicMetadataHttp(pace)
    provider = MusicBrainzMetadataProvider(http)
    rows = candidates(snapshot)
    if retry_from is not None:
        with retry_from.open(encoding="utf-8-sig", newline="") as previous:
            pending = {
                row["id"]
                for row in csv.DictReader(previous, delimiter=";")
                if row["status"]
                in {"metadata_network_unavailable", "metadata_provider_busy", "deezer_unavailable"}
            }
        rows = (entry for entry in rows if entry[0]["ID записи Vault"] in pending)
    columns = ("id", "artist", "title", "query", "status", "provider", "release", "cover")
    try:
        with output.open("x", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, columns, delimiter=";")
            writer.writeheader()
            for row, kind, cleaned in rows:
                query = MetadataQuery(
                    row["Трек"],
                    row["Исполнитель"],
                    row["Альбом в Vault"] or None,
                    int(row["Длительность файла (мс)"] or 0),
                )
                result = dict(
                    id=row["ID записи Vault"], artist=query.artist, title=query.title, query=cleaned
                )
                try:
                    page = http.json(search_url(query.artist, cleaned, clean_title=False))
                    if page.get("error"):
                        raise MetadataProviderError("deezer_unavailable")
                    match = match_artwork(
                        page.get("data"), query.title, query.artist, query.album, query.duration_ms
                    )
                    if (not match or not match.cover) and kind in {"COVER", "REMIX"}:
                        match = provider.find_original_artwork(query)
                    if match and match.cover:
                        result.update(
                            status=match.basis,
                            provider=match.provider,
                            release=match.album,
                            cover=match.cover,
                        )
                    else:
                        result["status"] = "unconfirmed"
                except MetadataProviderError as error:
                    result["status"] = error.code
                    if error.retryable and error.code in {
                        "metadata_provider_busy",
                        "deezer_unavailable",
                    }:
                        time.sleep(min(error.retry_after_seconds, 60))
                writer.writerow(result)
                handle.flush()
    finally:
        http.client.close()


def review_version_labels(snapshot, output):
    """Flag multi-part video titles independently of artwork and listening history."""
    with (
        snapshot.open(encoding="utf-8-sig", newline="") as src,
        output.open("x", encoding="utf-8-sig", newline="") as dst,
    ):
        writer = csv.writer(dst, delimiter=";")
        writer.writerow(
            [
                "ID записи Vault",
                "Исполнитель",
                "Исходное название",
                "Версия по названию",
                "Причина проверки",
            ]
        )
        for row in csv.DictReader(src, delimiter=";"):
            title = row["Трек"]
            parts = [part.strip() for part in title.split("|")]
            if len(parts) < 3 or not all(parts):
                continue
            kind = variation_kind(None, False, None, None, title)
            writer.writerow(
                [
                    row["ID записи Vault"],
                    row["Исполнитель"],
                    title,
                    kind or "Не установлена",
                    "Несколько частей названия через вертикальную черту",
                ]
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--review-versions", action="store_true")
    modes.add_argument("--retry-from", type=Path)
    args = parser.parse_args()
    if args.review_versions:
        review_version_labels(args.snapshot, args.output)
    else:
        run(args.snapshot, args.output, args.retry_from)
