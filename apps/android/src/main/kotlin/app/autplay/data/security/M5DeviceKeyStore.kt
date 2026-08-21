package app.autplay.data.security

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature

/** P-256 proof-of-possession boundary. Implementations never expose a private key. */
interface M5DeviceKeyStore {
    fun publicKeySpki(alias: String): ByteArray
    fun publicKeyThumbprintSha256(alias: String): String
    fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray
    fun ensure(alias: String)
    fun delete(alias: String)
}

class AndroidM5DeviceKeyStore : M5DeviceKeyStore {
    override fun ensure(alias: String) {
        if (keyStore().containsAlias(alias)) return
        KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply {
            initialize(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setAlgorithmParameterSpec(java.security.spec.ECGenParameterSpec("secp256r1"))
                .build())
            generateKeyPair()
        }
    }
    override fun publicKeySpki(alias: String): ByteArray = publicKey(alias).encoded.copyOf()
    override fun publicKeyThumbprintSha256(alias: String): String = sha256(publicKeySpki(alias))
    override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray): ByteArray {
        require(payloadSha256.size == 32)
        val signature = Signature.getInstance("SHA256withECDSA").apply { initSign(privateKey(alias)); update(domainSeparator.toByteArray(Charsets.US_ASCII)); update(payloadSha256) }.sign()
        return derToP1363(signature)
    }
    override fun delete(alias: String) { keyStore().deleteEntry(alias) }
    private fun keyStore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    private fun privateKey(alias: String): PrivateKey = keyStore().getKey(alias, null) as? PrivateKey ?: error("M5_DEVICE_KEY_MISSING")
    private fun publicKey(alias: String): PublicKey = keyStore().getCertificate(alias)?.publicKey ?: error("M5_DEVICE_KEY_MISSING")
}

/** Encodes an M5 signed request without retaining the request or signature in ordinary storage. */
object M5RequestSigner {
    fun sign(keyStore: M5DeviceKeyStore, alias: String, domainSeparator: String, canonicalRequestBytes: ByteArray): M5SignedRequest {
        val hash = MessageDigest.getInstance("SHA-256").digest(canonicalRequestBytes)
        return try {
            M5SignedRequest(
                hash.joinToString("") { "%02x".format(it.toInt() and 0xff) },
                java.util.Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(keyStore.signP1363(alias, domainSeparator, hash)),
            )
        } finally { hash.fill(0) }
    }
}
data class M5SignedRequest(val requestSha256: String, val signatureB64Url: String)

private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }

private fun derToP1363(der: ByteArray): ByteArray {
    require(der.size >= 8 && der[0] == 0x30.toByte()) { "M5_SIGNATURE_DER_INVALID" }
    var index = 1
    index += derLength(der, index).second
    require(der[index++] == 0x02.toByte()) { "M5_SIGNATURE_DER_INVALID" }
    val (rLength, rBytes) = derLength(der, index); index += rBytes
    val r = der.copyOfRange(index, index + rLength); index += rLength
    require(der[index++] == 0x02.toByte()) { "M5_SIGNATURE_DER_INVALID" }
    val (sLength, sBytes) = derLength(der, index); index += sBytes
    val s = der.copyOfRange(index, index + sLength)
    return ByteArray(64).also { copyInteger(r, it, 0); copyInteger(s, it, 32) }
}
private fun derLength(bytes: ByteArray, index: Int): Pair<Int, Int> {
    val first = bytes[index].toInt() and 0xff
    if (first < 128) return first to 1
    val count = first and 0x7f; require(count in 1..2 && index + count < bytes.size) { "M5_SIGNATURE_DER_INVALID" }
    var value = 0; repeat(count) { value = (value shl 8) or (bytes[index + it + 1].toInt() and 0xff) }; return value to count + 1
}
private fun copyInteger(source: ByteArray, target: ByteArray, offset: Int) {
    val significant = source.dropWhile { it == 0.toByte() }.toByteArray(); require(significant.size <= 32) { "M5_SIGNATURE_DER_INVALID" }
    significant.copyInto(target, offset + 32 - significant.size)
}
