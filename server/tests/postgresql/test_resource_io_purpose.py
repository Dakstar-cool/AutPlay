"""HTTP I/O target binding cannot borrow another purpose or trust a recording hint."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.models import AudioVariantRow, LibraryEntryRow
from autplay.domain.resource_admission import ResourceAdmissionError, ResourceKind, ResourceRequest
from sqlalchemy import select

from .test_resource_admission_runtime import AdmissionHarness, fence, play, present
from .test_resource_admission_runtime import admission as admission


def test_playback_maps_actual_variant_and_rechecks_it_on_renewal(
    admission: AdmissionHarness,
) -> None:
    actor = admission.actor()
    first, second = admission.variant(actor), admission.variant(actor)
    with admission.sessions() as session:
        first_recording = present(session.get(AudioVariantRow, first)).recording_id
        second_recording = present(session.get(AudioVariantRow, second)).recording_id
    admission.budget()
    operation = admission.service.acquire(actor, play())
    activation = fence(operation)
    admission.service.attach(actor, activation, 0, first_recording, second_recording)
    permit = admission.service.open_io(actor, activation, first, resource_type="PLAY_INSTANCE")
    assert permit.target_id == first_recording
    assert (
        admission.service.renew_io(
            actor,
            permit,
            resource_type="PLAY_INSTANCE",
            target_id=first,
        ).permit_id
        == permit.permit_id
    )
    # Another attached recording cannot take over an existing HTTP reader's permit.
    with pytest.raises(ResourceAdmissionError, match="resource_target_mismatch"):
        admission.service.renew_io(actor, permit, resource_type="PLAY_INSTANCE", target_id=second)
    # A URL must name a real authorized audio variant, not an attached recording UUID.
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(actor, activation, first_recording, resource_type="PLAY_INSTANCE")
    with admission.sessions.begin() as session:
        for entry in session.scalars(
            select(LibraryEntryRow).where(
                LibraryEntryRow.user_id == actor.user_id,
            )
        ):
            entry.removed_at = datetime.now(UTC)
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.renew_io(actor, permit, resource_type="PLAY_INSTANCE", target_id=first)


@pytest.mark.parametrize("resource_type", ["DOWNLOAD_INTENT", "UPLOAD_INTENT"])
def test_transfer_cannot_borrow_other_purpose(
    admission: AdmissionHarness, resource_type: str
) -> None:
    actor = admission.actor()
    target = (
        admission.variant(actor) if resource_type == "DOWNLOAD_INTENT" else admission.upload(actor)
    )
    admission.budget()
    operation = admission.service.acquire(
        actor,
        ResourceRequest(
            uuid4(),
            ResourceKind.TRANSFER,
            resource_type,
            uuid4(),
            target,
        ),
    )
    activation = fence(operation)
    for other in {"DOWNLOAD_INTENT", "UPLOAD_INTENT", "PLAY_INSTANCE", "INTERNET_ACQUISITION"} - {
        resource_type,
    }:
        with pytest.raises(ResourceAdmissionError, match="resource_purpose_mismatch"):
            admission.service.open_io(actor, activation, target, resource_type=other)
    permit = admission.service.open_io(actor, activation, target, resource_type=resource_type)
    with pytest.raises(ResourceAdmissionError, match="resource_purpose_mismatch"):
        admission.service.renew_io(actor, permit, target_id=target, resource_type="PLAY_INSTANCE")
    with pytest.raises(ResourceAdmissionError, match="resource_target_mismatch"):
        admission.service.renew_io(actor, permit, target_id=uuid4(), resource_type=resource_type)
    admission.service.close_io(permit)


def test_playback_variant_must_belong_to_current_actor(admission: AdmissionHarness) -> None:
    actor, stranger = admission.actor(), admission.actor()
    target = admission.variant(stranger)
    admission.budget()
    activation = fence(admission.service.acquire(actor, play()))
    with pytest.raises(ResourceAdmissionError, match="resource_target_unavailable"):
        admission.service.open_io(actor, activation, target, resource_type="PLAY_INSTANCE")
