"""Privilege boundary for the Linux worker cgroup bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest
from autplay.entrypoints import worker_cgroup_bootstrap as bootstrap


class ExecObserved(BaseException):
    pass


def test_bootstrap_delegates_before_privilege_drop_and_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    monkeypatch.setattr(bootstrap, "_autplay_identity", lambda: (999, 998))
    monkeypatch.setattr(bootstrap, "_mount_private_cgroup", lambda: events.append("mount"))
    monkeypatch.setattr(
        bootstrap, "_delegate", lambda uid, gid: events.append(("delegate", uid, gid))
    )
    monkeypatch.setattr(
        bootstrap, "_drop_privileges", lambda uid, gid: events.append(("drop", uid, gid))
    )

    def observe_exec(file: str, args: list[str], environment: dict[str, str]) -> None:
        events.append(("exec", file, args, environment["AUTPLAY_WORKER_CGROUP_ROOT"]))
        raise ExecObserved

    monkeypatch.setattr("autplay.entrypoints.worker_cgroup_bootstrap.os.execvpe", observe_exec)
    with pytest.raises(ExecObserved):
        bootstrap.main([])
    assert events == [
        "mount",
        ("delegate", 999, 998),
        ("drop", 999, 998),
        (
            "exec",
            "autplay-worker-cpu",
            ["autplay-worker-cpu"],
            str(Path("/tmp/autplay-cgroup/delegation")),
        ),
    ]


def test_readiness_drops_privilege_without_mounting(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(bootstrap, "_autplay_identity", lambda: (999, 998))
    monkeypatch.setattr(
        bootstrap, "_mount_private_cgroup", lambda: events.append("unexpected-mount")
    )
    monkeypatch.setattr(bootstrap, "_delegate", lambda *_: events.append("unexpected-delegate"))
    monkeypatch.setattr(
        bootstrap, "_drop_privileges", lambda uid, gid: events.append(("drop", uid, gid))
    )

    def observe_exec(file: str, args: list[str], environment: dict[str, str]) -> None:
        del environment
        events.append(("exec", file, args))
        raise ExecObserved

    monkeypatch.setattr("autplay.entrypoints.worker_cgroup_bootstrap.os.execvpe", observe_exec)
    with pytest.raises(ExecObserved):
        bootstrap.main(["--check-readiness"])
    assert events == [
        ("drop", 999, 998),
        ("exec", "autplay-worker-cpu", ["autplay-worker-cpu", "--check-readiness"]),
    ]
