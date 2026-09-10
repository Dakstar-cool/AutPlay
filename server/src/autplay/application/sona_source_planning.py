"""Deterministic request-level time splits for an authorized Sona quality source."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import new as new_hmac
from math import isfinite
from typing import Final, cast
from uuid import UUID

import rfc8785

from autplay.application.sona import sona_inference_request_document
from autplay.domain.recommendations import JsonValue
from autplay.domain.sona import SonaInferenceRequest
from autplay.domain.sona_approval import SONA_LABEL_DELAY_EMBARGO_MS, SONA_SPLIT_POLICY

SONA_SOURCE_SPLIT_PLAN_KIND: Final = "SONA_SOURCE_SPLIT_PLAN_V1"
SONA_SOURCE_SPLIT_ALGORITHM: Final = "GLOBAL_CUTOFF_TIME_60_20_20_V1"
SONA_SOURCE_REKEY_PLAN_KIND: Final = "SONA_SOURCE_REKEY_PLAN_V1"
SONA_SOURCE_REKEY_ALGORITHM: Final = "PRESERVE_P11_TIME_SPLIT_MEMBERSHIP_V1"
SONA_MAX_QUALITY_SOURCE_REQUESTS: Final = 4_096
P11_CANONICAL_REQUEST_SHA256_V1: Final = "P11_CANONICAL_REQUEST_SHA256_V1"
SONA_INFERENCE_REQUEST_SHA256_V1: Final = "SONA_INFERENCE_REQUEST_SHA256_V1"
SONA_SOURCE_REQUEST_IDENTITY_KINDS: Final = frozenset(
    {P11_CANONICAL_REQUEST_SHA256_V1, SONA_INFERENCE_REQUEST_SHA256_V1}
)


@dataclass(frozen=True, slots=True)
class SonaSourceRequestIdentity:
    """Owner-pseudonymous identity and time boundary for one prospective example."""

    request_sha256: str
    owner_lineage_token: str
    cutoff_at_ms: int
    observed_at_ms: int

    def __post_init__(self) -> None:
        _validate_sha256(self.request_sha256, "request_sha256")
        _validate_sha256(self.owner_lineage_token, "owner_lineage_token")
        if not (
            0
            <= self.cutoff_at_ms
            < self.observed_at_ms
            <= self.cutoff_at_ms + SONA_LABEL_DELAY_EMBARGO_MS
        ):
            raise ValueError("Sona source outcome is outside the immutable label window")


@dataclass(frozen=True, slots=True)
class SonaSourceRequestObservation:
    """Transient owner-bearing row that must never enter the persisted plan."""

    request_sha256: str
    owner_user_id: UUID
    cutoff_at_ms: int
    observed_at_ms: int

    def __post_init__(self) -> None:
        _validate_sha256(self.request_sha256, "request_sha256")
        if not (
            0
            <= self.cutoff_at_ms
            < self.observed_at_ms
            <= self.cutoff_at_ms + SONA_LABEL_DELAY_EMBARGO_MS
        ):
            raise ValueError("Sona source outcome is outside the immutable label window")


@dataclass(frozen=True, slots=True)
class SonaSourcePlanningRecord:
    """Transient exact P11 snapshot and attributed outcome selected by schema 0026."""

    observation: SonaSourceRequestObservation
    request_document: dict[str, JsonValue]
    snapshot_document: dict[str, JsonValue]
    baseline_snapshot_id: UUID
    input_snapshot_sha256: str
    interaction_watermark: int
    catalog_snapshot: int
    availability_snapshot: str
    policy_snapshot_sha256: str
    retained_until: datetime
    outcome_source_event_id: UUID
    outcome_source_request_sha256: str
    outcome_recording_id: UUID
    outcome_played_ms: int
    outcome_completion_ratio: float | None
    outcome_excluded_from_taste: bool

    def __post_init__(self) -> None:
        _validate_sha256(self.input_snapshot_sha256, "input_snapshot_sha256")
        _validate_sha256(self.policy_snapshot_sha256, "policy_snapshot_sha256")
        _validate_sha256(
            self.outcome_source_request_sha256,
            "outcome_source_request_sha256",
        )
        ratio = self.outcome_completion_ratio
        if (
            self.interaction_watermark < 0
            or self.catalog_snapshot < 0
            or self.outcome_played_ms < 0
            or not self.availability_snapshot
            or self.retained_until.tzinfo is None
            or self.outcome_excluded_from_taste
            or (ratio is not None and (not isfinite(ratio) or not 0.0 <= ratio <= 1.0))
        ):
            raise ValueError("Sona source planning record is invalid")


@dataclass(frozen=True, slots=True)
class SonaSourceRequestRekey:
    """One reviewed P11-to-Sona identity transition with unchanged temporal lineage."""

    p11_request_sha256: str
    sona_request_sha256: str
    owner_lineage_token: str
    cutoff_at_ms: int
    observed_at_ms: int

    def __post_init__(self) -> None:
        _validate_sha256(self.p11_request_sha256, "p11_request_sha256")
        _validate_sha256(self.sona_request_sha256, "sona_request_sha256")
        _validate_sha256(self.owner_lineage_token, "owner_lineage_token")
        if self.p11_request_sha256 == self.sona_request_sha256:
            raise ValueError("Sona source re-key did not change request identity stage")
        if not (
            0
            <= self.cutoff_at_ms
            < self.observed_at_ms
            <= self.cutoff_at_ms + SONA_LABEL_DELAY_EMBARGO_MS
        ):
            raise ValueError("Sona source re-key outcome is outside the immutable label window")


@dataclass(frozen=True, slots=True)
class SonaSourceSplitPlan:
    """Exact selected membership plus a canonical review document."""

    request_identity_kind: str
    owner_lineage_key_id: str
    train: tuple[SonaSourceRequestIdentity, ...]
    validation: tuple[SonaSourceRequestIdentity, ...]
    test: tuple[SonaSourceRequestIdentity, ...]
    excluded_request_count: int
    request_set_sha256: str
    document: dict[str, JsonValue]
    plan_sha256: str

    @property
    def selected(self) -> tuple[SonaSourceRequestIdentity, ...]:
        return (*self.train, *self.validation, *self.test)


def plan_sona_quality_source_splits(
    requests: Sequence[SonaSourceRequestIdentity],
    *,
    request_identity_kind: str,
    owner_lineage_key_id: str,
) -> SonaSourceSplitPlan:
    """Assign all eligible requests outside two fixed label-delay embargo gaps."""

    if request_identity_kind not in SONA_SOURCE_REQUEST_IDENTITY_KINDS:
        raise ValueError("Sona source request_identity_kind is unsupported")
    _validate_label(owner_lineage_key_id, "owner_lineage_key_id")
    values = tuple(requests)
    if not 3 <= len(values) <= SONA_MAX_QUALITY_SOURCE_REQUESTS:
        raise ValueError("Sona quality source request count is outside the accepted bound")
    request_hashes = tuple(value.request_sha256 for value in values)
    if len(set(request_hashes)) != len(request_hashes):
        raise ValueError("Sona quality source contains duplicate request identities")
    ordered = tuple(sorted(values, key=lambda value: (value.cutoff_at_ms, value.request_sha256)))
    minimum_ms = ordered[0].cutoff_at_ms
    maximum_ms = ordered[-1].cutoff_at_ms
    span_ms = maximum_ms - minimum_ms
    usable_ms = span_ms - 2 * SONA_LABEL_DELAY_EMBARGO_MS
    if usable_ms <= 0:
        raise ValueError("Sona quality source cannot fit two label-delay embargoes")

    train_end_ms = minimum_ms + usable_ms * 60 // 100
    validation_start_ms = train_end_ms + SONA_LABEL_DELAY_EMBARGO_MS
    validation_end_ms = minimum_ms + usable_ms * 80 // 100 + SONA_LABEL_DELAY_EMBARGO_MS
    test_start_ms = minimum_ms + usable_ms * 80 // 100 + 2 * SONA_LABEL_DELAY_EMBARGO_MS
    train = tuple(value for value in ordered if value.cutoff_at_ms <= train_end_ms)
    validation = tuple(
        value for value in ordered if validation_start_ms <= value.cutoff_at_ms <= validation_end_ms
    )
    test = tuple(value for value in ordered if test_start_ms <= value.cutoff_at_ms <= maximum_ms)
    if not train or not validation or not test:
        raise ValueError("Sona quality source produces an empty time split")
    if max(value.observed_at_ms for value in train) >= min(
        value.cutoff_at_ms for value in validation
    ) or max(value.observed_at_ms for value in validation) >= min(
        value.cutoff_at_ms for value in test
    ):
        raise ValueError("Sona quality source label evidence crosses a later split boundary")
    _verify_owner_time_order(train, validation, test)

    selected = (*train, *validation, *test)
    request_set_sha256 = _set_sha256({value.request_sha256 for value in selected})
    split_documents: list[JsonValue] = [
        _split_document("train", train),
        _split_document("validation", validation),
        _split_document("test", test),
    ]
    owner_tokens = {value.owner_lineage_token for value in selected}
    owner_lineage_document: dict[str, JsonValue] = {
        "schema_version": 1,
        "scheme": "HMAC_SHA256_OWNER_UUID_BYTES_V1",
        "owner_lineage_key_id": owner_lineage_key_id,
        "owner_count": len(owner_tokens),
        "tokens": cast(list[JsonValue], sorted(owner_tokens)),
    }
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "plan_kind": SONA_SOURCE_SPLIT_PLAN_KIND,
        "request_identity_kind": request_identity_kind,
        "split_policy": SONA_SPLIT_POLICY,
        "algorithm": SONA_SOURCE_SPLIT_ALGORITHM,
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "quality_eligible": False,
        "source_request_count": len(values),
        "selected_request_count": len(selected),
        "excluded_request_count": len(values) - len(selected),
        "owner_lineage_key_id": owner_lineage_key_id,
        "owner_count": len(owner_tokens),
        "owner_lineage_manifest_sha256": sha256(rfc8785.dumps(owner_lineage_document)).hexdigest(),
        "request_set_sha256": request_set_sha256,
        "boundaries": {
            "source_cutoff_min_ms": minimum_ms,
            "train_end_ms": train_end_ms,
            "validation_start_ms": validation_start_ms,
            "validation_end_ms": validation_end_ms,
            "test_start_ms": test_start_ms,
            "source_cutoff_max_ms": maximum_ms,
        },
        "request_hash_overlap_count": 0,
        "owner_time_ordering_violation_count": 0,
        "label_boundary_violation_count": 0,
        "splits": split_documents,
    }
    plan_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    return SonaSourceSplitPlan(
        request_identity_kind,
        owner_lineage_key_id,
        train,
        validation,
        test,
        len(values) - len(selected),
        request_set_sha256,
        document,
        plan_sha256,
    )


def rekey_sona_quality_source_plan(
    p11_plan: SonaSourceSplitPlan,
    rekeys: Sequence[SonaSourceRequestRekey],
) -> SonaSourceSplitPlan:
    """Preserve approved P11 split lineage while deriving canonical Sona identities."""

    _verify_plan_integrity(p11_plan)
    if p11_plan.request_identity_kind != P11_CANONICAL_REQUEST_SHA256_V1:
        raise ValueError("Sona source re-key requires a P11 identity plan")
    values = tuple(rekeys)
    selected_by_hash = {value.request_sha256: value for value in p11_plan.selected}
    p11_hashes = tuple(value.p11_request_sha256 for value in values)
    sona_hashes = tuple(value.sona_request_sha256 for value in values)
    if len(values) != len(selected_by_hash) or set(p11_hashes) != set(selected_by_hash):
        raise ValueError("Sona source re-key does not cover the exact selected P11 membership")
    if len(set(p11_hashes)) != len(p11_hashes) or len(set(sona_hashes)) != len(sona_hashes):
        raise ValueError("Sona source re-key contains duplicate request identities")
    if set(p11_hashes) & set(sona_hashes):
        raise ValueError("Sona source re-key identity stages overlap")
    rekey_by_p11 = {value.p11_request_sha256: value for value in values}
    if any(
        (
            rekey_by_p11[p11_hash].owner_lineage_token,
            rekey_by_p11[p11_hash].cutoff_at_ms,
            rekey_by_p11[p11_hash].observed_at_ms,
        )
        != (
            identity.owner_lineage_token,
            identity.cutoff_at_ms,
            identity.observed_at_ms,
        )
        for p11_hash, identity in selected_by_hash.items()
    ):
        raise ValueError("Sona source re-key changed owner or temporal lineage")

    def rekey_split(
        split: tuple[SonaSourceRequestIdentity, ...],
    ) -> tuple[SonaSourceRequestIdentity, ...]:
        return tuple(
            SonaSourceRequestIdentity(
                request_sha256=rekey_by_p11[value.request_sha256].sona_request_sha256,
                owner_lineage_token=value.owner_lineage_token,
                cutoff_at_ms=value.cutoff_at_ms,
                observed_at_ms=value.observed_at_ms,
            )
            for value in split
        )

    train = rekey_split(p11_plan.train)
    validation = rekey_split(p11_plan.validation)
    test = rekey_split(p11_plan.test)
    selected = (*train, *validation, *test)
    request_set_sha256 = _set_sha256({value.request_sha256 for value in selected})
    owner_tokens = {value.owner_lineage_token for value in selected}
    owner_lineage_document = _owner_lineage_document(
        p11_plan.owner_lineage_key_id,
        owner_tokens,
    )
    ordered_pairs: list[JsonValue] = [
        cast(JsonValue, [value.p11_request_sha256, value.sona_request_sha256])
        for value in sorted(values, key=lambda value: value.p11_request_sha256)
    ]
    split_documents: list[JsonValue] = [
        _split_document("train", train),
        _split_document("validation", validation),
        _split_document("test", test),
    ]
    document: dict[str, JsonValue] = {
        "schema_version": 1,
        "plan_kind": SONA_SOURCE_REKEY_PLAN_KIND,
        "request_identity_kind": SONA_INFERENCE_REQUEST_SHA256_V1,
        "split_policy": SONA_SPLIT_POLICY,
        "algorithm": SONA_SOURCE_REKEY_ALGORITHM,
        "label_delay_embargo_ms": SONA_LABEL_DELAY_EMBARGO_MS,
        "quality_eligible": False,
        "source_request_count": len(selected),
        "selected_request_count": len(selected),
        "excluded_request_count": 0,
        "owner_lineage_key_id": p11_plan.owner_lineage_key_id,
        "owner_count": len(owner_tokens),
        "owner_lineage_manifest_sha256": sha256(rfc8785.dumps(owner_lineage_document)).hexdigest(),
        "request_set_sha256": request_set_sha256,
        "parent_request_identity_kind": P11_CANONICAL_REQUEST_SHA256_V1,
        "parent_request_set_sha256": p11_plan.request_set_sha256,
        "parent_plan_sha256": p11_plan.plan_sha256,
        "rekey_pair_set_sha256": sha256(rfc8785.dumps(ordered_pairs)).hexdigest(),
        "boundaries": deepcopy(p11_plan.document["boundaries"]),
        "request_hash_overlap_count": 0,
        "owner_time_ordering_violation_count": 0,
        "label_boundary_violation_count": 0,
        "splits": split_documents,
    }
    plan_sha256 = sha256(rfc8785.dumps(document)).hexdigest()
    plan = SonaSourceSplitPlan(
        SONA_INFERENCE_REQUEST_SHA256_V1,
        p11_plan.owner_lineage_key_id,
        train,
        validation,
        test,
        0,
        request_set_sha256,
        document,
        plan_sha256,
    )
    _verify_plan_integrity(plan)
    return plan


def derive_sona_source_request_rekey(
    observation: SonaSourceRequestObservation,
    request: SonaInferenceRequest,
    *,
    owner_lineage_hmac_key: bytes,
) -> SonaSourceRequestRekey:
    """Derive one re-key only from an exact canonical Sona request and source owner."""

    if not 32 <= len(owner_lineage_hmac_key) <= 1_024:
        raise ValueError("Sona owner-lineage HMAC key is outside the accepted bound")
    if request.owner_user_id != observation.owner_user_id:
        raise ValueError("Sona source re-key contains a cross-owner request")
    if request.cutoff_at_ms != observation.cutoff_at_ms:
        raise ValueError("Sona source re-key changed the request cutoff")
    actual_request_sha256 = sha256(
        rfc8785.dumps(sona_inference_request_document(request))
    ).hexdigest()
    if actual_request_sha256 != request.request_sha256:
        raise ValueError("Sona source re-key request hash is not canonical")
    return SonaSourceRequestRekey(
        p11_request_sha256=observation.request_sha256,
        sona_request_sha256=request.request_sha256,
        owner_lineage_token=new_hmac(
            owner_lineage_hmac_key,
            observation.owner_user_id.bytes,
            sha256,
        ).hexdigest(),
        cutoff_at_ms=observation.cutoff_at_ms,
        observed_at_ms=observation.observed_at_ms,
    )


def plan_sona_quality_source_observations(
    observations: Sequence[SonaSourceRequestObservation],
    *,
    request_identity_kind: str,
    owner_lineage_key_id: str,
    owner_lineage_hmac_key: bytes,
) -> SonaSourceSplitPlan:
    """Pseudonymize transient owners in memory, then build the persisted plan."""

    if not 32 <= len(owner_lineage_hmac_key) <= 1_024:
        raise ValueError("Sona owner-lineage HMAC key is outside the accepted bound")
    identities = tuple(
        SonaSourceRequestIdentity(
            request_sha256=value.request_sha256,
            owner_lineage_token=new_hmac(
                owner_lineage_hmac_key,
                value.owner_user_id.bytes,
                sha256,
            ).hexdigest(),
            cutoff_at_ms=value.cutoff_at_ms,
            observed_at_ms=value.observed_at_ms,
        )
        for value in observations
    )
    return plan_sona_quality_source_splits(
        identities,
        request_identity_kind=request_identity_kind,
        owner_lineage_key_id=owner_lineage_key_id,
    )


def _verify_owner_time_order(
    train: tuple[SonaSourceRequestIdentity, ...],
    validation: tuple[SonaSourceRequestIdentity, ...],
    test: tuple[SonaSourceRequestIdentity, ...],
) -> None:
    for left, right in ((train, validation), (validation, test), (train, test)):
        owners = {value.owner_lineage_token for value in left} & {
            value.owner_lineage_token for value in right
        }
        if any(
            max(value.cutoff_at_ms for value in left if value.owner_lineage_token == owner)
            >= min(value.cutoff_at_ms for value in right if value.owner_lineage_token == owner)
            for owner in owners
        ):
            raise ValueError("Sona quality source violates owner time ordering")


def _verify_plan_integrity(plan: SonaSourceSplitPlan) -> None:
    _validate_label(plan.owner_lineage_key_id, "owner_lineage_key_id")
    if plan.request_identity_kind not in SONA_SOURCE_REQUEST_IDENTITY_KINDS:
        raise ValueError("Sona source plan request identity kind is unsupported")
    selected = plan.selected
    boundaries = plan.document.get("boundaries")
    if not isinstance(boundaries, dict):
        raise ValueError("Sona source plan boundaries are invalid")
    request_hashes = {value.request_sha256 for value in selected}
    owner_tokens = {value.owner_lineage_token for value in selected}
    owner_lineage_document = _owner_lineage_document(
        plan.owner_lineage_key_id,
        owner_tokens,
    )
    expected_splits: list[JsonValue] = [
        _split_document("train", plan.train),
        _split_document("validation", plan.validation),
        _split_document("test", plan.test),
    ]
    if (
        not plan.train
        or not plan.validation
        or not plan.test
        or plan.excluded_request_count < 0
        or len(request_hashes) != len(selected)
        or max(value.observed_at_ms for value in plan.train)
        >= min(value.cutoff_at_ms for value in plan.validation)
        or max(value.observed_at_ms for value in plan.validation)
        >= min(value.cutoff_at_ms for value in plan.test)
        or plan.request_set_sha256 != _set_sha256(request_hashes)
        or plan.document.get("request_identity_kind") != plan.request_identity_kind
        or plan.document.get("owner_lineage_key_id") != plan.owner_lineage_key_id
        or plan.document.get("owner_count") != len(owner_tokens)
        or plan.document.get("owner_lineage_manifest_sha256")
        != sha256(rfc8785.dumps(owner_lineage_document)).hexdigest()
        or plan.document.get("selected_request_count") != len(selected)
        or plan.document.get("excluded_request_count") != plan.excluded_request_count
        or plan.document.get("source_request_count") != len(selected) + plan.excluded_request_count
        or plan.document.get("request_set_sha256") != plan.request_set_sha256
        or plan.document.get("quality_eligible") is not False
        or plan.document.get("request_hash_overlap_count") != 0
        or plan.document.get("owner_time_ordering_violation_count") != 0
        or plan.document.get("label_boundary_violation_count") != 0
        or plan.document.get("splits") != expected_splits
        or sha256(rfc8785.dumps(plan.document)).hexdigest() != plan.plan_sha256
    ):
        raise ValueError("Sona source plan integrity verification failed")
    _verify_owner_time_order(plan.train, plan.validation, plan.test)


def verify_sona_source_split_plan(plan: SonaSourceSplitPlan) -> None:
    """Fail closed when a persisted or transported split plan is internally inconsistent."""

    _verify_plan_integrity(plan)


def _owner_lineage_document(
    owner_lineage_key_id: str,
    owner_tokens: set[str],
) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "scheme": "HMAC_SHA256_OWNER_UUID_BYTES_V1",
        "owner_lineage_key_id": owner_lineage_key_id,
        "owner_count": len(owner_tokens),
        "tokens": cast(list[JsonValue], sorted(owner_tokens)),
    }


def _split_document(
    split: str, values: tuple[SonaSourceRequestIdentity, ...]
) -> dict[str, JsonValue]:
    request_hashes = {value.request_sha256 for value in values}
    return {
        "split": split,
        "request_count": len(values),
        "request_set_sha256": _set_sha256(request_hashes),
        "cutoff_min_ms": min(value.cutoff_at_ms for value in values),
        "cutoff_max_ms": max(value.cutoff_at_ms for value in values),
        "observed_max_ms": max(value.observed_at_ms for value in values),
    }


def _set_sha256(values: set[str]) -> str:
    return sha256(rfc8785.dumps(cast(list[JsonValue], sorted(values)))).hexdigest()


def _validate_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Sona source {field} is not a lowercase SHA-256 digest")


def _validate_label(value: str, field: str) -> None:
    if not 1 <= len(value) <= 200 or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise ValueError(f"Sona source {field} is invalid")


__all__ = (
    "P11_CANONICAL_REQUEST_SHA256_V1",
    "SONA_INFERENCE_REQUEST_SHA256_V1",
    "SONA_MAX_QUALITY_SOURCE_REQUESTS",
    "SONA_SOURCE_REKEY_ALGORITHM",
    "SONA_SOURCE_REKEY_PLAN_KIND",
    "SONA_SOURCE_REQUEST_IDENTITY_KINDS",
    "SONA_SOURCE_SPLIT_ALGORITHM",
    "SONA_SOURCE_SPLIT_PLAN_KIND",
    "SonaSourcePlanningRecord",
    "SonaSourceRequestIdentity",
    "SonaSourceRequestObservation",
    "SonaSourceRequestRekey",
    "SonaSourceSplitPlan",
    "derive_sona_source_request_rekey",
    "plan_sona_quality_source_observations",
    "plan_sona_quality_source_splits",
    "rekey_sona_quality_source_plan",
    "verify_sona_source_split_plan",
)
