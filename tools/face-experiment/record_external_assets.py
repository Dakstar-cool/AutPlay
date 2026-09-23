"""Verify manually downloaded research assets and record their external provenance.

This command only reads files. It never loads a model, decodes audio, or approves a
license. The output belongs next to the assets outside Git and every product image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

CLAP_REVISION = "4189a948e5e2aaa5df3e69da506e54513e977191"
CLAP_SHA256 = "013f1d40b8981b5dd741c0ccc444ae6cf8485d7cd4333892bcb2c6e8c3047064"
YAMNET_SHA256 = "13c3308955bbfaef262f175ac9c40e47b134573a93984f009220dd7cc12a1744"
ALBUM_SHA256 = "5495a8797bab9cc35f71490500c9008ea707f2e7f9f5537fddf9bed5d2fd0fed"
CLAP_METADATA_FILES = (
    "README.upstream.md",
    "metadata.upstream.json",
    "config.json",
    "merges.txt",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def _file(
    path: Path, *, expected_size: int | None = None, expected_hash: str | None = None
) -> dict[str, int | str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or linked asset: {path}")
    size = path.stat().st_size
    digest = _digest(path)
    if expected_size is not None and size != expected_size:
        raise ValueError(f"asset size mismatch: {path}")
    if expected_hash is not None and digest != expected_hash:
        raise ValueError(f"asset hash mismatch: {path}")
    return {"path": str(path), "size_bytes": size, "sha256": digest}


def build_manifest(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    clap = root / "clap_music" / "model.safetensors"
    clap_file = _file(clap, expected_size=776_327_440, expected_hash=CLAP_SHA256)
    with clap.open("rb") as stream:
        header_size = struct.unpack("<Q", stream.read(8))[0]
        if not 0 < header_size < 16 * 1024 * 1024:
            raise ValueError("invalid safetensors header bound")
        header = json.loads(stream.read(header_size))
        if not isinstance(header, dict) or not header:
            raise ValueError("invalid safetensors header")
    metadata = json.loads(
        (root / "clap_music" / "metadata.upstream.json").read_text(encoding="utf-8")
    )
    if metadata.get("sha") != CLAP_REVISION:
        raise ValueError("CLAP repository revision mismatch")
    siblings = {row["rfilename"]: row for row in metadata["siblings"]}
    if siblings["model.safetensors"]["lfs"]["sha256"] != CLAP_SHA256:
        raise ValueError("CLAP upstream blob digest mismatch")
    clap_metadata = []
    for name in CLAP_METADATA_FILES:
        path = root / "clap_music" / name
        row = _file(path)
        if name in siblings and row["size_bytes"] != siblings[name]["size"]:
            raise ValueError(f"CLAP upstream metadata size mismatch: {name}")
        clap_metadata.append(
            {"name": name, "size_bytes": row["size_bytes"], "sha256": row["sha256"]}
        )

    yamnet = root / "yamnet" / "yamnet.h5"
    yamnet_file = _file(yamnet, expected_size=15_296_092, expected_hash=YAMNET_SHA256)
    with yamnet.open("rb") as stream:
        if stream.read(8) != b"\x89HDF\r\n\x1a\n":
            raise ValueError("YAMNet HDF5 signature mismatch")

    album = root / "audio_smoke_source" / "album_1_0.zip"
    album_file = _file(album, expected_size=86_340_620, expected_hash=ALBUM_SHA256)
    with zipfile.ZipFile(album) as archive:
        entries = archive.infolist()
        if len(entries) != 27 or sum(row.file_size for row in entries) > 100_000_000:
            raise ValueError("unexpected audio archive size or entry count")
        for row in entries:
            relative = PurePosixPath(row.filename)
            if relative.is_absolute() or ".." in relative.parts or row.file_size > 20_000_000:
                raise ValueError("unsafe audio archive member")
        if archive.testzip() is not None:
            raise ValueError("audio archive CRC mismatch")

    return {
        "schema": "autplay.face.external-research-assets.v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "root": str(root),
        "purpose": "PRIVATE_RESEARCH_ONLY_NO_PRODUCT_ACTIVATION",
        "assets": [
            {
                "name": "laion_larger_clap_music",
                "source": f"https://huggingface.co/laion/larger_clap_music/tree/{CLAP_REVISION}",
                "weights": clap_file,
                "metadata": clap_metadata,
                "license_state": "PENDING_CHECKPOINT_AND_TRAINING_PROVENANCE_REVIEW",
                "format": "safetensors_no_pickle",
            },
            {
                "name": "tensorflow_yamnet",
                "source": "https://github.com/tensorflow/models/tree/master/research/audioset/yamnet",
                "weights_url": "https://storage.googleapis.com/audioset/yamnet.h5",
                "weights": yamnet_file,
                "readme": _file(root / "yamnet" / "README.upstream.md"),
                "license_state": "PENDING_EXACT_WEIGHT_LICENSE_REVIEW",
                "format": "hdf5_no_pickle",
            },
            {
                "name": "opengameart_album_1",
                "source": "https://opengameart.org/content/album-1-0",
                "license_label": "CC0",
                "archive": album_file,
                "source_page": _file(root / "audio_smoke_source" / "source_page.html"),
                "license_state": "QUARANTINED_COMPONENT_AND_REPRESENTATIVENESS_REVIEW",
                "qualification_collection": False,
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    manifest = build_manifest(args.root)
    output = args.root / "external-assets.manifest.json"
    if output.exists():
        raise ValueError("refusing to replace existing evidence manifest")
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
