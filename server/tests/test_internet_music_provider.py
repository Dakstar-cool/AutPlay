from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from autplay.adapters.internet_music import InternetMusicProvider, InternetMusicProviderError


def test_download_requires_private_token_provider_before_starting_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTPLAY_MUSIC_PO_TOKEN_URL", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("download started without token provider"),
    )

    with pytest.raises(InternetMusicProviderError, match="provider_token_configuration_invalid"):
        InternetMusicProvider().download("B3j5Z5NkCxw", tmp_path)


def test_download_uses_mweb_provider_and_classifies_bot_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTPLAY_MUSIC_PO_TOKEN_URL", "http://music-po-token:4416")
    observed: list[str] = []

    def run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        observed.extend(arguments)
        return subprocess.CompletedProcess(
            arguments,
            1,
            b"",
            b"ERROR: Sign in to confirm you're not a bot",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(InternetMusicProviderError) as failure:
        InternetMusicProvider().download("B3j5Z5NkCxw", tmp_path)

    assert failure.value.code == "provider_challenge_unresolved"
    extractor_args = observed[observed.index("--extractor-args") + 1]
    assert extractor_args == (
        "youtube:player_client=mweb;youtubepot-bgutilhttp:base_url=http://music-po-token:4416"
    )
