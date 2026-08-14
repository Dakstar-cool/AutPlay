"""Principal-scoped object authorization policies."""

from __future__ import annotations

from uuid import UUID

from autplay.domain.auth import OwnedObjectNotFoundError, Principal


def require_same_owner(principal: Principal, owner_user_id: UUID) -> None:
    """Fail closed when an object is not owned by the authenticated user.

    The intentionally indistinguishable not-found result prevents callers
    from using authorization failures to enumerate another user's objects.
    Administrative cross-user commands require a separate explicit use case.
    """

    if principal.user_id != owner_user_id:
        raise OwnedObjectNotFoundError


def is_same_owner(principal: Principal, owner_user_id: UUID) -> bool:
    """Return whether an object belongs to the authenticated user."""

    return principal.user_id == owner_user_id


__all__ = ("is_same_owner", "require_same_owner")
