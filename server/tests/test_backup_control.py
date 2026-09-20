from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from autplay.application.backup_control import (
    BackupControlService,
    BackupSnapshot,
    BackupStatus,
    parse_backup_targets,
)
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.web.presentation import navigation
from autplay.web.renderer import AdminTemplateRenderer


def _actor(role: AccountRole = AccountRole.OWNER) -> WebActor:
    return WebActor(uuid4(), uuid4(), uuid4(), role, 1)


def _targets() -> tuple:
    return parse_backup_targets(
        json.dumps(
            [
                {
                    "id": "workstation-usb-e",
                    "label": "External USB E",
                    "kind": "external-agent",
                },
                {"id": "nas-primary", "label": "Primary NAS", "kind": "mounted"},
            ]
        )
    )


def test_registry_rejects_paths_and_duplicate_or_unknown_targets() -> None:
    with pytest.raises(ValueError, match="entry"):
        parse_backup_targets(
            '[{"id":"usb","label":"USB","kind":"mounted","path":"/media/usb"}]'
        )
    with pytest.raises(ValueError, match="identifier"):
        parse_backup_targets(
            '[{"id":"usb","label":"One","kind":"mounted"},'
            '{"id":"usb","label":"Two","kind":"mounted"}]'
        )


def test_owner_configures_policy_and_requests_agent_without_storage_authority(
    tmp_path: Path,
) -> None:
    service = BackupControlService(tmp_path / "control", _targets())
    actor = _actor()
    policy = service.configure(
        actor,
        target_id="workstation-usb-e",
        max_backup_bytes=140 * 1024**3,
        warning_percent=90,
        expected_revision=0,
    )
    operation_id = uuid4()

    service.request(actor, operation_id, policy.revision)
    service.request(actor, operation_id, policy.revision)

    request = json.loads((service.root / "request.json").read_text(encoding="utf-8"))
    status = service.snapshot(actor).status
    assert request["target_id"] == "workstation-usb-e"
    assert "path" not in request
    assert request["max_backup_bytes"] == 140 * 1024**3
    assert status.state == "REQUESTED"
    assert status.message_code == "backup_waiting_for_agent"


def test_non_owner_and_stale_revision_fail_closed(tmp_path: Path) -> None:
    service = BackupControlService(tmp_path / "control", _targets())
    with pytest.raises(WebAdminError, match="forbidden"):
        service.snapshot(_actor(AccountRole.ADMIN))
    policy = service.configure(
        _actor(),
        target_id="nas-primary",
        max_backup_bytes=128 * 1024**3,
        warning_percent=85,
        expected_revision=0,
    )
    with pytest.raises(WebAdminError, match="backup_policy_stale"):
        service.configure(
            _actor(),
            target_id="nas-primary",
            max_backup_bytes=128 * 1024**3,
            warning_percent=85,
            expected_revision=policy.revision - 1,
        )


def test_status_threshold_renders_assertive_alert_without_paths(tmp_path: Path) -> None:
    service = BackupControlService(tmp_path / "control", _targets())
    actor = _actor()
    policy = service.configure(
        actor,
        target_id="workstation-usb-e",
        max_backup_bytes=100 * 1024**3,
        warning_percent=90,
        expected_revision=0,
    )
    snapshot = service.snapshot(actor)
    status = BackupStatus(
        "RUNNING",
        "workstation-usb-e",
        "admin-target-20260920T180629Z",
        91 * 1024**3,
        20 * 1024**3,
        policy.max_backup_bytes,
        policy.warning_percent,
        "backup_limit_approaching",
        "2026-09-20T18:06:29Z",
    )
    warned = BackupSnapshot(snapshot.targets, policy, status, True)
    context = {
        "authenticated": True,
        "development_mode": False,
        "flash": None,
        "language_url": "/admin/recovery?lang=en",
        "navigation": navigation("recovery"),
        "page_title": "Backups",
        "unavailable": False,
        "snapshot": warned,
        "policy": policy,
        "policy_operation_id": str(uuid4()),
        "request_operation_id": str(uuid4()),
        "csrf_token": "safe-token",
    }

    html = AdminTemplateRenderer().render(
        "backup_control.html", locale="ru", context=context
    )

    assert 'role="alert"' in html
    assert '<meta http-equiv="refresh" content="10;url=/admin/recovery?lang=ru">' in html
    assert "workstation-usb-e" in html
    assert "E:\\" not in html and "/srv/" not in html
    assert "91,0 GiB" in html


def test_malformed_agent_status_fails_closed(tmp_path: Path) -> None:
    service = BackupControlService(tmp_path / "control", _targets())
    (service.root / "status.json").write_text(
        '{"state":"RUNNING","bytes_written":-1}', encoding="utf-8"
    )

    with pytest.raises(WebAdminError, match="backup_control_unavailable"):
        service.snapshot(_actor())
