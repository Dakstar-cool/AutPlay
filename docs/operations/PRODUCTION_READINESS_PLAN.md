# AutPlay production readiness plan

**Status:** IN_PROGRESS

**Baseline:** `v0.4.0`, source commit `627adf86a6ba67f08676ecec839bd043fc9e113b`

**Updated:** 2026-09-22

This is the single active plan for moving AutPlay from the published development release to a
production release. Historical P00-P14 prompts, milestone handoffs and dated evidence remain
reference material and must be opened only when a task needs their exact contract or proof.

## Current conclusion

The local-first Android and CPU server core are production-grade candidates. The published
`v0.4.0` distribution is not a production release: it uses the retained development signer, the
packaged server is a trusted-LAN development topology, the CPU worker requires reviewed target
budgets, and public-edge/target acceptance is incomplete.

Closed foundations do not need to be replayed: P00-P14, Frontend M1-M4, Product M5A/M5B, Server
M6, A1A-A1C, S1A-S1D, S2, L1, PA1/PA2 and personal-server backup/recovery have accepted evidence.
P12 A-030 remains an approved optional deferral; P13 is qualified only for its trusted-local,
single-API topology.

## Production definition of done

AutPlay may be called production only when one exact release satisfies all of the following:

- an immutable source commit, Android artifact, server image digest and Alembic head are bound in
  one manifest;
- the hardened `app.autplay` APK is signed with the retained production signer and its update or
  migration path from existing development-signed installs is explicit;
- the exact production APK passes final Samsung A55 qualification against the exact target server;
- Admin/account target Gates A-D pass against one recorded target identity and configuration;
- the CPU worker uses reviewed real resource-report v1 and internal-I/O report v3 budgets;
- the public edge passes certificate, external scan, renewal, rollback and real-mobile API/Range
  checks without exposing Admin Web;
- PostgreSQL, Vault and both independent ledgers have a verified off-host encrypted generation and
  an isolated restore drill meeting the accepted RPO/RTO;
- current dependency, vulnerability, license, secret-scan and artifact-provenance evidence is
  generated for the exact release inputs;
- release, deployment, rollback, observability and incident ownership are documented without
  conflicting current-state documents;
- final review has no unresolved Critical/Major finding and the operator explicitly approves live
  activation.

## Invariants for every phase

- Android remains local-first; ordinary local playback and mutations never require a synchronous
  server call.
- PostgreSQL/Vault migrations have no destructive fallback. Back up before persistent-target
  mutation and never roll schema backward during process/image rollback.
- CPU serving remains independent of GPU, Face and Sona-Lite. Missing optional ML input fails
  closed to the existing deterministic CPU behavior.
- Secrets, recovery material, private origins, raw device identifiers and personal paths never
  enter Git, routine logs or shared evidence.
- Synthetic, emulator, M52 or disposable-server results cannot be relabelled as A55/target-server
  production evidence.
- Each phase updates only evidence tied to the exact source/artifact/configuration it verified.

## Execution order

### Phase 0 - active documentation baseline

**State:** COMPLETE for navigation cleanup; fresh release evidence remains Phase 5 work.

- Keep this file as the only active cross-project implementation plan.
- Keep `START_HERE.md` and `AGENTS.md` short and route historical material on demand.
- Treat `docs/implementation/`, `docs/build-pack/` and dated release reports as historical evidence,
  not executable instructions.
- Remove retired local harness documents and stop-hook references.
- Correct current deployment/security summaries so they no longer present pre-`v0.4.0` blockers as
  current facts.

Exit: a new task can identify current work from this plan without loading the P00-P14 history.

### Phase 1 - exact Android release and final audit closure

**State:** IN_PROGRESS.

1. Use `v1.0.0`, `versionCode 13` and direct APK distribution for the first production release.
2. Use a controlled clean reinstall from development-signed `app.autplay` to the production
   signer. The existing A55 and M52 installations are disposable test installations with no
   retained user-data requirement. Signer lineage and full export/import are therefore outside the
   first-release critical path. This decision must not be generalized to a future installation
   with valuable local-only state: uninstall removes the application sandbox.
3. Build one minified hardened APK from an immutable commit with production signing inputs kept
   outside the repository.
4. Bind APK hash, certificate hash, package/version, source commit and Room schema to the release
   manifest.
5. Run the final complete Samsung A55 release qualification, including upgrade/migration,
   local-first playback, process death, sync, pairing/recovery and accessibility surfaces.
6. Reconcile Android audit Q3/Q4: record the already available hosted CI evidence and close Q4 only
   with the exact production artifact.

Tooling progress: the parameterized production signer and credential-free release finalizer are
implemented. The finalizer binds the signed APK and Room schema to the verified Docker archive
digest, single Alembic head and fresh release-audit package, while retaining
`production_deployed=false`. This is implementation readiness, not exact-artifact evidence; the
clean `v1.0.0` tag, interactive signing run and A55 qualification remain open.

Candidate-source validation on 2026-09-22: the complete Windows canonical gate passed after the
`v1.0.0` / `versionCode 13` identity and release-audit/finalizer changes. It covered 301
contract/release tests, 41 GPU tests (2 expected Windows symlink skips), 300 acquisition tests (8
expected platform/provider skips), 87 training tests, Android lint/unit/debug/trusted-LAN/minified
release assembly, and 2,238 server tests against disposable PostgreSQL 18.4/pgvector 0.8.6 (59
documented platform/image skips), followed by exact Compose cleanup. This result validates the
candidate source tree only; it is not production-signing, exact APK/A55 or deployment evidence.

Exit: one production-signed APK and its supported install/update path have exact A55 evidence.

### Phase 2 - complete AutPlay Face in the selected user sequence

**State:** IN_PROGRESS; the neutral PCM-reactive renderer remains the safe production fallback.

The previously selected sequence places full Face qualification before PA3. Moving this phase after
the core production launch requires an explicit user decision; it must not happen implicitly.

1. Supply and authorize a representative initial music set; 10-20 tracks are a pilot, not final
   quality evidence.
2. Resolve the exact model/license conflict before any distribution or commercial use.
3. Freeze interpretation/calibration criteria and held-out quality thresholds.
4. Implement the versioned semantic timeline, cache, lineage, persistence, API/job/lease,
   activation, retention/export/delete and garbage-collection lifecycle.
5. Add continuous semantic rendering and perceptual palette interpolation while preserving unknown
   values, abstention and neutral fallback.
6. Qualify CPU/GPU behavior as applicable, physical-device performance, battery/frame behavior,
   reduced motion and the full accessibility matrix.

Exit: a reviewed model/interpreter and timeline are production-qualified, or an explicit scope
decision defers semantic Face while retaining the neutral renderer.

### Phase 3 - Admin/account target acceptance

**State:** BLOCKED on target/operator evidence.

- **Gate A:** canonical private HTTPS Admin origin, real Windows Hello and A55 platform passkeys,
  EN/RU desktop/mobile views, keyboard navigation, revocation and third-device network denial.
- **Gate B:** repeat pairing/recovery/deletion/consent/quota ceremonies on the exact A55 and bind
  their receipts to the exact deployed server identity and current migration.
- **Gate C:** run the worst permitted combined workload on real server/storage/database/network,
  produce resource-report v1 and internal-I/O report v3, review them, then separately approve
  applying the budgets.
- **Gate D:** create the approved NAS/off-host backup, restore it in isolation, validate deletion and
  consent ledgers, offline execution drain, live-PID abort and current publication behavior.

Exit: all four gates have dated PASS evidence and reviewer sign-off bound to one target set.

### Phase 4 - PA3 live public edge

**State:** BLOCKED on explicit live activation and evidence.

1. Obtain explicit approval for the external mutation window.
2. Freeze source commit, server image digest, migration head, backup generation, domains, DNS,
   target identity and rollback image.
3. Perform pre-deployment backup and validate its hashes before migration or image switch.
4. Activate only Caddy TCP 443; keep Admin Web private and API/stream containers without host
   ports.
5. Verify certificate issuance and chain, external IPv4 exposure, source-spoof rejection and absence
   of unintended listeners.
6. From a real mobile network, verify registration/session, API authorization and authenticated
   `Range`/`If-Range`/resume behavior.
7. Exercise renewal and failed-config behavior, then prove process/image rollback without schema or
   user-data rollback.
8. Record redacted health, image, migration, backup and rollback receipts.

Exit: PA3 changes from `BLOCKED` to `PASS`; PA4 WAN Wave remains a separate decision.

### Phase 5 - production release and operational handoff

**State:** NOT_STARTED.

1. Run the complete applicable canonical gates on the immutable release commit.
2. Integrate `scripts/p14_release_audit.py` into the production packaging/workflow path and fail
   packaging when fresh SBOM, vulnerability, license or secret-scan evidence is missing or stale.
3. Build and verify the production APK and server archive/image; bind every artifact hash.
4. Publish through the chosen distribution path with rollback artifacts retained.
5. Enable reviewed monitoring, storage/certificate expiry checks and backup/restore schedules.
6. Replace development-only release wording with an exact production manifest and release notes.
7. Perform final independent scope/security review and operator sign-off.

Exit: the release manifest may state `PRODUCTION_RELEASE`, `production_signed=true`, and
`production_deployed=true` only for the exact deployed artifacts.

## Product backlog after or alongside production readiness

These items are real planned work but do not silently block the deterministic local-first core.

| Item | State | Next prerequisite |
| --- | --- | --- |
| Smooth track transitions and deferred Wave switch | NOT_STARTED | Select transition mechanism/duration; prove natural current-track completion and next-track selection from the new Wave |
| Admin download-script control | NOT_STARTED | Define controls, authorization, cancellation, audit and acceptance criteria with the user |
| R1B Sona-Lite shadow quality | BLOCKED | Authorized complete candidate sets, mature causal outcomes, replay histories, one approved embedding model and sufficient chronological span |
| R1C recommendation activation | NOT_STARTED | R1B PASS plus explicit activation, monitoring and rollback decision |
| P12 A-030 GPU evidence | DEFERRED | Approved model, real CUDA OOM/batch reduction and throughput/p95/VRAM/quality evidence |
| PA4 WAN Wave/cross-instance fanout | NOT_STARTED | PA3 PASS and a separate network/authority decision |
| Browser guest playback, Room/Vault grants, Party Mode | DEFERRED | Separate accepted capability and media-authority scope |

## Remaining decisions

1. Record the explicit scope decision to ship the production core with the neutral Face and
   continue semantic Face afterward, if the selected sequence is to change.
2. Define the forward-fix rollback support and artifact-retention window for `v1.0.0`.
3. Confirm verified offline image transport and its immutable retention policy.
4. Confirm that smooth transitions and Admin download controls belong to the first post-production
   feature release.

## Evidence and document policy

- Start future cross-project work here, then open only the affected contract, ADR, operation
  runbook and latest relevant handoff.
- Dated release reports are immutable snapshots. Never describe their branch, test count or target
  as current unless the exact inputs still match.
- Do not copy historical status paragraphs into new plans. Link them as evidence.
- Update this plan when a state changes; release notes describe shipped artifacts, not future work.
- Preserve historical evidence, but keep it outside default agent/navigation context.
