package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.*
import app.autplay.data.security.M5DeviceKeyStore
import java.io.File
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AccountRecoveryProtocolTest {
    private val vectors = recoveryFixture()

    @Test fun serverVectorsMatchCodeScopeCanonicalHashesAndRealDeviceSignatures() {
        val keys = RecoveryFixtureKeys(vectors)
        val requests = vectors.getValue("requests").jsonObject
        for (kind in listOf("configure", "preview", "recover")) {
            val expected = requests.getValue(kind).jsonObject
            val identity = SelfPairingIdentity.parse(expected)
            val common = identity.fields().keys + setOf("contract_version", "schema_version", "account_id", "operation_id", "requested_at", "request_sha256", "device_signature_b64url")
            val raw = AccountRecoveryProof.request(kind, identity, expected.text("account_id"),
                expected.filterKeys { it !in common }, expected.instant("requested_at"), keys, "fixture", expected.text("operation_id"))
            val actual = SelfPairingJson.parse(raw)
            assertEquals(expected.text("request_sha256"), actual.text("request_sha256"))
            assertEquals(vectors.text("verifier_sha256"), AccountRecoveryProof.verifier(identity, expected.text("account_id"), vectors.text("code").toByteArray()))
            if (kind != "configure") for (candidate in listOf(actual, expected)) {
                val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keys.publicKeySpki("fixture")))
                val verifier = Signature.getInstance("SHA256withECDSAinP1363Format")
                verifier.initVerify(publicKey)
                verifier.update("autplay:account-recovery:$kind:v1\n".toByteArray(Charsets.US_ASCII))
                verifier.update(candidate.text("request_sha256").chunked(2).map { it.toInt(16).toByte() }.toByteArray())
                assertTrue(verifier.verify(Base64.getUrlDecoder().decode(candidate.text("device_signature_b64url"))))
            }
        }
    }

    @Test fun portableFileRoundTripsAndRejectsAmbiguousOrUnboundedInputs() {
        val raw = vectors.getValue("document").jsonObject
        val document = AccountRecoveryDocument.parse(raw.toString().toByteArray())
        assertEquals(raw, SelfPairingJson.parse(document.encode()))
        assertFalse(document.toString().contains(vectors.text("code")))
        assertArrayEquals(document.code, AccountRecoveryProof.normalize(vectors.text("code").lowercase().chunked(4).joinToString("-")))
        document.close()
        assertTrue(document.code.all { it == 0.toByte() })
        for (bad in listOf(
            raw.toString().dropLast(1) + ",\"version\":1}",
            raw.toString().dropLast(1) + ",\"vers\\u0069on\":1}",
            JsonObject(raw + ("version" to JsonPrimitive("1"))).toString(),
            JsonObject(raw + ("version" to JsonPrimitive(2))).toString(),
            JsonObject(raw + ("extra" to JsonPrimitive("ignored"))).toString(),
            JsonObject(raw + ("api_origin" to JsonPrimitive("https://api.test.invalid/path?code=x"))).toString(),
            " ".repeat(4097), "[]",
        )) assertThrows(IllegalArgumentException::class.java) { AccountRecoveryDocument.parse(bad.toByteArray()) }
        for (bad in listOf("I".repeat(32), "O".repeat(32), "\u0410".repeat(32), "A".repeat(31), "A".repeat(33), " ".repeat(97))) {
            assertThrows(IllegalArgumentException::class.java) { AccountRecoveryProof.normalize(bad) }
        }
    }
}

internal fun recoveryFixture(): JsonObject {
    val path = "tests/fixtures/account-recovery/v1/proof-vectors.json"
    val root = generateSequence(File(requireNotNull(System.getProperty("user.dir")))) { it.parentFile }
        .first { File(it, path).isFile }
    return Json.parseToJsonElement(File(root, path).readText()).jsonObject
}

internal class RecoveryFixtureKeys(private val fixture: JsonObject = recoveryFixture()) : M5DeviceKeyStore {
    var created = 0
    var lost = false
    private val privateKey = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(Base64.getDecoder().decode(fixture.text("test_only_private_key_pkcs8_b64"))))
    override fun ensure(alias: String) { created++ }
    override fun delete(alias: String) = Unit
    override fun publicKeySpki(alias: String): ByteArray { check(!lost); return Base64.getDecoder().decode(fixture.text("public_key_spki_b64")) }
    override fun publicKeyThumbprintSha256(alias: String) = SelfPairingProof.hash(publicKeySpki(alias))
    override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSAinP1363Format").run {
        check(!lost); initSign(privateKey); update(domainSeparator.toByteArray(Charsets.US_ASCII)); update(payloadSha256); sign()
    }
}
