package app.autplay.data.security

import app.autplay.domain.ServerProfileId
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class CredentialStoreTest {
    @Test fun accessTokenReaderSupportsEnvelopeAndWipesDecryptedMaterial() = runBlocking {
        val profile = ServerProfileId("10000000-0000-4000-8000-000000000001")
        val material = SessionCredentialEnvelopeCodec.encode(
            SessionCredentialEnvelope("access-value", "refresh-value", 3),
        )
        val store = InMemoryStore(material)

        val access = store.readAccessToken(profile)

        assertEquals("access-value", access!!.toString(StandardCharsets.UTF_8))
        assertArrayEquals(ByteArray(material.size), material)
        access.fill(0)
    }

    @Test fun legacyRawAccessTokenRemainsReadable() = runBlocking {
        val profile = ServerProfileId("10000000-0000-4000-8000-000000000001")
        val access = InMemoryStore("legacy-access".toByteArray()).readAccessToken(profile)
        assertEquals("legacy-access", access!!.toString(StandardCharsets.UTF_8))
        access.fill(0)
    }

    private class InMemoryStore(private val material: ByteArray) : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray = material
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
}
