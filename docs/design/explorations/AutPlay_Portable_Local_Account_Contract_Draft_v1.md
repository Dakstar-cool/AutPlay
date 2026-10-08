# Portable local account (implementation draft)

Status: local implementation, not a frozen contract or release acceptance report. The installed
production app and server must not be assumed to support the changes below. Current verification
belongs to the integration run's evidence, not historical test counts in this draft.

## User decisions (2026-10-08)

- Create an account offline on first launch with an entered Public ID and password.
- Keep its stable UUID when attaching or switching servers and preserve existing A55/M52 identity.
- Carry listening history, preferences, playlists and taste; show statistics for the whole account.
- Join another phone with the account-screen QR scanner, or sign in through a server with ID/password.
- Require ID/password for local disconnect and remote logout/device removal.
- Recover with an explicitly saved recovery file or code without replacing the account UUID.

## Identity and authority

An offline UUID and P-256 root key identify the account. Public ID is a human-readable alias;
there is no global alias registry across independent servers. Registration must reject conflicting
UUID/key or alias. CLAIM uses an existing authenticated device to preserve a legacy account UUID.
Portable registration grants USER authority, never an imported server administrator role.

Passwords unwrap the private root locally with PBKDF2-HMAC-SHA256 (600,000 iterations, salt16)
and AES-256-GCM (nonce12, account/login/key/purpose AAD). The server receives an encrypted passport
and canonical root proofs; it never receives the password or plaintext recovery code. Recovery uses
a separate random 256-bit code. Password and recovery material stay in encrypted credential storage
or transient secure-window UI, never ordinary DataStore, Room, WorkManager input, saved UI state,
normal logs or owner data exports. Passwords accept 15..128 Unicode code points; trivial repeated
values are rejected. Parsers reject unknown/duplicate fields and unsupported KDF parameters.

Device keys, sessions and the pinned server identity remain separate authorities. Server attachment
must not manufacture another personal identity. An imported root does not adopt unknown local data:
account ownership and import cutoff are explicit, and raw listening/outbox facts remain intact.

## Implemented server routes

See [feature configuration and API boundaries](../../operations/PORTABLE_ACCOUNTS.md).
All routes are disabled unless `PORTABLE_ACCOUNTS_ENABLED` is enabled with persistent server
identity. Responses use `Cache-Control: no-store` and `Pragma: no-cache`.

| Route | Behavior |
| --- | --- |
| GET `/portable-accounts/current` | Authenticated current passport and server public ID; specialized credential-copy route |
| POST `/portable-accounts/register-challenge` | Challenge for an offline identity's first copy |
| POST `/portable-accounts/claim-challenge` | Authenticated legacy UUID adoption |
| POST `/portable-accounts/login-challenge` | Public ID lookup and pinned, expiring sign-in challenge |
| POST `/portable-accounts/reauth-challenge` | Authenticated action/operation/device/session-bound lifecycle proof |
| POST `/portable-accounts/rotation-challenge` | Existing-root challenge for a replacement key with the same UUID |
| POST `/portable-accounts/complete` | Root proof completion; rotation also requires replacement-root proof |
| GET/POST `/portable-accounts/records` | Authenticated account-owned, bounded metadata transfer |

Challenges bind server ID, epoch, identity thumbprint, origins, account and passport hash. Root
proofs cover RFC 8785 canonical requests with `autplay:portable-account:<purpose>:v1` plus newline.
The client persists exact enrollment/rotation commands in encrypted storage before network writes.
Exact retries recover receipts without extending the original enrollment grant. Source attempt
limits commit before rejected account lookups/proofs. Same-device login retires previous sessions.

## Data portability

The [record schema](../../../contracts/portable-account/v1/account-record.schema.json) defines
strict LISTENING, PREFERENCE, PLAYLIST, PLAYLIST_ENTRY and TASTE documents. Requests contain
1..32 records and at most 524,288 bytes; individual canonical documents are at most 16,384 bytes.
Per-account portable storage is bounded to 200,000 records and 64 MiB of serialized JSON storage.
Pages contain at most 32 records with a UTC timestamp/UUID tuple cursor. POST requires body account
ID equal to the revalidated principal. GET sources are `records`, `history`, `preferences`,
`playlists`, `playlist_entries` and `tastes`, including projections of legacy server data.

LISTENING aliases include the logical record ID and at most 32 unique event IDs. Overlapping
aliases merge idempotently; server timestamps advance under an owner lock so updates do not slip
behind a prior cursor. Statistics deduplicate portable aliases without deleting immutable raw
listening facts. Imported history/preference/playlist/taste records apply to ordinary owner data;
taste has a typed `library.portable_taste_signal` projection. Personal metadata never imports
Vault grants, roles, device/session IDs, filesystem paths, source permissions or private origins.
Media bytes require separately authorized transfer. Recommendation output depends on the target
catalog/models; transferring taste/history does not promise identical rankings across servers.

## Exit and recovery

Remote logout-current, logout-all and device removal require a fresh account-root proof once a
portable root is configured. Local disconnect verifies ID/password and the captured binding before
clearing it. Legacy rootless accounts remain compatible with prior server flows; the portable UI
must first configure their root instead of presenting a passwordless portable exit.

Recovery and password change create a new P-256 root and fresh recovery code with the same account
UUID. Re-encrypting an unchanged private key alone is not revocation. Online rotation checks both
old and replacement proofs, revokes the selected server's previous sessions and requires sign-in
again. It cannot revoke old copies on other independent/offline servers. Encrypted local lineage
supports later propagation to a selected server; a lost response leaves a resumable exact command.
Recovery export uses strict v2 JSON (at most 65,536 bytes) with the current passport/code and up to
eight flat predecessor passport/code pairs for the same UUID/Public ID; legacy v1 imports are
accepted. Root/code correspondence and unique P-256 key thumbprints are checked. Lineage stays in
encrypted credential storage (49,152-byte limit); an absent legacy enrollment marker is treated
as already enrolled. An explicitly never-enrolled local root can rotate offline. A rootless file
restore makes no remote revocation claim; a rootless short code needs a trusted server passport.
Connecting to a server holding a retained predecessor proves old-to-current rotation first.
Reaching the lineage limit stops the operation instead of discarding a required predecessor.
Keep the newly exported recovery file after verified write/read-back. A code-only recovery needs
a stored passport or a trusted selected server's existing account copy; ID/password alone cannot
reconstruct never-uploaded history on a new phone.

## Privacy and schema boundary

Migration `0066_portable_local_account` adds portable identity, challenge, record and typed taste
storage without rewriting existing accounts. Identity/records have owner cascades; taste has
RESTRICT references and is explicitly removed before track references in accepted privacy purge.
Challenges intentionally have no account FK because registration precedes account creation.
Accepted purge removes account/device/session references, including nested challenge/receipt JSON,
then removes portable records, identity and tastes. Empty development downgrade restores the prior
purge function; any portable rows block rollback.

The ordinary export helper selects explicit public identity columns and validates each data record.
It excludes the identity passport, ciphertext/recovery fields and every challenge/receipt. It is
an internal owner-session-revalidated helper, not a new HTTP archive endpoint or a recovery backup.
See [privacy procedures](../../operations/PRIVACY_DELETE_EXPORT.md). The historical executable
[DDL baseline](../AutPlay_PostgreSQL_Schema_v1.sql) stays at 0019; its separately marked,
non-executing 0066 appendix is checked against migration structure and privacy function bodies.

## Required integration evidence

The implementation still requires the complete affected server and Android gates, real Room17->18
migration evidence and two-phone/server-switch/recovery acceptance. No older crypto/unit counts
prove the combined state. This draft does not authorize deployment, signing or destructive owner
operations and does not declare the production feature enabled or release verified.
