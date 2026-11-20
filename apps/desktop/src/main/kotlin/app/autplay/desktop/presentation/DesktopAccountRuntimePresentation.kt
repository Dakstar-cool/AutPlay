package app.autplay.desktop.presentation

import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import app.autplay.desktop.account.AccountExitAction
import app.autplay.desktop.account.AccountRecoveryDestination
import app.autplay.desktop.account.AccountRecoverySource
import app.autplay.desktop.account.DesktopAccountHealth
import app.autplay.desktop.account.DesktopAccountPhase
import app.autplay.desktop.account.DesktopAccountRuntime
import app.autplay.desktop.account.DesktopAccountViewState
import app.autplay.desktop.account.DesktopProfileController
import app.autplay.desktop.account.DesktopProfileViewState
import app.autplay.desktop.account.DesktopRecoveryFiles
import app.autplay.ui.desktop.*
import java.nio.CharBuffer
import java.nio.charset.StandardCharsets
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean

/** Main supplies the actual explicit OS chooser; cancellation returns null. No retained locator. */
public interface DesktopAccountRecoveryPicker {
    public fun chooseRecoveryDestination(): CompletionStage<AccountRecoveryDestination?>
    public fun chooseRecoverySource(): CompletionStage<AccountRecoverySource?>
}

public sealed interface DesktopAccountUiSettlement {
    /** The controller settled. Read its current authority/projection before publishing UI success. */
    public data object Finished : DesktopAccountUiSettlement
    public data object PickerCancelled : DesktopAccountUiSettlement
    public data class Failed(public val code: AccountControlFailure) : DesktopAccountUiSettlement
}

/**
 * Real adapters for APIs already owned by the account writer. Other typed actions go to the
 * mandatory host composition, which consumes transient inputs synchronously. Nothing defaults
 * to fake authentication, assumed consent or a successful unsupported operation.
 */
public class DesktopAccountRuntimeActions(
    private val runtime: DesktopAccountRuntime,
    private val profile: DesktopProfileController,
    private val recoveryFiles: DesktopRecoveryFiles,
    private val picker: DesktopAccountRecoveryPicker,
    private val current: () -> DesktopAccountWorkspaceState,
    private val remaining: DesktopAccountActionSink,
    private val onSettlement: (DesktopAccountScope, DesktopAccountAction, DesktopAccountUiSettlement) -> Unit,
) : DesktopAccountActionSink, AutoCloseable {
    private val closed = AtomicBoolean(false)
    private val gate = Any()
    private var admittedScope: DesktopAccountScope? = null
    private val used: MutableSet<String> = mutableSetOf()

    override fun dispatch(intent: DesktopAccountIntent): Unit {
        try {
            if (!admit(intent)) { settled(intent, DesktopAccountUiSettlement.Failed(AccountControlFailure.CONTEXT_CHANGED)); return }
            val command = intent.command
            val operation: CompletionStage<Unit>? = when (command) {
                is DesktopAccountCommand.CreatePortable -> command.password.consume { runtime.createPortable(command.publicId, it) }
                is DesktopAccountCommand.LoginPortable -> command.password.consume { runtime.loginPortable(command.publicId, it) }
                is DesktopAccountCommand.Secret -> secretCommand(command)
                is DesktopAccountCommand.Invoke -> when (command.action) {
                    DesktopAccountAction.Refresh -> runtime.reload()
                    DesktopAccountAction.RefreshDevices -> profile.listDevicesAndSessions()
                    DesktopAccountAction.ReadPolicy -> profile.loadPrivacy()
                    else -> null
                }
                else -> null
            }
            when {
                operation != null -> operation.whenComplete { _, failure ->
                    settled(intent, if (failure == null) DesktopAccountUiSettlement.Finished
                        else DesktopAccountUiSettlement.Failed(presentationFailure(failure)))
                }
                command is DesktopAccountCommand.Invoke && command.action == DesktopAccountAction.ExportRecoveryFile -> exportRecovery(intent)
                command is DesktopAccountCommand.Invoke && command.action == DesktopAccountAction.ImportRecoveryFile -> importRecovery(intent)
                else -> remaining.dispatch(intent)
            }
        } catch (failure: Exception) {
            settled(intent, DesktopAccountUiSettlement.Failed(presentationFailure(failure)))
        } finally {
            when (val command = intent.command) {
                is DesktopAccountCommand.Secret -> command.clear()
                is DesktopAccountCommand.CreatePortable -> command.password.clear()
                is DesktopAccountCommand.LoginPortable -> command.password.clear()
                is DesktopAccountCommand.Discover -> command.enteredOrigin.clear()
                is DesktopAccountCommand.RecoveryPreview -> command.clear()
                is DesktopAccountCommand.PinnedTransport -> command.clear()
                else -> Unit
            }
        }
    }

    private fun admit(intent: DesktopAccountIntent): Boolean = synchronized(gate) {
        if (!isCurrent(intent)) return@synchronized false
        if (admittedScope != intent.scope) { admittedScope = intent.scope; used.clear() }
        used.add(intent.permit.id)
    }

    private fun isCurrent(intent: DesktopAccountIntent): Boolean {
        val state = current()
        return !closed.get() && state.scope == intent.scope && state.permits.any { it === intent.permit } &&
            intent.command.action == intent.permit.action && intent.command.targetId == intent.permit.targetId &&
            intent.permit.acceptsCommand(intent.command)
    }

    private fun secretCommand(command: DesktopAccountCommand.Secret): CompletionStage<Unit>? = when (command.action) {
        DesktopAccountAction.RegisterPortable, DesktopAccountAction.LoginPortable ->
            command.input.consume { runtime.connectPortable(it, command.action == DesktopAccountAction.RegisterPortable) }
        DesktopAccountAction.SubmitInvitation -> command.input.consume { characters ->
            val encoded = StandardCharsets.UTF_8.encode(CharBuffer.wrap(characters))
            val bytes = ByteArray(encoded.remaining()); encoded.get(bytes)
            try { profile.enrollInvitation(bytes) } finally {
                bytes.fill(0)
                if (encoded.hasArray()) encoded.array().fill(0)
            }
        }
        DesktopAccountAction.LogoutCurrent, DesktopAccountAction.LogoutAll,
        DesktopAccountAction.RevokeDevice, DesktopAccountAction.Disconnect -> {
            val action = when (command.action) {
                DesktopAccountAction.LogoutCurrent -> AccountExitAction.LOGOUT_CURRENT
                DesktopAccountAction.LogoutAll -> AccountExitAction.LOGOUT_ALL
                DesktopAccountAction.RevokeDevice -> AccountExitAction.REVOKE_DEVICE
                else -> AccountExitAction.DISCONNECT_LOCAL
            }
            val target = if (action == AccountExitAction.REVOKE_DEVICE) profile.state.value.devices
                .singleOrNull { it.handle.id == command.targetId }?.handle
                ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED) else null
            command.input.consume { profile.exit(action, confirmed = true, password = it, target = target) }
        }
        else -> null
    }

    private fun exportRecovery(intent: DesktopAccountIntent): Unit {
        picker.chooseRecoveryDestination().whenComplete { destination, failure ->
            when {
                failure != null -> settled(intent, DesktopAccountUiSettlement.Failed(presentationFailure(failure)))
                destination == null -> settled(intent, DesktopAccountUiSettlement.PickerCancelled)
                !isCurrent(intent) -> settled(intent, DesktopAccountUiSettlement.Failed(AccountControlFailure.CONTEXT_CHANGED))
                else -> recoveryFiles.exportPortable(destination).whenComplete { _, error ->
                    settled(intent, if (error == null) DesktopAccountUiSettlement.Finished
                        else DesktopAccountUiSettlement.Failed(presentationFailure(error)))
                }
            }
        }
    }

    private fun importRecovery(intent: DesktopAccountIntent): Unit {
        picker.chooseRecoverySource().whenComplete { source, failure ->
            when {
                failure != null -> settled(intent, DesktopAccountUiSettlement.Failed(presentationFailure(failure)))
                source == null -> settled(intent, DesktopAccountUiSettlement.PickerCancelled)
                !isCurrent(intent) -> settled(intent, DesktopAccountUiSettlement.Failed(AccountControlFailure.CONTEXT_CHANGED))
                else -> recoveryFiles.importPortable(source).whenComplete { _, error ->
                    settled(intent, if (error == null) DesktopAccountUiSettlement.Finished
                        else DesktopAccountUiSettlement.Failed(presentationFailure(error)))
                }
            }
        }
    }

    private fun settled(intent: DesktopAccountIntent, outcome: DesktopAccountUiSettlement): Unit =
        onSettlement(intent.scope, intent.command.action, outcome)

    override fun close(): Unit { closed.set(true) }
}

/** Redacted ordinary status. Exact origins/identities belong only to DesktopTransportReview. */
public fun desktopRuntimeConnectionView(state: DesktopAccountViewState): DesktopConnectionView = DesktopConnectionView(
    condition = when {
        state.busy -> DesktopAccountCondition.Loading
        state.health == DesktopAccountHealth.OFFLINE -> DesktopAccountCondition.Offline
        state.health == DesktopAccountHealth.AUTH_REQUIRED -> DesktopAccountCondition.Attention
        state.phase == DesktopAccountPhase.OUTCOME_PENDING -> DesktopAccountCondition.Pending
        state.phase == DesktopAccountPhase.CONNECTED -> DesktopAccountCondition.Ready
        state.phase == DesktopAccountPhase.TRUST_REQUIRED -> DesktopAccountCondition.Attention
        state.phase == DesktopAccountPhase.LOCAL -> DesktopAccountCondition.Empty
        else -> DesktopAccountCondition.Unavailable
    }, issue = when (state.failure) {
        AccountControlFailure.TRUST_REQUIRED -> DesktopAccountIssue.IdentityChanged
        AccountControlFailure.AUTHENTICATION_REQUIRED -> DesktopAccountIssue.Reconnect
        AccountControlFailure.CAPACITY -> DesktopAccountIssue.Capacity
        AccountControlFailure.EXPIRED -> DesktopAccountIssue.Expired
        AccountControlFailure.CONFLICT, AccountControlFailure.CONTEXT_CHANGED -> DesktopAccountIssue.Conflict
        AccountControlFailure.UNSUPPORTED -> DesktopAccountIssue.Unsupported
        null -> DesktopAccountIssue.None
        else -> DesktopAccountIssue.Retry
    }, accountPublicId = state.publicId, pendingItems = state.pendingCount)

/** A fresh successful list read is explicit, since the controller's initial empty list is not zero. */
public fun desktopProfileDevicesView(state: DesktopProfileViewState, listReadConfirmed: Boolean,
    language: DesktopLanguage): DesktopDevicesView {
    val condition = if (listReadConfirmed) DesktopAccountCondition.Ready else DesktopAccountCondition.Unavailable
    val devices = state.devices.map { device -> DesktopDeviceView(device.handle.id, device.name, device.current, condition, null) }
    val sessions = state.sessions.map { session ->
        val label = state.devices.singleOrNull { it.handle.id == session.device.id }?.name
            ?: if (language == DesktopLanguage.Russian) "Устройство" else "Device"
        DesktopSessionView(session.device.id + ":" + session.generation, label, session.current, condition)
    }
    return DesktopDevicesView(condition, devices, sessions, emptyList())
}

public fun desktopProfilePrivacyView(state: DesktopProfileViewState, deletion: DesktopDeletionView?): DesktopPrivacyView =
    DesktopPrivacyView(if (state.trainingRevision != null || state.visibilityRevision != null) DesktopAccountCondition.Ready
        else DesktopAccountCondition.Unavailable, when (state.trainingDecision) {
            "UNKNOWN" -> DesktopTrainingPolicy.Unknown
            "GRANTED" -> DesktopTrainingPolicy.Granted
            "DENIED" -> DesktopTrainingPolicy.Denied
            "WITHDRAWN" -> DesktopTrainingPolicy.Withdrawn
            else -> null
        }, state.trainingRevision, state.friendsCanViewStatistics, state.visibilityRevision, deletion)

private fun presentationFailure(failure: Throwable): AccountControlFailure {
    var current = failure
    repeat(8) {
        if (current is AccountControlException) return (current as AccountControlException).code
        current = current.cause ?: return AccountControlFailure.TRANSPORT_UNAVAILABLE
    }
    return AccountControlFailure.TRANSPORT_UNAVAILABLE
}
