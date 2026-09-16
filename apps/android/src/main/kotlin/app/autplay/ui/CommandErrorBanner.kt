package app.autplay.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R

/** Shared by every scaffold; stable diagnostic codes never reach the user-facing text. */
@Composable
internal fun CommandErrorBanner(errorCode: String?, onDismiss: () -> Unit) {
    if (errorCode == null) return
    Snackbar(
        modifier = Modifier.padding(12.dp).testTag("command-error")
            .semantics { liveRegion = LiveRegionMode.Polite },
        action = {
            TextButton(onClick = onDismiss) { Text(stringResource(R.string.action_dismiss_error)) }
        },
    ) {
        Text(stringResource(R.string.action_failed_friendly))
    }
}
