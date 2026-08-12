# P10 - Import Adapters and Track Identity Resolution

Выполни только phase P10. Следуй common protocol и прочитай `HANDOFF_P09.md`.

## Цель

Реализовать auditable, resumable library import and identity resolution path, который сохраняет unresolved/ambiguous user intent и не делает ложных global merges.

## Inputs

- Product import/library migration/source requirements
- `docs/design/AutPlay_Track_Identity_v1.md`
- ER import/identity entities
- PostgreSQL import/match/source tables
- `REFERENCE_PROJECTS.md`: beets, MusicBrainz/Picard/Chromaprint, Music Assistant

## Scope

1. Versioned import envelope and parsers for supported user-owned CSV/JSON/HTML exports.
2. Golden fixtures including encoding, malformed rows, unavailable/gray items, duplicates and partial metadata.
3. Import job/entry checkpoints, per-row status, cancellation/resume and final report.
4. Source Adapter port with explicit capability/limits/credential/provenance contract.
5. Initial safe adapters:
   - local MediaStore/SAF files;
   - generic user export parser;
   - approved public metadata lookup where no private scraping is required.
6. Candidate generation from identifiers, normalized metadata, duration, release context, SHA and versioned fingerprint.
7. Evidence feature storage, hard conflicts, scoring, top-two margin and explanation.
8. `NO_MATCH`, `REVIEW_REQUIRED`, `RESOLVED` workflow and Android review UI.
9. Labeled benchmark harness with positives and hard negatives.
10. Merge/split/reassign change-set seam with audit; irreversible auto-merge remains disabled until gate.

## Constraints

- Do not scrape authenticated/private pages or bypass DRM/access controls.
- Do not implement a service-specific acquisition adapter without user approval and policy/API review.
- Provider/external ID and ISRC are evidence, not unique Recording key.
- Fingerprint mismatch/version marker conflict blocks auto-match.
- Raw source fields/provenance remain after resolution.
- Failed import row does not fail whole job unless envelope is invalid.
- No global Recording per poor export row.

## Required tests

- repeated import idempotency;
- resume after crash/cancel;
- duplicate rows and same Track in multiple playlists;
- malformed/unknown fields preserved/reported;
- studio vs live/remix/edit/remaster hard negatives;
- same recording across codecs/bitrates;
- fingerprint absent/ambiguous/wrong association;
- candidate tie and threshold boundary;
- merge rollback/change-set audit;
- no private credential/source URL in report/log;
- benchmark produces precision/recall/confusion/error slices.

## Acceptance

Supported exports produce a deterministic report and preserve every user intent; ambiguous items enter review; no auto-match is enabled without documented benchmark gate meeting Track Identity requirements.

Create `HANDOFF_P10.md`, update A-023..A-025 and stop.
