package app.autplay.application.devicecompatibility

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Discovery
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Family
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * Pure CLIENT D1 prerequisite, not a controller, transport, authority grant or pending store.
 * Context inputs must be resolved by the caller under its current lifecycle/authority fences.
 * Source bearer/device/session family/generation, explicit SAS/account consent, HTTPS/PKI,
 * header possession, gates, rates, expiry and server replay authority remain independent checks.
 * K/J are unapproved: retain neither QR/header secrets nor retry state in ordinary DB/logs.
 * A future approved custodian must preserve exact originals and secrets before network effects;
 * this transient codec does not make a connected flow safe without that prerequisite.
 */
object SelfDeviceContractV2 {
    const val MAX_BODY_BYTES: Int = 8192
    const val MAX_QR_BYTES: Int = 4096
    private const val MAX_INTEGER: Long = 9_007_199_254_740_991L
    private const val SAS_DOMAIN: String = "autplay:device-compatibility:self:sas:v2\n"

    enum class Role { SOURCE, RECIPIENT }
    enum class Operation(val role: Role) {
        START(Role.SOURCE), CLAIM(Role.RECIPIENT), POLL(Role.RECIPIENT),
        DECISION(Role.SOURCE), EXCHANGE(Role.RECIPIENT);

        /** Closed registry. A caller cannot supply or replace the domain. */
        val proofDomain: String? get() = when (this) {
            CLAIM -> "autplay:device-compatibility:self:claim:v2\n"
            POLL -> "autplay:device-compatibility:self:poll:v2\n"
            EXCHANGE -> "autplay:device-compatibility:self:exchange:v2\n"
            START, DECISION -> null
        }
    }

    enum class Failure { INVALID_DOCUMENT, TOO_LARGE, IDENTITY_MISMATCH, CONTEXT_MISMATCH,
        INVALID_SIGNATURE, EXPIRED, CLOSED, UNAVAILABLE }
    class Rejected internal constructor(val failure: Failure) :
        IllegalArgumentException("SELF_V2_$failure", null) {
        override fun toString(): String = "SelfV2.Rejected($failure)"
    }

    /** Immutable caller-supplied scope; no secret, generic field override or authority inference. */
    class Context internal constructor(
        internal val identity: TrustedM5Identity,
        val platform: Platform,
        val sourcePlatform: Platform,
        val ceremonyId: String,
        internal val kind: Operation,
        internal val expiresAt: Instant? = null,
        spki: ByteArray? = null,
        internal val originalClaim: WireRequest? = null,
        internal val status: JsonObject? = null,
        internal val discoveryExpiresAt: Instant? = null,
    ) {
        private val key: ByteArray? = spki?.copyOf()
        internal fun keyCopy(): ByteArray? = key?.copyOf()
        override fun toString(): String = "SelfV2.Context"
    }

    class PreparedRequest internal constructor(
        val operation: Operation, internal val context: Context,
        internal val body: JsonObject, canonical: ByteArray, digest: ByteArray,
    ) {
        private val unsigned: ByteArray = canonical.copyOf()
        private val hash: ByteArray = digest.copyOf()
        val platform: Platform get() = context.platform
        val platformRole: Role get() = operation.role
        val proofDomain: String? get() = operation.proofDomain
        val requestSha256: String get() = hex(hash)
        fun canonicalUnsignedCopy(): ByteArray = unsigned.copyOf()
        fun requestDigestCopy(): ByteArray = hash.copyOf()
        fun publicKeySpkiCopy(): ByteArray? = context.keyCopy()
        val keyThumbprintSha256: String? get() = context.keyCopy()?.let { hex(sha(it)) }
        override fun toString(): String = "SelfV2.PreparedRequest"
    }

    /** Complete nonsecret body/proof evidence. It grants no session or replay authority. */
    class WireRequest internal constructor(
        internal val prepared: PreparedRequest, internal val document: JsonObject,
        canonical: ByteArray, signature: ByteArray?,
    ) {
        private val wire: ByteArray = canonical.copyOf()
        private val proof: ByteArray? = signature?.copyOf()
        private val originalDigest: ByteArray = sha(wire)
        val operation: Operation get() = prepared.operation
        val platform: Platform get() = prepared.platform
        val platformRole: Role get() = prepared.platformRole
        val requestSha256: String get() = prepared.requestSha256
        fun canonicalDocumentCopy(): ByteArray = wire.copyOf()
        fun signatureCopy(): ByteArray? = proof?.copyOf()
        fun originalDocumentDigestCopy(): ByteArray = originalDigest.copyOf()
        override fun toString(): String = "SelfV2.WireRequest"
    }

    /** Client-only bearer. Raw/encoded input and returned copies belong to the caller to erase. */
    class Qr internal constructor(
        internal val identity: TrustedM5Identity, val ceremonyId: String,
        val sourcePlatform: Platform, val expiresAt: Instant,
        private val expiryText: String, secret: ByteArray,
        internal val verifiedRecipient: Platform? = null,
        internal val discoveryExpiresAt: Instant? = null,
    ) : AutoCloseable {
        private val bearer: ByteArray = secret.copyOf()
        private var closed: Boolean = false
        @Synchronized fun rendezvousSecretCopy(): ByteArray {
            ensure(!closed, Failure.CLOSED)
            return bearer.copyOf()
        }
        @Synchronized fun encode(): ByteArray = guarded {
            ensure(!closed, Failure.CLOSED)
            boundedCanonical(JsonObject(trustFields(identity) + mapOf(
                "format" to JsonPrimitive("autplay-self-device-pairing"),
                "version" to JsonPrimitive(2), "contract_version" to JsonPrimitive("v2"),
                "schema_version" to JsonPrimitive(2), "ceremony_id" to JsonPrimitive(ceremonyId),
                "source_platform" to JsonPrimitive(sourcePlatform.name),
                "expires_at" to JsonPrimitive(expiryText),
                "rendezvous_secret" to JsonPrimitive(bearer.toString(Charsets.US_ASCII)),
            )), MAX_QR_BYTES)
        }
        @Synchronized override fun close() { bearer.fill(0); closed = true }
        override fun toString(): String = "SelfV2.Qr(<redacted>)"
    }

    fun sourceContext(trusted: TrustedM5Identity, platform: Platform, ceremonyId: String): Context = guarded {
        validateIdentity(trusted)
        uuid(ceremonyId)
        Context(trusted, platform, platform, ceremonyId, Operation.START)
    }

    /** QR has already passed separate trusted identity + signed discovery verification. */
    fun recipientContext(qr: Qr, platform: Platform, publicKeySpki: ByteArray, now: Instant): Context = guarded {
        qr.rendezvousSecretCopy().fill(0) // Also rejects a closed bearer owner.
        ensure(qr.verifiedRecipient == platform, Failure.CONTEXT_MISMATCH)
        ensure(qr.discoveryExpiresAt?.let { it > now } == true, Failure.EXPIRED)
        ensure(qr.expiresAt > now, Failure.EXPIRED)
        val key = publicKeySpki.copyOf()
        p256(key)
        Context(qr.identity, platform, qr.sourcePlatform, qr.ceremonyId, Operation.CLAIM, qr.expiresAt, key,
            discoveryExpiresAt = qr.discoveryExpiresAt)
    }

    /**
     * Restore ONLY a custodian's previously admitted, protected original claim scope. This does
     * not admit a QR/new claim, refresh discovery or extend any deadline. readRequest must verify
     * the original claim next; current server authority still decides every continuation.
     */
    fun restoredRecipientContext(trusted: TrustedM5Identity, platform: Platform, sourcePlatform: Platform,
        ceremonyId: String, expiresAt: Instant, publicKeySpki: ByteArray): Context = guarded {
        validateIdentity(trusted); uuid(ceremonyId); p256(publicKeySpki)
        Context(trusted, platform, sourcePlatform, ceremonyId, Operation.CLAIM, expiresAt, publicKeySpki)
    }

    /** Validates all status fields, platform separation and SAS against the original scope. */
    fun readStatus(original: WireRequest, raw: ByteArray): JsonObject = guarded {
        ensure(original.operation in setOf(Operation.START, Operation.CLAIM), Failure.CONTEXT_MISMATCH)
        val value = status(raw, original.prepared.context)
        if (original.operation == Operation.CLAIM) {
            claimMatches(value, original)
            if (value.text("state") !in setOf("APPROVED", "EXCHANGED")) ensure("account_id" !in value)
        }
        if (value.text("state") == "OPEN") ensure("claim_id" !in value)
        if (value.text("state") in setOf("APPROVED", "EXCHANGED")) ensure("account_id" in value)
        if ("device_id" in value) ensure(value.text("state") == "EXCHANGED")
        value
    }

    /** Status is from the exact authenticated source transport, never an untrusted QR response. */
    fun decisionContext(originalStart: WireRequest, statusBytes: ByteArray): Context = guarded {
        ensure(originalStart.operation == Operation.START, Failure.CONTEXT_MISMATCH)
        val scope = originalStart.prepared.context
        val status = status(statusBytes, scope)
        ensure(status.text("state") in setOf("OPEN", "CLAIMED"), Failure.CONTEXT_MISMATCH)
        Context(scope.identity, scope.platform, scope.sourcePlatform, scope.ceremonyId,
            Operation.DECISION, time(status.text("expires_at")), status = status)
    }

    /** POLL/EXCHANGE key, platform and claim digest are fixed by this original verified claim. */
    fun continuationContext(originalClaim: WireRequest, statusBytes: ByteArray): Context = guarded {
        ensure(originalClaim.operation == Operation.CLAIM, Failure.CONTEXT_MISMATCH)
        val scope = originalClaim.prepared.context
        val status = status(statusBytes, scope)
        claimMatches(status, originalClaim)
        Context(scope.identity, scope.platform, scope.sourcePlatform, scope.ceremonyId,
            Operation.POLL, scope.expiresAt, scope.keyCopy(), originalClaim, status)
    }

    /**
     * Exact UNSIGNED flat model: hash/signature members are forbidden, not overwritten.
     * All version/trust/platform fields must already match the typed context. No defaults.
     * Only new commands are prepared here; readRequest handles an exact historical original
     * without imposing fresh-command time rules on committed continuations.
     */
    fun prepare(operation: Operation, rawUnsigned: ByteArray, context: Context, now: Instant): PreparedRequest = guarded {
        val body = parse(rawUnsigned, MAX_BODY_BYTES)
        val prepared = prepareBody(operation, body, context)
        ensure(Duration.between(time(body.text("requested_at")), now).abs() <= Duration.ofSeconds(120),
            Failure.EXPIRED)
        if (operation == Operation.CLAIM) {
            ensure(context.expiresAt?.let { it > now } == true &&
                context.discoveryExpiresAt?.let { it > now } == true, Failure.EXPIRED)
        }
        prepared
    }

    fun completeBearer(prepared: PreparedRequest): WireRequest = guarded {
        ensure(prepared.platformRole == Role.SOURCE, Failure.CONTEXT_MISMATCH)
        finish(prepared, null)
    }

    /** Verify the exact prepared body/key using SHA256withECDSA MESSAGE(domain ASCII || raw D). */
    fun complete(prepared: PreparedRequest, signatureP1363: ByteArray): WireRequest = guarded {
        ensure(prepared.platformRole == Role.RECIPIENT, Failure.CONTEXT_MISMATCH)
        val signature = signatureP1363.copyOf()
        verify(prepared, signature)
        finish(prepared, signature)
    }

    /** No custody/replay fallback: caller supplies the complete original plus its locked scope. */
    fun readRequest(operation: Operation, rawBody: ByteArray, context: Context): WireRequest = guarded {
        val document = parse(rawBody, MAX_BODY_BYTES)
        val proofMembers = if (operation.role == Role.RECIPIENT) setOf("device_signature_b64url") else emptySet()
        ensure(document.keys == fields(operation) + "request_sha256" + proofMembers)
        val body = JsonObject(document.filterKeys { it != "request_sha256" && it !in proofMembers })
        val prepared = prepareBody(operation, body, context)
        ensure(hash(document.text("request_sha256")) == prepared.requestSha256)
        val signature = if (operation.role == Role.RECIPIENT) {
            decode(document.text("device_signature_b64url"), true, 64).also { verify(prepared, it) }
        } else null
        // Preserve the original strings and exact original signature, never a redacted projection.
        WireRequest(prepared, document, boundedCanonical(document, MAX_BODY_BYTES), signature)
    }

    /** Successful v2 start + locally retained encoded secret; no server plaintext-secret API. */
    fun qrFromStart(start: WireRequest, successfulStatus: ByteArray, localSecret: ByteArray, now: Instant): Qr = guarded {
        ensure(start.operation == Operation.START, Failure.CONTEXT_MISMATCH)
        val scope = start.prepared.context
        val value = status(successfulStatus, scope)
        ensure(value.text("state") == "OPEN" && value["recipient_platform"] == JsonNull && "claim_id" !in value,
            Failure.CONTEXT_MISMATCH)
        val secret = localSecret.copyOf()
        try {
            validateSecret(secret)
            ensure(hex(sha(secret)) == start.document.text("rendezvous_secret_sha256"), Failure.CONTEXT_MISMATCH)
            val expires = qrExpiry(value.text("expires_at"), now)
            Qr(scope.identity, scope.ceremonyId, scope.sourcePlatform, expires, value.text("expires_at"), secret)
        } finally { secret.fill(0) }
    }

    /**
     * No network selection/trust establishment: reverify the already verified discovery freshly.
     * QR is unsigned bearer material. Its source platform/expiry stay provisional until the
     * authenticated stored-ceremony status matches them in continuationContext; QR grants no
     * account authority. Never send its secret to an origin selected solely from this document.
     */
    fun readQr(raw: ByteArray, trusted: TrustedM5Identity, verifiedDiscovery: Discovery,
        recipientPlatform: Platform, now: Instant): Qr = guarded {
        validateIdentity(trusted)
        val result = DeviceContractV2Negotiation.discovery(verifiedDiscovery.canonicalEnvelope, trusted, now)
        val fresh = (result as? DeviceContractV2Negotiation.Result.Verified<Discovery>)?.value
            ?: reject(Failure.UNAVAILABLE)
        val value = parse(raw, MAX_QR_BYTES)
        ensure(value.keys == TRUST + setOf("format", "version", "contract_version", "schema_version",
            "ceremony_id", "rendezvous_secret", "expires_at", "source_platform"))
        version(value)
        ensure(value.text("format") == "autplay-self-device-pairing" && positive(value, "version") == 2L)
        matchTrust(value, trusted)
        val source = platform(value.text("source_platform"))
        ensure(fresh.advertises(source, Family.SELF) && fresh.advertises(recipientPlatform, Family.SELF), Failure.UNAVAILABLE)
        val expires = qrExpiry(value.text("expires_at"), now)
        val secret = value.text("rendezvous_secret").toByteArray(Charsets.UTF_8)
        try {
            validateSecret(secret)
            boundedCanonical(value, MAX_QR_BYTES)
            Qr(trusted, uuid(value.text("ceremony_id")), source, expires, value.text("expires_at"), secret,
                recipientPlatform, fresh.expiresAt)
        } finally { secret.fill(0) }
    }

    fun comparisonCode(start: WireRequest, claim: WireRequest): String = guarded {
        ensure(start.operation == Operation.START && claim.operation == Operation.CLAIM, Failure.CONTEXT_MISMATCH)
        sameCeremony(start.prepared.context, claim.prepared.context)
        sas(start.prepared.context, claim.requestSha256, claim.document.text("device_key_thumbprint_sha256"))
    }

    private fun prepareBody(operation: Operation, body: JsonObject, scope: Context): PreparedRequest {
        ensure(operation == scope.kind || (scope.kind == Operation.POLL && operation == Operation.EXCHANGE), Failure.CONTEXT_MISMATCH)
        ensure(body.keys == fields(operation))
        version(body)
        matchTrust(body, scope.identity)
        ensure(platform(body.text("platform")) == scope.platform && uuid(body.text("ceremony_id")) == scope.ceremonyId,
            Failure.CONTEXT_MISMATCH)
        time(body.text("requested_at"))
        for ((name, value) in body) {
            if (value == JsonNull) {
                ensure(operation == Operation.DECISION && name in CLAIM_REF + "comparison_code")
            } else if (name in setOf("schema_version", "expected_identity_epoch", "expected_revision")) {
                positive(body, name)
            } else {
                val text = body.text(name)
                ensure(text.codePointCount(0, text.length) in 1..2048)
                if (name.endsWith("_id")) uuid(text)
                if (name.endsWith("_sha256")) hash(text)
            }
        }
        if (operation == Operation.CLAIM) {
            display(body.text("device_name"), 120)
            display(body.text("app_version"), 32)
            val key = decode(body.text("device_public_key_spki_b64"), false, 91)
            p256(key)
            ensure(key.contentEquals(scope.keyCopy()) && hex(sha(key)) == body.text("device_key_thumbprint_sha256"),
                Failure.CONTEXT_MISMATCH)
        }
        if (operation == Operation.POLL || operation == Operation.EXCHANGE) {
            val claim = scope.originalClaim ?: reject(Failure.CONTEXT_MISMATCH)
            ensure(body["claim_id"] == claim.document["claim_id"] &&
                body.text("claim_request_sha256") == claim.requestSha256, Failure.CONTEXT_MISMATCH)
            if (operation == Operation.EXCHANGE) {
                val status = scope.status ?: reject(Failure.CONTEXT_MISMATCH)
                ensure(status.text("state") == "APPROVED" && body["approval_operation_id"] == status["approval_operation_id"] &&
                    body["confirmed_account_id"] == status["account_id"], Failure.CONTEXT_MISMATCH)
            }
        }
        if (operation == Operation.DECISION) {
            val status = scope.status ?: reject(Failure.CONTEXT_MISMATCH)
            ensure(body["claim_id"] == (status["claim_id"] ?: JsonNull) &&
                body["claim_request_sha256"] == (status["claim_request_sha256"] ?: JsonNull) &&
                positive(body, "expected_revision") == positive(status, "revision"), Failure.CONTEXT_MISMATCH)
            val action = body.text("action")
            ensure(action in setOf("APPROVE", "REJECT", "CANCEL"))
            if (action != "CANCEL") ensure(status.text("state") == "CLAIMED", Failure.CONTEXT_MISMATCH)
            if (action == "APPROVE") {
                ensure(body.text("comparison_code") == status.text("comparison_code"), Failure.CONTEXT_MISMATCH)
            } else ensure(body["comparison_code"] == JsonNull)
        }
        val canonical = boundedCanonical(body, MAX_BODY_BYTES)
        return PreparedRequest(operation, scope, body, canonical, sha(canonical))
    }

    private fun finish(prepared: PreparedRequest, signature: ByteArray?): WireRequest {
        val fields = prepared.body + ("request_sha256" to JsonPrimitive(prepared.requestSha256)) +
            (signature?.let { mapOf("device_signature_b64url" to JsonPrimitive(Base64.getUrlEncoder()
                .withoutPadding().encodeToString(it))) } ?: emptyMap())
        val document = JsonObject(fields)
        return WireRequest(prepared, document, boundedCanonical(document, MAX_BODY_BYTES), signature)
    }

    private fun status(raw: ByteArray, scope: Context): JsonObject {
        val value = parse(raw, 65_536)
        val required = setOf("contract_version", "schema_version", "ceremony_id", "state", "revision",
            "expires_at", "retry_after_seconds", "source_platform", "recipient_platform")
        val claimFields = setOf("claim_id", "claim_request_sha256", "device_name", "device_key_thumbprint_sha256", "comparison_code")
        val optional = claimFields + setOf("account_id", "account_label", "approval_operation_id", "device_id")
        ensure(value.keys.containsAll(required) && value.keys.all { it in required + optional })
        version(value)
        ensure(uuid(value.text("ceremony_id")) == scope.ceremonyId &&
            platform(value.text("source_platform")) == scope.sourcePlatform, Failure.CONTEXT_MISMATCH)
        positive(value, "revision")
        ensure(positive(value, "retry_after_seconds") == 2L)
        val expires = time(value.text("expires_at"))
        ensure(scope.expiresAt == null || scope.expiresAt == expires, Failure.CONTEXT_MISMATCH)
        val state = value.text("state")
        ensure(state in setOf("OPEN", "CLAIMED", "APPROVED", "EXCHANGED", "CANCELLED", "REJECTED", "EXPIRED"))
        val hasClaim = "claim_id" in value
        ensure(claimFields.all { (it in value) == hasClaim })
        if (hasClaim) {
            platform(value.text("recipient_platform"))
            uuid(value.text("claim_id")); hash(value.text("claim_request_sha256"))
            display(value.text("device_name"), 120)
            hash(value.text("device_key_thumbprint_sha256"))
            ensure(Regex("[0-9]{12}").matches(value.text("comparison_code")))
            ensure(value.text("comparison_code") == sas(scope, value.text("claim_request_sha256"),
                value.text("device_key_thumbprint_sha256")), Failure.CONTEXT_MISMATCH)
        } else ensure(value["recipient_platform"] == JsonNull)
        if (state in setOf("CLAIMED", "APPROVED", "EXCHANGED", "REJECTED")) ensure(hasClaim)
        if (state in setOf("APPROVED", "EXCHANGED")) ensure("approval_operation_id" in value)
        if (state == "EXCHANGED") ensure("device_id" in value)
        ensure(("account_id" in value) == ("account_label" in value))
        for (name in setOf("account_id", "approval_operation_id", "device_id")) if (name in value) uuid(value.text(name))
        if ("account_label" in value) ensure(value.text("account_label").codePointCount(0, value.text("account_label").length) in 1..240)
        boundedCanonical(value, 65_536)
        return value
    }

    private fun claimMatches(status: JsonObject, claim: WireRequest) {
        ensure(status["claim_id"] == claim.document["claim_id"] && status.text("claim_request_sha256") == claim.requestSha256 &&
            status.text("recipient_platform") == claim.platform.name &&
            status["device_key_thumbprint_sha256"] == claim.document["device_key_thumbprint_sha256"] &&
            status["device_name"] == claim.document["device_name"], Failure.CONTEXT_MISMATCH)
    }

    private fun sameCeremony(first: Context, second: Context) {
        ensure(first.identity.sameAs(second.identity) && first.ceremonyId == second.ceremonyId &&
            first.sourcePlatform == second.sourcePlatform &&
            (first.expiresAt == null || second.expiresAt == null || first.expiresAt == second.expiresAt), Failure.CONTEXT_MISMATCH)
    }

    private fun sas(scope: Context, claimHash: String, keyThumb: String): String {
        // Frozen mapping: do NOT add platform fields. Recipient platform is inside exact claim D;
        // source platform is separately bound by context/status, not a new SAS payload member.
        val payload = JsonObject(mapOf("server_instance_id" to JsonPrimitive(scope.identity.serverInstanceId),
            "identity_epoch" to JsonPrimitive(scope.identity.identityEpoch), "ceremony_id" to JsonPrimitive(scope.ceremonyId),
            "claim_request_sha256" to JsonPrimitive(hash(claimHash)), "key_thumbprint" to JsonPrimitive(hash(keyThumb))))
        val digest = sha(SAS_DOMAIN.toByteArray(Charsets.US_ASCII) + sha(canonical(payload)))
        return BigInteger(1, digest.copyOfRange(0, 8)).mod(BigInteger.TEN.pow(12)).toString().padStart(12, '0')
    }

    private fun verify(prepared: PreparedRequest, signature: ByteArray) {
        ensure(signature.size == 64, Failure.INVALID_SIGNATURE)
        val key = p256(prepared.publicKeySpkiCopy() ?: reject(Failure.CONTEXT_MISMATCH))
        fun integer(offset: Int): ByteArray {
            val value = BigInteger(1, signature.copyOfRange(offset, offset + 32))
            ensure(value.signum() > 0 && value < key.params.order, Failure.INVALID_SIGNATURE)
            return value.toByteArray() // Both valid high-S and low-S remain accepted.
        }
        val r = integer(0)
        val s = integer(32)
        val der = byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 2, r.size.toByte()) + r + byteArrayOf(2, s.size.toByte()) + s
        val valid = try {
            Signature.getInstance("SHA256withECDSA").run {
                initVerify(key)
                update((prepared.proofDomain ?: reject(Failure.CONTEXT_MISMATCH)).toByteArray(Charsets.US_ASCII))
                update(prepared.requestDigestCopy())
                verify(der)
            }
        } catch (_: java.security.GeneralSecurityException) { reject(Failure.INVALID_SIGNATURE) }
        ensure(valid, Failure.INVALID_SIGNATURE)
    }

    private fun validateIdentity(identity: TrustedM5Identity) {
        uuid(identity.serverInstanceId)
        ensure(identity.identityEpoch in 1..MAX_INTEGER)
        origin(identity.apiOrigin); origin(identity.streamOrigin)
        p256(identity.publicKeySpki)
        ensure(hash(identity.thumbprintSha256) == hex(sha(identity.publicKeySpki)), Failure.IDENTITY_MISMATCH)
    }

    private fun trustFields(identity: TrustedM5Identity): Map<String, JsonElement> = mapOf(
        "expected_server_instance_id" to JsonPrimitive(identity.serverInstanceId),
        "expected_identity_epoch" to JsonPrimitive(identity.identityEpoch),
        "expected_identity_thumbprint_sha256" to JsonPrimitive(identity.thumbprintSha256),
        "expected_api_origin" to JsonPrimitive(identity.apiOrigin), "expected_stream_origin" to JsonPrimitive(identity.streamOrigin))
    private fun matchTrust(value: JsonObject, identity: TrustedM5Identity) {
        ensure(trustFields(identity).all { (name, expected) -> value[name] == expected }, Failure.IDENTITY_MISMATCH)
        positive(value, "expected_identity_epoch")
    }
    private fun version(value: JsonObject) {
        ensure(value.text("contract_version") == "v2" && positive(value, "schema_version") == 2L)
    }
    private fun platform(value: String): Platform = Platform.entries.firstOrNull { it.name == value } ?: reject()
    private fun positive(value: JsonObject, name: String): Long {
        val token = value[name] as? JsonPrimitive ?: reject()
        ensure(!token.isString && Regex("[1-9][0-9]{0,15}").matches(token.content))
        return (token.content.toLongOrNull() ?: reject()).also { ensure(it in 1..MAX_INTEGER) }
    }
    private fun JsonObject.text(name: String): String = (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject()
    private fun uuid(value: String): String {
        ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(value) && UUID.fromString(value).toString() == value)
        return value
    }
    private fun hash(value: String): String { ensure(Regex("[0-9a-f]{64}").matches(value)); return value }
    private fun display(value: String, max: Int) {
        ensure(value.codePointCount(0, value.length) in 1..max && value.none { it.code < 32 || it.code == 127 })
    }
    private fun time(value: String): Instant {
        ensure(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z").matches(value))
        return LocalDateTime.parse(value.dropLast(1)).toInstant(ZoneOffset.UTC)
    }
    private fun qrExpiry(text: String, now: Instant): Instant = time(text).also {
        // Existing QR policy: fixed ceremony TTL900 + allowed clock skew120, not a fresh TTL.
        ensure(it > now && it <= now.plusSeconds(1020), Failure.EXPIRED)
    }
    private fun origin(value: String) {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 })
        val authority = OriginAuthorityV2.parse(value) ?: reject()
        ensure(authority.portText == null || authority.portText.length in 1..5)
        ensure(authority.port == null || authority.port in 0..65535)
        ensure(authority.isIpv6 || authority.hasDnsName(253))
        // Transport policy is deliberately not inferred here; release HTTPS/PKI remains mandatory.
    }
    private fun decode(value: String, url: Boolean, size: Int): ByteArray {
        ensure(value.length <= 256)
        val bytes = (if (url) Base64.getUrlDecoder() else Base64.getDecoder()).decode(value)
        val roundtrip = (if (url) Base64.getUrlEncoder().withoutPadding() else Base64.getEncoder()).encodeToString(bytes)
        ensure(bytes.size == size && roundtrip == value)
        return bytes
    }
    private fun validateSecret(value: ByteArray) {
        ensure(value.size == 43 && value.all { it.toInt() in 0..127 })
        val raw = Base64.getUrlDecoder().decode(value)
        try {
            ensure(raw.size == 32 && Base64.getUrlEncoder().withoutPadding().encode(raw).contentEquals(value))
        } finally { raw.fill(0) }
    }
    private fun p256(spki: ByteArray): ECPublicKey {
        val prefix = byteArrayOf(0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86.toByte(),
            0x48, 0xce.toByte(), 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86.toByte(), 0x48,
            0xce.toByte(), 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0, 4)
        ensure(spki.size == 91 && spki.copyOfRange(0, prefix.size).contentEquals(prefix))
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey ?: reject()
        val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val actual = key.params
        ensure(actual.curve == expected.curve && actual.generator == expected.generator && actual.order == expected.order &&
            actual.cofactor == expected.cofactor && key.encoded.contentEquals(spki))
        val p = (expected.curve.field as ECFieldFp).p
        val x = key.w.affineX
        val y = key.w.affineY
        ensure(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p &&
            y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x)).add(expected.curve.b).mod(p))
        return key
    }
    private fun canonical(value: JsonObject): ByteArray = JsonCanonicalizer(value.toString()).encodedString.toByteArray(Charsets.UTF_8)
    private fun boundedCanonical(value: JsonObject, max: Int): ByteArray = canonical(value).also { ensure(it.size <= max, Failure.TOO_LARGE) }
    private fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun ensure(condition: Boolean, failure: Failure = Failure.INVALID_DOCUMENT) { if (!condition) reject(failure) }
    private fun reject(failure: Failure = Failure.INVALID_DOCUMENT): Nothing = throw Rejected(failure)
    private inline fun <T> guarded(block: () -> T): T = try { block() }
        catch (error: Rejected) { throw error }
        catch (_: java.security.NoSuchAlgorithmException) { reject(Failure.UNAVAILABLE) }
        catch (_: Exception) { reject() }

    private val TRUST = setOf("expected_server_instance_id", "expected_identity_epoch", "expected_identity_thumbprint_sha256",
        "expected_api_origin", "expected_stream_origin")
    private val COMMON = TRUST + setOf("contract_version", "schema_version", "platform", "ceremony_id", "requested_at")
    private val CLAIM_REF = setOf("claim_id", "claim_request_sha256")
    private fun fields(operation: Operation): Set<String> = COMMON + when (operation) {
        Operation.START -> setOf("operation_id", "rendezvous_secret_sha256")
        Operation.CLAIM -> setOf("claim_id", "poll_secret_sha256", "device_public_key_spki_b64",
            "device_key_thumbprint_sha256", "device_name", "app_version")
        Operation.POLL -> CLAIM_REF
        Operation.DECISION -> CLAIM_REF + setOf("operation_id", "action", "expected_revision", "comparison_code")
        Operation.EXCHANGE -> CLAIM_REF + setOf("exchange_id", "binding_commit_id", "approval_operation_id",
            "confirmed_account_id", "next_refresh_token_sha256")
    }

    private fun parse(bytes: ByteArray, max: Int): JsonObject {
        ensure(bytes.size in 2..max, if (bytes.size > max) Failure.TOO_LARGE else Failure.INVALID_DOCUMENT)
        val raw = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes.copyOf())).toString()
        return FlatJson(raw).parse()
    }

    /** Flat lexical parser: duplicates after unescaping, nesting, coercion and bad Unicode fail. */
    private class FlatJson(private val raw: String) {
        private var cursor: Int = 0
        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(char: Char): Boolean {
            whitespace()
            return (cursor < raw.length && raw[cursor] == char).also { if (it) cursor++ }
        }
        fun parse(): JsonObject {
            ensure(take('{'))
            val members = linkedMapOf<String, JsonElement>()
            if (!take('}')) {
                do {
                    whitespace()
                    val name = quoted()
                    ensure(name !in members && take(':'))
                    whitespace()
                    ensure(cursor < raw.length)
                    members[name] = if (raw[cursor] == '"') JsonPrimitive(quoted()) else {
                        val start = cursor
                        while (cursor < raw.length && raw[cursor] !in " \t\r\n,}") cursor++
                        val token = raw.substring(start, cursor)
                        ensure(token == "null" || Regex("0|[1-9][0-9]{0,15}").matches(token))
                        if (token == "null") JsonNull else {
                            ensure((token.toLongOrNull() ?: reject()) <= MAX_INTEGER)
                            JsonPrimitive(token.toLong())
                        }
                    }
                } while (take(','))
                ensure(take('}'))
            }
            whitespace()
            ensure(cursor == raw.length)
            return JsonObject(members.toMap())
        }
        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"')
            val start = cursor++
            var closed = false
            while (cursor < raw.length) {
                val char = raw[cursor++]
                if (char == '"') { closed = true; break }
                ensure(char.code >= 32)
                if (char == '\\') {
                    ensure(cursor < raw.length)
                    val escape = raw[cursor++]
                    ensure(escape in "\"\\/bfnrtu")
                    if (escape == 'u') {
                        ensure(cursor + 4 <= raw.length && raw.substring(cursor, cursor + 4).all { it in "0123456789abcdefABCDEF" })
                        cursor += 4
                    }
                }
            }
            ensure(closed)
            val text = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            var index = 0
            while (index < text.length) {
                val char = text[index++]
                if (char.isHighSurrogate()) ensure(index < text.length && text[index++].isLowSurrogate())
                else ensure(!char.isLowSurrogate())
            }
            return text
        }
    }
}
