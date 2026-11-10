package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

internal fun desktopSearchErrorText(code: String): DesktopText = when (desktopSearchErrorCode(code)) {
    "desktop_search_closed" -> DesktopText.SearchClosed
    "desktop_search_invalid" -> DesktopText.SearchInvalid
    "desktop_search_unavailable" -> DesktopText.SearchUnavailable
    else -> DesktopText.SearchFailure
}

@Composable
internal fun DesktopSearchFailure(
    errorCode: String?,
    language: DesktopLanguage,
    enabled: Boolean,
    onRetry: () -> Unit,
    onReset: () -> Unit,
) {
    if (errorCode == null) return
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(desktopSearchErrorText(errorCode).inLanguage(language), color = MaterialTheme.colorScheme.error,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onRetry, enabled = enabled) { Text(DesktopText.SearchRetry.inLanguage(language)) }
            OutlinedButton(onClick = onReset) { Text(DesktopText.SearchReset.inLanguage(language)) }
        }
    }
}
