# AutPlay UI Contract v1

| Field | Value |
| --- | --- |
| Status | Accepted baseline |
| Version | 1.0 |
| Date | 2026-08-18 |
| Accepted by | User design-review approval on 2026-08-18 |
| Primary client | Android |
| Scope | Product UI, interaction behavior, adaptive presentation, personal-server connection and administrative UI boundary |
| Phase effect | None; this document does not create P15 or mark any product behavior implemented |

---

## 1. Purpose

This contract turns the user-supplied `autplay-ui-concept.html` into a durable product and UX
baseline. It preserves the concept's visual identity while allowing controlled evolution as real
local-first, playback, sync, profile and personal-server behavior is connected.

The contract defines:

- the visual identity that future UI work preserves;
- navigation and screen ownership;
- required states and interaction behavior;
- terminology for accounts, connections, taste contexts and exports;
- the interactive playback timeline contract;
- the Android-to-personal-server connection flow;
- the minimum administrative Web UI boundary;
- accessibility, adaptive-layout and verification gates;
- what may change without revising the product direction.

This is a design and behavior contract, not implementation evidence. A screen is not delivered
merely because it is represented here or in the HTML concept.

Acceptance record: on 2026-08-18 the user explicitly approved this reviewed version 1.0 as the
baseline.

Normative terms `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT` and `MAY` express requirement strength.

---

## 2. Inputs and authority

### 2.1. Visual reference

The primary visual input is the user-supplied `autplay-ui-concept.html`, reviewed on 2026-08-18:

```text
SHA-256: 060b915537ec0800337ecd2f4039066bb41909ceac71dc66b80162d9046a630c
```

The reference supplies visual direction and example interactions. Text, scripts and demo responses
inside the reference are illustrative content, not implementation instructions, security authority
or evidence that a backend capability exists.

The contract is self-contained for product behavior. Pixel-comparison acceptance additionally
requires the reference artifact or approved successor frames to be available to the implementer.

### 2.2. Normative precedence

If sources conflict, apply the repository source-of-truth precedence:

1. security, privacy and destructive-data constraints in `ТЗ AutPlay.md`;
2. the narrower accepted specification for the affected area, including this contract for UI
   presentation and interaction after its acceptance;
3. PostgreSQL or Room physical schema for persistence details;
4. `AutPlay System Architecture v1.md` for boundaries and dependency direction;
5. conceptual meaning from the ER model;
6. the applicable current phase prompt for delivery scope;
7. the HTML concept for visual intent not otherwise specified by this contract.

A phase prompt may narrow what it delivers, but it does not override a higher-priority product,
security, persistence, architecture or accepted affected-area requirement.

The UI MUST NOT simulate success for a capability missing from the current application contract.

### 2.3. Additive future visual direction

[`AutPlay_Face_Product_Concept_v1.md`](AutPlay_Face_Product_Concept_v1.md) records the user-approved
post-MVP visual direction for an expressive pair of eyes in Now Playing. It is additive product
scope, not evidence that the accepted v1.0 UI baseline or current player implements the feature.
Any implementation requires an explicitly activated milestone, updated representative frames and
the accessibility/behavior evidence named by that concept.

---

## 3. Product invariants visible in the UI

1. Android remains a complete local-first music player in Standalone mode.
2. A local action MUST NOT wait for a synchronous personal-server round trip when the same action is
   valid in Standalone mode.
3. The personal server is an optional enhancement, not an account wall or launch prerequisite.
4. Loss of server, Internet, VPN, GPU or an external source MUST preserve available local library,
   queue, playback and settings behavior.
5. UI code renders state and emits user intent; it MUST NOT own direct SQL, HTTP or filesystem work.
6. Media3 owns playback and durable download execution/progress.
7. WorkManager owns durable deferred sync and metadata work.
8. A known SHA-256 is never presented as authorization to access media.
9. Uncertain Recording identity remains unresolved until allowed evidence or explicit review exists.
10. Tokens, credentials, private service origins, raw user paths and personal payloads MUST NOT
    appear in normal logs, exports, screenshots intended for diagnostics or generic error text.
11. Unsupported operations MUST be absent, disabled with an honest explanation, or represented as a
    future capability. They MUST NOT return placeholder success.

---

## 4. Product terminology

The UI MUST use distinct names for distinct concepts.

| UI term | Meaning | Must not be confused with |
| --- | --- | --- |
| `Local mode` / `Локальный режим` | Standalone application state without a server account | A server user or a disposable guest account |
| `Account` / `Аккаунт` | One human identity on a personal server, backed by `user_account` | Android `server_profile_id` |
| `Server connection` / `Подключение к серверу` | Device-local association between one app installation and one server account/device session | A person's library or taste profile |
| `Taste context` / `Контекст вкуса` | General, Workout, Cycling, Work, Sleep, Party or a future recommendation context | Authentication profile or playback quality |
| `My Wave` / `Моя волна` | A personalized recommendation queue or feed | A synchronized multi-user Wave room |
| `Listen together` / `Совместное прослушивание` | A host-controlled synchronized Wave room | My Wave recommendations |
| `Quality preset` / `Профиль качества` | Storage/stream/download quality policy | User identity |
| `Profile export` / `Экспорт профиля` | Portable library references, metadata, playlists, preferences and related user state | Mandatory export of all audio bytes |
| `Server role` / `Роль` | Owner, Admin or User authorization | Taste context or subscription tier |

`server_profile_id` is a local wrong-profile guard and immutable event member. It MUST NOT be shown
to users as the identity of a human profile or used as a server-lineage label.

For multi-user servers, separate people SHOULD use separate accounts with separate library,
history, preferences and recommendation state. Multiple pseudoprofiles inside one account require a
future explicit product and persistence decision.

---

## 5. Visual identity

### 5.1. Design character

AutPlay SHOULD feel calm, personal, warm and focused on owned music rather than infrastructure.
The interface preserves the concept's following visual anchors:

- warm neutral background and elevated light surfaces;
- dark mode with near-black background and restrained elevation;
- coral as the default accent;
- rounded cards, panels, artwork and controls;
- a prominent personalized Home hero;
- artwork-led content hierarchy;
- compact persistent mini-player above primary navigation;
- an immersive expanded player with large artwork;
- sparse iconography and short, human-readable status text.

Infrastructure vocabulary such as cursor, Journal epoch, job lease, SHA-256 or HTTP status MUST NOT
appear in the normal music experience. It MAY appear in an explicitly opened diagnostic view.

### 5.2. Reference color tokens

The following tokens preserve the concept direction. Implementations MAY adjust exact values to
meet contrast, dynamic-color or display requirements while preserving the relationships.

| Token | Light | Dark |
| --- | --- | --- |
| Background | `#f6f3ed` | `#121212` |
| Surface | `#ffffff` | `#1c1c1e` |
| Raised surface | `#faf8f4` | `#262629` |
| Primary text | `#181818` | `#f5f5f5` |
| Muted text | `#706d68` | `#aaa7a3` |
| Border | `#e5e0d8` | `#37373a` |
| Default accent | `#ff5b35` | `#ff6b45` |
| Soft accent | `#ffe2d7` | `#3c211b` |
| Mini-player surface | `#24211f` | `#f1eee8` |

Supported user accent families are Coral, Violet, Green and Blue. Accent color MUST NOT be the
only indicator of selection, failure, playback state or destructive risk.

### 5.3. Shape, density and hierarchy

- Primary cards SHOULD use approximately 18-26 dp corner radii.
- Compact rows and controls SHOULD use approximately 10-16 dp corner radii.
- Pills and selected contexts MAY use fully rounded shapes.
- Interactive touch targets MUST be at least 48 x 48 dp even when the visible icon is smaller.
- Content density MAY increase on tablets, but primary music actions MUST remain visually dominant.
- Real artwork replaces gradient placeholders when available; deterministic neutral placeholders
  MUST preserve layout when artwork is missing.

### 5.4. Typography and icons

- Typography SHOULD use the platform-compatible sans-serif stack unless an approved bundled font is
  introduced with license, size and rendering evidence.
- Screen and hero titles SHOULD use restrained weight rather than decorative heavy bold.
- Track title, artist, release and state MUST have an unambiguous hierarchy.
- Icons SHOULD follow the simple rounded line style shown by the concept. Android MUST use bundled
  vector assets or platform icons; runtime availability MUST NOT depend on an icon CDN.
- Every icon-only action MUST have an accessible label.

### 5.5. Motion

- Navigation, selection and mini-player expansion SHOULD use short 150-300 ms transitions.
- Motion MUST communicate continuity rather than delay input.
- Playback state MUST never be represented by animation alone.
- Reduced-motion system preference MUST suppress non-essential motion.

---

## 6. Information architecture and navigation

### 6.1. Compact primary navigation

The default compact-phone model has three persistent primary destinations:

1. Home / `Главная`;
2. Search / `Поиск`;
3. Library / `Моя музыка`.

The mini-player sits directly above this navigation while a queue item exists. The expanded player
hides the bottom navigation and owns the full content area.

Profile and Settings are reachable from the top application chrome. My Wave recommendations are
discoverable through the Home hero. Synchronized Listen together rooms use a separate, clearly
labeled entry point and active-room status; they MUST NOT reuse My Wave wording as if the two
features were one mode. A future fourth persistent Listen together tab MAY be added after usability
evidence; it is not required by the visual baseline.

The current five-item technical shell is not the target visual navigation merely because its routes
exist. Every delivered route remains reachable through primary, secondary or contextual navigation.

### 6.2. Secondary destinations

Secondary destinations include:

- playlists;
- downloads and offline content;
- listening history;
- import and identity review;
- track, artist, album and playlist detail;
- Now Playing and queue;
- Wave room/lobby;
- sync status and conflicts;
- personal-server connection and capabilities;
- profile, privacy/data and settings;
- diagnostics.

Secondary destinations MUST use predictable back behavior and MUST NOT unexpectedly reset the
active queue, selected account or scroll state.

### 6.3. Adaptive layout

| Width class | Navigation | Content policy |
| --- | --- | --- |
| Compact, `<600dp` | Bottom navigation, mini-player above it | One primary pane; detail pushes or overlays |
| Medium, `600-839dp` | Navigation rail or compact side navigation | List/detail MAY coexist when useful |
| Expanded, `>=840dp` | Persistent rail with optional secondary pane | Multi-pane library, queue or detail; no stretched phone column |

Fold posture, landscape and window resizing MUST preserve active destination, playback state and
user input. Width-class changes MUST NOT create a new player session or trigger network work.

---

## 7. Screen contract

### 7.1. Home

Home prioritizes useful local content and then enriches it when the server is available.

Recommended order:

1. Continue Listening, when a resumable queue exists;
2. My Wave recommendation hero and explicit taste-context chips;
3. Recently Played or Recently Added;
4. Recommendations / Collected for You;
5. New Releases and Forgotten Favorites when evidence exists;
6. Playlists and Offline Ready;
7. Problems Requiring Attention, only when actionable.

Home MUST render from available local projections without blocking on server refresh. Cached offline
recommendations remain usable while online refresh is pending or unavailable.

Greeting text MAY use an account display name but MUST have a neutral Local mode form and MUST NOT
require email, cloud identity or network availability.

### 7.2. Search

Search provides one coherent entry point with visible scope controls for:

- Local;
- Vault, when a server account is connected;
- External, only for explicitly enabled and authorized adapters.

Local results MUST appear without waiting for remote sources. Remote scopes show their own loading,
offline, empty and error states without replacing valid local results. Search results that start
playback MUST create an attributable durable queue.

Mood/category tiles are discovery shortcuts, not a substitute for text search or scope selection.
Voice search is optional and MUST be hidden unless a real permission-aware capability exists.

### 7.3. Library

Library includes Tracks, Artists, Albums, Playlists, Downloads/Offline, Unavailable and Review.

The default screen emphasizes Loved, Downloaded and recently added content in the concept's card and
row language. Sorting/filtering MUST be explicit and retained when the user returns.

Unavailable audio MUST preserve the library reference and expose recovery choices. Missing or
revoked SAF access MUST NOT be presented as deletion of the user's track.

### 7.4. Track and collection detail

Track detail MAY progressively disclose:

- title, artist, release and artwork;
- playable source and quality;
- local/offline/Vault availability;
- download, restore and add-to-playlist actions;
- metadata provenance and confidence;
- Recording/release version information;
- merge/split correction only through evidence-safe review.

Playlist-level Taste Profile exclusion is a required future capability. Its owning
interaction/persistence contract MUST be delivered before a milestone claims complete
recommendation-profile compliance. Playlist detail then MUST expose a durable control for whether
listens started from that playlist contribute to the Taste Profile. Until that contract exists, the
UI may honestly report the capability as unavailable but MUST NOT claim that playlist exclusion is
implemented. The setting is separate from Like/Dislike and from the active taste context.

The normal view SHOULD use human terminology. Technical identity evidence belongs in an expanded
details or review surface.

### 7.5. Profile

Profile presents the human and data ownership view, not raw connection identifiers.

When connected, it SHOULD show:

- display name and optional avatar/initial;
- server role;
- personal-server display name and connection health summary;
- sync summary;
- connected devices/sessions only when a real enumeration contract exists;
- profile and settings import/export;
- privacy/data controls;
- logout current session, logout all sessions and device revocation where supported.

`Log out all sessions` and device revocation require a confirmation surface that names the affected
account/device and distinguishes the current device from all sessions. It MUST explain that local
library data remains but server synchronization may require enrollment again. Commands MUST be
idempotent, disable duplicate submission while pending and report offline/remote failure without
claiming revocation. Cancellation, duplicate tap and retry behavior require tests.

`Disconnect locally` remains a separately named action. It MUST NOT claim that a remote session or
device was revoked.

In Local mode it MUST clearly state that no account is required and keep local profile export
available.

Email, password change, recovery and public registration from the HTML concept are future-capability
examples. They MUST be absent or explicitly unavailable until approved credential persistence,
recovery policy, API contract and migrations exist.

### 7.6. Settings

Settings groups SHOULD include:

- appearance and accent;
- library access and rescan;
- storage, offline quota and audio quality;
- personal-server connection;
- sync/network policy;
- recommendations and taste contexts;
- Wave policy;
- privacy, export and diagnostics.

Android folder selection MUST use SAF/MediaStore. The UI MAY display a friendly folder label but
MUST NOT treat `/storage/...` or another raw path as the persisted identity of user media.

Credentials, service origins, device binding and device-specific URI permissions MUST be excluded
from ordinary settings export.

---

## 8. Persistent mini-player

The mini-player is present whenever a current or resumable queue item exists.

It MUST show:

- artwork or deterministic placeholder;
- track title;
- primary artist;
- play/pause;
- an affordance to open Now Playing;
- progress indication when duration is known.

The compact progress line MAY be non-draggable if the expanded player offers immediate interactive
seek. If mini-player seek is supported, its interactive target MUST meet the 48 dp rule even when the
visible line is thin.

The mini-player MUST remain reachable above bottom navigation, avoid covering list content and
preserve playback during destination changes.

---

## 9. Expanded player and interactive timeline

### 9.1. Required content

Now Playing includes:

- large artwork;
- queue or context label;
- track title and artist link;
- interactive timeline;
- previous, play/pause and next;
- shuffle and repeat state;
- Like and Dislike;
- an explicit `Exclude from Taste Profile` action for the current listen/session;
- queue preview and reorder affordance where supported;
- local/Vault/offline or Wave state only when useful to the current decision.

### 9.2. Timeline state

The playback presentation model MUST expose at least:

```text
position_ms
duration_ms | unknown
buffered_position_ms | unknown
is_seekable
is_playing
playback_state
```

`buffered_position_ms` is an absolute media position, not remaining buffered duration.

When duration is known and seekable, the timeline MUST:

- expose a range from zero to duration;
- display elapsed and total or remaining time;
- update approximately every 250-500 ms while visible and playing;
- stop unnecessary high-frequency updates while paused or not visible;
- clamp all requested positions to valid media bounds;
- represent buffer progress separately from played progress;
- support tap and drag with accessible adjustment actions.

During drag, the thumb and time label follow a local preview value and MUST NOT snap back to the
player position. The default policy sends one Media3 seek command when drag finishes. An explicitly
tested throttled-seek policy MAY be used, but UI gesture events MUST NOT become unbounded network or
persistence writes.

If `queue_entry_id` changes, playback becomes non-seekable or the active item disappears while a
gesture is in progress, the UI MUST cancel the preview, discard the stale request and reconcile to
the new authoritative player state.

After the command, authoritative player callbacks reconcile the displayed position. Seek is not
counted as listened time; listening history uses observed playback time rather than position delta.

When duration is unknown or media is not seekable, the UI MUST show an honest non-interactive state
instead of a fabricated percentage.

### 9.3. Source and failure behavior

- Local seek MUST not require the server.
- Vault seek uses Media3 and authorized HTTP Range behavior.
- Authentication refresh, buffering or server loss MAY temporarily show a recoverable state but MUST
  not erase queue or current position.
- A source fallback MUST preserve the logical queue item and attribution.
- Player errors use stable human messages with Retry, Choose another source or Keep in library when
  those actions are valid.

### 9.4. Wave behavior

In a Wave room:

- the host may issue seek only when a versioned Wave contract carries the target position and
  authorizes it;
- a host gesture submits one durable Wave `SEEK` command and reconciles from the authoritative room
  timeline; it MUST NOT first perform an independent local Media3 seek;
- a member sees the shared timeline but MUST NOT issue an unauthorized local seek that silently
  diverges from the room;
- a locked timeline MUST explain that the host controls position;
- future request-to-host behavior requires its own real contract;
- drift correction remains Media3/Wave coordination behavior and is not presented as user scrubbing.

The current Wave v1 surface admits the `SEEK` command kind but does not carry a target
`position_ms`, persist it in the command document or update the authoritative room timeline.
Interactive Wave scrubbing is therefore unavailable until the public contract, server runtime and
Android transport are extended and tested together. This limitation does not affect ordinary
non-Wave Media3 seek.

### 9.5. Taste exclusion

The player MUST distinguish explicit preference from recommendation-training scope:

- Like and Dislike are explicit signals about the track;
- `Exclude this listen` prevents the current logical listen from contributing to the Taste Profile;
- `Exclude this session` applies to the active queue/session, including guest, sleep, test or
  background listening;
- a playlist-level default MUST exclude sessions started from that playlist when selected while
  allowing an explicit per-session override.

The selected exclusion state MUST be visible, durable through process restart, usable offline and
captured with the owning listening attribution when synchronization becomes available. Changing it
before listening finalization updates the captured session intent without counting the toggle as a
Like, Dislike or Skip. A post-finalization reversal, if offered, requires an explicit correction
contract and MUST NOT silently rewrite an immutable synchronized event.

Playlist-level exclusion state MUST also be visible in playlist detail, survive process restart,
work offline and apply predictably to each newly started playlist session until changed.

Acceptance evidence MUST cover offline selection, process restart, later sync, excluded versus
ordinary sessions, playlist default plus per-session override and absence of accidental preference
signals.

---

## 10. Personal-server connection UX

### 10.1. Entry point

Profile and Settings expose one `Personal server` surface with these top-level states:

```text
Not connected
Checking
Pairing
Connected
Connected with sync pending
Unavailable, local mode continues
Authentication requires attention
Incompatible API or capability version
Server identity or certificate changed, when identity verification is configured
```

The server is never required to enter the app or play readable local media.

### 10.2. Target pairing flow

The preferred future flow separates server discovery from device enrollment:

```text
Open Personal server
  -> Discover a server by QR metadata or an entered private origin
  -> Perform the future server-identity and capability compatibility checks
  -> Present an owner/admin-issued short-lived enrollment invitation
  -> Confirm server label, account and new device
  -> Exchange the enrollment credential for a device-bound session
  -> Choose what to do with eligible local-only changes
  -> Start background sync
```

An origin or discovery QR locates a server; it is not by itself authentication. The enrollment
credential SHOULD be short-lived and single-use. It MUST NOT require the user to copy raw access or
refresh bearer values. API/stream origins are sensitive non-secret device-local configuration and
are excluded from ordinary export and diagnostics; enrollment credentials and sessions are secrets.

The repository does not currently define pairing, capability-discovery or cryptographic
server-instance-identity endpoints. Those require a future versioned security contract. A friendly
server label may be stored device-locally, but a raw API/stream origin is not server identity, and
certificate/identity-change handling belongs to the future pairing/TLS design.

The existing local administrative CLI remains the mandatory first-owner bootstrap and recovery
fallback until Web setup and credential contracts are approved. Owner/admin-issued device
invitation is the target enrollment direction; this contract does not decide public registration.

Every asynchronous discovery/enrollment attempt MUST be bound to an immutable flow generation and
the normalized origin selected for that generation. When a future identity contract exists, the
verified server-instance identity is bound as well. Changing the QR/origin, navigating back,
cancelling or replacing the flow invalidates prior work; late responses are cancelled or ignored.

Immediately before persisting a session or materializing standalone intent, the application MUST
verify that the current generation, normalized origin, verified instance identity when applicable,
account and device equal the values explicitly confirmed by the user. Rotation/recreation,
back/cancel, QR/origin replacement and delayed out-of-order responses require behavior tests.

Production TLS/domain topology and public registration policy remain separate decisions. A trusted
LAN development flow MUST NOT be described as public-Internet-safe.

### 10.3. Existing local data decision

On first binding, the UI MUST explicitly offer applicable choices in plain language:

- `Keep only on this phone`;
- `Review and connect this library to <account>`;
- `Cancel` and leave the current binding unchanged.

The application MUST NOT silently attach standalone intent to the first configured account.

After explicit consent and binding revalidation, eligible standalone intent may produce newly
materialized immutable Journal events and projections through the accepted ADR-018 transaction. It
MUST NOT rewrite an existing event's `user_id`, `device_id`, `server_profile_id`, sequence, hash or
payload, and it MUST NOT silently re-scope existing rows.

Account switching is deferred unless an owning future contract explicitly supports it. When
supported, switching an already configured binding selects isolated views and pending state; it does
not automatically rematerialize prior local or account-owned intent.

### 10.4. Capability-driven presentation

The connection surface obtains a bounded capability view. UI for password change, device listing,
profile export jobs, remote import evidence or other optional operations MUST be shown as actionable
only when a real authenticated contract supports them.

Stable internal errors may be available in diagnostics, but normal UI uses concise recovery-oriented
messages.

---

## 11. Administrative Web UI boundary

The first Web UI is an administrative companion, not a required music client.

It SHOULD be an optional CPU-only entrypoint or adapter inside the existing modular monolith. It
uses published application query/command interfaces rather than direct cross-module table writes,
shares versioned authentication, RBAC, object authorization and audit behavior, and adds no broker,
microservice or GPU dependency. It never becomes Android's source of truth. The administrative CLI
remains the mandatory bootstrap/recovery path until real Web setup and credential contracts exist.

Initial scope SHOULD include:

- service and dependency health;
- owner/admin account management supported by real contracts;
- devices and sessions;
- Vault capacity, replicas and integrity state;
- import and job status;
- `AMBIGUOUS` / `REVIEW_REQUIRED` queues with sufficient evidence;
- backup and restore status;
- redacted diagnostics and audit views.

It MUST NOT:

- become necessary for Android local playback or library mutation;
- expose bearer tokens, private raw paths or unrestricted payloads;
- offer blind candidate acceptance;
- bypass RBAC or object authorization;
- imply public exposure, domain or TLS provider selection;
- use a destructive operation without explicit confirmation, audit and a recoverable workflow where
  the product contract requires one.

Cookie-based Web UI authentication requires CSRF protection. The exact credential/recovery and TLS
topology require an owning future phase and accepted security design.

---

## 12. State and degradation matrix

Every data-backed screen MUST define the applicable states before implementation.

| State | Required presentation | Prohibited behavior |
| --- | --- | --- |
| Loading | Stable skeleton or bounded progress; retain usable previous content when safe | Blank indefinite spinner replacing valid local state |
| Empty | Explain what is empty and provide one valid next action | Fake sample data presented as the user's library |
| Offline | Show local content and a quiet connectivity status | Blocking local actions or repeated modal errors |
| Server unavailable | Preserve local content, queue and pending intent; offer retry/status | Treating server absence as logout or data loss |
| Authentication attention | Explain reconnect/re-pair impact; preserve local library | Clearing local media or claiming remote revocation after local disconnect |
| Partial/sync pending | Human summary such as `14 tracks waiting for sync` | Cursor, hash, epoch or raw protocol terminology in normal UI |
| Conflict/review | Preserve both evidence paths and provide safe review | Silent last-write-wins or uncertain merge |
| Permission revoked | Retain track/reference and offer reauthorization/rescan/Vault fallback | Deleting the track intent automatically |
| Playback unavailable | Keep queue position and explain retry/source options | Dropping the queue or reporting playback success |
| Terminal error | Stable message, relevant retry/help and diagnostic code access | Stack trace, token, private URL or personal payload |

Problems Requiring Attention SHOULD aggregate actionable failures rather than scatter repeated alerts
through Home.

---

## 13. Import, export and privacy

### 13.1. Profile export

The portable profile includes, as applicable:

- track references and metadata;
- playlists and duplicate/order semantics;
- Like/Dislike and preference state;
- source references safe for export;
- recommendation/taste settings and compatible model provenance;
- import history;
- optional embeddings or fingerprint index under explicit format/version rules.

Audio bytes are optional and separate. UI MUST state whether an export is metadata-only, includes
selected media, or is a server backup.

### 13.2. Settings export

Settings export MAY include appearance, quality and non-secret user preferences. It MUST exclude:

- credentials and sessions;
- API/stream private origins;
- device binding identifiers not required by the portable format;
- device-specific SAF URI permissions;
- raw filesystem paths;
- room codes and transient invitation material.

### 13.3. Import safety

Import is additive by default and MUST preview destructive or conflicting effects. Unknown fields
from newer versions are preserved or rejected according to the owning versioned contract; they are
not silently reinterpreted.

Telemetry and external analytics remain off by default. Personal recommendation/history data MUST
not leave the user's systems without explicit permission.

---

## 14. Accessibility and internationalization

- Text and meaningful controls MUST meet WCAG AA contrast; large decorative artwork is exempt only
  where no information depends on it.
- Touch targets are at least 48 dp and remain separated under font scaling.
- TalkBack traversal follows visual and action order.
- Icon-only controls have stable accessible labels and selected/disabled state.
- Slider exposes role, range, current value and adjustable actions.
- Dynamic status uses polite announcements and does not repeatedly announce playback ticks.
- Layout MUST remain usable at 200% font scaling without clipping critical actions.
- Color, motion or sound alone MUST NOT communicate state.
- User-facing strings MUST be resource-backed. Russian and English content expansion MUST be tested.
- Time, count and storage values use locale-aware formatting while stable diagnostic codes remain
  ASCII.

---

## 15. Verification contract

### 15.1. Required evidence types

UI delivery requires all applicable evidence:

1. behavior tests for state transitions and user intent;
2. visual comparison or approved screenshots for representative states;
3. accessibility semantics checks;
4. compact, medium and expanded layout checks;
5. physical-device smoke on Samsung SM-A556E and minimum-SDK evidence where behavior depends on the
   platform;
6. offline/server-unavailable and process-restart checks;
7. no fake-success or secret/path exposure review.

Screenshot tests alone are insufficient for interaction behavior. A connected test count alone is
insufficient for visual-concept conformance.

### 15.2. Representative visual matrix

At minimum, capture or compare:

| Surface | Compact | Medium/expanded | Light/dark | Special state |
| --- | --- | --- | --- | --- |
| Home | Required | Required | Both | Local mode, populated, offline |
| Search | Required | Required | Both | Local results plus remote unavailable |
| Library | Required | Required | Both | Empty, populated, permission revoked |
| Mini-player | Required | Required | Both | Playing, paused, unavailable |
| Now Playing | Required | Required | Both | Dragging timeline, buffering, Wave member lock |
| Profile | Required | Required | Both | Local mode and connected account |
| Server connection | Required | Required | Both | Not connected, pairing, unavailable |
| Settings | Required | Required | Both | Large font and SAF-selected folder label |

Suggested width samples are 360 dp and 412 dp compact, 700 dp medium and 1000 dp expanded. Exact
device pixels are evidence metadata, not a replacement for dp width-class behavior.

### 15.3. Product acceptance questions

A UI milestone does not pass until reviewers can answer yes to all applicable questions:

- Does the app look recognizably derived from the approved concept?
- Can a new user play local music without configuring a server?
- Is the current track and playback state always understandable?
- Can the user seek accurately without the UI fighting the gesture?
- Are unavailable server features honest and recoverable?
- Can local, server and external data be distinguished without exposing infrastructure complexity?
- Can the user tell which human account and server connection are active?
- Are export scope and destructive consequences clear before confirmation?
- Does resize, rotation or process restart preserve meaningful state?
- Are private values absent from normal UI, export and diagnostics?

---

## 16. Fixed, flexible and deferred decisions

### 16.1. Fixed without contract revision

- Android-first, local-first and server-optional behavior;
- the warm, artwork-led visual identity and persistent mini-player;
- Home, Search and Library as primary compact destinations;
- a real expanded player with interactive seek;
- honest capability-driven UI with no placeholder success;
- separation of Account, Server connection, Taste context and Quality preset;
- SAF/MediaStore rather than raw Android paths;
- accessibility and adaptive-width requirements;
- privacy, authorization and unresolved-first boundaries.

### 16.2. Flexible within the baseline

- exact spacing, typography metrics and animation curves;
- exact card count and Home section order based on available data;
- gradient placeholder artwork;
- whether Wave becomes a fourth primary compact destination after usability evidence;
- whether elapsed/remaining time is the default timeline label;
- tablet list/detail composition;
- exact icon family, provided assets are bundled and visually coherent;
- later visual refinement of palettes while contrast and identity remain intact.

### 16.3. Deferred and not implied

- public multi-user registration;
- email/password login, recovery and password change persistence;
- production domain, TLS/reverse proxy and public network topology;
- a full browser music player;
- production backup target/retention provider;
- external music provider selection;
- request-to-host Wave seek;
- Party Mode, lyrics, year review and statistics unless separately scoped;
- production signing, publication or deployment.

---

## 17. Change governance

1. Visual refinement that stays inside section 16.2 requires updated approved frames and visual
   evidence, not an ADR.
2. A change to local-first behavior, security/data boundaries, identity meaning, playback ownership
   or persistence requires the owning specification/ADR process.
3. New UI MUST name its real application/API contract and failure states before implementation.
4. A future product phase or post-RC milestone prompt SHOULD name this document as a design input
   and declare which screen subset and acceptance matrix it owns.
5. Completion of this contract does not update P00-P14 acceptance rows and does not create P15.
6. The HTML concept may evolve. A successor reference MUST record version/date/hash and list
   intentional deviations from this baseline.

---

## 18. Recommended delivery slices

These are planning slices, not automatically started phases:

1. **UI foundation:** theme tokens, components, adaptive shell and state patterns.
2. **Playback vertical slice:** mini-player, Now Playing, timeline, queue and process recovery.
3. **Core local product:** Home, Search, Library and item detail with real Room/Media3 data.
4. **Profile and connection:** Local mode, account terminology, server pairing and explicit data
   materialization consent.
5. **Server/admin surfaces:** capability-driven Android server views and minimum Admin Web UI.
6. **Deep product surfaces:** imports/review, downloads, recommendations, Wave, diagnostics and
   multi-pane refinement.
7. **Visual/accessibility qualification:** representative matrix, physical device, offline/failure
   behavior and independent review.

Each slice MUST preserve already verified local-first, sync, Vault, identity and security behavior.

---

## 19. Current implementation gap recorded by this contract

The post-P14 frontend proves technical navigation and contract bindings but does not yet satisfy this
visual/product contract. In particular:

- the current text/button-heavy technical shell is not concept-conformant product UI;
- seek exists as fixed-step Media3 commands, but current `PlaybackUiState` has no duration, exposes
  `bufferedMs` as remaining buffered-ahead duration rather than absolute buffered position, and has
  no visible periodic position ticker; the presentation therefore lacks a complete interactive
  timeline;
- Profile does not have password, recovery or device-enumeration contracts;
- normal browser access to a server administration UI is not delivered;
- several item-detail and expanded multi-pane flows remain incomplete;
- connected-test success is functional evidence, not visual acceptance.

This gap is an honest planning baseline, not a regression of the P14 local RC or frontend M1/M2
handoffs.
