"""Shared real-process fixtures; contains no production provider or network access."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from autplay.adapters.child_process import vault_child_launch
from autplay.adapters.filesystem.vault_process import ProcessTreeFactory, RetainedVaultProcess
from autplay.adapters.linux_process_tree import LinuxCgroupTree
from autplay.adapters.windows_process_tree import WindowsJobTree
from autplay.domain.ingest_execution import IngestExecutionTicket
from autplay.domain.provider_maintenance import MaintenanceTicket
from autplay.domain.resource_admission import ActivationFence, IoPermit
from autplay.domain.resource_execution import (
    ExecutionKind,
    ExecutionTicket,
    ProcessExitEvidence,
    ProcessIdentity,
)


def tree_child_launch() -> tuple[list[str], dict[str, str]]:
    arguments, environment = vault_child_launch()
    script = Path(__file__).parent / "fixtures" / "provider_tree_child.py"
    return [arguments[0], "-I", str(script)], environment


def provider_ticket() -> ExecutionTicket:
    fence = ActivationFence(uuid4(), uuid4(), 1)
    target = uuid4()
    permit = IoPermit(uuid4(), fence, target, datetime.now(UTC) + timedelta(seconds=5))
    return ExecutionTicket(uuid4(), uuid4(), permit, ExecutionKind.PROVIDER, target)


def process_tree_factory() -> ProcessTreeFactory:
    if os.name == "nt":
        return WindowsJobTree
    root = os.environ.get("AUTPLAY_TEST_CGROUP_ROOT")
    if not root:
        pytest.skip("real Linux containment requires an explicit delegated test cgroup")
    return lambda: LinuxCgroupTree(Path(root))


def wait_tree_exit[Ticket: ExecutionTicket | IngestExecutionTicket | MaintenanceTicket](
    child: RetainedVaultProcess[Ticket], identity: ProcessIdentity | None
) -> ProcessExitEvidence:
    until = monotonic() + 5
    while (proof := child.exit_evidence(identity)) is None:
        if monotonic() >= until:
            raise AssertionError("owned test process tree did not exit")
        sleep(0.01)
    return proof


def wait_marker(marker: Path) -> None:
    until = monotonic() + 5
    while not marker.exists():
        if monotonic() >= until:
            raise AssertionError("bounded test descendant did not start")
        sleep(0.01)
