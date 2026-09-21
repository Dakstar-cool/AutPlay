package app.autplay.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.TrainingConsentUi
import app.autplay.application.trainingconsent.TRAINING_CONSENT_MAX_REVISION

@Composable
internal fun TrainingConsentCard(ui: TrainingConsentUi) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(stringResource(R.string.training_consent_title), style = MaterialTheme.typography.titleMedium)
        Text(stringResource(R.string.training_consent_explanation))
        Text(stringResource(R.string.training_consent_weights))
        val policy = ui.state.confirmed
        val status = when {
            ui.state.pending -> R.string.training_consent_pending
            ui.state.busy -> R.string.training_consent_loading
            ui.state.error -> R.string.training_consent_unavailable
            policy == null -> R.string.training_consent_unavailable
            policy.granted -> R.string.training_consent_granted
            policy.decision == "UNKNOWN" -> R.string.training_consent_unknown
            else -> R.string.training_consent_private
        }
        Text(stringResource(status))
        if (ui.available && policy != null && !ui.state.pending && policy.revision < TRAINING_CONSENT_MAX_REVISION) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { ui.choose("GRANTED") }, enabled = !ui.state.busy && !policy.granted,
                    modifier = Modifier.weight(1f)) { Text(stringResource(R.string.training_consent_allow)) }
                OutlinedButton(onClick = { ui.choose(if (policy.granted) "WITHDRAWN" else "DENIED") }, enabled = !ui.state.busy,
                    modifier = Modifier.weight(1f)) { Text(stringResource(if (policy.granted) R.string.training_consent_withdraw else R.string.training_consent_refuse)) }
            }
        }
        if (ui.available && ui.state.pending) {
            OutlinedButton(onClick = { ui.choose("WITHDRAWN") }, enabled = !ui.state.busy) {
                Text(stringResource(R.string.training_consent_withdraw))
            }
        }
        if (ui.available && !ui.state.busy) {
            TextButton(onClick = ui.retry) { Text(stringResource(R.string.training_consent_refresh)) }
        }
        if (ui.state.error) Text(stringResource(R.string.training_consent_error))
    }
}
