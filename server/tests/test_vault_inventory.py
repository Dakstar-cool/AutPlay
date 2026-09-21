"""Inventory bounds include directory work and never read object payloads."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.filesystem.vault_inventory import FilesystemVaultInventoryCursor
from autplay.application.vault_inventory import InventoryArea, InventoryEntry
from autplay.domain.vault import OpaqueStorageKey, StorageOperationError, StorageSafetyError


def populate(root: Path, count: int) -> set[InventoryEntry]:
    storage = FilesystemVaultStorage(root)
    shard = root / "objects" / "ab" / "cd"
    shard.mkdir(parents=True)
    expected = set()
    for index in range(count):
        key = OpaqueStorageKey(f"abcd{index:060x}")
        (shard / key.value).write_bytes(b"not-read-by-inventory")
        expected.add(InventoryEntry(InventoryArea.OBJECT, key))
    for name in ("disc-active", "provider-owned", "uncommitted-upload"):
        key = OpaqueStorageKey(name)
        storage.create_staging(key)
        expected.add(InventoryEntry(InventoryArea.STAGING, key))
    # Unrelated or large quarantine/provider trees are outside this traversal.
    (root / "quarantine" / "not-a-file").mkdir()
    (root / "provider-work").mkdir()
    return expected


class CountedIterator:
    def __init__(self, owner: EnumerationMeter, path: Path) -> None:
        self._owner = owner
        self._iterator = owner.original(path)
        owner.opened += 1
        owner.live += 1
        owner.high_water = max(owner.high_water, owner.live)

    def __enter__(self) -> CountedIterator:
        return self

    def __exit__(self, *_args: object) -> None:
        self._iterator.close()
        self._owner.live -= 1

    def __iter__(self) -> Iterator[os.DirEntry[str]]:
        return self

    def __next__(self) -> os.DirEntry[str]:
        self._owner.advances += 1
        return next(self._iterator)


class EnumerationMeter:
    def __init__(self) -> None:
        self.original = os.scandir
        self.opened = self.live = self.high_water = self.advances = 0

    def scan(self, path: Path) -> CountedIterator:
        return CountedIterator(self, path)


def test_pages_resume_without_rescan_or_unbounded_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = populate(tmp_path, 237)
    meter = EnumerationMeter()
    with monkeypatch.context() as patches:
        patches.setattr(os, "scandir", meter.scan)
        cursor = FilesystemVaultInventoryCursor(tmp_path)
        assert meter.opened == 0
        observed: list[InventoryEntry] = []
        try:
            for _ in range(100):
                before = meter.opened + meter.advances
                page = cursor.next_page(maximum=7)
                assert page.work_units <= 7
                assert len(page.entries) <= page.work_units
                assert meter.opened + meter.advances - before <= 7
                observed.extend(page.entries)
                if page.exhausted:
                    break
            else:
                pytest.fail("bounded cursor did not make forward progress")
            assert len(observed) == len(set(observed))
            assert set(observed) == expected
            assert meter.opened == 4 and meter.high_water == 3 and meter.live == 0
            assert cursor.next_page(maximum=1).exhausted
        finally:
            cursor.close()


def test_empty_directories_consume_budget_and_cancel_closes_every_iterator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FilesystemVaultStorage(tmp_path)
    for first in range(32):
        for second in range(32):
            (tmp_path / "objects" / f"{first:02x}" / f"{second:02x}").mkdir(parents=True)
    meter = EnumerationMeter()
    with monkeypatch.context() as patches:
        patches.setattr(os, "scandir", meter.scan)
        cursor = FilesystemVaultInventoryCursor(tmp_path)
        page = cursor.next_page(maximum=9)
        assert not page.exhausted and not page.entries
        assert page.work_units == 9 and meter.opened + meter.advances == 9
        assert meter.live > 0
        cursor.close()
        assert meter.live == 0
        with pytest.raises(StorageOperationError):
            cursor.next_page(maximum=1)


@pytest.mark.parametrize("maximum", [True, False, 0, -1, 101, 1.5])
def test_invalid_budget_does_not_touch_filesystem(tmp_path: Path, maximum: Any) -> None:
    cursor = FilesystemVaultInventoryCursor(tmp_path / "absent")
    try:
        with pytest.raises(ValueError, match="vault_inventory_limit_invalid"):
            cursor.next_page(maximum=maximum)
    finally:
        cursor.close()


@pytest.mark.parametrize("location", ["root", "shard", "object", "staging"])
def test_symlink_is_rejected_and_cannot_become_an_orphan_observation(
    tmp_path: Path, location: str
) -> None:
    root = tmp_path / "vault"
    populate(root, 1)
    external = tmp_path / "external"
    external.mkdir()
    (external / "payload").write_bytes(b"preserve")
    target = root
    if location == "shard":
        target = root / "objects" / "ab" / "cd"
    elif location == "object":
        target = root / "objects" / "ab" / "cd" / f"abcd{0:060x}"
    elif location == "staging":
        target = root / "staging" / "disc-active"
    is_directory = target.is_dir()
    backup = target.with_name(target.name + "-original")
    target.rename(backup)
    try:
        target.symlink_to(external if is_directory else external / "payload", is_directory)
    except OSError:
        backup.rename(target)
        pytest.skip("symlink creation is unavailable on this host")
    cursor = FilesystemVaultInventoryCursor(root)
    try:
        with pytest.raises(StorageSafetyError):
            cursor.next_page(maximum=100)
        with pytest.raises(StorageOperationError):
            cursor.next_page(maximum=100)
        assert (external / "payload").read_bytes() == b"preserve"
    finally:
        cursor.close()


def test_directory_replacement_between_pages_aborts_instead_of_false_completion(
    tmp_path: Path,
) -> None:
    populate(tmp_path, 2)
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    try:
        # The first boundary opens objects before any child name is consumed.
        assert not cursor.next_page(maximum=1).entries
        objects = tmp_path / "objects"
        try:
            objects.rename(tmp_path / "original-objects")
        except PermissionError:
            pytest.skip("host prevents renaming an open enumeration directory")
        objects.mkdir()
        with pytest.raises(StorageSafetyError):
            cursor.next_page(maximum=100)
    finally:
        cursor.close()


def test_disappeared_entry_uses_budget_without_reporting_missing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(tmp_path, 1)
    original = FilesystemVaultInventoryCursor._safe_stat
    vanished = False

    def observe(path: Path, *, directory: bool) -> os.stat_result:
        nonlocal vanished
        if path.name == "disc-active":
            vanished = True
            raise FileNotFoundError()
        return original(path, directory=directory)

    monkeypatch.setattr(FilesystemVaultInventoryCursor, "_safe_stat", staticmethod(observe))
    cursor = FilesystemVaultInventoryCursor(tmp_path)
    try:
        page = cursor.next_page(maximum=100)
        assert vanished and page.exhausted
        assert "disc-active" not in {item.key.value for item in page.entries}
        assert (tmp_path / "staging" / "disc-active").exists()
    finally:
        cursor.close()


def test_nested_scan_failure_closes_iterators_and_poisons_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    populate(tmp_path, 1)
    (tmp_path / "objects" / "ab" / "cd" / ("abcd" + "g" * 60)).write_bytes(b"unsafe-key")
    meter = EnumerationMeter()
    with monkeypatch.context() as patches:
        patches.setattr(os, "scandir", meter.scan)
        cursor = FilesystemVaultInventoryCursor(tmp_path)
        with pytest.raises(StorageSafetyError):
            cursor.next_page(maximum=100)
        assert meter.high_water == 3 and meter.live == 0
        with pytest.raises(StorageOperationError):
            cursor.next_page(maximum=100)
        cursor.close()
