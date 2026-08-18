# AutPlay OpenAPI contracts

`v1/autplay-sync.openapi.json` is the versioned OpenAPI 3.1 source for the
authenticated device binding and sync boundary.  Cursor values are opaque
strings: the explicit `server_sequence` inside a returned event is ordered,
but clients must never derive, parse, or construct cursors from it.

This file is a static language-neutral contract. P04 does not mount these
operations in the P03 FastAPI process. `openapi-spec-validator==0.9.0` validates
the document inside `uv run --frozen pytest tests/contract`.
