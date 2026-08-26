# ADR-036: S1A friends, presence and Wave invitation boundary

- Status: Accepted on 2026-08-25; implementation deferred to S1C
- Date: 2026-08-25
- Scope: Post-MVP S1A contract only; implementation deferred to S1C

## Context

AutPlay needs same-server friends and fast Wave invitations without treating friendship or presence
as account, device, session, media or Room authority. P13 has host-created device membership and no
remote kick command.

## Decision

1. Friendship is an explicitly accepted unordered pair of two active accounts on one personal
   server. Signed contact cards are explicitly shared locators, not public discovery or authority.
2. Directed user block is separate from exact device-key block, wins pair races and cancels pending
   social/Room invitations. Friend removal and user block do not silently rewrite Room history.
3. Presence and invitation availability are independent settings, default false. Friend-visible
   presence is a coarse aggregate of fresh authenticated heartbeats with 90-second freshness and no
   durable activity history.
4. Only the current P13 host device may create a ten-minute friend invitation for one exact room and
   target friend account. Acceptance by one active target device rechecks all mutable social, Room,
   device/session and capacity authority before ordinary P13 membership materialization.
5. No kick is inferred. While blocker and target share an active Room, block returns
   active_room_exit_required with no mutation. The blocker first uses ordinary ordered P13 leave,
   transfer or close and then retries. Friend removal leaves existing membership visible.
6. PostgreSQL remains authority. REST recovery may use disposable hints; no broker, Redis, graph
   database or social microservice is added.

## Consequences

The design favors visible, fail-closed Room behavior over a misleading one-step block. Presence
cannot reveal tracks, device counts, Room IDs or exact activity. S1C must prove concurrency,
cross-owner denial, expiry and P13 recovery/source authorization end to end.

## Rejected alternatives

- Auto-friend on crossed requests: explicit acceptance would be ambiguous.
- Session existence as online: creates false presence and surveillance history.
- Friendship as room allowlist or Vault grant: crosses both P13 and owner-object authorization.
- Block as hidden remote kick: no accepted ordered P13 command exists.
