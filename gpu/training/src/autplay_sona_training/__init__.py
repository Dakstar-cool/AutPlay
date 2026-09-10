"""Isolated Sona-Lite tokenizer, model training, and export package."""

from .benchmark import SonaBenchmarkResult, benchmark_sona_checkpoint
from .dataset import SonaTensorDataset, load_sona_dataset, materialize_sona_dataset
from .fixture import SonaFixtureBundle, materialize_synthetic_fixture_bundle
from .model import SonaLiteConfig, SonaLiteModel
from .objective import TorchSonaLossBreakdown, compute_torch_sona_losses
from .tokenizer import (
    SONA_MAX_ACTIVE_TOKENIZER_CODES,
    SONA_MAX_EMBEDDING_DIMENSIONS,
    SONA_MAX_TOKENIZER_RECORDINGS,
    SonaTokenizerFit,
    compute_source_embeddings_sha256,
    fit_residual_tokenizer,
    load_sona_tokenizer,
    materialize_sona_tokenizer,
)
from .trainer import (
    SonaTrainingConfig,
    SonaTrainingResult,
    load_quality_sona_checkpoint,
    load_sona_checkpoint,
    train_quality_sona_checkpoint,
    train_sona_checkpoint,
)

__all__ = (
    "SONA_MAX_ACTIVE_TOKENIZER_CODES",
    "SONA_MAX_EMBEDDING_DIMENSIONS",
    "SONA_MAX_TOKENIZER_RECORDINGS",
    "SonaBenchmarkResult",
    "SonaFixtureBundle",
    "SonaLiteConfig",
    "SonaLiteModel",
    "SonaTensorDataset",
    "SonaTokenizerFit",
    "SonaTrainingConfig",
    "SonaTrainingResult",
    "TorchSonaLossBreakdown",
    "benchmark_sona_checkpoint",
    "compute_source_embeddings_sha256",
    "compute_torch_sona_losses",
    "fit_residual_tokenizer",
    "load_quality_sona_checkpoint",
    "load_sona_checkpoint",
    "load_sona_dataset",
    "load_sona_tokenizer",
    "materialize_sona_dataset",
    "materialize_sona_tokenizer",
    "materialize_synthetic_fixture_bundle",
    "train_quality_sona_checkpoint",
    "train_sona_checkpoint",
)
