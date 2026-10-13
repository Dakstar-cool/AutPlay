package app.autplay

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import app.autplay.application.portableaccount.PortableAccountRuntime
import app.autplay.application.portableaccount.PortableRecoveryFile
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.profilepairing.ProfilePairingRuntimeState
import app.autplay.data.settings.NonSecretSettings
import app.autplay.ui.profilepairing.PortableAccountActions
import app.autplay.ui.profilepairing.PortableAccountUiState
import java.io.InputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal data class MainPortableAccountUi(val state: PortableAccountUiState, val actions: PortableAccountActions, val runtime: PortableAccountRuntime)

@Composable
internal fun rememberPortableAccountUi(runtime: PortableAccountRuntime, settings: NonSecretSettings,
    pairing: ProfilePairingRuntimeState, reportError: (String) -> Unit): MainPortableAccountUi {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val status by runtime.state.collectAsState()
    var recoveryFile by remember(runtime) { mutableStateOf<ByteArray?>(null) }
    DisposableEffect(runtime) { onDispose { recoveryFile?.fill(0); recoveryFile = null } }
    val importFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) scope.launch {
            try {
                val bytes = withContext(Dispatchers.IO) {
                    requireNotNull(context.contentResolver.openInputStream(uri)).use(::readBoundedPortableRecoveryBytes)
                }
                try {
                    PortableRecoveryFile.parse(bytes).close()
                    recoveryFile?.fill(0)
                    recoveryFile = bytes.copyOf()
                } finally { bytes.fill(0) }
            } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch (_: Exception) { reportError("ACCOUNT_RECOVERY_INVALID") }
        }
    }
    val export = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if (uri != null) scope.launch {
            val bytes = runtime.store.recoveryExport()
            if (bytes != null) try {
                withContext(Dispatchers.IO) {
                    requireNotNull(context.contentResolver.openOutputStream(uri, "wt")).use { it.write(bytes); it.flush() }
                    val saved = requireNotNull(context.contentResolver.openInputStream(uri)).use(::readBoundedPortableRecoveryBytes)
                    try { require(saved.size <= PortableRecoveryFile.MAX_BYTES); runtime.store.acknowledgeRecoveryExport(saved) } finally { saved.fill(0) }
                }
                runtime.refresh()
            } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch (_: Exception) { reportError("ACCOUNT_RECOVERY_SAVE_FAILED") }
            finally { bytes.fill(0) }
        }
    }
    return MainPortableAccountUi(PortableAccountUiState(status, settings.activeServerProfileId != null,
        pairing.pairing is PairingState.AwaitingTrust && pairing.trustConfirmed, status.serverPublicId ?: settings.pendingPublicId,
        recoveryFileReady = recoveryFile != null),
        PortableAccountActions(create = runtime::create, connect = runtime::connect,
            exportRecovery = { export.launch("autplay-recovery.json") }, retry = { scope.launch { runtime.resume() } },
            importRecovery = { importFile.launch(arrayOf("application/json", "text/plain", "application/octet-stream")) },
            clearRecoveryFile = { recoveryFile?.fill(0); recoveryFile = null },
            recover = { login, code, password ->
                val bytes = recoveryFile?.copyOf()
                try { runtime.recover(login, code, bytes, password) }
                finally { bytes?.fill(0); recoveryFile?.fill(0); recoveryFile = null }
            },
            recoveryCode = runtime.store::recoveryCodeExport,
            acknowledgeCode = { code -> runtime.store.acknowledgeRecoveryCode(code); runtime.refresh() }), runtime)
}

/** A one-byte oversize sentinel bounds both stream consumption and allocation on Android API 26. */
internal fun readBoundedPortableRecoveryBytes(input: InputStream): ByteArray {
    val buffer = ByteArray(PortableRecoveryFile.MAX_BYTES + 1)
    var total = 0
    try {
        while (total < buffer.size) {
            val count = input.read(buffer, total, buffer.size - total)
            if (count < 0) break
            if (count == 0) {
                val next = input.read()
                if (next < 0) break
                buffer[total++] = next.toByte()
            } else total += count
        }
        require(total in 2..PortableRecoveryFile.MAX_BYTES) { "ACCOUNT_RECOVERY_INVALID" }
        return buffer.copyOf(total)
    } finally { buffer.fill(0) }
}
