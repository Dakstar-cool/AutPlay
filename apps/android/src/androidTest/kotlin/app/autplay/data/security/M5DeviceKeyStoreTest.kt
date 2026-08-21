package app.autplay.data.security

import androidx.test.ext.junit.runners.AndroidJUnit4
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class M5DeviceKeyStoreTest {
    @Test fun api26KeyIsNonExportableAndSignsFixedWidthP1363Proof() {
        val alias = "autplay.m5.test.${UUID.randomUUID()}"
        val store = AndroidM5DeviceKeyStore()
        try {
            store.ensure(alias)
            val proof = store.signP1363(alias, "AutPlay test v1\n", ByteArray(32) { 7 })
            assertEquals(64, proof.size)
            val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(store.publicKeySpki(alias)))
            val verified = Signature.getInstance("SHA256withECDSA").run {
                initVerify(key)
                update("AutPlay test v1\n".toByteArray(Charsets.US_ASCII))
                update(ByteArray(32) { 7 })
                verify(p1363ToDer(proof))
            }
            assertTrue(verified)
            assertEquals(64, store.publicKeyThumbprintSha256(alias).length)
        } finally {
            store.delete(alias)
        }
    }

    private fun p1363ToDer(value: ByteArray): ByteArray {
        fun integer(offset: Int): ByteArray {
            val stripped = value.copyOfRange(offset, offset + 32).dropWhile { it == 0.toByte() }.toByteArray()
            val raw = if (stripped.isEmpty()) byteArrayOf(0) else stripped
            return if (raw[0].toInt() and 0x80 != 0) byteArrayOf(0) + raw else raw
        }
        val r = integer(0); val s = integer(32)
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 0x02, r.size.toByte()) + r + byteArrayOf(0x02, s.size.toByte()) + s
    }
}
