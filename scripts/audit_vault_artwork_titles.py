"""List cover and remix candidates from an existing Vault artwork audit without contacting providers."""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from autplay.domain.catalog_artwork import artwork_title
from autplay.domain.music_variations import variation_kind
from autplay.domain.original_artwork import credited_original
from autplay.domain.track_metadata import MetadataQuery


def inspect(path: Path, show_rows: bool) -> None:
    counts = Counter()
    writer = csv.writer(sys.stdout, delimiter=";")
    if show_rows:
        writer.writerow(
            [
                "ID записи Vault",
                "Исполнитель",
                "Трек",
                "Тип версии",
                "Исполнитель для проверки",
                "Название для проверки",
            ]
        )
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source, delimiter=";"):
            counts["всего"] += 1
            if row["Итог в Vault"] != "Пока без обложки":
                continue
            counts["без обложки"] += 1
            artist, title = row["Исполнитель"], row["Трек"]
            kind = variation_kind(None, False, None, None, artwork_title(title, artist))
            if kind in {"COVER", "REMIX"}:
                counts[kind] += 1
                if show_rows:
                    hint = credited_original(MetadataQuery(title, artist), kind)
                    writer.writerow(
                        [row["ID записи Vault"], artist, title, kind, *(hint or ("", ""))]
                    )
    for name, count in counts.items():
        print(f"{name}: {count}", file=sys.stderr if show_rows else sys.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--list", action="store_true", help="Print matching Vault IDs and lookup titles"
    )
    args = parser.parse_args()
    inspect(args.snapshot, args.list)
