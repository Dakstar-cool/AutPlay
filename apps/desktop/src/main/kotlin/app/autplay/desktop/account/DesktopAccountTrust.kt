package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.securestate.*
import app.autplay.desktop.security.DeviceSignatureCodec
import java.math.BigInteger
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.X509EncodedKeySpec
import java.time.Duration
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.*

public class AccountM5Capabilities internal constructor(
    public val guard: M5CapabilityGuard, public val expiresAt: Instant,
    public val operations: Set<String>, public val requiredFeatures: Set<String>,
) {
    public fun requireOperation(operation: String, now: Instant) {
        controlRequire(expiresAt > now && requiredFeatures.isEmpty() && operation in operations, AccountControlFailure.UNSUPPORTED)
    }
    override fun toString(): String = "AccountM5Capabilities(<redacted>)"
}

/** Exact-key M5 verification. A displayed fingerprint is only accepted by explicit confirmIdentity. */
public object DesktopAccountTrust {
    public fun confirmIdentity(rawDiscovery: ByteArray, transport: ConfirmedAccountTransport,
        expectedInstance: String, expectedEpoch: Long, expectedSpki: ByteArray,
        identityConfirmed: Boolean, now: Instant): D1.TrustedM5Identity {
        controlRequire(identityConfirmed, AccountControlFailure.TRUST_REQUIRED)
        canonicalUuid(expectedInstance); DeviceSignatureCodec.validateSpki(expectedSpki)
        val payload = signed(rawDiscovery, expectedSpki, "AutPlay discovery v1\n", 16_384)
        val spki = decode(payload.string("identity_public_key_spki_b64"), false, 91)
        try {
            controlRequire(spki.contentEquals(expectedSpki) && payload.string("server_instance_id") == expectedInstance &&
                payload.number("identity_epoch") == expectedEpoch && expectedEpoch > 0 &&
                payload.string("identity_thumbprint_sha256") == secureSha256(expectedSpki) &&
                payload.string("api_origin") == transport.api.toASCIIString() &&
                payload.string("stream_origin") == transport.stream.toASCIIString(), AccountControlFailure.TRUST_REQUIRED)
            val majors = payload.array("supported_api_majors")
            controlRequire(majors.size in 1..8 && majors.distinct().size == majors.size && JsonPrimitive(1) in majors)
            controlRequire(payload.boolean("enrollment_protocol_available"), AccountControlFailure.UNSUPPORTED)
            display(payload.string("label_hint"), 80); fresh(payload, now, 600)
            return D1.TrustedM5Identity(expectedInstance, expectedEpoch, expectedSpki,
                secureSha256(expectedSpki), payload.string("api_origin"), payload.string("stream_origin"))
        } finally { spki.fill(0) }
    }

    public fun capabilities(raw: ByteArray, trusted: D1.TrustedM5Identity, account: String,
        device: String, previous: M5CapabilityGuard?, now: Instant): AccountM5Capabilities {
        val key = trusted.publicKeySpki
        val payload = try { signed(raw, key, "AutPlay capabilities v1\n", 65_536) } finally { key.fill(0) }
        controlRequire(payload.string("server_instance_id") == trusted.serverInstanceId &&
            payload.number("identity_epoch") == trusted.identityEpoch && payload.string("user_id") == account &&
            payload.string("device_id") == device && payload.number("api_major") == 1L, AccountControlFailure.CONTEXT_CHANGED)
        val expiry = fresh(payload, now, 3600)
        display(payload.string("product_version"), 32)
        fun names(name: String, maximum: Int): Set<String> {
            val array = payload[name] as? JsonArray ?: if (name == "required_features") JsonArray(emptyList()) else
                throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
            controlRequire(array.size <= maximum)
            val result = array.map { (it as? JsonPrimitive)?.takeIf { value -> value.isString }?.content
                ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE) }
            controlRequire(result.distinct().size == result.size && result.all { Regex("[A-Za-z][A-Za-z0-9_.-]{0,63}").matches(it) })
            return result.toSet()
        }
        val limits = payload["limits"] as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
        controlRequire(limits.size <= 64 && limits.keys.all { limits.number(it) <= Int.MAX_VALUE })
        // Frozen M5 policy is a singleton revision, independently of D1/B1 effective hashes.
        val guard = M5CapabilityGuard(payload.number("capability_revision"))
        controlRequire(guard.revision > 0 && (previous == null || previous.accepts(guard)), AccountControlFailure.CONTEXT_CHANGED)
        return AccountM5Capabilities(guard, expiry, names("operations", 64), names("required_features", 16))
    }

    internal fun signed(raw: ByteArray, spki: ByteArray, domain: String, maximum: Int): JsonObject {
        val envelope = SecureJson.parse(raw, maximum, canonical = false)
        envelope.exact("contract_version", "schema_version", "payload", "payload_sha256", "signature_algorithm", "signature_b64url")
        controlRequire(envelope.string("contract_version") == "v1" && envelope.number("schema_version") == 1L &&
            envelope.string("signature_algorithm") == "ES256-P1363")
        DeviceSignatureCodec.validateSpki(spki)
        val payload = envelope["payload"] as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
        val encoded = SecureJson.encode(payload)
        val digest = MessageDigest.getInstance("SHA-256").digest(encoded)
        try {
            controlRequire(encoded.size <= maximum && envelope.string("payload_sha256") ==
                digest.joinToString("") { "%02x".format(it.toInt() and 255) })
            val signature = decode(envelope.string("signature_b64url"), true, 64)
            try {
                val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as ECPublicKey
                fun integer(offset: Int): ByteArray {
                    val n = BigInteger(1, signature.copyOfRange(offset, offset + 32))
                    controlRequire(n.signum() > 0 && n < key.params.order)
                    return n.toByteArray()
                }
                val r = integer(0); val s = integer(32)
                val der = byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 2, r.size.toByte()) + r + byteArrayOf(2, s.size.toByte()) + s
                controlRequire(Signature.getInstance("SHA256withECDSA").run {
                    initVerify(key); update(domain.toByteArray(Charsets.US_ASCII)); update(digest); verify(der)
                }, AccountControlFailure.TRUST_REQUIRED)
            } finally { signature.fill(0) }
            return payload
        } finally { encoded.fill(0); digest.fill(0) }
    }

    internal fun fresh(payload: JsonObject, now: Instant, ttl: Long): Instant {
        val issued = Instant.parse(payload.string("issued_at")); val expires = Instant.parse(payload.string("expires_at"))
        controlRequire(issued <= now.plusSeconds(120) && expires > now && expires > issued &&
            Duration.between(issued, expires).seconds <= ttl && payload.string("issued_at").endsWith('Z') &&
            payload.string("expires_at").endsWith('Z'), AccountControlFailure.EXPIRED)
        return expires
    }
    internal fun display(value: String, maximum: Int) {
        controlRequire(value.codePointCount(0, value.length) in 1..maximum && value.none { it.code < 32 || it.code == 127 })
    }
    internal fun decode(value: String, url: Boolean, size: Int): ByteArray {
        val bytes = try { if (url) Base64.getUrlDecoder().decode(value) else Base64.getDecoder().decode(value) }
        catch (_: Exception) { throw AccountControlException(AccountControlFailure.INVALID_RESPONSE) }
        val same = if (url) Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) else Base64.getEncoder().encodeToString(bytes)
        if (bytes.size != size || same != value) { bytes.fill(0); throw AccountControlException(AccountControlFailure.INVALID_RESPONSE) }
        return bytes
    }
}
