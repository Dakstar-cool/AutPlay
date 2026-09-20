"""Contained Jamendo worker command with fresh metadata and durable exact exit."""

import asyncio
from pathlib import Path

from autplay.adapters.child_process import discovery_child_launch
from autplay.adapters.filesystem.discovery_protocol import DISCOVERY_CHILD_ERRORS, decode_result
from autplay.adapters.filesystem.vault_child import ChildProtocolError, decode_document
from autplay.adapters.filesystem.vault_process import ChildLaunch, ProcessTreeFactory
from autplay.application.controlled_discovery import (
    DiscoveryAcquisitionTarget,
    DiscoveryFileReceipt,
)
from autplay.domain.discovery import DiscoveryError, DiscoveryEvidence
from autplay.domain.resource_admission import AcquisitionClaim, ActivationFence
from autplay.domain.vault import VaultLimits, VerifiedStagedFile

from .provider_io import execute_provider
from .vault_io import VaultIoCoordinator, VaultIoSession


class DiscoveryIoExecutor:
    def __init__(
        self,
        coordinator: VaultIoCoordinator,
        *,
        root: Path,
        limits: VaultLimits,
        tree_factory: ProcessTreeFactory,
        client_id: str,
        max_download_bytes: int = 150 * 1024**2,
        launch: ChildLaunch | None = None,
    ) -> None:
        if type(max_download_bytes) is not int or not 1 <= max_download_bytes <= 1024**3:
            raise ValueError("discovery_download_limit_invalid")
        discovery_child_launch(client_id)
        self._coordinator, self._root, self._limits = coordinator, root, limits
        self._maximum = min(limits.max_object_bytes, max_download_bytes)
        self._tree_factory = tree_factory
        self._launch = launch or (lambda: discovery_child_launch(client_id))

    def execute(
        self, claim: AcquisitionClaim, fence: ActivationFence, target: DiscoveryAcquisitionTarget
    ) -> DiscoveryFileReceipt:
        identity, (verified, evidence) = asyncio.run(
            execute_provider(
                self._coordinator,
                claim,
                fence,
                tree_factory=self._tree_factory,
                launch=self._launch,
                command=lambda io: self._download(io, target),
            )
        )
        return DiscoveryFileReceipt(identity, verified, evidence)

    def _download(
        self, io: VaultIoSession, target: DiscoveryAcquisitionTarget
    ) -> tuple[VerifiedStagedFile, DiscoveryEvidence]:
        maximum = self._maximum
        io.child.go(
            {
                "version": 1,
                "provider": "JAMENDO",
                "candidate_id": target.provider_track_id,
                "provider_artist_id": target.provider_artist_id,
                "execution_id": io.child.ticket.execution_id.hex,
                "root": str(self._root),
                "max_object_bytes": maximum,
                "max_chunk_bytes": min(self._limits.max_chunk_bytes, maximum),
                "max_chunks": self._limits.max_chunks,
                "io_block_bytes": self._limits.io_block_bytes,
            }
        )
        tag, payload = io.child.read_result()
        if tag == b"E":
            error = decode_document(payload)
            if (
                set(error) == {"code"}
                and isinstance(error["code"], str)
                and error["code"] in DISCOVERY_CHILD_ERRORS
            ):
                raise DiscoveryError(error["code"])
            raise ChildProtocolError()
        if tag != b"R":
            raise ChildProtocolError()
        verified, evidence = decode_result(payload, maximum=maximum)
        if (evidence.provider_track_id, evidence.provider_artist_id) != (
            target.provider_track_id,
            target.provider_artist_id,
        ):
            raise DiscoveryError("discovery_not_eligible")
        return verified, evidence
