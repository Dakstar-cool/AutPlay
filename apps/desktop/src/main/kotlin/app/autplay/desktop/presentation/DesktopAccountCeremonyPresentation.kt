package app.autplay.desktop.presentation

import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import app.autplay.desktop.account.DesktopSelfPairingController
import app.autplay.desktop.account.DesktopSelfPairingViewState
import app.autplay.desktop.account.DesktopServerRecoveryCode
import app.autplay.desktop.account.DesktopServerRecoveryController
import app.autplay.ui.desktop.*
import java.nio.CharBuffer
import java.nio.charset.StandardCharsets
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean

/** Decorates the mandatory remaining sink of DesktopAccountRuntimeActions. */
public class DesktopAccountCeremonyActions(
    private val self: DesktopSelfPairingController,
    private val recovery: DesktopServerRecoveryController,
    private val current: () -> DesktopAccountWorkspaceState,
    private val next: DesktopAccountActionSink,
    private val onSettlement: (DesktopAccountScope, DesktopAccountAction, DesktopAccountUiSettlement) -> Unit,
) : DesktopAccountActionSink, AutoCloseable {
    private val closed = AtomicBoolean(false)

    override fun dispatch(intent: DesktopAccountIntent): Unit {
        try {
            val state = current()
            requireCurrent(!closed.get() && state.scope == intent.scope && state.permits.any { it === intent.permit } &&
                intent.command.action == intent.permit.action && intent.command.targetId == intent.permit.targetId)
            val command = intent.command
            val operation: CompletionStage<Unit>? = when (command) {
                is DesktopAccountCommand.RecoveryPreview -> command.accountId.consume { account ->
                    command.code.consume { code ->
                        val normalized = DesktopServerRecoveryCode.normalize(code.concatToString())
                        try { recovery.previewCode(account.concatToString(), normalized, command.cancelDeletion) }
                        finally { normalized.fill(0) }
                    }
                }
                is DesktopAccountCommand.Secret -> if (command.action == DesktopAccountAction.ClaimSelf)
                    command.input.consume { characters ->
                        val encoded = StandardCharsets.UTF_8.encode(CharBuffer.wrap(characters))
                        val bytes = ByteArray(encoded.remaining()); encoded.get(bytes)
                        try { self.claimTextOrFile(bytes) } finally {
                            bytes.fill(0); if (encoded.hasArray()) encoded.array().fill(0)
                        }
                    } else null
                is DesktopAccountCommand.Invoke -> selfCommand(command, state.selfPairing)
                else -> null
            }
            if (operation == null) next.dispatch(intent)
            else operation.whenComplete { _, failure ->
                onSettlement(intent.scope, command.action, if (failure == null) DesktopAccountUiSettlement.Finished
                    else DesktopAccountUiSettlement.Failed(ceremonyFailure(failure)))
            }
        } catch (failure: Exception) {
            onSettlement(intent.scope, intent.command.action, DesktopAccountUiSettlement.Failed(ceremonyFailure(failure)))
        } finally {
            when (val command = intent.command) {
                is DesktopAccountCommand.RecoveryPreview -> command.clear()
                is DesktopAccountCommand.PinnedTransport -> command.clear()
                is DesktopAccountCommand.Secret -> command.clear()
                is DesktopAccountCommand.CreatePortable -> command.password.clear()
                is DesktopAccountCommand.Discover -> command.enteredOrigin.clear()
                else -> Unit
            }
        }
    }

    private fun selfCommand(command: DesktopAccountCommand.Invoke, displayed: DesktopSelfPairingView): CompletionStage<Unit>? =
        when (command.action) {
            DesktopAccountAction.StartSelf -> self.startSource()
            DesktopAccountAction.ApproveSelf -> {
                requireCurrent(displayed.role == DesktopSelfRole.Source && displayed.ceremonyId == command.targetId &&
                    displayed.sas != null && displayed.sas == self.state.value.comparisonCode)
                self.decideSource("APPROVE", displayed.sas)
            }
            DesktopAccountAction.ExchangeSelf -> {
                requireCurrent(displayed.role == DesktopSelfRole.Recipient && displayed.ceremonyId == command.targetId &&
                    displayed.exchangeConfirmationRequired && displayed.accountLabel != null &&
                    displayed.accountLabel == self.state.value.accountLabel && self.state.value.state == "APPROVED")
                self.exchangeRecipient(confirmedAccount = true)
            }
            DesktopAccountAction.ResumeSelf -> {
                requireCurrent(displayed.ceremonyId == command.targetId)
                if (displayed.role == DesktopSelfRole.Source) self.refreshSourceStatus() else self.pollRecipient()
            }
            DesktopAccountAction.CancelSelf -> if (displayed.role == DesktopSelfRole.Source) {
                requireCurrent(displayed.ceremonyId == command.targetId); self.decideSource("CANCEL")
            } else null
            else -> null
        }

    override fun close(): Unit { closed.set(true) }
}

/** Caller supplies the actual retained ceremony target/role; neither is inferred from a label. */
public fun desktopSelfPairingView(state: DesktopSelfPairingViewState, role: DesktopSelfRole,
    ceremonyTargetId: String?): DesktopSelfPairingView = DesktopSelfPairingView(
    condition = when (state.state) {
        "IDLE" -> DesktopAccountCondition.Empty
        "OPEN", "CLAIMED", "APPROVED" -> DesktopAccountCondition.Pending
        "EXCHANGED" -> DesktopAccountCondition.Ready
        "REJECTED", "CANCELLED", "EXPIRED" -> DesktopAccountCondition.Attention
        else -> DesktopAccountCondition.Unavailable
    }, role = role, ceremonyId = ceremonyTargetId, sas = state.comparisonCode,
    recipientLabel = state.deviceName, sourceApproved = when (state.state) {
        "APPROVED", "EXCHANGED" -> true
        "OPEN", "CLAIMED" -> false
        else -> null
    }, expiresLabel = null, accountLabel = state.accountLabel,
    exchangeConfirmationRequired = role == DesktopSelfRole.Recipient && state.state == "APPROVED")

/** Call only for the explicit source display flow; never during rendering, logging or diagnostics. */
public fun desktopSelfQrForPresentation(self: DesktopSelfPairingController,
    scope: DesktopAccountScope): DesktopAccountSensitiveOutput = self.qrForExplicitDisplay().use { output ->
    val bytes = output.copy()
    try {
        val decoded = StandardCharsets.UTF_8.decode(java.nio.ByteBuffer.wrap(bytes))
        val characters = CharArray(decoded.remaining()); decoded.get(characters)
        try { DesktopAccountSensitiveOutput(scope, DesktopAccountSensitiveOutputKind.SelfQr, characters) }
        finally { characters.fill('\u0000'); if (decoded.hasArray()) decoded.array().fill('\u0000') }
    } finally { bytes.fill(0) }
}

private fun requireCurrent(value: Boolean): Unit {
    if (!value) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
private fun ceremonyFailure(failure: Throwable): AccountControlFailure {
    var current = failure
    repeat(8) {
        if (current is AccountControlException) return (current as AccountControlException).code
        current = current.cause ?: return AccountControlFailure.TRANSPORT_UNAVAILABLE
    }
    return AccountControlFailure.TRANSPORT_UNAVAILABLE
}
