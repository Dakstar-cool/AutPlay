package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.portableaccount.PortableAccountCrypto
import app.autplay.application.portableaccount.PortableAccountDocument
import app.autplay.application.portableaccount.PortableDeviceContractV2 as Codec
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * FUTURE source-only assertions inside the FIRST existing persistence composite; no new test entry.
 * Real account creation/unlock, hashes and signatures happen only when explicitly authorized later.
 * The independent sorted serializer uses only strings/null/lexical integers from these fixtures;
 * no production canonicalizer or computed golden literals supply the oracle. This is not executed
 * interoperability, transport, Mac custody, persistence, rollout evidence or a matrix A-row point.
 */
internal fun desktopPortableDeviceV2Assertions(): Unit {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val accountId = "22222222-2222-4222-8222-222222222222"
    val instance = "11111111-1111-4111-8111-111111111111"
    val challengeId = "33333333-3333-4333-8333-333333333333"
    val bindingId = "44444444-4444-4444-8444-444444444444"
    val otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    val password = "portable account strong password".toCharArray()
    fun key(curve: String = "secp256r1") = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec(curve)); generateKeyPair()
    }
    fun sha(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
    fun hex(value: ByteArray): String = value.joinToString("") { "%02x".format(it.toInt() and 255) }
    fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    fun sorted(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + sorted(it.value)
        }
        else -> value.toString()
    }
    fun bytes(value: JsonElement): ByteArray = sorted(value).toByteArray(Charsets.UTF_8)
    fun obj(value: JsonElement?): JsonObject = value as JsonObject
    fun text(value: JsonElement?): String = (value as JsonPrimitive).content
    fun change(value: JsonObject, name: String, field: JsonElement): JsonObject = JsonObject(value + (name to field))
    fun rejected(expected: Codec.Failure, block: () -> Unit) {
        val failure = runCatching(block).exceptionOrNull()
        assertTrue("expected redacted $expected", failure is Codec.Rejected)
        assertEquals(expected, (failure as Codec.Rejected).failure)
        assertNull(failure.cause)
        assertTrue(failure.suppressed.isEmpty())
    }
    fun fails(block: () -> Unit) { assertTrue(runCatching(block).isFailure) }
    val identityKey = key()
    val deviceKey = key()
    val identityThumb = hex(sha(identityKey.public.encoded))
    val refreshHash = hex(sha("future-caller-chosen-refresh-input".toByteArray(Charsets.US_ASCII)))
    fun trusted(
        id: String = instance, epoch: Long = 1, thumb: String = identityThumb,
        origin: String = api, streamOrigin: String = stream, spki: ByteArray = identityKey.public.encoded,
    ): TrustedM5Identity = TrustedM5Identity(id, epoch, spki, thumb, origin, streamOrigin)
    val created = PortableAccountCrypto.create("portable_user", password, accountId)
    val document = created.document
    val rootKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(document.publicKeySpki))
    fun fixture(
        purpose: Codec.Purpose = Codec.Purpose.REGISTER, platform: Codec.Platform = Codec.Platform.MACOS,
        passport: JsonObject = document.fields,
    ): JsonObject {
        val challenge = JsonObject(mapOf(
            "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "platform" to JsonPrimitive(platform.name), "purpose" to JsonPrimitive(purpose.name),
            "challenge_id" to JsonPrimitive(challengeId), "account_id" to passport.getValue("account_id"),
            "document_sha256" to JsonPrimitive(hex(sha(bytes(passport)))),
            "server_instance_id" to JsonPrimitive(instance), "identity_epoch" to JsonPrimitive(1),
            "identity_thumbprint_sha256" to JsonPrimitive(identityThumb), "api_origin" to JsonPrimitive(api),
            "stream_origin" to JsonPrimitive(stream),
            "nonce" to JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 7 })),
            "expires_at" to JsonPrimitive(now.plusSeconds(120).toString()),
            "actor_device_id" to JsonNull, "actor_session_id" to JsonNull, "action" to JsonNull,
            "operation_id" to JsonNull, "target_device_id" to JsonNull,
        ))
        return JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "platform" to JsonPrimitive(platform.name), "challenge" to challenge, "account" to passport))
    }
    fun changedChallenge(response: JsonObject, name: String, value: JsonElement): JsonObject =
        change(response, "challenge", change(obj(response["challenge"]), name, value))
    fun prepare(
        response: JsonObject = fixture(), trust: TrustedM5Identity = trusted(),
        purpose: Codec.Purpose = Codec.Purpose.REGISTER, platform: Codec.Platform = Codec.Platform.MACOS,
        own: PortableAccountDocument = document, spki: ByteArray = deviceKey.public.encoded,
        name: String = "My device", version: String = "1.0", refresh: String = refreshHash,
        binding: String = bindingId, raw: ByteArray = bytes(response), clock: Instant = now,
    ): Codec.PreparedRequest = Codec.prepare(raw, trust, platform, purpose, own, spki, name, version, refresh, binding, clock)
    fun verify(signature: ByteArray, prefix: String, payload: ByteArray): Boolean =
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(rootKey); update(prefix.toByteArray(Charsets.US_ASCII)); update(payload); verify(signature)
        }
    fun domain(purpose: Codec.Purpose, version: Int = 2): String = when (purpose) {
        Codec.Purpose.REGISTER -> "autplay:portable-account:register:v$version\n"
        Codec.Purpose.LOGIN -> "autplay:portable-account:login:v$version\n"
    }
    fun derIntegers(der: ByteArray): Pair<BigInteger, BigInteger> {
        var cursor = 0
        assertEquals(0x30, der[cursor++].toInt() and 255)
        assertEquals(der.size - 2, der[cursor++].toInt() and 255)
        fun integer(): BigInteger {
            assertEquals(2, der[cursor++].toInt() and 255)
            val size = der[cursor++].toInt() and 255
            assertTrue(size in 1..33)
            val bytes = der.copyOfRange(cursor, cursor + size)
            cursor += size
            val value = BigInteger(bytes)
            assertTrue(value.signum() > 0)
            assertArrayEquals(value.toByteArray(), bytes)
            return value
        }
        val result = integer() to integer()
        assertEquals(der.size, cursor)
        return result
    }
    try {
        PortableAccountCrypto.unlock(document, password).use { unlocked ->
            for (purpose in Codec.Purpose.entries) for (platform in Codec.Platform.entries)
                for (field in listOf("api_origin", "stream_origin")) {
                    for (origin in listOf("https://node.123", "https://node.1a", "https://node.123.",
                        "https://192.0.2.1:65535", "https://[::ffff:c000:201]")) {
                        val trust = trusted(origin = if (field == "api_origin") origin else api,
                            streamOrigin = if (field == "stream_origin") origin else stream)
                        val response = changedChallenge(fixture(purpose, platform), field, JsonPrimitive(origin))
                        prepare(response, trust, purpose, platform).use { prepared ->
                            val canonical = prepared.transientCanonicalRequestCopy()
                            val request = obj(Json.parseToJsonElement(canonical.toString(Charsets.UTF_8)))
                            assertTrue(obj(request["challenge"])[field] == JsonPrimitive(origin))
                            assertArrayEquals(document.encode(), bytes(obj(request["account"])))
                            unlocked.signDeviceContractV2(prepared).use { signed ->
                                val wire = signed.transientWireCopy()
                                try {
                                    val outer = obj(Json.parseToJsonElement(wire.toString(Charsets.UTF_8)))
                                    assertArrayEquals(canonical, bytes(outer.getValue("request")))
                                    assertTrue(verify(Base64.getDecoder().decode(text(outer["proof"])), domain(purpose), canonical))
                                } finally { wire.fill(0) }
                            }
                            canonical.fill(0)
                        }
                        rejected(Codec.Failure.IDENTITY_MISMATCH) { prepare(response, purpose = purpose, platform = platform).close() }
                    }
                    for (origin in listOf("https://NODE.123", "https://node.123:00443", "https://node.123:0",
                        "https://node.123:65536", "https://node.123:", "https://-node.123", "https://node-.123", "https://node..123",
                        "https://${"a".repeat(64)}.123", "https://123.456", "https://192.000.2.1", "https://[::ffff:192.0.2.1]",
                        "https://[2001:0db8::1]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
                        val trust = trusted(origin = if (field == "api_origin") origin else api,
                            streamOrigin = if (field == "stream_origin") origin else stream)
                        val response = changedChallenge(fixture(purpose, platform), field, JsonPrimitive(origin))
                        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(response, trust, purpose, platform).close() }
                    }
                }
            for (purpose in Codec.Purpose.entries) for (platform in Codec.Platform.entries) {
                val response = fixture(purpose, platform)
                val raw = bytes(response)
                val spki = deviceKey.public.encoded.copyOf()
                prepare(response, purpose = purpose, platform = platform, raw = raw, spki = spki).use { prepared ->
                    raw.fill(0); spki.fill(0)
                    val canonical = prepared.transientCanonicalRequestCopy()
                    val request = obj(Json.parseToJsonElement(canonical.toString(Charsets.UTF_8)))
                    val expected = JsonObject(mapOf(
                        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
                        "platform" to JsonPrimitive(platform.name), "challenge" to response.getValue("challenge"),
                        "account" to response.getValue("account"),
                        "device_public_key_spki_b64" to JsonPrimitive(b64(deviceKey.public.encoded)),
                        "device_name" to JsonPrimitive("My device"), "app_version" to JsonPrimitive("1.0"),
                        "next_refresh_token_sha256" to JsonPrimitive(refreshHash),
                        "binding_commit_id" to JsonPrimitive(bindingId),
                    ))
                    assertEquals(expected, request)
                    assertArrayEquals(bytes(expected), canonical)
                    assertArrayEquals(document.encode(), bytes(obj(request["account"])))
                    assertFalse(request.containsKey("proof"))
                    assertEquals(purpose, prepared.purpose); assertEquals(platform, prepared.platform)
                    assertEquals("PortableDeviceContractV2.PreparedRequest(redacted)", prepared.toString())
                    val requestCopy = prepared.transientCanonicalRequestCopy()
                    requestCopy.fill(0)
                    assertArrayEquals(canonical, prepared.transientCanonicalRequestCopy())
                    unlocked.signDeviceContractV2(prepared).use { signed ->
                        val wire = signed.transientWireCopy()
                        val outer = obj(Json.parseToJsonElement(wire.toString(Charsets.UTF_8)))
                        assertEquals(setOf("request", "proof"), outer.keys)
                        assertEquals(request, outer["request"])
                        assertArrayEquals(bytes(outer), wire)
                        assertTrue(wire.size <= Codec.MAX_BYTES && canonical.size <= Codec.MAX_BYTES)
                        val proofText = text(outer["proof"])
                        val proof = Base64.getDecoder().decode(proofText)
                        assertEquals(b64(proof), proofText)
                        assertTrue(proof.size in 8..72)
                        val (r, s) = derIntegers(proof)
                        assertTrue(verify(proof, domain(purpose), canonical))
                        assertFalse(verify(proof, domain(purpose, 1), canonical))
                        assertFalse(verify(proof, domain(if (purpose == Codec.Purpose.LOGIN)
                            Codec.Purpose.REGISTER else Codec.Purpose.LOGIN), canonical))
                        assertFalse(verify(proof, domain(purpose), sha(canonical))) // Not prehash.
                        for ((field, replacement) in listOf(
                            "platform" to JsonPrimitive(if (platform == Codec.Platform.MACOS) "ANDROID" else "MACOS"),
                            "binding_commit_id" to JsonPrimitive(otherId), "device_name" to JsonPrimitive("Changed"),
                            "app_version" to JsonPrimitive("2.0"), "next_refresh_token_sha256" to JsonPrimitive("0".repeat(64)),
                            "device_public_key_spki_b64" to JsonPrimitive(b64(identityKey.public.encoded)),
                            "account" to change(document.fields, "public_id", JsonPrimitive("changed_user")),
                            "challenge" to change(obj(request["challenge"]), "nonce",
                                JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 8 }))),
                        )) assertFalse(verify(proof, domain(purpose), bytes(change(request, field, replacement))))
                        // Independent high-S DER remains a valid MESSAGE signature; no low-S rewrite.
                        val order = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
                            .getParameterSpec(ECParameterSpec::class.java).order
                        val high = (if (s > order.shiftRight(1)) s else order.subtract(s)).toByteArray()
                        val rBytes = r.toByteArray()
                        val highDer = byteArrayOf(0x30, (4 + rBytes.size + high.size).toByte(), 2, rBytes.size.toByte()) +
                            rBytes + byteArrayOf(2, high.size.toByte()) + high
                        assertTrue(verify(highDer, domain(purpose), canonical))
                        // Old method retains the exact v1 prefix even for this v2-shaped payload.
                        val legacyPurpose = if (purpose == Codec.Purpose.LOGIN) "login" else "register"
                        val legacy = unlocked.sign(legacyPurpose, canonical)
                        assertTrue(verify(legacy, domain(purpose, 1), canonical))
                        assertFalse(verify(legacy, domain(purpose), canonical))
                        fails { unlocked.sign("${legacyPurpose}:v2", canonical) }
                        assertEquals("PortableDeviceContractV2.SignedCommand(redacted)", signed.toString())
                        val expectedWire = wire.copyOf()
                        wire.fill(0)
                        assertArrayEquals(expectedWire, signed.transientWireCopy())
                        assertArrayEquals(expectedWire, signed.transientWireCopy()) // Exact retry, no signing.
                        rejected(Codec.Failure.ALREADY_SIGNED) { unlocked.signDeviceContractV2(prepared) }
                        prepared.close()
                        rejected(Codec.Failure.CLOSED) { prepared.transientCanonicalRequestCopy() }
                        assertArrayEquals(expectedWire, signed.transientWireCopy()) // Separate owned buffers.
                        signed.close(); signed.close()
                        rejected(Codec.Failure.CLOSED) { signed.transientWireCopy() }
                        proof.fill(0); legacy.fill(0); expectedWire.fill(0)
                    }
                    canonical.fill(0)
                }
            }
            // New signer must own the EXACT encrypted passport, not only its account ID/root.
            val changedPassport = change(document.fields, "password_nonce_b64", JsonPrimitive(b64(ByteArray(12) { 4 })))
            val changedDocument = PortableAccountDocument.parse(bytes(changedPassport))
            prepare(fixture(passport = changedPassport), own = changedDocument).use {
                rejected(Codec.Failure.SCOPE_MISMATCH) { unlocked.signDeviceContractV2(it) }
            }
            PortableAccountCrypto.create("portable_other", password, accountId).use { other ->
                prepare().use { prepared -> PortableAccountCrypto.unlock(other.document, password).use { wrongRoot ->
                    rejected(Codec.Failure.SCOPE_MISMATCH) { wrongRoot.signDeviceContractV2(prepared) }
                } }
            }
            prepare().use { it.close(); rejected(Codec.Failure.CLOSED) { unlocked.signDeviceContractV2(it) } }
        }
        val closedRoot = PortableAccountCrypto.unlock(document, password)
        closedRoot.close()
        prepare().use { fails { closedRoot.signDeviceContractV2(it) } }

        val valid = fixture()
        for (identity in listOf(trusted(id = otherId), trusted(epoch = 2),
            trusted(origin = "https://other.example.invalid"), trusted(streamOrigin = "https://other.example.invalid"))) {
            rejected(Codec.Failure.IDENTITY_MISMATCH) { prepare(trust = identity) }
        }
        val otherIdentityKey = key()
        rejected(Codec.Failure.IDENTITY_MISMATCH) {
            prepare(trust = trusted(thumb = hex(sha(otherIdentityKey.public.encoded)), spki = otherIdentityKey.public.encoded))
        }
        rejected(Codec.Failure.IDENTITY_MISMATCH) { prepare(trust = trusted(thumb = "0".repeat(64))) }
        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(trust = trusted(spki = key("secp384r1").public.encoded)) }
        for (field in listOf("server_instance_id", "identity_epoch", "identity_thumbprint_sha256", "api_origin", "stream_origin")) {
            val replacement = when (field) {
                "server_instance_id" -> JsonPrimitive(otherId)
                "identity_epoch" -> JsonPrimitive(2)
                "identity_thumbprint_sha256" -> JsonPrimitive("0".repeat(64))
                else -> JsonPrimitive("https://other.example.invalid")
            }
            rejected(Codec.Failure.IDENTITY_MISMATCH) { prepare(changedChallenge(valid, field, replacement)) }
        }
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(platform = Codec.Platform.ANDROID) }
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(purpose = Codec.Purpose.LOGIN) }
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(changedChallenge(valid, "platform", JsonPrimitive("ANDROID"))) }
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(changedChallenge(valid, "account_id", JsonPrimitive(otherId))) }
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(changedChallenge(valid, "document_sha256", JsonPrimitive("0".repeat(64)))) }
        // Recompute each passport's digest so stale hash cannot mask its intended validation check.
        val alternatePassport = change(document.fields, "public_id", JsonPrimitive("other_user"))
        rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(fixture(passport = alternatePassport)) }
        for (passport in listOf(
            change(document.fields, "account_id", JsonPrimitive(otherId)),
            change(document.fields, "public_key_spki_b64", JsonPrimitive(b64(otherIdentityKey.public.encoded))),
        )) rejected(Codec.Failure.SCOPE_MISMATCH) { prepare(fixture(passport = passport)) }
        for (expires in listOf(now.minusNanos(1), now, now.plusSeconds(180), now.plusSeconds(3600))) {
            rejected(Codec.Failure.EXPIRED) { prepare(changedChallenge(valid, "expires_at", JsonPrimitive(expires.toString()))) }
        }
        for (expires in listOf(now.plusNanos(1), now.plusSeconds(179).plusNanos(999999999))) {
            prepare(changedChallenge(valid, "expires_at", JsonPrimitive(expires.toString()))).close()
        }
        for (expires in listOf("2026-02-30T09:00:00Z", "2026-10-16T24:00:00Z", "2026-10-16T09:00:60Z",
            "2026-10-16T09:00:01+00:00", "2026-10-16T09:00:01.1234567890Z", "0000-01-01T00:00:00Z")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(changedChallenge(valid, "expires_at", JsonPrimitive(expires))) }
        }
        for (member in listOf("actor_device_id", "actor_session_id", "action", "operation_id", "target_device_id")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(changedChallenge(valid, member, JsonPrimitive(otherId))) }
            rejected(Codec.Failure.INVALID_DOCUMENT) {
                prepare(change(valid, "challenge", JsonObject(obj(valid["challenge"]) - member)))
            }
        }
        for (bad in listOf(change(valid, "extra", JsonNull), JsonObject(valid - "account"),
            changedChallenge(valid, "extra", JsonNull), change(valid, "schema_version", JsonPrimitive(1)),
            change(valid, "contract_version", JsonPrimitive("v1")))) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(bad) }
        }
        for (field in listOf("challenge_id", "account_id", "server_instance_id")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) {
                prepare(changedChallenge(valid, field, JsonPrimitive("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")))
            }
        }
        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(binding = "1-1-1-1-1") }
        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(refresh = "A".repeat(64)) }
        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(refresh = "0".repeat(63)) }
        val canonicalNonce = text(obj(valid["challenge"])["nonce"])
        for (badNonce in listOf(canonicalNonce + "=", canonicalNonce.dropLast(1) + "d", "?".repeat(43))) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(changedChallenge(valid, "nonce", JsonPrimitive(badNonce))) }
        }
        for (badOrigin in listOf(api + "/", api + "/path", api + "?x", api + "#x", "https://u@api.example.invalid",
            "https://API.example.invalid", "https://api.example.invalid:0443", "https://api.example.invalid:",
            "https://api.example.invalid:0", "https://127.000.0.1", "https://[0:0:0:0:0:0:0:1]",
            "https://api.example.invalid%2f", "https://api.example.invalid ", "https://\u0430pi.example.invalid")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(changedChallenge(valid, "api_origin", JsonPrimitive(badOrigin))) }
        }
        for (origin in listOf("https://[::1]", "https://[2001:db8::1]:443", "https://127.0.0.1:443")) {
            prepare(changedChallenge(valid, "api_origin", JsonPrimitive(origin)), trust = trusted(origin = origin)).close()
        }
        for (name in listOf("", "x".repeat(121), "line\nname", "nul\u0000", "del\u007f", "bad\ud800")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(name = name) }
        }
        for (version in listOf("", "x".repeat(33), "v\t1", "bad\udfff")) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(version = version) }
        }
        val unicodeName = "  \u00e9 e\u0301 \ud83c\udfb5  "
        prepare(name = unicodeName, version = "\ud83c\udfb5".repeat(32)).use {
            val request = obj(Json.parseToJsonElement(it.transientCanonicalRequestCopy().toString(Charsets.UTF_8)))
            assertEquals(unicodeName, text(request["device_name"])) // Neither trim nor Unicode normalization.
        }
        for (badKey in listOf(ByteArray(91), deviceKey.public.encoded + byteArrayOf(0), key("secp384r1").public.encoded,
            deviceKey.public.encoded.copyOf().also { it[26] = 0 })) {
            rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(spki = badKey) }
        }
        for ((member, bad) in listOf(
            "version" to JsonPrimitive(2), "iterations" to JsonPrimitive(599999), "kdf" to JsonPrimitive("PBKDF2"),
            "public_id" to JsonPrimitive("UPPER"), "account_id" to JsonPrimitive("1-1-1-1-1"),
            "public_key_spki_b64" to JsonPrimitive(b64(key("secp384r1").public.encoded)),
            "public_key_spki_b64" to JsonPrimitive(b64(deviceKey.public.encoded + byteArrayOf(0))),
            "password_salt_b64" to JsonPrimitive(b64(ByteArray(15))),
            "password_nonce_b64" to JsonPrimitive(b64(ByteArray(11))),
            "recovery_nonce_b64" to JsonPrimitive(b64(ByteArray(13))),
            "password_ciphertext_b64" to JsonPrimitive(b64(ByteArray(79))),
            "recovery_ciphertext_b64" to JsonPrimitive(b64(ByteArray(513))),
            "extra" to JsonNull,
        )) rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(fixture(passport = change(document.fields, member, bad))) }
        for (member in listOf("password_salt_b64", "password_ciphertext_b64", "recovery_ciphertext_b64")) {
            val original = b64(ByteArray(if (member == "password_salt_b64") 16 else 80))
            // JDK's old v1 decoder accepts some omitted padding; D1 must round-trip exactly.
            val bad = original.trimEnd('=')
            assertNotEquals(original, bad)
            rejected(Codec.Failure.INVALID_DOCUMENT) {
                prepare(fixture(passport = change(document.fields, member, JsonPrimitive(bad))))
            }
        }
        val raw = sorted(valid)
        for ((member, tokens) in listOf("schema_version" to listOf("2.0", "2e0", "true", "\"2\"", "-0"),
            "identity_epoch" to listOf("1.0", "1e0", "false", "9007199254740992", "-1"),
            "version" to listOf("1.0", "1e0", "true"), "iterations" to listOf("600000.0", "6e5", "true"))) {
            val original = when (member) { "schema_version" -> "2"; "iterations" -> "600000"; else -> "1" }
            for (token in tokens) {
                val malformed = raw.replace("\"$member\":$original", "\"$member\":$token")
                assertNotEquals(raw, malformed)
                rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(raw = malformed.toByteArray(Charsets.UTF_8)) }
            }
        }
        for (malformed in listOf(
            "{\"schema_version\":2," + raw.drop(1),
            raw.replace("\"nonce\":", "\"nonce\":\"$canonicalNonce\",\"no\\u006ece\":"),
            raw.replace("\"format\":", "\"format\":\"autplay-portable-account\",\"format\":"),
            raw.replace("\"public_id\":\"portable_user\"", "\"public_id\":\"\\ud800\""),
            raw.replace("\"public_id\":\"portable_user\"", "\"public_id\":\"\\udfff\""),
            raw.replace("\"platform\":\"MACOS\"", "\"platform\":\"MACOS\u0001\""),
            raw + "{}", "{\"extra\":{\"a\":{\"b\":{\"c\":{}}}}}",
        )) rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(raw = malformed.toByteArray(Charsets.UTF_8)) }
        rejected(Codec.Failure.INVALID_DOCUMENT) { prepare(raw = byteArrayOf(0xc3.toByte(), 0x28)) }
        rejected(Codec.Failure.TOO_LARGE) { prepare(raw = ByteArray(Codec.MAX_BYTES + 1) { 32 }) }
        // Valid bytes at the outer raw ceiling; one extra whitespace byte must fail before parsing.
        val atLimit = bytes(valid).let { it + ByteArray(Codec.MAX_BYTES - it.size) { 32 } }
        prepare(raw = atLimit).close()
        rejected(Codec.Failure.TOO_LARGE) { prepare(raw = atLimit + byteArrayOf(32)) }
        // Raw passport bound applies even if whitespace disappears during JCS.
        val expanded = raw.replace("\"account\":{", "\"account\":{" + " ".repeat(4096))
        rejected(Codec.Failure.TOO_LARGE) { prepare(raw = expanded.toByteArray(Charsets.UTF_8)) }
        assertEquals("DeviceContractV2.TrustedM5Identity", trusted().toString())
    } finally { created.close(); password.fill('\u0000') }
}
