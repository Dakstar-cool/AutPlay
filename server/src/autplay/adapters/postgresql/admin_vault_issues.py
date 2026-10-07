"""Bounded diagnostic groups; upload errors remain scoped to the acting account."""

from __future__ import annotations

from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.orm import Session

from autplay.adapters.postgresql.models.vault import (
    UploadSessionRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.domain.admin_vault import VAULT_ERROR_REASONS
from autplay.domain.admin_views import AdminVaultIssue
from autplay.domain.web_admin import WebActor


def vault_issue_groups(
    session: Session, actor: WebActor
) -> tuple[tuple[AdminVaultIssue, ...], bool]:
    upload_code = case(
        (UploadSessionRow.error_code.in_(tuple(VAULT_ERROR_REASONS)), UploadSessionRow.error_code),
        else_="vault_error_unknown",
    )
    object_code = case(
        (
            VaultObjectRow.verification_error.in_(tuple(VAULT_ERROR_REASONS)),
            VaultObjectRow.verification_error,
        ),
        else_="vault_error_unknown",
    )
    groups = union_all(
        select(
            literal("upload").label("scope"),
            UploadSessionRow.state.label("state"),
            upload_code.label("error_code"),
            func.count().label("issue_count"),
            func.max(UploadSessionRow.completed_at).label("last_occurred_at"),
        )
        .where(
            UploadSessionRow.user_id == actor.user_id,
            UploadSessionRow.state.in_(("FAILED", "QUARANTINED")),
        )
        .group_by(UploadSessionRow.state, upload_code),
        select(
            literal("object"),
            VaultObjectRow.commit_status,
            object_code,
            func.count(),
            func.max(VaultObjectRow.updated_at),
        )
        .where(VaultObjectRow.commit_status == "QUARANTINED")
        .group_by(
            VaultObjectRow.commit_status,
            object_code,
        ),
        select(
            literal("replica"),
            VaultReplicaRow.replica_status,
            literal("vault_error_unknown"),
            func.count(),
            func.max(VaultReplicaRow.updated_at),
        )
        .where(VaultReplicaRow.replica_status.in_(("MISSING", "CORRUPT", "QUARANTINED")))
        .group_by(VaultReplicaRow.replica_status),
    ).subquery()
    rows = session.execute(
        select(groups)
        .order_by(
            groups.c.last_occurred_at.desc().nulls_last(),
            groups.c.scope,
            groups.c.state,
            groups.c.error_code,
        )
        .limit(51)
    ).all()
    return tuple(
        AdminVaultIssue(
            row.scope, row.state, row.error_code, int(row.issue_count), row.last_occurred_at
        )
        for row in rows[:50]
    ), len(rows) > 50
