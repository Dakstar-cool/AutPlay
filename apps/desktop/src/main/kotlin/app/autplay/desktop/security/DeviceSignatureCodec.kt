package app.autplay.desktop.security

import app.autplay.application.securestate.*
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec

/** Representation only: no key creation, private export, new low-S policy or live vectors. */
internal object DeviceSignatureCodec {
    private val prefix: ByteArray = byteArrayOf(0x30,0x59,0x30,0x13,0x06,0x07,0x2a,0x86.toByte(),0x48,0xce.toByte(),0x3d,0x02,0x01,0x06,0x08,0x2a,0x86.toByte(),0x48,0xce.toByte(),0x3d,0x03,0x01,0x07,0x03,0x42,0x00)
    private val order: BigInteger = BigInteger("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
    fun spkiFromPoint(point: ByteArray): ByteArray {
        try {
            secureRequire(point.size == 65 && point[0] == 4.toByte())
            return (prefix + point).also(::validateSpki)
        } finally { point.fill(0) }
    }
    fun validateSpki(spki: ByteArray) {
        try {
            secureRequire(spki.size == 91 && spki.copyOfRange(0, prefix.size).contentEquals(prefix))
            val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }.getParameterSpec(ECParameterSpec::class.java)
            val actual = key.params
            secureRequire(actual.curve == expected.curve && actual.generator == expected.generator && actual.order == expected.order && actual.cofactor == expected.cofactor && key.encoded.contentEquals(spki))
            val p = (expected.curve.field as ECFieldFp).p; val x = key.w.affineX; val y = key.w.affineY
            secureRequire(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p && y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x)).add(expected.curve.b).mod(p))
        } catch (failure: SecureStateFailure) { throw failure }
        catch (_: Exception) { throw SecureStateFailure(SecureStateStatus.CORRUPT) }
    }
    fun derToP1363(der: ByteArray): ByteArray {
        secureRequire(der.size in 8..72 && der[0] == 0x30.toByte() && (der[1].toInt() and 255) == der.size - 2)
        var at = 2
        fun integer(): ByteArray {
            secureRequire(at + 2 <= der.size && der[at++] == 2.toByte())
            val length = der[at++].toInt() and 255
            secureRequire(length in 1..33 && at + length <= der.size)
            val first = der[at].toInt() and 255
            secureRequire(first < 128 && !(length > 1 && first == 0 && (der[at + 1].toInt() and 128) == 0))
            val bytes = der.copyOfRange(at, at + length); at += length
            try {
                val value = BigInteger(1, bytes)
                secureRequire(value.signum() > 0 && value < order)
                val skip = if (bytes.size == 33) 1 else 0
                secureRequire(bytes.size - skip <= 32)
                return ByteArray(32).also { bytes.copyInto(it, 32 - bytes.size + skip, skip) }
            } finally { bytes.fill(0) }
        }
        val r = integer()
        try { val s = integer(); try { secureRequire(at == der.size); return r + s } finally { s.fill(0) } }
        finally { r.fill(0) }
    }
}
