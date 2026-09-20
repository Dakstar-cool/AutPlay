"""Strict bounded pages for the private incremental-inventory child pipe."""

from uuid import UUID

from autplay.application.vault_inventory import (
    MAX_INVENTORY_WORK,
    InventoryArea,
    InventoryEntry,
    InventoryPage,
)
from autplay.domain.vault import OpaqueStorageKey

from .vault_child import ChildProtocolError

MAX_INVENTORY_REPLY_BYTES = 32768
MAX_INVENTORY_SEQUENCE = 2**63 - 1


def page_command(sequence: int, maximum: int) -> dict[str, object]:
    if (
        type(sequence) is not int
        or not 1 <= sequence <= MAX_INVENTORY_SEQUENCE
        or type(maximum) is not int
        or not 1 <= maximum <= MAX_INVENTORY_WORK
    ):
        raise ChildProtocolError()
    return {"sequence": sequence, "maximum": maximum}


def page_document(claim_id: UUID, sequence: int, page: InventoryPage) -> dict[str, object]:
    return {
        "action": "INVENTORY",
        "claim_id": str(claim_id),
        "sequence": sequence,
        "work_units": page.work_units,
        "exhausted": page.exhausted,
        "entries": [{"area": entry.area.value, "key": entry.key.value} for entry in page.entries],
    }


def decode_page(
    document: dict[str, object], *, claim_id: UUID, sequence: int, maximum: int
) -> InventoryPage:
    if set(document) != {"action", "claim_id", "sequence", "work_units", "exhausted", "entries"}:
        raise ChildProtocolError()
    if (
        document["action"] != "INVENTORY"
        or document["claim_id"] != str(claim_id)
        or type(document["sequence"]) is not int
        or document["sequence"] != sequence
    ):
        raise ChildProtocolError()
    units, exhausted, values = document["work_units"], document["exhausted"], document["entries"]
    if (
        type(units) is not int
        or not 0 <= units <= maximum
        or type(exhausted) is not bool
        or not isinstance(values, list)
        or len(values) > units
        or (units == 0 and not exhausted)
    ):
        raise ChildProtocolError()
    entries: list[InventoryEntry] = []
    for item in values:
        if (
            not isinstance(item, dict)
            or set(item) != {"area", "key"}
            or not isinstance(item["area"], str)
            or not isinstance(item["key"], str)
        ):
            raise ChildProtocolError()
        entries.append(InventoryEntry(InventoryArea(item["area"]), OpaqueStorageKey(item["key"])))
    return InventoryPage(tuple(entries), units, exhausted)
