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
}

/** Versioned access/refresh material encrypted as one profile-scoped Keystore value. */
data class SessionCredentialEnvelope(
    val accessToken: String,
    val refreshToken: String?,
    val generation: Long,
    val refreshPending: Boolean = false,
) {
    init {
        require(accessToken.isNotBlank() && accessToken.length <= MAX_TOKEN_CHARS)
        require(refreshToken == null || (refreshToken.isNotBlank() && refreshToken.length <= MAX_TOKEN_CHARS))
        require(generation >= 0)
    }

    private companion object { const val MAX_TOKEN_CHARS = 4_096 }
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
        )
    }

    fun encode(value: SessionCredentialEnvelope): ByteArray = buildJsonObject {
        put("access_token", value.accessToken)
        value.refreshToken?.let { put("refresh_token", it) }
        put("generation", value.generation)
        put("refresh_pending", value.refreshPending)
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
        val packed = Base64.decode(encoded, Base64.NO_WRAP)
        require(packed.size > IV_SIZE_BYTES) { "Stored credential material is malformed." }
        val iv = packed.copyOfRange(0, IV_SIZE_BYTES)
        val cipherText = packed.copyOfRange(IV_SIZE_BYTES, packed.size)
        return cipher(Cipher.DECRYPT_MODE, iv).doFinal(cipherText)
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
