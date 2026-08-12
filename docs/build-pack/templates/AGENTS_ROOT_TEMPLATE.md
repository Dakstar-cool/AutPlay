# AGENTS.md

## Project

AutPlay is an Android-first, local-first music platform with an optional personal server.

## Read first

- `docs/design/AutPlay_Design_Package_v1.md`
- `docs/build-pack/PROMPT_PROTOCOL.md`
- current phase prompt and latest handoff

## Hard rules

- Work only within the current phase scope.
- Preserve unrelated user changes.
- Do not use destructive Git or database operations.
- Do not push, deploy, publish, or use real credentials without explicit approval.
- Keep scripts, code identifiers, configs, and comments in English/ASCII.
- Use `uv` for Python workflows.
- Keep the server CPU-only path free of CUDA dependencies.
- Do not add Redis, RabbitMQ, NATS, Kafka, MinIO, or a separate vector database without an accepted ADR and benchmark.
- Never use destructive Room or Alembic migration fallback.
- Do not duplicate Media3 download execution state in Room.
- Do not silently auto-merge uncertain recordings.

## Required checks

Use the canonical commands documented by P01. Run every check relevant to changed files and record the result in the phase handoff.

## Final response

Report outcome, changed files, checks, decisions, blockers, and handoff path. Do not paste full changed files.
