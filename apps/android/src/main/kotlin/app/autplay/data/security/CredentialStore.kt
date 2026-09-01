package app.autplay.data.security

import android.annotation.SuppressLint
import android.content.Context
import android.util.Base64
import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * Port for short secret material scoped to a server profile.
 *
 * Callers provide bytes instead of String so this layer does not introduce long-lived immutable
 * copies of tokens. It never logs credential material, URLs, or aliases.
 */
interface CredentialStore {
    suspend fun read(profileId: ServerProfileId): ByteArray?

    suspend fun write(profileId: ServerProfileId, material: ByteArray)

    suspend fun clear(profileId: ServerProfileId)

    /**
     * Returns whether any encrypted profile still owns an uncertain PA2 first registration.
     * Implementations that cannot enumerate their private credential namespace must fail closed.
     */
    suspend fun hasPublicAccessPendingRegistration(): Boolean =
        throw UnsupportedOperationException("CREDENTIAL_PENDING_ENUMERATION_UNAVAILABLE")
}

/** Versioned access/refresh material encrypted as one profile-scoped Keystore value. */
data class SessionCredentialEnvelope(
    val accessToken: String,
    val refreshToken: String?,
    val generation: Long,
    val refreshPending: Boolean = false,
    /** M5 secret/non-secret crash-recovery marker and session lineage. */
    val bindingCommitId: String? = null,
    val sessionId: String? = null,
    val sessionFamilyId: String? = null,
    val sessionGeneration: Long? = null,
    /** Encrypted exact M5 rotation replay material; never present for legacy P03 credentials. */
    val m5PendingRotationId: String? = null,
    val m5PendingRotationRequest: String? = null,
    val m5PendingSuccessorRefreshToken: String? = null,
    val m5PendingExchangeId: String? = null,
    val m5PendingExchangeRequest: String? = null,
    val m5PendingExchangeSuccessorRefreshToken: String? = null,
    /** Encrypted F-018 consent snapshot retained until idempotent Room materialization succeeds. */
    val m5PendingMaterializationRequest: String? = null,
    /** PA2-only replay material. Deliberately separate from M5 pending exchange fields. */
    val publicAccessPendingRegistrationId: String? = null,
    val publicAccessPendingCanonicalRequest: String? = null,
    val publicAccessPendingSuccessorRefreshToken: String? = null,
) {
    init {
        require(accessToken.isNotBlank() && accessToken.length <= MAX_TOKEN_CHARS)
        require(refreshToken == null || (refreshToken.isNotBlank() && refreshToken.length <= MAX_TOKEN_CHARS))
        require(generation >= 0)
        val m5 = listOf(bindingCommitId, sessionId, sessionFamilyId, sessionGeneration)
        require(m5.all { it == null } || m5.all { it != null }) { "M5 credential lineage must be complete." }
        require(sessionGeneration == null || sessionGeneration >= 0)
        val pending = listOf(m5PendingRotationId, m5PendingRotationRequest, m5PendingSuccessorRefreshToken)
        require(pending.all { it == null } || pending.all { it != null }) { "M5 pending rotation must be complete." }
        require(m5PendingRotationId == null || refreshPending) { "M5 pending rotation requires durable pending state." }
        val exchange = listOf(m5PendingExchangeId, m5PendingExchangeRequest, m5PendingExchangeSuccessorRefreshToken)
        require(exchange.all { it == null } || exchange.all { it != null }) { "M5 pending exchange must be complete." }
        require(m5PendingExchangeId == null || refreshPending) { "M5 pending exchange requires durable pending state." }
        require(m5PendingRotationId == null || m5PendingExchangeId == null) { "M5 pending operations are mutually exclusive." }
        require(
            m5PendingMaterializationRequest == null ||
                (m5.all { it != null } && m5PendingMaterializationRequest.length in 2..MAX_MATERIALIZATION_CHARS),
        ) { "M5 pending materialization requires complete active lineage." }
        val publicAccess = listOf(publicAccessPendingRegistrationId, publicAccessPendingCanonicalRequest, publicAccessPendingSuccessorRefreshToken)
        require(publicAccess.all { it == null } || publicAccess.all { it != null }) { "Public access pending registration must be complete." }
        require(publicAccessPendingRegistrationId == null || (refreshPending && m5PendingExchangeId == null && m5PendingRotationId == null)) { "Public access pending registration requires isolated pending state." }
        publicAccessPendingRegistrationId?.let { require(java.util.UUID.fromString(it).toString() == it) }
        require(publicAccessPendingCanonicalRequest == null || publicAccessPendingCanonicalRequest.length in 2..32_768) { "Public access pending request is bounded." }
        require(publicAccessPendingSuccessorRefreshToken == null || publicAccessPendingSuccessorRefreshToken.length in 43..128) { "Public access pending successor is bounded." }
    }

    private companion object {
        const val MAX_TOKEN_CHARS = 4_096
        const val MAX_MATERIALIZATION_CHARS = 8_192
    }
}

/** Keeps old raw access-token values readable while new profiles retain their refresh credential. */
object SessionCredentialEnvelopeCodec {
    fun decode(material: ByteArray): SessionCredentialEnvelope {
        val raw = material.toString(StandardCharsets.UTF_8)
        if (!raw.trimStart().startsWith("{")) return SessionCredentialEnvelope(raw, null, 0)
        val value = Json.parseToJsonElement(raw).jsonObject
        return SessionCredentialEnvelope(
            accessToken = value.requiredString("access_token"),
            refreshToken = value["refresh_token"]?.let { element ->
                (element as? JsonPrimitive)?.takeUnless { it.content == "null" }?.content
            },
            generation = value["generation"]?.jsonPrimitive?.content?.toLongOrNull() ?: 0,
            refreshPending = value["refresh_pending"]?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: false,
            bindingCommitId = value["binding_commit_id"]?.jsonPrimitive?.content,
            sessionId = value["session_id"]?.jsonPrimitive?.content,
            sessionFamilyId = value["session_family_id"]?.jsonPrimitive?.content,
            sessionGeneration = value["session_generation"]?.jsonPrimitive?.content?.toLongOrNull(),
            m5PendingRotationId = value["m5_pending_rotation_id"]?.jsonPrimitive?.content,
            m5PendingRotationRequest = value["m5_pending_rotation_request"]?.jsonPrimitive?.content,
            m5PendingSuccessorRefreshToken = value["m5_pending_successor_refresh_token"]?.jsonPrimitive?.content,
            m5PendingExchangeId = value["m5_pending_exchange_id"]?.jsonPrimitive?.content,
            m5PendingExchangeRequest = value["m5_pending_exchange_request"]?.jsonPrimitive?.content,
            m5PendingExchangeSuccessorRefreshToken = value["m5_pending_exchange_successor_refresh_token"]?.jsonPrimitive?.content,
            m5PendingMaterializationRequest = value["m5_pending_materialization_request"]?.jsonPrimitive?.content,
            publicAccessPendingRegistrationId = value["public_access_pending_registration_id"]?.jsonPrimitive?.content,
            publicAccessPendingCanonicalRequest = value["public_access_pending_canonical_request"]?.jsonPrimitive?.content,
            publicAccessPendingSuccessorRefreshToken = value["public_access_pending_successor_refresh_token"]?.jsonPrimitive?.content,
        )
    }

    fun encode(value: SessionCredentialEnvelope): ByteArray = buildJsonObject {
        put("access_token", value.accessToken)
        value.refreshToken?.let { put("refresh_token", it) }
        put("generation", value.generation)
        put("refresh_pending", value.refreshPending)
        value.bindingCommitId?.let { put("binding_commit_id", it) }
        value.sessionId?.let { put("session_id", it) }
        value.sessionFamilyId?.let { put("session_family_id", it) }
        value.sessionGeneration?.let { put("session_generation", it) }
        value.m5PendingRotationId?.let { put("m5_pending_rotation_id", it) }
        value.m5PendingRotationRequest?.let { put("m5_pending_rotation_request", it) }
        value.m5PendingSuccessorRefreshToken?.let { put("m5_pending_successor_refresh_token", it) }
        value.m5PendingExchangeId?.let { put("m5_pending_exchange_id", it) }
        value.m5PendingExchangeRequest?.let { put("m5_pending_exchange_request", it) }
        value.m5PendingExchangeSuccessorRefreshToken?.let { put("m5_pending_exchange_successor_refresh_token", it) }
        value.m5PendingMaterializationRequest?.let { put("m5_pending_materialization_request", it) }
        value.publicAccessPendingRegistrationId?.let { put("public_access_pending_registration_id", it) }
        value.publicAccessPendingCanonicalRequest?.let { put("public_access_pending_canonical_request", it) }
        value.publicAccessPendingSuccessorRefreshToken?.let { put("public_access_pending_successor_refresh_token", it) }
    }.toString().toByteArray(StandardCharsets.UTF_8)

    private fun JsonObject.requiredString(name: String): String =
        requireNotNull(this[name]) { "CREDENTIAL_ENVELOPE_INVALID" }.jsonPrimitive.content
}

/** Returns only the bearer access token and wipes the decrypted envelope buffer immediately. */
suspend fun CredentialStore.readAccessToken(profileId: ServerProfileId): ByteArray? {
    val material = read(profileId) ?: return null
    return try {
        SessionCredentialEnvelopeCodec.decode(material).accessToken.toByteArray(StandardCharsets.UTF_8)
    } finally {
        material.fill(0)
    }
}

/**
 * Encrypts credential material with a non-exportable Android Keystore AES key before persisting it
 * in private SharedPreferences. The persisted cipher text is unusable without the Keystore key.
 */
class AndroidKeystoreCredentialStore(context: Context) : CredentialStore {
    private val applicationContext = context.applicationContext
    private val preferences = applicationContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    override suspend fun read(profileId: ServerProfileId): ByteArray? {
        val encoded = preferences.getString(preferenceKey(profileId), null) ?: return null
        return decrypt(encoded)
    }

    @SuppressLint("UseKtx") // The KTX edit helper discards commit()'s failure signal.
    override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
        require(material.isNotEmpty()) { "Credential material must not be empty." }
        val cipher = cipher(Cipher.ENCRYPT_MODE)
        val encrypted = cipher.doFinal(material)
        val packed = cipher.iv + encrypted
        check(preferences.edit().putString(preferenceKey(profileId), Base64.encodeToString(packed, Base64.NO_WRAP)).commit()) {
            "Credential material could not be persisted."
        }
    }

    @SuppressLint("UseKtx") // Clearing credentials must also surface durable-write failure.
    override suspend fun clear(profileId: ServerProfileId) {
        check(preferences.edit().remove(preferenceKey(profileId)).commit()) {
            "Credential material could not be cleared."
        }
    }

    override suspend fun hasPublicAccessPendingRegistration(): Boolean {
        for (stored in preferences.all.values) {
            val encoded = stored as? String
                ?: throw IllegalStateException("Credential material is malformed.")
            val material = decrypt(encoded)
            try {
                if (
                    SessionCredentialEnvelopeCodec.decode(material)
                        .publicAccessPendingRegistrationId != null
                ) return true
            } finally {
                material.fill(0)
            }
        }
        return false
    }

    private fun decrypt(encoded: String): ByteArray {
        val packed = Base64.decode(encoded, Base64.NO_WRAP)
        require(packed.size > IV_SIZE_BYTES) { "Stored credential material is malformed." }
        val iv = packed.copyOfRange(0, IV_SIZE_BYTES)
        val cipherText = packed.copyOfRange(IV_SIZE_BYTES, packed.size)
        return cipher(Cipher.DECRYPT_MODE, iv).doFinal(cipherText)
    }

    private fun cipher(mode: Int, iv: ByteArray? = null): Cipher =
        Cipher.getInstance(TRANSFORMATION).apply {
            if (iv == null) {
                init(mode, key())
            } else {
                init(mode, key(), GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
            }
        }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KEY_ALGORITHM, ANDROID_KEYSTORE).apply {
            init(
                android.security.keystore.KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    android.security.keystore.KeyProperties.PURPOSE_ENCRYPT or
                        android.security.keystore.KeyProperties.PURPOSE_DECRYPT,
                ).setBlockModes(android.security.keystore.KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build(),
            )
        }.generateKey()
    }

    private fun preferenceKey(profileId: ServerProfileId): String =
        MessageDigest.getInstance("SHA-256")
            .digest(profileId.value.toByteArray(StandardCharsets.UTF_8))
            .joinToString(separator = "") { byte -> "%02x".format(byte.toInt() and 0xff) }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val KEY_ALIAS = "autplay.credential.master.v1"
        const val KEY_ALGORITHM = "AES"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val PREFERENCES_NAME = "autplay_keystore_credentials"
        const val IV_SIZE_BYTES = 12
        const val GCM_TAG_LENGTH_BITS = 128
    }
}
