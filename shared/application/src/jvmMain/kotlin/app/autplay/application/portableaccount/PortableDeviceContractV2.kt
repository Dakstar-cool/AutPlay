package app.autplay.application.portableaccount

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.devicecompatibility.OriginAuthorityV2
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
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
 * Pure D1 REGISTER/LOGIN preparation. The unsigned PortableChallengeResult MUST arrive through
 * the caller's already trusted M5 transport. This codec neither establishes that trust nor contacts
 * an origin. Capability declarations, syntax and local ROOT possession grant no server authority.
 * No HTTP, credentials store, token generation, protected pending custody K or Journal J is added.
 */
object PortableDeviceContractV2 {
    const val MAX_BYTES: Int = 16_384
    private const val MAX_INTEGER: Long = 9_007_199_254_740_991L

    enum class Purpose { REGISTER, LOGIN }
    enum class Platform { ANDROID, MACOS }
    enum class Failure { INVALID_DOCUMENT, TOO_LARGE, IDENTITY_MISMATCH, SCOPE_MISMATCH, EXPIRED, CLOSED, ALREADY_SIGNED }

    /** Fixed, redacted errors; decoder/crypto exceptions and their payloads are never attached. */
    class Rejected internal constructor(val failure: Failure) :
        IllegalArgumentException("PORTABLE_DEVICE_V2_${failure.name}")

    fun prepare(
        rawChallengeResult: ByteArray,
        trusted: TrustedM5Identity,
        expectedPlatform: Platform,
        expectedPurpose: Purpose,
        ownDocument: PortableAccountDocument,
        devicePublicKeySpki: ByteArray,
        deviceName: String,
        appVersion: String,
        nextRefreshTokenSha256: String,
        bindingCommitId: String,
        now: Instant,
    ): PreparedRequest = PreparedRequest.validated(rawChallengeResult, trusted, expectedPlatform,
        expectedPurpose, ownDocument, devicePublicKeySpki, deviceName, appVersion,
        nextRefreshTokenSha256, bindingCommitId, now)

    /**
     * Sensitive passport-bearing request, without ROOT proof. No generated copy/serialization.
     * It can be signed ONCE. Keep the resulting SignedCommand for exact retries; never prepare,
     * re-sign, choose another nonce or binding ID after an unknown remote outcome.
     * close() clears owned arrays best-effort, not JVM strings, provider memory or OS snapshots.
     */
    class PreparedRequest private constructor(
        val purpose: Purpose,
        val platform: Platform,
        request: ByteArray,
        passport: ByteArray,
    ) : AutoCloseable {
        private val requestBytes: ByteArray = request.copyOf()
        private val passportBytes: ByteArray = passport.copyOf()
        private var closed: Boolean = false
        private var signed: Boolean = false

        /** Transient credential copy for review/approved future transport, never ordinary export. */
        @Synchronized fun transientCanonicalRequestCopy(): ByteArray {
            ensure(!closed, Failure.CLOSED)
            return requestBytes.copyOf()
        }

        /** Only UnlockedPortableAccount supplies a signer; private key material stays inside it. */
        @Synchronized internal fun sign(
            document: PortableAccountDocument, signer: (ByteArray) -> ByteArray,
        ): SignedCommand = guarded {
            ensure(!closed, Failure.CLOSED)
            ensure(!signed, Failure.ALREADY_SIGNED)
            val own = passportSnapshot(document)
            try { ensure(own.contentEquals(passportBytes), Failure.SCOPE_MISMATCH) }
            finally { own.fill(0) }
            val prefix = when (purpose) {
                Purpose.REGISTER -> "autplay:portable-account:register:v2\n"
                Purpose.LOGIN -> "autplay:portable-account:login:v2\n"
            }.toByteArray(Charsets.US_ASCII)
            val message = prefix + requestBytes
            // Consumed even on signing failure; no silent re-signing of an ambiguous command.
            signed = true
            try {
                val proof = signer(message)
                try { SignedCommand.create(requestBytes, proof) }
                finally { proof.fill(0) }
            } finally { message.fill(0); prefix.fill(0) }
        }

        @Synchronized override fun close() {
            requestBytes.fill(0); passportBytes.fill(0); closed = true
        }
        override fun toString(): String = "PortableDeviceContractV2.PreparedRequest(redacted)"

        companion object {
            // The only construction path performs full validation, including for module callers.
            internal fun validated(
                raw: ByteArray, trusted: TrustedM5Identity, platform: Platform, purpose: Purpose,
                own: PortableAccountDocument, spki: ByteArray, name: String, version: String,
                refreshHash: String, bindingId: String, now: Instant,
            ): PreparedRequest = guarded {
                val response = parse(raw, MAX_BYTES)
                exact(response, versionMembers + setOf("challenge", "account"))
                version(response, platform)
                val challenge = response["challenge"] as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
                exact(challenge, challengeMembers)
                version(challenge, platform)
                ensure(challenge.text("purpose") == purpose.name, Failure.SCOPE_MISMATCH)
                nullMembers.forEach { ensure(challenge[it] === JsonNull, Failure.INVALID_DOCUMENT) }
                uuid(challenge.text("challenge_id"))
                uuid(challenge.text("account_id"))
                val passport = response["account"] as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
                val canonicalPassport = validatePassport(passport)
                try {
                    val ownPassport = passportSnapshot(own)
                    try { ensure(ownPassport.contentEquals(canonicalPassport), Failure.SCOPE_MISMATCH) }
                    finally { ownPassport.fill(0) }
                    ensure(challenge.text("account_id") == passport.text("account_id") &&
                        hash(challenge.text("document_sha256")) == hex(sha256(canonicalPassport)), Failure.SCOPE_MISMATCH)
                    trustedIdentity(challenge, trusted)
                    nonce(challenge.text("nonce"))
                    val expires = time(challenge.text("expires_at"))
                    // Existing Android verifyChallenge policy: server TTL is 120s; client accepts
                    // strictly (now, now+180s). No issued_at exists, so do not invent one or apply
                    // negotiation's separate issued_at clock allowance to this challenge.
                    ensure(expires > now && expires < now.plusSeconds(180), Failure.EXPIRED)
                    val device = spki.copyOf()
                    val encodedDevice = try { p256(device); Base64.getEncoder().encodeToString(device) }
                    finally { device.fill(0) }
                    text(name, 120); text(version, 32); hash(refreshHash); uuid(bindingId)
                    val request = JsonObject(mapOf(
                        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
                        "platform" to JsonPrimitive(platform.name), "challenge" to challenge, "account" to passport,
                        "device_public_key_spki_b64" to JsonPrimitive(encodedDevice),
                        "device_name" to JsonPrimitive(name), "app_version" to JsonPrimitive(version),
                        "next_refresh_token_sha256" to JsonPrimitive(refreshHash),
                        "binding_commit_id" to JsonPrimitive(bindingId),
                    ))
                    val bytes = canonical(request, MAX_BYTES)
                    try {
                        // Reserve the largest canonical DER proof (72 bytes => 96 base64 chars).
                        // Thus signing cannot create an over-bound outer command.
                        ensure(bytes.size + 23 + 96 <= MAX_BYTES, Failure.TOO_LARGE)
                        PreparedRequest(purpose, platform, bytes, canonicalPassport)
                    } finally { bytes.fill(0) }
                } finally { canonicalPassport.fill(0) }
            }
        }
    }

    /**
     * Executable ROOT authorization credential. Only explicit transient copies are available for
     * future transport and separately APPROVED pending custody. NEVER DB/outbox/log/ordinary export,
     * including encrypted fallback. K1 and J remain unapproved. Reuse exactly these bytes on retry.
     * Callers own/wipe returned copies. close() gates all later access and clears only owned arrays;
     * JVM strings, external copies, JCA/provider and OS/power-loss zeroization are not promised.
     */
    class SignedCommand private constructor(wire: ByteArray) : AutoCloseable {
        private val wireBytes: ByteArray = wire.copyOf()
        private var closed: Boolean = false
        @Synchronized fun transientWireCopy(): ByteArray {
            ensure(!closed, Failure.CLOSED)
            return wireBytes.copyOf()
        }
        @Synchronized override fun close() { wireBytes.fill(0); closed = true }
        override fun toString(): String = "PortableDeviceContractV2.SignedCommand(redacted)"

        companion object {
            internal fun create(request: ByteArray, proof: ByteArray): SignedCommand {
                canonicalDer(proof)
                val base64 = Base64.getEncoder().encodeToString(proof)
                // request is already complete JCS. ASCII wrapper is exactly JCS({request,proof}).
                val wire = "{\"proof\":\"$base64\",\"request\":".toByteArray(Charsets.US_ASCII) + request + byteArrayOf(125)
                try {
                    ensure(wire.size <= MAX_BYTES, Failure.TOO_LARGE)
                    return SignedCommand(wire)
                } finally { wire.fill(0) }
            }
        }
    }

    private fun trustedIdentity(value: JsonObject, trusted: TrustedM5Identity) {
        uuid(trusted.serverInstanceId)
        ensure(trusted.identityEpoch in 1..MAX_INTEGER, Failure.INVALID_DOCUMENT)
        hash(trusted.thumbprintSha256); origin(trusted.apiOrigin); origin(trusted.streamOrigin)
        val key = trusted.publicKeySpki
        try {
            p256(key)
            ensure(hex(sha256(key)) == trusted.thumbprintSha256, Failure.IDENTITY_MISMATCH)
        } finally { key.fill(0) }
        uuid(value.text("server_instance_id")); hash(value.text("identity_thumbprint_sha256"))
        origin(value.text("api_origin")); origin(value.text("stream_origin"))
        ensure(value.text("server_instance_id") == trusted.serverInstanceId &&
            integer(value["identity_epoch"]) == trusted.identityEpoch &&
            value.text("identity_thumbprint_sha256") == trusted.thumbprintSha256 &&
            value.text("api_origin") == trusted.apiOrigin && value.text("stream_origin") == trusted.streamOrigin,
            Failure.IDENTITY_MISMATCH)
    }

    private fun passportSnapshot(document: PortableAccountDocument): ByteArray {
        // Check lexical tokens BEFORE JCS can turn 1.0/1e0 into 1, and detach caller-owned maps.
        val raw = document.fields.toString().toByteArray(Charsets.UTF_8)
        return try { validatePassport(parse(raw, 4096)) } finally { raw.fill(0) }
    }

    private fun validatePassport(value: JsonObject): ByteArray {
        exact(value, passportMembers)
        ensure(value.text("format") == "autplay-portable-account" && integer(value["version"]) == 1L &&
            value.text("kdf") == "PBKDF2-HMAC-SHA256" && integer(value["iterations"]) == 600000L,
            Failure.INVALID_DOCUMENT)
        uuid(value.text("account_id"))
        ensure(Regex("[a-z0-9_]{3,24}").matches(value.text("public_id")), Failure.INVALID_DOCUMENT)
        p256(base64(value.text("public_key_spki_b64"), 91, 91))
        base64(value.text("password_salt_b64"), 16, 16)
        base64(value.text("password_nonce_b64"), 12, 12)
        base64(value.text("recovery_nonce_b64"), 12, 12)
        base64(value.text("password_ciphertext_b64"), 80, 512).fill(0)
        base64(value.text("recovery_ciphertext_b64"), 80, 512).fill(0)
        return canonical(value, 4096)
    }

    private fun version(value: JsonObject, expected: Platform) {
        ensure(value.text("contract_version") == "v2" && integer(value["schema_version"]) == 2L,
            Failure.INVALID_DOCUMENT)
        ensure(value.text("platform") == expected.name, Failure.SCOPE_MISMATCH)
    }
    private fun exact(value: JsonObject, members: Set<String>) {
        ensure(value.keys == members, Failure.INVALID_DOCUMENT)
    }
    private fun JsonObject.text(name: String): String = (this[name] as? JsonPrimitive)
        ?.takeIf { it.isString }?.content ?: reject(Failure.INVALID_DOCUMENT)
    private fun integer(value: JsonElement?): Long {
        val token = value as? JsonPrimitive ?: reject(Failure.INVALID_DOCUMENT)
        ensure(!token.isString && Regex("[1-9][0-9]{0,15}").matches(token.content), Failure.INVALID_DOCUMENT)
        return (token.content.toLongOrNull() ?: reject(Failure.INVALID_DOCUMENT)).also {
            ensure(it in 1..MAX_INTEGER, Failure.INVALID_DOCUMENT)
        }
    }
    private fun uuid(value: String) {
        ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(value) &&
            UUID.fromString(value).toString() == value, Failure.INVALID_DOCUMENT)
    }
    private fun hash(value: String): String {
        ensure(Regex("[0-9a-f]{64}").matches(value), Failure.INVALID_DOCUMENT)
        return value
    }
    private fun text(value: String, maximum: Int) {
        unicode(value)
        ensure(value.codePointCount(0, value.length) in 1..maximum &&
            value.none { it.code < 32 || it.code == 127 }, Failure.INVALID_DOCUMENT)
    }
    private fun time(value: String): Instant {
        ensure(value.length <= 30 && Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z")
            .matches(value) && !value.startsWith("0000"), Failure.INVALID_DOCUMENT)
        return LocalDateTime.parse(value.dropLast(1)).toInstant(ZoneOffset.UTC)
    }
    private fun origin(value: String) {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 } &&
            value.none { it in "?#@\\%" }, Failure.INVALID_DOCUMENT)
        val authority = OriginAuthorityV2.parse(value) ?: reject(Failure.INVALID_DOCUMENT)
        ensure(authority.port == null || authority.port in 1..65535, Failure.INVALID_DOCUMENT)
        // Require exact canonical lower-case DNS/IPv4 or compressed lower-case IPv6 spelling.
        val host = authority.host
        if (host.startsWith("[")) {
            val literal = host.removePrefix("[").removeSuffix("]")
            val groups = literal.split("::")
            ensure(groups.size <= 2 && literal == literal.lowercase(java.util.Locale.ROOT) && '.' !in literal,
                Failure.INVALID_DOCUMENT)
            val sides = groups.map { side -> if (side.isEmpty()) emptyList() else side.split(':') }
            val words = sides.flatten()
            ensure(words.all { Regex("0|[1-9a-f][0-9a-f]{0,3}").matches(it) } &&
                (if (groups.size == 1) words.size == 8 else words.size <= 6), Failure.INVALID_DOCUMENT)
            val expanded = if (groups.size == 1) words else sides[0] + List(8 - words.size) { "0" } + sides[1]
            var bestStart = -1
            var bestSize = 1
            var start = 0
            while (start < 8) {
                if (expanded[start] != "0") { start++; continue }
                var end = start
                while (end < 8 && expanded[end] == "0") end++
                if (end - start > bestSize) { bestStart = start; bestSize = end - start }
                start = end
            }
            val canonicalHost = if (bestStart < 0) expanded.joinToString(":") else
                expanded.take(bestStart).joinToString(":") + "::" + expanded.drop(bestStart + bestSize).joinToString(":")
            ensure(literal == canonicalHost, Failure.INVALID_DOCUMENT)
        } else if (host.all { it in "0123456789." }) {
            val parts = host.split('.')
            ensure(parts.size == 4 && parts.all { part -> Regex("0|[1-9][0-9]{0,2}").matches(part) &&
                (part.toIntOrNull() ?: 256) <= 255 }, Failure.INVALID_DOCUMENT)
        } else {
            ensure(host.length <= 253 && host.removeSuffix(".").split('.').all {
                Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?").matches(it)
            }, Failure.INVALID_DOCUMENT)
        }
        ensure(value == authority.scheme + "://" + host +
            (if (authority.port == null) "" else ":${authority.port}"), Failure.INVALID_DOCUMENT)
    }

    private fun base64(value: String, min: Int, max: Int): ByteArray {
        ensure(value.length <= ((max + 2) / 3) * 4, Failure.INVALID_DOCUMENT)
        val bytes = Base64.getDecoder().decode(value)
        if (bytes.size !in min..max || Base64.getEncoder().encodeToString(bytes) != value) {
            bytes.fill(0); reject(Failure.INVALID_DOCUMENT)
        }
        return bytes
    }
    private fun nonce(value: String) {
        ensure(Regex("[A-Za-z0-9_-]{43}").matches(value), Failure.INVALID_DOCUMENT)
        val bytes = Base64.getUrlDecoder().decode(value)
        try { ensure(bytes.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) == value,
            Failure.INVALID_DOCUMENT) } finally { bytes.fill(0) }
    }

    private fun p256(spki: ByteArray): ECPublicKey {
        val prefix = byteArrayOf(0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86.toByte(), 0x48,
            0xce.toByte(), 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86.toByte(), 0x48, 0xce.toByte(),
            0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00, 0x04)
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

    private fun canonicalDer(proof: ByteArray) {
        ensure(proof.size in 8..72 && proof[0] == 0x30.toByte() &&
            (proof[1].toInt() and 255) == proof.size - 2, Failure.INVALID_DOCUMENT)
        val order = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java).order
        var cursor = 2
        repeat(2) {
            ensure(cursor + 2 <= proof.size && proof[cursor++] == 2.toByte(), Failure.INVALID_DOCUMENT)
            val size = proof[cursor++].toInt() and 255
            ensure(size in 1..33 && cursor + size <= proof.size, Failure.INVALID_DOCUMENT)
            val encoded = proof.copyOfRange(cursor, cursor + size)
            val integer = BigInteger(encoded)
            ensure(integer.signum() > 0 && integer < order && integer.toByteArray().contentEquals(encoded),
                Failure.INVALID_DOCUMENT)
            cursor += size
        }
        ensure(cursor == proof.size, Failure.INVALID_DOCUMENT) // High-S remains valid; no rewrite.
    }

    private fun parse(input: ByteArray, maximum: Int): JsonObject {
        ensure(input.size in 2..maximum, if (input.size > maximum) Failure.TOO_LARGE else Failure.INVALID_DOCUMENT)
        val bytes = input.copyOf()
        try {
            val raw = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
            val result = StrictJson(raw).parse() as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
            canonical(result, maximum).fill(0)
            return result
        } finally { bytes.fill(0) }
    }
    private fun canonical(value: JsonObject, maximum: Int): ByteArray {
        val bytes = JsonCanonicalizer(value.toString()).encodedString.toByteArray(Charsets.UTF_8)
        if (bytes.size !in 2..maximum) { bytes.fill(0); reject(Failure.TOO_LARGE) }
        return bytes
    }
    private fun sha256(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
    private fun hex(value: ByteArray): String = value.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun ensure(condition: Boolean, failure: Failure) { if (!condition) reject(failure) }
    private fun reject(failure: Failure): Nothing = throw Rejected(failure)
    private inline fun <T> guarded(block: () -> T): T = try { block() }
        catch (failure: Rejected) { throw failure }
        catch (_: Exception) { reject(Failure.INVALID_DOCUMENT) }
    private fun unicode(value: String) {
        var cursor = 0
        while (cursor < value.length) {
            val character = value[cursor++]
            if (character.isHighSurrogate()) {
                ensure(cursor < value.length && value[cursor++].isLowSurrogate(), Failure.INVALID_DOCUMENT)
            } else ensure(!character.isLowSurrogate(), Failure.INVALID_DOCUMENT)
        }
    }

    /** Object-only bounded models; reject duplicates after unescaping and numbers BEFORE JCS. */
    private class StrictJson(private val raw: String) {
        private var cursor = 0
        fun parse(): JsonElement {
            val result = value(0)
            whitespace()
            ensure(cursor == raw.length, Failure.INVALID_DOCUMENT)
            return result
        }
        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(character: Char): Boolean {
            whitespace()
            return (cursor < raw.length && raw[cursor] == character).also { if (it) cursor++ }
        }
        private fun value(depth: Int): JsonElement {
            whitespace()
            ensure(cursor < raw.length && depth <= 4, Failure.INVALID_DOCUMENT)
            return when (raw[cursor]) {
                '{' -> {
                    ensure(depth < 4, Failure.INVALID_DOCUMENT)
                    cursor++
                    val fields = linkedMapOf<String, JsonElement>()
                    if (!take('}')) {
                        do {
                            whitespace()
                            val key = quoted()
                            ensure(key !in fields && take(':'), Failure.INVALID_DOCUMENT)
                            val start = cursor
                            fields[key] = value(depth + 1)
                            if (key == "account") {
                                val passportRaw = raw.substring(start, cursor).toByteArray(Charsets.UTF_8)
                                try { ensure(passportRaw.size <= 4096, Failure.TOO_LARGE) }
                                finally { passportRaw.fill(0) }
                            }
                        } while (take(','))
                        ensure(take('}'), Failure.INVALID_DOCUMENT)
                    }
                    JsonObject(fields)
                }
                '"' -> JsonPrimitive(quoted())
                else -> {
                    val start = cursor
                    while (cursor < raw.length && raw[cursor] !in " \t\r\n,]}:") cursor++
                    val token = raw.substring(start, cursor)
                    if (token == "null") JsonNull else {
                        ensure(Regex("0|[1-9][0-9]{0,15}").matches(token) &&
                            (token.toLongOrNull() ?: -1) in 0..MAX_INTEGER, Failure.INVALID_DOCUMENT)
                        JsonPrimitive(token.toLong())
                    }
                }
            }
        }
        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"', Failure.INVALID_DOCUMENT)
            val start = cursor++
            var closed = false
            while (cursor < raw.length) {
                val character = raw[cursor++]
                if (character == '"') { closed = true; break }
                ensure(character.code >= 32, Failure.INVALID_DOCUMENT)
                if (character == '\\') {
                    ensure(cursor < raw.length, Failure.INVALID_DOCUMENT)
                    val escape = raw[cursor++]
                    ensure(escape in "\"\\/bfnrtu", Failure.INVALID_DOCUMENT)
                    if (escape == 'u') {
                        ensure(cursor + 4 <= raw.length && raw.substring(cursor, cursor + 4)
                            .all { it in "0123456789abcdefABCDEF" }, Failure.INVALID_DOCUMENT)
                        cursor += 4
                    }
                }
            }
            ensure(closed, Failure.INVALID_DOCUMENT)
            val value = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            unicode(value)
            return value
        }
    }

    private val versionMembers = setOf("contract_version", "schema_version", "platform")
    private val nullMembers = setOf("actor_device_id", "actor_session_id", "action", "operation_id", "target_device_id")
    private val challengeMembers = versionMembers + nullMembers + setOf("server_instance_id", "identity_epoch",
        "identity_thumbprint_sha256", "api_origin", "stream_origin", "challenge_id", "purpose", "account_id",
        "document_sha256", "nonce", "expires_at")
    private val passportMembers = setOf("format", "version", "account_id", "public_id", "public_key_spki_b64",
        "kdf", "iterations", "password_salt_b64", "password_nonce_b64", "password_ciphertext_b64",
        "recovery_nonce_b64", "recovery_ciphertext_b64")
}
