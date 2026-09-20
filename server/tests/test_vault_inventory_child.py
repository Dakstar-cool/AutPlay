"""Inventory replies and commands remain bounded beyond the default JSON limit."""

import io
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import vault_inventory_child_launch
from autplay.adapters.filesystem.inventory_protocol import (
    MAX_INVENTORY_REPLY_BYTES,
    decode_page,
    page_command,
    page_document,
)
from autplay.adapters.filesystem.vault_child import (
    ChildProtocolError,
    decode_document,
    encode_document,
    write_frame,
)
from autplay.adapters.filesystem.vault_inventory_child import execute_command
from autplay.adapters.filesystem.vault_process import RetainedVaultProcess
from autplay.application.vault_inventory import InventoryArea, InventoryEntry, InventoryPage
from autplay.domain.provider_maintenance import (
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
)
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import ExecutionState
from autplay.domain.vault import OpaqueStorageKey
from autplay.runtime.resource_io_deadline import ResourceIoDeadline
from test_vault_inventory import populate


def test_full_long_key_page_exceeds_default_frame_but_fits_explicit_bound() -> None:
    claim_id = uuid4()
    page = InventoryPage(
        tuple(
            InventoryEntry(InventoryArea.STAGING, OpaqueStorageKey(f"{index:03}" + "x" * 197))
            for index in range(100)
        ),
        100,
        False,
    )
    payload = encode_document(page_document(claim_id, 1, page), maximum=MAX_INVENTORY_REPLY_BYTES)
    assert 8192 < len(payload) <= MAX_INVENTORY_REPLY_BYTES
    assert (
        decode_page(
            decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES),
            claim_id=claim_id,
            sequence=1,
            maximum=100,
        )
        == page
    )


@pytest.mark.parametrize("fault", ["claim", "sequence", "bool", "budget", "entries", "extra"])
def test_reply_cannot_change_identity_sequence_or_bounds(fault: str) -> None:
    claim_id = uuid4()
    page = InventoryPage((InventoryEntry(InventoryArea.STAGING, OpaqueStorageKey("a")),), 1, False)
    document = page_document(claim_id, 1, page)
    if fault == "claim":
        document["claim_id"] = str(uuid4())
    elif fault == "sequence":
        document["sequence"] = 2
    elif fault == "bool":
        document["sequence"] = True
    elif fault == "budget":
        document["work_units"] = 2
    elif fault == "entries":
        document["entries"] = [{"area": "STAGING", "key": "a"}] * 2
    else:
        document["extra"] = 1
    with pytest.raises(ChildProtocolError):
        decode_page(document, claim_id=claim_id, sequence=1, maximum=1)


@pytest.mark.parametrize(
    "document",
    [
        {"sequence": True, "maximum": 1},
        {"sequence": 1, "maximum": 101},
        {"sequence": 2, "maximum": 1},
    ],
)
def test_invalid_page_command_never_touches_root(
    tmp_path: Path, document: dict[str, object]
) -> None:
    source = io.BytesIO()
    write_frame(source, b"N", encode_document(document))
    source.seek(0)
    root = tmp_path / "not-created"
    with pytest.raises(ChildProtocolError):
        execute_command(
            {"version": 1, "root": str(root), "action": "INVENTORY", "claim_id": str(uuid4())},
            source,
            io.BytesIO(),
        )
    assert not root.exists()


def test_scanner_launch_does_not_inherit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AUTPLAY_DATABASE_URL", "AUTPLAY_MUSIC_PROXY", "PATH", "HTTP_PROXY"):
        monkeypatch.setenv(key, "synthetic-secret")
    arguments, environment = vault_inventory_child_launch()
    assert arguments[-2:] == ["-m", "autplay.adapters.filesystem.vault_inventory_child"]
    assert "-I" in arguments and "synthetic-secret" not in environment.values()


def test_actual_child_pages_require_go_and_include_large_frame(tmp_path: Path) -> None:
    expected = populate(tmp_path, 137)
    execution = uuid4()
    ticket = MaintenanceTicket(execution, uuid4(), None, execution, MaintenanceAction.INVENTORY)
    child = RetainedVaultProcess(
        ticket, ResourceIoDeadline(monotonic()), launch=vault_inventory_child_launch
    )
    identity = child.spawn()
    try:
        with pytest.raises(ResourceAdmissionError):
            child.inventory_page(page_command(1, 100))
        child.allow_go(MaintenanceStatus(ticket, ExecutionState.RUNNING, identity))
        child.go(
            {"version": 1, "root": str(tmp_path), "claim_id": str(execution), "action": "INVENTORY"}
        )
        tag, payload = child.read_result()
        assert tag == b"R" and decode_document(payload)["claim_id"] == str(execution)
        observed: list[InventoryEntry] = []
        large = False
        for sequence in range(1, 10):
            tag, payload = child.inventory_page(page_command(sequence, 100))
            assert tag == b"R"
            large |= len(payload) > 8192
            page = decode_page(
                decode_document(payload, maximum=MAX_INVENTORY_REPLY_BYTES),
                claim_id=execution,
                sequence=sequence,
                maximum=100,
            )
            observed.extend(page.entries)
            if page.exhausted:
                break
        else:
            pytest.fail("scanner did not finish the finite fixture")
        assert large and set(observed) == expected and len(observed) == len(expected)
        until = monotonic() + 3
        while (proof := child.exit_evidence(identity)) is None:
            assert monotonic() < until
            sleep(0.01)
        assert proof.exit_code == 0
    finally:
        child.request_stop()
        until = monotonic() + 3
        while child.exit_evidence(identity) is None:
            assert monotonic() < until
            sleep(0.01)
        child.close_pipes_after_worker_exit()
