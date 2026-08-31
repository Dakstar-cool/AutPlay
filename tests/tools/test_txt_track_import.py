from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_txt_track_import() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    import_paths = (str(repository_root), str(repository_root / "server" / "src"))
    sys.path[:0] = import_paths
    try:
        return importlib.import_module("scripts.txt_track_import")
    finally:
        for import_path in import_paths:
            sys.path.remove(import_path)


txt_track_import = _load_txt_track_import()


def test_numbered_collection_normalization_preserves_track_text() -> None:
    payload = (
        "\ufeff1. Ozzy Osbourne - Mama, I'm Coming Home\n"
        "2) 8(913) - Прыгай, за руки держась\n"
        "=== НЕДОСТУПНЫЕ ===\n"
        "  Unnumbered Artist - Unnumbered Title  \n"
    ).encode()

    normalized, stats = txt_track_import.normalize_numbered_collection(payload)

    assert normalized.decode() == (
        "Ozzy Osbourne - Mama, I'm Coming Home\n"
        "8(913) - Прыгай, за руки держась\n"
        "  Unnumbered Artist - Unnumbered Title  \n"
    )
    assert stats.numbered_prefix_count == 2
    assert stats.section_marker_count == 1
    assert stats.unnumbered_line_count == 1


def test_numbered_collection_normalization_keeps_server_input_bounds() -> None:
    payload = b"1. Artist - Title\n" * 10_001

    with pytest.raises(ValueError, match=r"import\.row_limit"):
        txt_track_import.normalize_numbered_collection(payload)


def test_cli_writes_new_normalized_file_and_refuses_overwrite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "numbered.txt"
    destination = tmp_path / "normalized.txt"
    source.write_text("1. Artist - Title\n2. Artist - Title\n", encoding="utf-8")

    assert txt_track_import.main([str(source), "--normalized-output", str(destination)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["valid_count"] == 2
    assert summary["duplicate_valid_row_count"] == 1
    assert summary["artist_count"] == 1
    assert "artists" not in summary
    assert summary["normalization"] == {
        "numbered_prefix_count": 2,
        "section_marker_count": 0,
        "unnumbered_line_count": 0,
    }
    assert destination.read_text(encoding="utf-8") == "Artist - Title\nArtist - Title\n"

    assert txt_track_import.main([str(source), "--normalized-output", str(destination)]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "import.file_unavailable"}


def test_artist_payload_requires_explicit_preview_opt_in(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "tracks.txt"
    source.write_text("Artist - Title\n", encoding="utf-8")

    assert txt_track_import.main([str(source), "--include-artists"]) == 0

    assert json.loads(capsys.readouterr().out)["artists"] == [{"name": "Artist", "track_count": 1}]
