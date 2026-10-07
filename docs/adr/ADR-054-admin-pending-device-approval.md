# ADR-054: Admin approval from the pending-device list

- Status: Requested by the user on 2026-10-06; implementation verified locally
- Scope: Personal-server device admission and administrative device names

## Decision

The user requested approval in Admin Web without transferring a device locator or comparison code,
and a device name chosen during approval. This supersedes the mandatory locator/SAS ceremony and
the absence of a pending request list in ADR-035 for this new Web flow. The legacy resolver remains
available for older clients.

An authenticated OWNER/ADMIN sees at most 50 pending requests for the current server identity.
Requests already bound to another Web session remain unavailable. Each approval requires an
explicit same-origin POST with M6 CSRF validation. The transaction rechecks the actor's account,
role, browser session, current server identity, request state and expiry. The approved account is
always the actor's account; HTML cannot select a different account.

Signed request submission, exact P-256 key proof, poll bearer protection, signed Android exchange
and explicit account confirmation remain required. Admin approval does not issue an Android
session. Exact-key trust is created only with successful exchange. Request metadata is supplied by
the device, so the administrator must recognize the device being connected. The new ceremony
provides no out-of-band comparison-code protection against approving the wrong pending request.

The administrator's name is a separate nullable column, bounded to 120 Unicode characters and
validated against control/format characters. Signed nickname and request hash remain unchanged.
The name is used for device issuance, trusted-device presentation and same-key re-enrollment.
The migration preserves existing rows and refuses downgrade while administrator names exist.

Admin pending requests refresh periodically without overwriting edited names. Android moves directly
to pending status and polls only while the screen is resumed. Revoked devices and removed trust are
excluded from active administrative lists; their durable history and revocation receipts remain.
