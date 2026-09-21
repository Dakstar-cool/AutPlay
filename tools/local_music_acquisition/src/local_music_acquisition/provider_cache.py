"""Expiring exact-search misses, separate from failures and verified audio receipts."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

from .models import AcquiredArtifact, PlaylistItem, ProviderMiss
from .orchestrator import PlaylistDownloadError
from .providers.base import AcquisitionProvider
from .queue_store import read_json, write_json


def item_key(item: PlaylistItem) -> str:
    identity = [
        " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())
        for value in (item.artist, item.title, item.album)
    ]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def cache_namespace(settings: dict[str, object], inputs: list[Path]) -> str:
    """Invalidate on implementation, matching settings, catalogs or credential changes.

    Only one combined digest is persisted; input bytes and individual secret hashes
    are never returned or logged. Cache storage contains no track titles or URLs.
    """
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())
    package = Path(__file__).parent
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).replace("\\", "/").encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    for path in inputs:
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
    return digest.hexdigest()


class ProviderMissCache:
    """One bounded record per queue identity; use under the queue's exclusive lock."""

    def __init__(self, root: Path, namespace: str, ttl_seconds: int) -> None:
        if len(namespace) != 64 or any(c not in "0123456789abcdef" for c in namespace):
            raise PlaylistDownloadError("miss_cache_namespace_invalid")
        if not 1 <= ttl_seconds <= 86400:
            raise PlaylistDownloadError("miss_cache_ttl_invalid")
        self.root = root
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
        self.hits = 0
        self.stored = 0
        self.invalid = 0
        self.enabled = True

    def _record(self, item: PlaylistItem) -> dict[str, object]:
        identity = asdict(item)
        identity.pop("row_number")
        signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        empty: dict[str, object] = {
            "schema_version": 1,
            "namespace": self.namespace,
            "identity": signature,
            "providers": {},
        }
        path = self.root / f"{item_key(item)}.json"
        try:
            if not path.exists():
                return empty
            record = read_json(path, max_bytes=16384)
        except (OSError, PlaylistDownloadError):
            self.invalid += 1
            return empty
        if any(record.get(k) != empty[k] for k in ("schema_version", "namespace", "identity")):
            return empty
        providers = record.get("providers")
        if not isinstance(providers, dict) or len(providers) > 16:
            self.invalid += 1
            return empty
        return record

    def contains(self, provider: str, item: PlaylistItem) -> bool:
        with self.lock:
            if not self.enabled:
                return False
            providers = self._record(item)["providers"]
            assert isinstance(providers, dict)
            entry = providers.get(provider)
            if not isinstance(entry, dict) or entry.get("code") != "exact_match_not_found":
                return False
            checked = entry.get("checked_unix")
            if not isinstance(checked, (int, float)) or not math.isfinite(checked):
                return False
            if not 0 <= time.time() - checked < self.ttl_seconds:
                return False
            self.hits += 1
            return True

    def remember(self, provider: str, item: PlaylistItem) -> None:
        with self.lock:
            if not self.enabled:
                return
            record = self._record(item)
            providers = record["providers"]
            assert isinstance(providers, dict)
            providers[provider] = {"code": "exact_match_not_found", "checked_unix": time.time()}
            try:
                write_json(self.root / f"{item_key(item)}.json", record)
            except (OSError, PlaylistDownloadError):
                # Optional optimization must not alter the provider's genuine outcome.
                self.invalid += 1
                self.enabled = False
                return
            self.stored += 1

    def summary(self) -> dict[str, int]:
        with self.lock:
            return {"hits": self.hits, "stored": self.stored, "invalid": self.invalid}


class CachedProvider:
    def __init__(self, provider: AcquisitionProvider, cache: ProviderMissCache) -> None:
        self.provider = provider
        self.cache = cache
        self.name = provider.name
        self.requires_rights_confirmation = provider.requires_rights_confirmation
        self.requires_proxy = getattr(provider, "requires_proxy", False)
        self.max_parallelism = getattr(provider, "max_parallelism", 1)

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        try:
            return self.provider.acquire(item, output_directory)
        except ProviderMiss as error:
            if error.provider == self.name and error.code == "exact_match_not_found":
                self.cache.remember(self.name, item)
            raise

    def cached_miss(self, item: PlaylistItem) -> bool:
        return self.cache.contains(self.name, item)
