"""Acquire explicitly selected OpenGameArt source audio into offline quarantine.

The selection is a manually reviewed list of original OGA pages and file URLs.
CC0 page labels, hashes and decodability are evidence, not fixture approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

MAX_AUDIO_BYTES = 60_000_000
MAX_PAGE_BYTES = 1_000_000
SUFFIXES = frozenset({".mp3", ".ogg", ".flac", ".wav"})
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class OgaPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.license_names: list[str] = []
        self.author = ""
        self.files: dict[str, str] = {}
        self._anchor: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag not in VOID_TAGS:
            self.stack.append((tag, set((attributes.get("class") or "").split())))
        if tag != "a":
            return
        href = attributes.get("href") or ""
        if self._inside("field-name-field-art-files") and "/sites/default/files/" in href:
            self._anchor = ("file", href)
        elif self._inside("field-name-author-submitter") and href.startswith("/users/"):
            self._anchor = ("author", href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._anchor = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if not value:
            return
        if self._inside("license-name"):
            self.license_names.append(value)
        if self._anchor is not None:
            kind, href = self._anchor
            if kind == "author":
                self.author += value
            elif kind == "file":
                self.files[value] = href

    def _inside(self, css_class: str) -> bool:
        return any(css_class in classes for _, classes in self.stack)


def _source_url(url: str, prefix: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "opengameart.org"
        or not parsed.path.startswith(prefix)
    ):
        raise ValueError("untrusted OGA source URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("unexpected OGA source URL component")


def _digest(path: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            sha.update(chunk)
    return size, sha.hexdigest()


def _download(url: str, destination: Path, limit: int) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    if (
        destination.exists()
        or destination.is_symlink()
        or temporary.exists()
        or temporary.is_symlink()
    ):
        raise ValueError("refusing to overwrite existing source evidence")
    request = urllib.request.Request(url, headers={"User-Agent": "AutPlay-Face-Research/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            _source_url(response.url, "/")
            if response.status != 200:
                raise ValueError("unexpected source HTTP status")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > limit:
                raise ValueError("source exceeds byte limit")
            with temporary.open("xb") as output:
                remaining = limit
                while chunk := response.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("source exceeds byte limit")
                    output.write(chunk)
        temporary.replace(destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def _decode(path: Path, ffprobe: str, ffmpeg: str) -> tuple[float, int]:
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    info = json.loads(probe.stdout)
    streams = [row for row in info["streams"] if row.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ValueError("OGA source requires exactly one audio stream")
    duration = float(info["format"]["duration"])
    if not 120 <= duration <= 3_600:
        raise ValueError("OGA source duration outside full-track pool")
    decode = subprocess.run(
        [ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-map", "0:a:0", "-f", "null", "NUL"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if decode.returncode != 0:
        raise ValueError(f"OGA source full decode failed: {decode.stderr[:160]}")
    return duration, int(streams[0]["sample_rate"])


def acquire(selection_path: Path, destination: Path) -> dict[str, Any]:
    if selection_path.is_symlink() or destination.is_symlink():
        raise ValueError("linked selection or destination")
    selection_bytes = selection_path.read_bytes()
    selection = json.loads(selection_bytes)
    items = selection.get("items")
    if selection.get("schema") != "autplay.face.oga-primary-selection.v1" or not isinstance(
        items, list
    ):
        raise ValueError("invalid OGA selection")
    if not 15 <= len(items) <= 50:
        raise ValueError("OGA source pool outside selected bounds")
    if len({item["target_name"] for item in items}) != len(items):
        raise ValueError("duplicate OGA target name")
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if ffprobe is None or ffmpeg is None:
        raise ValueError("ffmpeg and ffprobe are required")
    destination.mkdir(parents=True, exist_ok=True)
    acquired: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in items:
        page_url = item["page_url"]
        file_url = item["file_url"]
        target_name = item["target_name"]
        _source_url(page_url, "/content/")
        _source_url(file_url, "/sites/default/files/")
        if (
            Path(target_name).name != target_name
            or Path(target_name).suffix.lower() not in SUFFIXES
        ):
            raise ValueError("unsafe OGA target name")
        if not isinstance(item["expected_author"], str) or not item["expected_author"]:
            raise ValueError("missing selected author")
        target = destination / target_name
        page = destination / f"{target_name}.source.html"
        try:
            if target.is_symlink() or page.is_symlink():
                raise ValueError("linked OGA evidence")
            if not page.exists():
                _download(page_url, page, MAX_PAGE_BYTES)
            parser = OgaPageParser()
            parser.feed(page.read_text(encoding="utf-8"))
            if parser.author != item["expected_author"] or parser.license_names != ["CC0"]:
                raise ValueError("OGA source author or single CC0 label differs")
            if file_url not in parser.files.values():
                raise ValueError("selected file URL absent from OGA source page")
            if not target.exists():
                _download(file_url, target, MAX_AUDIO_BYTES)
            size, sha256 = _digest(target)
            if not 128_000 <= size <= MAX_AUDIO_BYTES:
                raise ValueError("OGA source file size outside bounds")
            duration, sample_rate = _decode(target, ffprobe, ffmpeg)
            page_size, page_sha256 = _digest(page)
            acquired.append(
                {
                    "author": parser.author,
                    "source_page": page_url,
                    "source_page_path": str(page),
                    "source_page_size_bytes": page_size,
                    "source_page_sha256": page_sha256,
                    "license_labels_on_page": parser.license_names,
                    "source_file_url": file_url,
                    "audio_path": str(target),
                    "audio_size_bytes": size,
                    "audio_sha256": sha256,
                    "duration_seconds": duration,
                    "sample_rate_hz": sample_rate,
                    "status": "QUARANTINED_SOURCE_LEAD_NOT_FIXTURE_APPROVED",
                }
            )
            print(f"Verified OGA source lead {target_name}: {duration:.1f}s", flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            failures.append({"target_name": target_name, "reason": str(error)[:300]})
            print(f"Unavailable OGA source lead {target_name}: {type(error).__name__}", flush=True)
    return {
        "schema": "autplay.face.oga-primary-acquisition.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "selection_sha256": hashlib.sha256(selection_bytes).hexdigest(),
        "ffmpeg_path": ffmpeg,
        "ffprobe_path": ffprobe,
        "status": "QUARANTINED_SOURCE_LEADS_NOT_FIXTURE_OR_QUALIFICATION_APPROVED",
        "acquired": acquired,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    output = args.destination / "oga-primary-acquisition.manifest.json"
    if output.exists():
        raise ValueError("refusing to replace OGA acquisition evidence")
    result = acquire(args.selection, args.destination)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output}: {len(result['acquired'])} verified, {len(result['failures'])} unavailable")


if __name__ == "__main__":
    main()
