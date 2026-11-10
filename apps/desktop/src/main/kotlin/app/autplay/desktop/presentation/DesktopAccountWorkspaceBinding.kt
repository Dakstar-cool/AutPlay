package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.AccountLease
import app.autplay.ui.desktop.*
import java.awt.Window
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

internal enum class DesktopAccountAvailability {
    Opening, InitializeRequired, Available, IdentityUnavailable, KeychainLocked, KeychainAccessRequired,
    RecoveryRequired, Retired,
}

internal enum class DesktopContextSettlement { Ready, Settling, Failed }

/** Main receives real host projections; there is no synthetic authenticated/default workspace. */
internal class DesktopAccountWorkspaceBinding(
    val state: StateFlow<DesktopAccountWorkspaceState>,
    val actions: DesktopAccountActionSink,
    val sensitiveOutput: StateFlow<DesktopAccountSensitiveOutput?>,
    val attachWindow: (Window?) -> Unit,
    val setVisible: (Boolean) -> Unit,
    val availability: StateFlow<DesktopAccountAvailability> = MutableStateFlow(DesktopAccountAvailability.Available),
    val initialize: (() -> Unit)? = null,
    val setWindowVisible: (Boolean) -> Unit = {},
    /** Read-only Home presentation; never selects or retires the playback context. */
    val setHomeVisible: (Boolean) -> Unit = {},
    /** Ordinary Library catalog visibility; no media context selection or file chooser. */
    val setCatalogVisible: (Boolean) -> Unit = {},
    /** Retained settlement; true additionally selects the actual standalone media context. */
    val settleNavigation: (Boolean) -> CompletableFuture<Unit> = { CompletableFuture.completedFuture(Unit) },
    val contextSettlement: StateFlow<DesktopContextSettlement> = MutableStateFlow(DesktopContextSettlement.Ready),
    /** Actual issued K/J owner, for process-only navigation comparisons. */
    val accountOwner: () -> AccountLease? = { null },
) {
    companion object {
        fun unavailable(reason: DesktopAccountAvailability): DesktopAccountWorkspaceBinding = DesktopAccountWorkspaceBinding(
            MutableStateFlow(unavailableAccountWorkspace()), DesktopAccountActionSink(::rejectUnavailableAccountInput),
            MutableStateFlow(null), {}, {}, MutableStateFlow(reason),
        )
    }
}

/** No permits, authenticated identity, consent or selected scope is synthesized on failure. */
internal fun unavailableAccountWorkspace(): DesktopAccountWorkspaceState = DesktopAccountWorkspaceState(
    DesktopAccountScope(UUID.randomUUID().toString(), 0), emptyList(), emptyList(),
    DesktopPortableAccountView(DesktopAccountCondition.Unavailable, null, null, DesktopRecoveryFileStatus.None),
    DesktopConnectionView(DesktopAccountCondition.Unavailable),
    DesktopSelfPairingView(DesktopAccountCondition.Unavailable, DesktopSelfRole.Source, null, null, null, null, null),
    DesktopServerRecoveryView(DesktopAccountCondition.Unavailable, null, null, null),
    DesktopDevicesView(DesktopAccountCondition.Unavailable, emptyList(), emptyList(), emptyList()),
    DesktopPrivacyView(DesktopAccountCondition.Unavailable, null, null, null, null, null),
    DesktopCatalogView(DesktopAccountCondition.Unavailable, null, null, emptyList()),
    DesktopDownloadsView(DesktopAccountCondition.Unavailable, emptyList(), null, false), emptyList(),
)

internal fun rejectUnavailableAccountInput(intent: DesktopAccountIntent): Unit {
    // Rejection has no successful settlement and never retains an unexpected private input.
    when (val command = intent.command) {
        is DesktopAccountCommand.Secret -> command.clear()
        is DesktopAccountCommand.CreatePortable -> command.password.clear()
        is DesktopAccountCommand.Discover -> command.enteredOrigin.clear()
        is DesktopAccountCommand.RecoveryPreview -> command.clear()
        is DesktopAccountCommand.PinnedTransport -> command.clear()
        else -> Unit
    }
}
