"""The maintenance child has a bounded exact command and no inherited credentials."""

import io
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import provider_maintenance_child_launch
from autplay.adapters.filesystem.provider_maintenance_child import execute_command
from autplay.adapters.filesystem.vault_child import decode_document
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.application.provider_scratch import provider_scratch_id
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from test_provider_filesystem import setup


def command(root: Path, ticket: MaintenanceTicket) -> dict[str, object]:
    return {
        "version": 1,
        "root": str(root),
        "action": ticket.action.value,
        "provider_execution_id": str(ticket.provider_execution_id),
        "claim_id": str(ticket.claim_id),
    }


@pytest.mark.parametrize("action", [MaintenanceAction.CLEANUP, MaintenanceAction.SCRATCH])
def test_actual_child_only_moves_claimed_files_after_go(
    tmp_path: Path, action: MaintenanceAction
) -> None:
    _, cleanup, staged, workspace = setup(tmp_path)
    claim_id = (
        cleanup.claim_id
        if action == MaintenanceAction.CLEANUP
        else provider_scratch_id(cleanup.execution_id)
    )
    ticket = MaintenanceTicket(uuid4(), uuid4(), cleanup.execution_id, claim_id, action)
    child = RetainedVaultProcess(
        ticket, ResourceIoDeadline(monotonic()), launch=provider_maintenance_child_launch
    )
    identity = child.spawn()
    try:
        assert workspace.exists() and staged.exists()
        with pytest.raises(ResourceAdmissionError):
            child.go(command(tmp_path, ticket))
        child.allow_go(MaintenanceStatus(ticket, ExecutionState.RUNNING, identity))
        child.go(command(tmp_path, ticket))
        tag, payload = child.read_result()
        assert tag == b"R" and decode_document(payload)["claim_id"] == str(claim_id)
        until = monotonic() + 5
        while (proof := child.exit_evidence(identity)) is None:
            assert monotonic() < until
            sleep(0.01)
        assert proof.exit_code == 0
        assert not workspace.exists()
        assert staged.exists() == (action == MaintenanceAction.SCRATCH)
        assert (
            tmp_path / "provider-retired" / claim_id.hex / "audio.part"
        ).read_bytes() == b"partial"
    finally:
        child.request_stop()
        until = monotonic() + 5
        while child.exit_evidence(identity) is None:
            assert monotonic() < until
            sleep(0.01)
        child.close_pipes_after_worker_exit()


@pytest.mark.parametrize("fault", ["version", "root", "claim", "action", "extra"])
def test_invalid_command_never_initializes_storage(tmp_path: Path, fault: str) -> None:
    execution = uuid4()
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), execution, provider_scratch_id(execution), MaintenanceAction.SCRATCH
    )
    document = command(tmp_path / "not-created", ticket)
    if fault == "version":
        document["version"] = True
    elif fault == "root":
        document["root"] = "relative"
    elif fault == "claim":
        document["claim_id"] = str(uuid4())
    elif fault == "action":
        document["action"] = "DELETE"
    else:
        document["credentials"] = "synthetic-secret"
    with pytest.raises(ValueError):
        execute_command(document, io.BytesIO())
    assert not (tmp_path / "not-created").exists()


def test_launch_does_not_inherit_database_session_proxy_or_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("AUTPLAY_DATABASE_URL", "AUTPLAY_JAMENDO_CLIENT_ID", "AUTPLAY_MUSIC_PROXY", "PATH"):
        monkeypatch.setenv(key, "synthetic-secret")
    arguments, environment = provider_maintenance_child_launch()
    assert arguments[-2:] == ["-m", "autplay.adapters.filesystem.provider_maintenance_child"]
    assert "-I" in arguments
    assert (
        not {"AUTPLAY_DATABASE_URL", "AUTPLAY_JAMENDO_CLIENT_ID", "AUTPLAY_MUSIC_PROXY", "PATH"}
        & environment.keys()
    )
    assert "synthetic-secret" not in environment.values()
    if os.name == "nt":
        assert "SYSTEMROOT" in environment
