"""Live incremental traversal; each directory entry and boundary consumes budget.

This adapter belongs inside the retained maintenance child, not an API thread.
It observes names only and neither reads payloads nor creates storage directories.
"""

import os
import stat
from collections.abc import Generator
from pathlib import Path

from autplay.application.vault_inventory import (
    MAX_INVENTORY_WORK,
    InventoryArea,
    InventoryEntry,
    InventoryPage,
)
from autplay.domain.vault import OpaqueStorageKey, StorageOperationError, StorageSafetyError

_HEX = frozenset("0123456789abcdef")


class FilesystemVaultInventoryCursor:
    """Retain at most three directory iterators, without sorting or rescanning.

    A work unit opens a directory, consumes one entry, or closes an exhausted
    iterator. Empty directories and vanished entries therefore cannot evade the
    page limit. No filesystem call happens in the constructor. The cursor is
    single-owner and must be closed after cancellation or when its consumer exits.
    """

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("vault_inventory_root_invalid")
        self._root = root
        self._walk = self._traverse()
        self._closed = False
        self._exhausted = False
        self._ancestors: dict[Path, os.stat_result] = {}

    def next_page(self, *, maximum: int = MAX_INVENTORY_WORK) -> InventoryPage:
        if type(maximum) is not int or not 1 <= maximum <= MAX_INVENTORY_WORK:
            raise ValueError("vault_inventory_limit_invalid")
        if self._closed:
            raise StorageOperationError()
        entries: list[InventoryEntry] = []
        work_units = 0
        try:
            while work_units < maximum and not self._exhausted:
                try:
                    entry = next(self._walk)
                except StopIteration:
                    self._exhausted = True
                    break
                work_units += 1
                if entry is not None:
                    entries.append(entry)
        except OSError as error:
            self.close()
            raise StorageOperationError() from error
        except BaseException:
            self.close()
            raise
        return InventoryPage(tuple(entries), work_units, self._exhausted)

    def close(self) -> None:
        self._closed = True
        self._walk.close()

    @staticmethod
    def _safe_stat(path: Path, *, directory: bool) -> os.stat_result:
        observed = os.stat(path, follow_symlinks=False)
        valid = stat.S_ISDIR(observed.st_mode) if directory else stat.S_ISREG(observed.st_mode)
        if (
            not valid
            or getattr(observed, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise StorageSafetyError()
        return observed

    def _require_ancestors(self, directory: Path) -> None:
        current = self._root
        self._require_same_directory(current)
        for part in directory.relative_to(self._root).parts:
            current = current / part
            self._require_same_directory(current)

    def _require_same_directory(self, directory: Path) -> None:
        observed = self._safe_stat(directory, directory=True)
        expected = self._ancestors.get(directory)
        if expected is not None and not os.path.samestat(expected, observed):
            raise StorageSafetyError()
        self._ancestors[directory] = observed

    def _traverse(self) -> Generator[InventoryEntry | None]:
        # Each namespace is finite-depth; quarantine and provider-work are never
        # traversed. Names observed here confer no deletion or missing-file proof.
        yield from self._directory(self._root / "objects", InventoryArea.OBJECT, 0)
        yield from self._directory(self._root / "staging", InventoryArea.STAGING, 0)

    def _directory(
        self, directory: Path, area: InventoryArea, depth: int
    ) -> Generator[InventoryEntry | None]:
        self._require_ancestors(directory)
        try:
            with os.scandir(directory) as iterator:
                yield None
                while True:
                    # Recheck after each suspended page before using cached
                    # enumeration handles with path-based operations.
                    self._require_ancestors(directory)
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    path = directory / entry.name
                    is_directory = area == InventoryArea.OBJECT and depth < 2
                    # DirEntry metadata can be cached; use a fresh no-follow stat.
                    try:
                        self._safe_stat(path, directory=is_directory)
                    except FileNotFoundError:
                        yield None
                        continue
                    if is_directory:
                        if len(entry.name) != 2 or not set(entry.name) <= _HEX:
                            raise StorageSafetyError()
                        yield None
                        # A vanished directory aborts the traversal instead of
                        # yielding a misleading successful end marker.
                        yield from self._directory(path, area, depth + 1)
                        continue
                    key = OpaqueStorageKey(entry.name)
                    if area == InventoryArea.OBJECT and (
                        len(key.value) != 64
                        or not set(key.value) <= _HEX
                        or key.value[:2] != directory.parent.name
                        or key.value[2:4] != directory.name
                    ):
                        raise StorageSafetyError()
                    yield InventoryEntry(area, key)
        finally:
            self._ancestors.pop(directory, None)
        yield None
