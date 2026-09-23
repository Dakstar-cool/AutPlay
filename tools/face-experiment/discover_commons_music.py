"""Inventory Commons music with current per-file CC0/CC BY 4.0 API metadata.

This read-only discovery does not approve any license or create a final set.
Only the original file and description URLs are recorded; no media is fetched.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://commons.wikimedia.org/w/api.php"
CATEGORY = "Category:Audio files of music"
USER_AGENT = (
    "AutPlay-ML-Research/0.1 (https://github.com/Dakstar-cool/AutPlay; offline fixture inventory)"
)


def _plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    field = metadata.get(key, {})
    value = field.get("value", "") if type(field) is dict else ""
    return value if type(value) is str else ""


def _license_code(url: str, short_name: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "creativecommons.org" or parsed.query or parsed.fragment:
        return None
    parts = parsed.path.rstrip("/").split("/")
    if parts[-1] == "deed.en":
        parts.pop()
    if parts == ["", "publicdomain", "zero", "1.0"] and short_name in ("CC0", "CC0 1.0"):
        return "CC0-1.0"
    if parts == ["", "licenses", "by", "4.0"] and short_name == "CC BY 4.0":
        return "CC-BY-4.0"
    return None


def discover(checkpoint_path: Path) -> dict[str, Any]:
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("schema") != "autplay.face.commons-discovery-checkpoint.v1":
            raise ValueError("unknown Commons checkpoint")
        continuation = checkpoint["continuation"]
        seen_pages = set(checkpoint["seen_pages"])
        eligible = checkpoint["eligible"]
        scanned = checkpoint["scanned"]
    else:
        continuation: dict[str, str] = {}
        seen_pages: set[int] = set()
        eligible: list[dict[str, Any]] = []
        scanned = 0
    for _ in range(100):
        parameters = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": CATEGORY,
            "gcmtype": "file",
            "gcmlimit": "100",
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype|sha1|extmetadata",
            "iiextmetadatafilter": (
                "LicenseShortName|LicenseUrl|Artist|Credit|Permission|Copyrighted|Categories"
            ),
            "format": "json",
            "formatversion": "2",
            "maxlag": "5",
            **continuation,
        }
        request = urllib.request.Request(
            API + "?" + urllib.parse.urlencode(parameters), headers={"User-Agent": USER_AGENT}
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    if (
                        response.status != 200
                        or urllib.parse.urlsplit(response.geturl()).hostname
                        != "commons.wikimedia.org"
                    ):
                        raise ValueError("unexpected Commons API response")
                    data = response.read(2_000_001)
                break
            except urllib.error.HTTPError as error:
                if error.code not in (429, 503) or attempt == 3:
                    raise
                retry = error.headers.get("Retry-After", "5")
                delay = int(retry) if retry.isdigit() else 5
                time.sleep(min(max(delay, 5), 120))
        if len(data) > 2_000_000:
            raise ValueError("Commons API page exceeded bound")
        document = json.loads(data)
        if "error" in document:
            raise ValueError(f"Commons API error: {document['error']}")
        pages = document.get("query", {}).get("pages", [])
        if type(pages) is not list or len(pages) > 100:
            raise ValueError("unexpected Commons API page set")
        for page in pages:
            page_id = page["pageid"]
            if page_id in seen_pages:
                raise ValueError("duplicate Commons file page")
            seen_pages.add(page_id)
            scanned += 1
            info = page.get("imageinfo", [])
            if len(info) != 1:
                continue
            item = info[0]
            metadata = item.get("extmetadata", {})
            license_url = _metadata_value(metadata, "LicenseUrl")
            license_short_name = _plain(_metadata_value(metadata, "LicenseShortName"))
            license_code = _license_code(license_url, license_short_name)
            duration = item.get("duration")
            size = item.get("size")
            if (
                license_code is None
                or item.get("mediatype") != "AUDIO"
                or type(duration) not in (int, float)
                or not 135.0 <= float(duration) <= 900.0
                or type(size) is not int
                or not 128_000 <= size <= 50_000_000
                or not str(item.get("url", "")).startswith("https://upload.wikimedia.org/")
                or not re.fullmatch(r"[0-9a-f]{40}", str(item.get("sha1", "")))
            ):
                continue
            artist = _plain(_metadata_value(metadata, "Artist"))
            if not 1 <= len(artist) <= 250:
                continue
            eligible.append(
                {
                    "page_id": page_id,
                    "title": page["title"],
                    "artist_display": artist,
                    "file_url": item["url"],
                    "description_url": item["descriptionurl"],
                    "upstream_sha1": item["sha1"],
                    "size_bytes": size,
                    "duration_seconds_api": float(duration),
                    "mime": item.get("mime"),
                    "license_code": license_code,
                    "license_url": license_url,
                    "license_short_name": license_short_name,
                    "categories": _plain(_metadata_value(metadata, "Categories")),
                    "credit": _plain(_metadata_value(metadata, "Credit"))[:500],
                    "permission": _plain(_metadata_value(metadata, "Permission"))[:500],
                }
            )
        next_page = document.get("continue")
        if next_page is None:
            break
        if type(next_page) is not dict or "gcmcontinue" not in next_page:
            raise ValueError("unexpected Commons API continuation")
        continuation = {str(key): str(value) for key, value in next_page.items()}
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema": "autplay.face.commons-discovery-checkpoint.v1",
                    "continuation": continuation,
                    "seen_pages": sorted(seen_pages),
                    "eligible": eligible,
                    "scanned": scanned,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Scanned {scanned} Commons files; {len(eligible)} license-labeled leads", flush=True)
        time.sleep(3)
    else:
        raise ValueError("Commons category exceeded page bound")
    return {
        "schema": "autplay.face.commons-music-discovery.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "api": API,
        "category": CATEGORY,
        "api_request_user_agent": USER_AGENT,
        "scanned_file_pages": scanned,
        "license_counts": dict(Counter(row["license_code"] for row in eligible)),
        "status": "API_METADATA_LEADS_NOT_LICENSE_REVIEWED_OR_AUDIO_VERIFIED",
        "candidates": eligible,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("refusing to replace Commons discovery evidence")
    checkpoint = args.output.with_suffix(".checkpoint.json")
    result = discover(checkpoint)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    count = len(result["candidates"])
    print(f"{args.output}: {count} leads from {result['scanned_file_pages']} files")


if __name__ == "__main__":
    main()
