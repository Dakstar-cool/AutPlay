# Account recovery v1

Status: server and Android implementation locally verified; disabled by default. Physical
A55/system-picker/live-server acceptance remains required. Decision: ADR-053.

## Code and document

The client generates 32 independent random characters from `0123456789ABCDEFGHJKMNPQRSTVWXYZ`
(160 bits). Display groups of four separated by hyphens. Normalize ASCII case, spaces, tabs,
CR/LF and hyphens only; reject all other characters, confusables and inputs over 96 characters.
The verifier is SHA256 of ASCII `autplay:account-recovery-code:v1` followed by NUL, the server
UUID's 16 bytes, account UUID's 16 bytes, and the normalized 32 ASCII code characters.
The server stores verifiers only. Never put codes in URLs, logs, telemetry or diagnostic exports.

TXT is UTF-8 JSON, at most 4096 bytes, with exactly: `format=autplay-account-recovery`,
`version=1`, `server_instance_id`, `identity_epoch`, `identity_thumbprint_sha256`, `api_origin`,
`stream_origin`, `account_id`, `account_label`, `code`. Unknown/duplicate fields, versions,
credentials in origins and control characters are errors. A document is an untrusted locator:
import must not automatically contact or trust its origins. The client explicitly confirms
the selected server and verifies signed discovery against the pinned identity before sending
the code. Manual entry uses the same selected server/account context.

## Requests and confirmation

The OpenAPI companion defines exact bounded request and response schemas. Requests are JSON
objects no larger than 8192 bytes; timestamps are UTC and fresh requests must be within 120
seconds of database time. UUIDs and SHA-256 hexadecimal digests have canonical lowercase
spellings. `request_sha256` covers RFC8785 canonical JSON omitting itself and
`device_signature_b64url`. Preview/commit carry canonical P-256 SPKI and ES256 P1363 proof over
ASCII `autplay:account-recovery:<preview|recover>:v1\n` followed by the raw request digest.
Every request binds server UUID, epoch, identity thumbprint, both origins and account UUID.

| Path under /api/v1 | Authentication and effect |
| --- | --- |
| GET /account/recovery | Active V2 application session of any role; current configured state/generation |
| POST /account/recovery | Same authority; configure or rotate using expected generation (0 initially) and client-created next verifier |
| POST /recovery/preview | Exactly one X-AutPlay-Recovery-Code header and new-key proof; returns account label, role and code generation for explicit confirmation |
| POST /recovery/commit | Same proof; explicitly confirmed account, expected generation, next code verifier, client-created refresh hash and binding commit ID |
| POST /recovery/outcome | The exact signed commit body plus its client-created X-AutPlay-Recovery-Refresh secret; returns only an already committed matching result after an ambiguous/lost reply |

Before configure/commit, the client durably encrypts the exact pending request, key alias,
old/next code as applicable, new refresh secret and commit IDs. It must not send first and
persist afterwards. A successful response promotes the pending binding transactionally.
Existing accounts receive an explicit setup offer; no server-generated recovery secret or
silent backfill exists. New accounts must save a recovery document before setup is considered
complete. These client rules remain a feature-enablement prerequisite.

The PA2 binding write carries a nonsecret setup checkpoint keyed by server, account and
binding commit. Existing bindings are not backfilled. The client presents unfinished setup
on entry while preserving local music access. Saved evidence consists only of the exact
code generation and document SHA-256. A system-document-picker launch alone is not evidence:
the selected document must be written, closed, read back and matched, and the account,
binding, code generation and document must still match before acknowledgement. Cancellation,
process recreation or a stale picker result leaves setup incomplete. External document I/O
must not hold a binding or credential-journal lock.

## Atomicity, retries and revocation

Commit locks identity, global resource admission, account publication owner, account and child
authority in that order. Only ACTIVE, nondeleted accounts qualify. It atomically increments
account authority generation, consumes one recovery generation, revokes old application and
browser sessions, passkeys and trusted keys, cancels old invitations and unfinished approvals,
withdraws resource grants, creates one fresh device/session and rotates the recovery verifier.
Account/library/social data remain intact. Negative key blocks and terminal receipts remain.
Retained worker/process executions keep charged capacity until exact exit acknowledgement.

The new key cannot reuse a previous account device key. Recovery grants no physical-network
or trusted-device admission and no passkey. Disabled/finally deleted accounts cannot recover;
the separate 30-day deletion cancellation flow is not implemented by these endpoints.

Ordinary replay receipts last one day and bind the exact operation, account/server, old
verifier, new verifier/generation and result device/session. Exact ordinary retry proves the
old code and same key, checks current account authority, recovery generation and live result
binding, then issues a short access token for that binding. It never creates a second session
or returns a refresh secret. Ordinary replay after expiry remains denied.

The retained operation/result binding supports only the separate `/recovery/outcome` repair.
That endpoint proves the exact client-created refresh secret already committed by the signed
request, the same new device key, current rotated generation/authority and the still-live exact
result session. It cannot create or rotate a binding, accept a changed request, or recover a
superseded/revoked result. Receipt cleanup therefore cannot strand an already committed client
that lost its response, and cannot turn expired old-code authority into a replay capability.

All responses are no-store. Validation is 400, failed proof/unavailable authority 403,
conflicting generations/operations 409, throttling 429, disabled capability 503. Authentication
failures are 401. Source/global/account attempts use separate keyed digests and durable
15-minute windows (60/3000/30 respectively); proxy headers count only from the configured exact
trusted proxy. Enable `AUTPLAY_ACCOUNT_RECOVERY_ENABLED` only with persistent server identity
and after client and acceptance evidence is complete.
