# ML operation-bound step-up contract

Revision 15 section 7.5 defines three distinct proofs: Android credential
registration, Android per-use consent grant, and Web Admin mutation. Migration
`0061` reserves credential, challenge and receipt relations; no endpoint or
verifier is enabled by this contract.

## Shared invariants

Each challenge has 32 random nonce bytes, a UUID, purpose, action, actor,
operation ID, canonical command hash, issue time and expiry no more than five
minutes later. Store only the nonce hash. An operation consumes one challenge
at most once in the same transaction as its mutation and audit receipt. A
retry of the same operation and command hash returns only the committed
receipt. Reusing an operation ID with changed content conflicts. Expired or
consumed challenges are purged after 24 hours; the audit keeps hashes and
result metadata, never raw signatures. Caller JSON cannot supply actor role,
current session/device authority, generation or mutation timestamp.

Every verifier rechecks its live actor, session, device and relevant generation
while holding the authority rows until commit. A change to command bytes,
action, purpose, actor, session, device, parent key, generation or server
identity fails before consumption. JWT issue time, token refresh, recent login,
cookie possession and CSRF alone are not step-up evidence. A missing or failed
proof never creates an enabled credential, grant or license decision.

## Android registration

`POST /api/v1/ml/step-up/credentials/challenges` requires the active paired
device and current access token. The registration challenge binds actor,
session, exact device ID, M5 public-key thumbprint, dedicated
`device_key_generation`, server instance/identity and purpose
`ML_CONSENT_STEP_UP_REGISTER_V1`. Registration is a separate ceremony; a
consent grant cannot enroll a credential as a side effect.

The Android client creates a new P-256 `ML_CONSENT_STEP_UP_V1` key with the
server nonce as its attestation challenge. Its bounded response carries the
new SPKI, full attestation chain, authenticator/API declaration, and two
ES256-P1363 signatures from the existing M5 key and new key over one canonical
registration hash. The bound document includes nonce, challenge/operation,
actor/session/device and parent generation, server identity, new SPKI and
thumbprint, chain hash, API branch, issue/expiry and domain
`autplay.ml.step-up-register.v1\0`. The server validates exact nonce and
leaf-key equality, both signatures, certificate signatures up to a trusted
Android hardware root, root revocation freshness, TrustedEnvironment or
StrongBox, verified boot and device lock, app package/signing identity, EC
signing/SHA-256 purpose and per-use authorization. Off-device validation must
finish before the challenge and credential commit. The stored credential
contains the scoped public key, attestation summary/hash, parent key
generation and its own monotonic generation. Bearer possession or a software
key never completes registration.

On API 30+, the key uses zero-second authorization with
`AUTH_BIOMETRIC_STRONG | AUTH_DEVICE_CREDENTIAL` and signs via
`BiometricPrompt`/`CryptoObject`. API 26-29 uses the per-use validity value
`-1` and strong-biometric `CryptoObject`; combined device credential is not
accepted on that branch. Unsupported lock, biometric, attestation or
authenticator properties fail closed.

## Android grant and revocation

`POST /api/v1/ml/step-up/challenges` binds the self-only R1C grant command to
its own purpose and current registered step-up credential. Per-use user
verification authorizes its signature. The server consumes it atomically with
the independent serving-consent ledger append and current projection. Deny
and withdraw need only a valid current session and must not wait for step-up.

Paired M5 key rotation/revocation advances the dedicated device-key generation
in the same device-row transaction; the general row/session generation is no
substitute. Device or account revocation/deletion, screen-lock removal,
OS-reported key invalidation, step-up rotation or stale parent generation
invalidates descendant challenges and credentials before any later grant.
Recovery repeats the full registration ceremony.

An affected participant without usable Android hardware may use a separate
self-service WebAuthn user-verification assertion for the same operation only
if that credential was independently enrolled under a high-assurance process.
This fallback cannot enroll the participant's first credential from a bearer
token. Otherwise the grant is unavailable.

## Web Admin mutation

An `OWNER` or `ADMIN` starts a fresh assertion in the current Web session with
CSRF protection. The challenge binds that session, exact admin action,
canonical request hash, operation ID, expected generation, pinned relying
party/origin and at most five-minute expiry. An existing non-revoked WebAuthn
credential must return user verification. Current role, session, credential,
command and target generation are locked and rechecked with the mutation and
one-time receipt. A passkey login session or an assertion for another action
does not satisfy this proof. The assertion does not create or refresh a general
login session.

## Required failure cases

Contract tests must cover replay, changed body/operation, cross-actor/session/
device substitution, expiry, stolen bearer, old M5 plus new key, attestation
nonce/chain/root/property failures, both Android API branches, absent user
verification, parent/credential rotation and revocation, concurrent challenge
consumption, and exact idempotent receipt retry. Until those tests and the
transactional endpoints pass, all three operations remain disabled.
