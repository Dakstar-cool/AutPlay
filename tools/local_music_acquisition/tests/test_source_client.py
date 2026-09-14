from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from local_music_acquisition import cli, source_client
from local_music_acquisition.models import PlaylistItem, ProviderMiss
from local_music_acquisition.providers import _music_sites_worker as worker
from local_music_acquisition.providers.music_sites import SoundCloudProvider


def _file(tmp_path, value="a" * 32):
    path = tmp_path / "client-id"
    path.write_text(value + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("value", ["token", "https://example.test", "a" * 33, "a" * 31, " " * 32])
def test_invalid_client_config_is_rejected_before_network(tmp_path, value):
    with pytest.raises(ValueError, match="soundcloud_client_id_file_invalid"):
        SoundCloudProvider(client_id_file=_file(tmp_path, value))


def test_client_setting_travels_only_through_private_worker_input(monkeypatch, tmp_path):
    def popen(command, **kwargs):
        assert "a" * 32 not in " ".join(command)

        def communicate(raw, **kwargs):
            assert json.loads(raw)["soundcloud_client_id"] == "a" * 32
            return json.dumps({"status": "miss", "code": "exact_match_not_found"}), ""

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(ProviderMiss):
        SoundCloudProvider(client_id_file=_file(tmp_path)).acquire(
            PlaylistItem(1, "Any Artist", "Any Song"), tmp_path
        )


def test_refresh_writes_file_atomically_without_logging_value(monkeypatch, tmp_path, capsys):
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 60
        assert json.loads(kwargs["input"]) == {"action": "refresh_client_id"}
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "configured",
                    "client_id": "b" * 32,
                }
            ),
        )

    monkeypatch.setattr(source_client.subprocess, "run", run)
    output = _file(tmp_path)
    assert cli.main(["soundcloud-client-id", "--output", str(output)]) == 0
    assert source_client.read_client_id(output) == "b" * 32
    assert "b" * 32 not in capsys.readouterr().out


def test_failed_refresh_preserves_existing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        source_client.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="private error details"),
    )
    output = _file(tmp_path)
    assert cli.main(["soundcloud-client-id", "--output", str(output)]) == 2
    assert source_client.read_client_id(output) == "a" * 32
    assert "private" not in capsys.readouterr().err


def test_metadata_timeout_is_actionable_and_redacted():
    from yt_dlp.utils import DownloadError

    error = worker._translate_error(
        DownloadError("Read timed out: https://private.test?key=secret")
    )
    assert error.code == "metadata_timeout"
    assert "private" not in str(error)
