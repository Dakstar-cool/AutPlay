package app.autplay.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import app.autplay.R
import kotlinx.coroutines.launch

/** A local-only first-run introduction. It deliberately requests no folders, accounts, or network. */
@Composable
public fun WelcomeOnboardingScreen(
    onComplete: suspend (UiDestination) -> Boolean,
    modifier: Modifier = Modifier,
) {
    var step by rememberSaveable { mutableIntStateOf(0) }
    var saving by remember { mutableStateOf(false) }
    var saveFailed by rememberSaveable { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val title = when (step) {
        0 -> stringResource(R.string.onboarding_welcome_title)
        1 -> stringResource(R.string.onboarding_library_title)
        else -> stringResource(R.string.onboarding_server_title)
    }
    val body = when (step) {
        0 -> stringResource(R.string.onboarding_welcome_body)
        1 -> stringResource(R.string.onboarding_library_body)
        else -> stringResource(R.string.onboarding_server_body)
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 28.dp, vertical = 40.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = stringResource(R.string.onboarding_step, step + 1, 3),
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(12.dp))
        LinearProgressIndicator(
            progress = { (step + 1) / 3f },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(36.dp))
        Text(text = title, style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(16.dp))
        Text(text = body, style = MaterialTheme.typography.bodyLarge)
        Spacer(Modifier.height(48.dp))

        if (step < 2) {
            Button(
                modifier = Modifier.fillMaxWidth(),
                onClick = { step += 1 },
            ) { Text(stringResource(R.string.onboarding_next)) }
        } else {
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !saving,
                onClick = {
                    saving = true
                    saveFailed = false
                    scope.launch {
                        if (!onComplete(UiDestination.Home)) saveFailed = true
                        saving = false
                    }
                },
            ) { Text(stringResource(R.string.onboarding_continue_local)) }
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !saving,
                onClick = {
                    saving = true
                    saveFailed = false
                    scope.launch {
                        if (!onComplete(UiDestination.Profile)) saveFailed = true
                        saving = false
                    }
                },
            ) { Text(stringResource(R.string.onboarding_connect_server)) }
            if (saveFailed) {
                Spacer(Modifier.height(12.dp))
                Text(
                    text = stringResource(R.string.onboarding_save_failed),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        Spacer(Modifier.weight(1f))
        Text(
            text = stringResource(R.string.onboarding_local_note),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
    }
}
