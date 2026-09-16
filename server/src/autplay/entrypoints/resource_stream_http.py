"""Demand-pulled stream bytes from a supervised, durably admitted Vault child."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from autplay.adapters.filesystem.vault_child import ChildProtocolError
from autplay.application.vault_streaming import AuthorizedStream
from autplay.domain.auth import Principal
from autplay.domain.vault import ByteRange, StorageOperationError, VaultLimits
from autplay.entrypoints.resource_io_http import ResourceIoHeaders
from autplay.runtime.vault_io import VaultIoCoordinator, VaultIoSession


class ProcessStreamBody(AsyncIterator[bytes]):
    def __init__(
        self,
        io: VaultIoSession,
        authorized: AuthorizedStream,
        selected: ByteRange,
        *,
        root: Path,
        limits: VaultLimits,
    ) -> None:
        self.io = io
        self._authorized, self._selected = authorized, selected
        self._root, self._limits = root, limits
        self._received = 0
        self._finished = False

    def open_in_worker(self) -> None:
        self.io.child.go(
            {
                "version": 1,
                "kind": "STREAM",
                "root": str(self._root),
                "key": self._authorized.storage_key.value,
                "max_object_bytes": self._limits.max_object_bytes,
                "max_chunk_bytes": self._limits.max_chunk_bytes,
                "max_chunks": self._limits.max_chunks,
                "io_block_bytes": self._limits.io_block_bytes,
                "start": self._selected.start,
                "end": self._selected.end,
                "expected_size": self._authorized.byte_size,
                "verified_at": self._authorized.verified_at.isoformat(),
            }
        )
        tag, payload = self.io.child.read_result()
        if tag == b"E":
            raise StorageOperationError()
        if tag != b"O" or payload:
            raise ChildProtocolError()

    def _next_in_worker(self) -> bytes | None:
        tag, payload = self.io.child.next_block()
        if tag == b"E":
            raise StorageOperationError()
        if tag == b"R" and not payload and self._received == self._selected.length:
            self._finished = True
            return None
        if (
            tag != b"D"
            or not payload
            or len(payload) > self._limits.io_block_bytes
            or self._received + len(payload) > self._selected.length
        ):
            raise ChildProtocolError()
        self._received += len(payload)
        return payload

    async def __anext__(self) -> bytes:
        if self._finished:
            raise StopAsyncIteration
        payload = await self.io.perform(self._next_in_worker)
        if payload is None:
            raise StopAsyncIteration
        return payload


class ProcessStreamGateway:
    def __init__(
        self,
        coordinator: VaultIoCoordinator,
        *,
        root: Path,
        limits: VaultLimits | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._root, self._limits = root, limits or VaultLimits()

    async def open(
        self,
        actor: Principal,
        headers: ResourceIoHeaders,
        audio_variant_id: UUID,
        authorized: AuthorizedStream,
        selected: ByteRange,
    ) -> ProcessStreamBody:
        io = await self.coordinator.open(
            actor,
            headers.fence,
            resource_type=headers.resource_type,
            target_id=audio_variant_id,
        )
        body = ProcessStreamBody(io, authorized, selected, root=self._root, limits=self._limits)
        try:
            await io.perform(body.open_in_worker)
            return body
        except BaseException:
            io.finish()
            raise
