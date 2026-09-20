package app.autplay.application.accountrecovery

import app.autplay.application.selfpairing.SelfPairingIdentity
import app.autplay.domain.ServerProfileId

/** Captured before opening a destination picker; it contains neither document bytes nor a URI. */
class AccountRecoveryExportTicket internal constructor(
    val serverProfileId: ServerProfileId,
    val accountId: String,
    val bindingCommitId: String,
    val identity: SelfPairingIdentity,
    val codeGeneration: Long,
    val documentSha256: String,
) {
    override fun toString(): String = "AccountRecoveryExportTicket(generation=$codeGeneration, context=<redacted>)"
}
