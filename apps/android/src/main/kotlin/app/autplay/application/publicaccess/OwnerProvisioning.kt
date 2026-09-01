package app.autplay.application.publicaccess

/** Secret-free owner management projection; authorization is server-enforced, never inferred by UI. */
data class AccountInvitationView(val invitationId: String, val displayName: String, val role: String, val state: String, val issuedAt: String, val expiresAt: String, val terminalAt: String?, val invitedUserId: String?)
data class ProvisionedAccountView(val userId: String, val provisioningInvitationId: String, val displayName: String, val role: String, val status: String, val createdAt: String, val disabledAt: String?)

interface OwnerProvisioningPort {
    suspend fun create(displayName: String, expiresInSeconds: Int, operationId: String): Result<AccountInvitationCreateResult>
    suspend fun listInvitations(limit: Int = 50, cursor: String? = null): Result<List<AccountInvitationView>>
    suspend fun cancelInvitation(invitationId: String, operationId: String, reasonCode: String): Result<Unit>
    suspend fun listAccounts(limit: Int = 50, cursor: String? = null): Result<List<ProvisionedAccountView>>
    suspend fun disableAccount(userId: String, operationId: String, reasonCode: String): Result<Unit>
}
sealed interface AccountInvitationCreateResult { data class Created(val document: AccountInvitation) : AccountInvitationCreateResult; data class Replayed(val view: AccountInvitationView) : AccountInvitationCreateResult }

/** UI seam: secrets are volatile and secure-window-protected by the caller, pages are secret-free. */
data class OwnerProvisioningUiState(
    val invitations: List<AccountInvitationView> = emptyList(),
    val accounts: List<ProvisionedAccountView> = emptyList(),
    val shownInvitation: AccountInvitation? = null,
    val busy: Boolean = false,
    val available: Boolean = false,
    val errorCode: String? = null,
) : AutoCloseable {
    override fun close() { shownInvitation?.close() }
}
