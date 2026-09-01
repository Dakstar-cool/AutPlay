package app.autplay.application.publicaccess

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OwnerProvisioningCoordinatorTest {
    @Test
    fun ownerLifecycleIsBoundedAndShownOnceSecretIsClearedOnDismiss() = runBlocking {
        val port = FakeOwnerPort()
        val coordinator = OwnerProvisioningCoordinator(CoroutineScope(coroutineContext), port)

        coordinator.refresh()
        yield()
        assertTrue(coordinator.state.value.available)

        coordinator.create("Friend", 1_800)
        yield()
        val shown = requireNotNull(coordinator.state.value.shownInvitation)
        assertEquals(32, shown.secret.count { it.toInt() != 0 })
        coordinator.dismissShownInvitation()
        assertNull(coordinator.state.value.shownInvitation)
        assertTrue(shown.secret.all { it.toInt() == 0 })

        coordinator.cancelInvitation(INVITATION_ID)
        yield()
        coordinator.disableAccount(USER_ID)
        yield()
        assertEquals(listOf(INVITATION_ID), port.cancelled)
        assertEquals(listOf(USER_ID), port.disabled)
    }

    private class FakeOwnerPort : OwnerProvisioningPort {
        val cancelled = mutableListOf<String>()
        val disabled = mutableListOf<String>()

        override suspend fun create(
            displayName: String,
            expiresInSeconds: Int,
            operationId: String,
        ) = Result.success(
            AccountInvitationCreateResult.Created(
                AccountInvitationParser.parseQr(document()),
            ),
        )

        override suspend fun listInvitations(limit: Int, cursor: String?) = Result.success(emptyList<AccountInvitationView>())

        override suspend fun cancelInvitation(invitationId: String, operationId: String, reasonCode: String): Result<Unit> {
            cancelled += invitationId
            return Result.success(Unit)
        }

        override suspend fun listAccounts(limit: Int, cursor: String?) = Result.success(emptyList<ProvisionedAccountView>())

        override suspend fun disableAccount(userId: String, operationId: String, reasonCode: String): Result<Unit> {
            disabled += userId
            return Result.success(Unit)
        }
    }

    private companion object {
        const val INVITATION_ID = "10000000-0000-4000-8000-000000000001"
        const val USER_ID = "10000000-0000-4000-8000-000000000003"
        fun document() =
            """{"contract_version":"v1","schema_version":1,"invitation_id":"$INVITATION_ID","server_instance_id":"10000000-0000-4000-8000-000000000002","identity_epoch":1,"identity_thumbprint_sha256":"${"a".repeat(64)}","api_origin":"https://api.example","stream_origin":"https://stream.example","account_display_name":"Friend","account_role":"USER","issued_at":"2026-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","invitation_secret":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY","secret_handling":"DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"}"""
    }
}
