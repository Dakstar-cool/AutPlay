package app.autplay

import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.trainingconsent.*
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.ui.settings.TrainingConsentCard
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.ui.res.stringResource
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

internal data class TrainingConsentUi(val state: TrainingConsentState = TrainingConsentState(),
    val available: Boolean = false, val choose: (String) -> Unit = {}, val retry: () -> Unit = {})

@Composable
internal fun rememberTrainingConsentUi(settings: NonSecretSettings, pairing: PairingState): TrainingConsentUi {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val profile = settings.activeServerProfileId
    val account = settings.activeUserId?.value
    val device = settings.deviceId?.value
    val checkpoint = settings.m5Binding
    val origin = settings.serverBaseUrl
    val enabled = (pairing as? PairingState.Connected)?.capabilities?.supportedOperations
        ?.contains("shared_training_consent") == true
    val key = if (profile != null && account != null && device != null && checkpoint != null && origin != null)
        "${profile.value}:$account:$device:${checkpoint.bindingCommitId}:$origin" else null
    val runtime = remember(key, enabled) {
        if (!enabled || key == null) null else {
            val credentials = AndroidKeystoreCredentialStore(context.applicationContext)
            TrainingConsentRuntime(requireNotNull(account), key, TrainingConsentJournalStore(credentials,
                CredentialJournalSlots.trainingConsentProfile(requireNotNull(profile), account, requireNotNull(origin))),
                AutPlayRuntime.trainingConsentPort(context, requireNotNull(origin), requireNotNull(profile), credentials)) {
                val current = applicationNonSecretSettingsStore(context.applicationContext).settings.first()
                if (current.activeServerProfileId != profile || current.activeUserId?.value != account ||
                    current.deviceId?.value != device || current.m5Binding?.bindingCommitId != checkpoint?.bindingCommitId ||
                    current.serverBaseUrl != origin) false else {
                    val material = credentials.read(requireNotNull(profile))
                    if (material == null) false else try {
                        SessionCredentialEnvelopeCodec.decode(material).bindingCommitId == checkpoint?.bindingCommitId
                    } finally { material.fill(0) }
                }
            }
        }
    }
    LaunchedEffect(runtime) { runtime?.load() }
    val state by (runtime?.state ?: remember { kotlinx.coroutines.flow.MutableStateFlow(TrainingConsentState()) }).collectAsState()
    val ui = TrainingConsentUi(state, runtime != null,
        choose = { decision -> scope.launch { runtime?.choose(decision) } }, retry = { scope.launch { runtime?.load() } })
    var dismissed by remember(key) { mutableStateOf(false) }
    val title = stringResource(R.string.training_consent_title)
    if (runtime != null && state.confirmed?.decision == "UNKNOWN" && !state.busy && !state.pending && !dismissed) {
        AlertDialog(onDismissRequest = { dismissed = true }, title = { Text(title) },
            text = { TrainingConsentCard(ui) }, confirmButton = {})
    }
    return ui
}
