package app.autplay.application.publicaccess

import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Bounded OWNER-only PA2 orchestration. The server, not this client, decides authorization. */
class OwnerProvisioningCoordinator(
    private val scope: CoroutineScope,
    private val port: OwnerProvisioningPort,
) {
    private val mutable = MutableStateFlow(OwnerProvisioningUiState())
    val state: StateFlow<OwnerProvisioningUiState> = mutable.asStateFlow()

    fun refresh() = scope.launch {
        if (mutable.value.busy) return@launch
        mutable.value = mutable.value.copy(busy = true, errorCode = null)
        loadPages()
    }

    fun create(displayName: String, expiresInSeconds: Int) = scope.launch {
        if (mutable.value.busy) return@launch
        if (displayName.trim().length !in 1..120 || expiresInSeconds !in 60..1_800) {
            mutable.value = mutable.value.copy(errorCode = "ACCOUNT_INVITATION_INPUT_INVALID")
            return@launch
        }
        mutable.value = mutable.value.copy(busy = true, errorCode = null)
        port.create(displayName.trim(), expiresInSeconds, UUID.randomUUID().toString())
            .onSuccess { result ->
                when (result) {
                    is AccountInvitationCreateResult.Created -> {
                        mutable.value.shownInvitation?.close()
                        mutable.value = mutable.value.copy(
                            shownInvitation = result.document,
                            available = true,
                        )
                    }
                    is AccountInvitationCreateResult.Replayed -> Unit
                }
                loadPages(preserveShownInvitation = true)
            }
            .onFailure { fail("OWNER_PROVISIONING_CREATE_FAILED") }
    }

    fun cancelInvitation(invitationId: String) = lifecycle("OWNER_PROVISIONING_CANCEL_FAILED") {
        port.cancelInvitation(invitationId, UUID.randomUUID().toString(), "USER_REQUESTED")
    }

    fun disableAccount(userId: String) = lifecycle("OWNER_PROVISIONING_DISABLE_FAILED") {
        port.disableAccount(userId, UUID.randomUUID().toString(), "USER_REQUESTED")
    }

    fun dismissShownInvitation() {
        mutable.value.shownInvitation?.close()
        mutable.value = mutable.value.copy(shownInvitation = null)
    }

    private fun lifecycle(error: String, action: suspend () -> Result<Unit>) = scope.launch {
        if (mutable.value.busy) return@launch
        mutable.value = mutable.value.copy(busy = true, errorCode = null)
        action().onSuccess { loadPages() }.onFailure { fail(error) }
    }

    private suspend fun loadPages(preserveShownInvitation: Boolean = true) {
        val invitations = port.listInvitations(limit = PAGE_LIMIT)
        val accounts = port.listAccounts(limit = PAGE_LIMIT)
        if (invitations.isSuccess && accounts.isSuccess) {
            mutable.value = mutable.value.copy(
                invitations = invitations.getOrThrow(),
                accounts = accounts.getOrThrow(),
                shownInvitation = if (preserveShownInvitation) mutable.value.shownInvitation else null,
                busy = false,
                available = true,
                errorCode = null,
            )
        } else {
            fail("OWNER_PROVISIONING_UNAVAILABLE")
        }
    }

    private fun fail(code: String) {
        mutable.value = mutable.value.copy(busy = false, errorCode = code)
    }

    private companion object { const val PAGE_LIMIT = 50 }
}
