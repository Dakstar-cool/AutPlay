"""Resumable file-only queue. AutPlay database and Vault authority are deliberately separate."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .download_index import DownloadIndex
from .models import AcquiredArtifact, PlaylistItem, ProviderFailure
from .normalization import normalize_items
from .orchestrator import DownloadSession, PlaylistDownloadError
from .playlist import MAX_IMPORT_BYTES, normalize_numbered_collection, parse_playlist
from .providers.base import AcquisitionProvider
from .queue_store import (
    audio_receipt,
    exclusive_lock,
    make_directory,
    read_json,
    sync_directory,
    verify_receipt,
    write_json,
)


def _key(item: PlaylistItem) -> str:
    identity = [
        " ".join(unicodedata.normalize("NFKC", field or "").casefold().split())
        for field in (item.artist, item.title, item.album)
    ]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def _directory(path: Path) -> Path:
    absolute = path.absolute()
    if any(part.is_symlink() or part.is_junction() for part in (absolute, *absolute.parents)):
        raise PlaylistDownloadError("queue_link_directory_rejected")
    make_directory(absolute)
    return absolute.resolve()


def enqueue(
    input_file: Path,
    root: Path,
    output: Path,
    *,
    normalize: bool = False,
    normalization_catalog: Path | None = None,
) -> dict[str, Any]:
    with input_file.open("rb") as handle:
        payload = handle.read(MAX_IMPORT_BYTES + 1)
    if normalize:
        payload, _stats = normalize_numbered_collection(payload)
    parsed = parse_playlist(payload)
    rows, changes = normalize_items(parsed.rows, catalog_path=normalization_catalog)
    jobs = {_key(item): asdict(item) for item in rows if item.error_code is None}
    for item_data in jobs.values():
        if item_data.get("expected_duration_seconds") is None:
            item_data.pop("expected_duration_seconds", None)
    if not jobs:
        raise PlaylistDownloadError("queue_has_no_valid_tracks")
    root, output = _directory(root), _directory(output)
    document = {
        "schema_version": 1,
        "playlist_sha256": hashlib.sha256(payload).hexdigest(),
        "output": str(output),
        "requested": len(parsed.rows),
        "malformed": sum(item.error_code is not None for item in rows),
        "duplicates": sum(item.error_code is None for item in rows) - len(jobs),
        "jobs": jobs,
    }
    with exclusive_lock(root / "queue.lock"):
        manifest = root / "queue.json"
        if manifest.exists():
            if read_json(manifest, max_bytes=32 * 1024 * 1024) != document:
                raise PlaylistDownloadError("queue_input_changed_use_new_queue_directory")
        else:
            write_json(manifest, document)
        write_json(root / "normalization.json", {"version": 1, "changes": changes})
    return queue_status(root)


def _load(root: Path) -> dict[str, Any]:
    document = read_json(root / "queue.json", max_bytes=32 * 1024 * 1024)
    if document.get("schema_version") != 1 or not isinstance(document.get("jobs"), dict):
        raise PlaylistDownloadError("queue_manifest_invalid")
    if not 1 <= len(document["jobs"]) <= 10_000:
        raise PlaylistDownloadError("queue_manifest_invalid")
    try:
        for key, item in document["jobs"].items():
            if _key(PlaylistItem(**item)) != key:
                raise ValueError
        if not Path(document["output"]).is_absolute():
            raise ValueError
    except (TypeError, ValueError, KeyError) as error:
        raise PlaylistDownloadError("queue_manifest_invalid") from error
    return document


def _state(root: Path, key: str) -> dict[str, Any]:
    path = root / "jobs" / f"{key}.json"
    if not path.exists():
        return {"state": "pending", "attempts": 0}
    state = read_json(path)
    if state.get("state") not in {
        "pending",
        "running",
        "retry",
        "downloaded",
        "not_found",
        "failed",
        "needs_review",
    } or not isinstance(state.get("attempts"), int):
        raise PlaylistDownloadError("queue_job_state_invalid")
    return state


def queue_status(root: Path) -> dict[str, Any]:
    document = _load(root)
    counts = dict.fromkeys(
        ("pending", "running", "retry", "downloaded", "not_found", "failed", "needs_review"), 0
    )
    next_retry: float | None = None
    for key in document["jobs"]:
        state = _state(root, key)
        counts[state["state"]] += 1
        if state["state"] == "retry":
            next_retry = min(next_retry or float("inf"), float(state["next_retry"]))
    complete = not (counts["pending"] or counts["running"] or counts["retry"])
    return {
        "state": "finished" if complete else "pending",
        "requested": document["requested"],
        "unique_tracks": len(document["jobs"]),
        "duplicates": document["duplicates"],
        "malformed": document["malformed"],
        **counts,
        "next_retry_unix": next_retry,
        "paused": (root / "pause").exists(),
        "imported_to_autplay": False,
    }


class _VerifiedProvider:
    def __init__(self, provider: AcquisitionProvider, max_bytes: int) -> None:
        self.provider = provider
        self.name = provider.name
        self.requires_rights_confirmation = provider.requires_rights_confirmation
        self.requires_proxy = getattr(provider, "requires_proxy", False)
        self.max_bytes = max_bytes

    def acquire(self, item: PlaylistItem, output_directory: Path) -> AcquiredArtifact:
        directory = output_directory / self.name
        make_directory(directory)
        artifact = self.provider.acquire(item, directory)
        try:
            receipt = audio_receipt(
                directory,
                max_bytes=self.max_bytes,
                expected_seconds=item.expected_duration_seconds
                or artifact.expected_duration_seconds,
            )
            if artifact.provider != self.name or artifact.artifact_ref != (
                f"sha256:{str(receipt['sha256'])[:12]}"
            ):
                raise PlaylistDownloadError("queue_provider_artifact_mismatch")
            receipt.update(
                {
                    "schema_version": 1,
                    "key": _key(item),
                    "provider": self.name,
                    "item": asdict(item),
                    "identity_version": artifact.identity_version,
                }
            )
            write_json(output_directory / "receipt.json", receipt)
        except PlaylistDownloadError as error:
            raise ProviderFailure(self.name, str(error)) from error
        return artifact


def _publish(stage: Path, destination: Path, key: str) -> None:
    receipt = verify_receipt(stage, key)
    # Preserve unsuccessful provider artifacts for diagnosis, outside the committed music tree.
    for entry in stage.iterdir():
        if entry.name not in {"receipt.json", receipt["provider"]}:
            quarantine = stage.parent / "quarantine" / stage.name
            make_directory(quarantine)
            entry.rename(quarantine / entry.name)
            sync_directory(quarantine)
    sync_directory(stage)
    make_directory(destination.parent)
    if destination.exists():
        raise PlaylistDownloadError("queue_output_conflict")
    stage.rename(destination)
    sync_directory(destination.parent)
    sync_directory(stage.parent)


def _staging_root(root: Path, output: Path, key: str) -> Path:
    namespace = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return output / ".acquire" / namespace / key


def _reconcile(root: Path, output: Path, key: str, index: DownloadIndex) -> dict[str, Any]:
    state = _state(root, key)
    before = state.copy()
    destination = output / "tracks" / key
    try:
        if destination.exists():
            index.verify(key, force=state["state"] == "needs_review")
            state.update(state="downloaded", error_code=None)
        else:
            prepared = sorted(_staging_root(root, output, key).glob("*/receipt.json"))
            if not prepared and state["attempts"] > 0:
                # Recover a prepared receipt written by the earlier queue format.
                prepared = sorted((output / ".acquire" / key).glob("*/receipt.json"))
            if prepared:
                _publish(prepared[0].parent, destination, key)
                index.verify(key)
                state.update(state="downloaded", error_code=None)
            elif state["state"] == "downloaded":
                raise PlaylistDownloadError("queue_artifact_missing")
            elif state["state"] == "running":
                state.update(state="retry", next_retry=0, error_code="worker_interrupted")
    except PlaylistDownloadError as error:
        state.update(state="needs_review", error_code=str(error))
    if state != before:
        write_json(root / "jobs" / f"{key}.json", state)
    return state


def _process(
    root: Path,
    output: Path,
    item: PlaylistItem,
    session: DownloadSession,
    *,
    max_attempts: int,
    retry_seconds: int,
    max_bytes: int,
    index: DownloadIndex,
) -> None:
    key = _key(item)
    state = _state(root, key)
    state_path = root / "jobs" / f"{key}.json"
    used_attempts = state["attempts"] - state.get("attempt_budget_reset", 0)
    if used_attempts >= max_attempts:
        state.update(state="failed", error_code="attempts_exhausted")
        write_json(state_path, state)
        return
    # Reserve enough space for temporary and final provider bytes before any network request.
    if shutil.disk_usage(output).free < max_bytes * 3 + 64 * 1024 * 1024:
        raise PlaylistDownloadError("queue_disk_space_low")
    state.update(state="running", attempts=state["attempts"] + 1, updated_unix=time.time())
    write_json(state_path, state)
    # Independent recovery queues may target the same item after an earlier miss.
    stage = _staging_root(root, output, key) / str(state["attempts"])
    if stage.exists():
        raise PlaylistDownloadError("queue_attempt_directory_conflict")
    make_directory(stage)
    outcome = session.download(item, stage)
    state.update(error_code=outcome.error_code, provider=outcome.provider, updated_unix=time.time())
    if outcome.status == "downloaded":
        _publish(stage, output / "tracks" / key, key)
        index.verify(key)
        state.update(state="downloaded")
    elif outcome.status == "not_found":
        state.update(state="not_found")
    elif outcome.deferred:
        # attempts is also the staging sequence. Credit only an explicitly deferred
        # result, never a last error string that could hide a real earlier failure.
        state.update(
            state="retry",
            attempt_budget_reset=state.get("attempt_budget_reset", 0) + 1,
            next_retry=time.time() + retry_seconds,
        )
    elif used_attempts + 1 < max_attempts:
        state.update(
            state="retry",
            next_retry=time.time() + min(3600, retry_seconds * 2**used_attempts),
        )
    else:
        state.update(state="failed")
    write_json(state_path, state)


def run_queue(
    root: Path,
    *,
    providers: tuple[AcquisitionProvider, ...],
    rights_confirmed: frozenset[str] = frozenset(),
    max_workers: int = 2,
    max_attempts: int = 3,
    retry_seconds: int = 60,
    max_bytes: int = 200 * 1024 * 1024,
    stop: threading.Event | None = None,
    index_recheck_seconds: int = 86400,
) -> dict[str, Any]:
    if not 1 <= max_workers <= 4 or not 1 <= max_attempts <= 10 or not 1 <= retry_seconds <= 3600:
        raise PlaylistDownloadError("queue_policy_invalid")
    if not 1024 <= max_bytes <= 1024 * 1024 * 1024:
        raise PlaylistDownloadError("queue_max_bytes_invalid")
    session = DownloadSession(
        tuple(_VerifiedProvider(provider, max_bytes) for provider in providers),
        rights_confirmed,
        cooldown_seconds=retry_seconds,
    )
    root = _directory(root)
    document = _load(root)
    output = _directory(Path(document["output"]))
    stop = stop or threading.Event()
    started = time.monotonic()
    with (
        exclusive_lock(root / "queue.lock"),
        exclusive_lock(output / ".acquisition.lock"),
        closing(DownloadIndex(output, recheck_seconds=index_recheck_seconds)) as index,
    ):
        ready: list[PlaylistItem] = []
        for key, item in document["jobs"].items():
            state = _reconcile(root, output, key, index)
            if state["state"] == "pending" or (
                state["state"] == "retry" and state.get("next_retry", 0) <= time.time()
            ):
                ready.append(PlaylistItem(**item))
        items = iter(ready)
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="server-acquire"
        ) as pool:
            active: set[Future[None]] = set()
            exhausted = False
            while active or not exhausted:
                while len(active) < max_workers and not exhausted:
                    if stop.is_set() or (root / "pause").exists() or not session.available:
                        exhausted = True
                        break
                    item = next(items, None)
                    if item is None:
                        exhausted = True
                        break
                    active.add(
                        pool.submit(
                            _process,
                            root,
                            output,
                            item,
                            session,
                            max_attempts=max_attempts,
                            retry_seconds=retry_seconds,
                            max_bytes=max_bytes,
                            index=index,
                        )
                    )
                if active:
                    done, active = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
        write_json(
            root / "runtime.json",
            {
                "schema_version": 1,
                "finished_unix": time.time(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "workers": max_workers,
                "providers": session.metrics(),
                "index": index.summary(),
            },
        )
    return queue_status(root)


def retry_unsuccessful(root: Path, *, include_not_found: bool = False) -> dict[str, Any]:
    document = _load(root)
    with exclusive_lock(root / "queue.lock"):
        for key in document["jobs"]:
            state = _state(root, key)
            if state["state"] in {"failed", "retry"} or (
                include_not_found and state["state"] == "not_found"
            ):
                # Never reuse attempt directories or reset their monotonic sequence number.
                state.update(state="pending", attempts=state["attempts"], next_retry=0)
                state["attempt_budget_reset"] = state["attempts"]
                write_json(root / "jobs" / f"{key}.json", state)
    return queue_status(root)


def verify_downloads(root: Path) -> dict[str, Any]:
    """Force byte verification and refresh this queue's index without network calls."""
    root = _directory(root)
    document = _load(root)
    output = _directory(Path(document["output"]))
    with (
        exclusive_lock(root / "queue.lock"),
        exclusive_lock(output / ".acquisition.lock"),
        closing(DownloadIndex(output, recheck_seconds=0)) as index,
    ):
        for key in document["jobs"]:
            _reconcile(root, output, key, index)
        return {**queue_status(root), "index": index.summary()}
