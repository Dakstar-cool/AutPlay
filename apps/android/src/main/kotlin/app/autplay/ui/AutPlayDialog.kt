package app.autplay.ui

import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Shared surface treatment for app-owned confirmations and editors. */
@Composable
internal fun AutPlayDialog(
    onDismissRequest: () -> Unit,
    confirmButton: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    dismissButton: (@Composable () -> Unit)? = null,
    title: (@Composable () -> Unit)? = null,
    text: (@Composable () -> Unit)? = null,
) {
    AlertDialog(
        onDismissRequest = onDismissRequest,
        confirmButton = confirmButton,
        modifier = modifier,
        dismissButton = dismissButton,
        title = title,
        text = text,
        shape = MaterialTheme.shapes.extraLarge,
        containerColor = AutPlayTokens.colors.raisedSurface,
        titleContentColor = MaterialTheme.colorScheme.onSurface,
        textContentColor = AutPlayTokens.colors.mutedText,
        tonalElevation = 0.dp,
    )
}
