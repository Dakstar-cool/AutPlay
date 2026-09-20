package app.autplay

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import app.autplay.application.accountrecovery.AccountRecoveryExportTicket
import app.autplay.application.accountrecovery.AccountRecoveryRecipientRuntime
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySourceRuntime
import app.autplay.application.accountrecovery.AccountRecoverySourceState
import app.autplay.application.accountrecovery.AccountRestorationPurpose
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.profilepairing.AccountRecoveryActions
import app.autplay.ui.profilepairing.AccountRecoveryUiState
import app.autplay.ui.profilepairing.SelfPairingUiState
import app.autplay.ui.profilepairing.accountRecoveryBlocksFirstBind
import app.autplay.ui.profilepairing.publicRegistrationBlocksOrdinaryFirstBind
import app.autplay.ui.profilepairing.selfPairingBlocksFirstBind
import java.io.InputStream
import java.security.MessageDigest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal data class MainAccountRecoveryUi(val state: AccountRecoveryUiState, val actions: AccountRecoveryActions)

/** A picker result can save only the account and document explicitly selected before opening it. */
@Composable
internal fun rememberAccountRecoveryUi(
    source: AccountRecoverySourceRuntime,
    recipient: AccountRecoveryRecipientRuntime,
    settings: NonSecretSettings,
    pairing: PairingState,
    registration: PublicAccountRegistrationState,
    selfRecipient: SelfPairingRecipientState,
    bound: Boolean,
    purpose: AccountRestorationPurpose = AccountRestorationPurpose.RECOVERY,
    externallyBlocked: Boolean = false,
): MainAccountRecoveryUi {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val sourceState by source.state.collectAsState()
    val recipientState by recipient.state.collectAsState()
    var notice by remember { mutableStateOf<String?>(null) }
    var exportTicket by remember { mutableStateOf<AccountRecoveryExportTicket?>(null) }
    var exportInProgress by remember { mutableStateOf(false) }
    val canManage = !externallyBlocked && purpose == AccountRestorationPurpose.RECOVERY &&
        (pairing as? PairingState.Connected)?.capabilities?.supportedOperations?.contains("account_recovery") == true
    LaunchedEffect(canManage, settings.m5Binding?.bindingCommitId) {
        source.loadLocal()
        if (canManage) source.load()
    }
    LaunchedEffect(recipientState) {
        if (recipientState == AccountRecoveryRecipientState.Connected) source.load()
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            notice = null
            var imported: ByteArray? = null
            try {
                withContext(Dispatchers.IO) {
                    requireNotNull(context.contentResolver.openInputStream(uri)).use {
                        imported = readBoundedRecoveryBytes(it)
                    }
                }
                recipient.importDocument(requireNotNull(imported))
            } catch (failure: CancellationException) { throw failure }
            catch (_: Exception) { notice = "failed" }
            finally { imported?.fill(0) }
        }
    }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        val ticket = exportTicket
        exportTicket = null
        if (uri == null) {
            exportInProgress = false
        } else if (ticket == null) {
            // A recreated process cannot acknowledge an action whose account selection was lost.
            exportInProgress = false
            notice = "failed"
        } else scope.launch {
            notice = null
            try {
                source.saveExport(ticket) { bytes ->
                    withContext(Dispatchers.IO) {
                        requireNotNull(context.contentResolver.openOutputStream(uri, "wt")).use {
                            it.write(bytes)
                            it.flush()
                        }
                        var readBack: ByteArray? = null
                        try {
                            requireNotNull(context.contentResolver.openInputStream(uri)).use {
                                readBack = readBoundedRecoveryBytes(it)
                            }
                            check(MessageDigest.isEqual(bytes, requireNotNull(readBack))) { "RECOVERY_FILE_NOT_SAVED" }
                        } finally { readBack?.fill(0) }
                    }
                }
                notice = "saved"
            } catch (failure: CancellationException) { throw failure }
            catch (_: Exception) { notice = "failed" }
            finally { exportInProgress = false }
        }
    }
    val setup = settings.accountRecoverySetup
    val ready = sourceState as? AccountRecoverySourceState.Ready
    val confirmed = ready?.exportConfirmed == true && setup?.savedCodeGeneration == ready.generation &&
        setup.savedDocumentSha256 != null
    val required = setup?.matches(settings) == true && (setup.savedCodeGeneration == null ||
        (ready != null && !confirmed) || sourceState is AccountRecoverySourceState.Blocked)
    val state = AccountRecoveryUiState(if (purpose == AccountRestorationPurpose.RECOVERY) sourceState else AccountRecoverySourceState.Idle, recipientState, canManage,
        canRecover = !externallyBlocked && !bound && settings.m5Binding == null &&
            settings.m5PendingExchangeCheckpoint == null && settings.m5AdmissionCheckpoint == null &&
            !publicRegistrationBlocksOrdinaryFirstBind(registration) &&
            !selfPairingBlocksFirstBind(SelfPairingUiState(recipient = selfRecipient)) &&
            (pairing is PairingState.NotConnected || pairing is PairingState.Cancelled ||
                accountRecoveryBlocksFirstBind(AccountRecoveryUiState(recipient = recipientState))),
        fileNotice = notice, setupRequired = purpose == AccountRestorationPurpose.RECOVERY && required, purpose = purpose)
    val actions = AccountRecoveryActions(
        configure = { replace -> notice = null; scope.launch { source.configure(replace) } },
        export = {
            if (!exportInProgress) {
                exportInProgress = true
                notice = null
                scope.launch {
                    try {
                        exportTicket = source.prepareExport()
                        exporter.launch("AutPlay-recovery.txt")
                    } catch (failure: CancellationException) {
                        exportTicket = null; exportInProgress = false; throw failure
                    } catch (_: Exception) {
                        exportTicket = null; exportInProgress = false; notice = "failed"
                    }
                }
            }
        },
        pickFile = { picker.launch(arrayOf("text/plain", "application/json", "application/octet-stream")) },
        manual = { origin, account, code -> scope.launch { recipient.inspectManual(origin, account, code) } },
        confirmServer = { scope.launch { recipient.confirmServer() } },
        confirmAccount = { account -> scope.launch { recipient.confirmAccount(account) } },
        retrySource = { scope.launch { source.load() } },
        retryRecipient = { scope.launch { recipient.resume() } },
        cancel = { scope.launch { recipient.cancelBeforeCommit() } },
    )
    return MainAccountRecoveryUi(state, actions)
}

internal fun readBoundedRecoveryBytes(input: InputStream): ByteArray {
    val buffer = ByteArray(4097)
    var total = 0
    try {
        while (total < buffer.size) {
            val read = input.read(buffer, total, buffer.size - total)
            if (read < 0) break
            require(read > 0) { "RECOVERY_FILE_UNAVAILABLE" }
            total += read
        }
        require(total in 2..4096) { "RECOVERY_FILE_INVALID" }
        return buffer.copyOf(total)
    } finally { buffer.fill(0) }
}
