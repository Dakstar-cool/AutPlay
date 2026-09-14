from __future__ import annotations

from types import SimpleNamespace

from local_music_acquisition import preflight


def test_node20_is_rejected_before_network(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "node")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="v20.19.0\n"),
    )
    assert preflight.node_status() == "node_22_required"


def test_supported_node_and_missing_runtime(monkeypatch) -> None:
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "node")
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="v22.20.0\n"),
    )
    assert preflight.node_status() == "configured"
    monkeypatch.setattr(preflight.shutil, "which", lambda _: None)
    assert preflight.node_status() == "node_missing"
