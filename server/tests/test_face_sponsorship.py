"""An A -> B -> A source history never revives an earlier Face sponsor."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from autplay.domain.face_sponsorship import (
    FaceSponsor,
    FaceSponsorAuthority,
    FaceSponsorError,
    FaceSponsorIdentity,
    FaceSponsorState,
    replace_face_sponsor,
    require_current_face_sponsor,
    withdraw_face_sponsor,
)


def _identity() -> FaceSponsorIdentity:
    return FaceSponsorIdentity(
        sponsor_id=UUID(int=1),
        owner_user_id=UUID(int=2),
        user_track_ref_id=UUID(int=3),
        recording_id=UUID(int=4),
        audio_variant_id=UUID(int=5),
        source_sha256=b"a" * 32,
        lineage_sha256=b"l" * 32,
        artifact_policy_list_sha256=b"p" * 32,
        source_generation=1,
        policy_generation=1,
        activation_epoch=1,
        redirect_generation=0,
    )


def _authority(identity: FaceSponsorIdentity) -> FaceSponsorAuthority:
    return FaceSponsorAuthority(
        owner_user_id=identity.owner_user_id,
        user_track_ref_id=identity.user_track_ref_id,
        recording_id=identity.recording_id,
        audio_variant_id=identity.audio_variant_id,
        source_sha256=identity.source_sha256,
        lineage_sha256=identity.lineage_sha256,
        artifact_policy_list_sha256=identity.artifact_policy_list_sha256,
        source_generation=identity.source_generation,
        policy_generation=identity.policy_generation,
        activation_epoch=identity.activation_epoch,
        redirect_generation=identity.redirect_generation,
        reference_admitted=True,
        effective_enabled=True,
        source_valid=True,
        artifact_decisions_current=True,
    )


def test_successor_lineage_never_reopens_old_a() -> None:
    first = FaceSponsor(_identity(), FaceSponsorState.LIVE)
    require_current_face_sponsor(first, _authority(first.identity))
    second_identity = replace(
        first.identity,
        sponsor_id=UUID(int=6),
        audio_variant_id=UUID(int=7),
        source_sha256=b"b" * 32,
        source_generation=2,
    )
    old_a, b = replace_face_sponsor(first, second_identity)
    assert old_a.successor_id == b.identity.sponsor_id
    assert b.predecessor_id == old_a.identity.sponsor_id
    require_current_face_sponsor(b, _authority(b.identity))
    with pytest.raises(FaceSponsorError, match="stale"):
        require_current_face_sponsor(old_a, _authority(old_a.identity))

    third_identity = replace(
        first.identity,
        sponsor_id=UUID(int=8),
        source_generation=3,
        redirect_generation=1,
    )
    old_b, new_a = replace_face_sponsor(b, third_identity)
    require_current_face_sponsor(new_a, _authority(new_a.identity))
    with pytest.raises(FaceSponsorError, match="stale"):
        require_current_face_sponsor(first, _authority(new_a.identity))
    with pytest.raises(FaceSponsorError, match="stale"):
        require_current_face_sponsor(old_b, _authority(new_a.identity))

    withdrawn = withdraw_face_sponsor(new_a)
    with pytest.raises(FaceSponsorError, match="stale"):
        require_current_face_sponsor(withdrawn, _authority(new_a.identity))
    with pytest.raises(FaceSponsorError, match="withdraw twice"):
        withdraw_face_sponsor(withdrawn)


def test_owner_license_and_generation_mismatch_fail_closed() -> None:
    sponsor = FaceSponsor(_identity(), FaceSponsorState.LIVE)
    authority = _authority(sponsor.identity)
    for stale in (
        replace(authority, owner_user_id=UUID(int=9)),
        replace(authority, artifact_policy_list_sha256=b"x" * 32),
        replace(authority, artifact_decisions_current=False),
        replace(authority, policy_generation=2),
        replace(authority, reference_admitted=False),
    ):
        with pytest.raises(FaceSponsorError, match="stale"):
            require_current_face_sponsor(sponsor, stale)
    with pytest.raises(FaceSponsorError, match="successor invalid"):
        replace_face_sponsor(sponsor, replace(sponsor.identity, sponsor_id=UUID(int=10)))
    with pytest.raises(FaceSponsorError, match="invalid Face sponsor authority"):
        replace(authority, artifact_decisions_current=cast(Any, 1))
