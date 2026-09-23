"""Fetch only FMA-small candidate ZIP members by authenticated HTTP ranges.

The full 7.2 GiB archive is not needed for a 70-track development shortlist.
This isolated operator tool uses the official archive's ZIP central directory,
checks each member's CRC, and records SHA-256. It does not approve licenses,
decode audio, or create a qualified fixture collection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
import zipfile
import zlib
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"
ARCHIVE_SIZE = 7_679_594_875
EXPECTED_ARCHIVE_SHA1 = "ade154f733639d52e35e32f5593efe5be76c6d70"
BLOCK_SIZE = 4 * 1024 * 1024
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)\Z")


class HTTPRangeReader:
    """Minimal seekable read-only file with a bounded LRU of verified ranges."""

    def __init__(self) -> None:
        self.position = 0
        self.cache: OrderedDict[int, bytes] = OrderedDict()
        self.closed = False
        self.requests = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        if self.closed:
            raise ValueError("range reader closed")
        base = (0, self.position, ARCHIVE_SIZE)[whence]
        position = base + offset
        if not 0 <= position <= ARCHIVE_SIZE:
            raise ValueError("range seek outside archive")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("range reader closed")
        if size < 0:
            size = ARCHIVE_SIZE - self.position
        if size > 16 * 1024 * 1024:
            raise ValueError("unbounded range read")
        size = min(size, ARCHIVE_SIZE - self.position)
        parts: list[bytes] = []
        while size:
            index, offset = divmod(self.position, BLOCK_SIZE)
            block = self._block(index)
            piece = block[offset : offset + size]
            if not piece:
                raise OSError("empty archive range")
            parts.append(piece)
            self.position += len(piece)
            size -= len(piece)
        return b"".join(parts)

    def close(self) -> None:
        self.closed = True
        self.cache.clear()

    def _block(self, index: int) -> bytes:
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        start = index * BLOCK_SIZE
        end = min(start + BLOCK_SIZE, ARCHIVE_SIZE) - 1
        request = urllib.request.Request(
            URL,
            headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if response.status != 206 or match is None:
                raise OSError("FMA origin did not honor bounded range")
            if tuple(map(int, match.groups())) != (start, end, ARCHIVE_SIZE):
                raise OSError("FMA origin range identity changed")
            value = response.read(end - start + 2)
        if len(value) != end - start + 1:
            raise OSError("incomplete FMA range")
        self.requests += 1
        self.cache[index] = value
        if len(self.cache) > 8:
            self.cache.popitem(last=False)
        return value


def _hash_file(path: Path) -> tuple[int, str, int]:
    sha = hashlib.sha256()
    crc = 0
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
    return size, sha.hexdigest(), crc


def _write_member(source: BinaryIO, target: Path) -> tuple[int, str, int]:
    temporary = target.with_suffix(target.suffix + ".part")
    if temporary.exists():
        raise ValueError(f"refusing existing partial candidate: {temporary}")
    sha = hashlib.sha256()
    crc = 0
    size = 0
    with temporary.open("xb") as stream:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > 10_000_000:
                raise ValueError("FMA candidate member exceeds bound")
            stream.write(chunk)
            sha.update(chunk)
            crc = zlib.crc32(chunk, crc)
    temporary.replace(target)
    return size, sha.hexdigest(), crc


def fetch_candidates(inventory_path: Path, destination: Path) -> dict[str, object]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if (
        inventory.get("schema") != "autplay.face.fma-development-candidates.v1"
        or inventory.get("audio_archive_url") != URL
        or inventory.get("audio_archive_expected_sha1") != EXPECTED_ARCHIVE_SHA1
        or not 30 <= len(inventory.get("candidates", [])) <= 200
    ):
        raise ValueError("untrusted FMA candidate inventory")
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("linked candidate destination")
    reader = HTTPRangeReader()
    output: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(reader) as archive:
            for candidate in inventory["candidates"]:
                track_id = candidate["track_id"]
                member = f"fma_small/{track_id // 1000:03d}/{track_id:06d}.mp3"
                if candidate["archive_member"] != member:
                    raise ValueError("FMA candidate path differs from track identity")
                info = archive.getinfo(member)
                if info.file_size > 10_000_000 or info.is_dir():
                    raise ValueError("invalid FMA audio member")
                target = destination / f"{track_id:06d}.mp3"
                if target.is_symlink():
                    raise ValueError("linked candidate audio")
                if target.exists():
                    size, digest, crc = _hash_file(target)
                else:
                    with archive.open(info) as source:
                        size, digest, crc = _write_member(source, target)
                if size != info.file_size or crc != info.CRC:
                    raise ValueError("FMA candidate CRC or byte count mismatch")
                output.append(
                    {
                        "track_id": track_id,
                        "audio_path": str(target.resolve(strict=True)),
                        "sha256": digest,
                        "size_bytes": size,
                        "zip_crc32": f"{crc:08x}",
                        "license_url": candidate["license_url"],
                        "track_url": candidate["track_url"],
                    }
                )
                print(f"Verified FMA candidate {track_id}: {size} bytes", flush=True)
    finally:
        reader.close()
    return {
        "schema": "autplay.face.fma-range-acquisition.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        "source_archive_url": URL,
        "source_archive_size_bytes": ARCHIVE_SIZE,
        "source_archive_expected_sha1_not_verified_by_ranges": EXPECTED_ARCHIVE_SHA1,
        "http_range_requests": reader.requests,
        "status": "QUARANTINED_AUDIO_NOT_LICENSE_OR_DECODER_APPROVED",
        "candidates": output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    output_path = args.destination / "range-acquisition.manifest.json"
    if output_path.exists():
        raise ValueError("refusing to replace existing range acquisition evidence")
    result = fetch_candidates(args.inventory, args.destination)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output_path)


if __name__ == "__main__":
    main()
