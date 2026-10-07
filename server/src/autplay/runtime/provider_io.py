"""Execute the fixed provider command using retained worker and control ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem.provider_child import ProviderCommandResult
from autplay.adapters.filesystem.provider_media import PROVIDER_RESULT_BYTES
from autplay.adapters.filesystem.vault_child import ChildProtocolError, decode_document
from autplay.adapters.filesystem.vault_process import ChildLaunch, ProcessTreeFactory
from autplay.application.internet_acquisition import ProviderFileReceipt
from autplay.domain.jobs import TerminalJobError
from autplay.domain.resource_admission import (
    AcquisitionClaim,
    ActivationFence,
    ResourceAdmissionError,
)
from autplay.domain.track_metadata import sanitize_source_metadata, source_metadata_document
from autplay.domain.vault import Sha256Digest, VaultLimits, VerifiedStagedFile

from .vault_io import VaultIoCoordinator, VaultIoSession

_RETRYABLE_PROVIDER_ERRORS = {
    "provider_challenge_unresolved",
    "provider_download_failed",
    "provider_token_unavailable",
}
_TERMINAL_PROVIDER_ERRORS = {
    "provider_format_unsupported",
    "provider_runtime_unsupported",
    "provider_source_unavailable",
    "provider_token_configuration_invalid",
}


async def execute_provider[T](
    coordinator: VaultIoCoordinator,
    claim: AcquisitionClaim,
    fence: ActivationFence,
    *,
    tree_factory: ProcessTreeFactory,
    launch: ChildLaunch,
    command: Callable[[VaultIoSession], T],
) -> tuple[UUID, T]:
    """Response bytes are not completion: await exact tree exit and durable closure."""
    io: VaultIoSession | None = None
    try:
        io = await coordinator.open_provider(claim, fence, tree_factory=tree_factory, launch=launch)
        session = io
        result = await io.perform(lambda: command(session))
        await io.finish_provider()
        return io.child.ticket.execution_id, result
    except TimeoutError as error:
        raise ResourceAdmissionError("resource_execution_unconfirmed") from error
    except (ChildProtocolError, OSError) as error:
        raise ResourceAdmissionError("resource_provider_failed") from error
    finally:
        if io is not None:
            io.finish()


class ProviderIoExecutor:
    def __init__(
        self,
        coordinator: VaultIoCoordinator,
        *,
        root: Path,
        limits: VaultLimits,
        tree_factory: ProcessTreeFactory,
        launch: ChildLaunch = provider_child_launch,
    ) -> None:
        self._coordinator, self._root, self._limits = coordinator, root, limits
        self._tree_factory, self._launch = tree_factory, launch

    def execute(
        self, claim: AcquisitionClaim, fence: ActivationFence, candidate_id: str
    ) -> ProviderFileReceipt:
        """Job handlers are synchronous; OS/control owners outlive this private loop."""
        return asyncio.run(self._execute(claim, fence, candidate_id))

    async def _execute(
        self, claim: AcquisitionClaim, fence: ActivationFence, candidate_id: str
    ) -> ProviderFileReceipt:
        identity, verified = await execute_provider(
            self._coordinator,
            claim,
            fence,
            tree_factory=self._tree_factory,
            launch=self._launch,
            command=lambda io: self._download(io, candidate_id),
        )
        return ProviderFileReceipt(identity, verified.verified, verified.source_metadata)

    def _download(self, io: VaultIoSession, candidate_id: str) -> ProviderCommandResult:
        maximum = min(self._limits.max_object_bytes, 2 * 1024**3)
        io.child.go(
            {
                "version": 1,
                "provider": "YOUTUBE",
                "candidate_id": candidate_id,
                "execution_id": io.child.ticket.execution_id.hex,
                "root": str(self._root),
                "max_object_bytes": maximum,
                "max_chunk_bytes": min(self._limits.max_chunk_bytes, maximum),
                "max_chunks": self._limits.max_chunks,
                "io_block_bytes": self._limits.io_block_bytes,
            }
        )
        tag, payload = io.child.read_result()
        document = decode_document(payload, maximum=PROVIDER_RESULT_BYTES)
        if tag == b"E" and set(document) == {"code"}:
            code = document["code"]
            if isinstance(code, str) and code in _TERMINAL_PROVIDER_ERRORS:
                raise TerminalJobError(code)
            if isinstance(code, str) and code in _RETRYABLE_PROVIDER_ERRORS:
                raise ResourceAdmissionError(code)
            raise ResourceAdmissionError("resource_provider_failed")
        if tag != b"R" or set(document) not in (
            {"byte_size", "sha256"},
            {"byte_size", "sha256", "source_metadata"},
        ):
            raise ResourceAdmissionError("resource_provider_failed")
        size, digest = document["byte_size"], document["sha256"]
        if (
            type(size) is not int
            or not 1 <= size <= maximum
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise ResourceAdmissionError("resource_provider_failed")
        try:
            sha256 = Sha256Digest(bytes.fromhex(digest))
        except ValueError as error:
            raise ResourceAdmissionError("resource_provider_failed") from error
        if sha256.hex != digest:
            raise ResourceAdmissionError("resource_provider_failed")
        evidence: dict[str, object] | None = None
        if "source_metadata" in document:
            try:
                native = sanitize_source_metadata(document["source_metadata"])
                if native.provider == "YOUTUBE" and native.source_id == candidate_id:
                    evidence = source_metadata_document(native)
            except ValueError, TypeError, OverflowError:
                pass
        return ProviderCommandResult(VerifiedStagedFile(size, sha256), evidence)
