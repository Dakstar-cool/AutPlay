package app.autplay.ui.desktop

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackPresentationState

/** Session preferences belong to the host; this renderer never owns library or queue state. */
@Composable
public fun AutPlayDesktopShell(
    section: DesktopSection,
    language: DesktopLanguage,
    themeMode: DesktopThemeMode,
    playback: PlaybackPresentationState,
    playbackAdapterAvailable: Boolean,
    onSectionChange: (DesktopSection) -> Unit,
    onLanguageChange: (DesktopLanguage) -> Unit,
    onThemeChange: (DesktopThemeMode) -> Unit,
    playbackCallbacks: DesktopPlaybackCallbacks,
    modifier: Modifier = Modifier,
    library: DesktopLibrarySnapshot? = null,
    libraryCallbacks: DesktopLibraryCallbacks = DesktopLibraryCallbacks(),
    importing: Boolean = false,
    importResult: DesktopImportResult? = null,
    errorCode: String? = null,
) {
    AutPlayDesktopTheme(themeMode) {
        Surface(modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column {
                Row(Modifier.weight(1f).fillMaxWidth()) {
                    Sidebar(section, language, onSectionChange)
                    Column(
                        Modifier.weight(1f).fillMaxHeight().padding(24.dp),
                        verticalArrangement = Arrangement.spacedBy(24.dp),
                    ) {
                        Text(section.label(language), style = MaterialTheme.typography.headlineLarge)
                        if (library != null && section != DesktopSection.Settings) {
                            DesktopLibraryContent(section, library, language, importing, importResult,
                                errorCode, libraryCallbacks, Modifier.weight(1f).fillMaxWidth())
                        } else when (section) {
                            DesktopSection.Library -> {
                                MutedText(DesktopText.LibrarySubtitle.inLanguage(language))
                                EmptyCollection(
                                    language, DesktopText.EmptyLibrary, DesktopText.EmptyLibraryBody,
                                    DesktopText.Import,
                                )
                            }
                            DesktopSection.Playlists -> EmptyCollection(
                                language, DesktopText.EmptyPlaylists, DesktopText.EmptyPlaylistsBody,
                                DesktopText.CreatePlaylist,
                            )
                            DesktopSection.Queue -> MutedText(DesktopText.EmptyQueue.inLanguage(language))
                            DesktopSection.History -> MutedText(DesktopText.EmptyHistory.inLanguage(language))
                            DesktopSection.Settings -> {
                                MutedText(DesktopText.SettingsSubtitle.inLanguage(language))
                                Settings(language, themeMode, onLanguageChange, onThemeChange)
                            }
                        }
                    }
                }
                HorizontalDivider()
                PlaybackBar(playback, playbackAdapterAvailable, language, playbackCallbacks)
            }
        }
    }
}

@Composable
private fun Sidebar(section: DesktopSection, language: DesktopLanguage, onSectionChange: (DesktopSection) -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(
            Modifier.width(208.dp).fillMaxHeight().padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Surface(color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(14.dp)) {
                Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                    Text("AP", style = MaterialTheme.typography.titleLarge)
                }
            }
            Text("AutPlay", style = MaterialTheme.typography.titleLarge)
            MutedText(DesktopText.LocalCollection.inLanguage(language))
            Spacer(Modifier.height(16.dp))
            Column(Modifier.selectableGroup(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DesktopSection.entries.forEach { destination ->
                    val selected = section == destination
                    Surface(
                        color = if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                        shape = MaterialTheme.shapes.small,
                    ) {
                        Row(
                            Modifier.fillMaxWidth().selectable(
                                selected = selected, role = Role.Tab,
                                onClick = { onSectionChange(destination) },
                            ).padding(horizontal = 12.dp, vertical = 16.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Box(
                                Modifier.size(6.dp).background(
                                    if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
                                    RoundedCornerShape(3.dp),
                                ),
                            )
                            Text(destination.label(language), style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
            }
            Spacer(Modifier.weight(1f))
            MutedText(DesktopText.LocalOnly.inLanguage(language))
        }
    }
}

@Composable
private fun EmptyCollection(language: DesktopLanguage, title: DesktopText, body: DesktopText, action: DesktopText) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        shape = MaterialTheme.shapes.large,
    ) {
        Column(
            Modifier.padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = MaterialTheme.shapes.large) {
                Box(Modifier.size(72.dp), contentAlignment = Alignment.Center) {
                    Text("♫", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.primary)
                }
            }
            Text(title.inLanguage(language), style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
            Text(
                body.inLanguage(language), modifier = Modifier.widthIn(max = 480.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
                style = MaterialTheme.typography.bodyLarge,
            )
            OutlinedButton(onClick = {}, enabled = false) { Text(action.inLanguage(language)) }
        }
    }
}

@Composable
private fun Settings(
    language: DesktopLanguage,
    themeMode: DesktopThemeMode,
    onLanguageChange: (DesktopLanguage) -> Unit,
    onThemeChange: (DesktopThemeMode) -> Unit,
) {
    Surface(color = MaterialTheme.colorScheme.surface, shape = MaterialTheme.shapes.large) {
        Column(Modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(DesktopText.Appearance.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            Row(Modifier.selectableGroup(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                DesktopThemeMode.entries.forEach { mode ->
                    FilterChip(
                        selected = themeMode == mode, onClick = { onThemeChange(mode) },
                        label = { Text((if (mode == DesktopThemeMode.Light) DesktopText.Light else DesktopText.Dark).inLanguage(language)) },
                    )
                }
            }
            HorizontalDivider()
            Text(DesktopText.Language.inLanguage(language), style = MaterialTheme.typography.titleMedium)
            Row(Modifier.selectableGroup(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FilterChip(
                    selected = language == DesktopLanguage.Russian,
                    onClick = { onLanguageChange(DesktopLanguage.Russian) }, label = { Text("Русский") },
                )
                FilterChip(
                    selected = language == DesktopLanguage.English,
                    onClick = { onLanguageChange(DesktopLanguage.English) }, label = { Text("English") },
                )
            }
            MutedText(DesktopText.SessionSettings.inLanguage(language))
        }
    }
}

@Composable
private fun PlaybackBar(
    state: PlaybackPresentationState,
    adapterAvailable: Boolean,
    language: DesktopLanguage,
    callbacks: DesktopPlaybackCallbacks,
) {
    val controls = DesktopPlaybackControls.from(state, adapterAvailable)
    val duration = state.durationMs?.takeIf { it > 0 }
    val position = state.positionMs.coerceAtLeast(0).let { if (duration != null) it.coerceAtMost(duration) else it }
    var seekPreview by remember(state.mediaId) { mutableStateOf<Float?>(null) }
    Surface(color = MaterialTheme.colorScheme.surfaceContainer) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        state.title ?: DesktopText.NothingPlaying.inLanguage(language),
                        maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium,
                    )
                    val subtitle = when {
                        !adapterAvailable || state.connection == PlaybackConnectionState.Disconnected -> DesktopText.PlaybackUnavailable.inLanguage(language)
                        !controls.playPause -> DesktopText.ControlsUnavailable.inLanguage(language)
                        else -> state.artist.orEmpty()
                    }
                    Text(subtitle, maxLines = 1, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                OutlinedButton(onClick = callbacks.onPrevious, enabled = controls.previous) {
                    Text(DesktopText.Previous.inLanguage(language))
                }
                Button(onClick = if (state.isPlaying) callbacks.onPause else callbacks.onPlay, enabled = controls.playPause) {
                    Text((if (state.isPlaying) DesktopText.Pause else DesktopText.Play).inLanguage(language))
                }
                OutlinedButton(onClick = callbacks.onNext, enabled = controls.next) {
                    Text(DesktopText.Next.inLanguage(language))
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    formatPlaybackTime(position),
                    modifier = Modifier.semantics {
                        contentDescription = "${DesktopText.Elapsed.inLanguage(language)} ${formatPlaybackTime(position)}"
                    },
                    style = MaterialTheme.typography.labelMedium,
                )
                Slider(
                    value = seekPreview ?: if (duration != null) (position.toDouble() / duration).toFloat() else 0f,
                    onValueChange = { fraction ->
                        if (controls.seek) seekPreview = fraction
                    },
                    onValueChangeFinished = {
                        val fraction = seekPreview
                        if (controls.seek && duration != null && fraction != null) {
                            callbacks.onSeek((fraction.toDouble() * duration).toLong().coerceIn(0, duration))
                        }
                        seekPreview = null
                    },
                    enabled = controls.seek,
                    modifier = Modifier.weight(1f).semantics { contentDescription = DesktopText.Seek.inLanguage(language) },
                )
                Text(
                    duration?.let(::formatPlaybackTime) ?: "--:--",
                    modifier = Modifier.semantics {
                        contentDescription = "${DesktopText.Duration.inLanguage(language)} ${duration?.let(::formatPlaybackTime) ?: "--:--"}"
                    },
                    style = MaterialTheme.typography.labelMedium,
                )
            }
        }
    }
}

@Composable
private fun MutedText(text: String) {
    Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
}

internal fun formatPlaybackTime(milliseconds: Long): String {
    val seconds = milliseconds.coerceAtLeast(0) / 1_000
    val minutes = seconds / 60
    return "$minutes:${(seconds % 60).toString().padStart(2, '0')}"
}
