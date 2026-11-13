"""The live probe cannot turn incomplete or substituted evidence into target authority."""

from __future__ import annotations

import base64
import importlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    tool = importlib.import_module("macos_account_acceptance")
finally:
    sys.path.remove(str(SCRIPTS))

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def target() -> dict[str, object]:
    return {
        "kind": "AUTPLAY_LIVE_9443_ACCEPTANCE",
        "origin": "https://synthetic.invalid:9443",
        "stream_origin": "https://stream.synthetic.invalid:9443",
        "tls_spki_b64": base64.b64encode(b"t" * 91).decode(),
        "stream_tls_spki_b64": base64.b64encode(b"s" * 91).decode(),
        "instance": "11111111-1111-4111-8111-111111111111",
        "identity_epoch": 1,
        "identity_spki_b64": base64.b64encode(b"i" * 91).decode(),
        "server_source": "a" * 40,
        "server_image": "sha256:" + "b" * 64,
        "schema_head": "0074_device_deletion_v2",
        "deployment_receipt_sha256": "c" * 64,
        "backup_restore_receipt_sha256": "d" * 64,
        "production_deployed": True,
        "backup_restore_verified": True,
        "observed_at_utc": NOW.isoformat(),
    }


def test_exact_operator_target_is_retained_without_inventing_live_evidence() -> None:
    candidate = target()
    assert tool.validate_live_target(json.dumps(candidate).encode(), NOW) == candidate


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "http://synthetic.invalid:9443"),
        ("origin", "https://synthetic.invalid:443"),
        ("origin", "https://secret@synthetic.invalid:9443"),
        ("origin", "https://synthetic.invalid:9443?"),
        ("origin", "https://synthetic.invalid:9443/"),
        ("origin", 9443),
        ("stream_origin", "http://stream.synthetic.invalid:9443"),
        ("stream_origin", "https://stream.synthetic.invalid:443"),
        ("stream_origin", "https://secret@stream.synthetic.invalid:9443"),
        ("stream_origin", "https://stream.synthetic.invalid:9443/"),
        ("stream_origin", "https://stream.synthetic.invalid:9443?"),
        ("stream_origin", 9443),
        ("production_deployed", False),
        ("production_deployed", 1),
        ("backup_restore_verified", False),
        ("backup_restore_verified", "true"),
        ("identity_epoch", True),
        ("identity_epoch", 0),
        ("instance", "invalid"),
        ("server_source", "a" * 39),
        ("server_image", "latest"),
        ("schema_head", "0067_sona_native_collection"),
        ("deployment_receipt_sha256", ""),
        ("backup_restore_receipt_sha256", ""),
        ("tls_spki_b64", "invalid"),
        ("stream_tls_spki_b64", "invalid"),
        ("identity_spki_b64", "invalid"),
        ("observed_at_utc", (NOW - timedelta(hours=2)).isoformat()),
        ("observed_at_utc", (NOW + timedelta(minutes=3)).isoformat()),
        ("observed_at_utc", "2026-11-11T12:00:00"),
        ("password", "owner-password-is-never-accepted"),
        ("public_id", "owner-account-is-never-accepted"),
    ],
)
def test_invalid_boundary_is_rejected_with_a_redacted_error(field: str, value: object) -> None:
    candidate = {**target(), field: value}
    with pytest.raises(ValueError, match=r"^Invalid live acceptance target evidence$"):
        tool.validate_live_target(json.dumps(candidate).encode(), NOW)


def test_duplicate_fields_and_oversized_input_fail_closed() -> None:
    raw = json.dumps(target()).encode()
    for invalid in (raw[:-1] + b',"production_deployed":false}', b" " * 16385):
        with pytest.raises(ValueError, match=r"^Invalid live acceptance target evidence$"):
            tool.validate_live_target(invalid, NOW)


def test_probe_packaging_failure_preserves_existing_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    home = tmp_path / "home"
    canonical = home / "Library/Application Support/AutPlay"
    canonical.mkdir(parents=True)
    original = canonical / "original-account"
    original.write_bytes(b"synthetic-existing-state")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(tool, "TEMP", tmp_path / "probe")
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        tool.community, "run", lambda *args, **kwargs: SimpleNamespace(stdout="original.keychain")
    )

    def fail_copy(*args: object, **kwargs: object) -> None:
        raise RuntimeError("packaging failed")

    monkeypatch.setattr(tool.shutil, "copytree", fail_copy)
    with pytest.raises(RuntimeError, match="packaging failed"):
        tool.exercise_clients(tmp_path / "unused.private.json", "synthetic")
    assert original.read_bytes() == b"synthetic-existing-state"


def test_source_proof_accepts_only_harness_changes_and_rejects_dirty_or_runtime_changes(
    tmp_path: Path,
) -> None:
    def git(*args: str) -> str:
        return (
            subprocess.run(
                ["git", *args], cwd=tmp_path, capture_output=True, check=True, timeout=30
            )
            .stdout.decode()
            .strip()
        )

    def commit() -> str:
        git("add", ".")
        git(
            "-c",
            "user.name=Acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Synthetic source proof",
        )
        return git("rev-parse", "HEAD")

    git("init")
    runtime = tmp_path / "server/runtime.py"
    runtime.parent.mkdir()
    runtime.write_text("original_runtime = True\n")
    baseline = commit()
    exact = tool.verify_live_source(baseline, baseline, tmp_path)
    assert exact["production_tree_equal"] and exact["harness_only_paths"] == []
    harness = tmp_path / "scripts/macos_account_acceptance.py"
    harness.parent.mkdir()
    harness.write_text("synthetic_harness_fix = True\n")
    repaired = commit()
    proof = tool.verify_live_source(baseline, repaired, tmp_path)
    assert proof["production_tree_equal"]
    assert proof["harness_only_paths"] == ["scripts/macos_account_acceptance.py"]
    runtime.write_text("changed_runtime = True\n")
    with pytest.raises(RuntimeError, match="does not match deployed production code"):
        tool.verify_live_source(baseline, repaired, tmp_path)
    changed = commit()
    with pytest.raises(RuntimeError, match="does not match deployed production code"):
        tool.verify_live_source(baseline, changed, tmp_path)
    with pytest.raises(RuntimeError, match="does not match deployed production code"):
        tool.verify_live_source(baseline, "0" * 40, tmp_path)


def test_both_origins_and_pins_are_mandatory() -> None:
    for field in ("stream_origin", "stream_tls_spki_b64"):
        candidate = target()
        del candidate[field]
        with pytest.raises(ValueError, match="Invalid live acceptance target evidence"):
            tool.validate_live_target(json.dumps(candidate).encode(), NOW)
