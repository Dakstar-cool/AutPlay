# P06 - Vault Ingest and Direct Streaming

Выполни только phase P06. Следуй common protocol и прочитай `HANDOFF_P05.md`.

## Цель

Реализовать crash-safe filesystem Vault path: authorized upload/acquisition -> staging -> validation/fingerprint -> SHA-256 dedup -> atomic commit -> authorized HTTP Range streaming.

## Inputs

- Product specification Vault/upload/stream/security sections
- System Architecture ingest, streaming, jobs and failure flows
- PostgreSQL Vault/import/job tables
- Track Identity specification
- `REFERENCE_PROJECTS.md` sections Navidrome and Jellyfin

## Scope

1. Filesystem Vault adapter behind port; generated storage keys only.
2. Bounded resumable upload session/chunks with idempotency.
3. Staging area on same filesystem/atomicity domain as final commit where required.
4. Streaming hash and byte-size verification.
5. Safe FFprobe/decode validation and technical metadata extraction.
6. Versioned Chromaprint/fpcalc generation as core ingest evidence.
7. Transactional metadata commit and atomic file move with reconciliation for crash windows.
8. Duplicate SHA handling without duplicate bytes.
9. Quarantine/corruption states and operational reconciliation command.
10. Authorized direct stream with `Range`, `If-Range`/ETag policy, content length/type and cancellation.
11. Job checkpoints, retry and audit events.

## Security constraints

- No shell interpolation; executable argument arrays only.
- Enforce size, time, chunk count, codec and resource limits.
- Reject path traversal, symlink escape and special files.
- Hash knowledge is not authorization.
- Private source URL/headers/tokens are never logged or returned.
- External downloader/scraper is not part of this phase.
- No silent overwrite of committed Vault bytes.
- Transcoding is optional and out of scope except an explicit tested seam.

## Required tests

- duplicate chunks/retry and wrong offset/hash;
- process death before/after file move and before/after DB commit;
- same SHA uploaded concurrently;
- corrupt/truncated/malicious metadata file;
- storage full/permission failure;
- quarantine and reconciliation;
- Range 200/206/416, partial reads, cancel and unauthorized user;
- stream while optional GPU/external Internet is unavailable;
- no orphan committed DB row or untracked final file after recovery.

## Acceptance

Repeated ingest converges to one immutable VaultObject, crash windows are recoverable, and authorized direct streaming serves exact bytes with correct HTTP semantics.

Create `HANDOFF_P06.md`, update A-011..A-013 and stop.
