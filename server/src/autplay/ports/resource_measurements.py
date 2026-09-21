"""Trusted local initialization with one atomic policy/audit transaction."""

from typing import Protocol

from autplay.domain.resource_measurements import InitializeResourceBudget


class ResourceBudgetInitializer(Protocol):
    def initialize(self, command: InitializeResourceBudget) -> dict[str, object]:
        """Check exact server identity, replay and CAS under the common admission lock."""
        ...
