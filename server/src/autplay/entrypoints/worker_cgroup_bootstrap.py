"""Create one private cgroup-v2 delegation, then run the worker without privilege."""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

SERVICE_NAME = "autplay-worker-cpu"
_MOUNTPOINT = Path("/tmp/autplay-cgroup")
_DELEGATION = _MOUNTPOINT / "delegation"
_CGROUP2_MAGIC = 0x63677270
_MOUNT_FLAGS = 2 | 4 | 8  # MS_NOSUID | MS_NODEV | MS_NOEXEC


def _autplay_identity() -> tuple[int, int]:
    import pwd

    account = cast(Any, pwd).getpwnam("autplay")
    return account.pw_uid, account.pw_gid


def _is_cgroup2(path: Path) -> bool:
    posix = cast(Any, os)
    descriptor = os.open(path, os.O_RDONLY | posix.O_DIRECTORY | posix.O_CLOEXEC)
    try:
        api = ctypes.CDLL(None, use_errno=True)
        api.fstatfs.argtypes = [ctypes.c_int, ctypes.c_void_p]
        api.fstatfs.restype = ctypes.c_int
        result = (ctypes.c_long * 64)()
        return api.fstatfs(descriptor, ctypes.byref(result)) == 0 and result[0] == _CGROUP2_MAGIC
    finally:
        os.close(descriptor)


def _mount_private_cgroup() -> None:
    _MOUNTPOINT.mkdir(mode=0o700, exist_ok=True)
    api = ctypes.CDLL(None, use_errno=True)
    api.mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    api.mount.restype = ctypes.c_int
    if (
        api.mount(
            b"none",
            os.fsencode(_MOUNTPOINT),
            b"cgroup2",
            _MOUNT_FLAGS,
            None,
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error == errno.EBUSY and _is_cgroup2(_MOUNTPOINT):
        return
    raise OSError(error, "private cgroup2 mount unavailable")


def _delegate(uid: int, gid: int) -> None:
    posix = cast(Any, os)
    _DELEGATION.mkdir(mode=0o700, exist_ok=True)
    events = (_DELEGATION / "cgroup.events").read_text(encoding="ascii").splitlines()
    populated = [line for line in events if line.startswith("populated ")]
    if populated != ["populated 0"]:
        raise OSError("cgroup delegation is not empty")
    for path in (
        _DELEGATION,
        _DELEGATION / "cgroup.procs",
        _DELEGATION / "cgroup.threads",
        _DELEGATION / "cgroup.subtree_control",
    ):
        posix.chown(path, uid, gid)
    (_DELEGATION / "cgroup.procs").write_text(str(os.getpid()), encoding="ascii")


def _drop_privileges(uid: int, gid: int) -> None:
    posix = cast(Any, os)
    posix.setgroups([])
    posix.setgid(gid)
    posix.setuid(uid)
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines()
        if ":" in line
    )
    if posix.geteuid() != uid or posix.getegid() != gid or int(status["CapEff"].strip(), 16) != 0:
        raise OSError("worker privilege drop failed")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autplay-worker-cgroup-bootstrap")
    parser.add_argument("--check-readiness", action="store_true")
    options = parser.parse_args(arguments)
    stage = "identity"
    try:
        uid, gid = _autplay_identity()
        if not options.check_readiness:
            stage = "mount"
            _mount_private_cgroup()
            stage = "delegation"
            _delegate(uid, gid)
            os.environ["AUTPLAY_WORKER_CGROUP_ROOT"] = str(_DELEGATION)
        stage = "privilege_drop"
        _drop_privileges(uid, gid)
        stage = "exec"
        command = ["autplay-worker-cpu"]
        if options.check_readiness:
            command.append("--check-readiness")
        os.execvpe(command[0], command, os.environ)
    except KeyError, OSError, RuntimeError, ValueError:
        sys.stderr.write(
            json.dumps(
                {
                    "event": f"resource_process_tree_{stage}_unavailable",
                    "service": SERVICE_NAME,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
