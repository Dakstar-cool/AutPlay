"""Payload-free immutable metadata inventory for one controlled training input root."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

import rfc8785
from autplay.domain.recommendations import JsonValue

SONA_MAX_TRAINING_ROOT_ENTRIES = 16_384
SONA_MAX_TRAINING_INPUT_BYTES = 2**63 - 1
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _InventoryEntry(TypedDict):
    path: str
    kind: str
    device: str
    inode: str
    mode: str
    size: str
    modified_ns: str
    changed_ns: str


@dataclass(frozen=True, slots=True)
class SonaTrainingRootInventory:
    inventory_sha256: str
    input_bytes: int
    entry_count: int
    root_device: str
    root_inode: str


def _unsafe(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT)


def _entry(path: str, kind: str, metadata: os.stat_result) -> _InventoryEntry:
    return {
        "path": path,
        "kind": kind,
        "device": str(metadata.st_dev),
        "inode": str(metadata.st_ino),
        "mode": str(stat.S_IMODE(metadata.st_mode)),
        "size": str(metadata.st_size),
        "modified_ns": str(metadata.st_mtime_ns),
        "changed_ns": str(metadata.st_ctime_ns),
    }


def _snapshot(root: Path) -> tuple[dict[str, object], int, int]:
    root_metadata = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink() or _unsafe(root_metadata):
        raise ValueError("Sona training input root is unsafe")
    entries: list[_InventoryEntry] = []
    input_bytes = 0
    seen: set[str] = set()
    pending: list[tuple[Path, str]] = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda child: child.name)
        except OSError as error:
            raise ValueError("Sona training input root is unavailable") from error
        directories: list[tuple[Path, str]] = []
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            canonical = relative.replace(os.sep, "/")
            identity = canonical.casefold() if os.name == "nt" else canonical
            if not child.name or child.name in {".", ".."} or identity in seen:
                raise ValueError("Sona training input root contains an ambiguous path")
            seen.add(identity)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError("Sona training input root is unavailable") from error
            if child.is_symlink() or _unsafe(metadata):
                raise ValueError("Sona training input root contains a link")
            if stat.S_ISDIR(metadata.st_mode):
                kind = "DIRECTORY"
                directories.append((Path(child.path), canonical))
            elif stat.S_ISREG(metadata.st_mode):
                kind = "FILE"
                input_bytes += metadata.st_size
                if not 0 <= input_bytes <= SONA_MAX_TRAINING_INPUT_BYTES:
                    raise ValueError("Sona training input root exceeds the byte bound")
            else:
                raise ValueError("Sona training input root contains a non-file entry")
            entries.append(_entry(canonical, kind, metadata))
            if len(entries) > SONA_MAX_TRAINING_ROOT_ENTRIES:
                raise ValueError("Sona training input root exceeds the entry bound")
        pending.extend(reversed(directories))
    document: dict[str, object] = {
        "schema_version": 1,
        "root": _entry("", "DIRECTORY", root_metadata),
        "entries": entries,
        "input_bytes": str(input_bytes),
    }
    return document, input_bytes, len(entries)


def inspect_sona_training_root(root: Path) -> SonaTrainingRootInventory:
    """Bind stable names/identities/sizes without reading owner-derived file payloads."""

    root = Path(root)
    if not root.is_absolute():
        raise ValueError("Sona training input root must be absolute")
    first, input_bytes, entry_count = _snapshot(root)
    second, second_bytes, second_count = _snapshot(root)
    if first != second or input_bytes != second_bytes or entry_count != second_count:
        raise ValueError("Sona training input root changed during inventory")
    return SonaTrainingRootInventory(
        sha256(rfc8785.dumps(cast(JsonValue, first))).hexdigest(),
        input_bytes,
        entry_count,
        cast(dict[str, str], first["root"])["device"],
        cast(dict[str, str], first["root"])["inode"],
    )


__all__ = (
    "SONA_MAX_TRAINING_INPUT_BYTES",
    "SONA_MAX_TRAINING_ROOT_ENTRIES",
    "SonaTrainingRootInventory",
    "inspect_sona_training_root",
)
