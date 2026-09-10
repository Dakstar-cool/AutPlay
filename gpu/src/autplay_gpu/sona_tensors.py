"""Deterministic tensor packing shared by Sona-Lite training and inference."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from autplay.domain.sona import SONA_MAX_CANDIDATES, SONA_MAX_HISTORY_EVENTS, SonaInferenceRequest

SONA_MAX_TRAINING_BATCH = 64

type Int64Array = npt.NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class SonaInputTensorBatch:
    """Fixed-bound tensors consumed by the shared Sona-Lite encoder graph."""

    history_sids: Int64Array
    history_actions: Int64Array
    history_origins: Int64Array
    history_age_buckets: Int64Array
    history_mask: Int64Array
    candidate_sids: Int64Array
    candidate_mask: Int64Array
    seed: Int64Array

    def as_feed(self) -> dict[str, npt.NDArray[np.generic]]:
        return {
            "history_sids": self.history_sids,
            "history_actions": self.history_actions,
            "history_origins": self.history_origins,
            "history_age_buckets": self.history_age_buckets,
            "history_mask": self.history_mask,
            "candidate_sids": self.candidate_sids,
            "candidate_mask": self.candidate_mask,
            "seed": self.seed,
        }


def pack_sona_requests(
    requests: tuple[SonaInferenceRequest, ...],
) -> SonaInputTensorBatch:
    """Left-pad chronological histories and right-pad candidate sets deterministically."""

    if not 1 <= len(requests) <= SONA_MAX_TRAINING_BATCH:
        raise ValueError("Sona tensor batch is empty or exceeds the accepted bound")
    batch_size = len(requests)
    history_sids = np.zeros((batch_size, SONA_MAX_HISTORY_EVENTS, 3), dtype=np.int64)
    history_actions = np.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_origins = np.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_age_buckets = np.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    history_mask = np.zeros((batch_size, SONA_MAX_HISTORY_EVENTS), dtype=np.int64)
    candidate_sids = np.zeros((batch_size, SONA_MAX_CANDIDATES, 3), dtype=np.int64)
    candidate_mask = np.zeros((batch_size, SONA_MAX_CANDIDATES), dtype=np.int64)
    seed = np.zeros((batch_size,), dtype=np.int64)

    for batch_index, request in enumerate(requests):
        history_start = SONA_MAX_HISTORY_EVENTS - len(request.history)
        for offset, event in enumerate(request.history, history_start):
            history_sids[batch_index, offset] = event.semantic_id.values
            history_actions[batch_index, offset] = int(event.action)
            history_origins[batch_index, offset] = int(event.origin)
            history_age_buckets[batch_index, offset] = event.age_bucket
            history_mask[batch_index, offset] = 1
        for candidate_index, candidate in enumerate(request.candidates):
            candidate_sids[batch_index, candidate_index] = candidate.semantic_id.values
            candidate_mask[batch_index, candidate_index] = 1
        seed[batch_index] = request.seed
    return SonaInputTensorBatch(
        history_sids=history_sids,
        history_actions=history_actions,
        history_origins=history_origins,
        history_age_buckets=history_age_buckets,
        history_mask=history_mask,
        candidate_sids=candidate_sids,
        candidate_mask=candidate_mask,
        seed=seed,
    )


__all__ = (
    "SONA_MAX_TRAINING_BATCH",
    "SonaInputTensorBatch",
    "pack_sona_requests",
)
