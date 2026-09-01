package app.autplay.application.publicaccess

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.domain.ServerProfileId
import java.time.Instant
import java.util.UUID
import org.junit.Assert.assertEquals
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Test

class AccountRegistrationRuntimeTest {
    @Test fun m5ReservationWinsBeforePublicRedeemWithoutCallingServer() = runBlocking {
        val store = RecordingStore()
        val firstBindGate = FirstBindCeremonyGate()
        assertTrue(firstBindGate.reserve(FirstBindCeremonyOwner.M5))
        val runtime = AccountRegistrationRuntime(
            store,
            AccountRegistrationPort { error("must not call") },
            AccountRegistrationBindingCommitter { _, _, _, _ -> error("must not call") },
            ActiveProfileGate { false },
            object : AccountRegistrationDiscoveryGate {
                override suspend fun verify(invitation: AccountInvitation) = error("must not call")
                override fun takeVerifiedContext() = null
            },
            firstBindGate,
        )

        val result = runtime.redeem(
            AccountRegistrationProof.create(
                AccountInvitationParser.parseQr(document()),
                "Pixel",
                "0.3.0",
                FakeKeys(),
            ),
        )

        assertTrue(result.isFailure)
        assertTrue(store.writes.isEmpty())
    }
    @Test fun rejectsRegistrationWhenAnotherProfileIsActiveBeforeWritingSecrets() = runBlocking {
        val store = RecordingStore()
        val runtime = AccountRegistrationRuntime(store, AccountRegistrationPort { error("must not call") }, AccountRegistrationBindingCommitter { _, _, _, _ -> true }, ActiveProfileGate { true }, object : AccountRegistrationDiscoveryGate { override suspend fun verify(invitation: AccountInvitation) = error("must not call"); override fun takeVerifiedContext() = null })
        val result = runtime.redeem(AccountRegistrationProof.create(AccountInvitationParser.parseQr(document()), "Pixel", "0.3.0", FakeKeys()))
        assertTrue(result.isFailure)
        assertTrue(store.writes.isEmpty())
    }
    @Test fun rejectsRegistrationUntilSignedDiscoveryAndExplicitTrustHavePassed() = runBlocking {
        val store = RecordingStore()
        val runtime = AccountRegistrationRuntime(store, AccountRegistrationPort { error("must not call") }, AccountRegistrationBindingCommitter { _, _, _, _ -> true }, ActiveProfileGate { false }, object : AccountRegistrationDiscoveryGate { override suspend fun verify(invitation: AccountInvitation) = false; override fun takeVerifiedContext() = null })
        val result = runtime.redeem(AccountRegistrationProof.create(AccountInvitationParser.parseQr(document()), "Pixel", "0.3.0", FakeKeys()))
        assertTrue(result.isFailure)
        assertTrue(store.writes.isEmpty())
    }

    @Test fun writesEncryptedPendingBeforeIoAndKeepsAccessTokenAliveThroughBindingCommit() = runBlocking {
        val store = RecordingStore()
        val keys = FakeKeys()
        val invitation = AccountInvitationParser.parseQr(document())
        val signed = AccountRegistrationProof.create(invitation, "Pixel", "0.3.0", keys)
        val responseToken = "x".repeat(64).toByteArray()
        val response = AccountRegistrationResponse(
            signed.registration.registrationId,
            signed.registration.bindingCommitId,
            invitation.serverInstanceId,
            UUID.randomUUID().toString(),
            invitation.accountDisplayName,
            UUID.randomUUID().toString(),
            UUID.randomUUID().toString(),
            Instant.now().plusSeconds(3_600).toEpochMilli(),
            Instant.now().plusSeconds(3_900).toEpochMilli(),
            Instant.now().plusSeconds(300).toEpochMilli(),
            responseToken,
        )
        val context = VerifiedAccountRegistrationContext(
            PairingFlowSnapshot(
                UUID.randomUUID().toString(),
                invitation.apiOrigin,
                invitation.streamOrigin,
                ServerProfileId(invitation.serverInstanceId),
                invitation.serverInstanceId,
                invitation.identityEpoch,
                invitation.identityThumbprintSha256,
                null,
                null,
                null,
                null,
                null,
            ),
            ByteArray(91) { 9 },
        )
        val gate = object : AccountRegistrationDiscoveryGate {
            override suspend fun verify(invitation: AccountInvitation) = true
            override fun takeVerifiedContext() = context
        }
        val runtime = AccountRegistrationRuntime(
            store,
            AccountRegistrationPort {
                assertEquals(1, store.writes.size)
                assertTrue(SessionCredentialEnvelopeCodec.decode(store.writes.single()).refreshPending)
                Result.success(response)
            },
            AccountRegistrationBindingCommitter { _, _, seen, _ ->
                assertTrue(seen.accessToken.all { it.toInt() != 0 })
                val staged = SessionCredentialEnvelopeCodec.decode(store.writes.last())
                assertTrue(staged.refreshPending)
                assertEquals(
                    signed.registration.registrationId,
                    staged.publicAccessPendingRegistrationId,
                )
                true
            },
            ActiveProfileGate { false },
            gate,
        )

        assertTrue(runtime.redeem(signed).isSuccess)
        assertTrue(responseToken.all { it.toInt() == 0 })
    }

    @Test fun failedBindingKeepsExactRegistrationEvidenceAfterServerCommit() = runBlocking {
        val store = RecordingStore()
        val keys = FakeKeys()
        val invitation = AccountInvitationParser.parseQr(document())
        val signed = AccountRegistrationProof.create(invitation, "Pixel", "0.3.0", keys)
        val response = AccountRegistrationResponse(
            signed.registration.registrationId,
            signed.registration.bindingCommitId,
            invitation.serverInstanceId,
            UUID.randomUUID().toString(),
            invitation.accountDisplayName,
            UUID.randomUUID().toString(),
            UUID.randomUUID().toString(),
            Instant.now().plusSeconds(3_600).toEpochMilli(),
            Instant.now().plusSeconds(3_900).toEpochMilli(),
            Instant.now().plusSeconds(300).toEpochMilli(),
            "x".repeat(64).toByteArray(),
        )
        val context = VerifiedAccountRegistrationContext(
            PairingFlowSnapshot(
                UUID.randomUUID().toString(), invitation.apiOrigin, invitation.streamOrigin,
                ServerProfileId(invitation.serverInstanceId), invitation.serverInstanceId,
                invitation.identityEpoch, invitation.identityThumbprintSha256,
                null, null, null, null, null,
            ),
            ByteArray(91) { 9 },
        )
        val runtime = AccountRegistrationRuntime(
            store,
            AccountRegistrationPort { Result.success(response) },
            AccountRegistrationBindingCommitter { _, _, _, _ -> false },
            ActiveProfileGate { false },
            object : AccountRegistrationDiscoveryGate {
                override suspend fun verify(invitation: AccountInvitation) = true
                override fun takeVerifiedContext() = context
            },
        )

        assertTrue(runtime.redeem(signed).isFailure)
        val retained = SessionCredentialEnvelopeCodec.decode(store.writes.last())
        assertTrue(retained.refreshPending)
        assertEquals(response.registrationId, retained.publicAccessPendingRegistrationId)
        assertTrue(retained.publicAccessPendingCanonicalRequest != null)
        assertTrue(retained.publicAccessPendingSuccessorRefreshToken != null)
    }

    @Test fun reimportedExactInvitationFindsUncertainEncryptedRequestButChangedInvitationConflicts() = runBlocking {
        val store = RecoverableStore()
        val keys = FakeKeys()
        val first = AccountInvitationParser.parseQr(document())
        val context = VerifiedAccountRegistrationContext(
            PairingFlowSnapshot(
                UUID.randomUUID().toString(), first.apiOrigin, first.streamOrigin,
                ServerProfileId(first.serverInstanceId), first.serverInstanceId, first.identityEpoch,
                first.identityThumbprintSha256, null, null, null, null, null,
            ),
            ByteArray(91) { 9 },
        )
        val runtime = AccountRegistrationRuntime(
            store,
            AccountRegistrationPort { Result.failure(IllegalStateException("lost response")) },
            AccountRegistrationBindingCommitter { _, _, _, _ -> true },
            ActiveProfileGate { false },
            object : AccountRegistrationDiscoveryGate {
                override suspend fun verify(invitation: AccountInvitation) = true
                override fun takeVerifiedContext() = context
            },
        )
        assertTrue(runtime.redeem(AccountRegistrationProof.create(first, "Pixel", "0.3.0", keys)).isFailure)
        val exact = AccountInvitationParser.parseQr(document())
        val changed = AccountInvitationParser.parseQr(document().replace("Friend", "Other"))
        try {
            assertEquals(true, runtime.pendingMatches(ServerProfileId(exact.serverInstanceId), exact))
            assertEquals(false, runtime.pendingMatches(ServerProfileId(changed.serverInstanceId), changed))
        } finally {
            exact.close()
            changed.close()
        }
    }
    private class RecordingStore : CredentialStore {
        val writes = mutableListOf<ByteArray>()
        override suspend fun read(profileId: ServerProfileId) = null
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { writes += material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) = Unit
    }
    private class RecoverableStore : CredentialStore {
        private var value: ByteArray? = null
        override suspend fun read(profileId: ServerProfileId) = value?.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) {
            value?.fill(0)
            value = material.copyOf()
        }
        override suspend fun clear(profileId: ServerProfileId) {
            value?.fill(0)
            value = null
        }
    }
    private class FakeKeys : app.autplay.data.security.M5DeviceKeyStore {
        override fun ensure(alias: String) = Unit
        override fun publicKeySpki(alias: String) = ByteArray(91) { 7 }
        override fun publicKeyThumbprintSha256(alias: String) = "b".repeat(64)
        override fun signP1363(alias: String, domainSeparator: String, payloadSha256: ByteArray) = ByteArray(64) { 1 }
        override fun delete(alias: String) = Unit
    }
    private fun document() = """{"contract_version":"v1","schema_version":1,"invitation_id":"10000000-0000-4000-8000-000000000001","server_instance_id":"10000000-0000-4000-8000-000000000002","identity_epoch":1,"identity_thumbprint_sha256":"${"a".repeat(64)}","api_origin":"https://api.example","stream_origin":"https://stream.example","account_display_name":"Friend","account_role":"USER","issued_at":"2026-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","invitation_secret":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY","secret_handling":"DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"}"""
}
