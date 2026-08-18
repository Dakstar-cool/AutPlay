"""Read-only Git inspection and hard safety gates."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitSafetyError(RuntimeError):
    """Raised when repository state makes automation unsafe."""


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    """Minimal repository identity used by state and resume checks."""

    root: Path
    branch: str
    head: str
    dirty_paths: tuple[str, ...]
    dirty_fingerprints: tuple[tuple[str, str], ...] = ()


_DANGEROUS_REQUESTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bforce[- ]?push\b", re.IGNORECASE),
    re.compile(r"\bmerge\s+(?:into\s+)?(?:main|master)\b", re.IGNORECASE),
    re.compile(r"\bdeploy\s+(?:to\s+)?production\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(?:the\s+)?(?:database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+(?:the\s+)?(?:media\s+)?vault\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+(?:real\s+|user\s+)?data\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?|-f\b)", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\b[^\r\n]*--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+checkout\s+--(?:\s|$)", re.IGNORECASE),
    re.compile(r"\bgit\s+restore\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\b[^\r\n]*-[^\s]*f", re.IGNORECASE),
    re.compile(r"\bremove-item\b[^\r\n]*(?:-recurse|-force)", re.IGNORECASE),
    re.compile(r"\b(?:rmdir|rd)\b(?=[^\r\n]*/s)(?=[^\r\n]*/q)", re.IGNORECASE),
    re.compile(
        r"\bудал(?:и|ить|ение)\w*\s+(?:баз\w*\s+данн\w*|.*vault|хранилищ\w*)",  # noqa: RUF001
        re.IGNORECASE,
    ),
    re.compile(
        r"\bразверн\w*\s+.*(?:production|продакш\w*)",  # noqa: RUF001
        re.IGNORECASE,
    ),
    re.compile(r"\bфорс\w*\s+(?:push|пуш\w*)", re.IGNORECASE),  # noqa: RUF001
)


class GitInspector:
    """Perform bounded Git reads without invoking a shell."""

    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory.resolve()

    def snapshot(self) -> GitSnapshot:
        root = Path(self._git("rev-parse", "--show-toplevel")).resolve()
        self.working_directory = root
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            raise GitSafetyError("detached HEAD is not supported by the harness")
        head = self._git("rev-parse", "HEAD")
        status = self._git_raw("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = _parse_porcelain_z_entries(status)
        index_entries = _parse_index_entries(self._git_raw("ls-files", "--stage", "-z"))
        fingerprints = tuple(
            (
                path,
                self._dirty_fingerprint(
                    root,
                    path,
                    entry_status,
                    index_entries.get(path, ""),
                ),
            )
            for path, entry_status in entries
        )
        return GitSnapshot(
            root=root,
            branch=branch,
            head=head,
            dirty_paths=tuple(path for path, _entry_status in entries),
            dirty_fingerprints=fingerprints,
        )

    def changed_files(self) -> tuple[str, ...]:
        return self.snapshot().dirty_paths

    def _git(self, *arguments: str) -> str:
        return self._git_raw(*arguments).strip()

    def _git_raw(self, *arguments: str) -> str:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        process = subprocess.run(
            ["git", *arguments],
            cwd=self.working_directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "unknown Git error"
            raise GitSafetyError(f"git {' '.join(arguments)} failed: {detail[:1000]}")
        return process.stdout

    def _dirty_fingerprint(
        self,
        root: Path,
        path: str,
        status: str,
        index_entry: str,
    ) -> str:
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise GitSafetyError("git reported a path outside the repository")
        candidate = root / relative
        digest = hashlib.sha256()
        digest.update(status.encode("utf-8"))
        digest.update(index_entry.encode("utf-8", errors="replace"))
        if candidate.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(candidate).encode("utf-8", errors="surrogateescape"))
        elif candidate.is_file():
            digest.update(b"file\0")
            try:
                with candidate.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as exc:
                raise GitSafetyError(f"cannot fingerprint dirty path {path!r}: {exc}") from exc
        elif candidate.is_dir():
            digest.update(b"directory\0")
        else:
            digest.update(b"absent\0")
        return digest.hexdigest()


def assert_safe_to_start(
    snapshot: GitSnapshot,
    *,
    expected_root: Path,
    protected_branches: frozenset[str],
) -> None:
    """Reject unsafe branch/root/dirty-tree starts before invoking Codex."""

    if snapshot.root != expected_root.resolve():
        raise GitSafetyError("Git root does not match the configured repository root")
    if snapshot.branch.casefold() in protected_branches:
        raise GitSafetyError(
            f"refusing automated writes on protected branch {snapshot.branch!r}; "
            "create a work branch"
        )
    if snapshot.dirty_paths:
        raise GitSafetyError("working tree is dirty; commit or stash it before automated writes")


def assert_resume_compatible(
    state_root: str,
    state_branch: str,
    expected_head: str,
    snapshot: GitSnapshot,
) -> None:
    """Ensure resume cannot silently continue in another repository or branch."""

    if Path(state_root).resolve() != snapshot.root:
        raise GitSafetyError("stored task belongs to a different repository")
    if state_branch != snapshot.branch:
        raise GitSafetyError(
            f"stored task belongs to branch {state_branch!r}, current branch is {snapshot.branch!r}"
        )
    if expected_head != snapshot.head:
        raise GitSafetyError(
            "repository HEAD changed since the last harness checkpoint; inspect before resuming"
        )


def assert_dirty_baseline_preserved(expected: dict[str, str], snapshot: GitSnapshot) -> None:
    """Reject a checkpoint if any pre-existing dirty byte snapshot changed."""

    current = dict(snapshot.dirty_fingerprints)
    changed = sorted(
        path for path, fingerprint in expected.items() if current.get(path) != fingerprint
    )
    if changed:
        raise GitSafetyError(
            "pre-existing dirty content changed or disappeared: " + ", ".join(changed)
        )


def assert_request_is_non_destructive(description: str) -> None:
    """Require a human-guided workflow for operations outside harness authority."""

    for pattern in _DANGEROUS_REQUESTS:
        if pattern.search(description):
            raise GitSafetyError(
                "request contains a destructive, deploy, protected-merge, or force-push operation; "
                "the harness cannot authorize it"
            )


def _parse_porcelain_z(output: str) -> tuple[str, ...]:
    return tuple(path for path, _status in _parse_porcelain_z_entries(output))


def _parse_porcelain_z_entries(output: str) -> tuple[tuple[str, str], ...]:
    fields = output.split("\0")
    entries: dict[str, str] = {}
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise GitSafetyError("unexpected git status --porcelain output")
        status = entry[:2]
        path = entry[3:]
        entries[path.replace("\\", "/")] = status
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise GitSafetyError("incomplete rename/copy entry in git status output")
            entries[fields[index].replace("\\", "/")] = f"{status}:source"
            index += 1
    return tuple(sorted(entries.items()))


def _parse_index_entries(output: str) -> dict[str, str]:
    entries: dict[str, list[str]] = {}
    for raw_entry in output.split("\0"):
        if not raw_entry:
            continue
        metadata, separator, path = raw_entry.partition("\t")
        if not separator or not path:
            raise GitSafetyError("unexpected git ls-files --stage output")
        normalized = path.replace("\\", "/")
        entries.setdefault(normalized, []).append(metadata)
    return {path: "\n".join(values) for path, values in entries.items()}
