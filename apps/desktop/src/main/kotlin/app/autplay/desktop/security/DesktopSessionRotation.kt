package app.autplay.desktop.security

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2
import app.autplay.application.securestate.*
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.serialization.json.*

/** D1-only renewal. A fresh successor and final original wire are durable before transport. */
public class DesktopSessionRotation(
    private val secure: DesktopSecureStateOwner,
    private val signer: DesktopDeviceSigner,
    private val custody: DesktopSecureCustody,
) {
    public fun prepare(snapshot: SecureControl, binding: SecureRecordOwner,
        trusted: DeviceContractV2Negotiation.TrustedM5Identity, pendingSlot: String,
        now: Instant): CompletableFuture<SecurePendingTicket> = secure.read(requireNotNull(binding.profile), snapshot).thenCompose { nullable ->
        val stored = nullable ?: throw SecureStateFailure(SecureStateStatus.UNAVAILABLE)
        try {
            secureRequire(stored.owner == binding && binding.stage == SecureStage.ACTIVE && snapshot.activeBinding == binding.profile, SecureStateStatus.CONTEXT_CHANGED)
            requireTrust(binding, trusted)
            val session = stored.payload as? SessionPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(session.refreshExpiresAt > now.epochSecond, SecureStateStatus.CONTEXT_CHANGED)
            val rotation = UUID.randomUUID().toString()
            val owner = binding.copy(operation = rotation, ceremony = rotation, role = SecureRole.AUTHENTICATED,
                stage = SecureStage.PREPARED, expiresAtEpochSeconds = session.refreshExpiresAt)
            secure.reserveCompletion(snapshot, CompletionReservation(rotation, listOf(pendingSlot, requireNotNull(binding.profile)).distinct(), 8, 4L * SecureStateLimits.VALUE)).thenCompose { reserved ->
            secure.publicKey(reserved, requireNotNull(binding.keyTag)).thenCompose { key ->
                val refresh = session.refreshCopy()
                val rawSuccessor = ByteArray(32).also { SecureRandom().nextBytes(it) }
                val successor = try { Base64.getUrlEncoder().withoutPadding().encode(rawSuccessor) } finally { rawSuccessor.fill(0) }
                try {
                    val context = ProfileDeviceContractV2.rotationContext(trusted, DeviceContractV2Negotiation.Platform.MACOS, key,
                        requireNotNull(binding.account), requireNotNull(binding.device), requireNotNull(binding.session),
                        requireNotNull(binding.family), requireNotNull(binding.bindingCommit), requireNotNull(binding.sessionGeneration),
                        rotation, secureSha256(refresh), secureSha256(successor), Instant.ofEpochSecond(session.refreshExpiresAt))
                    val document = buildJsonObject {
                        put("contract_version", "v2"); put("schema_version", 2); put("platform", "MACOS")
                        put("expected_server_instance_id", trusted.serverInstanceId); put("expected_identity_epoch", trusted.identityEpoch)
                        put("expected_identity_thumbprint_sha256", trusted.thumbprintSha256); put("expected_api_origin", trusted.apiOrigin); put("expected_stream_origin", trusted.streamOrigin)
                        put("rotation_id", rotation); put("device_id", binding.device); put("parent_session_id", binding.session)
                        put("current_generation", binding.sessionGeneration); put("current_refresh_token", refresh.toString(Charsets.US_ASCII))
                        put("next_refresh_token_sha256", secureSha256(successor)); put("signature_algorithm", "ES256-P1363")
                    }
                    val bytes = SecureJson.encode(document)
                    val prepared = try { ProfileDeviceContractV2.rawBody(bytes).use { ProfileDeviceContractV2.prepare(ProfileDeviceContractV2.Operation.SESSION_ROTATE, it, context, now) } } finally { bytes.fill(0) }
                    val successorOwner = secure.transientSecret(successor)
                    signer.sign(reserved, owner, prepared).thenCompose { signed ->
                        val exactSuccessor = successorOwner.copy()
                        try { custody.prepareProfile(reserved, pendingSlot, owner, signed, byteArrayOf(), exactSuccessor) }
                        finally { exactSuccessor.fill(0); signed.close() }
                    }.whenComplete { _, _ -> successorOwner.close(); prepared.close() }
                } finally { key.fill(0); refresh.fill(0); successor.fill(0) }
            }
            }.whenComplete { _, _ -> stored.close() }
        } catch (failure: Exception) { stored.close(); throw failure }
    }

    /** Existing pending owner is read/replayed; this method never creates another successor. */
    public fun resume(snapshot: SecureControl, pendingSlot: String,
        expected: SecureRecordOwner, originalContext: ProfileDeviceContractV2.Context): CompletableFuture<SecurePendingTicket> =
        custody.resume(snapshot, pendingSlot, expected) { record ->
            SecureJournalRestoration.profile(record, originalContext).use { original ->
                secureRequire(original.operation == ProfileDeviceContractV2.Operation.SESSION_ROTATE, SecureStateStatus.CONTEXT_CHANGED)
                true
            }
        }

    public fun commit(result: SecureDispatchResult, now: Instant, cacheControl: String, pragma: String,
        d1: CapabilityGuard, m5: M5CapabilityGuard?, localMediaGuard: ByteArray,
        transportPolicy: ByteArray, m5HighWater: ByteArray): CompletableFuture<SecureControl> =
        secure.read(result.ticket.slot, result.ticket.control).thenCompose { nullable ->
            val pending = nullable ?: throw SecureStateFailure(SecureStateStatus.OUTCOME_PENDING)
            val response = result.response.copy()
            try {
                val verified = SecureSessionResponseCodec.verify(response, pending, now, cacheControl, pragma,
                    d1, m5, localMediaGuard, transportPolicy, m5HighWater)
                custody.commitSession(result.ticket, verified)
            } finally { pending.close(); response.fill(0); result.close() }
        }

    private fun requireTrust(owner: SecureRecordOwner, trust: DeviceContractV2Negotiation.TrustedM5Identity) {
        secureRequire(owner.server == trust.serverInstanceId && owner.identityEpoch == trust.identityEpoch && owner.identityThumbprint == trust.thumbprintSha256 && owner.apiOrigin == trust.apiOrigin && owner.streamOrigin == trust.streamOrigin, SecureStateStatus.CONTEXT_CHANGED)
    }
}

/** Typed credentials only: no legacy raw-token fallback or generic JSON credential interface. */
public class DesktopCredentialStore(private val secure: DesktopSecureStateOwner) {
    public fun access(snapshot: SecureControl, binding: SecureRecordOwner): CompletableFuture<SecretBytes> = secure.access(snapshot, binding)
    public fun disconnect(snapshot: SecureControl): CompletableFuture<SecureControl> = secure.detach(snapshot)
    public fun hasPendingRegistration(snapshot: SecureControl): CompletableFuture<Boolean> {
        val candidates = snapshot.selected.map { it.slot }
        fun next(index: Int): CompletableFuture<Boolean> {
            if (index == candidates.size) return CompletableFuture.completedFuture(false)
            return secure.read(candidates[index], snapshot).thenCompose { stored ->
                val pending = try { stored?.let { it.payload is PendingPayload && it.owner.stage != SecureStage.TERMINAL &&
                    (it.payload as PendingPayload).operation in setOf(PendingOperation.ENROLLMENT, PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE, PendingOperation.PUBLIC_REGISTRATION, PendingOperation.PORTABLE_REGISTER, PendingOperation.PORTABLE_LOGIN) } ?: false }
                finally { stored?.close() }
                if (pending) CompletableFuture.completedFuture(true) else next(index + 1)
            }
        }
        return next(0) // Any unreadable slot propagates an unavailable result, never false.
    }
}
