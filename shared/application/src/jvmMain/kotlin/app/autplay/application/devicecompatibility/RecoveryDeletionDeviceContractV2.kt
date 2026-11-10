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
import java.time.LocalDateTime
import java.util.Base64
import java.util.Collections
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * CLIENT D1 source prerequisite, UNVERIFIED. Five nonsecret signed BODY models, nine routes.
 * No controller, response/terminal-state interpretation, HTTP, custody, persistence or key creation.
 * Inputs must come from already trusted M5/account/consent evidence, never QR/recovery-file fields.
 * Server-bound accountId is not a portable ROOT accountId. Callers resolve that mapping explicitly.
 *
 * Proof possession is not current server authority. Future approved K/controller must revalidate
 * bearer/device/family, identity, account/root/code/authority generations, lifecycle, gates, quotas,
 * freshness for first effects, parent deletion/revision/deadline and live outcome result bindings.
 * Fresh deletion REQUEST additionally requires an active own bearer at the CALLER. Historical
 * receipt/resolve requires neither a fresh bearer nor one-day/request freshness in this codec.
 * Request-resolve cutover/time/coverage/ATTEMPTED/SEALED and authoritative NOT_ACCEPTED are server
 * decisions; a verified candidate cannot clear pending state or prove a negative result.
 *
 * Raw recovery code and chosen-next-refresh headers are separate credential boundaries. They are
 * absent here. Preserve existing server/account-prefixed code verifier and normalization; a generic
 * SHA256(code) is not a replacement. Recovery file1/4096 and portable export2/65536 remain unchanged.
 * K/J/grants/media are unapproved. Future approved custody must retain FULL original wire/proof and
 * required retry credentials before any effect. Ordinary DB/settings/logs or encrypted fallback are
 * not authorized. close() clears owned arrays best-effort only; JVM/provider/caller copies and String
 * intermediates cannot be securely erased. Public byte copies are caller-owned; toString is redacted.
 */
object RecoveryDeletionDeviceContractV2 {
    const val MAX_BODY_BYTES: Int = 8192
    private const val MAX_INTEGER: Long = 9_007_199_254_740_991L

    enum class Role { CURRENT_RECIPIENT, CURRENT_AUTHENTICATED_ACTOR, HISTORICAL_READ_ORIGINAL }
    enum class BodyKind(val proofDomain: String, val signingRole: Role) {
        RECOVERY_PREVIEW("autplay:device-compatibility:recovery:preview:v2\n", Role.CURRENT_RECIPIENT),
        RECOVERY_COMMIT("autplay:device-compatibility:recovery:recover:v2\n", Role.CURRENT_RECIPIENT),
        DELETION_REQUEST("autplay:device-compatibility:deletion:request:v2\n", Role.CURRENT_AUTHENTICATED_ACTOR),
        DELETION_PREVIEW("autplay:device-compatibility:deletion:preview:v2\n", Role.CURRENT_RECIPIENT),
        DELETION_CANCEL("autplay:device-compatibility:deletion:cancel:v2\n", Role.CURRENT_RECIPIENT),
    }
    enum class Route(val bodyKind: BodyKind, val suffix: String, val originalOnly: Boolean = false) {
        RECOVERY_PREVIEW(BodyKind.RECOVERY_PREVIEW, "/recovery/preview"),
        RECOVERY_COMMIT(BodyKind.RECOVERY_COMMIT, "/recovery/commit"),
        RECOVERY_OUTCOME(BodyKind.RECOVERY_COMMIT, "/recovery/outcome", true),
        DELETION_REQUEST(BodyKind.DELETION_REQUEST, "/deletion/requests"),
        DELETION_REQUEST_RECEIPT(BodyKind.DELETION_REQUEST, "/deletion/request-receipt", true),
        DELETION_REQUEST_RESOLVE(BodyKind.DELETION_REQUEST, "/deletion/request-resolve", true),
        DELETION_PREVIEW(BodyKind.DELETION_PREVIEW, "/deletion/cancel/preview"),
        DELETION_CANCEL(BodyKind.DELETION_CANCEL, "/deletion/cancel/commit"),
        DELETION_CANCEL_OUTCOME(BodyKind.DELETION_CANCEL, "/deletion/cancel/outcome", true);

        val path: String get() = "/api/v1/device-compatibility/v2" + suffix
        val method: String get() = "POST"
        val proofDomain: String get() = bodyKind.proofDomain
        val role: Role get() = if (this == DELETION_REQUEST_RECEIPT || this == DELETION_REQUEST_RESOLVE)
            Role.HISTORICAL_READ_ORIGINAL else bodyKind.signingRole
    }
    enum class Failure { INVALID_DOCUMENT, TOO_LARGE, CONTEXT_MISMATCH, IDENTITY_MISMATCH,
        INVALID_SIGNATURE, CLOSED, ALREADY_SIGNED, UNAVAILABLE }
    class Rejected internal constructor(val failure: Failure) :
        IllegalArgumentException("RECOVERY_DELETION_V2_$failure", null) {
        override fun toString(): String = "RecoveryDeletionV2.Rejected($failure)"
    }

    /** Immutable specific context; construction asserts caller provenance, never authenticates it. */
    class Context internal constructor(
        internal val identity: TrustedM5Identity, val platform: Platform, val bodyKind: BodyKind,
        val role: Role, key: ByteArray, expected: Map<String, JsonElement>, original: ByteArray? = null,
        internal val onlyRoute: Route? = null,
    ) {
        private val spki: ByteArray = key.copyOf()
        private val fields: Map<String, JsonElement> = Collections.unmodifiableMap(LinkedHashMap(expected))
        private val retained: ByteArray? = original?.copyOf()
        internal fun keyCopy(): ByteArray = spki.copyOf()
        internal fun expectedCopy(): Map<String, JsonElement> = fields.toMap()
        internal fun originalCopy(): ByteArray? = retained?.copyOf()
        override fun toString(): String = "RecoveryDeletionV2.Context(<redacted>)"
    }
    class PreparedRequest internal constructor(internal val context: Context, unsigned: ByteArray, digest: ByteArray) : AutoCloseable {
        private val body: ByteArray = unsigned.copyOf()
        private val hash: ByteArray = digest.copyOf()
        private val key: ByteArray = context.keyCopy()
        private val message: ByteArray = context.bodyKind.proofDomain.toByteArray(Charsets.US_ASCII) + hash
        private var closed: Boolean = false
        private var consumed: Boolean = false
        val bodyKind: BodyKind get() = context.bodyKind
        val role: Role get() = context.role
        val platform: Platform get() = context.platform
        val keyThumbprintSha256: String
            @Synchronized get() { open(); return hex(sha(key)) }
        @Synchronized fun unsignedCopy(): ByteArray { open(); return body.copyOf() }
        @Synchronized fun requestDigestCopy(): ByteArray { open(); return hash.copyOf() }
        @Synchronized fun signingMessageCopy(): ByteArray { open(); return message.copyOf() }
        @Synchronized fun publicKeySpkiCopy(): ByteArray { open(); return key.copyOf() }
        @Synchronized internal fun consume(signer: (BodyKind, ByteArray) -> ByteArray): SignedCommand {
            open(); ensure(!consumed, Failure.ALREADY_SIGNED)
            consumed = true // Failure also consumes the preparation; never silently re-sign a retry.
            val input = hash.copyOf()
            try {
                val proof = signer(bodyKind, input).copyOf()
                try {
                    verify(key, message, proof)
                    val unsigned = parse(body)
                    validate(unsigned, context)
                    ensure(sha(canonical(unsigned)).contentEquals(hash), Failure.CONTEXT_MISMATCH)
                    val wire = canonical(JsonObject(unsigned + mapOf(
                        "request_sha256" to JsonPrimitive(hex(hash)), "device_signature_b64url" to JsonPrimitive(url(proof)))))
                    return try { SignedCommand(context, wire, hash, proof) } finally { wire.fill(0) }
                } finally { proof.fill(0) }
            } finally { input.fill(0) }
        }
        private fun open() { ensure(!closed, Failure.CLOSED) }
        @Synchronized override fun close() { body.fill(0); hash.fill(0); key.fill(0); message.fill(0); closed = true }
        override fun toString(): String = "RecoveryDeletionV2.PreparedRequest(<redacted>)"
    }
    /** Full immutable nonsecret original, including its one actual signature, never a receipt. */
    class SignedCommand internal constructor(
        internal val context: Context, wire: ByteArray, digest: ByteArray, signature: ByteArray,
    ) : AutoCloseable {
        private val bytes: ByteArray = wire.copyOf()
        private val hash: ByteArray = digest.copyOf()
        private val proof: ByteArray = signature.copyOf()
        private var closed: Boolean = false
        val bodyKind: BodyKind get() = context.bodyKind
        val platform: Platform get() = context.platform
        @Synchronized fun fullOriginalWireCopy(): ByteArray { open(); return bytes.copyOf() }
        @Synchronized fun requestDigestCopy(): ByteArray { open(); return hash.copyOf() }
        @Synchronized fun signatureCopy(): ByteArray { open(); return proof.copyOf() }
        private fun open() { ensure(!closed, Failure.CLOSED) }
        @Synchronized override fun close() { bytes.fill(0); hash.fill(0); proof.fill(0); closed = true }
        override fun toString(): String = "RecoveryDeletionV2.SignedCommand(<redacted>)"
    }
    class WireRequest internal constructor(val route: Route, val platform: Platform, wire: ByteArray) : AutoCloseable {
        private val bytes: ByteArray = wire.copyOf()
        private var closed: Boolean = false
        val role: Role get() = route.role
        @Synchronized fun bodyCopy(): ByteArray { ensure(!closed, Failure.CLOSED); return bytes.copyOf() }
        @Synchronized override fun close() { bytes.fill(0); closed = true }
        override fun toString(): String = "RecoveryDeletionV2.WireRequest(<redacted>)"
    }
    /** Possession of original REQUEST only. NO accepted/NOT_ACCEPTED or lifecycle authority. */
    class VerifiedDeletionResolutionCandidate internal constructor(private val request: WireRequest) : AutoCloseable {
        fun originalRequestBodyCopy(): ByteArray = request.bodyCopy()
        override fun close() { request.close() }
        override fun toString(): String = "RecoveryDeletionV2.VerifiedDeletionResolutionCandidate(<redacted>)"
    }

    fun recoveryPreviewContext(trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        accountId: String, operationId: String): Context = guarded {
        scope(trusted, platform, deviceSpki, BodyKind.RECOVERY_PREVIEW, accountId, operationId)
    }
    fun recoveryCommitContext(trusted: TrustedM5Identity, platform: Platform, deviceSpki: ByteArray,
        accountId: String, operationId: String, confirmedAccountId: String, currentCodeGeneration: Long,
        nextCodeVerifierSha256: String, nextRefreshTokenSha256: String, bindingCommitId: String): Context = guarded {
        scope(trusted, platform, deviceSpki, BodyKind.RECOVERY_COMMIT, accountId, operationId,
            commit(accountId, confirmedAccountId, currentCodeGeneration, nextCodeVerifierSha256, nextRefreshTokenSha256, bindingCommitId))
    }
    /** Caller supplies its current authenticated own actor key/platform, not a historical key. */
    fun deletionRequestContext(trusted: TrustedM5Identity, actorPlatform: Platform, actorSpki: ByteArray,
        accountId: String, operationId: String, confirmedAccountId: String,
        currentCodeGeneration: Long, currentAuthorityGeneration: Long): Context = guarded {
        ensure(accountId == confirmedAccountId, Failure.CONTEXT_MISMATCH)
        scope(trusted, actorPlatform, actorSpki, BodyKind.DELETION_REQUEST, accountId, operationId,
            mapOf("confirmed_account_id" to JsonPrimitive(uuid(confirmedAccountId)),
                "expected_code_generation" to JsonPrimitive(positive(currentCodeGeneration)),
                "expected_authority_generation" to JsonPrimitive(positive(currentAuthorityGeneration))))
    }
    /** New recipient platform/key, independent of the historical DeletionResult actor platform. */
    fun deletionPreviewContext(trusted: TrustedM5Identity, recipientPlatform: Platform, recipientSpki: ByteArray,
        accountId: String, operationId: String): Context = guarded {
        scope(trusted, recipientPlatform, recipientSpki, BodyKind.DELETION_PREVIEW, accountId, operationId)
    }
    fun deletionCancelContext(trusted: TrustedM5Identity, recipientPlatform: Platform, recipientSpki: ByteArray,
        accountId: String, operationId: String, confirmedAccountId: String, currentCodeGeneration: Long,
        nextCodeVerifierSha256: String, nextRefreshTokenSha256: String, bindingCommitId: String,
        deletionRequestId: String, expectedRevision: Long): Context = guarded {
        scope(trusted, recipientPlatform, recipientSpki, BodyKind.DELETION_CANCEL, accountId, operationId,
            commit(accountId, confirmedAccountId, currentCodeGeneration, nextCodeVerifierSha256, nextRefreshTokenSha256, bindingCommitId) +
                mapOf("deletion_request_id" to JsonPrimitive(uuid(deletionRequestId)),
                    "expected_revision" to JsonPrimitive(positive(expectedRevision))))
    }

    fun prepare(context: Context, unsignedUtf8: ByteArray): PreparedRequest = guarded {
        ensure(context.role == context.bodyKind.signingRole && context.onlyRoute == null, Failure.CONTEXT_MISMATCH)
        ensure(unsignedUtf8.size <= MAX_BODY_BYTES, Failure.TOO_LARGE)
        val owned = unsignedUtf8.copyOf()
        try {
            val document = parse(owned); validate(document, context)
            val canonical = canonical(document)
            try { PreparedRequest(context, canonical, sha(canonical)) } finally { canonical.fill(0) }
        } finally { owned.fill(0) }
    }
    /** Closed body-kind callback receives RAW D. Sign ASCII(kind.proofDomain) || D as MESSAGE. */
    fun sign(prepared: PreparedRequest, deviceP256: (BodyKind, ByteArray) -> ByteArray): SignedCommand =
        guarded { prepared.consume(deviceP256) }
    fun complete(prepared: PreparedRequest, signatureP1363: ByteArray): SignedCommand =
        guarded { prepared.consume { _, _ -> signatureP1363 } }

    /** Reparse FULL original under externally retained expected scope. No store/custody is implemented. */
    fun verifyOriginal(context: Context, fullOriginalUtf8: ByteArray): SignedCommand = guarded {
        ensure(context.onlyRoute == null && context.role == context.bodyKind.signingRole, Failure.CONTEXT_MISMATCH)
        ensure(fullOriginalUtf8.size <= MAX_BODY_BYTES, Failure.TOO_LARGE)
        verified(context, fullOriginalUtf8.copyOf())
    }
    fun continuationContext(original: SignedCommand, route: Route): Context = guarded {
        ensure(route.originalOnly && route.bodyKind == original.bodyKind, Failure.CONTEXT_MISMATCH)
        val wire = original.fullOriginalWireCopy()
        try {
            val parent = original.context
            Context(parent.identity, parent.platform, parent.bodyKind, route.role, parent.keyCopy(), parent.expectedCopy(), wire, route)
        } finally { wire.fill(0) }
    }
    /** Only full retained original bytes can select a continuation. No preparation or signing. */
    fun readOriginal(context: Context, route: Route, fullOriginalUtf8: ByteArray): WireRequest = guarded {
        ensure(route.originalOnly && context.onlyRoute == route && context.role == route.role &&
            context.bodyKind == route.bodyKind, Failure.CONTEXT_MISMATCH)
        ensure(fullOriginalUtf8.size <= MAX_BODY_BYTES, Failure.TOO_LARGE)
        val retained = context.originalCopy() ?: reject(Failure.CONTEXT_MISMATCH)
        val candidate = fullOriginalUtf8.copyOf()
        try {
            ensure(candidate.contentEquals(retained), Failure.CONTEXT_MISMATCH)
            verified(context, candidate.copyOf()).use { WireRequest(route, context.platform, candidate) }
        } finally { retained.fill(0); candidate.fill(0) }
    }
    fun request(command: SignedCommand, route: Route): WireRequest = guarded {
        ensure(!route.originalOnly && route.bodyKind == command.bodyKind &&
            command.context.role == route.role && command.context.onlyRoute == null, Failure.CONTEXT_MISMATCH)
        val raw = command.fullOriginalWireCopy()
        try { verified(command.context, raw.copyOf()).use { WireRequest(route, command.platform, raw) } }
        finally { raw.fill(0) }
    }
    fun deletionResolutionCandidate(context: Context, fullOriginalUtf8: ByteArray): VerifiedDeletionResolutionCandidate =
        guarded { VerifiedDeletionResolutionCandidate(readOriginal(context, Route.DELETION_REQUEST_RESOLVE, fullOriginalUtf8)) }

    private fun verified(context: Context, owned: ByteArray): SignedCommand {
        try {
            val body = parse(owned)
            ensure(body.keys == fields(context.bodyKind) + PROOF)
            val unsigned = JsonObject(body.filterKeys { it !in PROOF }); validate(unsigned, context)
            val digest = sha(canonical(unsigned))
            ensure(body.text("request_sha256") == hex(digest))
            val proof = decode(body.text("device_signature_b64url"), true, 64)
            verify(context.keyCopy(), context.bodyKind.proofDomain.toByteArray(Charsets.US_ASCII) + digest, proof)
            canonical(body) // Enforce canonical bound too, retaining the complete supplied wire below.
            return SignedCommand(context, owned, digest, proof)
        } finally { owned.fill(0) }
    }
    private fun scope(identity: TrustedM5Identity, platform: Platform, key: ByteArray, kind: BodyKind,
        account: String, operation: String, extra: Map<String, JsonElement> = emptyMap()): Context {
        val identityKey = identity.publicKeySpki
        p256(identityKey); uuid(identity.serverInstanceId); positive(identity.identityEpoch)
        ensure(hash(identity.thumbprintSha256) == hex(sha(identityKey)), Failure.IDENTITY_MISMATCH)
        origin(identity.apiOrigin); origin(identity.streamOrigin)
        val ownedKey = key.copyOf()
        p256(ownedKey)
        val snapshot = TrustedM5Identity(identity.serverInstanceId, identity.identityEpoch, identityKey,
            identity.thumbprintSha256, identity.apiOrigin, identity.streamOrigin)
        return Context(snapshot, platform, kind, kind.signingRole, ownedKey,
            mapOf("account_id" to JsonPrimitive(uuid(account)), "operation_id" to JsonPrimitive(uuid(operation))) + extra)
    }
    private fun commit(account: String, confirmed: String, generation: Long, verifier: String, refresh: String,
        binding: String): Map<String, JsonElement> {
        ensure(account == confirmed, Failure.CONTEXT_MISMATCH)
        return mapOf("confirmed_account_id" to JsonPrimitive(uuid(confirmed)),
            "expected_code_generation" to JsonPrimitive(positive(generation)),
            "next_code_verifier_sha256" to JsonPrimitive(hash(verifier)),
            "next_refresh_token_sha256" to JsonPrimitive(hash(refresh)), "binding_commit_id" to JsonPrimitive(uuid(binding)))
    }
    private fun validate(body: JsonObject, context: Context) {
        ensure(body.keys == fields(context.bodyKind))
        for ((name, value) in body) {
            if (name in INTEGERS) { positive(integer(value)); continue }
            val text = body.text(name)
            ensure(text.codePointCount(0, text.length) in 1..2048)
            if (name.endsWith("_id")) uuid(text)
            if (name.endsWith("_sha256")) hash(text)
        }
        ensure(body.text("contract_version") == "v2" && integer(body["schema_version"]) == 2L)
        ensure(body.text("platform") == context.platform.name, Failure.CONTEXT_MISMATCH)
        val identity = context.identity
        val trust = mapOf("expected_server_instance_id" to JsonPrimitive(identity.serverInstanceId),
            "expected_identity_epoch" to JsonPrimitive(identity.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(identity.thumbprintSha256),
            "expected_api_origin" to JsonPrimitive(identity.apiOrigin), "expected_stream_origin" to JsonPrimitive(identity.streamOrigin))
        ensure(trust.all { (name, value) -> body[name] == value }, Failure.IDENTITY_MISMATCH)
        ensure(context.expectedCopy().all { (name, value) -> body[name] == value }, Failure.CONTEXT_MISMATCH)
        origin(body.text("expected_api_origin")); origin(body.text("expected_stream_origin"))
        val time = body.text("requested_at")
        ensure(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z").matches(time))
        ensure(!time.startsWith("0000-"))
        LocalDateTime.parse(time.dropLast(1))
        for ((name, limit) in listOf("device_name" to 120, "app_version" to 32)) {
            val text = body.text(name)
            ensure(text.codePointCount(0, text.length) in 1..limit && text.none { it.code < 32 || it.code == 127 })
        }
        val key = decode(body.text("device_public_key_spki_b64"), false, 91); p256(key)
        ensure(key.contentEquals(context.keyCopy()) && hex(sha(key)) == body.text("device_key_thumbprint_sha256"), Failure.CONTEXT_MISMATCH)
        body["confirmed_account_id"]?.let { ensure(it == body["account_id"], Failure.CONTEXT_MISMATCH) }
    }
    private fun p256(spki: ByteArray): ECPublicKey {
        ensure(spki.size == 91 && spki.copyOfRange(0, SPKI_PREFIX.size).contentEquals(SPKI_PREFIX))
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey ?: reject()
        val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val actual = key.params
        ensure(actual.curve == expected.curve && actual.generator == expected.generator && actual.order == expected.order &&
            actual.cofactor == expected.cofactor && key.encoded.contentEquals(spki))
        val p = (expected.curve.field as ECFieldFp).p; val x = key.w.affineX; val y = key.w.affineY
        ensure(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p &&
            y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x)).add(expected.curve.b).mod(p))
        return key
    }
    private fun verify(spki: ByteArray, message: ByteArray, proof: ByteArray) {
        ensure(proof.size == 64, Failure.INVALID_SIGNATURE)
        val key = p256(spki)
        val r = BigInteger(1, proof.copyOfRange(0, 32)); val s = BigInteger(1, proof.copyOfRange(32, 64))
        ensure(r.signum() > 0 && r < key.params.order && s.signum() > 0 && s < key.params.order, Failure.INVALID_SIGNATURE)
        val rb = r.toByteArray(); val sb = s.toByteArray()
        val der = byteArrayOf(0x30, (4 + rb.size + sb.size).toByte(), 2, rb.size.toByte()) + rb + byteArrayOf(2, sb.size.toByte()) + sb
        ensure(Signature.getInstance("SHA256withECDSA").run { initVerify(key); update(message); verify(der) }, Failure.INVALID_SIGNATURE)
    }
    private fun origin(value: String) {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 } && value.none { it in "?#@\\%" })
        val authority = OriginAuthorityV2.parse(value) ?: reject()
        ensure(authority.portText == null || authority.portText.length in 1..5)
        ensure(authority.port == null || authority.port in 1..65535)
        ensure(authority.isIpv6 || authority.hasDnsName(253))
        // No trimming, case folding, path stripping, DNS or other runtime/transport probe.
    }
    private fun integer(value: JsonElement?): Long {
        val token = value as? JsonPrimitive ?: reject()
        ensure(!token.isString && Regex("0|[1-9][0-9]{0,15}").matches(token.content))
        return (token.content.toLongOrNull() ?: reject()).also { ensure(it in 0..MAX_INTEGER) }
    }
    private fun positive(value: Long): Long = value.also { ensure(it in 1..MAX_INTEGER) }
    private fun uuid(value: String): String = value.also { ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(it)) }
    private fun hash(value: String): String = value.also { ensure(Regex("[0-9a-f]{64}").matches(it)) }
    private fun JsonObject.text(name: String): String = (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject()
    private fun decode(text: String, isUrl: Boolean, size: Int): ByteArray {
        ensure(text.length <= 256)
        val bytes = (if (isUrl) Base64.getUrlDecoder() else Base64.getDecoder()).decode(text)
        ensure(bytes.size == size && (if (isUrl) url(bytes) else Base64.getEncoder().encodeToString(bytes)) == text)
        return bytes
    }
    private fun url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    private fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun canonical(body: JsonObject): ByteArray = JsonCanonicalizer(body.toString()).encodedString.toByteArray(Charsets.UTF_8)
        .also { ensure(it.size <= MAX_BODY_BYTES, Failure.TOO_LARGE) }
    private fun ensure(condition: Boolean, failure: Failure = Failure.INVALID_DOCUMENT) { if (!condition) reject(failure) }
    private fun reject(failure: Failure = Failure.INVALID_DOCUMENT): Nothing = throw Rejected(failure)
    private inline fun <T> guarded(block: () -> T): T = try { block() }
        catch (failure: Rejected) { throw failure }
        catch (_: java.security.NoSuchAlgorithmException) { reject(Failure.UNAVAILABLE) }
        catch (_: Exception) { reject() }

    private val SPKI_PREFIX = byteArrayOf(0x30, 0x59, 0x30, 0x13, 6, 7, 0x2a, 0x86.toByte(),
        0x48, 0xce.toByte(), 0x3d, 2, 1, 6, 8, 0x2a, 0x86.toByte(), 0x48, 0xce.toByte(), 0x3d, 3, 1, 7, 3, 0x42, 0, 4)
    private val PROOF = setOf("request_sha256", "device_signature_b64url")
    private val INTEGERS = setOf("schema_version", "expected_identity_epoch", "expected_code_generation", "expected_authority_generation", "expected_revision")
    private val COMMON = setOf("contract_version", "schema_version", "platform", "expected_server_instance_id",
        "expected_identity_epoch", "expected_identity_thumbprint_sha256", "expected_api_origin", "expected_stream_origin",
        "operation_id", "account_id", "requested_at", "device_public_key_spki_b64", "device_key_thumbprint_sha256", "device_name", "app_version")
    private val COMMIT = setOf("expected_code_generation", "next_code_verifier_sha256", "next_refresh_token_sha256", "binding_commit_id", "confirmed_account_id")
    private fun fields(kind: BodyKind): Set<String> = COMMON + when (kind) {
        BodyKind.RECOVERY_PREVIEW, BodyKind.DELETION_PREVIEW -> emptySet()
        BodyKind.RECOVERY_COMMIT -> COMMIT
        BodyKind.DELETION_REQUEST -> setOf("confirmed_account_id", "expected_code_generation", "expected_authority_generation")
        BodyKind.DELETION_CANCEL -> COMMIT + setOf("deletion_request_id", "expected_revision")
    }
    private fun parse(bytes: ByteArray): JsonObject {
        ensure(bytes.size in 2..MAX_BODY_BYTES, if (bytes.size > MAX_BODY_BYTES) Failure.TOO_LARGE else Failure.INVALID_DOCUMENT)
        val raw = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        return FlatJson(raw).parse()
    }
    /** All five actual schemas are flat. Decode member names before duplicate detection. */
    private class FlatJson(private val raw: String) {
        private var cursor = 0
        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(char: Char): Boolean { whitespace(); return (cursor < raw.length && raw[cursor] == char).also { if (it) cursor++ } }
        fun parse(): JsonObject {
            ensure(take('{')); val members = linkedMapOf<String, JsonElement>()
            if (!take('}')) {
                do {
                    whitespace(); val name = quoted(); ensure(name !in members && take(':')); whitespace(); ensure(cursor < raw.length)
                    members[name] = if (raw[cursor] == '"') JsonPrimitive(quoted()) else {
                        val start = cursor
                        while (cursor < raw.length && raw[cursor] !in " \t\r\n,}") cursor++
                        val token = raw.substring(start, cursor)
                        ensure(Regex("0|[1-9][0-9]{0,15}").matches(token))
                        val number = token.toLongOrNull() ?: reject(); ensure(number <= MAX_INTEGER); JsonPrimitive(number)
                    }
                } while (take(','))
                ensure(take('}'))
            }
            whitespace(); ensure(cursor == raw.length); return JsonObject(members.toMap())
        }
        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"'); val start = cursor++; var closed = false
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
            val value = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            var index = 0
            while (index < value.length) {
                val char = value[index++]
                if (char.isHighSurrogate()) ensure(index < value.length && value[index++].isLowSurrogate()) else ensure(!char.isLowSurrogate())
            }
            return value
        }
    }
}
