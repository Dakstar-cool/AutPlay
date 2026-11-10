package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.syncbinding.SyncBindingContractV2 as B1
import app.autplay.application.syncbinding.SyncBindingContractV2.Failure
import java.math.BigInteger
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Signature
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
 * FUTURE assertions only; root may add one call inside an existing persistence composite after
 * review. No additional test entry point. No execution, imported code evaluation or crypto vector
 * computation was performed during authoring. All synthetic keys/hashes/signatures below are
 * generated only when a future authorized composite runs. They grant no real K/J/server authority.
 * Restricted ASCII/integer fixtures have an independent sorted serializer and literal oracle;
 * production JCS is never used to generate their expected MESSAGE or canonical bytes.
 * Future transport qualification remains separate: capture an Authorization sentinel at the exact
 * trusted node.123 API origin, return a redirect to another origin and assert zero forwarded
 * credentials/requests there. A changed/untrusted capture must fail before credential acquisition.
 * This pure codec has no HTTP adapter, so the context-refusal assertions below are not that proof.
 */
internal fun desktopSyncBindingV2Assertions(): Unit {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val instance = "11111111-1111-4111-8111-111111111111"
    val user = "22222222-2222-4222-8222-222222222222"
    val device = "33333333-3333-4333-8333-333333333333"
    val profile = "44444444-4444-4444-8444-444444444444"
    val epoch = "55555555-5555-4555-8555-555555555555"
    val other = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    val discoveryDomain = "autplay:sync-binding:negotiation:discovery:v2\n"
    val capabilityDomain = "autplay:sync-binding:negotiation:capabilities:v2\n"
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
    fun <T> verified(result: B1.Result<T>): T {
        assertTrue("expected verified, got $result", result is B1.Result.Verified)
        return (result as B1.Result.Verified<T>).value
    }
    fun refused(reason: Failure, result: B1.Result<*>) {
        assertTrue("expected $reason, got $result", result is B1.Result.Rejected)
        assertEquals(reason, (result as B1.Result.Rejected).failure)
    }
    val pair = key()
    val spki = pair.public.encoded
    val thumb = hex(sha(spki))
    val input = spki.copyOf()
    val trusted = TrustedM5Identity(instance, 1, input, thumb, api, stream)
    input.fill(0); trusted.publicKeySpki.fill(0)
    assertArrayEquals(spki, trusted.publicKeySpki)
    fun owner(platform: Platform = Platform.MACOS, generation: Long = 1, userId: String = user,
        deviceId: String = device, identity: TrustedM5Identity = trusted): B1.OwnerCapture =
        B1.OwnerCapture(identity, userId, deviceId, platform, generation)
    fun payload(platform: Platform? = null, available: Boolean = true, revision: Long = 7): JsonObject {
        val fields = linkedMapOf<String, JsonElement>(
            "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "binding_contract_version" to JsonPrimitive(2), "protocol_version" to JsonPrimitive(1),
            "api_major" to JsonPrimitive(1), "server_instance_id" to JsonPrimitive(instance),
            "identity_epoch" to JsonPrimitive(1), "identity_public_key_spki_b64" to JsonPrimitive(b64(spki)),
            "identity_thumbprint_sha256" to JsonPrimitive(thumb), "api_origin" to JsonPrimitive(api),
            "stream_origin" to JsonPrimitive(stream), "binding_available" to JsonPrimitive(available),
            "platforms" to if (available) strings("ANDROID", "MACOS") else strings(),
            "issued_at" to JsonPrimitive(now.toString()),
            "expires_at" to JsonPrimitive(now.plusSeconds(if (platform == null) 600 else 3600).toString()))
        if (platform != null) fields.putAll(mapOf("user_id" to JsonPrimitive(user), "device_id" to JsonPrimitive(device),
            "platform" to JsonPrimitive(platform.name), "capability_revision" to JsonPrimitive(revision)))
        return JsonObject(fields)
    }
    fun rawSignature(der: ByteArray): ByteArray {
        var cursor = 0
        assertEquals(0x30, der[cursor++].toInt() and 255)
        assertEquals(der.size - 2, der[cursor++].toInt() and 255)
        fun integer(): ByteArray {
            assertEquals(2, der[cursor++].toInt() and 255)
            val size = der[cursor++].toInt() and 255
            val number = BigInteger(der.copyOfRange(cursor, cursor + size)); cursor += size
            val magnitude = number.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            assertTrue(number.signum() > 0 && magnitude.size <= 32)
            return ByteArray(32 - magnitude.size) + magnitude
        }
        val result = integer() + integer()
        assertEquals(der.size, cursor)
        return result
    }
    fun frame(data: JsonObject, domain: String, signer: KeyPair = pair, recipe: String = "message",
        literal: String? = null, derWire: Boolean = false): JsonObject {
        val canonical = (literal ?: asciiCanonical(data)).toByteArray(Charsets.UTF_8)
        if (literal != null) assertArrayEquals(canonical, bytes(data))
        val digest = sha(canonical)
        val message = when (recipe) {
            "hex" -> domain.toByteArray(Charsets.US_ASCII) + hex(digest).toByteArray(Charsets.US_ASCII)
            "prehash" -> digest
            else -> domain.toByteArray(Charsets.US_ASCII) + digest
        }
        val der = Signature.getInstance(if (recipe == "prehash") "NONEwithECDSA" else "SHA256withECDSA").run {
            initSign(signer.private); update(message); sign()
        }
        return JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "payload" to data, "payload_sha256" to JsonPrimitive(hex(digest)),
            "signature_algorithm" to JsonPrimitive("ES256-P1363"),
            "signature_b64url" to JsonPrimitive(url(if (derWire) der else rawSignature(der)))))
    }
    fun discover(data: JsonObject, identity: TrustedM5Identity = trusted): B1.Result<B1.Discovery> =
        B1.discovery(bytes(frame(data, discoveryDomain)), identity, now)
    val mac = owner()
    val initial = verified(B1.revisionGuard(mac, 0, null))
    fun caps(data: JsonObject, guard: B1.RevisionGuard = initial,
        actor: B1.OwnerCapture = mac): B1.Result<B1.CapabilityTransition> =
        B1.capabilities(bytes(frame(data, capabilityDomain)), actor, guard, now)

    assertEquals(listOf("GET", "GET", "POST"), B1.Operation.entries.map { it.method })
    assertEquals(listOf("/api/v1/sync-binding/v2/discovery", "/api/v1/sync-binding/v2/capabilities",
        "/api/v1/sync-binding/v2/bind"), B1.Operation.entries.map { it.path })
    assertEquals(listOf(false, true, true), B1.Operation.entries.map { it.bearerRequired })
    // Literal canonical ASCII oracle, independent of production serializer and fixed domains.
    val literalDiscovery = """{"api_major":1,"api_origin":"https://api.example.invalid","binding_available":true,"binding_contract_version":2,"contract_version":"v2","expires_at":"2026-10-16T09:10:00Z","identity_epoch":1,"identity_public_key_spki_b64":"${b64(spki)}","identity_thumbprint_sha256":"$thumb","issued_at":"2026-10-16T09:00:00Z","platforms":["ANDROID","MACOS"],"protocol_version":1,"schema_version":2,"server_instance_id":"11111111-1111-4111-8111-111111111111","stream_origin":"https://stream.example.invalid"}"""
    val literalCapabilities = """{"api_major":1,"api_origin":"https://api.example.invalid","binding_available":true,"binding_contract_version":2,"capability_revision":7,"contract_version":"v2","device_id":"33333333-3333-4333-8333-333333333333","expires_at":"2026-10-16T10:00:00Z","identity_epoch":1,"identity_public_key_spki_b64":"${b64(spki)}","identity_thumbprint_sha256":"$thumb","issued_at":"2026-10-16T09:00:00Z","platform":"MACOS","platforms":["ANDROID","MACOS"],"protocol_version":1,"schema_version":2,"server_instance_id":"11111111-1111-4111-8111-111111111111","stream_origin":"https://stream.example.invalid","user_id":"22222222-2222-4222-8222-222222222222"}"""
    val discoveryWire = bytes(frame(payload(), discoveryDomain, literal = literalDiscovery))
    val discovery = verified(B1.discovery(discoveryWire, trusted, now))
    assertTrue(discovery.bindingAvailable)
    assertEquals(listOf(Platform.ANDROID, Platform.MACOS), discovery.platforms)
    val capWire = bytes(frame(payload(Platform.MACOS), capabilityDomain, literal = literalCapabilities))
    val transition = verified(B1.capabilities(capWire, mac, initial, now))
    val accepted = verified(B1.acceptCapabilities(transition, transition.next, mac))
    val longestName = List(3) { "a".repeat(63) }.joinToString(".") + "." + "b".repeat(61)
    // Fresh signatures, exact trust and literal MESSAGE input: no obsolete digest can mask this seam.
    for (platform in Platform.entries) for (field in listOf("api_origin", "stream_origin")) {
        for (origin in listOf("https://node.123", "https://node.1a", "https://123.456", "https://NODE.123.:00443",
            "https://$longestName.", "https://[2001:0DB8:0:0::1]:00001", "https://[::ffff:192.0.2.1]:65535")) {
            val apiOrigin = if (field == "api_origin") origin else api
            val streamOrigin = if (field == "stream_origin") origin else stream
            val trust = TrustedM5Identity(instance, 1, spki, thumb, apiOrigin, streamOrigin)
            val actor = owner(platform, identity = trust)
            val guard = verified(B1.revisionGuard(actor, 0, null))
            val data = change(payload(), field, JsonPrimitive(origin))
            val discoveryOracle = literalDiscovery.replace(api, apiOrigin).replace(stream, streamOrigin)
            val capabilityOracle = literalCapabilities.replace(api, apiOrigin).replace(stream, streamOrigin)
                .replace("\"platform\":\"MACOS\"", "\"platform\":\"${platform.name}\"")
            val found = verified(B1.discovery(bytes(frame(data, discoveryDomain, literal = discoveryOracle)), trust, now))
            assertTrue(found.identity.apiOrigin == apiOrigin && found.identity.streamOrigin == streamOrigin)
            val cap = change(payload(platform), field, JsonPrimitive(origin))
            val view = verified(B1.capabilities(bytes(frame(cap, capabilityDomain, literal = capabilityOracle)), actor, guard, now))
            val selected = verified(B1.acceptCapabilities(view, view.next, actor))
            val lineage = B1.DurableLineage(actor, profile, epoch)
            val context = verified(B1.bindContext(selected, actor, lineage, view.next, B1.BindMode.FIRST_BIND, null, now))
            val request = verified(B1.encodeBind(context, actor, lineage, view.next, now))
            val requestBody = Json.parseToJsonElement(request.canonicalBody.toString(Charsets.UTF_8)) as JsonObject
            assertTrue(requestBody["expected_api_origin"] == JsonPrimitive(apiOrigin) &&
                requestBody["expected_stream_origin"] == JsonPrimitive(streamOrigin))
            assertTrue(requestBody.keys.none { it in setOf("access_token", "refresh_token", "proof", "platform") })
            val result = JsonObject(requestBody.filterKeys { !it.startsWith("expected_") } + mapOf(
                "state" to JsonPrimitive("BOUND"), "platform" to JsonPrimitive(platform.name),
                "server_instance_id" to JsonPrimitive(instance), "identity_epoch" to JsonPrimitive(1),
                "identity_thumbprint_sha256" to JsonPrimitive(thumb), "api_origin" to JsonPrimitive(apiOrigin),
                "stream_origin" to JsonPrimitive(streamOrigin)))
            verified(B1.decodeBindResult(200, bytes(result), request, actor, lineage))
            refused(Failure.IDENTITY_MISMATCH, discover(data))
            refused(Failure.IDENTITY_MISMATCH, caps(cap))
            val oldOwner = owner(platform)
            refused(Failure.STALE_CONTEXT, B1.encodeBind(context, oldOwner,
                B1.DurableLineage(oldOwner, profile, epoch), view.next, now))
        }
        for (origin in listOf("https://-node.123", "https://node-.123", "https://node..123", "https://node_1.123",
            "https://${"a".repeat(64)}.123", "https://${longestName}x", "https://node.123:0", "https://node.123:000001",
            "https://node.123:65536", "https://node.123:", "https://[::ffff:192.000.2.1]",
            "https://[::ffff:256.0.2.1]", "https://[1:2:3:4:5:6:7:8:9]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
            refused(Failure.INVALID_DOCUMENT, discover(change(payload(), field, JsonPrimitive(origin))))
            refused(Failure.INVALID_DOCUMENT, caps(change(payload(Platform.MACOS), field, JsonPrimitive(origin))))
        }
    }
    discoveryWire.fill(0); discovery.canonicalEnvelope.fill(0)
    assertTrue(discovery.canonicalEnvelope.first() == '{'.code.toByte())
    capWire.fill(0); transition.snapshot.canonicalEnvelope.fill(0)
    transition.next.previousB1!!.effectivePayloadSha256.fill(0)
    assertTrue(transition.snapshot.canonicalEnvelope.first() == '{'.code.toByte())

    val disabled = verified(discover(payload(available = false)))
    assertFalse(disabled.bindingAvailable); assertTrue(disabled.platforms.isEmpty())
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "platforms", strings("MACOS", "ANDROID"))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(available = false), "platforms", strings("MACOS"))))
    for (domain in listOf(capabilityDomain, "autplay:device-compatibility:negotiation:discovery:v2\n",
        "autplay:sync-binding:negotiation:discovery:v2\\n")) {
        refused(Failure.INVALID_SIGNATURE, B1.discovery(bytes(frame(payload(), domain)), trusted, now))
    }
    for (recipe in listOf("hex", "prehash")) {
        refused(Failure.INVALID_SIGNATURE, B1.discovery(bytes(frame(payload(), discoveryDomain, recipe = recipe)), trusted, now))
    }
    refused(Failure.INVALID_SIGNATURE, B1.capabilities(bytes(frame(payload(Platform.MACOS), discoveryDomain)), mac, initial, now))
    for (recipe in listOf("hex", "prehash")) {
        refused(Failure.INVALID_SIGNATURE, B1.capabilities(bytes(frame(payload(Platform.MACOS), capabilityDomain, recipe = recipe)), mac, initial, now))
    }
    val signedDiscovery = frame(payload(), discoveryDomain)
    val signatureText = (signedDiscovery["signature_b64url"] as JsonPrimitive).content
    val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    val noncanonicalPadBits = signatureText.dropLast(1) + alphabet[alphabet.indexOf(signatureText.last()) xor 1]
    refused(Failure.INVALID_DOCUMENT, B1.discovery(bytes(change(signedDiscovery, "signature_b64url",
        JsonPrimitive(noncanonicalPadBits))), trusted, now))
    val rawProof = Base64.getUrlDecoder().decode(signatureText)
    val order = (pair.public as java.security.interfaces.ECPublicKey).params.order
    val originalS = BigInteger(1, rawProof.copyOfRange(32, 64))
    val highS = maxOf(originalS, order.subtract(originalS)).toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
    val highProof = rawProof.copyOfRange(0, 32) + ByteArray(32 - highS.size) + highS
    verified(B1.discovery(bytes(change(signedDiscovery, "signature_b64url", JsonPrimitive(url(highProof)))), trusted, now))
    refused(Failure.INVALID_DOCUMENT, B1.discovery(bytes(change(signedDiscovery, "signature_b64url",
        JsonPrimitive((signedDiscovery["signature_b64url"] as JsonPrimitive).content + "="))), trusted, now))
    refused(Failure.INVALID_SIGNATURE, B1.discovery(bytes(change(signedDiscovery, "signature_b64url",
        JsonPrimitive(url(ByteArray(64))))), trusted, now))
    refused(Failure.HASH_MISMATCH, B1.discovery(bytes(change(signedDiscovery, "payload_sha256",
        JsonPrimitive("0".repeat(64)))), trusted, now))
    refused(Failure.INVALID_SIGNATURE, B1.discovery(bytes(frame(payload(), discoveryDomain, signer = key())), trusted, now))
    refused(Failure.INVALID_DOCUMENT, B1.discovery(bytes(frame(payload(), discoveryDomain, derWire = true)), trusted, now))
    val stranger = key()
    val selfSigned = change(change(payload(), "identity_public_key_spki_b64", JsonPrimitive(b64(stranger.public.encoded))),
        "identity_thumbprint_sha256", JsonPrimitive(hex(sha(stranger.public.encoded))))
    refused(Failure.IDENTITY_MISMATCH, B1.discovery(bytes(frame(selfSigned, discoveryDomain, stranger)), trusted, now))
    refused(Failure.IDENTITY_MISMATCH, discover(change(payload(), "server_instance_id", JsonPrimitive(other))))
    refused(Failure.IDENTITY_MISMATCH, discover(change(payload(), "identity_epoch", JsonPrimitive(2))))
    refused(Failure.IDENTITY_MISMATCH, discover(change(payload(), "api_origin", JsonPrimitive("https://API.example.invalid"))))
    refused(Failure.IDENTITY_MISMATCH, discover(change(payload(), "identity_thumbprint_sha256", JsonPrimitive("0".repeat(64)))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "server_instance_id", JsonPrimitive(other.uppercase()))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "identity_thumbprint_sha256", JsonPrimitive("A".repeat(64)))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "identity_public_key_spki_b64", JsonPrimitive(b64(spki).trimEnd('=')))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "identity_public_key_spki_b64", JsonPrimitive(b64(key("secp384r1").public.encoded)))))
    for (origin in listOf("https://api.example.invalid/", "https://api.example.invalid:", "https://api.example.invalid:0",
        "https://api.example.invalid:65536", "https://a@api.example.invalid", "https://api.example.invalid?q=1",
        "https://api.example.invalid#x", " https://api.example.invalid", "https://api.ex\u200bample.invalid",
        "https://${"a".repeat(64)}.invalid", "https://-api.invalid", "https://api..invalid")) {
        refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "api_origin", JsonPrimitive(origin))))
    }
    for (badTime in listOf("2026-02-30T09:00:00Z", "2026-10-16T24:00:00Z", "2026-10-16T09:00:60Z",
        "2026-10-16T09:00:00+00:00", "2026-10-16T09:00:00.1234567890Z")) {
        refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "issued_at", JsonPrimitive(badTime))))
    }
    refused(Failure.EXPIRED, B1.discovery(bytes(frame(payload(), discoveryDomain)), trusted, now.plusSeconds(600)))
    val skew = change(change(payload(), "issued_at", JsonPrimitive(now.plusSeconds(120).plusNanos(1).toString())),
        "expires_at", JsonPrimitive(now.plusSeconds(500).toString()))
    refused(Failure.CLOCK_SKEW, discover(skew))
    verified(discover(change(skew, "issued_at", JsonPrimitive(now.plusSeconds(120).toString()))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "expires_at", JsonPrimitive(now.plusSeconds(600).plusNanos(1).toString()))))
    refused(Failure.INVALID_DOCUMENT, caps(change(payload(Platform.MACOS), "expires_at", JsonPrimitive(now.plusSeconds(3600).plusNanos(1).toString()))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "expires_at", JsonPrimitive(now.toString()))))

    // Every structural payload mutation gets a fresh digest and signature, not a stale-hash mask.
    for (field in payload().keys) {
        refused(Failure.INVALID_DOCUMENT, discover(JsonObject(payload() - field)))
        val invalid = discover(change(payload(), field, JsonNull))
        assertTrue(invalid is B1.Result.Rejected)
        assertNotEquals(Failure.HASH_MISMATCH, (invalid as B1.Result.Rejected).failure)
    }
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "families", strings("sync-binding"))))
    refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "binding_available", JsonPrimitive("true"))))
    for (number in listOf("true", "2.0", "2e0", "0", "-1", "9007199254740992")) {
        refused(Failure.INVALID_DOCUMENT, discover(change(payload(), "identity_epoch", Json.parseToJsonElement(number))))
        refused(Failure.INVALID_DOCUMENT, caps(change(payload(Platform.MACOS), "capability_revision", Json.parseToJsonElement(number))))
    }
    for (field in listOf("binding_contract_version", "protocol_version")) {
        refused(Failure.UNSUPPORTED, discover(change(payload(), field, JsonPrimitive(3))))
    }
    val fresh = bytes(frame(payload(), discoveryDomain)).toString(Charsets.UTF_8)
    refused(Failure.INVALID_JSON, B1.discovery(fresh.replace("\"schema_version\":2", "\"schema_version\":2,\"schema_vers\\u0069on\":2")
        .toByteArray(), trusted, now))
    refused(Failure.INVALID_JSON, B1.discovery(fresh.replace("\"platforms\":[\"ANDROID\",\"MACOS\"]",
        "\"platforms\":[[[[[]]]]]").toByteArray(), trusted, now)) // Depth sentinel, before signature.
    refused(Failure.INVALID_JSON, B1.discovery(fresh.replace("\"api_origin\":\"$api\"",
        "\"api_origin\":\"\\ud800\"").toByteArray(), trusted, now)) // Unicode sentinel.
    refused(Failure.INVALID_JSON, B1.discovery(byteArrayOf(0xc3.toByte(), 0x28), trusted, now))
    refused(Failure.INVALID_JSON, B1.discovery((fresh + " true").toByteArray(), trusted, now))
    val maxDiscovery = fresh.toByteArray() + ByteArray(B1.DISCOVERY_MAX_BYTES - fresh.toByteArray().size) { 32 }
    verified(B1.discovery(maxDiscovery, trusted, now))
    refused(Failure.TOO_LARGE, B1.discovery(maxDiscovery + byteArrayOf(32), trusted, now))
    val freshCap = bytes(frame(payload(Platform.MACOS), capabilityDomain))
    val maxCap = freshCap + ByteArray(B1.CAPABILITIES_MAX_BYTES - freshCap.size) { 32 }
    verified(B1.capabilities(maxCap, mac, initial, now))
    refused(Failure.TOO_LARGE, B1.capabilities(maxCap + byteArrayOf(32), mac, initial, now))
    // Explicit bound sentinels: no valid crypto is needed before canonical-envelope size refusal.
    val boundSentinel = change(change(frame(payload(), discoveryDomain), "payload_sha256", JsonPrimitive("0".repeat(64))),
        "signature_b64url", JsonPrimitive(url(ByteArray(64))))
    val discoveryExpansion = bytes(change(boundSentinel, "pad", JsonArray(List(800) { Json.parseToJsonElement("1e20") })))
    assertTrue(discoveryExpansion.size < B1.DISCOVERY_MAX_BYTES)
    refused(Failure.TOO_LARGE, B1.discovery(discoveryExpansion, trusted, now))
    val capExpansion = bytes(change(boundSentinel, "pad", JsonArray(List(3100) { Json.parseToJsonElement("1e20") })))
    assertTrue(capExpansion.size < B1.CAPABILITIES_MAX_BYTES)
    refused(Failure.TOO_LARGE, B1.capabilities(capExpansion, mac, initial, now))

    refused(Failure.SCOPE_MISMATCH, caps(change(payload(Platform.MACOS), "user_id", JsonPrimitive(other))))
    refused(Failure.SCOPE_MISMATCH, caps(change(payload(Platform.MACOS), "device_id", JsonPrimitive(other))))
    refused(Failure.SCOPE_MISMATCH, caps(payload(Platform.ANDROID)))
    refused(Failure.REVISION_ROLLBACK, caps(payload(Platform.MACOS, revision = 6), transition.next))
    refused(Failure.REVISION_CONFLICT, caps(payload(Platform.MACOS, available = false), transition.next))
    val refreshed = change(change(payload(Platform.MACOS), "issued_at", JsonPrimitive(now.plusSeconds(1).toString())),
        "expires_at", JsonPrimitive(now.plusSeconds(3601).toString()))
    verified(caps(refreshed, transition.next)) // Time changes excluded from same-kind effective content.
    val offTransition = verified(caps(payload(Platform.MACOS, available = false, revision = 8), transition.next))
    val off = verified(B1.acceptCapabilities(offTransition, offTransition.next, mac))
    refused(Failure.GUARD_MISMATCH, B1.acceptCapabilities(transition, initial, mac))
    refused(Failure.STALE_CONTEXT, B1.acceptCapabilities(transition, transition.next, owner(generation = 3)))
    refused(Failure.STALE_CONTEXT, caps(payload(Platform.MACOS), initial, owner(generation = 3)))
    refused(Failure.GUARD_MISMATCH, B1.revisionGuard(owner(userId = other), 7, transition.next.previousB1))

    // An actual verified D1 observation raises the shared global number, never supplies B1 content.
    val d1Data = JsonObject(payload(Platform.MACOS, revision = 9).filterKeys {
        it !in setOf("binding_available", "binding_contract_version", "protocol_version", "platforms")
    } + ("families" to strings("session")))
    val d1Result = D1.capabilities(bytes(frame(d1Data, "autplay:device-compatibility:negotiation:capabilities:v2\n")),
        trusted, D1.CapabilityScope(user, device, Platform.MACOS), null, now)
    assertTrue(d1Result is D1.Result.Verified)
    val d1Observation = (d1Result as D1.Result.Verified<D1.CapabilityTransition>).value.nextObservation
    val combined = verified(B1.revisionGuard(mac, 8, offTransition.next.previousB1, d1Observation))
    assertEquals(9L, combined.globalHighWater)
    refused(Failure.REVISION_ROLLBACK, caps(payload(Platform.MACOS, available = false, revision = 8), combined))
    val equalOtherKind = verified(caps(payload(Platform.MACOS, available = false, revision = 9), combined))
    assertFalse(d1Observation.effectivePayloadSha256.contentEquals(equalOtherKind.next.previousB1!!.effectivePayloadSha256))
    refused(Failure.GUARD_MISMATCH, B1.revisionGuard(owner(deviceId = other), 9, null, d1Observation))
    verified(caps(payload(Platform.MACOS, revision = B1.MAX_INTEGER)))

    for (platform in Platform.entries) {
        val actor = owner(platform)
        val guard = verified(B1.revisionGuard(actor, 0, null))
        val view = verified(caps(payload(platform), guard, actor))
        val selected = verified(B1.acceptCapabilities(view, view.next, actor))
        val lineage = B1.DurableLineage(actor, profile, epoch)
        val context = verified(B1.bindContext(selected, actor, lineage, view.next, B1.BindMode.FIRST_BIND, null, now))
        val request = verified(B1.encodeBind(context, actor, lineage, view.next, now))
        val expectedRequest = """{"binding_contract_version":2,"contract_version":"v2","device_id":"$device","expected_api_origin":"$api","expected_capability_revision":7,"expected_identity_epoch":1,"expected_identity_thumbprint_sha256":"$thumb","expected_server_instance_id":"$instance","expected_stream_origin":"$stream","journal_epoch":"$epoch","protocol_version":1,"schema_version":2,"server_profile_id":"$profile","supported_event_schema_versions":[1],"user_id":"$user"}"""
        assertArrayEquals(expectedRequest.toByteArray(), request.canonicalBody)
        assertEquals(B1.Operation.BIND, request.operation)
        assertEquals(B1.BindMode.FIRST_BIND, request.mode)
        assertTrue(request.canonicalBody.size <= B1.MAX_BODY_BYTES)
        request.canonicalBody.fill(0)
        assertArrayEquals(expectedRequest.toByteArray(), request.canonicalBody)
        val result = JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "binding_contract_version" to JsonPrimitive(2), "protocol_version" to JsonPrimitive(1),
            "state" to JsonPrimitive("BOUND"), "supported_event_schema_versions" to JsonArray(listOf(JsonPrimitive(1))),
            "user_id" to JsonPrimitive(user), "device_id" to JsonPrimitive(device), "server_profile_id" to JsonPrimitive(profile),
            "journal_epoch" to JsonPrimitive(epoch), "platform" to JsonPrimitive(platform.name),
            "server_instance_id" to JsonPrimitive(instance), "identity_epoch" to JsonPrimitive(1),
            "identity_thumbprint_sha256" to JsonPrimitive(thumb), "api_origin" to JsonPrimitive(api), "stream_origin" to JsonPrimitive(stream)))
        fun decode(data: JsonObject): B1.Result<B1.MatchedBindingResult> = B1.decodeBindResult(200, bytes(data), request, actor, lineage)
        val matched = verified(decode(result))
        assertEquals(B1.BindMode.FIRST_BIND, matched.mode)
        matched.canonicalBody.fill(0)
        assertArrayEquals(bytes(result), matched.canonicalBody)
        for (name in listOf("user_id", "device_id", "server_profile_id", "journal_epoch", "server_instance_id")) {
            refused(Failure.SCOPE_MISMATCH, decode(change(result, name, JsonPrimitive(other))))
        }
        refused(Failure.SCOPE_MISMATCH, decode(change(result, "platform", JsonPrimitive(if (platform == Platform.MACOS) "ANDROID" else "MACOS"))))
        refused(Failure.SCOPE_MISMATCH, decode(change(result, "api_origin", JsonPrimitive("https://other.example.invalid"))))
        refused(Failure.SCOPE_MISMATCH, decode(change(result, "stream_origin", JsonPrimitive("https://other.example.invalid"))))
        refused(Failure.SCOPE_MISMATCH, decode(change(result, "identity_epoch", JsonPrimitive(2))))
        refused(Failure.SCOPE_MISMATCH, decode(change(result, "identity_thumbprint_sha256", JsonPrimitive("0".repeat(64)))))
        refused(Failure.UNEXPECTED_STATUS, B1.decodeBindResult(201, bytes(result), request, actor, lineage))
        refused(Failure.INVALID_DOCUMENT, decode(change(result, "cursor", JsonPrimitive("forbidden"))))
        refused(Failure.INVALID_DOCUMENT, decode(change(result, "identity_epoch", Json.parseToJsonElement("1e0"))))
        refused(Failure.INVALID_DOCUMENT, decode(change(result, "supported_event_schema_versions", JsonArray(listOf(JsonPrimitive(true))))))
        refused(Failure.UNSUPPORTED, decode(change(result, "supported_event_schema_versions", JsonArray(listOf(JsonPrimitive(2))))))
        for (name in result.keys) {
            refused(Failure.INVALID_DOCUMENT, decode(JsonObject(result - name)))
            assertTrue(decode(change(result, name, JsonNull)) is B1.Result.Rejected)
        }
        val padded = bytes(result) + ByteArray(B1.MAX_BODY_BYTES - bytes(result).size) { 32 }
        verified(B1.decodeBindResult(200, padded, request, actor, lineage))
        refused(Failure.TOO_LARGE, B1.decodeBindResult(200, padded + byteArrayOf(32), request, actor, lineage))
        // Canonical-size sentinel: 1e20 expands to 21 JCS digits. Bound fails before closed model.
        val expands = bytes(change(result, "pad", JsonArray(List(800) { Json.parseToJsonElement("1e20") })))
        assertTrue(expands.size < B1.MAX_BODY_BYTES)
        refused(Failure.TOO_LARGE, B1.decodeBindResult(200, expands, request, actor, lineage))
        val lateOwner = owner(platform, generation = 3)
        refused(Failure.STALE_CONTEXT, B1.encodeBind(context, lateOwner, B1.DurableLineage(lateOwner, profile, epoch), view.next, now))
        refused(Failure.STALE_CONTEXT, B1.decodeBindResult(200, bytes(result), request, lateOwner, B1.DurableLineage(lateOwner, profile, epoch)))
        refused(Failure.STALE_CONTEXT, B1.decodeBindResult(200, bytes(result), request, actor, B1.DurableLineage(actor, other, epoch)))
        refused(Failure.STALE_CONTEXT, B1.decodeBindResult(200, bytes(result), request, actor, B1.DurableLineage(actor, profile, other)))
        refused(Failure.LINEAGE_REQUIRED, B1.encodeBind(context, actor, null, view.next, now))
        refused(Failure.EXPIRED, B1.encodeBind(context, actor, lineage, view.next, now.plusSeconds(3600)))
        for (redacted in listOf(actor, lineage, context, request, matched, view, selected, view.next, view.next.previousB1!!)) {
            val text = redacted.toString()
            for (secret in listOf(user, device, profile, epoch, api, stream, thumb, b64(spki))) assertFalse(text.contains(secret))
        }
    }
    val lineage = B1.DurableLineage(mac, profile, epoch)
    refused(Failure.LINEAGE_REQUIRED, B1.bindContext(accepted, mac, null, transition.next, B1.BindMode.FIRST_BIND, null, now))
    refused(Failure.BINDING_UNAVAILABLE, B1.bindContext(off, mac, lineage, offTransition.next, B1.BindMode.FIRST_BIND, null, now))
    refused(Failure.ESTABLISHED_TUPLE_REQUIRED, B1.bindContext(off, mac, lineage, offTransition.next,
        B1.BindMode.ESTABLISHED_CONFIRMATION, null, now))
    refused(Failure.STALE_CONTEXT, B1.bindContext(off, mac, lineage, offTransition.next,
        B1.BindMode.ESTABLISHED_CONFIRMATION, B1.EstablishedTuple(B1.DurableLineage(mac, other, epoch)), now))
    val established = B1.EstablishedTuple(lineage)
    val confirmation = verified(B1.bindContext(off, mac, lineage, offTransition.next,
        B1.BindMode.ESTABLISHED_CONFIRMATION, established, now))
    val confirmationRequest = verified(B1.encodeBind(confirmation, mac, lineage, offTransition.next, now))
    assertEquals(B1.BindMode.ESTABLISHED_CONFIRMATION, confirmationRequest.mode)
    val confirmationFields = (Json.parseToJsonElement(confirmationRequest.canonicalBody.toString(Charsets.UTF_8)) as JsonObject)
        .filterKeys { !it.startsWith("expected_") }
    val confirmationResult = JsonObject(confirmationFields + mapOf(
        "state" to JsonPrimitive("BOUND"), "platform" to JsonPrimitive("MACOS"),
        "server_instance_id" to JsonPrimitive(instance), "identity_epoch" to JsonPrimitive(1),
        "identity_thumbprint_sha256" to JsonPrimitive(thumb), "api_origin" to JsonPrimitive(api), "stream_origin" to JsonPrimitive(stream)))
    val confirmed = verified(B1.decodeBindResult(200, bytes(confirmationResult), confirmationRequest, mac, lineage))
    assertEquals(B1.BindMode.ESTABLISHED_CONFIRMATION, confirmed.mode)
    // Old accepted revision 7 can confirm the same established tuple after disable at revision 8.
    val oldConfirmation = verified(B1.bindContext(accepted, mac, lineage, offTransition.next,
        B1.BindMode.ESTABLISHED_CONFIRMATION, established, now.plusSeconds(3601)))
    verified(B1.encodeBind(oldConfirmation, mac, lineage, offTransition.next, now.plusSeconds(3601)))
    refused(Failure.GUARD_MISMATCH, B1.bindContext(accepted, mac, lineage, offTransition.next,
        B1.BindMode.FIRST_BIND, null, now))
}
