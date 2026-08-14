"""Explicit Argon2id password hashing for a future approved login contract."""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher as ArgonPasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

MAX_PASSWORD_UTF8_BYTES = 1024


@dataclass(frozen=True, slots=True)
class Argon2idParameters:
    """Auditable Argon2id work factors for the CPU-only server."""

    time_cost: int = 3
    memory_cost_kib: int = 65_536
    parallelism: int = 4
    hash_len: int = 32
    salt_len: int = 16

    def __post_init__(self) -> None:
        if self.time_cost < 2:
            raise ValueError("Argon2id time cost must be at least 2")
        if self.memory_cost_kib < 19_456:
            raise ValueError("Argon2id memory cost must be at least 19 MiB")
        if self.parallelism < 1:
            raise ValueError("Argon2id parallelism must be positive")
        if self.hash_len < 32 or self.salt_len < 16:
            raise ValueError("Argon2id hash/salt lengths are below the P03 floor")


class Argon2idPasswordHasher:
    """Hash and verify passwords without enabling a password-login endpoint."""

    def __init__(self, parameters: Argon2idParameters | None = None) -> None:
        self.parameters = parameters or Argon2idParameters()
        self._hasher = ArgonPasswordHasher(
            time_cost=self.parameters.time_cost,
            memory_cost=self.parameters.memory_cost_kib,
            parallelism=self.parameters.parallelism,
            hash_len=self.parameters.hash_len,
            salt_len=self.parameters.salt_len,
            type=Type.ID,
        )

    def hash_password(self, password: str) -> str:
        """Return an Argon2id v19 hash with a library-generated random salt."""

        _validate_password(password)
        return self._hasher.hash(password)

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        """Verify a password while mapping malformed hashes to safe failure."""

        try:
            _validate_password(password)
            return bool(self._hasher.verify(encoded_hash, password))
        except InvalidHashError, VerificationError, ValueError:
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        """Return true for obsolete parameters or malformed encoded hashes."""

        try:
            return self._hasher.check_needs_rehash(encoded_hash)
        except InvalidHashError:
            return True


def _validate_password(password: str) -> None:
    if not password:
        raise ValueError("password must not be empty")
    if len(password.encode("utf-8")) > MAX_PASSWORD_UTF8_BYTES:
        raise ValueError("password exceeds 1024 UTF-8 bytes")


__all__ = (
    "MAX_PASSWORD_UTF8_BYTES",
    "Argon2idParameters",
    "Argon2idPasswordHasher",
)
