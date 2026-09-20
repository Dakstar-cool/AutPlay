"""Bind the bounded maintenance command and reply to the same durable target."""

from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket


def result_identity(ticket: MaintenanceTicket) -> dict[str, object]:
    document: dict[str, object] = {"claim_id": str(ticket.claim_id), "action": ticket.action.value}
    if ticket.action == MaintenanceAction.INVENTORY:
        return document
    if ticket.action in (
        MaintenanceAction.ORPHAN_OBJECT,
        MaintenanceAction.ORPHAN_MISSING,
        MaintenanceAction.UPLOAD_CLEANUP,
    ):
        if ticket.storage_key is None:
            raise ValueError("maintenance_target_invalid")
        document["storage_key"] = ticket.storage_key.value
    else:
        document["provider_execution_id"] = str(ticket.provider_execution_id)
    return document
