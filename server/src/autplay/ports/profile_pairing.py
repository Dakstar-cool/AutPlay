"""Boundary owned by M5B profile-pairing application commands."""

from __future__ import annotations

from typing import Protocol

from autplay.domain.auth import Principal


class ProfilePairingQueries(Protocol):
    """Read-only self-account views exposed to the HTTP adapter."""

    def discovery(self) -> dict[str, object]: ...
    def capabilities(self, principal: Principal) -> dict[str, object]: ...
    def list_devices(self, principal: Principal) -> dict[str, object]: ...


__all__ = ("ProfilePairingQueries",)
