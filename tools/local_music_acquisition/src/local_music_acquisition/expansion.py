"""Resumable related-track queues; original queue and immutable receipts stay intact."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .matching import recording_identity
from .models import PlaylistItem, ProviderFailure
from .normalization import normalize_items, search_variants
from .orchestrator import PlaylistDownloadError
from .providers.base import AcquisitionProvider
from .queue import _directory, _key, _load, _state, enqueue, run_queue
from .queue_store import exclusive_lock, inspect_receipt, read_json, write_json
from .related import POLICY_VERSION, RelatedCandidate, candidate_score, select_candidates


def _existing(output: Path) -> dict[tuple[frozenset[str], str], PlaylistItem]:
    known: dict[tuple[frozenset[str], str], PlaylistItem] = {}
    for path in sorted((output / "tracks").glob("*/receipt.json")):
        receipt = inspect_receipt(path.parent, path.parent.name)
        item = PlaylistItem(**receipt["item"])
        if _key(item) != path.parent.name:
            raise PlaylistDownloadError("expansion_existing_identity_invalid")
        known.setdefault(recording_identity(item.artist, item.title), item)
    return known


def _discover(
    item: PlaylistItem,
    providers: tuple[AcquisitionProvider, ...],
    limit: int,
    stop: threading.Event,
) -> tuple[list[RelatedCandidate], list[dict[str, Any]]]:
    candidates: list[RelatedCandidate] = []
    attempts: list[dict[str, Any]] = []
    for query in search_variants(item):
        for provider in providers:
            discover = getattr(provider, "discover", None)
            if not callable(discover) or stop.is_set():
                continue
            try:
                found = discover(query)
            except ProviderFailure as error:
                attempts.append({"provider": provider.name, "code": error.code, "failed": True})
                continue
            candidates.extend(found)
            attempts.append({"provider": provider.name, "candidates": len(found), "failed": False})
            selected = select_candidates(item, candidates, limit=limit)
            exact = any(
                recording_identity(c.artist, c.title) == recording_identity(item.artist, item.title)
                for c in selected
            )
            if exact:
                return selected, attempts
    return select_candidates(item, candidates, limit=limit), attempts


def run_expansion(
    source: Path,
    root: Path,
    output: Path,
    *,
    providers: tuple[AcquisitionProvider, ...],
    rights_confirmed: frozenset[str],
    stop: threading.Event | None = None,
    candidate_limit: int = 3,
    parent_limit: int = 10000,
    parent_keys: tuple[str, ...] = (),
    normalization_catalog: Path | None = None,
    workers: int = 2,
    max_bytes: int = 200 * 1024 * 1024,
    index_recheck_seconds: int = 86400,
    retry_seconds: int = 60,
) -> dict[str, Any]:
    if not 1 <= candidate_limit <= 3 or not 1 <= parent_limit <= 10000:
        raise PlaylistDownloadError("expansion_policy_invalid")
    if source.resolve() == root.resolve():
        raise PlaylistDownloadError("expansion_requires_new_queue")
    for provider in providers:
        if provider.requires_rights_confirmation and provider.name not in rights_confirmed:
            raise PlaylistDownloadError(f"{provider.name}_rights_confirmation_required")
    if not any(callable(getattr(p, "discover", None)) for p in providers):
        raise PlaylistDownloadError("discovery_providers_empty")
    root, output = _directory(root), _directory(output)
    original = _load(source)
    if set(parent_keys) - set(original["jobs"]):
        raise PlaylistDownloadError("expansion_parent_unknown")
    stop = stop or threading.Event()
    policy = {
        "schema_version": 1,
        "policy": POLICY_VERSION,
        "source": str(source.resolve()),
        "source_sha256": hashlib.sha256((source / "queue.json").read_bytes()).hexdigest(),
        "output": str(output),
        "candidate_limit": candidate_limit,
        "providers": [provider.name for provider in providers],
        "normalization_sha256": hashlib.sha256(normalization_catalog.read_bytes()).hexdigest()
        if normalization_catalog is not None
        else None,
    }
    with exclusive_lock(root / "expansion.lock"):
        manifest = root / "expansion.json"
        if manifest.exists():
            if read_json(manifest) != policy:
                raise PlaylistDownloadError("expansion_policy_changed_use_new_directory")
        else:
            write_json(manifest, policy)
        known = _existing(output)
        for saved_path in (root / "parents").glob("*/expansion.json"):
            saved = read_json(saved_path)
            for record in saved.get("selected", []):
                frozen = PlaylistItem(**record["item"])
                if _key(frozen) != record["key"]:
                    raise PlaylistDownloadError("expansion_plan_invalid")
                known.setdefault(recording_identity(frozen.artist, frozen.title), frozen)
        processed = 0
        for parent_key, fields in original["jobs"].items():
            if stop.is_set() or (root / "pause").exists() or processed >= parent_limit:
                break
            if parent_keys and parent_key not in parent_keys:
                continue
            if _state(source, parent_key)["state"] not in {"not_found", "failed"}:
                continue
            parent_root = root / "parents" / parent_key
            state_path = parent_root / "expansion.json"
            state: dict[str, Any] = (
                read_json(state_path) if state_path.exists() else {"attempts": 0, "phase": "new"}
            )
            if state["phase"] in {"complete", "no_candidates", "discovery_failed"}:
                continue
            if state.get("next_retry", 0) > time.time():
                continue
            processed += 1
            if state["phase"] != "planned":
                original_item = PlaylistItem(**fields)
                normalized, _changes = normalize_items(
                    (original_item,), catalog_path=normalization_catalog
                )
                selected, attempts = _discover(normalized[0], providers, candidate_limit, stop)
                if stop.is_set():
                    break
                state = {
                    "attempts": state["attempts"] + 1,
                    "phase": "planned",
                    "original": fields,
                    "parent_key": parent_key,
                    "providers": attempts,
                    "selected": [],
                    "policy": POLICY_VERSION,
                }
                if not selected:
                    failed = any(a["failed"] for a in attempts)
                    state["phase"] = (
                        ("retry_discovery" if state["attempts"] < 3 else "discovery_failed")
                        if failed
                        else "no_candidates"
                    )
                    state["next_retry"] = time.time() + retry_seconds * 2 ** (state["attempts"] - 1)
                for candidate in selected:
                    identity = recording_identity(candidate.artist, candidate.title)
                    item = known.get(identity) or candidate.item(len(state["selected"]) + 1)
                    known.setdefault(identity, item)
                    state["selected"].append(
                        {
                            "candidate": asdict(candidate),
                            "item": asdict(item),
                            "key": _key(item),
                            "score": candidate_score(normalized[0], candidate),
                        }
                    )
                write_json(state_path, state)
            if state["phase"] != "planned":
                continue
            playlist = parent_root / "candidates.txt"
            payload = "".join(
                f"{r['item']['artist']}\t{r['item']['title']}"
                + (f"\t{r['item']['album']}" if r["item"].get("album") else "")
                + "\n"
                for r in state["selected"]
            )
            if playlist.exists():
                if playlist.read_text(encoding="utf-8") != payload:
                    raise PlaylistDownloadError("expansion_plan_changed")
            else:
                temporary = playlist.with_suffix(".tmp")
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(playlist)
            queue = parent_root / "queue"
            enqueue(playlist, queue, output)
            result = run_queue(
                queue,
                providers=providers,
                rights_confirmed=rights_confirmed,
                stop=stop,
                max_workers=workers,
                retry_seconds=retry_seconds,
                max_bytes=max_bytes,
                index_recheck_seconds=index_recheck_seconds,
            )
            state["result"] = result
            if result["state"] == "finished":
                state["phase"] = "complete"
            write_json(state_path, state)
            print(
                json.dumps(
                    {
                        "expansion_parent": parent_key[:12],
                        "candidates": len(state["selected"]),
                        "downloaded": result["downloaded"],
                        "phase": state["phase"],
                    }
                ),
                flush=True,
            )
        counts: Counter[str] = Counter()
        ready: set[str] = set()
        for path in (root / "parents").glob("*/expansion.json"):
            if parent_keys and path.parent.name not in parent_keys:
                continue
            state = read_json(path)
            counts[state["phase"]] += 1
            for record in state.get("selected", []):
                queue = path.parent / "queue"
                if _state(queue, record["key"])["state"] == "downloaded":
                    ready.add(record["key"])
        eligible = [
            k
            for k in original["jobs"]
            if (not parent_keys or k in parent_keys)
            and _state(source, k)["state"] in {"not_found", "failed"}
        ]
        summary = {
            "policy": POLICY_VERSION,
            "processed_this_pass": processed,
            "parents": dict(counts),
            "eligible_parents": len(eligible),
            "ready_distinct_candidates": len(ready),
            "paused": (root / "pause").exists(),
            "stopped": stop.is_set(),
            "remaining_parents": len(eligible)
            - counts["complete"]
            - counts["no_candidates"]
            - counts["discovery_failed"],
        }
        write_json(root / "runtime.json", summary)
        return summary
