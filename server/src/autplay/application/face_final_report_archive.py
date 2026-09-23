"""Canonical technical archive for one sealed Face final evaluation.

The archive carries no fixture/rater legal approval and cannot activate Face.
Its bytes belong in the bounded external evidence store, never in SQL approval
metadata or a client projection.
"""

from __future__ import annotations

import math
import sys
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import rfc8785

from autplay.application.face_final_evaluation import (
    FaceFinalCandidateInput,
    FaceFinalTechnicalReport,
    evaluate_face_final_candidate,
)
from autplay.application.face_final_statistics import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_FAMILIES,
    face_bootstrap_seed,
)
from autplay.application.face_rater_corpus import FaceRaterCorpusReference

_SCRIPT_NAMES = (
    "face_bakeoff.py",
    "face_bakeoff_metrics.py",
    "face_ordinal_alpha.py",
    "face_presentation_map.py",
    "face_qualification_segments.py",
    "face_rater_submission.py",
    "face_final_statistics.py",
    "face_rater_corpus.py",
    "face_final_evaluation.py",
    "face_final_report_archive.py",
)
_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024


class FaceFinalReportArchiveError(ValueError):
    """The complete technical report cannot be sealed reproducibly."""


@dataclass(frozen=True, slots=True)
class FaceFinalReportArchive:
    canonical_bytes: bytes
    report_sha256: str
    technical_pass: bool


def archive_face_final_candidate(
    qualification_manifest_sha256: str,
    corpus: FaceRaterCorpusReference,
    candidate: FaceFinalCandidateInput,
    *,
    exclusions: tuple[tuple[str, str], ...] = (),
) -> FaceFinalReportArchive:
    """Run the frozen evaluator once and retain every input, draw and code hash."""

    if (
        type(exclusions) is not tuple
        or len(exclusions) > 1_000
        or any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(value) is not str or not 1 <= len(value) <= 128 for value in item)
            for item in exclusions
        )
        or len({item[0] for item in exclusions}) != len(exclusions)
        or {item[0] for item in exclusions} & {track.track_id for track in corpus.tracks}
    ):
        raise FaceFinalReportArchiveError("invalid sealed Face exclusions")
    scripts = _script_hashes()
    technical: FaceFinalTechnicalReport = evaluate_face_final_candidate(
        qualification_manifest_sha256, corpus, candidate
    )
    if scripts != _script_hashes():
        raise FaceFinalReportArchiveError("Face evaluator changed during qualification")
    document = {
        "schema": "autplay.face.final-technical-report.v1",
        "authority": "TECHNICAL_ONLY_NO_FIXTURE_OR_RATER_APPROVAL",
        "qualification_manifest_sha256": qualification_manifest_sha256,
        "fixture_manifest_sha256": corpus.fixture_manifest_sha256,
        "candidate_manifest_sha256": candidate.candidate_manifest_sha256,
        "study_id": str(corpus.study_id),
        "technical_pass": technical.technical_pass,
        "exclusions": exclusions,
        "implementation": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "script_sha256": scripts,
        },
        "protocol": {
            "bootstrap_draws": BOOTSTRAP_DRAWS,
            "seed_by_family_decimal": {
                family: str(face_bootstrap_seed(qualification_manifest_sha256, family))
                for family in sorted(BOOTSTRAP_FAMILIES)
            },
            "axis_one_sided_alpha": "1/120",
            "singleton_one_sided_alpha": "1/20",
            "bootstrap_quantile": "EMPIRICAL_TYPE_1",
            "ece_bin_edges_milli": tuple(range(0, 1_001, 100)),
            "transition_match_window_ms": 3_000,
        },
        "reference_corpus": asdict(corpus),
        "candidate": asdict(candidate),
        "metrics_and_all_draws": asdict(technical),
    }
    try:
        canonical_bytes = rfc8785.dumps(_json_value(document))
    except (TypeError, ValueError, OverflowError) as error:
        raise FaceFinalReportArchiveError("final Face report cannot be canonicalized") from error
    if len(canonical_bytes) > _MAX_ARCHIVE_BYTES:
        raise FaceFinalReportArchiveError("final Face report exceeds evidence bound")
    return FaceFinalReportArchive(
        canonical_bytes,
        sha256(canonical_bytes).hexdigest(),
        technical.technical_pass,
    )


def _json_value(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is UUID:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise FaceFinalReportArchiveError("non-finite Face report value")
        return value
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise FaceFinalReportArchiveError("unsupported Face report value")


def _script_path(name: str) -> Path:
    root = Path(__file__).parent
    if name == "face_presentation_map.py":
        return root.parent / "domain" / name
    return root / name


def _script_hashes() -> dict[str, str]:
    return {name: sha256(_script_path(name).read_bytes()).hexdigest() for name in _SCRIPT_NAMES}
