package app.autplay.desktop.security

import app.autplay.application.securestate.*
import java.time.Instant
import kotlinx.serialization.json.*

/** Created only after exact response/request correlation, never from an ordinary projection. */
public class VerifiedSecureSession internal constructor(
    public val pendingOwner: SecureRecordOwner, public val owner: SecureRecordOwner,
    private val payload: SessionPayload, response: ByteArray,
) : AutoCloseable {
    private val response: SecretBytes = SecretBytes(response)
    internal fun responseCopy(): ByteArray = response.copy()
    internal fun pendingCopy(pending: PendingPayload): PendingPayload {
        val bytes = response.copy()
        try { return pending.duplicate(bytes) } finally { bytes.fill(0) }
    }
    internal fun payloadCopy(): SessionPayload {
        val record = SecureRecord(requireNotNull(owner.profile), 1, java.util.UUID.randomUUID().toString(), owner, payload)
        val encoded = SecureRecordCodec.encode(record)
        return try { SecureRecordCodec.decode(encoded).payload as SessionPayload } finally { encoded.fill(0) }
    }
    override fun close() { payload.close(); response.close() }
    override fun toString(): String = "VerifiedSecureSession(<redacted>)"
}

public object SecureSessionResponseCodec {
    /**
     * Transport already owns authenticated HTTPS, exact trusted destination and bounded reads.
     * This method rejects unknown/duplicate members and mismatched original IDs before custody.
     * No response may return a refresh credential; the exact pre-dispatch successor is retained.
     */
    public fun verify(response: ByteArray, pendingRecord: SecureRecord, now: Instant,
        cacheControl: String, pragma: String, d1: CapabilityGuard, m5: M5CapabilityGuard?,
        localMediaGuard: ByteArray, transportPolicy: ByteArray, m5HighWater: ByteArray): VerifiedSecureSession {
        try {
            secureRequire(cacheControl.split(',').any { it.trim().equals("no-store", true) } && pragma.split(',').any { it.trim().equals("no-cache", true) })
            val owner = pendingRecord.owner
            secureRequire(owner.stage in setOf(SecureStage.DISPATCHED, SecureStage.OUTCOME_PENDING, SecureStage.BINDING_COMMIT_PENDING))
            val pending = pendingRecord.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            secureRequire(pending.wireKind in setOf(PendingWireKind.ENROLLMENT_EXCHANGE, PendingWireKind.ADMISSION_EXCHANGE,
                PendingWireKind.TRUSTED_EXCHANGE, PendingWireKind.SESSION_ROTATE, PendingWireKind.SELF_EXCHANGE,
                PendingWireKind.RECOVERY_COMMIT, PendingWireKind.DELETION_CANCEL, PendingWireKind.PORTABLE_REGISTER,
                PendingWireKind.PORTABLE_LOGIN), SecureStateStatus.CONTEXT_CHANGED)
            val wire = pending.requestCopy(); val successor = pending.successorCopy()
            try {
                val original = SecureJson.parse(wire, pending.operation.maximumWire, canonical = false)
                val request = if (pending.operation in setOf(PendingOperation.PORTABLE_REGISTER, PendingOperation.PORTABLE_LOGIN)) original["request"] as JsonObject else original
                val expectedRefresh = request.string("next_refresh_token_sha256")
                secureRequire(successor.size in 43..128 && secureSha256(successor) == expectedRefresh)
                val value = SecureJson.parse(response, 65536, canonical = false)
                secureRequire(value.string("contract_version") == "v2" && value.number("schema_version") == 2L && value.string("platform") == "MACOS")
                val common = setOf("contract_version", "schema_version", "platform", "binding_commit_id", "server_instance_id", "user_id", "device_id", "session_id", "refresh_generation", "refresh_absolute_expires_at", "receipt_expires_at", "access_token", "access_expires_at", "replayed")
                val session: String; val family: String; val generation: Long; val device: String
                val absolute: Instant
                when (pending.operation) {
                    PendingOperation.ROTATION -> {
                        secureRequire(value.keys == setOf("contract_version", "schema_version", "platform", "rotation_id", "session_id", "parent_session_id", "family_id", "generation", "refresh_absolute_expires_at", "receipt_expires_at", "access_token", "access_expires_at", "replayed"))
                        secureRequire(value.string("rotation_id") == owner.operation && value.string("rotation_id") == request.string("rotation_id") && value.string("parent_session_id") == owner.session && value.string("family_id") == owner.family)
                        secureRequire(value.number("generation") == SecureStateLimits.next(requireNotNull(owner.sessionGeneration)))
                        session = value.string("session_id"); family = value.string("family_id"); generation = value.number("generation"); device = requireNotNull(owner.device)
                        absolute = instant(value, "refresh_absolute_expires_at")
                        value.boolean("replayed"); instant(value, "receipt_expires_at")
                    }
                    PendingOperation.PORTABLE_REGISTER, PendingOperation.PORTABLE_LOGIN -> {
                        secureRequire(value.keys == setOf("contract_version", "schema_version", "platform", "account_id", "device_id", "session_id", "session_family_id", "generation", "binding_commit_id", "issued_at", "access_expires_at", "refresh_expires_at", "access_token"))
                        secureRequire(value.string("account_id") == owner.account && value.string("binding_commit_id") == owner.bindingCommit && value.number("generation") == 0L)
                        session = value.string("session_id"); family = value.string("session_family_id"); generation = 0; device = value.string("device_id")
                        absolute = instant(value, "refresh_expires_at")
                        secureRequire(!instant(value, "issued_at").isAfter(now.plusSeconds(120)))
                    }
                    PendingOperation.RECOVERY, PendingOperation.DELETION -> {
                        secureRequire(value.keys == common + setOf("operation_id", "account_label", "code_generation", "outcome_recovered"))
                        secureRequire(value.string("operation_id") == owner.operation && value.string("operation_id") == request.string("operation_id"))
                        secureRequire(value.number("code_generation") == SecureStateLimits.next(request.number("expected_code_generation")))
                        secureRequire(value.string("account_label").length in 1..200); value.boolean("outcome_recovered")
                        requireBinding(value, owner); value.boolean("replayed")
                        session = value.string("session_id"); family = session; generation = 0; device = value.string("device_id"); absolute = instant(value, "refresh_absolute_expires_at")
                        instant(value, "receipt_expires_at")
                    }
                    PendingOperation.ENROLLMENT, PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE, PendingOperation.SELF -> {
                        secureRequire(value.keys == common + "exchange_id")
                        secureRequire(value.string("exchange_id") == owner.operation && value.string("exchange_id") == request.string("exchange_id"))
                        requireBinding(value, owner); value.boolean("replayed")
                        session = value.string("session_id"); family = session; generation = 0; device = value.string("device_id"); absolute = instant(value, "refresh_absolute_expires_at")
                        instant(value, "receipt_expires_at")
                    }
                    else -> throw SecureStateFailure(SecureStateStatus.CONTEXT_CHANGED)
                }
                listOf(session, family, device).forEach(::canonicalUuid)
                secureRequire(owner.device == null || owner.device == device, SecureStateStatus.CONTEXT_CHANGED)
                val accessExpires = instant(value, "access_expires_at")
                secureRequire(accessExpires > now && absolute > now && accessExpires <= absolute)
                val accessText = value.string("access_token")
                secureRequire(accessText.length in 32..8192 && accessText.all { it.code in 33..126 })
                val access = accessText.toByteArray(Charsets.US_ASCII)
                try {
                    val bound = owner.copy(account = owner.account ?: value.string("user_id"), device = device,
                        session = session, family = family, sessionGeneration = generation,
                        stage = SecureStage.BINDING_COMMIT_PENDING, detached = false, deletionVeto = false, accountProjection = null)
                    bound.requireBinding()
                    return VerifiedSecureSession(owner, bound, SessionPayload(access, successor, d1, m5, localMediaGuard, transportPolicy, m5HighWater, accessExpires.epochSecond, absolute.epochSecond), response)
                } finally { access.fill(0) }
            } finally { wire.fill(0); successor.fill(0) }
        } catch (failure: SecureStateFailure) { throw failure }
        catch (_: Exception) { throw SecureStateFailure(SecureStateStatus.CORRUPT) }
    }
    private fun requireBinding(value: JsonObject, owner: SecureRecordOwner) {
        secureRequire(value.string("binding_commit_id") == owner.bindingCommit && value.string("server_instance_id") == owner.server &&
            (owner.account == null || value.string("user_id") == owner.account) && value.number("refresh_generation") == 0L)
    }
    private fun instant(value: JsonObject, field: String): Instant {
        val text = value.string(field); secureRequire(text.length in 20..40 && text.endsWith('Z'))
        return Instant.parse(text)
    }
}
