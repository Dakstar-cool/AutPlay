package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as Codec
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Failure
import java.math.BigInteger
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * Future assertions, invoked only inside the FIRST existing persistence composite. No new @Test.
 * Keys, hashes and real signatures are generated only when that composite eventually runs.
 * Valid fixtures use ASCII strings and lexical integers; their independent sorted serializer is
 * RFC8785 for this restricted subset. Invalid numeric tokens must fail BEFORE canonicalization.
 * Invalid Unicode origins stay unnormalized. Production uses the pinned full JCS implementation.
 */
internal fun desktopDeviceContractV2Assertions(): Unit {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val instance = "11111111-1111-4111-8111-111111111111"
    val user = "22222222-2222-4222-8222-222222222222"
    val device = "33333333-3333-4333-8333-333333333333"
    val otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    val discoveryDomain = "autplay:device-compatibility:negotiation:discovery:v2\n"
    val capabilityDomain = "autplay:device-compatibility:negotiation:capabilities:v2\n"
    fun key(curve: String = "secp256r1"): KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec(curve)); generateKeyPair()
    }
    fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
    fun url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    fun asciiCanonical(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + asciiCanonical(it.value)
        }
        is JsonArray -> value.joinToString(",", "[", "]") { asciiCanonical(it) }
        else -> value.toString()
    }
    fun bytes(value: JsonElement): ByteArray = asciiCanonical(value).toByteArray(Charsets.UTF_8)
    fun change(value: JsonObject, name: String, field: JsonElement): JsonObject = JsonObject(value + (name to field))
    fun strings(vararg values: String): JsonArray = JsonArray(values.map { JsonPrimitive(it) })
    fun <T> verified(result: Codec.Result<T>): T {
        assertTrue("expected verified, got $result", result is Codec.Result.Verified)
        return (result as Codec.Result.Verified<T>).value
    }
    fun refused(reason: Failure, result: Codec.Result<*>) {
        assertTrue("expected $reason, got $result", result is Codec.Result.Rejected)
        assertEquals(reason, (result as Codec.Result.Rejected).failure)
    }
    val pair = key()
    val originalSpki = pair.public.encoded
    val inputSpki = originalSpki.copyOf()
    val thumbprint = hex(sha(originalSpki))
    val trusted = Codec.TrustedM5Identity(instance, 1, inputSpki, thumbprint, api, stream)
    inputSpki.fill(0)
    trusted.publicKeySpki.fill(0)
    assertArrayEquals(originalSpki, trusted.publicKeySpki)
    val scope = Codec.CapabilityScope(user, device, Codec.Platform.MACOS)
    fun identity(
        id: String = instance, epoch: Long = 1, spki: ByteArray = originalSpki,
        fingerprint: String = thumbprint, apiOrigin: String = api, streamOrigin: String = stream,
    ): Codec.TrustedM5Identity = Codec.TrustedM5Identity(id, epoch, spki, fingerprint, apiOrigin, streamOrigin)
    fun payload(capabilities: Boolean = false): JsonObject {
        val fields = linkedMapOf<String, JsonElement>(
            "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "server_instance_id" to JsonPrimitive(instance), "identity_epoch" to JsonPrimitive(1),
            "identity_public_key_spki_b64" to JsonPrimitive(b64(originalSpki)),
            "identity_thumbprint_sha256" to JsonPrimitive(thumbprint),
            "api_origin" to JsonPrimitive(api), "stream_origin" to JsonPrimitive(stream),
            "api_major" to JsonPrimitive(1), "families" to strings("self", "session"),
            "issued_at" to JsonPrimitive(now.toString()),
            "expires_at" to JsonPrimitive(now.plusSeconds(if (capabilities) 3600 else 600).toString()),
        )
        if (capabilities) {
            fields["user_id"] = JsonPrimitive(user)
            fields["device_id"] = JsonPrimitive(device)
            fields["platform"] = JsonPrimitive("MACOS")
            fields["capability_revision"] = JsonPrimitive(7)
        } else fields["platforms"] = strings("ANDROID", "MACOS")
        return JsonObject(fields)
    }
    fun rawSignature(der: ByteArray): ByteArray {
        // JCA ECDSA DER output is a short SEQUENCE of two positive INTEGERs for P-256.
        var cursor = 0
        assertEquals(0x30, der[cursor++].toInt() and 255)
        assertEquals(der.size - 2, der[cursor++].toInt() and 255)
        fun integer(): ByteArray {
            assertEquals(2, der[cursor++].toInt() and 255)
            val size = der[cursor++].toInt() and 255
            val number = BigInteger(der.copyOfRange(cursor, cursor + size))
            cursor += size
            val magnitude = number.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            assertTrue(number.signum() > 0 && magnitude.size <= 32)
            return ByteArray(32 - magnitude.size) + magnitude
        }
        val result = integer() + integer()
        assertEquals(der.size, cursor)
        return result
    }
    fun frame(
        data: JsonObject, domain: String, signer: KeyPair = pair,
        recipe: String = "message", derWire: Boolean = false,
    ): JsonObject {
        val digest = sha(bytes(data))
        val message = when (recipe) {
            "hex" -> domain.toByteArray(Charsets.US_ASCII) + hex(digest).toByteArray(Charsets.US_ASCII)
            "portable" -> "autplay:portable-account:login:v2\n".toByteArray(Charsets.US_ASCII) + bytes(data)
            "prehash" -> digest
            else -> domain.toByteArray(Charsets.US_ASCII) + digest
        }
        val der = Signature.getInstance(if (recipe == "prehash") "NONEwithECDSA" else "SHA256withECDSA").run {
            initSign(signer.private); update(message); sign()
        }
        return JsonObject(mapOf(
            "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "payload" to data, "payload_sha256" to JsonPrimitive(hex(digest)),
            "signature_algorithm" to JsonPrimitive("ES256-P1363"),
            "signature_b64url" to JsonPrimitive(url(if (derWire) der else rawSignature(der))),
        ))
    }
    fun discover(data: JsonObject, trust: Codec.TrustedM5Identity = trusted): Codec.Result<Codec.Discovery> =
        Codec.discovery(bytes(frame(data, discoveryDomain)), trust, now)
    fun capabilities(
        data: JsonObject, prior: Codec.CapabilityObservation? = null,
        expected: Codec.CapabilityScope = scope, trust: Codec.TrustedM5Identity = trusted,
    ): Codec.Result<Codec.CapabilityTransition> =
        Codec.capabilities(bytes(frame(data, capabilityDomain)), trust, expected, prior, now)

    val discovery = payload()
    val capability = payload(true)
    val goodDiscoveryFrame = frame(discovery, discoveryDomain)
    val goodCapabilityFrame = frame(capability, capabilityDomain)
    // Future regression: these are M5-preparable origins, not a DNS lookup or a trust grant.
    val longestName = List(3) { "a".repeat(63) }.joinToString(".") + "." + "b".repeat(61)
    for (platform in Codec.Platform.entries) for (field in listOf("api_origin", "stream_origin")) {
        for (origin in listOf("https://node.123", "https://node.1a", "https://123.456",
            "https://$longestName.", "https://[2001:db8::1]:65535", "https://[::ffff:c000:201]")) {
            val expected = identity(apiOrigin = if (field == "api_origin") origin else api,
                streamOrigin = if (field == "stream_origin") origin else stream)
            val d = change(discovery, field, JsonPrimitive(origin))
            val c = change(change(capability, field, JsonPrimitive(origin)), "platform", JsonPrimitive(platform.name))
            val found = verified(discover(d, expected))
            val observed = verified(capabilities(c, expected = Codec.CapabilityScope(user, device, platform), trust = expected))
            assertTrue(found.identity.apiOrigin == expected.apiOrigin && found.identity.streamOrigin == expected.streamOrigin)
            assertTrue(observed.snapshot.identity.apiOrigin == expected.apiOrigin &&
                observed.snapshot.identity.streamOrigin == expected.streamOrigin)
            // Correct MESSAGE signatures do not let a response replace independently captured trust.
            refused(Failure.IDENTITY_MISMATCH, discover(d))
            refused(Failure.IDENTITY_MISMATCH, capabilities(c, expected = Codec.CapabilityScope(user, device, platform)))
        }
        for (origin in listOf("https://-node.123", "https://node-.123", "https://node..123", "https://node_1.123",
            "https://${"a".repeat(64)}.123", "https://${longestName}x", "https://node.123:0",
            "https://node.123:65536", "https://node.123:", "https://[::ffff:192.000.2.1]",
            "https://[1:2:3:4:5:6:7:8:9]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
            refused(Failure.INVALID_DOCUMENT, discover(change(discovery, field, JsonPrimitive(origin))))
            refused(Failure.INVALID_DOCUMENT, capabilities(change(capability, field, JsonPrimitive(origin))))
        }
    }
    val wire = bytes(goodDiscoveryFrame)
    val view = verified(Codec.discovery(wire, trusted, now))
    val canonicalCopy = view.canonicalEnvelope
    wire.fill(0)
    view.canonicalEnvelope.fill(0)
    assertArrayEquals(canonicalCopy, view.canonicalEnvelope)
    assertArrayEquals(originalSpki, view.identity.publicKeySpki)
    assertFalse(view.isEmptyAdvertisement)
    assertTrue(view.advertises(Codec.Platform.MACOS, Codec.Family.SELF))
    val first = verified(capabilities(capability))
    assertNull(first.expectedPrevious)
    assertEquals(7L, first.snapshot.revision)
    val prior = first.nextObservation
    val effectiveCopy = prior.effectivePayloadSha256
    val capabilityEnvelopeCopy = first.snapshot.canonicalEnvelope
    prior.effectivePayloadSha256.fill(0)
    first.snapshot.canonicalEnvelope.fill(0)
    assertArrayEquals(effectiveCopy, prior.effectivePayloadSha256)
    assertArrayEquals(capabilityEnvelopeCopy, first.snapshot.canonicalEnvelope)
    fun frozen(block: () -> Unit) {
        try { block(); fail("verified collection was mutable") }
        catch (_: UnsupportedOperationException) { /* Required defensive freeze. */ }
    }
    frozen { (view.families as MutableSet<Codec.Family>).clear() }
    frozen { (view.platforms as MutableSet<Codec.Platform>).clear() }
    frozen { (first.snapshot.families as MutableSet<Codec.Family>).add(Codec.Family.DELETION) }
    assertEquals(setOf(Codec.Family.SELF, Codec.Family.SESSION), first.snapshot.families)

    // Both document types: outer/payload closed membership, constants and token types.
    for ((data, domain) in listOf(discovery to discoveryDomain, capability to capabilityDomain)) {
        fun decode(root: JsonObject): Codec.Result<*> = if (domain == discoveryDomain)
            Codec.discovery(bytes(root), trusted, now)
        else Codec.capabilities(bytes(root), trusted, scope, null, now)
        val good = frame(data, domain)
        for (name in good.keys) refused(Failure.INVALID_DOCUMENT, decode(JsonObject(good - name)))
        refused(Failure.INVALID_DOCUMENT, decode(change(good, "unknown", JsonPrimitive(true))))
        for (name in data.keys) refused(Failure.INVALID_DOCUMENT, decode(frame(JsonObject(data - name), domain)))
        refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "unknown", JsonNull), domain)))
        refused(Failure.UNSUPPORTED, decode(change(good, "contract_version", JsonPrimitive("v1"))))
        refused(Failure.UNSUPPORTED, decode(frame(change(data, "schema_version", JsonPrimitive(3)), domain)))
        refused(Failure.UNSUPPORTED, decode(frame(change(data, "api_major", JsonPrimitive(2)), domain)))
        refused(Failure.UNSUPPORTED, decode(change(good, "signature_algorithm", JsonPrimitive("ES256"))))
        for (bad in listOf(JsonPrimitive(true), JsonPrimitive("2"), JsonNull,
            Json.parseToJsonElement("2.0"), Json.parseToJsonElement("2e0"))) {
            refused(Failure.INVALID_DOCUMENT, decode(change(good, "schema_version", bad)))
            refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "schema_version", bad), domain)))
        }
        for (name in listOf("api_major", "identity_epoch") + if (domain == capabilityDomain)
            listOf("capability_revision") else emptyList()) {
            for (bad in listOf(JsonPrimitive(false), JsonPrimitive("1"), JsonPrimitive(0), JsonPrimitive(-1),
                JsonPrimitive(Codec.MAX_REVISION + 1), Json.parseToJsonElement("1.0"), Json.parseToJsonElement("1e0"))) {
                refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, name, bad), domain)))
            }
        }
        refused(Failure.HASH_MISMATCH, decode(change(good, "payload_sha256", JsonPrimitive("0".repeat(64)))))
        refused(Failure.INVALID_DOCUMENT, decode(change(good, "payload_sha256", JsonPrimitive("A".repeat(64)))))
        // Freshly sign each semantic mutation; do not accidentally fail on an obsolete payload hash.
        for (name in listOf("server_instance_id", "api_origin", "stream_origin")) {
            val replacement = if (name == "server_instance_id") otherId else "https://other.example.invalid"
            refused(Failure.IDENTITY_MISMATCH, decode(frame(change(data, name, JsonPrimitive(replacement)), domain)))
        }
        refused(Failure.IDENTITY_MISMATCH, decode(frame(change(data, "identity_epoch", JsonPrimitive(2)), domain)))
        refused(Failure.IDENTITY_MISMATCH, decode(frame(change(data, "identity_thumbprint_sha256",
            JsonPrimitive("0".repeat(64))), domain)))
        for (badOrigin in listOf("$api/", "$api?", "$api#x", "https://u@host.invalid", "https://host.invalid:",
            "https://host.invalid:0", "https://host.invalid:65536", "https://ho%73t.invalid", " https://host.invalid",
            "https://\u00e9.example.invalid", "https://e\u0301.example.invalid")) {
            refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "api_origin", JsonPrimitive(badOrigin)), domain)))
        }
        refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "families", strings("self", "self")), domain)))
        refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "families", strings("unknown")), domain)))
        refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "families", JsonArray(listOf(JsonPrimitive(true)))), domain)))
        refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "families", JsonNull), domain)))
        for (wrongDomain in listOf(discoveryDomain, capabilityDomain, "AutPlay discovery v1\n").filter { it != domain }) {
            refused(Failure.INVALID_SIGNATURE, decode(frame(data, wrongDomain)))
        }
        for (recipe in listOf("hex", "prehash", "portable"))
            refused(Failure.INVALID_SIGNATURE, decode(frame(data, domain, recipe = recipe)))
        refused(Failure.INVALID_SIGNATURE, decode(frame(data, domain, signer = key())))
        refused(Failure.INVALID_DOCUMENT, decode(frame(data, domain, derWire = true)))
        val signatureText = (good["signature_b64url"] as JsonPrimitive).content
        for (badSignature in listOf(signatureText + "=", signatureText.dropLast(1), "!".repeat(86),
            signatureText.dropLast(1) + "B")) {
            refused(Failure.INVALID_DOCUMENT, decode(change(good, "signature_b64url", JsonPrimitive(badSignature))))
        }
        val order = (pair.public as ECPublicKey).params.order
        fun fixed(value: BigInteger): ByteArray {
            val magnitude = value.toByteArray().takeLast(32).toByteArray()
            return ByteArray(32 - magnitude.size) + magnitude
        }
        val signature = Base64.getUrlDecoder().decode(signatureText)
        for (bad in listOf(ByteArray(32) + signature.copyOfRange(32, 64),
            signature.copyOfRange(0, 32) + ByteArray(32), fixed(order) + signature.copyOfRange(32, 64),
            signature.copyOfRange(0, 32) + fixed(order))) {
            refused(Failure.INVALID_SIGNATURE, decode(change(good, "signature_b64url", JsonPrimitive(url(bad)))))
        }
        val s = BigInteger(1, signature.copyOfRange(32, 64))
        val lowS = if (s <= order.shiftRight(1)) s else order.subtract(s)
        for (validS in listOf(lowS, order.subtract(lowS))) {
            verified(decode(change(good, "signature_b64url", JsonPrimitive(url(
                signature.copyOfRange(0, 32) + fixed(validS))))))
        }
        val spkiText = b64(originalSpki)
        for (badSpki in listOf(spkiText.dropLast(1), spkiText + "=", spkiText + "\n", b64(originalSpki + byteArrayOf(0)),
            b64(originalSpki.copyOf().apply { this[26] = 0x02 }),
            b64(originalSpki.copyOf().apply { fill(0, 27, size) }), b64(key("secp384r1").public.encoded))) {
            refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "identity_public_key_spki_b64",
                JsonPrimitive(badSpki)), domain)))
        }
        val ttl = if (domain == discoveryDomain) 600L else 3600L
        for (expiry in listOf(now, now.minusSeconds(1), now.plusSeconds(ttl).plusNanos(1))) {
            refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "expires_at", JsonPrimitive(expiry.toString())), domain)))
        }
        var expired = change(data, "issued_at", JsonPrimitive(now.minusSeconds(ttl).toString()))
        expired = change(expired, "expires_at", JsonPrimitive(now.toString()))
        refused(Failure.EXPIRED, decode(frame(expired, domain)))
        var future = change(data, "issued_at", JsonPrimitive(now.plusSeconds(120).toString()))
        future = change(future, "expires_at", JsonPrimitive(now.plusSeconds(121).toString()))
        verified(decode(frame(future, domain)))
        future = change(future, "issued_at", JsonPrimitive(now.plusSeconds(120).plusNanos(1).toString()))
        refused(Failure.CLOCK_SKEW, decode(frame(future, domain)))
        for (badTime in listOf("2026-02-30T09:00:00Z", "2026-10-16T24:00:00Z", "2026-10-16T09:00:60Z",
            "2026-10-16T09:00:00+00:00", "2026-10-16T09:00:00.1234567890Z")) {
            refused(Failure.INVALID_DOCUMENT, decode(frame(change(data, "issued_at", JsonPrimitive(badTime)), domain)))
        }
        val raw = asciiCanonical(good)
        fun decodeRaw(value: ByteArray): Codec.Result<*> = if (domain == discoveryDomain)
            Codec.discovery(value, trusted, now) else Codec.capabilities(value, trusted, scope, null, now)
        for (broken in listOf(raw.replaceFirst("{", "{\"schema_version\":2,"),
            raw.replace("\"api_major\":1", "\"api_major\":1,\"api_maj\\u006fr\":1"),
            raw.replace("\"families\":[", "\"families\":[\"\\uD800\","),
            raw.replace("\"families\":[", "\"families\":[\"\\uDC00\","),
            raw.replace("\"api_major\":1", "\"api_major\":NaN"),
            raw.replace("\"api_major\":1", "\"api_major\":01"), raw + "{}")) {
            refused(Failure.INVALID_JSON, decodeRaw(broken.toByteArray(Charsets.UTF_8)))
        }
        refused(Failure.INVALID_JSON, decodeRaw(byteArrayOf(0xc3.toByte(), 0x28)))
        refused(Failure.INVALID_JSON, decodeRaw(byteArrayOf(0xef.toByte(), 0xbb.toByte(), 0xbf.toByte()) + bytes(good)))
        // Alternate JSON escaping/whitespace must verify the same canonical payload; no text normalization.
        verified(decodeRaw(raw.replace("\"contract_version\"", "\"contract_versi\\u006fn\"")
            .replace("https://", "https:\\/\\/").toByteArray(Charsets.UTF_8)))
        val maximum = if (domain == discoveryDomain) Codec.DISCOVERY_MAX_BYTES else Codec.CAPABILITIES_MAX_BYTES
        val goodBytes = bytes(good)
        verified(decodeRaw(goodBytes + ByteArray(maximum - goodBytes.size) { 32 }))
        refused(Failure.TOO_LARGE, decodeRaw(goodBytes + ByteArray(maximum - goodBytes.size + 1) { 32 }))
        // Whole envelope exceeds the limit while the payload itself remains below it.
        val enlarged = change(data, "api_origin", JsonPrimitive("x".repeat(maximum - bytes(data).size)))
        assertTrue(bytes(enlarged).size <= maximum)
        assertTrue(bytes(frame(enlarged, domain)).size > maximum)
        refused(Failure.TOO_LARGE, decode(frame(enlarged, domain)))
    }

    for (trust in listOf(identity(id = otherId), identity(epoch = 2), identity(spki = key().public.encoded),
        identity(fingerprint = "0".repeat(64)), identity(apiOrigin = "$api:443"), identity(streamOrigin = api))) {
        refused(Failure.IDENTITY_MISMATCH, discover(discovery, trust))
        refused(Failure.IDENTITY_MISMATCH, capabilities(capability, trust = trust))
    }
    // A self-consistent replacement key in the document cannot establish trust.
    val replacementKey = key()
    var substituted = change(discovery, "identity_public_key_spki_b64", JsonPrimitive(b64(replacementKey.public.encoded)))
    substituted = change(substituted, "identity_thumbprint_sha256", JsonPrimitive(hex(sha(replacementKey.public.encoded))))
    refused(Failure.IDENTITY_MISMATCH, Codec.discovery(bytes(frame(substituted, discoveryDomain, replacementKey)), trusted, now))
    for (bad in listOf(strings("ANDROID", "ANDROID"), strings("OTHER"), strings("MACOS", "ANDROID", "MACOS"),
        JsonArray(listOf(JsonPrimitive(false))))) {
        refused(Failure.INVALID_DOCUMENT, discover(change(discovery, "platforms", bad)))
    }
    for ((name, value) in listOf("user_id" to otherId, "device_id" to otherId, "platform" to "ANDROID"))
        refused(Failure.SCOPE_MISMATCH, capabilities(change(capability, name, JsonPrimitive(value))))
    refused(Failure.INVALID_DOCUMENT, capabilities(change(capability, "platform", JsonPrimitive("OTHER"))))
    refused(Failure.SCOPE_MISMATCH, capabilities(capability, expected = Codec.CapabilityScope(otherId, device, Codec.Platform.MACOS)))
    val emptyDiscovery = verified(discover(change(change(discovery, "families", strings()), "platforms", strings())))
    assertTrue(emptyDiscovery.isEmptyAdvertisement)
    for (platform in Codec.Platform.entries) for (family in Codec.Family.entries)
        assertFalse(emptyDiscovery.advertises(platform, family))
    assertTrue(verified(discover(change(discovery, "platforms", strings()))).isEmptyAdvertisement)
    assertTrue(verified(discover(change(discovery, "families", strings()))).isEmptyAdvertisement)
    val emptyCapability = change(capability, "families", strings())
    assertTrue(verified(capabilities(emptyCapability)).snapshot.isEmptyAdvertisement)
    refused(Failure.REVISION_ROLLBACK, capabilities(change(capability, "capability_revision", JsonPrimitive(6)), prior))
    refused(Failure.REVISION_CONFLICT, capabilities(emptyCapability, prior))
    val disabled = verified(capabilities(change(emptyCapability, "capability_revision", JsonPrimitive(8)), prior))
    assertTrue(disabled.snapshot.isEmptyAdvertisement)
    assertSame(prior, disabled.expectedPrevious)
    refused(Failure.REVISION_ROLLBACK, capabilities(capability, disabled.nextObservation))
    verified(capabilities(change(capability, "capability_revision", JsonPrimitive(Codec.MAX_REVISION)), prior))
    var refresh = change(capability, "issued_at", JsonPrimitive(now.plusSeconds(1).toString()))
    refresh = change(refresh, "expires_at", JsonPrimitive(now.plusSeconds(3601).toString()))
    val refreshed = verified(capabilities(refresh, prior))
    assertEquals(prior.revision, refreshed.nextObservation.revision)
    assertArrayEquals(effectiveCopy, refreshed.nextObservation.effectivePayloadSha256)
    assertSame(prior, refreshed.expectedPrevious)
    val otherScopeData = change(capability, "user_id", JsonPrimitive(otherId))
    refused(Failure.OBSERVATION_SCOPE_MISMATCH, capabilities(otherScopeData, prior,
        Codec.CapabilityScope(otherId, device, Codec.Platform.MACOS)))
    val changedIdentityData = change(capability, "identity_epoch", JsonPrimitive(2))
    refused(Failure.OBSERVATION_SCOPE_MISMATCH, capabilities(changedIdentityData, prior, trust = identity(epoch = 2)))
    verified(discover(change(discovery, "identity_epoch", JsonPrimitive(Codec.MAX_REVISION)),
        identity(epoch = Codec.MAX_REVISION)))
    verified(capabilities(change(capability, "identity_epoch", JsonPrimitive(Codec.MAX_REVISION)),
        trust = identity(epoch = Codec.MAX_REVISION)))
    for (data in listOf(discovery, capability)) {
        val minimumTtl = change(data, "expires_at", JsonPrimitive(now.plusNanos(1).toString()))
        if (data === discovery) verified(discover(minimumTtl)) else verified(capabilities(minimumTtl))
    }
    val exactExpiry = now.plusSeconds(600)
    refused(Failure.EXPIRED, Codec.discovery(bytes(goodDiscoveryFrame), trusted, exactExpiry))
    verified(Codec.discovery(bytes(goodDiscoveryFrame), trusted, exactExpiry.minusNanos(1)))
    refused(Failure.EXPIRED, Codec.capabilities(bytes(goodCapabilityFrame), trusted, scope, prior, now.plusSeconds(3600)))
    // Default diagnostics expose bounded stages only, including distinguishable non-success states.
    val diagnosticValues = listOf<Any>(trusted, scope, view, first, first.snapshot, prior,
        Codec.discovery(bytes(goodDiscoveryFrame), trusted, now), Codec.Result.Rejected(Failure.EXPIRED),
        Codec.Result.Rejected(Failure.UNSUPPORTED), Codec.Result.Rejected(Failure.UNAVAILABLE))
    for (value in diagnosticValues) for (sensitive in listOf(instance, user, device, api, stream, thumbprint, b64(originalSpki)))
        assertFalse(value.toString().contains(sensitive))
    assertNotEquals(Codec.Result.Rejected(Failure.UNSUPPORTED).toString(), Codec.Result.Rejected(Failure.UNAVAILABLE).toString())
    assertNotEquals(Codec.Result.Rejected(Failure.EXPIRED).toString(), emptyCapability.toString())
}
