"""Future C1 query/capacity/cleanup assertions; no PostgreSQL authorization proof."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from autplay.application.catalog_view import CatalogViewService
from autplay.domain.catalog_view import MAX_REPLY_BYTES, CatalogViewError, CatalogViewQuery
from autplay.ports.catalog_view import CatalogViewRead

SUBJECT = "8f38ab31-05b3-4bdd-8e80-9b0b00c18610"
POSITION = "38c0b829-499e-4f48-9172-80329f904b20"
RELEASE = "47214d34-8494-472d-948b-d55abc108e91"
# Synthetic wire witness, not a calculated catalog revision or crypto vector.
WITNESS = "ab" * 32


def _continuation() -> list[tuple[str, str]]:
    return [
        ("limit", "25"),
        ("view_revision", WITNESS),
        ("after_medium_position", "1"),
        ("after_sequence_no", "2"),
        ("after_release_track_id", POSITION),
    ]


@pytest.mark.parametrize("kind", ["recording_detail", "release_detail"])
def test_details_reject_every_query_argument(kind: Any) -> None:
    assert str(CatalogViewQuery.parse(kind, SUBJECT, []).subject_id) == SUBJECT
    for pairs in [[("limit", "100")], [("view_revision", WITNESS)], [("unknown", "value")]]:
        with pytest.raises(CatalogViewError) as refused:
            CatalogViewQuery.parse(kind, SUBJECT, pairs)
        assert (refused.value.status, refused.value.code) == (400, "request_validation_failed")


@pytest.mark.parametrize("value", ["0", "101", "01", "+1", " 1", "1 ", "1.0", "1e1", "\u0661", ""])
def test_position_limit_uses_canonical_ascii_grammar(value: str) -> None:
    with pytest.raises(CatalogViewError) as refused:
        CatalogViewQuery.parse("release_tracks", SUBJECT, [("limit", value)])
    assert refused.value.status == 400


def test_continuation_requires_the_complete_same_kind_tuple_and_explicit_limit() -> None:
    pairs = _continuation()
    query = CatalogViewQuery.parse("release_tracks", SUBJECT, pairs)
    assert query.limit == 25 and query.revision == WITNESS
    assert query.after is not None
    assert query.after.wire() == {
        "medium_position": 1,
        "sequence_no": 2,
        "release_track_id": POSITION,
    }
    for index in range(len(pairs)):
        with pytest.raises(CatalogViewError):
            CatalogViewQuery.parse("release_tracks", SUBJECT, pairs[:index] + pairs[index + 1 :])
    with pytest.raises(CatalogViewError):
        CatalogViewQuery.parse("recording_releases", SUBJECT, pairs)
    recording = CatalogViewQuery.parse(
        "recording_releases", SUBJECT, [*pairs, ("after_release_id", RELEASE)]
    )
    assert recording.after is not None and recording.after.wire()["release_id"] == RELEASE
    with pytest.raises(CatalogViewError):
        CatalogViewQuery.parse("release_tracks", SUBJECT, [*pairs, ("after_release_id", RELEASE)])


def test_duplicates_noncanonical_ids_and_witnesses_are_not_normalized() -> None:
    for pairs in [[("limit", "25"), ("limit", "25")], [("unknown", "value")]]:
        with pytest.raises(CatalogViewError):
            CatalogViewQuery.parse("release_tracks", SUBJECT, pairs)
    for subject in [SUBJECT.upper(), SUBJECT.replace("-", ""), "{" + SUBJECT + "}", " " + SUBJECT]:
        with pytest.raises(CatalogViewError):
            CatalogViewQuery.parse("release_tracks", subject, [])
    for witness in [WITNESS.upper(), WITNESS[:-1], WITNESS + "0", "g" * 64]:
        pairs = [
            (key, witness if key == "view_revision" else value) for key, value in _continuation()
        ]
        with pytest.raises(CatalogViewError):
            CatalogViewQuery.parse("release_tracks", SUBJECT, pairs)


class _Transaction:
    def __init__(self, document: dict[str, Any], failure: CatalogViewError | None = None) -> None:
        self.document, self.failure = document, failure
        self.events: list[str] = []
        self.token: str | None = None

    def read(self, query: CatalogViewQuery) -> dict[str, Any]:
        self.events.append("read")
        if self.failure is not None:
            raise self.failure
        return self.document

    @contextmanager
    def __call__(self, token: str) -> Iterator[CatalogViewRead]:
        self.token = token
        self.events.append("entered")
        try:
            yield self
        finally:
            self.events.append("released")


def test_complete_reply_at_byte_limit_is_preserved_and_overflow_refuses() -> None:
    overhead = len(b'{"value":""}')
    document = {"value": "a" * (MAX_REPLY_BYTES - overhead)}
    query = CatalogViewQuery.parse("release_detail", SUBJECT, [])
    transaction = _Transaction(document)
    body = CatalogViewService(transaction, lambda: None).prepare("synthetic-token", query)
    assert len(body) == MAX_REPLY_BYTES and json.loads(body) == document
    assert transaction.token == "synthetic-token" and transaction.events[-1] == "released"
    for value in [document["value"] + "a", "\u044f" * (MAX_REPLY_BYTES // 2)]:
        oversized = _Transaction({"value": value})
        with pytest.raises(CatalogViewError) as refused:
            CatalogViewService(oversized, lambda: None).prepare("synthetic-token", query)
        assert (refused.value.code, refused.value.status) == ("catalog_view_capacity", 503)
        assert oversized.events[-1] == "released"


def test_expiry_before_admission_and_during_cleanup_cannot_return_success() -> None:
    query = CatalogViewQuery.parse("recording_detail", SUBJECT, [])
    transaction = _Transaction({"title": "synthetic"})

    def expired() -> None:
        raise CatalogViewError("catalog_view_unavailable")

    with pytest.raises(CatalogViewError):
        CatalogViewService(transaction, expired).prepare("synthetic-token", query)
    assert transaction.events == [] and transaction.token is None

    def expired_after_release() -> None:
        if transaction.events and transaction.events[-1] == "released":
            expired()

    with pytest.raises(CatalogViewError):
        CatalogViewService(transaction, expired_after_release).prepare("synthetic-token", query)
    assert transaction.events[-1] == "released"


def test_reader_refusal_is_retained_and_transaction_cleanup_still_runs() -> None:
    refusal = CatalogViewError("catalog_view_not_found", 404)
    transaction = _Transaction({}, refusal)
    with pytest.raises(CatalogViewError) as refused:
        CatalogViewService(transaction, lambda: None).prepare(
            "synthetic-token",
            CatalogViewQuery.parse("release_tracks", SUBJECT, []),
        )
    assert refused.value is refusal
    assert transaction.events == ["entered", "read", "released"]
