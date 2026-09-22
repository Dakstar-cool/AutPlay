# AutPlay v0.4 security and privacy boundary

This document is an evidence index, not a claim that the development-signed `v0.4.0` bundle is a
production release. Historical RC1 and post-RC checks remain useful only for the exact source and
artifacts they name. Production promotion must generate fresh vulnerability, license, secret-scan
and provenance evidence for the exact immutable release described by
[`PRODUCTION_READINESS_PLAN.md`](../operations/PRODUCTION_READINESS_PLAN.md).

## Outcome

No critical/high object-authorization or data-loss defect was recorded in the qualified
CPU/local-first snapshots. Later implementation added WebAuthn Admin sessions, device admission,
account recovery/deletion, training-consent controls and a locally qualified PA3 edge candidate.
Those additions have dated, input-bound evidence; they do not replace exact production-target
acceptance. The current release remains development-signed and not production-deployed.

## Threat model

| Boundary | Principal threats | Controls and evidence |
| --- | --- | --- |
| Android ↔ API | bearer theft/replay, device revoke, BOLA | short access lifetime, rotating hashed refresh, current session/device reload, owner-scoped repositories and negative API tests |
| Admin Web | passkey replay, session theft, CSRF, origin confusion | canonical origins/RP IDs, WebAuthn verification, bounded HttpOnly sessions, CSRF/idempotency controls, revocation and exact static-asset integrity checks; target Gate A remains open |
| Upload/ingest | oversized/truncated media, path traversal, partial publication | bounded resumable staging, generated keys, decode/hash/fingerprint checks, crash windows, immutable CAS and quarantine |
| API/stream ↔ Vault | hash-as-authorization, Range leakage | owner authorization before object lookup, masked not-found, capability refresh, no raw path/URL response |
| Source/import adapters | SSRF, token leakage, hostile provider/media input | bounded parsers and subprocesses, owner-scoped provider authorization, redacted durable errors, isolated PO-token service and explicit provider policy |
| Shell media tools | command injection, hostile files, resource exhaustion | argument arrays without shell interpolation, pinned/checksummed FFmpeg/fpcalc, bounded timeout/output and hostile-media tests |
| GPU/model supply chain | unlicensed/malicious weights, CUDA outage/OOM | isolated optional lock/image/profile, hash/license registry, no arbitrary URL/path, no model installed or active; A-030 deferred visibly |
| Logs/exports | token, private URL, path or payload disclosure | typed secrets, recursive redaction, bounded stable errors, production-source scan and runtime/import negative tests |
| Account/privacy actions | lost replies, unauthorized deletion/training, restored stale authority | exact-key device/session binding, independent deletion and consent ledgers, grace/cancel flow, restore fences and target Gate D |
| Public edge | proxy spoofing, unintended listeners, TLS/renewal failure | exact trusted proxy, Caddy-only TCP 443 candidate, no mobile API/stream host ports, file-backed secrets; live PA3 evidence remains open |

## Dependency and secret evidence

The results below are historical and must not be relabelled as a current production audit.

- Committed CycloneDX 1.5 SBOMs cover the server and isolated GPU projects. The release-candidate
  workflow generates a fresh root contract-tool SBOM for each bundle.
- The RC1 license inventory resolved every recorded Python and Android release-runtime entry.
  LGPL/MPL notice/linking and
  NVIDIA proprietary redistribution obligations are explicit; publication still requires legal/
  notice review and is outside P14.
- `uv audit` queried OSV for 36 root, 46 server and 55 isolated-GPU packages: zero reported
  vulnerabilities and zero adverse statuses on 2026-08-17.
- Production-source scan covers server/GPU/Android main source, Compose/deployment and scripts. It
  found no private-key, AWS, GitHub, Slack or token-bearing private-URL pattern. The only allowlist
  is exact-path scoped to the documented disposable loopback database credential.
- Android backup is disabled (`allowBackup=false`, `fullBackupContent=false`); bearer material is
  Keystore-owned. Revoked URI/device credential behavior remains fail-closed and repairable.

### Post-RC dependency remediation (2026-08-24)

- The direct server pin and both server/GPU locks were upgraded from `cryptography==46.0.5` to
  `cryptography==50.0.0`, covering the six Dependabot alerts grouped against the inherited GPU
  lock, including GHSA-jwv3-5hgf-82ww / CVE-2026-69249.
- Fresh frozen OSV audits cover 51 server and 58 isolated-GPU packages with zero vulnerabilities
  and zero adverse statuses. Regenerated CycloneDX 1.5 SBOMs record the same component counts and
  the exact `cryptography` 50.0.0 package.
- The five-test profile-pairing unit set passes against the upgraded package, including the current
  P-256 key serialization, signing and verification boundary.

## Current residual security boundaries

- `v0.4.0` uses a development Android signer; the exact production artifact and install/update path
  are not qualified.
- Public Caddy/TLS is inactive. Certificate issuance, external scan, renewal, rollback and real
  mobile API/Range checks remain PA3 gates.
- Admin/account target Gates A-D and real CPU-worker resource budgets remain open.
- Face and Sona-Lite have no active production model. The deterministic CPU recommender and neutral
  PCM-reactive Face remain authoritative fallbacks.
- The local scanner complements, rather than replaces, fresh hosted/provider checks appropriate to
  the chosen production distribution path.
- Container bases are digest-pinned, but the development release manifest records no registry push
  or external image signing.
