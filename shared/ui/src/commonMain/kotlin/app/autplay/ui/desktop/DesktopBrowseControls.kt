package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.referentialEqualityPolicy
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** Identity guards must observe a new instance even when its data equals the previous value. */
internal fun <T> desktopBrowseReferenceState(value: T): MutableState<T> =
    mutableStateOf(value, referentialEqualityPolicy())

@Composable
internal fun <T> rememberDesktopBrowseReference(value: T): State<T> {
    val state = remember { desktopBrowseReferenceState(value) }
    state.value = value
    return state
}

/** Navigation only; the caller owns captured input/revision admission for each callback. */
@Composable
internal fun DesktopBrowsePager(
    page: DesktopBrowsePage,
    label: String,
    language: DesktopLanguage,
    enabled: Boolean,
    onPage: (Int) -> Unit,
) {
    val range = DesktopText.BrowseRange.inLanguage(language)
        .replace("{start}", (if (page.total == 0) 0 else page.start + 1).toString())
        .replace("{end}", page.end.toString()).replace("{total}", page.total.toString())
    val position = DesktopText.BrowsePage.inLanguage(language)
        .replace("{page}", (page.index + 1).toString()).replace("{pages}", page.pages.toString())
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("$label: $range; $position", style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(
                Triple(DesktopText.BrowseFirst, 0, page.index > 0),
                Triple(DesktopText.BrowsePrevious, page.index - 1, page.index > 0),
                Triple(DesktopText.BrowseNext, page.index + 1, page.index + 1 < page.pages),
                Triple(DesktopText.BrowseLast, page.pages - 1, page.index + 1 < page.pages),
            ).forEach { (text, target, available) ->
                val action = text.inLanguage(language)
                OutlinedButton(onClick = { if (enabled && available) onPage(target) }, enabled = enabled && available,
                    modifier = Modifier.semantics { contentDescription = "$label: $action; $range; $position" }) {
                    Text(action)
                }
            }
        }
    }
}
