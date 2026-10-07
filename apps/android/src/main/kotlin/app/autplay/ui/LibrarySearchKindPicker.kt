package app.autplay.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import app.autplay.R
import app.autplay.application.search.LibrarySearchKind

@Composable
internal fun LibrarySearchKindPicker(kind: LibrarySearchKind, onChange: (LibrarySearchKind) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }, modifier = Modifier.testTag("search-kind-picker")) {
            Text(stringResource(R.string.search_kind_label, kindLabel(kind)))
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            LibrarySearchKind.entries.forEach { option ->
                DropdownMenuItem(text = { Text(kindLabel(option)) },
                    leadingIcon = { RadioButton(selected = option == kind, onClick = null) },
                    modifier = Modifier.testTag("search-kind-${option.wireValue}"),
                    onClick = { open = false; onChange(option) })
            }
        }
    }
}

@Composable
private fun kindLabel(kind: LibrarySearchKind): String = stringResource(when (kind) {
    LibrarySearchKind.All -> R.string.search_kind_all
    LibrarySearchKind.Track -> R.string.search_kind_track
    LibrarySearchKind.Artist -> R.string.search_kind_artist
    LibrarySearchKind.Album -> R.string.search_kind_album
})
