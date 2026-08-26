# AutPlay Identity, Trust and Social Threat Model v1

Status: ACCEPTED / CONTRACT_ONLY / NOT_IMPLEMENTED

## Assets and trust boundaries

Protected assets are M5/M6 session authority, exact device private keys, admission polling and guest
bearers, the social graph, user privacy settings, restricted presence, Room membership and each
participant's independent media authorization. Trust boundaries are Android Keystore/process state,
the optional CPU API, M6 browser session, PostgreSQL authority and P13 REST/WebSocket execution.

## Threat matrix

| Threat | Required mitigation | Residual boundary |
| --- | --- | --- |
| Admission request substitution | M5 server trust, canonical request hash, exact-key PoP, 12-decimal comparison | User may compare the wrong request; Web shows full consequence before decision |
| Request/poll brute force | 128-bit poll bearer, hash-only storage, key/account/source limits, non-disclosing state | Availability can still be degraded within bounded limits |
| Nickname/model/IP impersonation | Trust and block key only exact SPKI/thumbprint; metadata is display-only | A user can choose a misleading nickname |
| Approval replay/race | M6 CSRF/Origin/operation binding, row locks, exact request/key, M5 receipt semantics | Lost non-exact responses fail closed |
| Trust survives revoke unexpectedly | Separate controls and explicit combined action; mutable authority reload | User must understand remove-trust versus revoke-access wording |
| Cross-account administration | Request target must equal current Web actor user; non-disclosing foreign rows | No delegated administrator in v1 |
| Friend graph enumeration | Signed explicitly shared contact cards, no directory/search, pair-scoped reads | A recipient can retain a deliberately shared contact card until expiry |
| Friend/block race | Directed block wins, pair lock, pending request/invite cancellation | Existing Room membership requires explicit exit workflow |
| Presence surveillance | Default off, friends-only, coarse aggregate, 90-second freshness, no history | Friends can observe coarse availability while enabled |
| False online from sessions | Only fresh authenticated heartbeat counts; session existence is ignored | Network loss remains visible for at most the freshness window |
| Room invitation escalation | Host-only create, target friend/account, accept by one exact device, full P13 recheck | An invited friend still needs an independently playable source |
| Block used as remote kick | No kick; active_room_exit_required and ordinary ordered P13 leave/transfer/close | The target may remain in a Room the blocker chose not to leave |
| Guest bearer leakage | Non-HTTP app document, no-store POST, ephemeral state, no clipboard/log/screenshot | Physical shoulder surfing before redemption remains possible inside TTL |
| Guest scope escalation | Capability allowlist and per-request Room/status/expiry recheck; deny all other API families | Guest display name remains untrusted bounded text |
| Media authority confusion | Ordinary P08/P13 source resolution on each device; no Room/Vault grant | Guest may join but report unavailable |
| Stale cache or disabled account | PostgreSQL reload inside mutations; cache is display-only; immediate presence/invite expiry | Offline UI can only show explicitly stale/unknown state |
| Telemetry/export leakage | Structured allowlists; exclude identifiers, graph, names, origins, bearers and media payload | Restricted account export may contain opaque counterpart IDs |

## Mandatory negative evidence

Executable vectors must cover concurrent duplicate admission, changed-key replay, expiry/reject/
block/revoke races, foreign Web actor access, exact-key trust inheritance denial, simultaneous friend
requests, block winning accept, private/stale/multi-device presence, Room invite accept races and
capacity/epoch/source failures, guest consume races and denial against every non-Room API family,
plus URL/history/referrer/log/screenshot/export redaction failures.

No implementation milestone may treat contract validation as runtime evidence. S1B/S1C/S1D each
must add real PostgreSQL concurrency, Android, WebUI and end-to-end negative authorization tests.
