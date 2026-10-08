# Portable account feature configuration (implementation draft)

This describes implemented local routes and storage, not a production rollout or a verified release.
The portability [design draft](../design/explorations/AutPlay_Portable_Local_Account_Contract_Draft_v1.md)
and [privacy runbook](PRIVACY_DELETE_EXPORT.md) describe data/credential boundaries.

## Configuration

`PORTABLE_ACCOUNTS_ENABLED` defaults to false. Enabling it requires a persistent
`PROFILE_IDENTITY_PRIVATE_KEY_PEM` (using the existing secret-file settings mechanism); startup
rejects a missing identity. Use the established server identity rather than generating a new key
for every restart. Never put key material, passwords or recovery codes in logs, commands or evidence.
The discovery capability is `portable_accounts` when enabled.

`SELF_DEVICE_PAIRING_ENABLED` independently defaults to false and also requires persistent server
identity. Its discovery capability is `self_device_pairing`, used by the Add Phone scanner flow.
`PASSWORD_LOGIN_ENABLED` is the older unsupported password-login configuration; it does not enable
portable root proofs and remains rejected by the missing persistence-contract guard.

Current server metadata readiness expects Alembic `0066_portable_local_account`. Apply migrations
through the existing controlled deployment procedure; this document does not instruct a deployment
or enable either flag on a persistent target. A development downgrade is accepted only when all
four new tables are empty, preserving the mandatory clean lifecycle and blocking data loss.

## API bounds

All portable routes send no-store/no-cache responses. Account commands require JSON with exact
members, reject duplicate fields and are limited to 16,384 bytes. Data POST is limited to
524,288 bytes and 1..32 records, using the
[record schema](../../contracts/portable-account/v1/account-record.schema.json). Standard JSON
Schema expresses field shapes; runtime additionally checks canonical UUIDs, integer tokens,
canonical document byte size and LISTENING record ID membership in its alias set.

GET `/portable-accounts/current` requires an active account/device/session. Its encrypted passport
is credential material for authorized account-copy flows, never ordinary archive data.
POST `/portable-accounts/login-challenge` takes `public_id`. POST `register-challenge`,
`claim-challenge` and `rotation-challenge` take `account`; CLAIM requires the active existing owner.
POST `reauth-challenge` takes `action`, `operation_id`, `target_device_id` and authenticates the
current owner. POST `complete` takes `request`, `proof`, plus `replacement_proof` for ROTATE only.
BACKUP/PASSWORD_CHANGE names in the storage CHECK are reserved purposes, not additional HTTP APIs.

GET `/portable-accounts/records` authenticates the owner and accepts `source`, `after_time` and
`after_id`. Sources are `records`, `history`, `preferences`, `playlists`, `playlist_entries`, `tastes`.
Defaults are `records`, `1970-01-01T00:00:00Z` and nil UUID. Cursor timestamps must be UTC-compatible,
include an offset and not precede 1970; timestamp text is bounded to 40 characters. Each page has
`account_id`, `records`, `cursor` and `next`; follow `next` using its `time` and `id`, and stop when
`next` is null. Cursors are separate per source. POST takes `account_id` and `records`; successful
response lists accepted normalized records. Account ID must match the active principal.

Server limits remain 200,000 stored records/account and 64 MiB of serialized JSON. This is metadata
transfer; it grants no target-server Vault authority or access to source media bytes. Existing
legacy sources remain exportable without first uploading normalized copies from an updated phone.

## Ordinary owner export

`PortableAccountOwnerExportService.page` is an internal operator-assisted helper, with no new HTTP
route or archive transport. It takes an authenticated owner principal and revalidates account,
device and session on every page. It accepts separate `records` and `taste` sources, each with a
32-row UTC timestamp/UUID cursor. The identity projection contains only key thumbprint, revision
and creation/update timestamps; the response also includes account ID and current public ID.
The helper never selects passport JSON, private encrypted envelopes, recovery code or challenge
receipts. Unexpected stored record fields fail closed rather than entering the export.

Use a quiesced isolated copy for consistent multi-page archives. This helper covers portable
records/typed taste; the established runbook still governs full library, legacy history, playlists,
owned media, manifest encryption, approved transfer and temporary archive retention. Exporting
ordinary owner data is different from an explicitly requested secure recovery-file export.
