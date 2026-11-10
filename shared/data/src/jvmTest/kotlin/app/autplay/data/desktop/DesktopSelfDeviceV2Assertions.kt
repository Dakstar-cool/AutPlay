package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as Negotiation
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.devicecompatibility.SelfDeviceContractV2 as Codec
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
 * FUTURE assertions called only inside the existing persistence composite. No new test entry.
 * Source-written, UNVERIFIED: no hash, key generation, signing, parsing or assertion ran while
 * authoring. Independent sorted scalar serialization and MESSAGE verification provide the
 * future oracle; this is not Android Keystore/Mac/HTTP/server/persistence/interop evidence.
 */
internal fun desktopSelfDeviceV2Assertions(): Unit {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val instance = "11111111-1111-4111-8111-111111111111"
    val ceremony = "22222222-2222-4222-8222-222222222222"
    val claimId = "33333333-3333-4333-8333-333333333333"
    val operationId = "44444444-4444-4444-8444-444444444444"
    val approvalId = "55555555-5555-4555-8555-555555555555"
    val accountId = "66666666-6666-4666-8666-666666666666"
    val otherId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    fun key(curve: String = "secp256r1"): KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec(curve)); generateKeyPair()
    }
    fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
    fun url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    fun text(value: JsonElement?): String = (value as JsonPrimitive).content
    fun obj(bytes: ByteArray): JsonObject = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as JsonObject
    // Fixtures use only scalar strings/null/lexical integers; UTF-16 key sorting matches JCS here.
    fun sorted(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + sorted(it.value)
        }
        else -> value.toString()
    }
    fun bytes(value: JsonElement): ByteArray = sorted(value).toByteArray(Charsets.UTF_8)
    fun change(value: JsonObject, name: String, field: JsonElement): JsonObject = JsonObject(value + (name to field))
    fun rejects(block: () -> Unit) {
        val failure = runCatching(block).exceptionOrNull()
        assertTrue("expected payload-free Self v2 rejection", failure is Codec.Rejected)
        assertNull(failure?.cause)
        assertTrue(failure!!.suppressed.isEmpty())
        assertFalse(failure.toString().contains(api))
    }
    fun toP1363(der: ByteArray): ByteArray {
        var index = 0
        assertEquals(0x30, der[index++].toInt() and 255)
        assertEquals(der.size - 2, der[index++].toInt() and 255)
        val output = ByteArray(64)
        for (offset in listOf(0, 32)) {
            assertEquals(2, der[index++].toInt() and 255)
            val count = der[index++].toInt() and 255
            val integer = BigInteger(der.copyOfRange(index, index + count))
            index += count
            assertTrue(integer.signum() > 0)
            val significant = integer.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            assertTrue(significant.size <= 32)
            significant.copyInto(output, offset + 32 - significant.size)
        }
        assertEquals(der.size, index)
        return output
    }
    fun toDer(proof: ByteArray): ByteArray {
        val r = BigInteger(1, proof.copyOfRange(0, 32)).toByteArray()
        val s = BigInteger(1, proof.copyOfRange(32, 64)).toByteArray()
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 2, r.size.toByte()) + r + byteArrayOf(2, s.size.toByte()) + s
    }
    fun signed(key: KeyPair, message: ByteArray): ByteArray = toP1363(Signature.getInstance("SHA256withECDSA").run {
        initSign(key.private); update(message); sign()
    })
    fun message(domain: String, digest: ByteArray): ByteArray = domain.toByteArray(Charsets.US_ASCII) + digest
    val identityKey = key()
    val deviceKey = key()
    val differentKey = key()
    val identityThumb = hex(sha(identityKey.public.encoded))
    fun trusted(id: String = instance, epoch: Long = 1, origin: String = api,
        streamOrigin: String = stream, thumb: String = identityThumb): TrustedM5Identity =
        TrustedM5Identity(id, epoch, identityKey.public.encoded, thumb, origin, streamOrigin)
    fun trustFields(trust: TrustedM5Identity): Map<String, JsonElement> = mapOf(
        "expected_server_instance_id" to JsonPrimitive(trust.serverInstanceId),
        "expected_identity_epoch" to JsonPrimitive(trust.identityEpoch),
        "expected_identity_thumbprint_sha256" to JsonPrimitive(trust.thumbprintSha256),
        "expected_api_origin" to JsonPrimitive(trust.apiOrigin), "expected_stream_origin" to JsonPrimitive(trust.streamOrigin))
    fun common(trust: TrustedM5Identity, platform: Platform, id: String = ceremony): Map<String, JsonElement> = trustFields(trust) + mapOf(
        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2), "platform" to JsonPrimitive(platform.name),
        "ceremony_id" to JsonPrimitive(id), "requested_at" to JsonPrimitive(now.toString()))
    fun discovery(trust: TrustedM5Identity = trusted(), families: List<String> = listOf("self"),
        expires: Instant = now.plusSeconds(600)): Negotiation.Discovery {
        val payload = JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "server_instance_id" to JsonPrimitive(trust.serverInstanceId), "identity_epoch" to JsonPrimitive(trust.identityEpoch),
            "identity_thumbprint_sha256" to JsonPrimitive(trust.thumbprintSha256),
            "identity_public_key_spki_b64" to JsonPrimitive(b64(identityKey.public.encoded)),
            "api_origin" to JsonPrimitive(trust.apiOrigin), "stream_origin" to JsonPrimitive(trust.streamOrigin),
            "api_major" to JsonPrimitive(1), "issued_at" to JsonPrimitive(now.toString()), "expires_at" to JsonPrimitive(expires.toString()),
            "families" to JsonArray(families.map { JsonPrimitive(it) }),
            "platforms" to JsonArray(Platform.entries.map { JsonPrimitive(it.name) })))
        val digest = sha(bytes(payload))
        val envelope = JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "payload" to payload, "payload_sha256" to JsonPrimitive(hex(digest)), "signature_algorithm" to JsonPrimitive("ES256-P1363"),
            "signature_b64url" to JsonPrimitive(url(signed(identityKey,
                message("autplay:device-compatibility:negotiation:discovery:v2\n", digest))))))
        return (Negotiation.discovery(bytes(envelope), trust, now) as Negotiation.Result.Verified<Negotiation.Discovery>).value
    }
    fun sasOracle(trust: TrustedM5Identity, id: String, claimHash: String, thumb: String,
        domain: String = "autplay:device-compatibility:self:sas:v2\n"): String {
        val payload = JsonObject(mapOf("server_instance_id" to JsonPrimitive(trust.serverInstanceId),
            "identity_epoch" to JsonPrimitive(trust.identityEpoch), "ceremony_id" to JsonPrimitive(id),
            "claim_request_sha256" to JsonPrimitive(claimHash), "key_thumbprint" to JsonPrimitive(thumb)))
        val digest = sha(message(domain, sha(bytes(payload))))
        return BigInteger(1, digest.copyOfRange(0, 8)).mod(BigInteger.TEN.pow(12)).toString().padStart(12, '0')
    }
    fun assertWire(prepared: Codec.PreparedRequest, wire: Codec.WireRequest, unsigned: JsonObject, publicKey: KeyPair = deviceKey) {
        val canonical = bytes(unsigned)
        val digest = sha(canonical)
        assertArrayEquals(canonical, prepared.canonicalUnsignedCopy())
        assertArrayEquals(digest, prepared.requestDigestCopy())
        assertEquals(hex(digest), wire.requestSha256)
        val document = obj(wire.canonicalDocumentCopy())
        assertEquals(unsigned, JsonObject(document.filterKeys { it !in setOf("request_sha256", "device_signature_b64url") }))
        assertEquals(hex(digest), text(document["request_sha256"]))
        assertArrayEquals(bytes(document), wire.canonicalDocumentCopy())
        assertArrayEquals(sha(bytes(document)), wire.originalDocumentDigestCopy())
        if (wire.platformRole == Codec.Role.RECIPIENT) {
            val domain = "autplay:device-compatibility:self:${wire.operation.name.lowercase()}:v2\n"
            val proof = Base64.getUrlDecoder().decode(text(document["device_signature_b64url"]))
            assertEquals(64, proof.size)
            assertEquals(url(proof), text(document["device_signature_b64url"]))
            assertArrayEquals(proof, wire.signatureCopy())
            assertTrue(Signature.getInstance("SHA256withECDSA").run {
                initVerify(publicKey.public); update(message(domain, digest)); verify(toDer(proof))
            })
            wire.signatureCopy()!!.fill(0)
            assertArrayEquals(proof, wire.signatureCopy())
            prepared.publicKeySpkiCopy()!!.fill(0)
            assertArrayEquals(publicKey.public.encoded, prepared.publicKeySpkiCopy())
        } else {
            assertFalse(document.containsKey("device_signature_b64url"))
            assertNull(wire.signatureCopy())
        }
        val copy = wire.canonicalDocumentCopy()
        copy.fill(0)
        assertArrayEquals(bytes(document), wire.canonicalDocumentCopy())
        prepared.canonicalUnsignedCopy().fill(0)
        assertArrayEquals(canonical, prepared.canonicalUnsignedCopy())
        val digestCopy = prepared.requestDigestCopy()
        digestCopy.fill(0)
        assertArrayEquals(digest, prepared.requestDigestCopy())
        val evidenceCopy = wire.originalDocumentDigestCopy()
        evidenceCopy.fill(0)
        assertArrayEquals(sha(bytes(document)), wire.originalDocumentDigestCopy())
        assertEquals("SelfV2.PreparedRequest", prepared.toString())
        assertEquals("SelfV2.WireRequest", wire.toString())
    }
    class PairFixture(val start: Codec.WireRequest, val claim: Codec.WireRequest,
        val source: Codec.Context, val recipient: Codec.Context, val startBody: JsonObject,
        val claimBody: JsonObject, val openStatus: JsonObject, val qrBytes: ByteArray)
    val secret = Base64.getUrlEncoder().withoutPadding().encode(ByteArray(32) { 7 })
    val secretHash = hex(sha(secret)) // Hash ENCODED ASCII secret, never its decoded bytes.
    val pollHash = hex(sha("synthetic-future-poll-input".toByteArray(Charsets.US_ASCII)))
    val discovered = discovery()
    fun pair(sourcePlatform: Platform, recipientPlatform: Platform, trust: TrustedM5Identity = trusted(),
        id: String = ceremony, recipientKey: KeyPair = deviceKey, name: String = "Phone \u21162"): PairFixture {
        val scope = Codec.sourceContext(trust, sourcePlatform, id)
        val body = JsonObject(common(trust, sourcePlatform, id) + mapOf("operation_id" to JsonPrimitive(operationId),
            "rendezvous_secret_sha256" to JsonPrimitive(secretHash)))
        val prepared = Codec.prepare(Codec.Operation.START, bytes(body), scope, now)
        val start = Codec.completeBearer(prepared)
        assertWire(prepared, start, body)
        val status = JsonObject(mapOf("contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
            "ceremony_id" to JsonPrimitive(id), "state" to JsonPrimitive("OPEN"), "revision" to JsonPrimitive(1),
            "expires_at" to JsonPrimitive(now.plusSeconds(900).toString()), "retry_after_seconds" to JsonPrimitive(2),
            "source_platform" to JsonPrimitive(sourcePlatform.name), "recipient_platform" to JsonNull))
        val secretInput = secret.copyOf()
        val sourceQr = Codec.qrFromStart(start, bytes(status), secretInput, now)
        secretInput.fill(0)
        rejects { Codec.recipientContext(sourceQr, recipientPlatform, recipientKey.public.encoded, now) }
        val encoded = sourceQr.encode()
        val parsedQr = Codec.readQr(encoded, trust, discovery(trust), recipientPlatform, now)
        rejects { Codec.recipientContext(parsedQr,
            if (recipientPlatform == Platform.ANDROID) Platform.MACOS else Platform.ANDROID, recipientKey.public.encoded, now) }
        val publicInput = recipientKey.public.encoded.copyOf()
        val recipient = Codec.recipientContext(parsedQr, recipientPlatform, publicInput, now)
        publicInput.fill(0)
        parsedQr.close()
        val copy = sourceQr.rendezvousSecretCopy()
        assertArrayEquals(secret, copy)
        copy.fill(0)
        assertArrayEquals(secret, sourceQr.rendezvousSecretCopy())
        sourceQr.close()
        rejects { sourceQr.encode() }
        rejects { sourceQr.rendezvousSecretCopy() }
        rejects { Codec.recipientContext(sourceQr, recipientPlatform, recipientKey.public.encoded, now) }
        val claimBody = JsonObject(common(trust, recipientPlatform, id) + mapOf("claim_id" to JsonPrimitive(claimId),
            "poll_secret_sha256" to JsonPrimitive(pollHash), "device_public_key_spki_b64" to JsonPrimitive(b64(recipientKey.public.encoded)),
            "device_key_thumbprint_sha256" to JsonPrimitive(hex(sha(recipientKey.public.encoded))),
            "device_name" to JsonPrimitive(name), "app_version" to JsonPrimitive("fixture-2")))
        val raw = bytes(claimBody)
        val claimPrepared = Codec.prepare(Codec.Operation.CLAIM, raw, recipient, now)
        raw.fill(0)
        val signature = signed(recipientKey, message("autplay:device-compatibility:self:claim:v2\n", sha(bytes(claimBody))))
        val claim = Codec.complete(claimPrepared, signature)
        signature.fill(0)
        assertWire(claimPrepared, claim, claimBody, recipientKey)
        assertArrayEquals(start.canonicalDocumentCopy(), Codec.readRequest(Codec.Operation.START,
            start.canonicalDocumentCopy(), scope).canonicalDocumentCopy())
        assertArrayEquals(claim.canonicalDocumentCopy(), Codec.readRequest(Codec.Operation.CLAIM,
            claim.canonicalDocumentCopy(), recipient).canonicalDocumentCopy())
        assertEquals(sasOracle(trust, id, claim.requestSha256, hex(sha(recipientKey.public.encoded))), Codec.comparisonCode(start, claim))
        return PairFixture(start, claim, scope, recipient, body, claimBody, status, encoded)
    }
    fun claimStatus(fixture: PairFixture, state: String = "CLAIMED"): JsonObject {
        val base = fixture.openStatus + mapOf("state" to JsonPrimitive(state), "revision" to JsonPrimitive(2),
            "recipient_platform" to JsonPrimitive(fixture.claim.platform.name), "claim_id" to JsonPrimitive(claimId),
            "claim_request_sha256" to JsonPrimitive(fixture.claim.requestSha256), "device_name" to fixture.claimBody.getValue("device_name"),
            "device_key_thumbprint_sha256" to fixture.claimBody.getValue("device_key_thumbprint_sha256"),
            "comparison_code" to JsonPrimitive(Codec.comparisonCode(fixture.start, fixture.claim)),
            "account_id" to JsonPrimitive(accountId), "account_label" to JsonPrimitive("Synthetic account"))
        return JsonObject(base + if (state == "APPROVED") mapOf("approval_operation_id" to JsonPrimitive(approvalId)) else emptyMap())
    }
    try {
        for (platform in Platform.entries) for (field in listOf("expected_api_origin", "expected_stream_origin")) {
            for (origin in listOf("https://node.123", "https://node.1a", "https://NODE.123.:00443",
                "https://[2001:0DB8::1]:00001", "https://[::ffff:192.0.2.1]:65535")) {
                val trust = trusted(origin = if (field == "expected_api_origin") origin else api,
                    streamOrigin = if (field == "expected_stream_origin") origin else stream)
                val recipient = if (platform == Platform.MACOS) Platform.ANDROID else Platform.MACOS
                val f = pair(platform, recipient, trust)
                assertTrue(f.startBody[field] == JsonPrimitive(origin) && f.claimBody[field] == JsonPrimitive(origin))
                // A valid signed claim remains tied to its exact pre-established context.
                val untrusted = pair(platform, recipient)
                rejects { Codec.readRequest(Codec.Operation.CLAIM, f.claim.canonicalDocumentCopy(), untrusted.recipient) }
                f.qrBytes.fill(0); untrusted.qrBytes.fill(0)
            }
            // Frozen SELF alone accepts port zero. Do not route this family-only fixture via D1.
            for (origin in listOf("https://node.123:0", "https://NODE.123.:00000")) {
                val trust = trusted(origin = if (field == "expected_api_origin") origin else api,
                    streamOrigin = if (field == "expected_stream_origin") origin else stream)
                val scope = Codec.sourceContext(trust, platform, ceremony)
                val body = JsonObject(common(trust, platform) + mapOf("operation_id" to JsonPrimitive(operationId),
                    "rendezvous_secret_sha256" to JsonPrimitive(secretHash)))
                val prepared = Codec.prepare(Codec.Operation.START, bytes(body), scope, now)
                val wire = Codec.completeBearer(prepared)
                assertWire(prepared, wire, body)
                assertArrayEquals(wire.canonicalDocumentCopy(), Codec.readRequest(Codec.Operation.START,
                    wire.canonicalDocumentCopy(), scope).canonicalDocumentCopy())
            }
            for (origin in listOf("https://-node.123", "https://node-.123", "https://node..123", "https://node_1.123",
                "https://${"a".repeat(64)}.123", "https://node.123:", "https://node.123:000001", "https://node.123:65536",
                "https://[::ffff:192.000.2.1]", "https://[1:2:3:4:5:6:7:8:9]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
                rejects { Codec.sourceContext(trusted(origin = if (field == "expected_api_origin") origin else api,
                    streamOrigin = if (field == "expected_stream_origin") origin else stream), platform, ceremony) }
            }
        }
        val codes = mutableMapOf<Platform, String>()
        for (source in Platform.entries) for (recipient in Platform.entries) {
            val f = pair(source, recipient)
            val claimed = claimStatus(f)
            val decisionScope = Codec.decisionContext(f.start, bytes(claimed))
            for (action in listOf("APPROVE", "REJECT", "CANCEL")) {
                val decision = JsonObject(common(trusted(), source) + mapOf("operation_id" to JsonPrimitive(operationId),
                    "action" to JsonPrimitive(action), "expected_revision" to JsonPrimitive(2), "claim_id" to JsonPrimitive(claimId),
                    "claim_request_sha256" to JsonPrimitive(f.claim.requestSha256),
                    "comparison_code" to if (action == "APPROVE") claimed.getValue("comparison_code") else JsonNull))
                val prepared = Codec.prepare(Codec.Operation.DECISION, bytes(decision), decisionScope, now)
                val wire = Codec.completeBearer(prepared)
                assertWire(prepared, wire, decision)
                assertArrayEquals(wire.canonicalDocumentCopy(), Codec.readRequest(Codec.Operation.DECISION,
                    wire.canonicalDocumentCopy(), decisionScope).canonicalDocumentCopy())
                rejects { Codec.prepare(Codec.Operation.DECISION, bytes(change(decision, "expected_revision", JsonPrimitive(3))), decisionScope, now) }
                rejects { Codec.prepare(Codec.Operation.DECISION, bytes(change(decision, "claim_request_sha256", JsonPrimitive("a".repeat(64)))), decisionScope, now) }
                rejects { Codec.prepare(Codec.Operation.DECISION, bytes(change(decision, "comparison_code",
                    if (action == "APPROVE") JsonNull else JsonPrimitive("000000000000"))), decisionScope, now) }
                rejects { Codec.complete(prepared, ByteArray(64)) }
            }
            val continuation = Codec.continuationContext(f.claim, bytes(claimed))
            val poll = JsonObject(common(trusted(), recipient) + mapOf("claim_id" to JsonPrimitive(claimId),
                "claim_request_sha256" to JsonPrimitive(f.claim.requestSha256)))
            val approved = claimStatus(f, "APPROVED")
            val exchangeScope = Codec.continuationContext(f.claim, bytes(approved))
            val exchange = JsonObject(poll + mapOf("exchange_id" to JsonPrimitive(operationId), "binding_commit_id" to JsonPrimitive(otherId),
                "approval_operation_id" to JsonPrimitive(approvalId), "confirmed_account_id" to JsonPrimitive(accountId),
                "next_refresh_token_sha256" to JsonPrimitive("d".repeat(64))))
            for ((operation, body, scope) in listOf(Triple(Codec.Operation.POLL, poll, continuation),
                Triple(Codec.Operation.EXCHANGE, exchange, exchangeScope))) {
                val prepared = Codec.prepare(operation, bytes(body), scope, now)
                val domain = "autplay:device-compatibility:self:${operation.name.lowercase()}:v2\n"
                val signature = signed(deviceKey, message(domain, sha(bytes(body))))
                val wire = Codec.complete(prepared, signature)
                assertWire(prepared, wire, body)
                assertArrayEquals(wire.canonicalDocumentCopy(), Codec.readRequest(operation, wire.canonicalDocumentCopy(), scope).canonicalDocumentCopy())
                rejects { Codec.prepare(operation, bytes(change(body, "claim_id", JsonPrimitive(otherId))), scope, now) }
                rejects { Codec.prepare(operation, bytes(change(body, "claim_request_sha256", JsonPrimitive("b".repeat(64)))), scope, now) }
                rejects { Codec.prepare(operation, bytes(change(body, "platform", JsonPrimitive(if (recipient == Platform.MACOS) "ANDROID" else "MACOS"))), scope, now) }
                rejects { Codec.complete(prepared, signed(differentKey, message(domain, sha(bytes(body))))) }
                rejects { Codec.complete(prepared, signed(deviceKey, message(domain.replace(":v2", ":v1"), sha(bytes(body))))) }
                rejects { Codec.complete(prepared, signed(deviceKey, message(domain, sha(sha(bytes(body)))))) }
                rejects { Codec.completeBearer(prepared) }
                rejects { Codec.readRequest(operation, bytes(change(obj(wire.canonicalDocumentCopy()), "requested_at", JsonPrimitive(now.plusSeconds(1).toString()))), scope) }
            }
            rejects { Codec.prepare(Codec.Operation.EXCHANGE, bytes(exchange), continuation, now) }
            rejects { Codec.prepare(Codec.Operation.EXCHANGE, bytes(change(exchange, "approval_operation_id", JsonPrimitive(otherId))), exchangeScope, now) }
            rejects { Codec.prepare(Codec.Operation.EXCHANGE, bytes(change(exchange, "confirmed_account_id", JsonPrimitive(otherId))), exchangeScope, now) }
            for ((name, value) in listOf("source_platform" to JsonPrimitive(if (source == Platform.MACOS) "ANDROID" else "MACOS"),
                "recipient_platform" to JsonPrimitive(if (recipient == Platform.MACOS) "ANDROID" else "MACOS"),
                "claim_id" to JsonPrimitive(otherId), "claim_request_sha256" to JsonPrimitive("c".repeat(64)),
                "expires_at" to JsonPrimitive(now.plusSeconds(899).toString()))) {
                rejects { Codec.continuationContext(f.claim, bytes(change(claimed, name, value))) }
            }
            val claimPrepared = Codec.prepare(Codec.Operation.CLAIM, bytes(f.claimBody), f.recipient, now)
            val proof = requireNotNull(f.claim.signatureCopy())
            val order = (deviceKey.public as ECPublicKey).params.order
            val alternateS = order.subtract(BigInteger(1, proof.copyOfRange(32, 64))).toByteArray()
                .dropWhile { it == 0.toByte() }.toByteArray()
            val alternate = proof.copyOf().also { it.fill(0, 32, 64); alternateS.copyInto(it, 64 - alternateS.size) }
            val alternateWire = Codec.complete(claimPrepared, alternate)
            assertEquals(f.claim.requestSha256, alternateWire.requestSha256)
            assertArrayEquals(alternate, alternateWire.signatureCopy())
            assertFalse(f.claim.originalDocumentDigestCopy().contentEquals(alternateWire.originalDocumentDigestCopy()))
            rejects { Codec.complete(claimPrepared, ByteArray(64)) }
            rejects { Codec.complete(claimPrepared, proof + byteArrayOf(0)) }
            rejects { Codec.complete(claimPrepared, proof.copyOf().also { it[0] = (it[0].toInt() xor 1).toByte() }) }
            rejects { Codec.complete(claimPrepared, signed(deviceKey, message("autplay:self-device-pairing:claim:v1\n", claimPrepared.requestDigestCopy()))) }
            rejects { Codec.complete(claimPrepared, signed(deviceKey, bytes(f.claimBody))) }
            val fullClaim = obj(f.claim.canonicalDocumentCopy())
            rejects { Codec.readRequest(Codec.Operation.CLAIM, bytes(change(fullClaim, "device_signature_b64url", JsonPrimitive(url(proof) + "="))), f.recipient) }
            rejects { Codec.readRequest(Codec.Operation.CLAIM, bytes(change(fullClaim, "request_sha256", JsonPrimitive("e".repeat(64)))), f.recipient) }
            rejects { Codec.prepare(Codec.Operation.START, bytes(f.startBody), f.recipient, now) }
            rejects { Codec.prepare(Codec.Operation.CLAIM, bytes(f.claimBody), f.source, now) }
            rejects { Codec.prepare(Codec.Operation.CLAIM, bytes(change(f.claimBody, "device_public_key_spki_b64", JsonPrimitive(b64(differentKey.public.encoded)))), f.recipient, now) }
            rejects { Codec.prepare(Codec.Operation.CLAIM, bytes(change(f.claimBody, "device_key_thumbprint_sha256", JsonPrimitive("f".repeat(64)))), f.recipient, now) }
            if (source == Platform.ANDROID) codes[recipient] = Codec.comparisonCode(f.start, f.claim)
            val qr = Codec.readQr(f.qrBytes, trusted(), discovered, recipient, now)
            val encoded = obj(qr.encode())
            assertEquals(JsonPrimitive(2), encoded["version"])
            assertEquals(JsonPrimitive("v2"), encoded["contract_version"])
            assertEquals(JsonPrimitive(source.name), encoded["source_platform"])
            assertEquals("SelfV2.Qr(<redacted>)", qr.toString())
            assertFalse(f.start.toString().contains(secret.toString(Charsets.US_ASCII)))
            val qrRaw = qr.encode().toString(Charsets.UTF_8)
            rejects { Codec.readQr(("{\"version\":2," + qrRaw.drop(1)).toByteArray(Charsets.UTF_8), trusted(), discovered, recipient, now) }
            rejects { Codec.readQr(bytes(JsonObject(encoded - "expires_at")), trusted(), discovered, recipient, now) }
            rejects { Codec.readQr(bytes(change(encoded, "expires_at", JsonNull)), trusted(), discovered, recipient, now) }
            // Unsigned QR metadata must agree with the later authenticated stored ceremony.
            for ((name, value) in listOf("source_platform" to JsonPrimitive(if (source == Platform.MACOS) "ANDROID" else "MACOS"),
                "expires_at" to JsonPrimitive(now.plusSeconds(899).toString()))) {
                Codec.readQr(bytes(change(encoded, name, value)), trusted(), discovered, recipient, now).use { altered ->
                    val alteredScope = Codec.recipientContext(altered, recipient, deviceKey.public.encoded, now)
                    val prepared = Codec.prepare(Codec.Operation.CLAIM, bytes(f.claimBody), alteredScope, now)
                    val alteredClaim = Codec.complete(prepared, requireNotNull(f.claim.signatureCopy()))
                    rejects { Codec.continuationContext(alteredClaim, bytes(claimed)) }
                }
            }
            for ((name, value) in listOf("version" to JsonPrimitive(1), "schema_version" to JsonPrimitive(1),
                "contract_version" to JsonPrimitive("v1"), "source_platform" to JsonPrimitive("OTHER"),
                "expected_api_origin" to JsonPrimitive("https://other.example.invalid"),
                "expected_stream_origin" to JsonPrimitive("https://other.example.invalid"),
                "expected_identity_epoch" to JsonPrimitive(2), "expected_server_instance_id" to JsonPrimitive(otherId),
                "expected_identity_thumbprint_sha256" to JsonPrimitive("a".repeat(64)),
                "rendezvous_secret" to JsonPrimitive("A".repeat(42) + "B"),
                "expires_at" to JsonPrimitive(now.toString()), "expires_at" to JsonPrimitive(now.plusSeconds(1021).toString()))) {
                rejects { Codec.readQr(bytes(change(encoded, name, value)), trusted(), discovered, recipient, now) }
            }
            rejects { Codec.readQr(f.qrBytes, trusted(id = otherId), discovered, recipient, now) }
            rejects { Codec.readQr(f.qrBytes, trusted(), discovery(families = emptyList()), recipient, now) }
            rejects { Codec.readQr(f.qrBytes, trusted(), discovered, recipient, now.plusSeconds(600)) }
            rejects { Codec.readQr(ByteArray(4097) { 32 }, trusted(), discovered, recipient, now) }
            Codec.readQr(bytes(change(encoded, "expires_at", JsonPrimitive(now.plusSeconds(1020).toString()))),
                trusted(), discovered, recipient, now).use { assertEquals(now.plusSeconds(1020), it.expiresAt) }
            rejects { Codec.qrFromStart(f.start, bytes(f.openStatus), ByteArray(43) { 65 }, now) }
            rejects { Codec.qrFromStart(f.claim, bytes(f.openStatus), secret, now) }
            qr.close()
            f.qrBytes.fill(0)
        }
        assertNotEquals(codes[Platform.ANDROID], codes[Platform.MACOS])

        val f = pair(Platform.MACOS, Platform.ANDROID)
        val startBytes = bytes(f.startBody).toString(Charsets.UTF_8)
        for (raw in listOf(startBytes.replace("\"schema_version\":2", "\"schema_version\":2.0"),
            startBytes.replace("\"schema_version\":2", "\"schema_version\":2e0"),
            startBytes.replace("\"schema_version\":2", "\"schema_version\":true"),
            startBytes.replace("\"schema_version\":2", "\"schema_version\":\"2\""),
            startBytes.replace("\"schema_version\":2", "\"schema_version\":9007199254740992"),
            startBytes.replace("\"schema_version\":2", "\"schema_version\":-0"),
            "{\"schema_versi\\u006fn\":2," + startBytes.drop(1),
            startBytes + " {}")) {
            rejects { Codec.prepare(Codec.Operation.START, raw.toByteArray(Charsets.UTF_8), f.source, now) }
        }
        rejects { Codec.prepare(Codec.Operation.START, byteArrayOf(123, -1, 125), f.source, now) }
        rejects { Codec.prepare(Codec.Operation.START, ByteArray(8193) { 32 }, f.source, now) }
        for ((name, value) in listOf("request_sha256" to JsonPrimitive("a".repeat(64)),
            "device_signature_b64url" to JsonPrimitive("a".repeat(86)), "domain" to JsonPrimitive("attacker"),
            "operation" to JsonPrimitive("CLAIM"), "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
            "requested_at" to JsonPrimitive("2026-02-30T09:00:00Z"), "requested_at" to JsonPrimitive("2026-10-16T09:00:00+00:00"),
            "requested_at" to JsonPrimitive(now.minusSeconds(121).toString()), "requested_at" to JsonPrimitive(now.plusSeconds(121).toString()),
            "ceremony_id" to JsonPrimitive(otherId), "expected_server_instance_id" to JsonPrimitive(otherId),
            "expected_identity_epoch" to JsonPrimitive(2), "expected_identity_thumbprint_sha256" to JsonPrimitive("a".repeat(64)),
            "expected_api_origin" to JsonPrimitive("https://other.example.invalid"), "expected_stream_origin" to JsonPrimitive("https://other.example.invalid"),
            "operation_id" to JsonNull, "rendezvous_secret_sha256" to JsonObject(emptyMap()), "rendezvous_secret" to JsonPrimitive("A".repeat(43)))) {
            rejects { Codec.prepare(Codec.Operation.START, bytes(change(f.startBody, name, value)), f.source, now) }
        }
        val cancelScope = Codec.decisionContext(f.start, bytes(f.openStatus))
        val cancel = JsonObject(common(trusted(), Platform.MACOS) + mapOf("operation_id" to JsonPrimitive(operationId),
            "action" to JsonPrimitive("CANCEL"), "expected_revision" to JsonPrimitive(1),
            "claim_id" to JsonNull, "claim_request_sha256" to JsonNull, "comparison_code" to JsonNull))
        assertWire(Codec.prepare(Codec.Operation.DECISION, bytes(cancel), cancelScope, now),
            Codec.completeBearer(Codec.prepare(Codec.Operation.DECISION, bytes(cancel), cancelScope, now)), cancel)
        for (name in listOf("claim_id", "claim_request_sha256", "comparison_code")) {
            rejects { Codec.prepare(Codec.Operation.DECISION, bytes(JsonObject(cancel - name)), cancelScope, now) }
        }
        rejects { Codec.prepare(Codec.Operation.DECISION, bytes(change(cancel, "action", JsonPrimitive("REJECT"))), cancelScope, now) }
        rejects { Codec.prepare(Codec.Operation.DECISION, bytes(change(cancel, "action", JsonPrimitive("APPROVE"))), cancelScope, now) }
        for ((name, value) in listOf("device_name" to JsonPrimitive("x".repeat(121)), "app_version" to JsonPrimitive("x".repeat(33)),
            "device_name" to JsonPrimitive("bad\u007f"), "app_version" to JsonPrimitive("bad\n"), "claim_id" to JsonNull)) {
            rejects { Codec.prepare(Codec.Operation.CLAIM, bytes(change(f.claimBody, name, value)), f.recipient, now) }
        }
        val loneSurrogate = bytes(f.claimBody).toString(Charsets.UTF_8).replace("Phone \u21162", "\\ud800")
        rejects { Codec.prepare(Codec.Operation.CLAIM, loneSurrogate.toByteArray(Charsets.UTF_8), f.recipient, now) }
        rejects { Codec.sourceContext(trusted(thumb = "0".repeat(64)), Platform.MACOS, ceremony) }
        Codec.readQr(f.qrBytes, trusted(), discovered, Platform.ANDROID, now).use { qr ->
            rejects { Codec.recipientContext(qr, Platform.ANDROID, key("secp384r1").public.encoded, now) }
            rejects { Codec.recipientContext(qr, Platform.ANDROID, deviceKey.public.encoded, now.plusSeconds(900)) }
        }
        // SAS changes each frozen payload input; source platform itself is not a new SAS member.
        val baseCode = Codec.comparisonCode(f.start, f.claim)
        val variants = listOf(pair(Platform.MACOS, Platform.ANDROID, trust = trusted(id = otherId)),
            pair(Platform.MACOS, Platform.ANDROID, trust = trusted(epoch = 2)),
            pair(Platform.MACOS, Platform.ANDROID, id = otherId),
            pair(Platform.MACOS, Platform.ANDROID, name = "Different signed claim"),
            pair(Platform.MACOS, Platform.ANDROID, recipientKey = differentKey))
        for (variant in variants) {
            assertNotEquals(baseCode, Codec.comparisonCode(variant.start, variant.claim))
            variant.qrBytes.fill(0)
        }
        val sameClaimOtherSource = pair(Platform.ANDROID, Platform.ANDROID)
        assertEquals(f.claim.requestSha256, sameClaimOtherSource.claim.requestSha256)
        assertEquals(baseCode, Codec.comparisonCode(sameClaimOtherSource.start, sameClaimOtherSource.claim))
        rejects { Codec.comparisonCode(f.start, sameClaimOtherSource.claim) }
        assertNotEquals(baseCode, sasOracle(trusted(), ceremony, f.claim.requestSha256,
            text(f.claimBody["device_key_thumbprint_sha256"]), "autplay:self-device-pairing:sas:v1\n"))
        sameClaimOtherSource.qrBytes.fill(0)
        f.qrBytes.fill(0)
    } finally { secret.fill(0) }
}
