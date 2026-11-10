package app.autplay.application.devicecompatibility

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
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneOffset
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * Source-only CLIENT D1 preparation/proof boundary. No HTTP, response cache, authority grant,
 * key creation, negotiation advertisement, persistence, K/J custody or v1 fallback.
 * All context arguments come from the caller's already trusted M5/lifecycle/consent boundary,
 * never from the document being validated. Syntax and local possession are not server authority.
 * Callers still own HTTPS/PKI, gates, current trust/account/session, rates, nonce freshness,
 * expiry, one-in-flight/generation fences and server replay/response validation.
 *
 * Invitation, current refresh and incoming trusted challenge are credentials. Every body owner
 * and returned byte copy is transient; never put it in ordinary DB/settings/logs/receipts.
 * close() clears owned arrays best-effort. JVM strings, JCS intermediates, provider memory and
 * caller copies cannot be reliably erased. No Serializable/data-class body or response API.
 * A future separately approved custodian MUST capture the exact signed wire, original signature
 * and required retry secrets BEFORE a network effect. Never re-sign an existing pending command
 * or reconstruct its original from public evidence. This codec does not implement that custodian.
 */
object ProfileDeviceContractV2 {
    const val MAX_BODY_BYTES: Int = 16_384
    private const val MAX_INTEGER: Long = 9_007_199_254_740_991L

    enum class Operation(val proofDomain: String, internal val ownHash: Boolean = false) {
        ENROLLMENT_EXCHANGE("autplay:device-compatibility:enrollment:exchange:v2\n", true),
        ADMISSION_SUBMIT("autplay:device-compatibility:admission:submit:v2\n"),
        ADMISSION_POLL("autplay:device-compatibility:admission:poll:v2\n"),
        ADMISSION_RECOVER("autplay:device-compatibility:admission:recover:v2\n"),
        ADMISSION_EXCHANGE("autplay:device-compatibility:admission:exchange:v2\n"),
        TRUSTED_CHALLENGE("autplay:device-compatibility:trusted:challenge:v2\n"),
        TRUSTED_EXCHANGE("autplay:device-compatibility:trusted:exchange:v2\n"),
        SESSION_ROTATE("autplay:device-compatibility:session:rotate:v2\n", true);

        internal val signatureField: String get() = if (ownHash) "device_signature_b64url" else "proof_b64url"
    }

    enum class Failure { INVALID_DOCUMENT, TOO_LARGE, IDENTITY_MISMATCH, CONTEXT_MISMATCH,
        INVALID_SIGNATURE, CLOSED, ALREADY_SIGNED, EXPIRED, UNAVAILABLE }
    class Rejected internal constructor(val failure: Failure) :
        IllegalArgumentException("PROFILE_DEVICE_V2_$failure", null) {
        override fun toString(): String = "ProfileDeviceV2.Rejected($failure)"
    }

    /** Nonsecret scope; full session/account/binding tuple is preserved even off-wire. */
    class Context internal constructor(
        internal val identity: TrustedM5Identity, val platform: Platform,
        internal val operation: Operation, spki: ByteArray,
        expected: Map<String, JsonElement>, internal val credential: Pair<String, String>? = null,
        parent: ByteArray? = null, internal val session: List<String> = emptyList(),
        internal val expiresAt: Instant? = null,
    ) {
        private val key: ByteArray = spki.copyOf()
        internal val expected: Map<String, JsonElement> = expected.toMap()
        private val parentDigest: ByteArray? = parent?.copyOf()
        internal fun keyCopy(): ByteArray = key.copyOf()
        internal fun matches(other: Context): Boolean = identity.sameAs(other.identity) &&
            platform == other.platform && operation == other.operation && key.contentEquals(other.key) &&
            expected == other.expected && credential == other.credential && session == other.session &&
            expiresAt == other.expiresAt && when {
                parentDigest == null -> other.parentDigest == null
                other.parentDigest == null -> false
                else -> parentDigest.contentEquals(other.parentDigest)
            }
        override fun toString(): String = "ProfileDeviceV2.Context(<redacted>)"
    }

    class RawBody internal constructor(input: ByteArray) : AutoCloseable {
        private val bytes: ByteArray = input.copyOf()
        private var closed: Boolean = false
        @Synchronized fun transientUtf8Copy(): ByteArray { ensure(!closed, Failure.CLOSED); return bytes.copyOf() }
        @Synchronized override fun close() { bytes.fill(0); closed = true }
        override fun toString(): String = "ProfileDeviceV2.RawBody(<redacted>)"
    }

    class PreparedRequest internal constructor(
        internal val context: Context, unsigned: ByteArray, digest: ByteArray,
    ) : AutoCloseable {
        private val body: ByteArray = unsigned.copyOf()
        private val hash: ByteArray = digest.copyOf()
        private val key: ByteArray = context.keyCopy()
        private val message: ByteArray = context.operation.proofDomain.toByteArray(Charsets.US_ASCII) + hash
        private var closed: Boolean = false
        private var consumed: Boolean = false
        val operation: Operation get() = context.operation
        val platform: Platform get() = context.platform
        val proofDomain: String get() = operation.proofDomain
        @Synchronized fun transientUnsignedCopy(): ByteArray { open(); return body.copyOf() }
        @Synchronized fun signingMessageCopy(): ByteArray { open(); return message.copyOf() }
        @Synchronized fun requestDigestCopy(): ByteArray { open(); return hash.copyOf() }
        @Synchronized fun publicKeySpkiCopy(): ByteArray { open(); return key.copyOf() }
        val keyThumbprintSha256: String
            @Synchronized get() { open(); return hex(sha(key)) }
        @Synchronized internal fun consume(signer: (Operation, ByteArray) -> ByteArray): SignedCommand {
            open(); ensure(!consumed, Failure.ALREADY_SIGNED)
            consumed = true // Even a failed callback must not silently sign this command again.
            val copy = hash.copyOf()
            try {
                val produced = signer(operation, copy)
                ensure(produced.size == 64, Failure.INVALID_SIGNATURE)
                val proof = produced.copyOf()
                try {
                    verify(key, message, proof)
                    val unsigned = parse(body)
                    val fields = unsigned + (operation.signatureField to JsonPrimitive(url(proof))) +
                        if (operation.ownHash) mapOf("request_sha256" to JsonPrimitive(hex(hash))) else emptyMap()
                    val wire = canonical(JsonObject(fields))
                    try { return SignedCommand(context, wire, hash, proof) } finally { wire.fill(0) }
                } finally { proof.fill(0) }
            } finally { copy.fill(0) }
        }
        private fun open() { ensure(!closed, Failure.CLOSED) }
        @Synchronized override fun close() { body.fill(0); hash.fill(0); key.fill(0); message.fill(0); closed = true }
        override fun toString(): String = "ProfileDeviceV2.PreparedRequest(<redacted>)"
    }

    /** Exact one-signature wire owner, not a receipt, credential grant or approved pending store. */
    class SignedCommand internal constructor(
        internal val context: Context, wire: ByteArray, digest: ByteArray, signature: ByteArray,
    ) : AutoCloseable {
        private val bytes: ByteArray = wire.copyOf()
        private val hash: ByteArray = digest.copyOf()
        private val proof: ByteArray = signature.copyOf()
        private var closed: Boolean = false
        val operation: Operation get() = context.operation
        val platform: Platform get() = context.platform
        @Synchronized fun transientWireCopy(): ByteArray { open(); return bytes.copyOf() }
        @Synchronized fun requestDigestCopy(): ByteArray { open(); return hash.copyOf() }
        @Synchronized fun signatureCopy(): ByteArray { open(); return proof.copyOf() }
        @Synchronized internal fun parentSnapshot(): Pair<Context, ByteArray> {
            open(); return context to sha(bytes)
        }
        private fun open() { ensure(!closed, Failure.CLOSED) }
        @Synchronized override fun close() { bytes.fill(0); hash.fill(0); proof.fill(0); closed = true }
        override fun toString(): String = "ProfileDeviceV2.SignedCommand(<redacted>)"
    }

    fun rawBody(bytes: ByteArray): RawBody = guarded {
        ensure(bytes.size <= MAX_BODY_BYTES, Failure.TOO_LARGE)
        RawBody(bytes)
    }

    fun enrollmentContext(
        trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        invitationId: String, expectedUserId: String, exchangeId: String, bindingCommitId: String,
        invitationSecretSha256: String, nextRefreshTokenSha256: String,
    ): Context = guarded {
        scope(trusted, platform, Operation.ENROLLMENT_EXCHANGE, deviceSpki,
            ids("invitation_id" to invitationId, "expected_user_id" to expectedUserId,
                "exchange_id" to exchangeId, "binding_commit_id" to bindingCommitId) +
                hashes("next_refresh_token_sha256" to nextRefreshTokenSha256),
            "invitation_secret" to hash(invitationSecretSha256))
    }

    fun admissionSubmitContext(
        trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray, requestId: String,
    ): Context = guarded { scope(trusted, platform, Operation.ADMISSION_SUBMIT, deviceSpki, ids("request_id" to requestId)) }

    fun admissionPollContext(
        originalSubmit: SignedCommand, trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
    ): Context = guarded { admissionParent(originalSubmit, trusted, platform, deviceSpki, Operation.ADMISSION_POLL) }

    fun admissionRecoverContext(
        originalSubmit: SignedCommand, trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
    ): Context = guarded { admissionParent(originalSubmit, trusted, platform, deviceSpki, Operation.ADMISSION_RECOVER) }

    /** Account/commit and poll hash must come from authenticated approval plus local consent. */
    fun admissionExchangeContext(
        originalSubmit: SignedCommand, trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        approvedAccountId: String, exchangeId: String, bindingCommitId: String,
        pollBearerSha256: String, nextRefreshTokenSha256: String,
    ): Context = guarded {
        admissionParent(originalSubmit, trusted, platform, deviceSpki, Operation.ADMISSION_EXCHANGE,
            ids("approved_account_id" to approvedAccountId, "exchange_id" to exchangeId,
                "binding_commit_id" to bindingCommitId) + hashes("poll_bearer_sha256" to pollBearerSha256,
                "next_refresh_token_sha256" to nextRefreshTokenSha256))
    }

    /** Caller must resolve approved/known-key platform evidence; no ANDROID default. */
    fun trustedChallengeContext(
        trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        accountId: String, challengeRequestId: String,
    ): Context = guarded {
        scope(trusted, platform, Operation.TRUSTED_CHALLENGE, deviceSpki,
            ids("account_id" to accountId, "challenge_request_id" to challengeRequestId))
    }

    /** No response parser/cache: trusted challenge credential is transient caller input only. */
    fun trustedExchangeContext(
        originalChallenge: SignedCommand, trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        challengeId: String, challengeCredential: ByteArray, exchangeId: String, bindingCommitId: String,
        nextRefreshTokenSha256: String, expiresAt: Instant, now: Instant,
    ): Context = guarded {
        val (parent, evidence) = parent(originalChallenge, Operation.TRUSTED_CHALLENGE, trusted, platform, deviceSpki)
        ensure(expiresAt > now && expiresAt <= now.plusSeconds(300), Failure.EXPIRED)
        ensure(challengeCredential.size == 22)
        val secret = challengeCredential.copyOf()
        try {
            ensure(secret.all { it.toInt() in 0..127 })
            decode(secret.toString(Charsets.US_ASCII), true, 16).fill(0)
            scope(trusted, platform, Operation.TRUSTED_EXCHANGE, deviceSpki,
                mapOf("account_id" to parent.expected.getValue("account_id")) +
                    ids("challenge_id" to challengeId, "exchange_id" to exchangeId, "binding_commit_id" to bindingCommitId) +
                    hashes("next_refresh_token_sha256" to nextRefreshTokenSha256),
                "challenge" to hex(sha(secret)), evidence, expiresAt = expiresAt)
        } finally { secret.fill(0); evidence.fill(0) }
    }

    /** Locked current M5 account/device/session/family/binding, never the incoming body's scope. */
    fun rotationContext(
        trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        accountId: String, deviceId: String, parentSessionId: String, sessionFamilyId: String,
        bindingCommitId: String, currentGeneration: Long, rotationId: String,
        currentRefreshTokenSha256: String, nextRefreshTokenSha256: String,
        absoluteExpiresAt: Instant,
    ): Context = guarded {
        ensure(currentGeneration in 0..MAX_INTEGER)
        scope(trusted, platform, Operation.SESSION_ROTATE, deviceSpki,
            ids("device_id" to deviceId, "parent_session_id" to parentSessionId, "rotation_id" to rotationId) +
                hashes("next_refresh_token_sha256" to nextRefreshTokenSha256) +
                ("current_generation" to JsonPrimitive(currentGeneration)),
            "current_refresh_token" to hash(currentRefreshTokenSha256),
            session = listOf(uuid(accountId), uuid(deviceId), uuid(parentSessionId), uuid(sessionFamilyId), uuid(bindingCommitId)),
            expiresAt = absoluteExpiresAt)
    }

    /** Exact unsigned model, without proof or OWN hash; reference hashes must already be present. */
    fun prepare(operation: Operation, rawUnsigned: RawBody, context: Context, now: Instant): PreparedRequest = guarded {
        ensure(context.operation == operation, Failure.CONTEXT_MISMATCH)
        context.expiresAt?.let { ensure(it > now, Failure.EXPIRED) }
        val raw = rawUnsigned.transientUtf8Copy()
        try {
            val body = parse(raw)
            validate(body, context)
            val canonical = canonical(body)
            try {
                val digest = sha(canonical)
                try { PreparedRequest(context, canonical, digest) } finally { digest.fill(0) }
            } finally { canonical.fill(0) }
        } finally { raw.fill(0) }
    }

    /**
     * Callback receives a CLOSED operation and RAW D; MESSAGE is enum.domain ASCII || D.
     * The callback owns its returned signature buffer and must clear it after sign() returns.
     */
    fun sign(prepared: PreparedRequest, signer: (Operation, ByteArray) -> ByteArray): SignedCommand =
        guarded { prepared.consume(signer) }

    /** For an existing message signer; verifies against the EXACT prepared P-256 key/message. */
    fun complete(prepared: PreparedRequest, signatureP1363: ByteArray): SignedCommand =
        guarded { prepared.consume { _, _ -> signatureP1363 } }

    /**
     * Validate a supplied retry against an existing exact original. No re-sign/reconstruction,
     * projection restore, time refresh or server-authority inference. Closed owners fail closed.
     * Custody restoration itself is deliberately not implemented or authorized here.
     */
    fun requireExactOriginal(original: SignedCommand, candidate: RawBody, context: Context): Unit = guarded {
        ensure(original.context.matches(context), Failure.CONTEXT_MISMATCH)
        val expected = original.transientWireCopy()
        try {
            val raw = candidate.transientUtf8Copy()
            try {
                ensure(expected.contentEquals(raw), Failure.CONTEXT_MISMATCH)
                val document = parse(raw)
                val operation = context.operation
                val omitted = setOf(operation.signatureField) + if (operation.ownHash) setOf("request_sha256") else emptySet()
                val unsigned = JsonObject(document.filterKeys { it !in omitted })
                validate(unsigned, context)
                val canonical = canonical(unsigned)
                val digest = try { sha(canonical) } finally { canonical.fill(0) }
                try {
                    val signature = decode(document.text(operation.signatureField), true, 64)
                    try {
                        if (operation.ownHash) ensure(document.text("request_sha256") == hex(digest))
                        verify(context.keyCopy(), operation.proofDomain.toByteArray(Charsets.US_ASCII) + digest, signature)
                    } finally { signature.fill(0) }
                } finally { digest.fill(0) }
            } finally { raw.fill(0) }
        } finally { expected.fill(0) }
    }

    private fun scope(
        identity: TrustedM5Identity, platform: Platform, operation: Operation, key: ByteArray,
        expected: Map<String, JsonElement>, credential: Pair<String, String>? = null,
        parent: ByteArray? = null, session: List<String> = emptyList(), expiresAt: Instant? = null,
    ): Context {
        uuid(identity.serverInstanceId); ensure(identity.identityEpoch in 1..MAX_INTEGER)
        origin(identity.apiOrigin); origin(identity.streamOrigin); p256(identity.publicKeySpki)
        ensure(hash(identity.thumbprintSha256) == hex(sha(identity.publicKeySpki)), Failure.IDENTITY_MISMATCH)
        ensure(key.size == 91)
        val spki = key.copyOf()
        try { p256(spki); return Context(identity, platform, operation, spki, expected, credential, parent, session.toList(), expiresAt) }
        finally { spki.fill(0) }
    }
    private fun parent(
        command: SignedCommand, required: Operation, identity: TrustedM5Identity, platform: Platform, key: ByteArray,
    ): Pair<Context, ByteArray> {
        ensure(command.operation == required, Failure.CONTEXT_MISMATCH)
        val (context, digest) = command.parentSnapshot()
        ensure(context.identity.sameAs(identity) && context.platform == platform && context.keyCopy().contentEquals(key),
            Failure.CONTEXT_MISMATCH)
        return context to digest
    }
    private fun admissionParent(
        original: SignedCommand, identity: TrustedM5Identity, platform: Platform, key: ByteArray,
        operation: Operation, extra: Map<String, JsonElement> = emptyMap(),
    ): Context {
        val (parent, evidence) = parent(original, Operation.ADMISSION_SUBMIT, identity, platform, key)
        val digest = original.requestDigestCopy()
        try {
            val reference = if (operation == Operation.ADMISSION_POLL) emptyMap() else
                mapOf("request_sha256" to JsonPrimitive(hex(digest)))
            return scope(identity, platform, operation, key,
                mapOf("request_id" to parent.expected.getValue("request_id")) + reference + extra, parent = evidence)
        } finally { evidence.fill(0); digest.fill(0) }
    }

    private fun validate(body: JsonObject, context: Context) {
        val spec = fields(context.operation)
        val optional = if (context.operation == Operation.ADMISSION_SUBMIT) setOf("device_model_hint") else emptySet()
        ensure(body.keys.containsAll(spec.keys) && (body.keys - spec.keys).all { it in optional })
        body.forEach { (name, value) -> validateField(value, spec[name] ?: "hint") }
        ensure(body.text("contract_version") == "v2" && integer(body["schema_version"]) == 2L)
        ensure(body.text("platform") == context.platform.name, Failure.CONTEXT_MISMATCH)
        val trust = mapOf("expected_server_instance_id" to JsonPrimitive(context.identity.serverInstanceId),
            "expected_identity_epoch" to JsonPrimitive(context.identity.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(context.identity.thumbprintSha256),
            "expected_api_origin" to JsonPrimitive(context.identity.apiOrigin),
            "expected_stream_origin" to JsonPrimitive(context.identity.streamOrigin))
        ensure(trust.all { (name, value) -> body[name] == value }, Failure.IDENTITY_MISMATCH)
        ensure(context.expected.all { (name, value) -> body[name] == value }, Failure.CONTEXT_MISMATCH)
        context.credential?.let { (name, expected) ->
            ensure(hex(sha(body.text(name).toByteArray(Charsets.US_ASCII))) == expected, Failure.CONTEXT_MISMATCH)
        }
        val key = context.keyCopy()
        try {
            body["device_key_thumbprint_sha256"]?.let {
                ensure(body.text("device_key_thumbprint_sha256") == hex(sha(key)), Failure.CONTEXT_MISMATCH)
            }
            body["device_public_key_spki_b64"]?.let {
                ensure(decode(body.text("device_public_key_spki_b64"), false, 91).contentEquals(key), Failure.CONTEXT_MISMATCH)
            }
            body["device_public_key_jwk"]?.let { ensure(jwk(it).contentEquals(key), Failure.CONTEXT_MISMATCH) }
        } finally { key.fill(0) }
    }
    private fun validateField(value: JsonElement, kind: String) {
        if (kind in setOf("positive", "natural", "one", "two")) {
            val number = integer(value)
            ensure(number >= if (kind == "natural") 0 else 1)
            if (kind == "one" || kind == "two") ensure(number == if (kind == "one") 1L else 2L)
            return
        }
        if (kind == "jwk") { jwk(value); return }
        val text = (value as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject()
        when (kind) {
            "v2" -> ensure(text == "v2")
            "platform" -> ensure(text in Platform.entries.map { it.name })
            "algorithm" -> ensure(text == "ES256-P1363")
            "uuid" -> uuid(text)
            "hash" -> hash(text)
            "origin" -> origin(text)
            "time" -> {
                ensure(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z").matches(text))
                LocalDateTime.parse(text.dropLast(1)).toInstant(ZoneOffset.UTC)
            }
            "name", "app", "hint" -> {
                val max = when (kind) { "name" -> 120; "app" -> 32; else -> 96 }
                ensure(text.codePointCount(0, text.length) in 1..max && text.none { it.code < 32 || it.code == 127 })
            }
            "spki" -> p256(decode(text, false, 91))
            "b16", "b32" -> decode(text, true, if (kind == "b16") 16 else 32).fill(0)
            else -> reject()
        }
    }
    private fun jwk(value: JsonElement): ByteArray {
        val body = value as? JsonObject ?: reject()
        ensure(body.keys == setOf("kty", "crv", "x", "y") && body.text("kty") == "EC" && body.text("crv") == "P-256")
        val spki = SPKI_PREFIX + decode(body.text("x"), true, 32) + decode(body.text("y"), true, 32)
        p256(spki)
        return spki
    }
    private fun p256(spki: ByteArray): ECPublicKey {
        ensure(spki.size == 91 && spki.copyOfRange(0, SPKI_PREFIX.size).contentEquals(SPKI_PREFIX))
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey ?: reject()
        val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val actual = key.params
        ensure(actual.curve == expected.curve && actual.generator == expected.generator && actual.order == expected.order &&
            actual.cofactor == expected.cofactor && key.encoded.contentEquals(spki))
        val p = (expected.curve.field as ECFieldFp).p
        val x = key.w.affineX; val y = key.w.affineY
        ensure(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p &&
            y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x)).add(expected.curve.b).mod(p))
        return key
    }
    private fun verify(spki: ByteArray, message: ByteArray, signature: ByteArray) {
        ensure(signature.size == 64, Failure.INVALID_SIGNATURE)
        val key = p256(spki)
        val r = BigInteger(1, signature.copyOfRange(0, 32)); val s = BigInteger(1, signature.copyOfRange(32, 64))
        ensure(r.signum() > 0 && r < key.params.order && s.signum() > 0 && s < key.params.order, Failure.INVALID_SIGNATURE)
        val rb = r.toByteArray(); val sb = s.toByteArray()
        val der = byteArrayOf(0x30, (4 + rb.size + sb.size).toByte(), 2, rb.size.toByte()) + rb + byteArrayOf(2, sb.size.toByte()) + sb
        // Existing provider convention: hash the complete domain+raw-D MESSAGE once. No low-S rule.
        val valid = Signature.getInstance("SHA256withECDSA").run { initVerify(key); update(message); verify(der) }
        ensure(valid, Failure.INVALID_SIGNATURE)
    }

    private fun origin(value: String) {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 } && value.none { it in "?#@\\%" })
        val authority = OriginAuthorityV2.parse(value) ?: reject()
        val host = authority.host
        val canonicalHost = if (host.startsWith("[")) "[${ipv6(host.drop(1).dropLast(1))}]" else {
            ensure(host == host.lowercase() && host.length <= 253)
            if (host.all { it in "0123456789." }) {
                val parts = host.split('.')
                ensure(parts.size == 4 && parts.all { it.toIntOrNull() in 0..255 && it.toInt().toString() == it })
            } else ensure(host.removeSuffix(".").split('.').all {
                Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?").matches(it)
            })
            host
        }
        ensure(authority.port == null || authority.port in 1..65535)
        ensure(value == authority.scheme + "://" + canonicalHost +
            (if (authority.port == null) "" else ":${authority.port}"))
    }
    private fun ipv6(value: String): String {
        ensure(value.count { it == ':' } >= 2 && value.all { it in "0123456789abcdef:" })
        val halves = value.split("::")
        ensure(halves.size <= 2)
        fun groups(text: String): List<Int> = if (text.isEmpty()) emptyList() else text.split(':').map {
            ensure(it.length in 1..4); it.toInt(16)
        }
        val left = groups(halves[0]); val right = if (halves.size == 2) groups(halves[1]) else emptyList()
        val missing = 8 - left.size - right.size
        ensure(if (halves.size == 2) missing >= 1 else missing == 0)
        val all = left + List(missing) { 0 } + right
        var bestStart = -1; var bestSize = 1; var cursor = 0
        while (cursor < 8) {
            if (all[cursor] != 0) { cursor++; continue }
            val start = cursor
            while (cursor < 8 && all[cursor] == 0) cursor++
            if (cursor - start > bestSize) { bestStart = start; bestSize = cursor - start }
        }
        if (bestStart < 0) return all.joinToString(":") { it.toString(16) }
        return all.take(bestStart).joinToString(":") { it.toString(16) } + "::" +
            all.drop(bestStart + bestSize).joinToString(":") { it.toString(16) }
    }
    private fun integer(value: JsonElement?): Long {
        val token = value as? JsonPrimitive ?: reject()
        ensure(!token.isString && Regex("0|[1-9][0-9]{0,15}").matches(token.content))
        return (token.content.toLongOrNull() ?: reject()).also { ensure(it in 0..MAX_INTEGER) }
    }
    private fun JsonObject.text(name: String): String = (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject()
    private fun uuid(text: String): String { ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(text)); return text }
    private fun hash(text: String): String { ensure(Regex("[0-9a-f]{64}").matches(text)); return text }
    private fun ids(vararg values: Pair<String, String>): Map<String, JsonElement> = values.associate { it.first to JsonPrimitive(uuid(it.second)) }
    private fun hashes(vararg values: Pair<String, String>): Map<String, JsonElement> = values.associate { it.first to JsonPrimitive(hash(it.second)) }
    private fun decode(text: String, isUrl: Boolean, size: Int): ByteArray {
        ensure(text.length <= 256)
        val decoded = (if (isUrl) Base64.getUrlDecoder() else Base64.getDecoder()).decode(text)
        try {
            ensure(decoded.size == size && (if (isUrl) url(decoded) else Base64.getEncoder().encodeToString(decoded)) == text)
            return decoded
        } catch (error: Rejected) { decoded.fill(0); throw error }
    }
    private fun url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    private fun canonical(body: JsonObject): ByteArray {
        val bytes = JsonCanonicalizer(body.toString()).encodedString.toByteArray(Charsets.UTF_8)
        if (bytes.size > MAX_BODY_BYTES) { bytes.fill(0); reject(Failure.TOO_LARGE) }
        return bytes
    }
    private fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun ensure(condition: Boolean, failure: Failure = Failure.INVALID_DOCUMENT) { if (!condition) reject(failure) }
    private fun reject(failure: Failure = Failure.INVALID_DOCUMENT): Nothing = throw Rejected(failure)
    private inline fun <T> guarded(block: () -> T): T = try { block() }
        catch (error: Rejected) { throw error }
        catch (_: java.security.NoSuchAlgorithmException) { reject(Failure.UNAVAILABLE) }
        catch (_: Exception) { reject() }

    private val SPKI_PREFIX = byteArrayOf(0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86.toByte(),
        0x48, 0xce.toByte(), 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86.toByte(), 0x48,
        0xce.toByte(), 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0, 4)
    private val COMMON = mapOf("contract_version" to "v2", "schema_version" to "two", "platform" to "platform",
        "expected_server_instance_id" to "uuid", "expected_identity_epoch" to "positive",
        "expected_identity_thumbprint_sha256" to "hash", "expected_api_origin" to "origin", "expected_stream_origin" to "origin")
    private val KEY = mapOf("device_key_thumbprint_sha256" to "hash", "device_public_key_jwk" to "jwk")
    private fun fields(operation: Operation): Map<String, String> = COMMON + when (operation) {
        Operation.ENROLLMENT_EXCHANGE -> mapOf("device_public_key_spki_b64" to "spki", "device_key_thumbprint_sha256" to "hash",
            "device_name" to "name", "app_version" to "app", "exchange_id" to "uuid", "binding_commit_id" to "uuid",
            "invitation_id" to "uuid", "invitation_secret" to "b32", "expected_user_id" to "uuid",
            "next_refresh_token_sha256" to "hash", "client_nonce_b64url" to "b16", "signature_algorithm" to "algorithm")
        Operation.ADMISSION_SUBMIT -> KEY + mapOf("request_id" to "uuid", "client_nonce_b64url" to "b32", "api_major" to "one",
            "nickname" to "name", "app_version" to "app", "requested_at" to "time")
        Operation.ADMISSION_POLL -> mapOf("request_id" to "uuid", "device_key_thumbprint_sha256" to "hash", "client_nonce_b64url" to "b32")
        Operation.ADMISSION_RECOVER -> mapOf("request_id" to "uuid", "request_sha256" to "hash",
            "device_key_thumbprint_sha256" to "hash", "recovery_nonce_b64url" to "b32")
        Operation.ADMISSION_EXCHANGE -> KEY + mapOf("request_id" to "uuid", "request_sha256" to "hash", "exchange_id" to "uuid",
            "binding_commit_id" to "uuid", "poll_bearer_sha256" to "hash", "approved_account_id" to "uuid",
            "client_nonce_b64url" to "b32", "next_refresh_token_sha256" to "hash")
        Operation.TRUSTED_CHALLENGE -> KEY + mapOf("challenge_request_id" to "uuid", "account_id" to "uuid", "client_nonce_b64url" to "b32")
        Operation.TRUSTED_EXCHANGE -> KEY + mapOf("challenge_id" to "uuid", "challenge" to "b16", "exchange_id" to "uuid",
            "binding_commit_id" to "uuid", "account_id" to "uuid", "next_refresh_token_sha256" to "hash", "client_nonce_b64url" to "b32")
        Operation.SESSION_ROTATE -> mapOf("rotation_id" to "uuid", "device_id" to "uuid", "parent_session_id" to "uuid",
            "current_generation" to "natural", "current_refresh_token" to "b32", "next_refresh_token_sha256" to "hash",
            "signature_algorithm" to "algorithm")
    }

    private fun parse(bytes: ByteArray): JsonObject {
        ensure(bytes.size in 2..MAX_BODY_BYTES, if (bytes.size > MAX_BODY_BYTES) Failure.TOO_LARGE else Failure.INVALID_DOCUMENT)
        val raw = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        return StrictJson(raw).parse()
    }
    /** Only objects, strings and bounded lexical integers; duplicate unescaped keys at ALL depths. */
    private class StrictJson(private val raw: String) {
        private var cursor = 0
        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(char: Char): Boolean { whitespace(); return (cursor < raw.length && raw[cursor] == char).also { if (it) cursor++ } }
        fun parse(): JsonObject { val value = obj(0); whitespace(); ensure(cursor == raw.length); return value }
        private fun obj(depth: Int): JsonObject {
            ensure(depth <= 1 && take('{'))
            val members = linkedMapOf<String, JsonElement>()
            if (!take('}')) {
                do {
                    whitespace(); val name = quoted(); ensure(name !in members && take(':')); whitespace()
                    ensure(cursor < raw.length)
                    members[name] = when (raw[cursor]) {
                        '{' -> obj(depth + 1)
                        '"' -> JsonPrimitive(quoted())
                        else -> {
                            val start = cursor
                            while (cursor < raw.length && raw[cursor] !in " \t\r\n,}") cursor++
                            val token = raw.substring(start, cursor)
                            ensure(Regex("0|[1-9][0-9]{0,15}").matches(token))
                            val number = token.toLongOrNull() ?: reject(); ensure(number <= MAX_INTEGER)
                            JsonPrimitive(number)
                        }
                    }
                } while (take(','))
                ensure(take('}'))
            }
            return JsonObject(members.toMap())
        }
        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"')
            val start = cursor++; var closed = false
            while (cursor < raw.length) {
                val char = raw[cursor++]
                if (char == '"') { closed = true; break }
                ensure(char.code >= 32)
                if (char == '\\') {
                    ensure(cursor < raw.length); val escape = raw[cursor++]; ensure(escape in "\"\\/bfnrtu")
                    if (escape == 'u') {
                        ensure(cursor + 4 <= raw.length && raw.substring(cursor, cursor + 4).all { it in "0123456789abcdefABCDEF" }); cursor += 4
                    }
                }
            }
            ensure(closed)
            val text = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            var index = 0
            while (index < text.length) {
                val char = text[index++]
                if (char.isHighSurrogate()) ensure(index < text.length && text[index++].isLowSurrogate()) else ensure(!char.isLowSurrogate())
            }
            return text
        }
    }
}
