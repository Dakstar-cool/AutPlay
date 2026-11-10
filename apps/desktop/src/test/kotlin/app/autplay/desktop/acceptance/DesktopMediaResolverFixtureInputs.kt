package app.autplay.desktop.acceptance

import app.autplay.application.desktop.media.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.syncbinding.SyncBindingContractV2 as B1
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.ByteOrder
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
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.*

/**
 * FUTURE / UNCALLED / UNVERIFIED. No initializer performs crypto, IO or a test on file loading.
 * Fixed observation time, identities, content and relationships make scenarios replayable; each
 * invocation generates ephemeral synthetic keys/proofs, so envelopes/fingerprints are NOT goldens.
 * D1/B1 use their public verification APIs. The explicit in-memory compare/readback below is a
 * TEST owner seam, never K custody. M5 has no shared public verifier: this restricted fixture
 * owner verifies the actual profile-v1 recipe before supplying M5ProfileCapture. Neither that
 * DTO nor the synthetic trust/policy confirmation below is production cryptographic authority.
 * No real token, file, database, network, secret store or committed fingerprint is involved.
 */
internal fun futureDesktopMediaResolverHostInputs(): DesktopMediaResolverHostInputs {
    val now = Instant.parse("2026-10-16T09:00:00Z")
    val expires = now.plusSeconds(3600)
    val instance = fixtureMediaId(1)
    val user = fixtureMediaId(2)
    val device = fixtureMediaId(3)
    val binding = fixtureMediaId(4)
    val ownerRef = fixtureMediaId(5)
    val store = fixtureMediaId(6)
    val scope = fixtureMediaId(7)
    val utr = fixtureMediaId(8)
    val r1 = fixtureMediaId(9)
    val r2 = fixtureMediaId(10)
    val v1 = fixtureMediaId(11)
    val v2 = fixtureMediaId(12)
    val api = "https://api.example.invalid"
    val stream = "https://stream.example.invalid"
    val identityKey = fixtureMediaKey()
    val deviceKey = fixtureMediaKey()
    val spki = identityKey.public.encoded
    val thumb = fixtureMediaHex(fixtureMediaSha(spki))
    val trusted = D1.TrustedM5Identity(instance.value, 1, spki, thumb, api, stream)
    val m5Revision = M5CapabilityRevision(3)
    val sharedRevision = 90L // D1/B1 per-instance/epoch/user/device; NOT M5 singleton scope.

    val identityFields = mapOf(
        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
        "server_instance_id" to JsonPrimitive(instance.value), "identity_epoch" to JsonPrimitive(1),
        "identity_public_key_spki_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(spki)),
        "identity_thumbprint_sha256" to JsonPrimitive(thumb),
        "api_origin" to JsonPrimitive(api), "stream_origin" to JsonPrimitive(stream),
        "api_major" to JsonPrimitive(1), "user_id" to JsonPrimitive(user.value),
        "device_id" to JsonPrimitive(device.value), "platform" to JsonPrimitive("MACOS"),
        "capability_revision" to JsonPrimitive(sharedRevision),
        "issued_at" to JsonPrimitive(now.toString()), "expires_at" to JsonPrimitive(expires.toString()),
    )
    val d1Payload = JsonObject(identityFields + ("families" to fixtureMediaStrings("self", "session")))
    val d1Transition = fixtureD1Verified(D1.capabilities(fixtureMediaEnvelope(identityKey, d1Payload,
        "autplay:device-compatibility:negotiation:capabilities:v2\n", "v2", 2), trusted,
        D1.CapabilityScope(user.value, device.value, D1.Platform.MACOS), null, now))
    // Explicit first observation in this newly created synthetic owner; no lost-custody recovery.
    var d1Readback: D1.CapabilityObservation? = null
    assertSame(d1Readback, d1Transition.expectedPrevious)
    d1Readback = d1Transition.nextObservation
    assertSame(d1Transition.nextObservation, d1Readback)
    val d1 = checkNotNull(d1Readback)
    val b1Owner = B1.OwnerCapture(trusted, user.value, device.value, D1.Platform.MACOS, 19)
    var b1Readback = fixtureB1Verified(B1.revisionGuard(b1Owner, 0, null, d1))
    val b1Payload = JsonObject(identityFields + mapOf(
        "binding_contract_version" to JsonPrimitive(2), "protocol_version" to JsonPrimitive(1),
        "binding_available" to JsonPrimitive(true), "platforms" to fixtureMediaStrings("ANDROID", "MACOS"),
    ))
    val b1Transition = fixtureB1Verified(B1.capabilities(fixtureMediaEnvelope(identityKey, b1Payload,
        "autplay:sync-binding:negotiation:capabilities:v2\n", "v2", 2), b1Owner, b1Readback, now))
    assertSame(b1Readback, b1Transition.expectedPrevious)
    b1Readback = b1Transition.next // Test-only compare/publish followed by the REAL public accept API.
    val b1Accepted = fixtureB1Verified(B1.acceptCapabilities(b1Transition, b1Readback, b1Owner))
    assertSame(b1Transition.snapshot, b1Accepted.snapshot)
    assertTrue(b1Accepted.snapshot.bindingAvailable)
    assertEquals(d1.revision, b1Readback.globalHighWater)
    assertFalse(d1.effectivePayloadSha256.contentEquals(checkNotNull(b1Readback.previousB1).effectivePayloadSha256))

    // Exact USER base operations from _capability_operations, trusted=false, feature flags=false.
    // No invented media/Vault/snapshot operation and no D1/B1 fields in this old v1 payload.
    val operations = linkedSetOf("getCapabilities", "listDevices", "listSessions", "rotateDeviceSession",
        "logoutCurrentSession", "logoutAllSessions", "revokeDevice")
    val m5Payload = JsonObject(mapOf(
        "server_instance_id" to JsonPrimitive(instance.value), "identity_epoch" to JsonPrimitive(1),
        "user_id" to JsonPrimitive(user.value), "device_id" to JsonPrimitive(device.value),
        "product_version" to JsonPrimitive("0.0.0"), "api_major" to JsonPrimitive(1),
        "capability_revision" to JsonPrimitive(m5Revision.value),
        "operations" to fixtureMediaStrings(*operations.toTypedArray()),
        "limits" to JsonObject(mapOf("device_list_max" to JsonPrimitive(100),
            "session_list_max" to JsonPrimitive(200), "invitation_active_max" to JsonPrimitive(5),
            "invitation_ttl_max_seconds" to JsonPrimitive(1800))),
        "issued_at" to JsonPrimitive(now.toString()), "expires_at" to JsonPrimitive(expires.toString()),
    ))
    val m5Envelope = fixtureMediaEnvelope(identityKey, m5Payload, "AutPlay capabilities v1\n", "v1", 1)
    val m5 = fixtureAcceptM5(m5Envelope, m5Payload, trusted, now, m5Revision, operations)
    val local = AccountLocalCapture(store, scope, 11, 17,
        LocalOwnerKey.BoundLocal(instance, user, binding), ownerRef, 23, fixtureMediaId(13), 29,
        LocalGuardCapture(ownerRef, 31, LocalUseState.AVAILABLE, LocalMediaVeto.NONE),
        LocalGuardCapture(ownerRef, 31, LocalUseState.AVAILABLE, LocalMediaVeto.NONE))
    val projection = JMediaProjectionCapture(store, scope, fixtureMediaId(14), utr, 41, 7, r1,
        pendingTargetMutation = false, conflict = false, tombstone = false)
    // Explicit synthetic confirmation at the test-owner seam, never discovered or default policy.
    val apiPolicy = MediaTransportPolicy.PlatformPki(ConfirmedMediaOrigin(api), 43)
    val streamPolicy = MediaTransportPolicy.PlatformPki(ConfirmedMediaOrigin(stream), 47)
    val network = VaultNetworkCapture(local, fixtureMediaId(15), user, device,
        fixtureMediaHex(fixtureMediaSha(deviceKey.public.encoded)), binding, local.kAuthorityRevision,
        instance, 1, thumb, apiPolicy, streamPolicy, fixtureMediaId(16), fixtureMediaId(17),
        7, 4, m5, D1B1BindingGuardCapture(ownerRef, 53, D1B1CapabilityRevision(sharedRevision), d1, b1Readback),
        LocalMediaVeto.NONE, projection)
    val capture = SelectionCapture.VaultNetwork(MediaSelectionFence(fixtureMediaId(18), fixtureMediaId(19),
        59, 61, 67, fixtureMediaId(20), fixtureMediaId(21), fixtureMediaId(22), 71), network)
    val originalContent = fixtureMediaWave(phase = 0)
    val reassignedContent = fixtureMediaWave(phase = 31)
    val originalDigest = fixtureMediaHex(fixtureMediaSha(originalContent))
    val reassignedDigest = fixtureMediaHex(fixtureMediaSha(reassignedContent))
    assertFalse(originalContent.contentEquals(reassignedContent))
    assertNotEquals(originalDigest, reassignedDigest)
    fun snapshot(utrVersion: Long, recording: MediaId, variant: MediaId, variantVersion: Long,
        content: ByteArray, digest: String): ByteArray = fixtureMediaBytes(JsonObject(mapOf(
        "namespace" to JsonPrimitive("autplay.media-source"), "wire_version" to JsonPrimitive(1),
        "server_instance_id" to JsonPrimitive(instance.value), "identity_epoch" to JsonPrimitive(1),
        "identity_thumbprint_sha256" to JsonPrimitive(thumb), "capability_revision" to JsonPrimitive(m5Revision.value),
        "user_id" to JsonPrimitive(user.value), "device_id" to JsonPrimitive(device.value),
        "user_track_ref_id" to JsonPrimitive(utr.value), "user_track_ref_row_version" to JsonPrimitive(utrVersion),
        "recording_id" to JsonPrimitive(recording.value), "audio_variant_id" to JsonPrimitive(variant.value),
        "audio_variant_row_version" to JsonPrimitive(variantVersion), "sha256" to JsonPrimitive(digest),
        "total_bytes" to JsonPrimitive(content.size), "media_type" to JsonPrimitive("audio/wav"),
        "codec" to JsonPrimitive("pcm_s16le"), "container" to JsonPrimitive("wav"),
    )))
    // Independent ROOT-only LOCAL owner: no bound server/user/device/session or source evidence.
    val rootOwnerRef = fixtureMediaId(25)
    val rootOnlyCapture = SelectionCapture.AccountLocal(
        MediaSelectionFence(fixtureMediaId(30), fixtureMediaId(31), 59, 61, 67,
            fixtureMediaId(32), fixtureMediaId(33), fixtureMediaId(34), 71),
        AccountLocalCapture(fixtureMediaId(26), fixtureMediaId(27), 11, 17,
            LocalOwnerKey.Portable(fixtureMediaId(24)), rootOwnerRef, 23, fixtureMediaId(28), 29,
            LocalGuardCapture(rootOwnerRef, 31, LocalUseState.AVAILABLE, LocalMediaVeto.NONE), null))
    // Same synthetic WAV bytes may have an independent reviewed copy; no Vault relationship.
    val rootOnlyRepresentation = MediaRepresentation(originalDigest, originalContent.size.toLong(),
        "audio/wav", MediaFormat.WAV, MediaRepresentationKind.INDEPENDENT_LOCAL)
    return DesktopMediaResolverHostInputs(capture, rootOnlyCapture, rootOnlyRepresentation,
        now, now.plusSeconds(600), now.plusSeconds(7200),
        snapshot(7, r1, v1, 11, originalContent, originalDigest),
        snapshot(8, r1, v1, 12, originalContent, originalDigest),
        snapshot(9, r2, v1, 13, originalContent, originalDigest),
        snapshot(9, r2, v2, 21, reassignedContent, reassignedDigest), originalContent, reassignedContent
    ).also { it.requireValidControls() }
}

/** Restricted synthetic owner, NOT a general M5 verifier or production K/publication adapter. */
private fun fixtureAcceptM5(raw: ByteArray, expected: JsonObject, trusted: D1.TrustedM5Identity,
    now: Instant, highWater: M5CapabilityRevision, operations: Set<String>): M5ProfileCapture {
    assertTrue(raw.size in 1..65_536)
    val frame = Json.parseToJsonElement(raw.toString(Charsets.UTF_8)) as JsonObject
    assertEquals(setOf("contract_version", "schema_version", "payload", "payload_sha256",
        "signature_algorithm", "signature_b64url"), frame.keys)
    assertEquals(JsonPrimitive("v1"), frame["contract_version"])
    assertEquals(JsonPrimitive(1), frame["schema_version"])
    assertEquals(JsonPrimitive("ES256-P1363"), frame["signature_algorithm"])
    val payload = frame.getValue("payload") as JsonObject
    assertEquals(expected, payload) // Includes exact operations AND every actual limits member.
    val digest = fixtureMediaSha(fixtureMediaBytes(payload))
    assertEquals(JsonPrimitive(fixtureMediaHex(digest)), frame["payload_sha256"])
    assertEquals(trusted.thumbprintSha256, fixtureMediaHex(fixtureMediaSha(trusted.publicKeySpki)))
    val proofText = (frame.getValue("signature_b64url") as JsonPrimitive).content
    val proof = Base64.getUrlDecoder().decode(proofText)
    assertEquals(64, proof.size)
    assertEquals(proofText, Base64.getUrlEncoder().withoutPadding().encodeToString(proof))
    val key = java.security.KeyFactory.getInstance("EC").generatePublic(
        java.security.spec.X509EncodedKeySpec(trusted.publicKeySpki))
    assertTrue(Signature.getInstance("SHA256withECDSA").run {
        initVerify(key); update("AutPlay capabilities v1\n".toByteArray(Charsets.US_ASCII))
        update(digest); verify(fixtureMediaDer(proof))
    })
    fun text(name: String): String = (payload.getValue(name) as JsonPrimitive).content
    assertEquals(trusted.serverInstanceId, text("server_instance_id"))
    assertEquals(trusted.identityEpoch.toString(), text("identity_epoch"))
    assertEquals("1", text("api_major"))
    val issued = Instant.parse(text("issued_at"))
    val expires = Instant.parse(text("expires_at"))
    val revision = M5CapabilityRevision(text("capability_revision").toLong())
    assertTrue(issued <= now && expires > now && expires > issued && expires <= issued.plusSeconds(3600))
    assertTrue(revision.value >= highWater.value && operations.size <= 64)
    return M5ProfileCapture(fixtureMediaId(23), MediaId(trusted.serverInstanceId), trusted.identityEpoch,
        trusted.thumbprintSha256, MediaId(text("user_id")), MediaId(text("device_id")), revision, highWater,
        fixtureMediaHex(digest), issued, expires, raw, operations)
}

private fun fixtureMediaId(value: Int): MediaId = MediaId("72000000-0000-4000-8000-${value.toString().padStart(12, '0')}")
private fun fixtureMediaKey(): KeyPair = KeyPairGenerator.getInstance("EC").run {
    initialize(ECGenParameterSpec("secp256r1")); generateKeyPair()
}
private fun fixtureMediaSha(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
private fun fixtureMediaHex(value: ByteArray): String = value.joinToString("") { "%02x".format(it.toInt() and 255) }
private fun fixtureMediaStrings(vararg values: String): JsonArray = JsonArray(values.map { JsonPrimitive(it) })

/** Independent sorted serializer, RFC8785 only for this bounded ASCII/lexical-integer fixture. */
private fun fixtureMediaBytes(value: JsonElement): ByteArray {
    fun sorted(element: JsonElement): String = when (element) {
        is JsonObject -> element.entries.sortedBy { it.key }.joinToString(",", "{", "}") {
            require(it.key.all { character -> character.code in 32..126 })
            JsonPrimitive(it.key).toString() + ":" + sorted(it.value)
        }
        is JsonArray -> element.joinToString(",", "[", "]") { sorted(it) }
        is JsonPrimitive -> element.toString().also {
            if (element.isString) require(element.content.all { character -> character.code in 32..126 })
            else require(element.content in setOf("true", "false") ||
                Regex("0|[1-9][0-9]{0,15}").matches(element.content) &&
                element.content.toLong() <= DesktopMediaLimits.MAX_INTEGER)
        }
    }
    return sorted(value).toByteArray(Charsets.UTF_8)
}

private fun fixtureMediaEnvelope(key: KeyPair, payload: JsonObject, domain: String,
    version: String, schema: Int): ByteArray {
    val digest = fixtureMediaSha(fixtureMediaBytes(payload))
    // MESSAGE recipe: SHA256withECDSA(domain ASCII + raw payload digest), never hex/prehash.
    val der = Signature.getInstance("SHA256withECDSA").run {
        initSign(key.private); update(domain.toByteArray(Charsets.US_ASCII)); update(digest); sign()
    }
    return fixtureMediaBytes(JsonObject(mapOf(
        "contract_version" to JsonPrimitive(version), "schema_version" to JsonPrimitive(schema),
        "payload" to payload, "payload_sha256" to JsonPrimitive(fixtureMediaHex(digest)),
        "signature_algorithm" to JsonPrimitive("ES256-P1363"),
        "signature_b64url" to JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(fixtureMediaRaw(der))),
    )))
}

private fun fixtureMediaRaw(der: ByteArray): ByteArray {
    var cursor = 0
    assertEquals(0x30, der[cursor++].toInt() and 255)
    assertEquals(der.size - 2, der[cursor++].toInt() and 255)
    fun integer(): ByteArray {
        assertEquals(2, der[cursor++].toInt() and 255)
        val length = der[cursor++].toInt() and 255
        assertTrue(length in 1..33)
        val number = BigInteger(der.copyOfRange(cursor, cursor + length))
        cursor += length
        val magnitude = number.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
        assertTrue(number.signum() > 0 && magnitude.size <= 32)
        return ByteArray(32 - magnitude.size) + magnitude
    }
    val result = integer() + integer()
    assertEquals(der.size, cursor)
    return result
}

private fun fixtureMediaDer(raw: ByteArray): ByteArray {
    require(raw.size == 64)
    fun integer(start: Int): ByteArray {
        val value = BigInteger(1, raw.copyOfRange(start, start + 32))
        require(value.signum() > 0)
        val bytes = value.toByteArray()
        return byteArrayOf(2, bytes.size.toByte()) + bytes
    }
    val body = integer(0) + integer(32)
    return byteArrayOf(0x30, body.size.toByte()) + body
}

/** One-second mono 8kHz signed little-endian PCM16 RIFF/WAVE; bytes stay in JVM memory only. */
private fun fixtureMediaWave(phase: Int): ByteArray = ByteBuffer.allocate(44 + 8_000 * 2)
    .order(ByteOrder.LITTLE_ENDIAN).run {
        put("RIFF".toByteArray(Charsets.US_ASCII)); putInt(capacity() - 8)
        put("WAVEfmt ".toByteArray(Charsets.US_ASCII)); putInt(16); putShort(1); putShort(1)
        putInt(8_000); putInt(16_000); putShort(2); putShort(16)
        put("data".toByteArray(Charsets.US_ASCII)); putInt(16_000)
        repeat(8_000) { putShort((((it + phase) % 128 - 64) * 128).toShort()) }
        assertEquals(capacity(), position())
        array()
    }

private fun <T> fixtureD1Verified(result: D1.Result<T>): T {
    assertTrue("Synthetic D1 verification refused", result is D1.Result.Verified)
    return (result as D1.Result.Verified<T>).value
}
private fun <T> fixtureB1Verified(result: B1.Result<T>): T {
    assertTrue("Synthetic B1 verification/acceptance refused", result is B1.Result.Verified)
    return (result as B1.Result.Verified<T>).value
}
