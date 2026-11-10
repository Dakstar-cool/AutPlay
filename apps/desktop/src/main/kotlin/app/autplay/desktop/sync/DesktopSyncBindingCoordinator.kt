package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.syncbinding.SyncBindingContractV2 as B1
import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import java.time.Instant

/** The production K/J owner performs publication and reads its actual newly issued lease. */
public interface DesktopSyncGuardPublication {
    public suspend fun publish(lease: AccountLease, owner: SecureRecordOwner, guard: CapabilityGuard,
        m5: M5CapabilityGuard, policy: ProtectedTransportPolicy): Unit
    public suspend fun deny(lease: AccountLease, owner: SecureRecordOwner): Unit
}

/** B1 confirms an existing enrolled MACOS device and the actual J epoch. It never calls v1 bind. */
public class DesktopSyncBindingCoordinator(private val store: DesktopAccountStore,
    private val transport: DesktopSyncTransport, private val publication: DesktopSyncGuardPublication) {
    private val pages = store.syncPages ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)

    public suspend fun confirm(lease: AccountLease) {
        val session = transport.session
        val original = session.capture(lease)
        val now = Instant.now()
        val discoveryRaw = session.control(original, AccountEndpoint.B1_DISCOVERY, transport.deadlineMs())
        val discovery = try { observed(original, B1.discovery(discoveryRaw, original.server.identity, now)) }
            finally { discoveryRaw.fill(0) }
        if (!discovery.bindingAvailable) deny(original, DesktopSyncFailure.UNAVAILABLE)

        val owner = B1.OwnerCapture(original.server.identity, requireNotNull(original.owner.account), requireNotNull(original.owner.device),
            D1.Platform.MACOS, session.currentGeneration())
        val d1raw = session.control(original, AccountEndpoint.D1_CAPABILITIES, transport.deadlineMs())
        val d1 = try { observedD1(original, D1.capabilities(d1raw, original.server.identity,
            D1.CapabilityScope(owner.userId, owner.deviceId, D1.Platform.MACOS), null, Instant.now())) }
            finally { d1raw.fill(0) }
        if (D1.Family.SESSION !in d1.snapshot.families) deny(original, DesktopSyncFailure.UNSUPPORTED)
        val d1Digest = d1.nextObservation.effectivePayloadSha256
        val d1Hash = try { hex(d1Digest) } finally { d1Digest.fill(0) }
        if (d1.snapshot.revision < original.guard.revision ||
            d1.snapshot.revision == original.guard.revision && d1Hash != original.guard.contentSha256)
            deny(original, DesktopSyncFailure.CONTEXT_CHANGED)
        val guard = observed(original, B1.revisionGuard(owner, original.guard.revision, null, d1.nextObservation))
        val b1raw = session.control(original, AccountEndpoint.B1_CAPABILITIES, transport.deadlineMs())
        val b1 = try { observed(original, B1.capabilities(b1raw, owner, guard, Instant.now())) } finally { b1raw.fill(0) }
        if (!b1.snapshot.bindingAvailable) deny(original, DesktopSyncFailure.UNAVAILABLE)
        // Concurrent policy publication requires a new bounded pass; never combine two revisions.
        if (b1.snapshot.revision != d1.snapshot.revision) deny(original, DesktopSyncFailure.CONTEXT_CHANGED)
        val observation = requireNotNull(b1.next.previousB1)
        val digest = observation.effectivePayloadSha256
        val b1Hash = try { hex(digest) } finally { digest.fill(0) }
        if (b1.snapshot.revision == original.guard.revision && original.guard.b1ContentSha256 != null &&
            original.guard.b1ContentSha256 != b1Hash) deny(original, DesktopSyncFailure.CONTEXT_CHANGED)

        val m5raw = session.control(original, AccountEndpoint.M5_CAPABILITIES, transport.deadlineMs())
        val m5 = try {
            try { DesktopAccountTrust.capabilities(m5raw, original.server.identity, owner.userId, owner.deviceId, original.m5, Instant.now()) }
            catch (_: AccountControlException) { deny(original, DesktopSyncFailure.CONTEXT_CHANGED) }
        } finally { m5raw.fill(0) }
        if (m5.requiredFeatures.isNotEmpty()) deny(original, DesktopSyncFailure.UNSUPPORTED)
        val next = CapabilityGuard(b1.snapshot.revision, d1Hash, b1Hash)
        session.revalidate(original)
        if (next != original.guard || m5.guard != original.m5) {
            session.fenceGuardPublication(lease)
            publication.publish(lease, original.owner, next, m5.guard, original.transportPolicy)
            // The transition really completed; this initiating pass still owns its OLD lease.
            // A fresh foreground retry must negotiate B1 again with the actual issued lease.
            throw DesktopSyncException(DesktopSyncFailure.CONTEXT_CHANGED)
        }
        val current = session.capture(lease)
        check(current.guard == next && current.m5 == m5.guard) { "ACCOUNT_SYNC_GUARD_READBACK_CHANGED" }
        val accepted = verified(B1.acceptCapabilities(b1, b1.next, owner))
        val state = pages.readSyncState(lease)
        check(state.deviceId.value == owner.deviceId) { "ACCOUNT_SYNC_DEVICE_CHANGED" }
        val lineage = B1.DurableLineage(owner, lease.profileId.value, state.journalEpoch.value)
        val context = verified(B1.bindContext(accepted, owner, lineage, b1.next, B1.BindMode.FIRST_BIND, null, Instant.now()))
        val request = verified(B1.encodeBind(context, owner, lineage, b1.next, Instant.now()))
        val body = request.canonicalBody
        try {
            session.request(lease, DomainRoute.Binding(), body, deadlineMs = transport.deadlineMs()).use { response ->
                transport.requireSuccess(response)
                verified(B1.decodeBindResult(response.status, response.bytes, request, owner, lineage))
                session.revalidate(current)
                val actual = pages.readSyncState(lease)
                check(actual.lineageId == state.lineageId && actual.deviceId == state.deviceId && actual.journalEpoch == state.journalEpoch &&
                    actual.revision == state.revision) { "ACCOUNT_SYNC_LINEAGE_CHANGED" }
                if (discovery.expiresAt <= Instant.now() || b1.snapshot.expiresAt <= Instant.now() ||
                    d1.snapshot.expiresAt <= Instant.now() || m5.expiresAt <= Instant.now()) throw DesktopSyncException(DesktopSyncFailure.UNAVAILABLE)
            }
        } finally { body.fill(0) }
    }

    private suspend fun deny(captured: DomainSessionCapture, failure: DesktopSyncFailure): Nothing {
        transport.session.fenceGuardPublication(captured.lease)
        publication.deny(captured.lease, captured.owner)
        throw DesktopSyncException(failure)
    }
    private suspend fun <T> observed(captured: DomainSessionCapture, result: B1.Result<T>): T =
        try { verified(result) } catch (failure: DesktopSyncException) { deny(captured, failure.failure) }

    private fun <T> verified(result: B1.Result<T>): T = when (result) {
        is B1.Result.Verified -> result.value
        is B1.Result.Rejected -> throw DesktopSyncException(when (result.failure) {
            B1.Failure.BINDING_UNAVAILABLE, B1.Failure.UNAVAILABLE -> DesktopSyncFailure.UNAVAILABLE
            B1.Failure.UNSUPPORTED -> DesktopSyncFailure.UNSUPPORTED
            else -> DesktopSyncFailure.CONTEXT_CHANGED
        })
    }
    private suspend fun <T> observedD1(captured: DomainSessionCapture, result: D1.Result<T>): T = when (result) {
        is D1.Result.Verified -> result.value
        is D1.Result.Rejected -> deny(captured, DesktopSyncFailure.CONTEXT_CHANGED)
    }
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { (it.toInt() and 255).toString(16).padStart(2, '0') }
}
