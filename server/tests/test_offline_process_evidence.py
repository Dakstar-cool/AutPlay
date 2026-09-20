"""Actual Windows process absence evidence for trusted offline restore drain."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.domain.resource_admission import ResourceAdmissionError


@pytest.mark.skipif(os.name != "nt", reason="actual Windows process evidence")
def test_windows_probe_blocks_live_or_ambiguous_pid_and_accepts_exact_absence() -> None:
    probe = OfflineProcessEvidenceProbe(None)
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(ResourceAdmissionError, match="offline_process_still_running"):
            probe.verify((process.pid,))
    finally:
        process.terminate()
        process.wait(timeout=10)

    evidence = probe.verify((process.pid,))
    assert evidence.checked_pids == (process.pid,)
    assert evidence.checked_cgroups == ()
    assert len(evidence.evidence_sha256) == 32
    with pytest.raises(ResourceAdmissionError, match="offline_process_evidence_invalid"):
        probe.verify((2**32,))


@pytest.mark.skipif(sys.platform != "linux", reason="actual Linux cgroup2 evidence")
def test_linux_probe_rejects_an_ordinary_directory(tmp_path: Path) -> None:
    with pytest.raises(ResourceAdmissionError, match="offline_process_evidence_unavailable"):
        OfflineProcessEvidenceProbe(tmp_path).verify(())
