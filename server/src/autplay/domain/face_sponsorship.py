"""Pure exact-generation Face sponsor lineage and publication guard.

SQL mutation, coalescing and per-owner authorization must enforce these same
identities under locks. A sponsor ID is never reused, including A -> B -> A.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID


class FaceSponsorError(ValueError):
    """A sponsor is stale, unauthorized, or cannot make this transition."""


class FaceSponsorState(StrEnum):
    LIVE = "LIVE"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class FaceSponsorIdentity:
    sponsor_id: UUID
    owner_user_id: UUID
    user_track_ref_id: UUID
    recording_id: UUID
    audio_variant_id: UUID
    source_sha256: bytes
    lineage_sha256: bytes
    artifact_policy_list_sha256: bytes
    source_generation: int
    policy_generation: int
    activation_epoch: int
    redirect_generation: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not UUID
                for value in (
                    self.sponsor_id,
                    self.owner_user_id,
                    self.user_track_ref_id,
                    self.recording_id,
                    self.audio_variant_id,
                )
            )
            or any(
                type(value) is not bytes or len(value) != 32
                for value in (
                    self.source_sha256,
                    self.lineage_sha256,
                    self.artifact_policy_list_sha256,
                )
            )
            or any(
                type(value) is not int or not 1 <= value < 9_007_199_254_740_991
                for value in (
                    self.source_generation,
                    self.policy_generation,
                    self.activation_epoch,
                )
            )
            or type(self.redirect_generation) is not int
            or not 0 <= self.redirect_generation < 9_007_199_254_740_991
        ):
            raise FaceSponsorError("invalid Face sponsor identity")


@dataclass(frozen=True, slots=True)
class FaceSponsor:
    identity: FaceSponsorIdentity
    state: FaceSponsorState
    predecessor_id: UUID | None = None
    successor_id: UUID | None = None

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not FaceSponsorIdentity
            or type(self.state) is not FaceSponsorState
            or (self.predecessor_id is not None and type(self.predecessor_id) is not UUID)
            or (self.successor_id is not None and type(self.successor_id) is not UUID)
            or (self.state is FaceSponsorState.LIVE and self.successor_id is not None)
            or (self.state is FaceSponsorState.WITHDRAWN and self.successor_id is not None)
            or (self.state is FaceSponsorState.SUPERSEDED and self.successor_id is None)
            or self.predecessor_id == self.identity.sponsor_id
            or self.successor_id == self.identity.sponsor_id
        ):
            raise FaceSponsorError("invalid Face sponsor link")


@dataclass(frozen=True, slots=True)
class FaceSponsorAuthority:
    owner_user_id: UUID
    user_track_ref_id: UUID
    recording_id: UUID
    audio_variant_id: UUID
    source_sha256: bytes
    lineage_sha256: bytes
    artifact_policy_list_sha256: bytes
    source_generation: int
    policy_generation: int
    activation_epoch: int
    redirect_generation: int
    reference_admitted: bool
    effective_enabled: bool
    source_valid: bool
    artifact_decisions_current: bool

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not UUID
                for value in (
                    self.owner_user_id,
                    self.user_track_ref_id,
                    self.recording_id,
                    self.audio_variant_id,
                )
            )
            or any(
                type(value) is not bytes or len(value) != 32
                for value in (
                    self.source_sha256,
                    self.lineage_sha256,
                    self.artifact_policy_list_sha256,
                )
            )
            or any(
                type(value) is not int or not 1 <= value < 9_007_199_254_740_991
                for value in (
                    self.source_generation,
                    self.policy_generation,
                    self.activation_epoch,
                )
            )
            or type(self.redirect_generation) is not int
            or not 0 <= self.redirect_generation < 9_007_199_254_740_991
            or any(
                type(value) is not bool
                for value in (
                    self.reference_admitted,
                    self.effective_enabled,
                    self.source_valid,
                    self.artifact_decisions_current,
                )
            )
        ):
            raise FaceSponsorError("invalid Face sponsor authority")


def require_current_face_sponsor(sponsor: FaceSponsor, authority: FaceSponsorAuthority) -> None:
    """Fail closed unless every exact owner/source/lineage generation still matches."""

    if type(sponsor) is not FaceSponsor or type(authority) is not FaceSponsorAuthority:
        raise FaceSponsorError("invalid Face sponsor authority")
    identity = sponsor.identity
    if (
        sponsor.state is not FaceSponsorState.LIVE
        or not authority.reference_admitted
        or not authority.effective_enabled
        or not authority.source_valid
        or not authority.artifact_decisions_current
        or any(
            getattr(identity, field) != getattr(authority, field)
            for field in (
                "owner_user_id",
                "user_track_ref_id",
                "recording_id",
                "audio_variant_id",
                "source_sha256",
                "lineage_sha256",
                "artifact_policy_list_sha256",
                "source_generation",
                "policy_generation",
                "activation_epoch",
                "redirect_generation",
            )
        )
    ):
        raise FaceSponsorError("Face sponsor authority stale")


def replace_face_sponsor(
    current: FaceSponsor, successor_identity: FaceSponsorIdentity
) -> tuple[FaceSponsor, FaceSponsor]:
    """Link one immutable successor; a historical sponsor can never reopen."""

    if (
        type(current) is not FaceSponsor
        or current.state is not FaceSponsorState.LIVE
        or type(successor_identity) is not FaceSponsorIdentity
        or successor_identity.sponsor_id == current.identity.sponsor_id
        or successor_identity.owner_user_id != current.identity.owner_user_id
        or successor_identity.user_track_ref_id != current.identity.user_track_ref_id
        or replace(successor_identity, sponsor_id=current.identity.sponsor_id) == current.identity
    ):
        raise FaceSponsorError("Face sponsor successor invalid")
    retired = replace(
        current, state=FaceSponsorState.SUPERSEDED, successor_id=successor_identity.sponsor_id
    )
    successor = FaceSponsor(
        successor_identity, FaceSponsorState.LIVE, predecessor_id=current.identity.sponsor_id
    )
    return retired, successor


def withdraw_face_sponsor(current: FaceSponsor) -> FaceSponsor:
    if type(current) is not FaceSponsor or current.state is not FaceSponsorState.LIVE:
        raise FaceSponsorError("Face sponsor cannot withdraw twice")
    return replace(current, state=FaceSponsorState.WITHDRAWN)
