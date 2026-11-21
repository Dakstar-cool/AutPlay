"""Limit a GitHub Release to the approved downloads for its tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = os.environ.get("GITHUB_REPOSITORY", "Dakstar-cool/AutPlay")
SERVER_LABEL = "Серверный пакет Sona для Linux x86-64"
FIELDS = "databaseId,tagName,isDraft,isPrerelease,body,assets"
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?\Z")


def gh(*args: str, payload: dict[str, Any] | None = None) -> str:
    """Run the GitHub CLI using the operator's existing credentials."""
    result = subprocess.run(
        ["gh", *args],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"gh {args[0]} failed")
    return result.stdout


def view(tag: str) -> dict[str, Any]:
    release: dict[str, Any] = json.loads(gh("release", "view", tag, "-R", REPO, "--json", FIELDS))
    if release["tagName"] != tag:
        raise ValueError("GitHub returned a different release tag")
    return release


def packages(tag: str, prerelease: bool) -> dict[str, str]:
    """Map exact versioned download names to their GitHub display labels."""
    version = tag[1:]
    names = {
        f"autplay-{version}-production.apk": "",
        f"autplay-{version}-macos-arm64.dmg": "",
        f"autplay-server-{tag}.docker.tar.gz": f"{SERVER_LABEL} (Docker, .tar.gz)",
    }
    if prerelease:
        for suffix in (".apk", "-dev-signed.apk", "-trusted-lan.apk"):
            names[f"autplay-{version}{suffix}"] = ""
        names[f"autplay-server-{tag}-installer.zip"] = f"{SERVER_LABEL} (.zip)"
    return names


def validate_names(names: set[str], allowed: dict[str, str], prerelease: bool) -> None:
    unexpected = names.difference(allowed)
    if unexpected:
        raise ValueError(f"Unexpected release downloads: {', '.join(sorted(unexpected))}")
    if not names or (not prerelease and names != allowed.keys()):
        raise ValueError("Missing release packages: " + ", ".join(sorted(allowed.keys() - names)))
    if sum(bool(allowed[name]) for name in names) > 1:
        raise ValueError("Select one server distribution for this release")


def notes_with_server(notes: str, name: str, tag: str, label: str) -> str:
    url = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
    if any(f"({url})" in line and "серверный пакет" in line.lower() for line in notes.splitlines()):
        return notes
    link = f"[{label}]({url})"
    return f"{notes.rstrip()}\n\n{link}\n" if notes.strip() else link


def verify(
    release: dict[str, Any], allowed: dict[str, str], hashes: dict[str, str] | None = None
) -> None:
    assets = release["assets"]
    names = {asset["name"] for asset in assets}
    validate_names(names, allowed, release["isPrerelease"])
    if len(assets) != len(names) or (hashes is not None and names != hashes.keys()):
        raise ValueError("The release downloads differ from the selected files")
    for asset in assets:
        name = asset["name"]
        if asset["state"] != "uploaded" or asset["size"] <= 0:
            raise ValueError(f"Incomplete release asset: {name}")
        if hashes is not None and asset["digest"] != hashes[name]:
            raise ValueError(f"SHA-256 mismatch: {name}")
        if allowed[name] and asset["label"] != allowed[name]:
            raise ValueError(f"Missing server package label: {name}")


def publish(tag: str, commit: str, files: list[Path]) -> None:
    """Upload approved packages to a draft and verify the complete set before publishing."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected the full approved source commit")
    release = view(tag)
    release_id = release["databaseId"]
    allowed = packages(tag, release["isPrerelease"])
    names = {path.name for path in files}
    validate_names(names, allowed, release["isPrerelease"])
    if len(names) != len(files):
        raise ValueError("Duplicate local package name")
    hashes = {}
    for path in files:
        if path.is_symlink() or not path.is_file() or not path.stat().st_size or "#" in str(path):
            raise ValueError(f"Invalid package file: {path}")
        with path.open("rb") as stream:
            hashes[path.name] = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()

    def check_tag() -> None:
        if gh("api", f"repos/{REPO}/commits/{tag}", "--jq", ".sha").strip() != commit:
            raise ValueError("The remote tag does not resolve to the approved source commit")

    check_tag()
    existing = {asset["name"]: asset for asset in release["assets"]}
    for name, asset in existing.items():
        if name not in hashes or asset["state"] != "uploaded" or asset["digest"] != hashes[name]:
            raise ValueError(f"Unexpected or mismatched asset already on the release: {name}")
    if release["isDraft"]:
        for path in files:
            name = path.name
            if name not in existing:
                spec = str(path.resolve())
                if allowed[name]:
                    spec += "#" + allowed[name]
                gh("release", "upload", tag, spec, "-R", REPO)
            elif allowed[name] and existing[name]["label"] != allowed[name]:
                asset_id = existing[name]["apiUrl"].rsplit("/", 1)[1]
                gh(
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{REPO}/releases/assets/{asset_id}",
                    "-f",
                    f"label={allowed[name]}",
                )
    current = view(tag)
    if current["databaseId"] != release_id or current["isPrerelease"] != release["isPrerelease"]:
        raise ValueError("The release was replaced or its channel changed")
    verify(current, allowed, hashes)
    notes = current["body"] or ""
    for name in names:
        if allowed[name]:
            notes = notes_with_server(notes, name, tag, allowed[name])
    if current["isDraft"]:
        check_tag()
        gh(
            "api",
            "-X",
            "PATCH",
            f"repos/{REPO}/releases/{release_id}",
            "--input",
            "-",
            payload={"draft": False, "body": notes},
        )
    final = view(tag)
    if final["databaseId"] != release_id or final["isDraft"] or final["body"] != notes:
        raise ValueError("Published release or server description differs from the verified draft")
    verify(final, allowed, hashes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="read-only verification of a release")
    check.add_argument("tag")
    upload = commands.add_parser("publish", help="publish an approved draft")
    upload.add_argument("tag")
    upload.add_argument("--source-commit", required=True)
    upload.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        if not TAG.fullmatch(args.tag):
            raise ValueError("Expected a version tag such as v1.0.11")
        if args.command == "publish":
            publish(args.tag, args.source_commit, args.files)
        else:
            release = view(args.tag)
            allowed = packages(args.tag, release["isPrerelease"])
            verify(release, allowed)
            for asset in release["assets"]:
                name = asset["name"]
                if (
                    allowed[name]
                    and notes_with_server(release["body"] or "", name, args.tag, allowed[name])
                    != release["body"]
                ):
                    raise ValueError(f"Missing server package download in release notes: {name}")
        print(f"{args.tag}: GitHub release downloads verified")
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
