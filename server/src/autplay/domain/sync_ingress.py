"""Dormant normalized ingress v2 primitives; wire event schema stays at v1.

SYNC_PAYLOAD_PG_V1 binds UTF-8 PostgreSQL JSONB text, including numeric scale.
This is an integrity codec, never recommendation or acceptance authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

type PayloadScalar = str | int | bool | Decimal | None
DOMAIN = b"SYNC_PAYLOAD_PG_V1\x00"
MAX_PAYLOAD_BYTES = 8192
INTERACTION_AGGREGATES = {
    "LISTENING_EVENT_RECORDED": "LISTENING_EVENT",
    "RECOMMENDATION_IMPRESSION_RECORDED": "USER_INTERACTION_EVENT",
    "RECOMMENDATION_FEEDBACK_RECORDED": "USER_INTERACTION_EVENT",
}
LISTENING_FIELDS = frozenset(
    {
        "interaction_type",
        "local_user_track_ref_id",
        "server_user_track_ref_id",
        "recording_id",
        "played_ms",
        "track_duration_ms",
        "completion_ratio",
        "event_origin",
        "context",
        "explicit_feedback",
        "excluded_from_taste",
    }
)


def pg_jsonb_text_v1(payload: dict[str, PayloadScalar]) -> str:
    """Encode the bounded flat allowlisted payload subset of PostgreSQL JSONB.

    Floats are excluded: callers normalize ratio to Decimal with six places.
    Keys sort by UTF-8 byte length then bytes, as PostgreSQL JSONB does.
    """
    if type(payload) is not dict or len(payload) > 16:
        raise ValueError("invalid_normalized_payload")

    def string(value: str) -> str:
        if "\x00" in value:
            raise ValueError("invalid_normalized_payload")
        value.encode("utf-8")
        return json.dumps(value, ensure_ascii=False)

    def scalar(value: PayloadScalar) -> str:
        if value is None:
            return "null"
        if type(value) is bool:
            return "true" if value else "false"
        if type(value) is int:
            if not -(2**63) <= value < 2**63:
                raise ValueError("invalid_normalized_payload")
            return str(value)
        if isinstance(value, Decimal):
            if not value.is_finite() or abs(value) > 1 or value.as_tuple().exponent != -6:
                raise ValueError("invalid_normalized_payload")
            return format(abs(value) if value == 0 else value, "f")
        if type(value) is str and len(value) <= 200:
            return string(value)
        raise ValueError("invalid_normalized_payload")

    if any(type(key) is not str or len(key) > 100 for key in payload):
        raise ValueError("invalid_normalized_payload")
    keys = sorted(payload, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
    document = "{" + ", ".join(string(key) + ": " + scalar(payload[key]) for key in keys) + "}"
    if len(document.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("invalid_normalized_payload")
    return document


def sync_payload_pg_v1(payload: dict[str, PayloadScalar]) -> bytes:
    """Versioned, domain-separated byte encoder, matching SQL golden vectors."""
    return DOMAIN + pg_jsonb_text_v1(payload).encode("utf-8")


def normalized_payload_sha256(payload: dict[str, PayloadScalar]) -> bytes:
    return hashlib.sha256(sync_payload_pg_v1(payload)).digest()


def validate_normalized_payload(event_type: str, payload: dict[str, PayloadScalar]) -> None:
    """Require the exact noncausal allowlist; never accept client attribution claims."""
    pg_jsonb_text_v1(payload)
    expected = {"interaction_type"}
    if event_type == "LISTENING_EVENT_RECORDED":
        expected = set(LISTENING_FIELDS)
    elif event_type == "RECOMMENDATION_FEEDBACK_RECORDED":
        expected.add("feedback_type")
    elif event_type != "RECOMMENDATION_IMPRESSION_RECORDED":
        raise ValueError("invalid_normalized_payload")
    if set(payload) != expected or payload["interaction_type"] != event_type:
        raise ValueError("invalid_normalized_payload")
    if event_type == "RECOMMENDATION_FEEDBACK_RECORDED":
        _label(payload["feedback_type"], 64)
    if event_type != "LISTENING_EVENT_RECORDED":
        return
    for key in ("local_user_track_ref_id", "server_user_track_ref_id", "recording_id"):
        value = payload[key]
        if value is None and key != "local_user_track_ref_id":
            continue
        try:
            valid_uuid = isinstance(value, str) and str(UUID(value)) == value
        except ValueError:
            valid_uuid = False
        if not valid_uuid:
            raise ValueError("invalid_normalized_payload")
    for key, minimum in (("played_ms", 0), ("track_duration_ms", 1)):
        value = payload[key]
        if value is None and key == "track_duration_ms":
            continue
        if type(value) is not int or not minimum <= value <= 604800000:
            raise ValueError("invalid_normalized_payload")
    ratio = payload["completion_ratio"]
    if ratio is not None and (not isinstance(ratio, Decimal) or not 0 <= ratio <= 1):
        raise ValueError("invalid_normalized_payload")
    for key, bound in (("event_origin", 100), ("context", 50), ("explicit_feedback", 50)):
        _label(payload[key], bound)
    if type(payload["excluded_from_taste"]) is not bool:
        raise ValueError("invalid_normalized_payload")


def _label(value: PayloadScalar, bound: int) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0," + str(bound - 1) + "}", value) is None
    ):
        raise ValueError("invalid_normalized_payload")


@dataclass(frozen=True)
class NormalizedIngressV2:
    """Exact staging input to a restricted trusted writer, without acceptance facts.

    Original bounded request hash is supplied by the future validated ingress
    transaction. This type cannot authenticate a client or authorize attribution.
    """

    event_id: UUID
    user_id: UUID
    device_id: UUID
    journal_epoch: UUID
    device_sequence: int
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    occurred_at: datetime
    request_hash: bytes
    payload: dict[str, PayloadScalar]
    ingress_validation_version: int = 2
    schema_version: int = 1

    def validate(self) -> None:
        if (
            type(self.ingress_validation_version) is not int
            or self.ingress_validation_version != 2
            or type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.device_sequence) is not int
            or not 1 <= self.device_sequence <= 2**53 - 1
            or type(self.request_hash) is not bytes
            or len(self.request_hash) != 32
            or self.aggregate_id != self.event_id
            or INTERACTION_AGGREGATES.get(self.event_type) != self.aggregate_type
            or not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
            or any(
                not isinstance(value, UUID)
                for value in (
                    self.event_id,
                    self.user_id,
                    self.device_id,
                    self.journal_epoch,
                    self.aggregate_id,
                )
            )
        ):
            raise ValueError("invalid_normalized_ingress")
        validate_normalized_payload(self.event_type, self.payload)
