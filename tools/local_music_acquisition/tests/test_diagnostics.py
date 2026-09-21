from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest
import requests

from local_music_acquisition import diagnostics


@pytest.mark.parametrize(
    "proxy",
    [
        "https://private.example",
        "socks5h://secret@127.0.0.1:10808",
        "socks5h://127.0.0.1:10808/?secret",
        "socks5h://127.0.0.1:99999",
    ],
)
def test_diagnostics_rejects_nonlocal_or_secret_proxy(proxy):
    with pytest.raises(argparse.ArgumentTypeError, match="local socks5h"):
        diagnostics.local_proxy(proxy)


def test_network_probes_do_not_log_credentials_or_follow_redirects(monkeypatch):
    calls = []

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            assert not self.trust_env
            assert not kwargs["allow_redirects"] and kwargs["stream"]
            assert kwargs["timeout"] == (5, 5)
            calls.append((url, kwargs["proxies"]))
            raise requests.RequestException("private URL, UUID, password")

    monkeypatch.setattr(diagnostics.requests, "Session", Session)
    report = diagnostics.page_probes("socks5h://127.0.0.1:10808")
    assert len(calls) == 6
    assert all(not call[1] for call in calls[:3])
    assert all(call[1]["https"] == "socks5h://127.0.0.1:10808" for call in calls[3:])
    assert "private" not in json.dumps(report) and "UUID" not in json.dumps(report)


def test_runtime_extracts_versions_without_raw_paths_or_hostname(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: "/private/path")
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="tool 22.3.4 private path"),
    )
    report = diagnostics.runtime_report()
    assert report["versions"]["node"] == "22.3.4"
    assert "private" not in json.dumps(report)
