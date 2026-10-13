package app.autplay.application.portableaccount

import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.social.normalizeSocialPublicId
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.AlgorithmParameters
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec
import javax.crypto.spec.SecretKeySpec
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import org.erdtman.jcs.JsonCanonicalizer

/** Public account facts and encrypted key backup. Neither a server nor a login alias owns identity. */
class PortableAccountDocument internal constructor(internal val fields: JsonObject) {
    val accountId: String get() = text("account_id")
    val publicId: String get() = text("public_id")
    val publicKeySpki: ByteArray get() = decode(text("public_key_spki_b64"), 91)
    val keyThumbprint: String get() = MessageDigest.getInstance("SHA-256").digest(publicKeySpki)
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }
    fun encode(): ByteArray = canonical(fields)
    internal fun text(name: String): String = (fields[name] as? JsonPrimitive)
        ?.takeIf { it.isString }?.content ?: error("ACCOUNT_DOCUMENT_INVALID")

    companion object {
        fun parse(bytes: ByteArray): PortableAccountDocument {
            require(bytes.size in 2..4096) { "ACCOUNT_DOCUMENT_INVALID" }
            val raw = Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
            // Reject duplicate keys and invalid Unicode before the ordinary JSON decoder.
            try { JsonCanonicalizer(raw) } catch (_: Exception) {
                throw IllegalArgumentException("ACCOUNT_DOCUMENT_INVALID")
            }
            val fields = Json.parseToJsonElement(raw) as? JsonObject ?: error("ACCOUNT_DOCUMENT_INVALID")
            require(fields.keys == MEMBERS) { "ACCOUNT_DOCUMENT_INVALID" }
            val doc = PortableAccountDocument(fields)
            require(doc.text("format") == "autplay-portable-account" && fields["version"] == JsonPrimitive(1))
            requireCanonicalUuid(doc.accountId)
            require(normalizeSocialPublicId(doc.publicId) == doc.publicId)
            require(doc.text("kdf") == "PBKDF2-HMAC-SHA256" && fields["iterations"] == JsonPrimitive(600000))
            val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(doc.publicKeySpki))
            val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
                .getParameterSpec(ECParameterSpec::class.java)
            val actual = (publicKey as java.security.interfaces.ECPublicKey).params
            require(actual.curve == expected.curve && actual.generator == expected.generator &&
                actual.order == expected.order && actual.cofactor == expected.cofactor) { "ACCOUNT_DOCUMENT_INVALID" }
            decode(doc.text("password_salt_b64"), 16)
            decode(doc.text("password_nonce_b64"), 12)
            decode(doc.text("recovery_nonce_b64"), 12)
            for (name in listOf("password_ciphertext_b64", "recovery_ciphertext_b64")) {
                require(Base64.getDecoder().decode(doc.text(name)).size in 80..512)
            }
            return doc
        }

        private val MEMBERS = setOf("format", "version", "account_id", "public_id", "public_key_spki_b64",
            "kdf", "iterations", "password_salt_b64", "password_nonce_b64", "password_ciphertext_b64",
            "recovery_nonce_b64", "recovery_ciphertext_b64")
    }
}

/** Secret-bearing objects deliberately have no generated toString or data-class copy methods. */
class UnlockedPortableAccount internal constructor(val document: PortableAccountDocument, private val pkcs8: ByteArray) : AutoCloseable {
    private var closed = false
    internal fun privateBytes(): ByteArray { check(!closed); return pkcs8.copyOf() }
    fun sign(domain: String, payload: ByteArray): ByteArray {
        check(!closed)
        require(domain in setOf("login", "register", "claim", "reauth", "backup", "rotate"))
        require(payload.size in 2..16384)
        val key = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(pkcs8))
        return Signature.getInstance("SHA256withECDSA").run {
            initSign(key)
            update("autplay:portable-account:$domain:v1\n".toByteArray(Charsets.UTF_8))
            update(payload)
            sign()
        }
    }
    override fun close() { pkcs8.fill(0); closed = true }
}

class CreatedPortableAccount internal constructor(val document: PortableAccountDocument, val recoveryCode: ByteArray) : AutoCloseable {
    override fun close() { recoveryCode.fill(0) }
}

/** An ancestor is flat: archives cannot recursively embed another archive. */
class PortableRecoveryPredecessor internal constructor(val document: PortableAccountDocument, val code: ByteArray) : AutoCloseable {
    internal fun copySecret(): PortableRecoveryPredecessor = PortableRecoveryPredecessor(document, code.copyOf())
    override fun close() { code.fill(0) }
}

/** Local recovery files contain bearer secrets; never log or save them in UI state bundles. */
class PortableRecoveryFile private constructor(
    val document: PortableAccountDocument,
    val code: ByteArray,
    val predecessors: List<PortableRecoveryPredecessor>,
) : AutoCloseable {
    override fun close() { code.fill(0); predecessors.forEach { it.close() } }

    companion object {
        const val MAX_BYTES: Int = 65_536
        const val MAX_PREDECESSORS: Int = 8

        /** Clones all owned secrets; callers retain ownership of their inputs. */
        fun create(document: PortableAccountDocument, code: ByteArray,
            predecessors: List<PortableRecoveryPredecessor> = emptyList()): PortableRecoveryFile {
            validateRecoveryEntries(document, predecessors)
            PortableAccountCrypto.recover(document, code).close()
            return PortableRecoveryFile(document, code.copyOf(), predecessors.map { it.copySecret() })
        }

        fun encode(document: PortableAccountDocument, code: ByteArray,
            predecessors: List<PortableRecoveryPredecessor> = emptyList()): ByteArray = create(document, code, predecessors).use {
            val bytes = canonical(JsonObject(mapOf("format" to JsonPrimitive("autplay-portable-account-recovery"),
                "version" to JsonPrimitive(2), "account" to document.fields,
                "recovery_code" to JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(it.code)),
                "predecessors" to recoveryEntries(it.predecessors))))
            if (bytes.size > MAX_BYTES) { bytes.fill(0); error("ACCOUNT_RECOVERY_INVALID") }
            bytes
        }

        fun decodeCode(value: CharArray): ByteArray {
            require(value.size == 43) { "ACCOUNT_RECOVERY_INVALID" }
            val text = value.concatToString()
            val bytes = Base64.getUrlDecoder().decode(text)
            if (bytes.size != 32 || Base64.getUrlEncoder().withoutPadding().encodeToString(bytes) != text) {
                bytes.fill(0)
                error("ACCOUNT_RECOVERY_INVALID")
            }
            return bytes
        }

        fun parse(bytes: ByteArray): PortableRecoveryFile {
            require(bytes.size in 2..MAX_BYTES) { "ACCOUNT_RECOVERY_INVALID" }
            val raw = Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()
            try { JsonCanonicalizer(raw) } catch (_: Exception) { throw IllegalArgumentException("ACCOUNT_RECOVERY_INVALID") }
            val fields = Json.parseToJsonElement(raw) as? JsonObject ?: error("ACCOUNT_RECOVERY_INVALID")
            val legacy = fields["version"] == JsonPrimitive(1)
            require(legacy || fields["version"] == JsonPrimitive(2)) { "ACCOUNT_RECOVERY_INVALID" }
            require(fields.keys == setOf("format", "version", "account", "recovery_code") +
                (if (legacy) emptySet() else setOf("predecessors")) &&
                fields["format"] == JsonPrimitive("autplay-portable-account-recovery")) { "ACCOUNT_RECOVERY_INVALID" }
            val document = PortableAccountDocument.parse(canonical(fields["account"] as? JsonObject ?: error("ACCOUNT_RECOVERY_INVALID")))
            val code = recoveryCode(fields)
            val predecessors = mutableListOf<PortableRecoveryPredecessor>()
            try {
                if (!legacy) parseRecoveryEntries(fields["predecessors"], predecessors)
                validateRecoveryEntries(document, predecessors)
                PortableAccountCrypto.recover(document, code).close()
                return PortableRecoveryFile(document, code, predecessors.toList())
            } catch (failure: Exception) {
                code.fill(0); predecessors.forEach { it.close() }
                throw failure
            }
        }
    }
}

/** Root-local encrypted lineage; missing legacy metadata is conservatively considered enrolled. */
class PortableAccountLineage internal constructor(
    val everEnrolled: Boolean,
    val predecessors: List<PortableRecoveryPredecessor>,
) : AutoCloseable {
    override fun close() { predecessors.forEach { it.close() } }

    internal fun encode(document: PortableAccountDocument): String {
        validateRecoveryEntries(document, predecessors)
        val bytes = canonical(JsonObject(mapOf("version" to JsonPrimitive(1), "ever_enrolled" to JsonPrimitive(everEnrolled),
            "predecessors" to recoveryEntries(predecessors))))
        return try { require(bytes.size <= MAX_BYTES) { "ACCOUNT_RECOVERY_HISTORY_FULL" }; bytes.toString(Charsets.UTF_8) }
        finally { bytes.fill(0) }
    }

    internal companion object {
        const val MAX_BYTES: Int = 49_152
        fun parse(document: PortableAccountDocument, raw: String?): PortableAccountLineage {
            if (raw == null) return PortableAccountLineage(true, emptyList())
            val encoded = raw.toByteArray(Charsets.UTF_8)
            try { require(encoded.size in 2..MAX_BYTES) { "ACCOUNT_LINEAGE_INVALID" } } finally { encoded.fill(0) }
            try { JsonCanonicalizer(raw) } catch (_: Exception) { error("ACCOUNT_LINEAGE_INVALID") }
            val fields = Json.parseToJsonElement(raw) as? JsonObject ?: error("ACCOUNT_LINEAGE_INVALID")
            require(fields.keys == setOf("version", "ever_enrolled", "predecessors") && fields["version"] == JsonPrimitive(1)) { "ACCOUNT_LINEAGE_INVALID" }
            val enrolled = (fields["ever_enrolled"] as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull
                ?: error("ACCOUNT_LINEAGE_INVALID")
            val entries = mutableListOf<PortableRecoveryPredecessor>()
            try {
                parseRecoveryEntries(fields["predecessors"], entries)
                validateRecoveryEntries(document, entries)
                return PortableAccountLineage(enrolled, entries.toList())
            } catch (failure: Exception) { entries.forEach { it.close() }; throw failure }
        }
    }
}

private fun recoveryCode(fields: JsonObject): ByteArray {
    val chars = (fields["recovery_code"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.toCharArray()
        ?: error("ACCOUNT_RECOVERY_INVALID")
    return try { PortableRecoveryFile.decodeCode(chars) } finally { chars.fill('\u0000') }
}

private fun recoveryEntries(entries: List<PortableRecoveryPredecessor>): JsonArray = JsonArray(entries.map {
    JsonObject(mapOf("account" to it.document.fields,
        "recovery_code" to JsonPrimitive(Base64.getUrlEncoder().withoutPadding().encodeToString(it.code))))
})

private fun parseRecoveryEntries(value: kotlinx.serialization.json.JsonElement?, into: MutableList<PortableRecoveryPredecessor>) {
    val entries = value as? JsonArray ?: error("ACCOUNT_RECOVERY_INVALID")
    require(entries.size <= PortableRecoveryFile.MAX_PREDECESSORS) { "ACCOUNT_RECOVERY_HISTORY_FULL" }
    for (entry in entries) {
        val fields = entry as? JsonObject ?: error("ACCOUNT_RECOVERY_INVALID")
        require(fields.keys == setOf("account", "recovery_code")) { "ACCOUNT_RECOVERY_INVALID" }
        val document = PortableAccountDocument.parse(canonical(fields["account"] as? JsonObject ?: error("ACCOUNT_RECOVERY_INVALID")))
        into += PortableRecoveryPredecessor(document, recoveryCode(fields))
    }
}

private fun validateRecoveryEntries(document: PortableAccountDocument, entries: List<PortableRecoveryPredecessor>) {
    require(entries.size <= PortableRecoveryFile.MAX_PREDECESSORS) { "ACCOUNT_RECOVERY_HISTORY_FULL" }
    val roots = mutableSetOf(document.keyThumbprint)
    for (entry in entries) {
        require(entry.document.accountId == document.accountId && entry.document.publicId == document.publicId &&
            roots.add(entry.document.keyThumbprint)) { "ACCOUNT_RECOVERY_INVALID" }
        PortableAccountCrypto.recover(entry.document, entry.code).close()
    }
}

/** Passwords only unwrap a random account key on the client; they are never a server credential. */
object PortableAccountCrypto {
    private val random = SecureRandom()

    fun validPassword(password: CharArray): Boolean {
        val count = Character.codePointCount(password, 0, password.size)
        if (count !in 15..128) return false
        if (password.all { it == password.first() }) return false
        return !password.concatToString().lowercase(java.util.Locale.ROOT).let {
            it in setOf("password123456789", "123456789012345", "qwertyuiopasdfgh", "passwordpassword")
        }
    }

    fun create(publicId: String, password: CharArray, existingAccountId: String? = null): CreatedPortableAccount {
        require(normalizeSocialPublicId(publicId) == publicId) { "PUBLIC_ID_INVALID" }
        require(validPassword(password)) { "ACCOUNT_PASSWORD_INVALID" }
        val id = existingAccountId ?: UUID.randomUUID().toString()
        requireCanonicalUuid(id)
        val pair = KeyPairGenerator.getInstance("EC").run { initialize(ECGenParameterSpec("secp256r1")); generateKeyPair() }
        val private = pair.private.encoded
        val recovery = ByteArray(32).also(random::nextBytes)
        try {
            return CreatedPortableAccount(seal(id, publicId, pair.public.encoded, private, password, recovery), recovery.copyOf())
        } finally { private.fill(0); recovery.fill(0) }
    }

    fun unlock(document: PortableAccountDocument, password: CharArray): UnlockedPortableAccount {
        require(Character.codePointCount(password, 0, password.size) in 15..128) { "ACCOUNT_AUTHENTICATION_FAILED" }
        val key = derive(password, decode(document.text("password_salt_b64"), 16))
        return try { open(document, key, "password") } finally { key.fill(0) }
    }

    fun recover(document: PortableAccountDocument, recoveryCode: ByteArray): UnlockedPortableAccount {
        require(recoveryCode.size == 32) { "ACCOUNT_RECOVERY_INVALID" }
        return open(document, recoveryCode, "recovery")
    }

    /** A reset changes authority. The caller must commit the signed rotation on the chosen server. */
    fun rotate(unlocked: UnlockedPortableAccount, password: CharArray): CreatedPortableAccount =
        create(unlocked.document.publicId, password, unlocked.document.accountId)

    private fun open(doc: PortableAccountDocument, key: ByteArray, kind: String): UnlockedPortableAccount {
        val plaintext = try {
            cipher(Cipher.DECRYPT_MODE, key, decode(doc.text("${kind}_nonce_b64"), 12), aad(doc.accountId, doc.publicId, doc.publicKeySpki, kind))
                .doFinal(Base64.getDecoder().decode(doc.text("${kind}_ciphertext_b64")))
        } catch (_: Exception) { throw IllegalArgumentException("ACCOUNT_AUTHENTICATION_FAILED") }
        try {
            // Prove correspondence; a substituted public key cannot become a different account.
            val privateKey = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(plaintext))
            val check = "autplay:portable-account:key-check:v1".toByteArray(Charsets.US_ASCII)
            val proof = Signature.getInstance("SHA256withECDSA").run { initSign(privateKey); update(check); sign() }
            val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(doc.publicKeySpki))
            require(Signature.getInstance("SHA256withECDSA").run { initVerify(publicKey); update(check); verify(proof) })
            return UnlockedPortableAccount(doc, plaintext.copyOf())
        } finally { plaintext.fill(0) }
    }

    private fun seal(id: String, login: String, public: ByteArray, private: ByteArray, password: CharArray, recovery: ByteArray): PortableAccountDocument {
        val salt = ByteArray(16).also(random::nextBytes)
        val passwordNonce = ByteArray(12).also(random::nextBytes)
        val recoveryNonce = ByteArray(12).also(random::nextBytes)
        val key = derive(password, salt)
        try {
            val passwordCiphertext = cipher(Cipher.ENCRYPT_MODE, key, passwordNonce, aad(id, login, public, "password")).doFinal(private)
            val recoveryCiphertext = cipher(Cipher.ENCRYPT_MODE, recovery, recoveryNonce, aad(id, login, public, "recovery")).doFinal(private)
            return PortableAccountDocument(JsonObject(mapOf(
                "format" to JsonPrimitive("autplay-portable-account"), "version" to JsonPrimitive(1),
                "account_id" to JsonPrimitive(id), "public_id" to JsonPrimitive(login), "public_key_spki_b64" to JsonPrimitive(b64(public)),
                "kdf" to JsonPrimitive("PBKDF2-HMAC-SHA256"), "iterations" to JsonPrimitive(600000),
                "password_salt_b64" to JsonPrimitive(b64(salt)), "password_nonce_b64" to JsonPrimitive(b64(passwordNonce)),
                "password_ciphertext_b64" to JsonPrimitive(b64(passwordCiphertext)),
                "recovery_nonce_b64" to JsonPrimitive(b64(recoveryNonce)), "recovery_ciphertext_b64" to JsonPrimitive(b64(recoveryCiphertext)),
            )))
        } finally { key.fill(0) }
    }

    private fun derive(password: CharArray, salt: ByteArray): ByteArray {
        val spec = PBEKeySpec(password, salt, 600_000, 256)
        return try { SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded }
        finally { spec.clearPassword() }
    }

    private fun cipher(mode: Int, key: ByteArray, nonce: ByteArray, aad: ByteArray): Cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
        init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce)); updateAAD(aad)
    }

    private fun aad(id: String, login: String, public: ByteArray, kind: String): ByteArray =
        "autplay:portable-account:key-envelope:v1\n$id\n$login\n${b64(public)}\n$kind".toByteArray(Charsets.UTF_8)
}

private fun canonical(fields: JsonObject): ByteArray = JsonCanonicalizer(fields.toString()).encodedString.toByteArray(Charsets.UTF_8)
private fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
private fun decode(value: String, size: Int): ByteArray = Base64.getDecoder().decode(value).also {
    require(it.size == size && b64(it) == value) { "ACCOUNT_DOCUMENT_INVALID" }
}
