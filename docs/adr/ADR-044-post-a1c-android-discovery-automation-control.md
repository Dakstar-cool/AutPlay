# ADR-044: Post-A1C Android discovery automation control

- Status: Accepted
- Date: 2026-08-30
- Scope: additive post-A1C mobile control surface; A1C remains closed

## Context

A1C delivered PostgreSQL-owned scheduled discovery and auto-import plus an Admin Web surface. Its
accepted scope deliberately did not define an Android API or Room projection. The user explicitly
requested the Android frontend adaptation after A1C PASS. Reusing the browser cookie/Origin/CSRF
router would conflate browser and device authorities, while reusing the older broad discovery view
schemas would claim a wire shape that the A1C runtime does not produce.

## Decision

1. Add a distinct bearer-authenticated JSON surface under `/api/v1/discovery/automation`. It is
   composed only while the existing default-off `DISCOVERY_AUTOMATION_ENABLED` operator gate is
   enabled. Owner identity is always derived from the authenticated `Principal`; the application
   service accepts only a narrow `user_id` actor protocol shared with the existing Web actor.
2. Reuse the frozen `automation-command.schema.json` vocabulary, RFC 8785 request hashes, owner-wide
   operation namespace, policy revision CAS and A1C repository. Define additive bounded mobile
   snapshot/candidate schemas for the actual implemented views instead of relabeling the older
   broader discovery schemas.
3. Keep Android local-first. The screen lives in Server Features, reads canonical artists from the
   existing profile-scoped Room artist projection, and keeps policy/run/candidate projections in
   memory. No Room schema or local playback dependency is added.
4. `AUTO_IMPORT` requires an explicit consequence dialog before Android sends the exact frozen
   confirmation code. Changing the selected canonical artist clears its provider mapping and
   returns to `MANUAL_ONLY + REVIEW_REQUIRED` unless that artist already has a policy.
5. One unresolved interactive command retains its random operation UUID in UI state. A retry of
   that exact command reuses the UUID; a different mutation is blocked until the first result is
   recovered or an authoritative refresh succeeds. Confirmed success clears the pending operation,
   so a later deliberate action receives a new UUID. After process recreation the screen is
   unloaded and requires an authoritative snapshot before mutation controls become actionable.
6. Unknown future mode values remain visible as unsupported server literals and cannot be edited or
   executed as if they were a known safe mode.

## Consequences

- Android and Admin Web remain separate authentication surfaces over one owner-scoped application
  service and PostgreSQL truth.
- Operator enablement, cadence, quotas, authorization rechecks and acquisition/Vault boundaries are
  unchanged.
- The change adds no dependency, broker, migration, Room schema, credential, deployment or public
  topology decision.
- Connected Compose behavior still requires an attached authorized Android device; host compilation
  proves the instrumentation sources but does not replace that evidence.
