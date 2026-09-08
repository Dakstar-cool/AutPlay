from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from yandex_music import Artist, Track

from local_music_acquisition.models import PlaylistItem, ProviderFailure, ProviderMiss
from local_music_acquisition.providers import yandex_provider
from local_music_acquisition.providers.yandex_provider import YandexProvider


def _track(artist: str = "Artist", title: str = "Title") -> Track:
    return Track("1", title=title, artists=[Artist("1", name=artist)])


def _provider(client: object) -> YandexProvider:
    provider = YandexProvider(Path("unused-token"))
    provider._client = client  # type: ignore[assignment]
    return provider


def test_yandex_download_uses_pinned_library_and_publishes_exact_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    track = _track()
    search = SimpleNamespace(tracks=SimpleNamespace(results=[track]))
    client = SimpleNamespace(search=lambda *_args, **_kwargs: search)
    payload = b"ID3" + b"x" * 2048
    monkeypatch.setattr(
        yandex_provider.ymd_core,
        "to_downloadable_track",
        lambda *_args, **_kwargs: SimpleNamespace(
            path=Path("download.mp3"), download_info=object()
        ),
    )
    monkeypatch.setattr(
        yandex_provider.ymd_api, "download_track", lambda *_args, **_kwargs: payload
    )

    artifact = _provider(client).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)

    destination = tmp_path / "Artist - Title.mp3"
    assert destination.read_bytes() == payload
    assert artifact.artifact_ref == ("sha256:" + hashlib.sha256(payload).hexdigest()[:12])


def test_yandex_rejects_non_exact_search_result(tmp_path: Path) -> None:
    search = SimpleNamespace(tracks=SimpleNamespace(results=[_track(title="Title live")]))
    client = SimpleNamespace(search=lambda *_args, **_kwargs: search)

    with pytest.raises(ProviderMiss, match=r"yandex\.exact_match_not_found"):
        _provider(client).acquire(PlaylistItem(1, "Artist", "Title"), tmp_path)


def test_yandex_token_errors_are_stable_and_do_not_echo_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "this-token-must-never-appear"
    token_file = tmp_path / "token"
    token_file.write_text(secret, encoding="utf-8")
    if yandex_provider.os.name != "nt":
        token_file.chmod(0o600)
    monkeypatch.setattr(
        yandex_provider.ymd_core,
        "init_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(ProviderFailure) as captured:
        YandexProvider(token_file)._client_or_raise()

    assert str(captured.value) == "yandex.initialization_failed"
    assert secret not in str(captured.value)
