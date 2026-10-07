"""Metadata-only discovery has an independent namespace from immutable source selections."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "metadata-discovery" / "v1"


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def examples() -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        json.loads((SCHEMAS / "page-examples.json").read_text(encoding="utf-8")),
    )


def test_generated_pages_are_bounded_namespaced_and_never_source_selections() -> None:
    schema = validator("page.schema.json")
    seen: set[str] = set()
    for page in examples():
        schema.validate(page)
        assert len(page["items"]) <= page["limit"]
        if page["next_offset"] is not None:
            assert page["offset"] < page["next_offset"] <= 1000
        for card in page["items"]:
            assert card["id"] == f"musicbrainz:{card['entity_type']}:{card['entity_id']}"
            assert card["acquisition_allowed"] is False
            assert "candidate_id" not in card and "search_id" not in card
            seen.add(card["entity_type"])
    assert seen == {"artist", "release", "recording", "release_track"}


@pytest.mark.parametrize(
    "mutation", ["acquire", "candidate", "invalid_uuid", "overlong_query", "album_download"]
)
def test_discovery_contract_rejects_fake_acquisition_or_unbounded_cards(mutation: str) -> None:
    page = copy.deepcopy(examples()[0])
    card = page["items"][0]
    if mutation == "acquire":
        card["acquisition_allowed"] = True
    elif mutation == "candidate":
        card["candidate_id"] = "B3j5Z5NkCxw"
    elif mutation == "invalid_uuid":
        card["entity_id"] = "not-a-uuid"
    elif mutation == "overlong_query":
        card["download_search_query"] = "a" * 201
    else:
        card["download_search_query"] = "download whole album"
    assert list(validator("page.schema.json").iter_errors(page))


def test_search_query_contract_preserves_existing_track_search_boundary() -> None:
    schema = validator("search-query.schema.json")
    schema.validate({"q": "AC/DC (Live)", "kind": "artist", "limit": 50, "offset": 1000})
    schema.validate({"q": "Album", "kind": "album"})
    for query in (
        {"q": "Track", "kind": "all"},
        {"q": "Track", "kind": "track"},
        {"q": " ", "kind": "artist"},
        {"q": "a" * 201, "kind": "album"},
        {"q": "Album", "kind": "album", "offset": 1001},
        {"q": "Album", "kind": "album", "operation_id": "unexpected"},
    ):
        assert list(schema.iter_errors(query))
