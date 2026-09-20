# Linux retained process containment: 2026-09-18

This is a verified foundation for the unfinished Admin/accounts objective. It adds
an explicit Linux cgroup v2 backend to the existing retained-process protocol.
Production worker composition, internal ingest admission and all-path acceptance
remain incomplete. No provider or ingest worker was activated, no migration was
added, and no commit, push or deployment was performed.

## Ownership and failure behavior

`server/src/autplay/adapters/linux_process_tree.py` creates one random private domain
cgroup beneath a caller-supplied delegated root. Directory traversal rejects
symlinks; fstatfs must identify real cgroup v2 before creation. The adapter retains
directory/control descriptors and checks the group's device/inode identity before
control, observation and removal. Ordinary directories, unavailable controls and
malformed/uncertain populated observations cannot authorize work or exit evidence.
The adapter performs no mounts, delegation or controller changes.

The existing fixed launcher waits for durable GO and cannot fork before attachment.
Attachment holds both the tree lock and the pinned CPython 3.14 Popen reaper lock
through the numeric cgroup.procs write. WNOWAIT checks liveness without reaping; if
the root exits during the write, its unreaped PID still cannot be reused. Lock
acquisition is nonblocking: a competing blocking Popen.wait cannot deadlock attach
and stop. Only Popen may reap the root; external waitpid/reaper ownership is excluded.

Stop is irreversible and requests cgroup.kill even after the root exits. Evidence
requires the exact root wait result and fresh populated=0 for the retained tree.
A detached descendant in its own session keeps the tree populated. Empty sealing
forbids later attachment. The supervisor keeps the group until durable exit
acknowledgement; failed acknowledgement retains quota, staging ownership and the
supervisor entry. Removal closes only the owned, sealed, empty group.

The backend contains trusted fixed tools which do not migrate themselves or use
external process brokers. It is not a hostile same-UID sandbox. Kernel kill does
not make an uninterruptible task disappear; uncertainty continues to retain ownership.
The [kernel cgroup v2 contract](https://docs.kernel.org/admin-guide/cgroup-v2.html)
defines subtree population, inherited membership and kill semantics. The numeric
attachment argument also depends on the pinned
[CPython Popen implementation](https://github.com/python/cpython/blob/v3.14.7/Lib/subprocess.py)
and [waitid/WNOWAIT](https://docs.python.org/3.14/library/os.html#os.waitid).

## Current evidence

- Linux: **24 passed in 53.15 s**, no skips: 17 actual cgroup cases plus seven real
  PostgreSQL/provider-tree cases. Host kernel 6.18.33.2-microsoft-standard-WSL2,
  cgroup v2, Python 3.14.7. The runner asserted UID/GID 10001 and CapEff=0 before
  exec; inspection of the running pytest PID 1 independently confirmed the same.
- Windows: **22 passed in 44.79 s**: the same seven PostgreSQL cases plus fifteen
  Job Object cases. PostgreSQL checks promote SQLAlchemy SAWarning to errors.
- Ruff, formatting and strict mypy pass for all five affected Python files;
  mypy checks both Windows and Linux targets.
- Independent bounded read-only review closed with no remaining actionable finding.
  The reviewer did not run tests. Its busy-reaper finding was fixed and covered by
  the final runtime checks.

The cgroup cases cover live detached descendants after root exit, stop during
Popen/attachment/factory, stopped/sealed late attachment, failed kill and failed or
malformed observation, fake filesystem and symlink rejection, replaced group
identity, constructor descriptor/directory cleanup, reaped/unreaped root rejection,
busy reaper refusal, and simultaneous wait/poll at a numeric-write barrier. The
barrier proves the exact PID remains unreaped at the write; this kernel accepts
the zombie write as a no-op, so a mandatory ESRCH assertion would be incorrect.

The database cases cover INTERNET and A1 release/revoke/quota-lowering while a
descendant lives, plus empty-tree retention after an injected confirmation failure.
Queued work obtains the slot only after actual tree exit and successful durable
acknowledgement. Synthetic children have an independent 30-second upper bound and
do not use a provider, network or real credentials.

Changed files: `adapters/linux_process_tree.py`, `tests/test_linux_process_tree.py`,
`tests/process_tree_support.py`, `tests/postgresql/test_provider_process_tree.py`,
and `tests/fixtures/provider_tree_child.py`, all below `server/src/autplay` or
`server` as appropriate. The shared fixture selects Windows Job Objects on Windows
and requires explicit AUTPLAY_TEST_CGROUP_ROOT on Linux. An absent test delegation
skips these environment-specific tests; a supplied invalid delegation fails.

## Reproduction

Run from `D:/AutPlayProd/AutPlay`. The disposable PostgreSQL 18.4 container is
`autplay-admin-20260916-postgres-1` (host loopback 1520, internal 5432). Each test
creates an isolated database; do not point this at production. The Linux image is
`autplay-acquisition:perf-gate-20260914`, verified image ID
`sha256:f8e4fcb9c8757c5c112f78eced6bbbefd5fca3dff03e3c00666f42f5c0a4e6da`.

An ordinary container exposes read-only cgroupfs. This proof gives only its
disposable setup process SYS_ADMIN to mount cgroup2 within its private cgroup
namespace, creates a delegated subtree, then drops UID/GID and capabilities before
pytest. There is no privileged-container flag, host cgroup namespace or host mount
change. The repository mounts are read-only. The source `.pth` is needed because
isolated `-I` children ignore PYTHONPATH. The virtualenv is outside the noexec
temporary mount.

```powershell
$script = @'
set -eu
uv sync --project /repo/server --frozen --python /opt/acquisition/.venv/bin/python --no-install-project --quiet
/opt/reconcile-venv/bin/python -c 'import sysconfig; from pathlib import Path; Path(sysconfig.get_path("purelib"), "autplay_source.pth").write_text("/repo/server/src\n")'
mkdir /tmp/autplay-cgroup-proof
mount -t cgroup2 -o nosuid,nodev,noexec none /tmp/autplay-cgroup-proof
exec /opt/reconcile-venv/bin/python - <<'PY'
import os, sys
from pathlib import Path
root = Path("/tmp/autplay-cgroup-proof/delegation")
root.mkdir()
for path in (root, *(root / name for name in ("cgroup.procs", "cgroup.threads", "cgroup.subtree_control"))):
    os.chown(path, 10001, 10001)
(root / "cgroup.procs").write_text(str(os.getpid()))
os.environ["AUTPLAY_TEST_CGROUP_ROOT"] = str(root)
os.setgroups([])
os.setgid(10001)
os.setuid(10001)
assert os.geteuid() == 10001
status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
assert int(status["CapEff"].strip(), 16) == 0
print(f"proof identity: uid={os.geteuid()} gid={os.getegid()} CapEff={status['CapEff'].strip()}", flush=True)
os.execv(sys.executable, [sys.executable, "-m", "pytest", "-c", "/repo/server/pyproject.toml", "-p", "no:cacheprovider", "/repo/server/tests/test_linux_process_tree.py", "/repo/server/tests/postgresql/test_provider_process_tree.py", "-q", "-x", "--tb=short", "-W", "error::sqlalchemy.exc.SAWarning"])
PY
'@
docker run --rm --cgroupns=private --cap-add SYS_ADMIN --network container:autplay-admin-20260916-postgres-1 --tmpfs /tmp:rw,nosuid,size=2g -v D:/AutPlayProd/AutPlay/server:/repo/server:ro -v D:/AutPlayProd/AutPlay/docs:/repo/docs:ro -v D:/AutPlayProd/AutPlay/contracts:/repo/contracts:ro -e UV_PROJECT_ENVIRONMENT=/opt/reconcile-venv -e UV_LINK_MODE=copy -e 'AUTPLAY_TEST_DATABASE_URL=postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:5432/autplay?connect_timeout=3' autplay-acquisition:perf-gate-20260914 sh -c $script

$env:AUTPLAY_TEST_DATABASE_URL='postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:1520/autplay?connect_timeout=3'
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/postgresql/test_provider_process_tree.py server/tests/test_windows_process_tree.py -q -x --tb=short -W error::sqlalchemy.exc.SAWarning
```

All reported test sessions completed. Production delegation must be configured
explicitly when wiring this backend; ordinary read-only Docker mounts cannot be
treated as working containment. Ingest still needs its own durable internal
execution receipt and measured budget across hashing, media, CAS publication and
post-finalize cleanup. The resource contract excludes ingest/analysis from audio
TRANSFER leases; do not invent a transfer or repurpose the maintenance singleton
to hide that remaining implementation.
