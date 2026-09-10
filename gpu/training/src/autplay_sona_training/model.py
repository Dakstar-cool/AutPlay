"""Compact shared-encoder Sona-Lite model sized for the RTX 3060 boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import torch
from autplay.domain.sona import (
    SONA_CODEBOOK_SIZE,
    SONA_MAX_HISTORY_EVENTS,
    SONA_RANKING_HEADS,
    SONA_SID_DEPTH,
)
from torch import Tensor, nn

SONA_ACTION_VOCABULARY_SIZE = 10
SONA_ORIGIN_VOCABULARY_SIZE = 6
SONA_AGE_VOCABULARY_SIZE = 64
SONA_MAX_SEED_VOCABULARY_SIZE = 4_096


@dataclass(frozen=True, slots=True)
class SonaLiteConfig:
    codebook_size: int = SONA_CODEBOOK_SIZE
    model_dimensions: int = 192
    encoder_layers: int = 3
    action_vocabulary_size: int = 10
    origin_vocabulary_size: int = 6
    age_vocabulary_size: int = 64
    seed_vocabulary_size: int = 4_096

    def __post_init__(self) -> None:
        if not 2 <= self.codebook_size <= SONA_CODEBOOK_SIZE:
            raise ValueError("Sona model codebook size is outside the accepted bound")
        if not 8 <= self.model_dimensions <= 512 or not 1 <= self.encoder_layers <= 8:
            raise ValueError("Sona model capacity is outside the accepted RTX 3060 bound")
        if (
            self.action_vocabulary_size != SONA_ACTION_VOCABULARY_SIZE
            or self.origin_vocabulary_size != SONA_ORIGIN_VOCABULARY_SIZE
            or self.age_vocabulary_size != SONA_AGE_VOCABULARY_SIZE
            or not 2 <= self.seed_vocabulary_size <= SONA_MAX_SEED_VOCABULARY_SIZE
        ):
            raise ValueError("Sona model categorical vocabulary is outside canonical bounds")


class SonaLiteModel(nn.Module):
    """One chronological encoder shared by SID generation and candidate ranking."""

    def __init__(self, config: SonaLiteConfig | None = None) -> None:
        super().__init__()
        self.config = config or SonaLiteConfig()
        config = self.config
        dimensions = config.model_dimensions
        self.sid_embeddings = nn.ModuleList(
            nn.Embedding(config.codebook_size, dimensions, padding_idx=0)
            for _ in range(SONA_SID_DEPTH)
        )
        self.action_embedding = nn.Embedding(
            config.action_vocabulary_size, dimensions, padding_idx=0
        )
        self.origin_embedding = nn.Embedding(
            config.origin_vocabulary_size, dimensions, padding_idx=0
        )
        self.age_embedding = nn.Embedding(config.age_vocabulary_size, dimensions, padding_idx=0)
        self.position_embedding = nn.Embedding(SONA_MAX_HISTORY_EVENTS, dimensions)
        self.encoder = nn.GRU(
            dimensions,
            dimensions,
            num_layers=config.encoder_layers,
            batch_first=True,
        )
        self.seed_embedding = nn.Embedding(config.seed_vocabulary_size, dimensions)
        self.decoder_start = nn.Parameter(torch.zeros(dimensions))
        self.decoder_cell = nn.GRUCell(dimensions, dimensions)
        self.decoder_output = nn.Linear(dimensions, config.codebook_size)
        self.ranking_module = nn.Sequential(
            nn.Linear(dimensions * 3, dimensions),
            nn.GELU(),
            nn.Linear(dimensions, len(SONA_RANKING_HEADS)),
        )
        combination_weights = torch.tensor((0.35, 0.30, -0.20, 0.15), dtype=torch.float32)
        self.register_buffer("ranking_combination_weights", combination_weights)
        self.ranking_combination_weights: Tensor = combination_weights
        semantic_output_bias = torch.zeros(config.codebook_size, dtype=torch.float32)
        semantic_output_bias[0] = -10_000.0
        self.register_buffer("semantic_output_bias", semantic_output_bias)
        self.semantic_output_bias: Tensor = semantic_output_bias

    def encode_history(
        self,
        history_sids: Tensor,
        history_actions: Tensor,
        history_origins: Tensor,
        history_age_buckets: Tensor,
        history_mask: Tensor,
    ) -> Tensor:
        """Encode the exact chronological state once for both downstream modules."""

        event_state = self._embed_sids(history_sids)
        event_state = event_state + self.action_embedding(history_actions)
        event_state = event_state + self.origin_embedding(history_origins)
        event_state = event_state + self.age_embedding(history_age_buckets)
        positions = torch.arange(history_sids.shape[1], device=history_sids.device)
        event_state = event_state + self.position_embedding(positions).unsqueeze(0)
        mask = history_mask.to(dtype=event_state.dtype).unsqueeze(-1)
        encoded, _ = self.encoder(event_state * mask)
        has_history = (history_mask.sum(dim=1, keepdim=True) > 0).to(dtype=encoded.dtype)
        return cast(Tensor, encoded[:, -1, :] * has_history)

    def training_forward(
        self,
        history_sids: Tensor,
        history_actions: Tensor,
        history_origins: Tensor,
        history_age_buckets: Tensor,
        history_mask: Tensor,
        candidate_sids: Tensor,
        candidate_mask: Tensor,
        seed: Tensor,
        target_sids: Tensor,
    ) -> tuple[Tensor, Tensor]:
        """Return teacher-forced SID logits and raw multi-head ranking logits."""

        user_state = self.encode_history(
            history_sids,
            history_actions,
            history_origins,
            history_age_buckets,
            history_mask,
        )
        hidden = user_state + self.seed_embedding(
            torch.remainder(seed, self.config.seed_vocabulary_size)
        )
        decoder_input = self.decoder_start.unsqueeze(0).expand_as(hidden)
        decoder_logits: list[Tensor] = []
        for level in range(SONA_SID_DEPTH):
            hidden = self.decoder_cell(decoder_input, hidden)
            decoder_logits.append(self.decoder_output(hidden) + self.semantic_output_bias)
            decoder_input = self.sid_embeddings[level](target_sids[:, level])
        ranking_logits = self._ranking_logits(user_state, candidate_sids)
        ranking_logits = ranking_logits * candidate_mask.unsqueeze(-1).to(ranking_logits.dtype)
        return torch.stack(decoder_logits, dim=1), ranking_logits

    def forward(
        self,
        history_sids: Tensor,
        history_actions: Tensor,
        history_origins: Tensor,
        history_age_buckets: Tensor,
        history_mask: Tensor,
        candidate_sids: Tensor,
        candidate_mask: Tensor,
        seed: Tensor,
    ) -> tuple[Tensor, Tensor, Tensor, Tensor]:
        """Greedy fixed-depth generation plus ranking for the ONNX serving contract."""

        user_state = self.encode_history(
            history_sids,
            history_actions,
            history_origins,
            history_age_buckets,
            history_mask,
        )
        hidden = user_state + self.seed_embedding(
            torch.remainder(seed, self.config.seed_vocabulary_size)
        )
        decoder_input = self.decoder_start.unsqueeze(0).expand_as(hidden)
        generated_tokens: list[Tensor] = []
        generated_log_probabilities: list[Tensor] = []
        for level in range(SONA_SID_DEPTH):
            hidden = self.decoder_cell(decoder_input, hidden)
            logits = self.decoder_output(hidden) + self.semantic_output_bias
            token = torch.argmax(logits, dim=1)
            log_probability = torch.log_softmax(logits, dim=1).gather(1, token.unsqueeze(1))
            generated_tokens.append(token)
            generated_log_probabilities.append(log_probability.squeeze(1))
            decoder_input = self.sid_embeddings[level](token)
        generated_sids = torch.stack(generated_tokens, dim=1).unsqueeze(1)
        generation_score = torch.stack(generated_log_probabilities, dim=1).sum(dim=1, keepdim=True)
        ranking_logits = self._ranking_logits(user_state, candidate_sids)
        ranking_head_scores = torch.sigmoid(ranking_logits)
        candidate_mask_float = candidate_mask.unsqueeze(-1).to(ranking_head_scores.dtype)
        ranking_head_scores = ranking_head_scores * candidate_mask_float
        ranking_scores = torch.sum(
            ranking_head_scores * self.ranking_combination_weights,
            dim=2,
        )
        ranking_scores = torch.where(
            candidate_mask > 0,
            ranking_scores,
            torch.full_like(ranking_scores, -10_000.0),
        )
        return generated_sids, generation_score, ranking_head_scores, ranking_scores

    def _embed_sids(self, semantic_ids: Tensor) -> Tensor:
        embeddings = [
            self.sid_embeddings[level](semantic_ids[..., level]) for level in range(SONA_SID_DEPTH)
        ]
        return torch.stack(embeddings, dim=0).sum(dim=0)

    def _ranking_logits(self, user_state: Tensor, candidate_sids: Tensor) -> Tensor:
        candidate_state = self._embed_sids(candidate_sids)
        expanded_user = user_state.unsqueeze(1).expand_as(candidate_state)
        features = torch.cat((candidate_state, expanded_user, candidate_state * expanded_user), 2)
        return cast(Tensor, self.ranking_module(features))


__all__ = (
    "SONA_ACTION_VOCABULARY_SIZE",
    "SONA_AGE_VOCABULARY_SIZE",
    "SONA_MAX_SEED_VOCABULARY_SIZE",
    "SONA_ORIGIN_VOCABULARY_SIZE",
    "SonaLiteConfig",
    "SonaLiteModel",
)
