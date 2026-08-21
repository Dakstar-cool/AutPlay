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

    @Test fun m5BindingCommitAndLineageRoundTripWithoutChangingLegacyFields() {
        val encoded = SessionCredentialEnvelopeCodec.encode(
            SessionCredentialEnvelope(
                accessToken = "access-value",
                refreshToken = "refresh-value",
                generation = 3,
                bindingCommitId = "10000000-0000-4000-8000-000000000001",
                sessionId = "10000000-0000-4000-8000-000000000002",
                sessionFamilyId = "10000000-0000-4000-8000-000000000003",
                sessionGeneration = 4,
            ),
        )

        val decoded = SessionCredentialEnvelopeCodec.decode(encoded)

        assertEquals("access-value", decoded.accessToken)
        assertEquals(3, decoded.generation)
        assertEquals("10000000-0000-4000-8000-000000000001", decoded.bindingCommitId)
        assertEquals(4L, decoded.sessionGeneration)
    }

    @Test fun m5PendingRotationRoundTripsOnlyAsCompleteEncryptedEnvelopeState() {
        val encoded = SessionCredentialEnvelopeCodec.encode(
            SessionCredentialEnvelope(
                accessToken = "access-value", refreshToken = "a".repeat(43), generation = 3,
                refreshPending = true,
                bindingCommitId = "10000000-0000-4000-8000-000000000001",
                sessionId = "10000000-0000-4000-8000-000000000002",
                sessionFamilyId = "10000000-0000-4000-8000-000000000003", sessionGeneration = 4,
                m5PendingRotationId = "10000000-0000-4000-8000-000000000004",
                m5PendingRotationRequest = "{\"request_sha256\":\"${"a".repeat(64)}\"}",
                m5PendingSuccessorRefreshToken = "b".repeat(43),
            ),
        )
        val decoded = SessionCredentialEnvelopeCodec.decode(encoded)
        assertEquals("10000000-0000-4000-8000-000000000004", decoded.m5PendingRotationId)
        assertEquals("b".repeat(43), decoded.m5PendingSuccessorRefreshToken)
    }

    @Test fun m5PendingMaterializationConsentRoundTripsWithActiveLineage() {
        val request = "{\"selected_local_change_ids\":[\"10000000-0000-4000-8000-000000000005\"]}"
        val encoded = SessionCredentialEnvelopeCodec.encode(
            SessionCredentialEnvelope(
                accessToken = "access-value",
                refreshToken = "refresh-value",
                generation = 3,
                bindingCommitId = "10000000-0000-4000-8000-000000000001",
                sessionId = "10000000-0000-4000-8000-000000000002",
                sessionFamilyId = "10000000-0000-4000-8000-000000000003",
                sessionGeneration = 4,
                m5PendingMaterializationRequest = request,
            ),
        )

        val decoded = SessionCredentialEnvelopeCodec.decode(encoded)

        assertEquals(request, decoded.m5PendingMaterializationRequest)
    }

    private class InMemoryStore(private val material: ByteArray) : CredentialStore {
        override suspend fun read(profileId: ServerProfileId): ByteArray = material
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
}
