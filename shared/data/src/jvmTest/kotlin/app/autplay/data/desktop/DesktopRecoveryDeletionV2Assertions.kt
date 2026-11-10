package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2 as Codec
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2.BodyKind
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2.Route
import java.math.BigInteger
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * FUTURE source assertions, invoked inside one existing composite. No new @Test entry points.
 * UNVERIFIED: no imports, key generation, hashes, signatures, canonicalization or checks ran when
 * authoring. Fixtures generate only disposable keys when separately authorized to execute later.
 * Independent sorted JSON fixture serializer, literal domains/routes and MESSAGE crypto oracle.
 * No response is fabricated; DeletionResult actor vs preview/cancel recipient is asserted through
 * independent command contexts. Actual response validation, Android alias/Keystore and Mac custody,
 * first-effect authority, server lifecycle/coverage/veto/retention and interoperability remain gates.
 */
internal fun desktopRecoveryDeletionV2Assertions(): Unit {
    val instance = "11111111-1111-4111-8111-111111111111"
    val account = "22222222-2222-4222-8222-222222222222"
    val operation = "33333333-3333-4333-8333-333333333333"
    val binding = "44444444-4444-4444-8444-444444444444"
    val parent = "55555555-5555-4555-8555-555555555555"
    val other = "66666666-6666-4666-8666-666666666666"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    val requested = "2026-10-16T09:00:00.123456789Z"
    val verifier = "a".repeat(64) // Synthetic input only, no claim about a recovery-code preimage.
    val refresh = "b".repeat(64)
    fun key(): KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec("secp256r1")); generateKeyPair()
    }
    fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
    fun url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    fun obj(bytes: ByteArray): JsonObject = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as JsonObject
    fun text(body: JsonObject, name: String): String = (body.getValue(name) as JsonPrimitive).content
    // Fixture keys are ASCII, numbers safe integers; Unicode string values retain exact code points.
    fun sorted(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + sorted(it.value)
        }
        else -> value.toString()
    }
    fun bytes(value: JsonElement): ByteArray = sorted(value).toByteArray(Charsets.UTF_8)
    fun change(body: JsonObject, name: String, value: JsonElement): JsonObject = JsonObject(body + (name to value))
    fun rejects(block: () -> Unit) {
        val failure = runCatching(block).exceptionOrNull()
        assertTrue("expected sanitized recovery/deletion rejection", failure is Codec.Rejected)
        assertTrue(failure?.cause == null && failure?.suppressed?.isEmpty() == true)
        assertTrue(failure.toString().startsWith("RecoveryDeletionV2.Rejected("))
    }
    fun p1363(der: ByteArray): ByteArray {
        var cursor = 0
        assertTrue(der[cursor++] == 0x30.toByte())
        assertTrue((der[cursor++].toInt() and 255) == der.size - 2)
        val result = ByteArray(64)
        for (offset in listOf(0, 32)) {
            assertTrue(der[cursor++] == 2.toByte())
            val size = der[cursor++].toInt() and 255
            val value = BigInteger(der.copyOfRange(cursor, cursor + size)); cursor += size
            assertTrue(value.signum() > 0)
            val significant = value.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            assertTrue(significant.size <= 32)
            significant.copyInto(result, offset + 32 - significant.size)
        }
        assertTrue(cursor == der.size)
        return result
    }
    fun der(proof: ByteArray): ByteArray {
        val r = BigInteger(1, proof.copyOfRange(0, 32)).toByteArray()
        val s = BigInteger(1, proof.copyOfRange(32, 64)).toByteArray()
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 2, r.size.toByte()) + r + byteArrayOf(2, s.size.toByte()) + s
    }
    fun sign(pair: KeyPair, message: ByteArray): ByteArray = p1363(Signature.getInstance("SHA256withECDSA").run {
        initSign(pair.private); update(message); sign()
    })
    fun verify(pair: KeyPair, message: ByteArray, proof: ByteArray): Boolean = Signature.getInstance("SHA256withECDSA").run {
        initVerify(pair.public); update(message); verify(der(proof))
    }
    val domains = mapOf(
        BodyKind.RECOVERY_PREVIEW to "autplay:device-compatibility:recovery:preview:v2\n",
        BodyKind.RECOVERY_COMMIT to "autplay:device-compatibility:recovery:recover:v2\n",
        BodyKind.DELETION_REQUEST to "autplay:device-compatibility:deletion:request:v2\n",
        BodyKind.DELETION_PREVIEW to "autplay:device-compatibility:deletion:preview:v2\n",
        BodyKind.DELETION_CANCEL to "autplay:device-compatibility:deletion:cancel:v2\n")
    val paths = mapOf(Route.RECOVERY_PREVIEW to "/recovery/preview", Route.RECOVERY_COMMIT to "/recovery/commit",
        Route.RECOVERY_OUTCOME to "/recovery/outcome", Route.DELETION_REQUEST to "/deletion/requests",
        Route.DELETION_REQUEST_RECEIPT to "/deletion/request-receipt", Route.DELETION_REQUEST_RESOLVE to "/deletion/request-resolve",
        Route.DELETION_PREVIEW to "/deletion/cancel/preview", Route.DELETION_CANCEL to "/deletion/cancel/commit",
        Route.DELETION_CANCEL_OUTCOME to "/deletion/cancel/outcome")
    val identityPair = key(); val device = key(); val replacement = key(); val wrong = key()
    val spki = device.public.encoded
    fun identity(server: String = instance, epoch: Long = 2, thumb: String = hex(sha(identityPair.public.encoded)),
        apiOrigin: String = api, streamOrigin: String = stream): TrustedM5Identity =
        TrustedM5Identity(server, epoch, identityPair.public.encoded, thumb, apiOrigin, streamOrigin)
    val trusted = identity()
    fun context(kind: BodyKind, platform: Platform, publicKey: ByteArray = spki, id: TrustedM5Identity = trusted): Codec.Context = when (kind) {
        BodyKind.RECOVERY_PREVIEW -> Codec.recoveryPreviewContext(id, platform, publicKey, account, operation)
        BodyKind.RECOVERY_COMMIT -> Codec.recoveryCommitContext(id, platform, publicKey, account, operation, account, 7, verifier, refresh, binding)
        BodyKind.DELETION_REQUEST -> Codec.deletionRequestContext(id, platform, publicKey, account, operation, account, 7, 9)
        BodyKind.DELETION_PREVIEW -> Codec.deletionPreviewContext(id, platform, publicKey, account, operation)
        BodyKind.DELETION_CANCEL -> Codec.deletionCancelContext(id, platform, publicKey, account, operation, account, 7, verifier, refresh, binding, parent, 1)
    }
    fun unsigned(kind: BodyKind, platform: Platform, publicKey: ByteArray = spki): JsonObject {
        val common = mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "platform" to JsonPrimitive(platform.name), "expected_server_instance_id" to JsonPrimitive(instance),
            "expected_identity_epoch" to JsonPrimitive(2), "expected_identity_thumbprint_sha256" to JsonPrimitive(trusted.thumbprintSha256),
            "expected_api_origin" to JsonPrimitive(api), "expected_stream_origin" to JsonPrimitive(stream),
            "account_id" to JsonPrimitive(account), "operation_id" to JsonPrimitive(operation),
            "requested_at" to JsonPrimitive(requested), "device_public_key_spki_b64" to JsonPrimitive(b64(publicKey)),
            "device_key_thumbprint_sha256" to JsonPrimitive(hex(sha(publicKey))), "device_name" to JsonPrimitive("Fixture \u00e9 \ud83c\udfb5"),
            "app_version" to JsonPrimitive("fixture-2"))
        val commit = mapOf("confirmed_account_id" to JsonPrimitive(account), "expected_code_generation" to JsonPrimitive(7),
            "next_code_verifier_sha256" to JsonPrimitive(verifier), "next_refresh_token_sha256" to JsonPrimitive(refresh),
            "binding_commit_id" to JsonPrimitive(binding))
        return JsonObject(common + when (kind) {
            BodyKind.RECOVERY_PREVIEW, BodyKind.DELETION_PREVIEW -> emptyMap()
            BodyKind.RECOVERY_COMMIT -> commit
            BodyKind.DELETION_REQUEST -> mapOf("confirmed_account_id" to JsonPrimitive(account),
                "expected_code_generation" to JsonPrimitive(7), "expected_authority_generation" to JsonPrimitive(9))
            BodyKind.DELETION_CANCEL -> commit + mapOf("deletion_request_id" to JsonPrimitive(parent), "expected_revision" to JsonPrimitive(1))
        })
    }
    fun message(kind: BodyKind, body: JsonObject): ByteArray = domains.getValue(kind).toByteArray(Charsets.US_ASCII) + sha(bytes(body))
    // Recompute BOTH digest and valid proof after structural/context mutations: stale hashes cannot mask failures.
    fun signed(kind: BodyKind, body: JsonObject, pair: KeyPair = device): ByteArray = bytes(JsonObject(body + mapOf(
        "request_sha256" to JsonPrimitive(hex(sha(bytes(body)))), "device_signature_b64url" to JsonPrimitive(url(sign(pair, message(kind, body)))))))
    fun rejectedUnsigned(scope: Codec.Context, body: JsonObject) {
        rejects {
            Codec.prepare(scope, bytes(body)).use { prepared ->
                Codec.sign(prepared) { _, _ -> throw AssertionError("invalid input reached signer sentinel") }
            }
        }
        rejects { Codec.verifyOriginal(scope, signed(scope.bodyKind, body)).close() }
    }

    assertTrue(domains.keys == BodyKind.entries.toSet() && paths.keys == Route.entries.toSet())
    for (platform in Platform.entries) for (field in listOf("expected_api_origin", "expected_stream_origin")) {
        for (kind in BodyKind.entries) for (origin in listOf("https://node.123", "https://node.1a",
            "https://NODE.123.:00443", "https://[::ffff:192.0.2.1]:65535")) {
            val trust = identity(apiOrigin = if (field == "expected_api_origin") origin else api,
                streamOrigin = if (field == "expected_stream_origin") origin else stream)
            val scope = context(kind, platform, id = trust)
            val body = change(unsigned(kind, platform), field, JsonPrimitive(origin))
            Codec.prepare(scope, bytes(body)).use { prepared ->
                assertArrayEquals(bytes(body), prepared.unsignedCopy())
                assertArrayEquals(message(kind, body), prepared.signingMessageCopy())
                val proof = sign(device, message(kind, body))
                Codec.complete(prepared, proof).use { command ->
                    assertTrue(verify(device, message(kind, body), command.signatureCopy()))
                    val wire = command.fullOriginalWireCopy()
                    try {
                        assertTrue(obj(wire)[field] == JsonPrimitive(origin))
                        Codec.verifyOriginal(scope, wire).use { assertArrayEquals(wire, it.fullOriginalWireCopy()) }
                        rejects { Codec.verifyOriginal(context(kind, platform), wire).close() }
                    } finally { wire.fill(0); proof.fill(0) }
                }
            }
        }
        for (origin in listOf("https://node.123:0", "https://node.123:000001", "https://node.123:65536",
            "https://node.123:", "https://-node.123", "https://node-.123", "https://node..123",
            "https://${"a".repeat(64)}.123", "https://[::ffff:192.000.2.1]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
            for (kind in listOf(BodyKind.RECOVERY_PREVIEW, BodyKind.DELETION_REQUEST)) {
                rejects { context(kind, platform, id = identity(apiOrigin = if (field == "expected_api_origin") origin else api,
                    streamOrigin = if (field == "expected_stream_origin") origin else stream)) }
                // Existing helper creates a fresh correct digest and MESSAGE signature for readback too.
                rejectedUnsigned(context(kind, platform), change(unsigned(kind, platform), field, JsonPrimitive(origin)))
            }
        }
    }
    for (platform in Platform.entries) for (kind in BodyKind.entries) {
        val scope = context(kind, platform)
        val body = unsigned(kind, platform)
        val raw = bytes(body)
        val canonical = raw.copyOf()
        val prepared = Codec.prepare(scope, raw)
        raw.fill(0)
        val expectedRole = if (kind == BodyKind.DELETION_REQUEST) Codec.Role.CURRENT_AUTHENTICATED_ACTOR else Codec.Role.CURRENT_RECIPIENT
        assertTrue(prepared.role == expectedRole && prepared.platform == platform && prepared.bodyKind == kind)
        assertArrayEquals(canonical, prepared.unsignedCopy())
        assertArrayEquals(message(kind, body), prepared.signingMessageCopy())
        prepared.publicKeySpkiCopy().fill(0); prepared.requestDigestCopy().fill(0); prepared.unsignedCopy().fill(0)
        assertArrayEquals(spki, prepared.publicKeySpkiCopy())
        var calls = 0; var produced: ByteArray? = null
        val command = Codec.sign(prepared) { operationKind, digest ->
            calls++
            assertTrue(operationKind == kind)
            assertArrayEquals(sha(canonical), digest)
            sign(device, domains.getValue(kind).toByteArray(Charsets.US_ASCII) + digest).also { produced = it }
        }
        produced?.fill(0)
        assertTrue(calls == 1)
        rejects { Codec.complete(prepared, ByteArray(64)) }
        val full = command.fullOriginalWireCopy()
        val document = obj(full)
        assertTrue(document.keys == body.keys + setOf("request_sha256", "device_signature_b64url"))
        assertTrue(text(document, "request_sha256") == hex(sha(canonical)))
        assertTrue(verify(device, message(kind, body), command.signatureCopy()))
        assertTrue(text(document, "device_signature_b64url") == url(command.signatureCopy()))
        Codec.verifyOriginal(scope, full).use { assertArrayEquals(full, it.fullOriginalWireCopy()) }
        Codec.prepare(scope, canonical).use { completion ->
            val signature = sign(device, message(kind, body))
            Codec.complete(completion, signature).use { completed ->
                signature.fill(0)
                assertTrue(verify(device, message(kind, body), completed.signatureCopy()))
            }
        }
        command.fullOriginalWireCopy().fill(0); command.signatureCopy().fill(0)
        assertArrayEquals(full, command.fullOriginalWireCopy())
        for (route in Route.entries.filter { it.bodyKind == kind }) {
            assertTrue(route.path == "/api/v1/device-compatibility/v2" + paths.getValue(route) && route.method == "POST")
            assertTrue(route.proofDomain == domains.getValue(kind))
            if (route.originalOnly) {
                val history = Codec.continuationContext(command, route)
                val historicalActorRead = route == Route.DELETION_REQUEST_RECEIPT || route == Route.DELETION_REQUEST_RESOLVE
                assertTrue(history.role == if (historicalActorRead) Codec.Role.HISTORICAL_READ_ORIGINAL else Codec.Role.CURRENT_RECIPIENT)
                rejects { Codec.prepare(history, canonical).close() }
                rejects { Codec.verifyOriginal(history, full).close() }
                rejects { Codec.request(command, route).close() }
                Codec.readOriginal(history, route, full).use {
                    assertArrayEquals(full, it.bodyCopy()); assertTrue(it.role == route.role && it.platform == platform)
                    it.bodyCopy().fill(0); assertArrayEquals(full, it.bodyCopy())
                }
                // Alternate mathematically valid ECDSA signature (r, n-s) is NOT the original proof.
                val alternate = command.signatureCopy()
                val order = (device.public as ECPublicKey).params.order
                val alternateS = order.subtract(BigInteger(1, alternate.copyOfRange(32, 64))).toByteArray()
                    .dropWhile { it == 0.toByte() }.toByteArray()
                alternate.fill(0, 32, 64); alternateS.copyInto(alternate, 64 - alternateS.size)
                assertTrue(verify(device, message(kind, body), alternate))
                val replaced = bytes(JsonObject(document + ("device_signature_b64url" to JsonPrimitive(url(alternate)))))
                Codec.verifyOriginal(scope, replaced).close() // Valid in isolation, but cannot replace retained original.
                rejects { Codec.readOriginal(history, route, replaced).close() }
                rejects { Codec.readOriginal(history, route, bytes(body)).close() }
                rejects { Codec.readOriginal(history, route, ByteArray(32)).close() }
                for (wrongRoute in Route.entries.filter { it != route }) {
                    rejects { Codec.readOriginal(history, wrongRoute, full).close() }
                }
                if (route == Route.DELETION_REQUEST_RESOLVE) {
                    Codec.deletionResolutionCandidate(history, full).use { assertArrayEquals(full, it.originalRequestBodyCopy()) }
                    assertTrue(history.role == Codec.Role.HISTORICAL_READ_ORIGINAL)
                }
            } else {
                Codec.request(command, route).use { assertArrayEquals(full, it.bodyCopy()) }
                rejects { Codec.continuationContext(command, route) }
            }
        }
        for (route in Route.entries.filter { it.bodyKind != kind }) rejects { Codec.request(command, route).close() }
        // Current context is externally trusted; each changed scope is rejected even with fresh valid hash/proof.
        for (name in listOf("account_id", "operation_id", "confirmed_account_id", "binding_commit_id", "deletion_request_id")) {
            if (name in body) rejectedUnsigned(scope, JsonObject(body + (name to JsonPrimitive(other))))
        }
        for (name in listOf("expected_code_generation", "expected_authority_generation", "expected_revision")) {
            if (name in body) rejectedUnsigned(scope, JsonObject(body + (name to JsonPrimitive(11))))
        }
        for (name in listOf("next_code_verifier_sha256", "next_refresh_token_sha256")) {
            if (name in body) rejectedUnsigned(scope, JsonObject(body + (name to JsonPrimitive("c".repeat(64)))))
        }
        val trustMutations = mapOf("expected_server_instance_id" to JsonPrimitive(other), "expected_identity_epoch" to JsonPrimitive(3),
            "expected_identity_thumbprint_sha256" to JsonPrimitive("c".repeat(64)), "expected_api_origin" to JsonPrimitive("https://other.example.invalid"),
            "expected_stream_origin" to JsonPrimitive("https://other.example.invalid"))
        for ((name, value) in trustMutations) rejectedUnsigned(scope, JsonObject(body + (name to value)))
        rejectedUnsigned(scope, JsonObject(body + ("platform" to JsonPrimitive(if (platform == Platform.ANDROID) "MACOS" else "ANDROID"))))
        rejectedUnsigned(scope, JsonObject(body + ("platform" to JsonPrimitive("OTHER"))))
        rejectedUnsigned(scope, JsonObject(body + ("contract_version" to JsonPrimitive("v1"))))
        rejectedUnsigned(scope, JsonObject(body + ("schema_version" to JsonPrimitive(1))))
        for (required in body.keys) {
            rejectedUnsigned(scope, JsonObject(body + (required to JsonNull)))
            rejectedUnsigned(scope, JsonObject(body - required))
        }
        rejectedUnsigned(scope, JsonObject(body + ("unexpected" to JsonPrimitive("closed"))))
        rejectedUnsigned(scope, JsonObject(body + ("device_name" to JsonPrimitive("n".repeat(121)))))
        rejectedUnsigned(scope, JsonObject(body + ("app_version" to JsonPrimitive("v".repeat(33)))))
        rejectedUnsigned(scope, JsonObject(body + ("device_name" to JsonPrimitive("bad\u007f"))))
        rejectedUnsigned(scope, JsonObject(body + ("requested_at" to JsonPrimitive("2026-02-30T09:00:00Z"))))
        rejectedUnsigned(scope, JsonObject(body + ("requested_at" to JsonPrimitive("0000-01-01T09:00:00Z"))))
        rejectedUnsigned(scope, JsonObject(body + ("requested_at" to JsonPrimitive("2026-10-16T09:00:00+00:00"))))
        rejectedUnsigned(scope, unsigned(kind, platform, wrong.public.encoded))
        val offCurve = spki.copyOf().also { it.fill(0, 27, it.size) }
        rejectedUnsigned(scope, unsigned(kind, platform, offCurve))
        rejectedUnsigned(scope, unsigned(kind, platform, spki + byteArrayOf(0)))
        rejectedUnsigned(scope, JsonObject(body + ("device_public_key_spki_b64" to JsonPrimitive(b64(spki).dropLast(1)))))
        rejectedUnsigned(scope, JsonObject(body + ("device_key_thumbprint_sha256" to JsonPrimitive("f".repeat(64)))))
        val signedWrongKey = signed(kind, body, wrong)
        rejects { Codec.verifyOriginal(scope, signedWrongKey).close() }
        for (badProof in listOf(url(ByteArray(64)), text(document, "device_signature_b64url") + "=", "_".repeat(86))) {
            rejects { Codec.verifyOriginal(scope, bytes(JsonObject(document + ("device_signature_b64url" to JsonPrimitive(badProof))))).close() }
        }
        for (domain in domains.values.filter { it != domains.getValue(kind) } + domains.getValue(kind).replace("\n", "\\n")) {
            val proof = sign(device, domain.toByteArray(Charsets.US_ASCII) + sha(canonical))
            rejects { Codec.verifyOriginal(scope, bytes(JsonObject(document + ("device_signature_b64url" to JsonPrimitive(url(proof)))))).close() }
        }
        for (token in listOf("true", "null", "2.0", "2e0", "\"2\"", "9007199254740992", "-1")) {
            val malformed = full.toString(Charsets.UTF_8).replace("\"schema_version\":2", "\"schema_version\":$token")
            rejects { Codec.verifyOriginal(scope, malformed.toByteArray()).close() }
            val malformedUnsigned = canonical.toString(Charsets.UTF_8).replace("\"schema_version\":2", "\"schema_version\":$token")
            rejects {
                Codec.prepare(scope, malformedUnsigned.toByteArray()).use { lexical ->
                    Codec.sign(lexical) { _, _ -> throw AssertionError("noninteger token reached signer sentinel") }
                }
            }
        }
        val source = full.toString(Charsets.UTF_8)
        // Escaped decoded duplicate name with identical value cannot be concealed by last-key-wins.
        val duplicate = source.dropLast(1) + ",\"\\u0073chema_version\":2}"
        rejects { Codec.verifyOriginal(scope, duplicate.toByteArray()).close() }
        rejects { Codec.verifyOriginal(scope, (source + "{}").toByteArray()).close() }
        rejects { Codec.verifyOriginal(scope, byteArrayOf(0xc3.toByte(), 0x28)).close() }
        rejects { Codec.verifyOriginal(scope, source.replace("fixture-2", "\\ud800").toByteArray()).close() }
        rejects { Codec.verifyOriginal(scope, ByteArray(8193) { 32 }).close() }
        rejects { Codec.prepare(scope, ByteArray(8193) { 32 }).close() }
        Codec.prepare(scope, canonical).use { rejects { Codec.complete(it, sign(wrong, message(kind, body))).close() } }
        Codec.prepare(scope, canonical).use { rejects { Codec.complete(it, ByteArray(63)).close() } }
        // Exact old original remains locally verifiable without a clock/fresh-bearer/one-day veto.
        val old = JsonObject(body + ("requested_at" to JsonPrimitive("2020-01-01T00:00:00Z")))
        Codec.verifyOriginal(scope, signed(kind, old)).close()
        prepared.close(); rejects { prepared.unsignedCopy() }
        command.close(); rejects { command.fullOriginalWireCopy() }
    }
    rejects { context(BodyKind.RECOVERY_PREVIEW, Platform.MACOS, id = identity(thumb = "c".repeat(64))) }
    rejects { context(BodyKind.RECOVERY_PREVIEW, Platform.MACOS, id = identity(apiOrigin = api + "/")) }
    rejects { context(BodyKind.RECOVERY_PREVIEW, Platform.MACOS, id = identity(apiOrigin = api + ":")) }
    rejects { Codec.recoveryCommitContext(trusted, Platform.MACOS, spki, account, operation, other, 7, verifier, refresh, binding) }
    // Same historical actor can prove only retained request reads. New recipient may be the other OS/key.
    val actorScope = context(BodyKind.DELETION_REQUEST, Platform.ANDROID)
    Codec.verifyOriginal(actorScope, signed(BodyKind.DELETION_REQUEST, unsigned(BodyKind.DELETION_REQUEST, Platform.ANDROID))).use { actor ->
        val history = Codec.continuationContext(actor, Route.DELETION_REQUEST_RECEIPT)
        rejects { Codec.prepare(history, bytes(unsigned(BodyKind.DELETION_REQUEST, Platform.ANDROID))).close() }
        rejects { Codec.readOriginal(history, Route.DELETION_CANCEL_OUTCOME, actor.fullOriginalWireCopy()).close() }
        val recipientScope = context(BodyKind.DELETION_CANCEL, Platform.MACOS, replacement.public.encoded)
        val cancel = unsigned(BodyKind.DELETION_CANCEL, Platform.MACOS, replacement.public.encoded)
        Codec.verifyOriginal(recipientScope, signed(BodyKind.DELETION_CANCEL, cancel, replacement)).use { recipient ->
            assertTrue(actor.platform == Platform.ANDROID && recipient.platform == Platform.MACOS)
            rejects { Codec.verifyOriginal(recipientScope, actor.fullOriginalWireCopy()).close() }
        }
    }
}
