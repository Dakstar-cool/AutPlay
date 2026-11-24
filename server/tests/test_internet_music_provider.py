from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from autplay.adapters import internet_music
from autplay.adapters import youtube_music_catalog as catalog
from autplay.adapters.filesystem.provider_child import ProviderChildError
from autplay.adapters.internet_music import InternetMusicProvider, InternetMusicProviderError
from autplay.domain.acquisition_policy import track_review_reasons


def test_search_requires_an_available_catalog_audio_track(monkeypatch: pytest.MonkeyPatch) -> None:
    song = dict(
        videoId="123456789ab",
        title="Live Forever",
        resultType="song",
        videoType="MUSIC_VIDEO_TYPE_ATV",
        artists=[{"name": "Oasis"}],
        album={"name": "Definitely Maybe"},
        duration_seconds=276,
    )

    def search(query: str, *, filter: str, limit: int) -> list[dict[str, object]]:
        assert (query, filter, limit) == ("Oasis", "songs", 50)
        return [
            song | {"resultType": "video"},
            song | {"videoType": "MUSIC_VIDEO_TYPE_UGC"},
            song | {"artists": []},
            song | {"album": {"name": "Live at Wembley"}},
            song | {"isAvailable": False},
            song | {"duration_seconds": 1201},
            song,
            song,
        ]

    monkeypatch.setattr(catalog, "_catalog", lambda: SimpleNamespace(search=search))
    assert catalog.song_candidates("Oasis") == [
        dict(
            candidate_id="123456789ab",
            provider="YouTube",
            title="Live Forever",
            artist="Oasis",
            duration_ms=276000,
            rank=1,
        )
    ]
    assert catalog.song_candidates("Oasis live at Wembley") == []


def test_twenty_five_distinct_studio_songs_keep_provider_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def search(query: str, *, filter: str, limit: int) -> list[dict[str, object]]:
        def song(i: int) -> dict[str, object]:
            return dict(
                videoId=f"{i:011d}",
                title=f"Song {i}",
                artists=[{"name": "Artist"}],
                album={"name": "Album"},
                duration_seconds=180,
                resultType="song",
                videoType="MUSIC_VIDEO_TYPE_ATV",
            )

        return [song(40) | {"isAvailable": False}, song(0)] + [song(i) for i in range(40)]

    monkeypatch.setattr(catalog, "_catalog", lambda: SimpleNamespace(search=search))
    rows = catalog.song_candidates("Artist")
    assert [row["candidate_id"] for row in rows] == [f"{i:011d}" for i in range(25)]
    assert [row["rank"] for row in rows] == list(range(1, 26))


@pytest.mark.parametrize(
    "label",
    [
        "Трек (Live)",
        "Live at Wembley",
        "Песня — лайв",
        "Song (Live in Montreal)",
        "MTV Unplugged",
        "Live @ KEXP",
        "Track (Concert Version)",
        "Песня (лайф-версия)",
        "Официальный концерт в Москве",
        "Song - Live",
        "Full Album",
        "Official Trailer",
        "Интервью для радио",
    ],
)
def test_performance_and_non_track_labels(label: str) -> None:
    assert track_review_reasons(180, label)


@pytest.mark.parametrize(
    "label",
    [
        "Live Forever",
        "Live and Let Die",
        "Long Live",
        "Movie Theme (Original Soundtrack)",
        "Концерт для фортепиано № 1",
        "Song (Remix)",
    ],
)
def test_studio_and_soundtrack_titles(label: str) -> None:
    assert not track_review_reasons(180, label)


@pytest.mark.parametrize(
    "change",
    [
        {"videoId": "another0000"},
        {"isLiveContent": True},
        {"musicVideoType": "MUSIC_VIDEO_TYPE_OMV"},
        {"musicVideoType": "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"},
        {"title": "Song (Live)"},
        {"lengthSeconds": "7200"},
        {"musicVideoType": None},
    ],
)
def test_exact_player_rechecks_source_kind(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    details = {
        "videoId": "123456789ab",
        "title": "Song",
        "musicVideoType": "MUSIC_VIDEO_TYPE_ATV",
        "lengthSeconds": "180",
        "isLiveContent": False,
    }
    monkeypatch.setattr(
        catalog,
        "_catalog",
        lambda: SimpleNamespace(get_song=lambda _: {"videoDetails": details | change}),
    )
    assert catalog.current_song_duration("123456789ab") is None


def test_download_requires_private_token_provider_before_starting_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTPLAY_MUSIC_PO_TOKEN_URL", raising=False)
    with pytest.raises(InternetMusicProviderError, match="provider_token_configuration_invalid"):
        InternetMusicProvider().download("B3j5Z5NkCxw", tmp_path)


def test_download_propagates_terminal_policy_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTPLAY_MUSIC_PO_TOKEN_URL", "http://music-po-token:4416")

    def rejected(identity: str, path: Path, maximum: int) -> Path:
        assert identity == "B3j5Z5NkCxw"
        raise ProviderChildError("provider_track_policy_rejected")

    monkeypatch.setattr(internet_music, "download_youtube", rejected)
    with pytest.raises(InternetMusicProviderError) as failure:
        InternetMusicProvider().download("B3j5Z5NkCxw", tmp_path)
    assert failure.value.code == "provider_track_policy_rejected" and not failure.value.retryable
