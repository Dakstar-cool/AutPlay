package app.autplay.application.devicecompatibility

import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.NoSuchAlgorithmException
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.time.Duration
import java.time.Instant
import java.util.Base64
import java.util.Collections
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * Pure CLIENT D1 codec. Input identity MUST come from an already trusted M5 binding.
 * No transport, trust establishment, credential custody, persistence or mutation authority.
 * HTTPS/PKI and the existing explicit development transport exception remain caller obligations.
 * Signed strings are compared exactly, never normalized, trimmed or used to contact an origin.
 */
object DeviceContractV2Negotiation {
    const val DISCOVERY_MAX_BYTES: Int = 16_384
    const val CAPABILITIES_MAX_BYTES: Int = 65_536
    const val MAX_REVISION: Long = 9_007_199_254_740_991L
    private const val DISCOVERY_DOMAIN = "autplay:device-compatibility:negotiation:discovery:v2\n"
    private const val CAPABILITIES_DOMAIN = "autplay:device-compatibility:negotiation:capabilities:v2\n"

    enum class Failure {
        INVALID_JSON, INVALID_DOCUMENT, TOO_LARGE, UNSUPPORTED, UNAVAILABLE,
        IDENTITY_MISMATCH, SCOPE_MISMATCH, HASH_MISMATCH, INVALID_SIGNATURE,
        EXPIRED, CLOCK_SKEW, REVISION_ROLLBACK, REVISION_CONFLICT, OBSERVATION_SCOPE_MISMATCH,
    }

    sealed interface Result<out T> {
        class Verified<T> internal constructor(val value: T) : Result<T> {
            override fun toString(): String = "DeviceContractV2.Verified"
        }
        // Also usable by a future transport to distinguish a missing route from unavailable service.
        class Rejected(val failure: Failure) : Result<Nothing> {
            override fun toString(): String = "DeviceContractV2.Rejected($failure)"
        }
    }

    enum class Platform { ANDROID, MACOS }
    enum class Family(val wire: String) {
        ENROLLMENT("enrollment"), ADMISSION("admission"), TRUSTED("trusted"), PORTABLE("portable"),
        SELF("self"), RECOVERY("recovery"), DELETION("deletion"), SESSION("session"),
    }

    class TrustedM5Identity(
        val serverInstanceId: String,
        val identityEpoch: Long,
        publicKeySpki: ByteArray,
        val thumbprintSha256: String,
        val apiOrigin: String,
        val streamOrigin: String,
    ) {
        private val spki: ByteArray = publicKeySpki.copyOf()
        val publicKeySpki: ByteArray get() = spki.copyOf()
        override fun toString(): String = "DeviceContractV2.TrustedM5Identity"
        internal fun sameAs(other: TrustedM5Identity): Boolean =
            serverInstanceId == other.serverInstanceId && identityEpoch == other.identityEpoch &&
                thumbprintSha256 == other.thumbprintSha256 && apiOrigin == other.apiOrigin &&
                streamOrigin == other.streamOrigin && spki.contentEquals(other.spki)
    }

    class CapabilityScope(val userId: String, val deviceId: String, val platform: Platform) {
        override fun toString(): String = "DeviceContractV2.CapabilityScope"
        internal fun sameAs(other: CapabilityScope): Boolean =
            userId == other.userId && deviceId == other.deviceId && platform == other.platform
    }

    class Discovery internal constructor(
        val identity: TrustedM5Identity,
        platforms: Set<Platform>, families: Set<Family>,
        val issuedAt: Instant, val expiresAt: Instant, canonicalEnvelope: ByteArray,
    ) {
        val platforms: Set<Platform> = frozen(platforms)
        val families: Set<Family> = frozen(families)
        private val envelope: ByteArray = canonicalEnvelope.copyOf()
        val canonicalEnvelope: ByteArray get() = envelope.copyOf()
        val isEmptyAdvertisement: Boolean get() = platforms.isEmpty() || families.isEmpty()
        /** Compatibility hint only; never account, session or mutation authority. */
        fun advertises(platform: Platform, family: Family): Boolean =
            platform in platforms && family in families
        override fun toString(): String = "DeviceContractV2.Discovery"
    }

    class Capabilities internal constructor(
        val identity: TrustedM5Identity, val scope: CapabilityScope, val revision: Long,
        families: Set<Family>, val issuedAt: Instant, val expiresAt: Instant,
        canonicalEnvelope: ByteArray,
    ) {
        val families: Set<Family> = frozen(families)
        private val envelope: ByteArray = canonicalEnvelope.copyOf()
        val canonicalEnvelope: ByteArray get() = envelope.copyOf()
        val isEmptyAdvertisement: Boolean get() = families.isEmpty()
        override fun toString(): String = "DeviceContractV2.Capabilities"
    }

    /**
     * Immutable comparison evidence, not a store or an authority token. A FUTURE approved protected
     * custodian must retain the high-water durably per identity/account/device, protect it from
     * rollback, and bind it to the current lifecycle/generation. No ordinary DB/global fallback.
     * Restoration/serialization is deliberately outside this codec and does not approve K1 or J.
     */
    class CapabilityObservation internal constructor(
        val identity: TrustedM5Identity, val scope: CapabilityScope, val revision: Long,
        effectivePayloadSha256: ByteArray,
    ) {
        private val effective: ByteArray = effectivePayloadSha256.copyOf()
        val effectivePayloadSha256: ByteArray get() = effective.copyOf()
        internal fun sameContent(digest: ByteArray): Boolean = MessageDigest.isEqual(effective, digest)
        override fun toString(): String = "DeviceContractV2.CapabilityObservation"
    }

    /** Decode only previously protected custodian evidence, never a network authority claim. */
    fun restoredObservation(trusted: TrustedM5Identity, scope: CapabilityScope, revision: Long,
        effectivePayloadSha256: ByteArray): Result<CapabilityObservation> = guarded {
        uuid(trusted.serverInstanceId); uuid(scope.userId); uuid(scope.deviceId)
        ensure(trusted.identityEpoch in 1..MAX_REVISION && revision in 1..MAX_REVISION, Failure.INVALID_DOCUMENT)
        ensure(effectivePayloadSha256.size == 32, Failure.INVALID_DOCUMENT)
        CapabilityObservation(trusted, scope, revision, effectivePayloadSha256)
    }

    /**
     * Proposal only: compare-and-set against expectedPrevious in approved protected custody before
     * using the view. Never apply a stale asynchronous result or reset a missing prior implicitly.
     * Snapshot verification cannot replace binding, consent or current server authority fences.
     */
    class CapabilityTransition internal constructor(
        val snapshot: Capabilities,
        val expectedPrevious: CapabilityObservation?,
        val nextObservation: CapabilityObservation,
    ) {
        override fun toString(): String = "DeviceContractV2.CapabilityTransition"
    }

    fun discovery(bytes: ByteArray, trusted: TrustedM5Identity, now: Instant): Result<Discovery> = guarded {
        val frame = envelope(bytes, DISCOVERY_MAX_BYTES, discoveryMembers, trusted, DISCOVERY_DOMAIN)
        val platforms = values(frame.payload, "platforms", Platform.entries.map { it.name }.toSet(), 2)
            .map { Platform.valueOf(it) }.toSet()
        val families = families(frame.payload)
        val (issued, expires) = freshness(frame.payload, now, 600)
        Discovery(trusted, platforms, families, issued, expires, frame.canonical)
    }

    /** prior=null is ONLY an explicit first observation authorized by the future custodian. */
    fun capabilities(
        bytes: ByteArray, trusted: TrustedM5Identity, expected: CapabilityScope,
        prior: CapabilityObservation?, now: Instant,
    ): Result<CapabilityTransition> = guarded {
        val frame = envelope(bytes, CAPABILITIES_MAX_BYTES, capabilityMembers, trusted, CAPABILITIES_DOMAIN)
        val payload = frame.payload
        val user = uuid(payload.text("user_id"))
        val device = uuid(payload.text("device_id"))
        val platformText = payload.text("platform")
        ensure(platformText in Platform.entries.map { it.name }, Failure.INVALID_DOCUMENT)
        ensure(user == uuid(expected.userId) && device == uuid(expected.deviceId) &&
            platformText == expected.platform.name, Failure.SCOPE_MISMATCH)
        val revision = payload.positive("capability_revision")
        val families = families(payload)
        val (issued, expires) = freshness(payload, now, 3600)
        // All wire-effective fields participate, including identity, origins and platform.
        // The server also advances revision for gates/role/limits not exposed in this wire schema.
        val effective = sha256(canonical(JsonObject(payload.filterKeys {
            it != "issued_at" && it != "expires_at" && it != "capability_revision"
        })))
        if (prior != null) {
            ensure(prior.identity.sameAs(trusted) && prior.scope.sameAs(expected), Failure.OBSERVATION_SCOPE_MISMATCH)
            ensure(revision >= prior.revision, Failure.REVISION_ROLLBACK)
            ensure(revision != prior.revision || prior.sameContent(effective), Failure.REVISION_CONFLICT)
        }
        val snapshot = Capabilities(trusted, expected, revision, families, issued, expires, frame.canonical)
        CapabilityTransition(snapshot, prior, CapabilityObservation(trusted, expected, revision, effective))
    }

    private class Frame(val payload: JsonObject, val canonical: ByteArray)

    private fun envelope(
        bytes: ByteArray, maximum: Int, members: Set<String>, trusted: TrustedM5Identity, domain: String,
    ): Frame {
        ensure(bytes.size <= maximum, Failure.TOO_LARGE)
        val input = bytes.copyOf()
        val raw = try {
            Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(input)).toString()
        } catch (_: Exception) { reject(Failure.INVALID_JSON) }
        val root = StrictJson(raw).parse() as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
        ensure(root.keys == envelopeMembers, Failure.INVALID_DOCUMENT)
        version(root)
        ensure(root.text("signature_algorithm") == "ES256-P1363", Failure.UNSUPPORTED)
        val payload = root["payload"] as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
        ensure(payload.keys == members, Failure.INVALID_DOCUMENT)
        version(payload)
        ensure(payload.positive("api_major") == 1L, Failure.UNSUPPORTED)
        // Check lexical integer tokens BEFORE JCS can turn 1.0/1e0 into 1.
        val epoch = payload.positive("identity_epoch")
        if (members == capabilityMembers) payload.positive("capability_revision")
        val whole = canonical(root)
        ensure(whole.size <= maximum, Failure.TOO_LARGE)
        val digest = sha256(canonical(payload))
        ensure(hash(root.text("payload_sha256")) == hex(digest), Failure.HASH_MISMATCH)
        val spki = base64(payload.text("identity_public_key_spki_b64"), false, 91)
        val publicKey = p256(spki)
        val thumbprint = hash(payload.text("identity_thumbprint_sha256"))
        ensure(thumbprint == hex(sha256(spki)), Failure.IDENTITY_MISMATCH)
        val instance = uuid(payload.text("server_instance_id"))
        val api = origin(payload.text("api_origin"))
        val stream = origin(payload.text("stream_origin"))
        ensure(instance == trusted.serverInstanceId && epoch == trusted.identityEpoch &&
            thumbprint == trusted.thumbprintSha256 && spki.contentEquals(trusted.publicKeySpki) &&
            api == trusted.apiOrigin && stream == trusted.streamOrigin, Failure.IDENTITY_MISMATCH)
        // The document's key has now been matched byte-for-byte to the caller's trusted key.
        val signature = base64(root.text("signature_b64url"), true, 64)
        val der = p1363ToDer(signature, publicKey.params.order)
        val valid = try {
            Signature.getInstance("SHA256withECDSA").run {
                initVerify(publicKey)
                update(domain.toByteArray(Charsets.US_ASCII))
                update(digest)
                verify(der)
            }
        } catch (error: NoSuchAlgorithmException) { throw error }
        catch (_: java.security.GeneralSecurityException) { reject(Failure.INVALID_SIGNATURE) }
        ensure(valid, Failure.INVALID_SIGNATURE)
        return Frame(payload, whole)
    }

    private fun version(value: JsonObject) {
        ensure(value.text("contract_version") == "v2", Failure.UNSUPPORTED)
        ensure(value.positive("schema_version") == 2L, Failure.UNSUPPORTED)
    }

    private fun freshness(value: JsonObject, now: Instant, seconds: Long): Pair<Instant, Instant> {
        val issued = time(value.text("issued_at"))
        val expires = time(value.text("expires_at"))
        val ttl = Duration.between(issued, expires)
        ensure(!ttl.isNegative && !ttl.isZero && ttl <= Duration.ofSeconds(seconds), Failure.INVALID_DOCUMENT)
        ensure(expires > now, Failure.EXPIRED)
        ensure(Duration.between(now, issued) <= Duration.ofSeconds(120), Failure.CLOCK_SKEW)
        return issued to expires
    }

    private fun time(value: String): Instant {
        ensure(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z").matches(value),
            Failure.INVALID_DOCUMENT)
        // LocalDateTime is strict about calendar values, 24:00 and leap-second coercion.
        return java.time.LocalDateTime.parse(value.dropLast(1)).toInstant(java.time.ZoneOffset.UTC)
    }

    private fun families(value: JsonObject): Set<Family> =
        values(value, "families", Family.entries.map { it.wire }.toSet(), 8)
            .map { name -> Family.entries.single { it.wire == name } }.toSet()

    private fun values(value: JsonObject, name: String, allowed: Set<String>, max: Int): Set<String> {
        val array = value[name] as? JsonArray ?: reject(Failure.INVALID_DOCUMENT)
        ensure(array.size <= max, Failure.INVALID_DOCUMENT)
        val strings = array.map { string(it) }
        ensure(strings.toSet().size == strings.size && strings.all { it in allowed }, Failure.INVALID_DOCUMENT)
        return strings.toSet()
    }

    private fun JsonObject.text(name: String): String = string(this[name])
    private fun string(value: JsonElement?): String =
        (value as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject(Failure.INVALID_DOCUMENT)

    private fun JsonObject.positive(name: String): Long {
        val token = this[name] as? JsonPrimitive ?: reject(Failure.INVALID_DOCUMENT)
        ensure(!token.isString && Regex("[1-9][0-9]{0,15}").matches(token.content), Failure.INVALID_DOCUMENT)
        val value = token.content.toLongOrNull() ?: reject(Failure.INVALID_DOCUMENT)
        ensure(value in 1..MAX_REVISION, Failure.INVALID_DOCUMENT)
        return value
    }

    private fun uuid(value: String): String {
        ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(value) &&
            UUID.fromString(value).toString() == value, Failure.INVALID_DOCUMENT)
        return value
    }

    private fun hash(value: String): String {
        ensure(Regex("[0-9a-f]{64}").matches(value), Failure.INVALID_DOCUMENT)
        return value
    }

    private fun origin(value: String): String {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 }, Failure.INVALID_DOCUMENT)
        val authority = OriginAuthorityV2.parse(value) ?: reject(Failure.INVALID_DOCUMENT)
        ensure(authority.port == null || authority.port in 1..65535, Failure.INVALID_DOCUMENT)
        // M5's settings preparer issues STD3 ASCII DNS names, including numeric final labels.
        // Its normalized spelling and transport trust remain the existing M5 owner's concern.
        ensure(authority.isIpv6 || authority.hasDnsName(253), Failure.INVALID_DOCUMENT)
        return value
    }

    private fun base64(value: String, url: Boolean, size: Int): ByteArray {
        ensure(value.length <= 256, Failure.INVALID_DOCUMENT)
        val decoded = try {
            (if (url) Base64.getUrlDecoder() else Base64.getDecoder()).decode(value)
        } catch (_: IllegalArgumentException) { reject(Failure.INVALID_DOCUMENT) }
        val encoded = (if (url) Base64.getUrlEncoder().withoutPadding() else Base64.getEncoder()).encodeToString(decoded)
        ensure(decoded.size == size && encoded == value, Failure.INVALID_DOCUMENT)
        return decoded
    }

    private fun p256(spki: ByteArray): ECPublicKey {
        // Exact DER id-ecPublicKey + named prime256v1 + uncompressed point BIT STRING.
        val prefix = byteArrayOf(0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86.toByte(),
            0x48, 0xce.toByte(), 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86.toByte(), 0x48,
            0xce.toByte(), 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00, 0x04)
        ensure(spki.size == 91 && spki.copyOfRange(0, prefix.size).contentEquals(prefix), Failure.INVALID_DOCUMENT)
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey
            ?: reject(Failure.INVALID_DOCUMENT)
        val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val actual = key.params
        ensure(actual.curve == expected.curve && actual.generator == expected.generator &&
            actual.order == expected.order && actual.cofactor == expected.cofactor &&
            key.encoded.contentEquals(spki), Failure.INVALID_DOCUMENT)
        val p = (expected.curve.field as ECFieldFp).p
        val x = key.w.affineX
        val y = key.w.affineY
        ensure(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p &&
            y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x))
                .add(expected.curve.b).mod(p), Failure.INVALID_DOCUMENT)
        return key
    }

    private fun p1363ToDer(signature: ByteArray, order: BigInteger): ByteArray {
        fun integer(offset: Int): ByteArray {
            val value = BigInteger(1, signature.copyOfRange(offset, offset + 32))
            ensure(value.signum() > 0 && value < order, Failure.INVALID_SIGNATURE)
            return value.toByteArray() // Minimal positive ASN.1 INTEGER; keep high-S valid.
        }
        val r = integer(0)
        val s = integer(32)
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 0x02, r.size.toByte()) + r +
            byteArrayOf(0x02, s.size.toByte()) + s
    }

    private fun canonical(value: JsonObject): ByteArray =
        JsonCanonicalizer(value.toString()).encodedString.toByteArray(Charsets.UTF_8)
    private fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun <T> frozen(values: Set<T>): Set<T> = Collections.unmodifiableSet(LinkedHashSet(values))

    private class Invalid(val failure: Failure) : RuntimeException(failure.name, null, false, false)
    private fun reject(failure: Failure): Nothing = throw Invalid(failure)
    private fun ensure(condition: Boolean, failure: Failure) { if (!condition) reject(failure) }
    private inline fun <T> guarded(block: () -> T): Result<T> = try {
        Result.Verified(block())
    } catch (error: Invalid) { Result.Rejected(error.failure) }
    catch (_: NoSuchAlgorithmException) { Result.Rejected(Failure.UNAVAILABLE) }
    catch (_: Exception) { Result.Rejected(Failure.INVALID_DOCUMENT) }

    private val envelopeMembers = setOf("contract_version", "schema_version", "payload", "payload_sha256",
        "signature_algorithm", "signature_b64url")
    private val commonMembers = setOf("contract_version", "schema_version", "server_instance_id", "identity_epoch",
        "identity_public_key_spki_b64", "identity_thumbprint_sha256", "api_origin", "stream_origin", "api_major",
        "families", "issued_at", "expires_at")
    private val discoveryMembers = commonMembers + "platforms"
    private val capabilityMembers = commonMembers + setOf("platform", "user_id", "device_id", "capability_revision")

    /** Bounded lexical parser: duplicate decoded keys, invalid Unicode and token coercion fail first. */
    private class StrictJson(private val raw: String) {
        private var cursor: Int = 0
        fun parse(): JsonElement {
            val result = value(0)
            whitespace()
            ensure(cursor == raw.length, Failure.INVALID_JSON)
            return result
        }

        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(character: Char): Boolean {
            whitespace()
            return (cursor < raw.length && raw[cursor] == character).also { if (it) cursor++ }
        }
        private fun value(depth: Int): JsonElement {
            whitespace()
            ensure(depth <= 8 && cursor < raw.length, Failure.INVALID_JSON)
            return when (raw[cursor]) {
                '{' -> {
                    cursor++
                    val members = linkedMapOf<String, JsonElement>()
                    if (!take('}')) {
                        do {
                            whitespace()
                            val key = quoted()
                            ensure(key !in members && take(':'), Failure.INVALID_JSON)
                            members[key] = value(depth + 1)
                        } while (take(','))
                        ensure(take('}'), Failure.INVALID_JSON)
                    }
                    JsonObject(members)
                }
                '[' -> {
                    cursor++
                    val items = mutableListOf<JsonElement>()
                    if (!take(']')) {
                        do { items.add(value(depth + 1)) } while (take(','))
                        ensure(take(']'), Failure.INVALID_JSON)
                    }
                    JsonArray(items)
                }
                '"' -> JsonPrimitive(quoted())
                else -> {
                    val start = cursor
                    while (cursor < raw.length && raw[cursor] !in " \t\r\n,]}:") cursor++
                    val token = raw.substring(start, cursor)
                    ensure(token in setOf("true", "false", "null") ||
                        Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?").matches(token), Failure.INVALID_JSON)
                    Json.parseToJsonElement(token)
                }
            }
        }

        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"', Failure.INVALID_JSON)
            val start = cursor++
            var closed = false
            while (cursor < raw.length) {
                val character = raw[cursor++]
                if (character == '"') { closed = true; break }
                ensure(character.code >= 32, Failure.INVALID_JSON)
                if (character == '\\') {
                    ensure(cursor < raw.length, Failure.INVALID_JSON)
                    val escape = raw[cursor++]
                    ensure(escape in "\"\\/bfnrtu", Failure.INVALID_JSON)
                    if (escape == 'u') {
                        ensure(cursor + 4 <= raw.length && raw.substring(cursor, cursor + 4)
                            .all { it in "0123456789abcdefABCDEF" }, Failure.INVALID_JSON)
                        cursor += 4
                    }
                }
            }
            ensure(closed, Failure.INVALID_JSON)
            val decoded = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            var index = 0
            while (index < decoded.length) {
                val character = decoded[index++]
                if (character.isHighSurrogate()) {
                    ensure(index < decoded.length && decoded[index++].isLowSurrogate(), Failure.INVALID_JSON)
                } else ensure(!character.isLowSurrogate(), Failure.INVALID_JSON)
            }
            return decoded
        }
    }
}
