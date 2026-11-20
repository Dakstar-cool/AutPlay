package app.autplay.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.data.settings.PlaybackAudioSettings
import kotlin.math.roundToInt

@Composable
internal fun PlaybackAudioSettingsPanel(
    settings: PlaybackAudioSettings,
    onUpdate: ((PlaybackAudioSettings) -> PlaybackAudioSettings) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        HorizontalDivider()
        Text(stringResource(R.string.settings_replay_gain), style = MaterialTheme.typography.titleSmall)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("OFF" to R.string.settings_gain_off, "TRACK" to R.string.settings_gain_track,
                "ALBUM" to R.string.settings_gain_album).forEach { (mode, label) ->
                FilterChip(
                    selected = if (mode == "OFF") settings.replayGainMode !in setOf("TRACK", "ALBUM") else settings.replayGainMode == mode,
                    onClick = { onUpdate { it.copy(replayGainMode = mode) } },
                    label = { Text(stringResource(label)) },
                    modifier = Modifier.weight(1f).testTag("replay-gain-$mode"),
                )
            }
        }
        Text(stringResource(R.string.settings_gain_tags_help), style = MaterialTheme.typography.bodySmall)
        AudioLevelSlider(stringResource(R.string.settings_gain_preamp), "replay-gain-preamp", settings.preampDb,
            enabled = settings.replayGainMode in setOf("TRACK", "ALBUM")) { value -> onUpdate { it.copy(preampDb = value) } }
        AudioSwitch(stringResource(R.string.settings_gain_protection), "replay-gain-protection", settings.preventClipping) {
            enabled -> onUpdate { it.copy(preventClipping = enabled) }
        }
        HorizontalDivider()
        AudioSwitch(stringResource(R.string.settings_equalizer), "equalizer-enabled", settings.equalizerEnabled) {
            enabled -> onUpdate { it.copy(equalizerEnabled = enabled) }
        }
        if (settings.equalizerEnabled) {
            Text(stringResource(R.string.settings_equalizer_headroom), style = MaterialTheme.typography.bodySmall)
            PlaybackAudioSettings.BAND_FREQUENCIES_HZ.forEachIndexed { index, frequency ->
                AudioLevelSlider(stringResource(R.string.settings_equalizer_band, frequency), "equalizer-band-$index",
                    settings.equalizerBandsDb[index]) { value ->
                    onUpdate { current -> current.copy(equalizerBandsDb = current.equalizerBandsDb.mapIndexed { band, old ->
                        if (band == index) value else old
                    }) }
                }
            }
            OutlinedButton(onClick = { onUpdate { it.copy(equalizerBandsDb = List(5) { 0 }) } },
                modifier = Modifier.fillMaxWidth().testTag("equalizer-reset")) {
                Text(stringResource(R.string.settings_equalizer_reset))
            }
        }
    }
}

@Composable
private fun AudioSwitch(label: String, tag: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange, modifier = Modifier.testTag(tag).semantics { contentDescription = label })
    }
}

@Composable
private fun AudioLevelSlider(label: String, tag: String, value: Int, enabled: Boolean = true, onChange: (Int) -> Unit) {
    var draft by remember(value) { mutableFloatStateOf(value.toFloat()) }
    Text(stringResource(R.string.settings_audio_db, label, draft.roundToInt()))
    Slider(value = draft, onValueChange = { draft = it }, onValueChangeFinished = { onChange(draft.roundToInt()) },
        valueRange = -12f..12f, steps = 23, enabled = enabled,
        modifier = Modifier.fillMaxWidth().testTag(tag).semantics { contentDescription = label })
}
