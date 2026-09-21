package app.autplay.data.settings

/** Device-local setup evidence for a newly registered account, never a credential or file locator. */
data class AccountRecoverySetupCheckpoint(
    val serverInstanceId: String,
    val accountId: String,
    val bindingCommitId: String,
    val savedCodeGeneration: Long? = null,
    val savedDocumentSha256: String? = null,
) {
    init {
        require(UUID_PATTERN.matches(serverInstanceId) && UUID_PATTERN.matches(accountId) && UUID_PATTERN.matches(bindingCommitId))
        require((savedCodeGeneration == null) == (savedDocumentSha256 == null))
        require(savedCodeGeneration == null || savedCodeGeneration > 0)
        require(savedDocumentSha256 == null || SHA256_PATTERN.matches(savedDocumentSha256))
    }

    fun matches(settings: NonSecretSettings): Boolean =
        settings.activeServerProfileId != null && settings.deviceId != null &&
            settings.activeUserId?.value == accountId &&
            settings.m5Binding?.serverInstanceId == serverInstanceId &&
            settings.m5Binding.bindingCommitId == bindingCommitId

    private companion object {
        val UUID_PATTERN = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        val SHA256_PATTERN = Regex("^[0-9a-f]{64}$")
    }
}
