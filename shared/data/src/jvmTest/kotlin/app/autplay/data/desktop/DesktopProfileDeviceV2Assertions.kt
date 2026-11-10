package app.autplay.data.desktop

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as Codec
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2.Operation
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
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * FUTURE source assertions in the existing composite, not a new @Test entry point.
 * UNVERIFIED: no imports, key generation, canonicalization, hashing, crypto or tests executed
 * while authoring. Independent sorted fixture serialization, literal domain registry and real
 * P-256 MESSAGE signing/verification are the future oracle, not computed vector claims.
 * No credential response/header capture, receipt storage or real DB/settings/media is used.
 * Assertions use Boolean comparisons/static messages so a failure never prints credential JSON.
 * Android Keystore alias/provider and real Android/Mac interop remain separate UNVERIFIED gates.
 */
internal fun desktopProfileDeviceV2Assertions(): Unit {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val instance = "11111111-1111-4111-8111-111111111111"
    val request = "22222222-2222-4222-8222-222222222222"
    val account = "33333333-3333-4333-8333-333333333333"
    val exchange = "44444444-4444-4444-8444-444444444444"
    val binding = "55555555-5555-4555-8555-555555555555"
    val invitation = "66666666-6666-4666-8666-666666666666"
    val challenge = "77777777-7777-4777-8777-777777777777"
    val device = "88888888-8888-4888-8888-888888888888"
    val session = "99999999-9999-4999-8999-999999999999"
    val family = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    val other = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    fun key(): KeyPair = KeyPairGenerator.getInstance("EC").run {
        initialize(ECGenParameterSpec("secp256r1")); generateKeyPair()
    }
    fun sha(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
    fun hex(value: ByteArray): String = value.joinToString("") { "%02x".format(it.toInt() and 255) }
    fun url(value: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(value)
    fun b64(value: ByteArray): String = Base64.getEncoder().encodeToString(value)
    fun text(value: JsonElement?): String = (value as JsonPrimitive).content
    fun obj(value: ByteArray): JsonObject = Json.parseToJsonElement(value.toString(Charsets.UTF_8)) as JsonObject
    // For these fixtures: UTF-16 sorted object keys, strict integers and JSON string escaping.
    fun sorted(value: JsonElement): String = when (value) {
        is JsonObject -> value.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            JsonPrimitive(it.key).toString() + ":" + sorted(it.value)
        }
        else -> value.toString()
    }
    fun bytes(value: JsonElement): ByteArray = sorted(value).toByteArray(Charsets.UTF_8)
    fun changed(body: JsonObject, name: String, value: JsonElement): JsonObject = JsonObject(body + (name to value))
    fun rejects(block: () -> Unit) {
        val failure = runCatching(block).exceptionOrNull()
        assertTrue("expected sanitized profile v2 rejection", failure is Codec.Rejected)
        assertTrue(failure?.cause == null && failure?.suppressed?.isEmpty() == true)
        assertTrue(failure.toString().startsWith("ProfileDeviceV2.Rejected("))
    }
    fun der(proof: ByteArray): ByteArray {
        val r = BigInteger(1, proof.copyOfRange(0, 32)).toByteArray()
        val s = BigInteger(1, proof.copyOfRange(32, 64)).toByteArray()
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 2, r.size.toByte()) + r + byteArrayOf(2, s.size.toByte()) + s
    }
    fun p1363(encoded: ByteArray): ByteArray {
        var cursor = 0
        assertTrue(encoded[cursor++] == 0x30.toByte())
        assertTrue((encoded[cursor++].toInt() and 255) == encoded.size - 2)
        val output = ByteArray(64)
        for (offset in listOf(0, 32)) {
            assertTrue(encoded[cursor++] == 2.toByte())
            val count = encoded[cursor++].toInt() and 255
            val integer = BigInteger(encoded.copyOfRange(cursor, cursor + count)); cursor += count
            assertTrue(integer.signum() > 0)
            val significant = integer.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            assertTrue(significant.size <= 32)
            significant.copyInto(output, offset + 32 - significant.size)
        }
        assertTrue(cursor == encoded.size)
        return output
    }
    fun sign(pair: KeyPair, message: ByteArray): ByteArray = p1363(Signature.getInstance("SHA256withECDSA").run {
        initSign(pair.private); update(message); sign()
    })
    fun verify(pair: KeyPair, message: ByteArray, proof: ByteArray): Boolean = Signature.getInstance("SHA256withECDSA").run {
        initVerify(pair.public); update(message); verify(der(proof))
    }
    val domains = mapOf(
        Operation.ENROLLMENT_EXCHANGE to "autplay:device-compatibility:enrollment:exchange:v2\n",
        Operation.ADMISSION_SUBMIT to "autplay:device-compatibility:admission:submit:v2\n",
        Operation.ADMISSION_POLL to "autplay:device-compatibility:admission:poll:v2\n",
        Operation.ADMISSION_RECOVER to "autplay:device-compatibility:admission:recover:v2\n",
        Operation.ADMISSION_EXCHANGE to "autplay:device-compatibility:admission:exchange:v2\n",
        Operation.TRUSTED_CHALLENGE to "autplay:device-compatibility:trusted:challenge:v2\n",
        Operation.TRUSTED_EXCHANGE to "autplay:device-compatibility:trusted:exchange:v2\n",
        Operation.SESSION_ROTATE to "autplay:device-compatibility:session:rotate:v2\n")
    val ownHash = setOf(Operation.ENROLLMENT_EXCHANGE, Operation.SESSION_ROTATE)
    val identityKey = key(); val deviceKey = key(); val wrongAliasKey = key(); val otherIdentityKey = key()
    val spki = deviceKey.public.encoded
    val thumb = hex(sha(spki))
    val secret32 = url(ByteArray(32) { (it + 1).toByte() })
    val secret16 = url(ByteArray(16) { (it + 2).toByte() })
    val nonce32 = url(ByteArray(32) { (it + 3).toByte() })
    val nonce16 = url(ByteArray(16) { (it + 4).toByte() })
    val secretHash = hex(sha(secret32.toByteArray(Charsets.US_ASCII)))
    val nextHash = hex(sha("synthetic-chosen-next-refresh".toByteArray(Charsets.US_ASCII)))
    val pollHash = hex(sha("synthetic-poll-bearer".toByteArray(Charsets.US_ASCII)))
    fun trusted(id: String = instance, epoch: Long = 1, pair: KeyPair = identityKey,
        apiOrigin: String = api, streamOrigin: String = stream): TrustedM5Identity =
        TrustedM5Identity(id, epoch, pair.public.encoded, hex(sha(pair.public.encoded)), apiOrigin, streamOrigin)
    fun common(platform: Platform): Map<String, JsonElement> = mapOf(
        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2), "platform" to JsonPrimitive(platform.name),
        "expected_server_instance_id" to JsonPrimitive(instance), "expected_identity_epoch" to JsonPrimitive(1),
        "expected_identity_thumbprint_sha256" to JsonPrimitive(hex(sha(identityKey.public.encoded))),
        "expected_api_origin" to JsonPrimitive(api), "expected_stream_origin" to JsonPrimitive(stream))
    fun jwk(pair: KeyPair = deviceKey): JsonObject {
        val ec = pair.public as ECPublicKey
        fun coordinate(number: BigInteger): String {
            val significant = number.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            return url(ByteArray(32 - significant.size) + significant)
        }
        return JsonObject(mapOf("kty" to JsonPrimitive("EC"), "crv" to JsonPrimitive("P-256"),
            "x" to JsonPrimitive(coordinate(ec.w.affineX)), "y" to JsonPrimitive(coordinate(ec.w.affineY))))
    }
    fun keyFields(): Map<String, JsonElement> = mapOf("device_key_thumbprint_sha256" to JsonPrimitive(thumb), "device_public_key_jwk" to jwk())
    fun prepare(operation: Operation, body: JsonObject, context: Codec.Context): Codec.PreparedRequest {
        val input = bytes(body)
        try { return Codec.rawBody(input).use { Codec.prepare(operation, it, context, now) } }
        finally { input.fill(0) }
    }
    fun invalid(operation: Operation, context: Codec.Context, input: ByteArray) {
        try { rejects { Codec.rawBody(input).use { Codec.prepare(operation, it, context, now).close() } } }
        finally { input.fill(0) }
    }
    fun exercise(operation: Operation, body: JsonObject, context: Codec.Context): Codec.SignedCommand {
        val input = bytes(body)
        val expected = input.copyOf()
        val digest = sha(expected)
        val message = domains.getValue(operation).toByteArray(Charsets.US_ASCII) + digest
        val raw = Codec.rawBody(input)
        input.fill(0)
        assertTrue(raw.transientUtf8Copy().contentEquals(expected))
        val prepared = Codec.prepare(operation, raw, context, now)
        raw.close(); rejects { raw.transientUtf8Copy() }
        assertTrue(prepared.operation == operation && prepared.platform == context.platform)
        assertTrue(prepared.proofDomain == domains.getValue(operation))
        assertTrue(prepared.transientUnsignedCopy().contentEquals(expected))
        assertTrue(prepared.requestDigestCopy().contentEquals(digest))
        assertTrue(prepared.signingMessageCopy().contentEquals(message))
        assertTrue(prepared.publicKeySpkiCopy().contentEquals(spki) && prepared.keyThumbprintSha256 == thumb)
        prepared.transientUnsignedCopy().fill(0); prepared.requestDigestCopy().fill(0)
        prepared.signingMessageCopy().fill(0); prepared.publicKeySpkiCopy().fill(0)
        assertTrue(prepared.transientUnsignedCopy().contentEquals(expected))
        assertTrue(!prepared.toString().contains(secret32) && !context.toString().contains(account))
        var produced: ByteArray? = null
        val signed = try {
            Codec.sign(prepared) { selected, rawDigest ->
                assertTrue(selected == operation && rawDigest.contentEquals(digest))
                sign(deviceKey, domains.getValue(selected).toByteArray(Charsets.US_ASCII) + rawDigest)
                    .also { produced = it; rawDigest.fill(0) }
            }
        } finally { produced?.fill(0) }
        var secondSignerCalls = 0
        rejects { Codec.sign(prepared) { _, _ -> secondSignerCalls++; sign(deviceKey, message) } }
        assertTrue(secondSignerCalls == 0)
        prepared.close(); rejects { prepared.transientUnsignedCopy() }; rejects { prepared.signingMessageCopy() }
        val wire = signed.transientWireCopy()
        try {
            val document = obj(wire)
            val signatureField = if (operation in ownHash) "device_signature_b64url" else "proof_b64url"
            val omitted = setOf(signatureField) + if (operation in ownHash) setOf("request_sha256") else emptySet()
            assertTrue(JsonObject(document.filterKeys { it !in omitted }) == body)
            assertTrue(wire.contentEquals(bytes(document)))
            if (operation in ownHash) assertTrue(text(document["request_sha256"]) == hex(digest))
            else if (operation != Operation.ADMISSION_RECOVER && operation != Operation.ADMISSION_EXCHANGE)
                assertTrue("request_sha256" !in document)
            val signature = Base64.getUrlDecoder().decode(text(document[signatureField]))
            assertTrue(signature.size == 64 && url(signature) == text(document[signatureField]))
            assertTrue(verify(deviceKey, message, signature))
            assertTrue(signed.signatureCopy().contentEquals(signature) && signed.requestDigestCopy().contentEquals(digest))
            signed.signatureCopy().fill(0); signed.requestDigestCopy().fill(0); signed.transientWireCopy().fill(0)
            assertTrue(signed.transientWireCopy().contentEquals(wire))
            Codec.rawBody(wire).use { Codec.requireExactOriginal(signed, it, context) }
            Codec.rawBody(wire + byteArrayOf(32)).use { rejects { Codec.requireExactOriginal(signed, it, context) } }
            val tampered = changed(document, signatureField, JsonPrimitive(url(ByteArray(64))))
            Codec.rawBody(bytes(tampered)).use { rejects { Codec.requireExactOriginal(signed, it, context) } }
            // A DIFFERENT valid signature still cannot replace the one captured original.
            val order = (deviceKey.public as ECPublicKey).params.order
            val oppositeS = order.subtract(BigInteger(1, signature.copyOfRange(32, 64))).toByteArray()
                .dropWhile { it == 0.toByte() }.toByteArray()
            val differentValid = signature.copyOf().also { output ->
                output.fill(0, 32, 64); oppositeS.copyInto(output, 64 - oppositeS.size)
            }
            assertTrue(verify(deviceKey, message, differentValid) && !differentValid.contentEquals(signature))
            Codec.rawBody(bytes(changed(document, signatureField, JsonPrimitive(url(differentValid))))).use {
                rejects { Codec.requireExactOriginal(signed, it, context) }
            }
            assertTrue(!signed.toString().contains(secret32) && !raw.toString().contains(secret16))
        } finally { wire.fill(0); expected.fill(0); digest.fill(0); message.fill(0) }
        return signed
    }

    assertTrue(Operation.entries.toSet() == domains.keys && domains.size == 8)
    // No START/DECISION/source bearer/ROOT operation or arbitrary domain/role entry exists here.
    assertTrue(Operation.entries.none { it.name in setOf("START", "DECISION", "REGISTER", "LOGIN") })
    fun originSubmit(platform: Platform, field: String, origin: String): JsonObject = changed(
        JsonObject(common(platform) + keyFields() + mapOf("request_id" to JsonPrimitive(request),
            "client_nonce_b64url" to JsonPrimitive(nonce32), "api_major" to JsonPrimitive(1),
            "nickname" to JsonPrimitive("Origin fixture"), "app_version" to JsonPrimitive("fixture-2"),
            "requested_at" to JsonPrimitive(now.toString()))), field, JsonPrimitive(origin))
    val longestName = List(3) { "a".repeat(63) }.joinToString(".") + "." + "b".repeat(61)
    for (platform in Platform.entries) for (field in listOf("expected_api_origin", "expected_stream_origin")) {
        for (origin in listOf("https://node.123", "https://node.1a", "https://node.123.", "https://$longestName",
            "https://192.0.2.1:65535", "https://[2001:db8::1]:1", "https://[::ffff:c000:201]")) {
            val trust = trusted(apiOrigin = if (field == "expected_api_origin") origin else api,
                streamOrigin = if (field == "expected_stream_origin") origin else stream)
            val context = Codec.admissionSubmitContext(trust, platform, spki, request)
            val body = originSubmit(platform, field, origin)
            exercise(Operation.ADMISSION_SUBMIT, body, context).use { command ->
                val wire = command.transientWireCopy()
                try { assertTrue(obj(wire)[field] == JsonPrimitive(origin)) } finally { wire.fill(0) }
            }
            invalid(Operation.ADMISSION_SUBMIT, Codec.admissionSubmitContext(trusted(), platform, spki, request), bytes(body))
        }
        // PROFILE compares canonical spelling; it must not adopt SELF/B1's raw spelling policy.
        for (origin in listOf("https://NODE.123", "https://node.123:00443", "https://node.123:0",
            "https://node.123:65536", "https://node.123:", "https://-node.123", "https://node-.123",
            "https://node..123", "https://${"a".repeat(64)}.123", "https://$longestName.",
            "https://192.000.2.1", "https://123.456", "https://[2001:0db8::1]", "https://[2001:DB8::1]",
            "https://[::ffff:192.0.2.1]", "https://[1::2::3]", "https://[fe80::1%25eth0]")) {
            rejects { Codec.admissionSubmitContext(trusted(apiOrigin = if (field == "expected_api_origin") origin else api,
                streamOrigin = if (field == "expected_stream_origin") origin else stream), platform, spki, request) }
            invalid(Operation.ADMISSION_SUBMIT, Codec.admissionSubmitContext(trusted(), platform, spki, request),
                bytes(originSubmit(platform, field, origin)))
        }
    }
    for (platform in Platform.entries) {
        val trust = trusted()
        val base = common(platform)
        val submitContext = Codec.admissionSubmitContext(trust, platform, spki, request)
        val submitBody = JsonObject(base + keyFields() + mapOf("request_id" to JsonPrimitive(request),
            "client_nonce_b64url" to JsonPrimitive(nonce32), "api_major" to JsonPrimitive(1),
            "nickname" to JsonPrimitive("Fixture \u2603"), "app_version" to JsonPrimitive("fixture-2"),
            "requested_at" to JsonPrimitive("2026-10-16T09:00:00.123456789Z"), "device_model_hint" to JsonPrimitive("synthetic")))
        val callerKey = spki.copyOf()
        val copiedKeyContext = Codec.admissionSubmitContext(trust, platform, callerKey, request)
        callerKey.fill(0)
        prepare(Operation.ADMISSION_SUBMIT, submitBody, copiedKeyContext).close()
        val submit = exercise(Operation.ADMISSION_SUBMIT, submitBody, submitContext)
        val originalSubmitDigest = hex(sha(bytes(submitBody)))
        val pollContext = Codec.admissionPollContext(submit, trust, platform, spki)
        val pollBody = JsonObject(base + mapOf("request_id" to JsonPrimitive(request), "device_key_thumbprint_sha256" to JsonPrimitive(thumb),
            "client_nonce_b64url" to JsonPrimitive(nonce32)))
        exercise(Operation.ADMISSION_POLL, pollBody, pollContext).use { command ->
            rejects { Codec.admissionRecoverContext(command, trust, platform, spki) }
        }
        val recoverContext = Codec.admissionRecoverContext(submit, trust, platform, spki)
        val recoverBody = JsonObject(base + mapOf("request_id" to JsonPrimitive(request), "request_sha256" to JsonPrimitive(originalSubmitDigest),
            "device_key_thumbprint_sha256" to JsonPrimitive(thumb), "recovery_nonce_b64url" to JsonPrimitive(nonce32)))
        exercise(Operation.ADMISSION_RECOVER, recoverBody, recoverContext).close()
        val admissionContext = Codec.admissionExchangeContext(submit, trust, platform, spki, account, exchange, binding, pollHash, nextHash)
        val admissionBody = JsonObject(base + keyFields() + mapOf("request_id" to JsonPrimitive(request),
            "request_sha256" to JsonPrimitive(originalSubmitDigest), "exchange_id" to JsonPrimitive(exchange), "binding_commit_id" to JsonPrimitive(binding),
            "poll_bearer_sha256" to JsonPrimitive(pollHash), "approved_account_id" to JsonPrimitive(account),
            "client_nonce_b64url" to JsonPrimitive(nonce32), "next_refresh_token_sha256" to JsonPrimitive(nextHash)))
        exercise(Operation.ADMISSION_EXCHANGE, admissionBody, admissionContext).close()
        for ((operation, body, context) in listOf(Triple(Operation.ADMISSION_RECOVER, recoverBody, recoverContext),
            Triple(Operation.ADMISSION_EXCHANGE, admissionBody, admissionContext))) {
            val included = sha(bytes(body))
            val wronglyOmitted = sha(bytes(JsonObject(body - "request_sha256")))
            assertTrue(!included.contentEquals(wronglyOmitted))
            invalid(operation, context, bytes(JsonObject(body - "request_sha256")))
            invalid(operation, context, bytes(changed(body, "request_sha256", JsonPrimitive("0".repeat(64)))))
            prepare(operation, body, context).use { prepared ->
                rejects { Codec.complete(prepared, sign(deviceKey, domains.getValue(operation).toByteArray(Charsets.US_ASCII) + wronglyOmitted)) }
            }
        }

        val enrollmentContext = Codec.enrollmentContext(trust, platform, spki, invitation, account, exchange, binding, secretHash, nextHash)
        val enrollmentBody = JsonObject(base + mapOf("device_public_key_spki_b64" to JsonPrimitive(b64(spki)),
            "device_key_thumbprint_sha256" to JsonPrimitive(thumb), "device_name" to JsonPrimitive("Fixture device"),
            "app_version" to JsonPrimitive("fixture-2"), "exchange_id" to JsonPrimitive(exchange), "binding_commit_id" to JsonPrimitive(binding),
            "invitation_id" to JsonPrimitive(invitation), "invitation_secret" to JsonPrimitive(secret32), "expected_user_id" to JsonPrimitive(account),
            "next_refresh_token_sha256" to JsonPrimitive(nextHash), "client_nonce_b64url" to JsonPrimitive(nonce16),
            "signature_algorithm" to JsonPrimitive("ES256-P1363")))
        exercise(Operation.ENROLLMENT_EXCHANGE, enrollmentBody, enrollmentContext).close()
        val wrongSecretHash = hex(sha(Base64.getUrlDecoder().decode(secret32)))
        val decodedInsteadOfEncoded = Codec.enrollmentContext(trust, platform, spki, invitation, account, exchange,
            binding, wrongSecretHash, nextHash)
        invalid(Operation.ENROLLMENT_EXCHANGE, decodedInsteadOfEncoded, bytes(enrollmentBody))
        val challengeContext = Codec.trustedChallengeContext(trust, platform, spki, account, request)
        val challengeBody = JsonObject(base + keyFields() + mapOf("challenge_request_id" to JsonPrimitive(request),
            "account_id" to JsonPrimitive(account), "client_nonce_b64url" to JsonPrimitive(nonce32)))
        val challengeCommand = exercise(Operation.TRUSTED_CHALLENGE, challengeBody, challengeContext)
        val incomingCredential = secret16.toByteArray(Charsets.US_ASCII)
        val trustedContext = Codec.trustedExchangeContext(challengeCommand, trust, platform, spki, challenge,
            incomingCredential, exchange, binding, nextHash, now.plusSeconds(300), now)
        incomingCredential.fill(0)
        val trustedBody = JsonObject(base + keyFields() + mapOf("challenge_id" to JsonPrimitive(challenge), "challenge" to JsonPrimitive(secret16),
            "exchange_id" to JsonPrimitive(exchange), "binding_commit_id" to JsonPrimitive(binding), "account_id" to JsonPrimitive(account),
            "next_refresh_token_sha256" to JsonPrimitive(nextHash), "client_nonce_b64url" to JsonPrimitive(nonce32)))
        exercise(Operation.TRUSTED_EXCHANGE, trustedBody, trustedContext).close()
        invalid(Operation.TRUSTED_EXCHANGE, trustedContext, bytes(changed(trustedBody, "challenge", JsonPrimitive(nonce16))))
        invalid(Operation.TRUSTED_EXCHANGE, trustedContext, bytes(changed(trustedBody, "account_id", JsonPrimitive(other))))
        rejects { Codec.trustedExchangeContext(submit, trust, platform, spki, challenge, secret16.toByteArray(), exchange, binding,
            nextHash, now.plusSeconds(300), now) }
        rejects { Codec.trustedExchangeContext(challengeCommand, trust, platform, spki, challenge, secret16.toByteArray(), exchange, binding,
            nextHash, now.plusSeconds(301), now) }
        Codec.rawBody(bytes(trustedBody)).use { raw ->
            rejects { Codec.prepare(Operation.TRUSTED_EXCHANGE, raw, trustedContext, now.plusSeconds(300)) }
        }

        fun rotationContext(accountId: String = account, familyId: String = family, commitId: String = binding,
            parentId: String = session, generation: Long = 0, expiry: Instant = now.plusSeconds(600)): Codec.Context =
            Codec.rotationContext(trust, platform, spki, accountId, device, parentId, familyId, commitId, generation,
                exchange, secretHash, nextHash, expiry)
        val rotateContext = rotationContext()
        val rotateBody = JsonObject(base + mapOf("rotation_id" to JsonPrimitive(exchange), "device_id" to JsonPrimitive(device),
            "parent_session_id" to JsonPrimitive(session), "current_generation" to JsonPrimitive(0), "current_refresh_token" to JsonPrimitive(secret32),
            "next_refresh_token_sha256" to JsonPrimitive(nextHash), "signature_algorithm" to JsonPrimitive("ES256-P1363")))
        val rotated = exercise(Operation.SESSION_ROTATE, rotateBody, rotateContext)
        for (wrong in listOf(rotationContext(accountId = other), rotationContext(familyId = other), rotationContext(commitId = other),
            rotationContext(parentId = other), rotationContext(generation = 1), rotationContext(expiry = now.plusSeconds(601)))) {
            Codec.rawBody(rotated.transientWireCopy()).use { rejects { Codec.requireExactOriginal(rotated, it, wrong) } }
        }
        invalid(Operation.SESSION_ROTATE, rotateContext, bytes(changed(rotateBody, "current_refresh_token", JsonPrimitive(nonce32))))
        invalid(Operation.SESSION_ROTATE, rotateContext, bytes(changed(rotateBody, "parent_session_id", JsonPrimitive(other))))
        invalid(Operation.ENROLLMENT_EXCHANGE, enrollmentContext, bytes(changed(enrollmentBody, "invitation_secret", JsonPrimitive(nonce32))))
        for ((operation, body, context) in listOf(Triple(Operation.ENROLLMENT_EXCHANGE, enrollmentBody, enrollmentContext),
            Triple(Operation.SESSION_ROTATE, rotateBody, rotateContext))) {
            invalid(operation, context, bytes(changed(body, "request_sha256", JsonPrimitive("0".repeat(64)))))
            invalid(operation, context, bytes(changed(body, "device_signature_b64url", JsonPrimitive(url(ByteArray(64))))))
        }

        // Every trusted M5 tuple member, recipient platform and parent key must match independently.
        for (wrongTrust in listOf(trusted(id = other), trusted(epoch = 2), trusted(pair = otherIdentityKey),
            trusted(apiOrigin = "https://other-api.example.invalid"), trusted(streamOrigin = "https://other-stream.example.invalid"))) {
            val wrongContext = Codec.admissionSubmitContext(wrongTrust, platform, spki, request)
            invalid(Operation.ADMISSION_SUBMIT, wrongContext, bytes(submitBody))
            rejects { Codec.admissionPollContext(submit, wrongTrust, platform, spki) }
        }
        val opposite = Platform.entries.first { it != platform }
        rejects { Codec.admissionPollContext(submit, trust, opposite, spki) }
        rejects { Codec.admissionRecoverContext(submit, trust, platform, wrongAliasKey.public.encoded) }
        rejects { Codec.admissionPollContext(challengeCommand, trust, platform, spki) }
        invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, "platform", JsonPrimitive(opposite.name))))
        invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, "request_id", JsonPrimitive(other))))
        rejects { prepare(Operation.ADMISSION_POLL, submitBody, submitContext) }
        val otherSubmit = exercise(Operation.ADMISSION_SUBMIT,
            changed(submitBody, "client_nonce_b64url", JsonPrimitive(secret32)), submitContext)
        val otherParent = Codec.admissionRecoverContext(otherSubmit, trust, platform, spki)
        invalid(Operation.ADMISSION_RECOVER, otherParent, bytes(recoverBody))
        otherSubmit.close()

        // Raw lexical/schema boundaries, including nested duplicate/unescaped key aliases.
        val json = sorted(submitBody)
        val malformed = listOf(
            json.replace("\"schema_version\":2", "\"schema_version\":2.0"),
            json.replace("\"schema_version\":2", "\"schema_version\":2e0"),
            json.replace("\"schema_version\":2", "\"schema_version\":true"),
            json.replace("\"schema_version\":2", "\"schema_version\":\"2\""),
            json.replace("\"schema_version\":2", "\"schema_version\":null"),
            json.replace("\"schema_version\":2", "\"schema_version\":9007199254740992"),
            json.dropLast(1) + ",\"schema_version\":2}",
            json.replace("\"kty\":\"EC\"", "\"kty\":\"EC\",\"kty\":\"EC\""),
            json.replace("\"kty\":\"EC\"", "\"kty\":\"EC\",\"\\u006bty\":\"EC\""),
            sorted(changed(submitBody, "nickname", JsonPrimitive("placeholder"))).replace("placeholder", "\\uD800"),
            json + "{}")
        malformed.forEach { invalid(Operation.ADMISSION_SUBMIT, submitContext, it.toByteArray(Charsets.UTF_8)) }
        invalid(Operation.ADMISSION_SUBMIT, submitContext, byteArrayOf(0x7b, 0x22, 0xc0.toByte(), 0xaf.toByte(), 0x22, 0x7d))
        for ((name, value) in listOf("contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
            "platform" to JsonPrimitive("OTHER"), "operation" to JsonPrimitive("self.start"),
            "nickname" to JsonPrimitive("bad\nname"), "app_version" to JsonPrimitive("x".repeat(33)),
            "nickname" to JsonPrimitive("x".repeat(121)), "device_model_hint" to JsonPrimitive("x".repeat(97)),
            "requested_at" to JsonPrimitive("2026-02-30T00:00:00Z"), "requested_at" to JsonPrimitive("2026-10-16T09:00:00+00:00"),
            "client_nonce_b64url" to JsonPrimitive(nonce32 + "="), "device_public_key_jwk" to JsonNull,
            "device_key_thumbprint_sha256" to JsonPrimitive(thumb.uppercase()))) {
            invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, name, value)))
        }
        invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(JsonObject(submitBody - "app_version")))
        prepare(Operation.ADMISSION_SUBMIT, JsonObject(submitBody - "device_model_hint"), submitContext).close()
        invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, "device_model_hint", JsonNull)))
        val point = jwk()
        for (badJwk in listOf(JsonObject(point + ("extra" to JsonPrimitive("x"))),
            changed(point, "crv", JsonPrimitive("P-384")), changed(point, "kty", JsonPrimitive("RSA")),
            changed(point, "x", JsonPrimitive(url(ByteArray(32)))),
            JsonObject(point + mapOf("x" to JsonPrimitive(url(ByteArray(32))), "y" to JsonPrimitive(url(ByteArray(32))))),
            changed(point, "x", JsonPrimitive(text(point["x"]) + "=")), jwk(wrongAliasKey))) {
            invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, "device_public_key_jwk", badJwk)))
        }
        invalid(Operation.ENROLLMENT_EXCHANGE, enrollmentContext,
            bytes(changed(enrollmentBody, "device_public_key_spki_b64", JsonPrimitive(b64(spki).trimEnd('=')))))
        val offCurve = spki.copyOf().also { it.fill(0, it.size - 64, it.size) }
        rejects { Codec.admissionSubmitContext(trust, platform, offCurve, request) }
        rejects { Codec.admissionSubmitContext(trust, platform, spki + byteArrayOf(0), request) }
        val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        // Change only an unused base64 tail bit, preserving decoded bytes; roundtrip must reject.
        val noncanonicalTail = nonce32.dropLast(1) + alphabet[alphabet.indexOf(nonce32.last()) + 1]
        invalid(Operation.ADMISSION_SUBMIT, submitContext, bytes(changed(submitBody, "client_nonce_b64url", JsonPrimitive(noncanonicalTail))))
        invalid(Operation.ADMISSION_SUBMIT, submitContext, ByteArray(Codec.MAX_BODY_BYTES + 1) { 32 })
        val nearCap = (" ".repeat(Codec.MAX_BODY_BYTES - json.toByteArray(Charsets.UTF_8).size) + json).toByteArray(Charsets.UTF_8)
        Codec.rawBody(nearCap).use { Codec.prepare(Operation.ADMISSION_SUBMIT, it, submitContext, now).close() }
        nearCap.fill(0)

        // Wrong alias signature, v1/cross-operation/literal-backslash-LF, digest and hex/prehash substitutes.
        val digest = sha(bytes(submitBody)); val message = domains.getValue(Operation.ADMISSION_SUBMIT).toByteArray(Charsets.US_ASCII) + digest
        val signatures = listOf(ByteArray(64), ByteArray(63), sign(wrongAliasKey, message),
            sign(deviceKey, "AutPlay enrollment exchange v1\n".toByteArray(Charsets.US_ASCII) + digest),
            sign(deviceKey, domains.getValue(Operation.ADMISSION_POLL).toByteArray(Charsets.US_ASCII) + digest),
            sign(deviceKey, domains.getValue(Operation.ADMISSION_SUBMIT).replace("\n", "\\n").toByteArray(Charsets.US_ASCII) + digest),
            sign(deviceKey, digest), sign(deviceKey, domains.getValue(Operation.ADMISSION_SUBMIT).toByteArray(Charsets.US_ASCII) + hex(digest).toByteArray()),
            der(sign(deviceKey, message)))
        signatures.forEach { proof -> prepare(Operation.ADMISSION_SUBMIT, submitBody, submitContext).use { rejects { Codec.complete(it, proof) } } }
        val valid = sign(deviceKey, message)
        val order = (deviceKey.public as ECPublicKey).params.order
        val highS = order.subtract(BigInteger(1, valid.copyOfRange(32, 64))).toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
        val alternative = valid.copyOf().also { output -> output.fill(0, 32, 64); highS.copyInto(output, 64 - highS.size) }
        prepare(Operation.ADMISSION_SUBMIT, submitBody, submitContext).use { Codec.complete(it, valid).close() }
        prepare(Operation.ADMISSION_SUBMIT, submitBody, submitContext).use { Codec.complete(it, alternative).close() }
        val failed = prepare(Operation.ADMISSION_SUBMIT, submitBody, submitContext)
        rejects { Codec.sign(failed) { _, _ -> throw IllegalStateException("synthetic signer unavailable") } }
        rejects { Codec.complete(failed, valid) }; failed.close()
        rotated.close(); rejects { rotated.transientWireCopy() }; rejects { rotated.signatureCopy() }; rejects { rotated.requestDigestCopy() }
        submit.close(); rejects { Codec.admissionPollContext(submit, trust, platform, spki) }
        challengeCommand.close()
    }
}
