# Same-server public ID validation, 2026-10-06

Prepared in `D:\AutPlayProd\AutPlay` on the shared working copy based on
`a17a4569ab462f2c650c757d1da433cdd58c0b03`. This is local review evidence, not a deployment
or release claim. No commit, push, production database access, production migration or Gradle run
was performed by this backend task.

## Implemented boundary

- `GET /api/v1/social/public-id` returns the authenticated caller's null/unregistered or canonical
  confirmed registration, with no automatic backfill of existing accounts.
- `PUT /api/v1/social/public-id` confirms one case-insensitive ASCII ID, 3-24 letters/digits/
  underscores. Its UUID receipt binds account, device, action and canonical input. Confirmation
  does not rename display names or another account. A confirmed ID cannot be silently changed.
- `GET /api/v1/social/accounts/by-public-id/{public_id}` resolves one complete ID to the existing
  signed ContactCard. Missing, invalid, blocked and inactive targets share the same 404 envelope.
  There is no user list, prefix search, autocomplete, global registry or unauthenticated lookup.
- Failed probes consume their account budget; recorded success/denial replays remain idempotent.
  All commands using the shared social receipt table acquire their UUID transaction lock before
  account/session locks, closing cross-action and cross-owner duplicate-receipt races.
- Existing explicit friend acceptance, crossed requests, presence privacy and Room/Vault/account
  authority remain unchanged. See [ADR-055](../adr/ADR-055-same-server-public-id-exact-lookup.md).

## Source and configuration identity

[The JSON receipt](SOCIAL_PUBLIC_ID_VALIDATION_2026-10-06.json) records SHA-256 values for the
affected source/tests/contracts, pinned locks and disposable Compose inputs. Existing unrelated
pairing/admin/health and concurrent Android/metadata work was preserved. API/composition files
were not edited; the existing social router mounts the new endpoints.

The exact catalog was extended for `social.public_id_registration`. The existing working-copy
`approved_device_name` and `jobs.worker_health` additions also required manifest completion:
172 mapped tables, 1934 columns; the inventory subset excluding Wave has 165 tables. Counts and
the compiled mapping fingerprint were updated without weakening live catalog parity or PUBLIC
privilege gates.

## Disposable environment

- PostgreSQL 18.4 / pgvector 0.8.6, using the repository's pinned image and unchanged Compose files.
- Compose project: `autplay-public-id-20261006-7c31`, loopback port `6843`.
- Container: `autplay-public-id-20261006-7c31-postgres-1`.
- Volume: `autplay-public-id-20261006-7c31_postgres-data`.
- Network: `autplay-public-id-20261006-7c31_default`.
- Tests use only randomly generated `autplay_p02_*` databases through the repository's guarded
  fixture, including its cleanup. No persistent production database was used.

## Checks

All Python checks use `uv --frozen` and the committed root/server locks.

| Check | Result |
| --- | --- |
| Social, public-ID, private statistics and social HTTP regression | 39 passed |
| Static social contracts and OpenAPI validation | 9 passed |
| Ruff check and format for 13 affected server/migration/test files | Passed |
| Strict mypy for social service/router/new model/new PostgreSQL tests | Passed, 4 source files |
| Root contract Ruff/format/mypy | Passed |
| Exact schema/catalog, Alembic lifecycle and adjacent close-gates | 31 passed |

Final social command:

```powershell
$env:AUTPLAY_TEST_DATABASE_URL='postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:6843/autplay'
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/postgresql/test_social_public_id.py server/tests/postgresql/test_social_s1c.py server/tests/postgresql/test_profile_statistics_s2.py server/tests/runtime/test_social_http.py -q --tb=short
```

Final schema command:

```powershell
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/postgresql/test_metadata.py server/tests/postgresql/test_inventory.py server/tests/postgresql/test_migrations.py server/tests/postgresql/test_migration_close_gates.py -q --tb=short
```

Root contract command:

```powershell
uv run --frozen python -m pytest tests/contract/test_social_contract_v1.py -q --tb=short
```

Public-ID tests cover persisted canonical registration, absence of backfill, raw database unique/
CHECK constraints, exact replay, changed-body/cross-owner/cross-device denial, same-handle claims,
one account racing two aliases, same-operation races and collisions with existing friendship and
presence commands. They also prove signed-card reuse, no crossed-request autoaccept, block in
either direction, disabled/deletion-pending/deleted target concealment, revoked/expired/foreign
session denial, revoked device denial, negative lookup limits, denied registration limits, own
read limits, strict whitespace/Unicode rejection, no directory routes and hard-delete FK cleanup.
The forward migration test preserves an existing account's UUID, name and row version and proves
a populated downgrade refuses without data loss. The catalog gates prove empty clean upgrade,
downgrade, upgrade again, every adjacent revision and live Alembic metadata parity.

A broad server lint/typecheck snapshot was also attempted while other authorized workers were
editing metadata/acquisition code. It reported in-progress unrelated formatting/type errors; it
is not recorded as a completed whole-server gate. Those modules remain owned by their workers.
`scripts/check.ps1` was not run here, avoiding their shared full-gate resource coordination.

## Error handling audit

Two test fixture failures were corrected without altering database invariants: deletion-pending
fixtures now include a coherent deletion lifecycle record; expired sessions keep expiry after
issuance and advance the test clock to expiry.

After repeated patch context errors following formatting, the considered fixes were regenerating
fresh context, splitting hunks, `git apply --check`, `git apply --recount`, and a patch dry-run.
Fresh file reads and single-file patches were chosen, preserving shared edits. References:
[Git apply documentation](https://git-scm.com/docs/git-apply) and
[GNU patch options](https://www.gnu.org/software/diffutils/manual/html_node/patch-Options.html).
For mistaken file names, the considered fixes were file enumeration, `Test-Path -LiteralPath`,
`Resolve-Path -LiteralPath` and absolute paths; enumeration plus exact reads was chosen. References:
[Test-Path](https://learn.microsoft.com/powershell/module/microsoft.powershell.management/test-path),
[Get-ChildItem](https://learn.microsoft.com/powershell/module/microsoft.powershell.management/get-childitem)
and [Resolve-Path](https://learn.microsoft.com/powershell/module/microsoft.powershell.management/resolve-path).

## Migration and cleanup

Forward migration is `0063_social_public_id` after `0062_cpu_worker_health`, with readiness at the
same exact head. Upgrade creates one additive table, canonical CHECK, unique constraint and
account FK, with PUBLIC access revoked. Downgrade refuses while registrations or registration
receipts exist. Deployment must apply the forward migration before the matching runtime.

Disposable teardown completed; zero test databases, project containers, volumes and networks remained. The JSON receipt records this verification. There are 79 distinct selected passing tests across the final three runs; overlapping intermediate runs are not added to that count. Later shared
source or migration-head changes invalidate this exact snapshot until their affected gates run.
