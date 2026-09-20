"""Durable independent consent evidence; opening must never provision an absent store."""

from typing import Protocol
from uuid import UUID

from autplay.domain.training_consent import TrainingConsentHistory, TrainingConsentIntent


class TrainingConsentLedger(Protocol):
    def owner_tag(self, user_id: UUID) -> str: ...

    def actor_tag(self, device_id: UUID) -> str: ...

    def read(self) -> TrainingConsentHistory: ...

    def record(self, intent: TrainingConsentIntent) -> TrainingConsentIntent: ...
