import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import github_release as publisher

TAG = "v1.0.12"
COMMIT = "a" * 40
APK = "autplay-1.0.12-production.apk"
DMG = "autplay-1.0.12-macos-arm64.dmg"
SERVER = "autplay-server-v1.0.12.docker.tar.gz"


class Draft:
    def __init__(self) -> None:
        self.release: dict[str, Any] = {
            "databaseId": 7,
            "tagName": TAG,
            "isDraft": True,
            "isPrerelease": False,
            "assets": [],
            "body": "Release notes",
        }
        self.uploaded: list[str] = []
        self.commit = COMMIT

    def gh(self, *args: str, payload: dict[str, Any] | None = None) -> str:
        if args[:2] == ("release", "view"):
            return json.dumps(self.release)
        if args[:2] == ("release", "upload"):
            path, _, label = args[3].partition("#")
            file = Path(path)
            self.uploaded.append(file.name)
            self.release["assets"].append(
                {
                    "name": file.name,
                    "label": label,
                    "state": "uploaded",
                    "size": file.stat().st_size,
                    "digest": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest(),
                    "apiUrl": "https://api.github.com/repos/Dakstar-cool/AutPlay/releases/assets/42",
                }
            )
            return ""
        if args[0] == "api" and "commits" in args[1]:
            return self.commit
        if args[:3] == ("api", "-X", "PATCH"):
            if args[3].endswith("/assets/42"):
                next(a for a in self.release["assets"] if a["name"] == SERVER)["label"] = args[
                    -1
                ].removeprefix("label=")
                return ""
            assert args[3].endswith("/releases/7") and payload and payload["draft"] is False
            self.release.update(isDraft=False, body=payload["body"])
            return ""
        raise AssertionError(args)


@pytest.fixture
def draft(monkeypatch: pytest.MonkeyPatch) -> Draft:
    state = Draft()
    monkeypatch.setattr(publisher, "gh", state.gh)
    return state


@pytest.fixture
def files(tmp_path: Path) -> list[Path]:
    selected = [tmp_path / name for name in (APK, DMG, SERVER)]
    for path in selected:
        path.write_bytes(path.name.encode())
    (tmp_path / "SHA256SUMS").write_text("internal evidence", encoding="utf-8")
    return selected


def test_publish_reuses_matching_uploads_and_labels_server(draft: Draft, files: list[Path]) -> None:
    draft.gh("release", "upload", TAG, str(files[-1]))
    publisher.publish(TAG, COMMIT, files)
    publisher.publish(TAG, COMMIT, files)
    assert sorted(draft.uploaded) == sorted([APK, DMG, SERVER])
    assert draft.release["isDraft"] is False
    assert draft.release["assets"][0]["label"] == f"{publisher.SERVER_LABEL} (Docker, .tar.gz)"
    assert draft.release["body"].count(SERVER) == 1


@pytest.mark.parametrize(
    "name",
    [
        "SHA256SUMS",
        "release-manifest.json",
        "autplay-1.0.12-release-audit.zip",
        "autplay-1.0.12-unsigned.apk",
        "autplay-1.0.11-production.apk",
        "source.tar.gz",
    ],
)
def test_extra_draft_asset_blocks_all_uploads(draft: Draft, files: list[Path], name: str) -> None:
    draft.release["assets"].append({"name": name})
    with pytest.raises(ValueError, match="Unexpected or mismatched"):
        publisher.publish(TAG, COMMIT, files)
    assert draft.release["isDraft"] and not draft.uploaded


def test_rejects_local_evidence_and_missing_production_package(
    draft: Draft, files: list[Path]
) -> None:
    with pytest.raises(ValueError, match="Unexpected release downloads"):
        publisher.publish(TAG, COMMIT, [*files, files[0].parent / "SHA256SUMS"])
    with pytest.raises(ValueError, match="Missing release packages"):
        publisher.publish(TAG, COMMIT, files[:2])
    assert not draft.uploaded


def test_tag_or_digest_mismatch_preserves_draft(draft: Draft, files: list[Path]) -> None:
    draft.commit = "b" * 40
    with pytest.raises(ValueError, match="remote tag"):
        publisher.publish(TAG, COMMIT, files)
    assert not draft.uploaded
    draft.commit = COMMIT
    draft.gh("release", "upload", TAG, str(files[0]))
    files[0].write_bytes(b"different package")
    with pytest.raises(ValueError, match="mismatched asset"):
        publisher.publish(TAG, COMMIT, files)
    assert draft.release["isDraft"]


def test_prerelease_accepts_signed_clients_and_one_server_distribution() -> None:
    allowed = publisher.packages(TAG, prerelease=True)
    selected = {"autplay-1.0.12-dev-signed.apk", "autplay-server-v1.0.12-installer.zip"}
    publisher.validate_names(selected, allowed, True)
    with pytest.raises(ValueError, match="one server"):
        publisher.validate_names(selected | {SERVER}, allowed, True)


def test_unexpected_attachment_during_upload_leaves_draft_private(
    draft: Draft, files: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def concurrent_gh(*args: str, payload: dict[str, Any] | None = None) -> str:
        result = draft.gh(*args, payload=payload)
        if args[:2] == ("release", "upload") and len(draft.uploaded) == 3:
            draft.release["assets"].append({"name": "release-audit.zip"})
        return result

    monkeypatch.setattr(publisher, "gh", concurrent_gh)
    with pytest.raises(ValueError, match="Unexpected release downloads"):
        publisher.publish(TAG, COMMIT, files)
    assert draft.release["isDraft"]
