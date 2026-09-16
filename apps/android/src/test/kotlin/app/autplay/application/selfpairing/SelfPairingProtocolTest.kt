package app.autplay.application.selfpairing

import app.autplay.data.security.M5DeviceKeyStore
import java.io.File
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfPairingProtocolTest {
    private val vectors = fixture("proof-vectors.json")
    private val examples = fixture("schema-examples.json")

    @Test fun realP256SignaturesCanonicalHashesAndSasMatchServerFixtures() {
        val keys = FixtureKeys(vectors)
        val claim = vectors["requests"]!!.jsonObject["claim"]!!.jsonObject["request"]!!.jsonObject
        for (kind in listOf("start", "claim", "poll", "decision", "exchange")) {
            val vector = vectors["requests"]!!.jsonObject[kind]!!.jsonObject
            val request = vector["request"]!!.jsonObject
            val identity = SelfPairingIdentity.parse(request)
            val common = identity.fields().keys + setOf("contract_version", "schema_version", "ceremony_id", "requested_at", "request_sha256", "device_signature_b64url")
            val encoded = SelfPairingProof.request(kind, identity, request.text("ceremony_id"), request.filterKeys { it !in common }, keys, "fixture", request.instant("requested_at"))
            val produced = SelfPairingJson.parse(encoded)
            assertEquals(vector.text("request_sha256"), produced.text("request_sha256"))
            if (kind in listOf("claim", "poll", "exchange")) {
                val public = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keys.publicKeySpki("fixture")))
                for (candidate in listOf(request, produced)) {
                    val verifier = Signature.getInstance("SHA256withECDSAinP1363Format")
                    verifier.initVerify(public)
                    verifier.update(SelfPairingProof.domain(kind).toByteArray(Charsets.US_ASCII))
                    verifier.update(hex(candidate.text("request_sha256")))
                    assertTrue(verifier.verify(Base64.getUrlDecoder().decode(candidate.text("device_signature_b64url"))))
                }
            }
        }
        assertEquals(vectors["sas"]!!.jsonObject.text("comparison_code"), SelfPairingProof.comparisonCode(
            SelfPairingIdentity.parse(claim), claim.text("ceremony_id"), claim.text("request_sha256"), claim.text("device_key_thumbprint_sha256"),
        ))
    }

    @Test fun qrIsBoundedTypedCanonicalAndHasNoLoginMaterial() {
        val raw = examples["qr-document"]!!.jsonObject
        val now = Instant.parse("2026-09-16T12:01:00Z")
        val qr = SelfPairingQr.parse(raw.toString().toByteArray(), now)
        assertFalse(qr.toString().contains(raw.text("rendezvous_secret")))
        assertEquals(raw, SelfPairingJson.parse(qr.encode()))
        qr.close()
        assertTrue(qr.rendezvousSecret.all { it == 0.toByte() })
        val bad = listOf(
            raw.toString().dropLast(1) + ",\"version\":1}",
            raw.toString().dropLast(1) + ",\"vers\\u0069on\":1}",
            JsonObject(raw + ("version" to JsonPrimitive("1"))).toString(),
            JsonObject(raw + ("account_id" to JsonPrimitive("injected"))).toString(),
            JsonObject(raw + ("expected_api_origin" to JsonPrimitive("https://api.test.invalid/redirect?secret=x"))).toString(),
            raw.toString().replace("\"version\":1", "\"version\":1.0"),
            raw.toString().replace(raw.text("rendezvous_secret"), "A".repeat(42) + "B"),
            "[".repeat(2000) + "]".repeat(2000),
        )
        for (value in bad) assertThrows(IllegalArgumentException::class.java) {
            SelfPairingQr.parse(value.toByteArray(), now)
        }
        assertThrows(IllegalArgumentException::class.java) { SelfPairingQr.parse(raw.toString().toByteArray(), now.plusSeconds(1000)) }
    }

    @Test fun malformedUtf8AndLargeQrNeverReachNetwork() {
        assertThrows(Exception::class.java) { SelfPairingQr.parse(byteArrayOf(0xC0.toByte(), 0xAF.toByte())) }
        assertThrows(IllegalArgumentException::class.java) { SelfPairingQr.parse(ByteArray(4097) { 32 }) }
    }

    private class FixtureKeys(vectors: JsonObject) : M5DeviceKeyStore {
        private val privateKey = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(Base64.getDecoder().decode(vectors.text("private_key_pkcs8_b64"))))
        private val claim = vectors["requests"]!!.jsonObject["claim"]!!.jsonObject["request"]!!.jsonObject
        override fun publicKeySpki(alias: String): ByteArray = Base64.getDecoder().decode(claim.text("device_public_key_spki_b64"))
        override fun publicKeyThumbprintSha256(alias: String): String = claim.text("device_key_thumbprint_sha256")
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSAinP1363Format").run {
            initSign(privateKey); update(domainSeparator.toByteArray(Charsets.US_ASCII)); update(payloadSha256); sign()
        }
        override fun ensure(alias: String) = Unit
        override fun delete(alias: String) = Unit
    }

    companion object {
        private fun fixture(name: String): JsonObject {
            val root = generateSequence(File(requireNotNull(System.getProperty("user.dir")))) { it.parentFile }
                .first { File(it, "tests/fixtures/self-device-pairing/v1/$name").isFile }
            return Json.parseToJsonElement(File(root, "tests/fixtures/self-device-pairing/v1/$name").readText()).jsonObject
        }
        private fun hex(value: String): ByteArray = value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    }
}
