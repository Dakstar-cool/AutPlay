package app.autplay.application.profilepairing

import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import java.security.KeyFactory
import java.security.interfaces.ECPublicKey
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.security.MessageDigest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonElement
import org.erdtman.jcs.JsonCanonicalizer

/** Exact S1B RFC8785/ES256-P1363 proof convention shared by all admission operations. */
internal object AdmissionProof {
    const val REQUEST_DOMAIN = "autplay:s1b:admission-request:v1\n"
    const val POLL_DOMAIN = "autplay:s1b:admission-poll:v1\n"
    const val RECOVERY_DOMAIN = "autplay:s1b:admission-recovery:v1\n"
    const val EXCHANGE_DOMAIN = "autplay:s1b:admission-exchange:v1\n"
    const val REENROLLMENT_DOMAIN = "autplay:s1b:trusted-reenrollment:v1\n"

    fun signedJson(keys: M5DeviceKeyStore, alias: String, domain: String, fields: Map<String, JsonElement>): SignedJson {
        val unsigned = JsonObject(fields)
        val canonical = JsonCanonicalizer(unsigned.toString()).encodedString.toByteArray(Charsets.UTF_8)
        val signed = M5RequestSigner.sign(keys, alias, domain, canonical)
        val complete = JsonObject(fields + ("proof_b64url" to JsonPrimitive(signed.signatureB64Url)))
        return SignedJson(JsonCanonicalizer(complete.toString()).encodedString, signed.requestSha256, signed.signatureB64Url)
    }

    fun p256Jwk(spki: ByteArray): Map<String, JsonPrimitive> {
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as ECPublicKey
        fun coordinate(value: java.math.BigInteger): String {
            val raw = value.toByteArray().let { bytes -> if (bytes.size == 33 && bytes[0] == 0.toByte()) bytes.copyOfRange(1, 33) else bytes }
            require(raw.size <= 32)
            return Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32 - raw.size) + raw)
        }
        return mapOf("kty" to JsonPrimitive("EC"), "crv" to JsonPrimitive("P-256"), "x" to JsonPrimitive(coordinate(key.w.affineX)), "y" to JsonPrimitive(coordinate(key.w.affineY)))
    }

    /** RFC8785/SHA-256 rejection-sampling value specified by the accepted S1A contract. */
    fun sasDecimal12(checkpoint: AdmissionCheckpoint): String {
        var counter = 0L
        while (true) {
            val tuple = JsonArray(listOf(
                JsonPrimitive("autplay:s1a:admission-sas:v1"), JsonPrimitive(checkpoint.serverInstanceId),
                JsonPrimitive(checkpoint.requestId), JsonPrimitive(checkpoint.requestSha256),
                JsonPrimitive(checkpoint.deviceKeyThumbprintSha256), JsonPrimitive(counter),
            ))
            val canonical = JsonCanonicalizer(tuple.toString()).encodedString.toByteArray(Charsets.UTF_8)
            val digest = MessageDigest.getInstance("SHA-256").digest(canonical)
            val value = ((digest[0].toLong() and 0xffL) shl 32) or ((digest[1].toLong() and 0xffL) shl 24) or
                ((digest[2].toLong() and 0xffL) shl 16) or ((digest[3].toLong() and 0xffL) shl 8) or (digest[4].toLong() and 0xffL)
            digest.fill(0)
            if (value < 1_000_000_000_000L) return value.toString().padStart(12, '0')
            counter++
        }
    }
}

internal data class SignedJson(val json: String, val sha256: String, val proofB64Url: String)
