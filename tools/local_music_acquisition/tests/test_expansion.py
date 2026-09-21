from __future__ import annotations

import hashlib
import io
import threading
import wave

import pytest

from local_music_acquisition import expansion, queue
from local_music_acquisition.models import AcquiredArtifact, PlaylistItem, ProviderFailure
from local_music_acquisition.queue_store import read_json, write_json
from local_music_acquisition.related import RelatedCandidate


class Provider:
    name = "fixture"
    requires_rights_confirmation = False

    def __init__(self, candidates=None, failure=False):
        self.candidates = candidates or []
        self.failure = failure
        self.acquisitions = 0
        self.discoveries = 0

    def discover(self, item):
        self.discoveries += 1
        if self.failure:
            raise ProviderFailure(self.name, "timeout")
        return self.candidates

    def acquire(self, item, directory):
        self.acquisitions += 1
        payload = io.BytesIO()
        with wave.open(payload, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\0\0" * 96000)
        data = payload.getvalue()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "track.wav").write_bytes(data)
        return AcquiredArtifact(self.name, "sha256:" + hashlib.sha256(data).hexdigest()[:12])


def prepare(tmp_path, text="Artist - The Son\nArtist - The Sonn\n"):
    playlist = tmp_path / "original.txt"
    playlist.write_text(text, encoding="utf-8")
    source, output = tmp_path / "original", tmp_path / "music"
    queue.enqueue(playlist, source, output)
    for key in queue._load(source)["jobs"]:
        write_json(source / "jobs" / f"{key}.json", {"state": "not_found", "attempts": 1})
    return source, output


def invoke(source, root, output, provider, **kwargs):
    return expansion.run_expansion(
        source, root, output, providers=(provider,), rights_confirmed=frozenset(), **kwargs
    )


def test_distinct_variants_replay_and_multiple_parents_reuse_audio(tmp_path):
    source, output = prepare(tmp_path)
    before = {str(p): p.read_bytes() for p in source.rglob("*.json")}
    provider = Provider(
        [
            RelatedCandidate("fixture", "Artist", title)
            for title in ("The Song", "The Song (Live)", "The Song (Remix)", "The Song (Acoustic)")
        ]
    )
    root = tmp_path / "expanded"
    first = invoke(source, root, output, provider)
    assert first["ready_distinct_candidates"] == 3
    assert provider.acquisitions == 3
    assert first["parents"] == {"complete": 2}
    second = invoke(source, root, output, provider)
    assert second["ready_distinct_candidates"] == 3
    assert second["processed_this_pass"] == 0
    assert provider.acquisitions == 3
    assert {str(p): p.read_bytes() for p in source.rglob("*.json")} == before
    receipts = [read_json(p) for p in output.glob("tracks/*/receipt.json")]
    assert len({r["item"]["title"] for r in receipts}) == 3
    assert all(r["item"]["album"] is None for r in receipts)


def test_discovery_failure_is_retried_without_becoming_not_found(tmp_path):
    source, output = prepare(tmp_path, "Artist - The Son\n")
    provider = Provider(failure=True)
    root = tmp_path / "expanded"
    result = invoke(source, root, output, provider)
    assert result["parents"] == {"retry_discovery": 1}
    calls = provider.discoveries
    invoke(source, root, output, provider)
    assert provider.discoveries == calls
    path = next(root.glob("parents/*/expansion.json"))
    for expected in ("retry_discovery", "discovery_failed"):
        state = read_json(path)
        state["next_retry"] = 0
        write_json(path, state)
        assert invoke(source, root, output, provider)["parents"] == {expected: 1}


def test_recover_after_child_publication_does_not_download_twice(tmp_path, monkeypatch):
    source, output = prepare(tmp_path, "Artist - The Son\n")
    provider = Provider([RelatedCandidate("fixture", "Artist", "The Song")])
    root = tmp_path / "expanded"
    original = expansion.write_json

    def interrupted(path, document):
        if path.name == "expansion.json" and document.get("phase") == "complete":
            raise InterruptedError("test interruption")
        original(path, document)

    monkeypatch.setattr(expansion, "write_json", interrupted)
    with pytest.raises(InterruptedError):
        invoke(source, root, output, provider)
    monkeypatch.setattr(expansion, "write_json", original)
    result = invoke(source, root, output, provider)
    assert result["ready_distinct_candidates"] == 1
    assert provider.acquisitions == 1
    assert provider.discoveries == 1


def test_existing_exact_identity_preserves_metadata_and_reuses_file(tmp_path):
    source, output = prepare(tmp_path, "Artist, Guest - The Sonn\n")
    known = tmp_path / "known.txt"
    known.write_text("Guest, Artist\tThe Song\tReal Album\n")
    queue.enqueue(known, tmp_path / "existing", output)
    provider = Provider([RelatedCandidate("fixture", "Artist, Guest", "The Song")])
    queue.run_queue(tmp_path / "existing", providers=(provider,))
    result = invoke(source, tmp_path / "expanded", output, provider)
    assert result["ready_distinct_candidates"] == 1
    assert provider.acquisitions == 1
    receipt = read_json(next(output.glob("tracks/*/receipt.json")))
    assert receipt["item"]["album"] == "Real Album"


def test_parent_filter_finishes_only_requested_scope(tmp_path):
    source, output = prepare(tmp_path)
    key = next(iter(queue._load(source)["jobs"]))
    provider = Provider([RelatedCandidate("fixture", "Artist", "The Song")])
    result = invoke(source, tmp_path / "expanded", output, provider, parent_keys=(key,))
    assert result["eligible_parents"] == 1
    assert result["remaining_parents"] == 0


def test_later_exact_match_takes_priority_over_three_fuzzy_candidates():
    first = Provider(
        [
            RelatedCandidate("fixture", "Artist", f"The Song ({version})")
            for version in ("Live", "Remix", "Acoustic")
        ]
    )
    second = Provider([RelatedCandidate("fixture", "Artist", "The Song")])
    candidates, _attempts = expansion._discover(
        PlaylistItem(1, "Artist", "The Song"), (first, second), 3, threading.Event()
    )
    assert [c.title for c in candidates] == ["The Song"]
    assert second.discoveries == 1


def test_restart_recovers_dedup_from_planned_retry_before_receipt(tmp_path):
    source, output = prepare(tmp_path)
    keys = list(queue._load(source)["jobs"])
    root = tmp_path / "expanded"

    class RetryProvider(Provider):
        fail_audio = True

        def acquire(self, item, directory):
            if self.fail_audio:
                self.acquisitions += 1
                raise ProviderFailure(self.name, "timeout")
            return super().acquire(item, directory)

    provider = RetryProvider([RelatedCandidate("fixture", "Artist", "The Song")])
    invoke(source, root, output, provider, parent_keys=(keys[0],))
    first = read_json(root / "parents" / keys[0] / "expansion.json")
    assert first["phase"] == "planned"
    provider.candidates = [RelatedCandidate("fixture", "ARTIST", "THE SONG")]
    provider.fail_audio = False
    invoke(source, root, output, provider, parent_keys=(keys[1],))
    second = read_json(root / "parents" / keys[1] / "expansion.json")
    assert first["selected"][0]["key"] == second["selected"][0]["key"]
    invoke(source, root, output, provider)
    assert provider.acquisitions == 2
    assert len(list(output.glob("tracks/*/receipt.json"))) == 1


def test_malformed_hitmo_discovery_is_provider_failure(monkeypatch):
    from local_music_acquisition.providers import hitmo
    from local_music_acquisition.providers.hitmo_provider import HitmoProvider

    def malformed(**kwargs):
        kwargs["candidate_sink"].append(
            {"artist": "Artist", "title": "The Song", "duration_seconds": 0}
        )
        return {"results": [{"status": "matched"}]}

    monkeypatch.setattr(hitmo, "download_hitmo_tracks", malformed)
    with pytest.raises(ProviderFailure, match="discovery_candidate_invalid"):
        HitmoProvider(cdp_endpoint="http://127.0.0.1:9222").discover(
            PlaylistItem(1, "Artist", "The Son")
        )
