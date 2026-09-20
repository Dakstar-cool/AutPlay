package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.*
import java.io.File
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class AccountDeletionProtocolTest {
    @Test fun serverVectorsMatchAllDeletionPurposesAndRejectRecoveryDomain() {
        val path = "tests/fixtures/account-deletion/v1/proof-vectors.json"
        val root = generateSequence(File(requireNotNull(System.getProperty("user.dir")))) { it.parentFile }.first { File(it, path).isFile }
        val fixture = Json.parseToJsonElement(File(root, path).readText()).jsonObject
        val keys = RecoveryFixtureKeys(fixture)
        for ((kind, element) in fixture.getValue("requests").jsonObject) {
            val expected = element.jsonObject
            val identity = SelfPairingIdentity.parse(expected)
            val common = identity.fields().keys + setOf("contract_version", "schema_version", "account_id", "operation_id", "requested_at", "request_sha256", "device_signature_b64url")
            val raw = AccountDeletionProof.request(kind, identity, expected.text("account_id"), expected.filterKeys { it !in common },
                expected.instant("requested_at"), keys, "fixture", expected.text("operation_id"))
            try {
                val actual = SelfPairingJson.parse(raw)
                assertEquals(expected.text("request_sha256"), actual.text("request_sha256"))
                val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(keys.publicKeySpki("fixture")))
                for (candidate in listOf(expected, actual)) for (domain in listOf("account-deletion", "account-recovery")) {
                    val verifier = Signature.getInstance("SHA256withECDSAinP1363Format")
                    verifier.initVerify(publicKey)
                    verifier.update("autplay:$domain:$kind:v1\n".toByteArray(Charsets.US_ASCII))
                    verifier.update(candidate.text("request_sha256").chunked(2).map { it.toInt(16).toByte() }.toByteArray())
                    assertEquals(domain == "account-deletion", verifier.verify(Base64.getUrlDecoder().decode(candidate.text("device_signature_b64url"))))
                }
            } finally { raw.fill(0) }
        }
    }

    @Test fun nonacceptanceMustMatchExactOriginalProofAndStrictExpiry() {
        val now = Instant.parse("2026-09-18T12:00:00Z")
        val account = "10000000-0000-4000-8000-000000000003"
        val operation = "10000000-0000-4000-8000-000000000009"
        val request = buildJsonObject {
            put("account_id", account); put("operation_id", operation); put("request_sha256", "a".repeat(64)); put("requested_at", now.toString())
        }
        val reply = buildJsonObject {
            put("contract_version", "v1"); put("schema_version", 1); put("account_id", account)
            put("deletion_request_id", operation); put("request_sha256", "a".repeat(64))
            put("state", "NOT_ACCEPTED"); put("resolved_at", now.plusSeconds(121).toString()); put("replayed", true)
        }
        assertEquals(operation, AccountDeletionNotAccepted.parse(reply, request).requestId)
        for (bad in listOf(
            reply + ("request_sha256" to JsonPrimitive("b".repeat(64))),
            reply + ("resolved_at" to JsonPrimitive(now.plusSeconds(120).toString())),
            reply + ("account_id" to JsonPrimitive(operation)),
            reply + ("deletion_request_id" to JsonPrimitive(account)),
            reply + ("state" to JsonPrimitive("CANCELLED")),
            reply + ("replayed" to JsonPrimitive("true")),
            reply + ("extra" to JsonPrimitive("ignored")),
        )) assertThrows(IllegalArgumentException::class.java) { AccountDeletionNotAccepted.parse(JsonObject(bad), request) }
        assertThrows(IllegalArgumentException::class.java) { AccountDeletionReceipt.parse(reply, account, operation) }
    }

    @Test fun deadlineAndConfirmedAccountAreStrictAndExpiryCannotBecomeRecovery() {
        val account = "10000000-0000-4000-8000-000000000003"
        val now = Instant.parse("2026-09-18T12:00:00Z")
        val request = JsonObject(mapOf("account_id" to JsonPrimitive(account)))
        val response = buildJsonObject {
            put("contract_version", "v1"); put("schema_version", 1)
            put("deletion_request_id", "10000000-0000-4000-8000-000000000009"); put("account_id", account)
            put("state", "PENDING"); put("revision", 1); put("requested_at", now.toString())
            put("cancel_before", now.plusSeconds(30 * 86400).toString()); put("replayed", false)
            put("account_label", "Account"); put("code_generation", 3); put("confirmation_required", true)
        }
        assertEquals(3L, AccountDeletionReceipt.preview(response, request, now).generation)
        for (bad in listOf(
            response + ("cancel_before" to JsonPrimitive(now.plusSeconds(30 * 86400 + 1).toString())),
            response + ("account_id" to JsonPrimitive("10000000-0000-4000-8000-000000000004")),
            response + ("revision" to JsonPrimitive(2)), response + ("confirmation_required" to JsonPrimitive("true")),
            response + ("state" to JsonPrimitive("CANCELLED")), response + ("extra" to JsonPrimitive("ignored")),
        )) assertThrows(IllegalArgumentException::class.java) { AccountDeletionReceipt.preview(JsonObject(bad), request, now) }
        assertThrows(IllegalArgumentException::class.java) { AccountDeletionReceipt.preview(response, request, now.plusSeconds(30 * 86400)) }
        assertThrows(IllegalArgumentException::class.java) { AccountRecoveryPreview.parse(response, request) }
    }
}
