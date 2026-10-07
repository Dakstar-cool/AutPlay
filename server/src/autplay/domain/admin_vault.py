"""Allowlisted Vault diagnostics; arbitrary stored error text is never public."""

from __future__ import annotations

from typing import Final

VAULT_ERROR_REASONS: Final = {
    "vault.integrity_mismatch": "integrity",
    "vault.integrity_conflict": "integrity",
    "upload_chunk_hash_mismatch": "integrity",
    "immutable_object_conflict": "integrity",
    "media_validation_failed": "media",
    "media_tool_output_invalid": "media",
    "media_tool_timeout": "timeout",
    "vault.capacity_low": "capacity",
    "vault_capacity_low": "capacity",
    "upload_limit_exceeded": "capacity",
    "vault_storage_unavailable": "storage",
    "vault_storage_unsafe": "storage",
    "staged_file_not_found": "missing",
    "source_authorization_unavailable": "authorization",
    "vault.invalid_job_payload": "internal",
    "vault.sha_commit_pending": "pending",
    "upload_expired": "expired",
    "upload_offset_mismatch": "interrupted",
}


def safe_vault_error(code: str | None) -> str:
    return code if code in VAULT_ERROR_REASONS else "vault_error_unknown"


def vault_error_reason(code: str) -> str:
    return VAULT_ERROR_REASONS.get(code, "unknown")
