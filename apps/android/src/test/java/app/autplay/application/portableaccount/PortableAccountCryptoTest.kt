package app.autplay.application.portableaccount

import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class PortableAccountCryptoTest {
    private val password get() = "coffee rain snowy garden".toCharArray()

    @Test fun recoveryArchiveAcceptsLegacyAndClosesAllSecretsButRejectsMixedRecursiveOrDuplicateRoots() {
        val passphrase = password
        try {
            PortableAccountCrypto.create("owner", passphrase).use { previous ->
                PortableAccountCrypto.create("owner", passphrase, previous.document.accountId).use { current ->
                    val entry = PortableRecoveryPredecessor(previous.document, previous.recoveryCode.copyOf())
                    entry.use {
                        val bytes = PortableRecoveryFile.encode(current.document, current.recoveryCode, listOf(entry))
                        try {
                            val parsed = PortableRecoveryFile.parse(bytes)
                            assertEquals(previous.document.keyThumbprint, parsed.predecessors.single().document.keyThumbprint)
                            val currentSecret = parsed.code
                            val previousSecret = parsed.predecessors.single().code
                            parsed.close()
                            assertTrue(currentSecret.all { it == 0.toByte() } && previousSecret.all { it == 0.toByte() })
                            val root = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
                            val legacy = JsonObject((root - "predecessors") + ("version" to JsonPrimitive(1))).toString().toByteArray()
                            try { PortableRecoveryFile.parse(legacy).use { assertTrue(it.predecessors.isEmpty()) } }
                            finally { legacy.fill(0) }
                            val nested = JsonObject(root + ("predecessors" to JsonArray(listOf(JsonObject(
                                root["predecessors"]!!.jsonArray.single().jsonObject + ("predecessors" to JsonArray(emptyList())),
                            ))))).toString().toByteArray()
                            try { expectFailure { PortableRecoveryFile.parse(nested) } } finally { nested.fill(0) }
                            expectFailure { PortableRecoveryFile.create(current.document, current.recoveryCode, listOf(entry, entry)) }
                            expectFailure { PortableRecoveryFile.create(current.document, current.recoveryCode, List(9) { entry }) }
                            val foreign = PortableAccountDocument(JsonObject(previous.document.fields +
                                ("account_id" to JsonPrimitive("819777b7-b82c-4f61-a988-38f822f0fd75"))))
                            PortableRecoveryPredecessor(foreign, previous.recoveryCode.copyOf()).use { alien ->
                                expectFailure { PortableRecoveryFile.create(current.document, current.recoveryCode, listOf(alien)) }
                            }
                            expectFailure { PortableRecoveryFile.parse(ByteArray(PortableRecoveryFile.MAX_BYTES + 1)) }
                        } finally { bytes.fill(0) }
                    }
                }
            }
        } finally { passphrase.fill('\u0000') }
    }

    @Test fun accountIsCreatedOfflineAndUnlocksWithPasswordOrRecovery() {
        PortableAccountCrypto.create("test_account", password).use { created ->
            val parsed = PortableAccountDocument.parse(created.document.encode())
            assertEquals(created.document.accountId, parsed.accountId)
            assertEquals(created.document.keyThumbprint, parsed.keyThumbprint)
            val payload = "{\"nonce\":\"a-test-server-challenge\"}".toByteArray()
            val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(parsed.publicKeySpki))
            PortableAccountCrypto.unlock(parsed, password).use { unlocked ->
                val proof = unlocked.sign("login", payload)
                assertTrue(Signature.getInstance("SHA256withECDSA").run {
                    initVerify(publicKey); update("autplay:portable-account:login:v1\n".toByteArray()); update(payload); verify(proof)
                })
                assertFalse(Signature.getInstance("SHA256withECDSA").run {
                    initVerify(publicKey); update("autplay:portable-account:register:v1\n".toByteArray()); update(payload); verify(proof)
                })
            }
            PortableAccountCrypto.recover(parsed, created.recoveryCode).use { recovered ->
                assertEquals(parsed.accountId, recovered.document.accountId)
            }
        }
    }

    @Test fun existingAccountKeepsItsIdDuringUpgrade() {
        val id = "01234567-1234-4321-8234-012345678901"
        PortableAccountCrypto.create("existing", password, id).use { assertEquals(id, it.document.accountId) }
    }

    @Test fun wrongPasswordAndChangedIdentityCannotUnlockTheKey() {
        PortableAccountCrypto.create("owner_one", password).use { created ->
            expectFailure { PortableAccountCrypto.unlock(created.document, "wrong password long enough".toCharArray()) }
            val altered = created.document.encode().toString(Charsets.UTF_8).replace("owner_one", "owner_two").toByteArray()
            val parsed = PortableAccountDocument.parse(altered)
            expectFailure { PortableAccountCrypto.unlock(parsed, password) }
            expectFailure { PortableAccountCrypto.recover(parsed, created.recoveryCode) }
        }
    }

    @Test fun malformedOrExpensiveDocumentsAreRejectedBeforeKeyDerivation() {
        PortableAccountCrypto.create("owner", password).use { created ->
            val raw = created.document.encode().toString(Charsets.UTF_8)
            expectFailure { PortableAccountDocument.parse(raw.replace("600000", "600000000").toByteArray()) }
            expectFailure { PortableAccountDocument.parse(raw.replace("\"version\":1", "\"version\":1,\"version\":1").toByteArray()) }
            expectFailure { PortableAccountDocument.parse(raw.replace("\"version\":1", "\"version\":1,\"extra\":true").toByteArray()) }
        }
    }

    @Test fun recoveryRotatesRootAndCodeWhilePreservingTheAccount() {
        PortableAccountCrypto.create("owner", password).use { created ->
            PortableAccountCrypto.recover(created.document, created.recoveryCode).use { recovered ->
                PortableAccountCrypto.rotate(recovered, "another long password phrase".toCharArray()).use { updated ->
                    assertEquals(created.document.accountId, updated.document.accountId)
                    assertNotEquals(created.document.keyThumbprint, updated.document.keyThumbprint)
                    expectFailure { PortableAccountCrypto.unlock(updated.document, password) }
                    expectFailure { PortableAccountCrypto.recover(updated.document, created.recoveryCode) }
                    PortableAccountCrypto.unlock(updated.document, "another long password phrase".toCharArray()).close()
                    PortableAccountCrypto.recover(updated.document, updated.recoveryCode).close()
                }
            }
        }
    }

    @Test fun recoveryFileAndCodeAreBoundedStrictAndMatchTheRoot() {
        PortableAccountCrypto.create("owner", password).use { created ->
            val file = PortableRecoveryFile.encode(created.document, created.recoveryCode)
            try {
                PortableRecoveryFile.parse(file).use { parsed ->
                    assertArrayEquals(created.recoveryCode, parsed.code)
                    PortableAccountCrypto.recover(parsed.document, parsed.code).close()
                }
                val raw = file.toString(Charsets.UTF_8)
                expectFailure { PortableRecoveryFile.parse(raw.replace("\"version\":1", "\"version\":1,\"version\":1").toByteArray()) }
                expectFailure { PortableRecoveryFile.parse(ByteArray(8193)) }
                expectFailure { PortableRecoveryFile.decodeCode("short".toCharArray()) }
                val padded = java.util.Base64.getUrlEncoder().encodeToString(created.recoveryCode)
                expectFailure { PortableRecoveryFile.decodeCode(padded.toCharArray()) }
            } finally { file.fill(0) }
        }
    }

    @Test fun closedSecretCannotSignAndPasswordRulesAllowPassphrases() {
        assertFalse(PortableAccountCrypto.validPassword("short".toCharArray()))
        assertFalse(PortableAccountCrypto.validPassword("aaaaaaaaaaaaaaaa".toCharArray()))
        assertTrue(PortableAccountCrypto.validPassword("длинная фраза для входа".toCharArray()))
        PortableAccountCrypto.create("owner", password).use { created ->
            val unlocked = PortableAccountCrypto.unlock(created.document, password)
            unlocked.close()
            expectFailure { unlocked.sign("login", "{}".toByteArray()) }
        }
    }

    private fun expectFailure(action: () -> Unit) {
        try { action(); fail("Expected fail-closed rejection") } catch (_: IllegalArgumentException) { }
        catch (_: IllegalStateException) { }
    }
}
