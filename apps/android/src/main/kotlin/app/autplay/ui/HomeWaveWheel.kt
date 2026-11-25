package app.autplay.ui

import androidx.annotation.StringRes
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.snapping.rememberSnapFlingBehavior
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.recommendation.HomeWheelMix
import androidx.compose.runtime.saveable.rememberSaveable
import kotlin.math.abs
import kotlinx.coroutines.launch

private data class WaveChoice(val id: String, @StringRes val label: Int, val tint: Color)

private val cards = listOf(
    WaveChoice("energy", R.string.home_context_energy, Color(0xFF984428)),
    WaveChoice("running", R.string.home_context_running, Color(0xFF85532D)),
    WaveChoice("focus", R.string.home_context_focus, Color(0xFF4A498C)),
    WaveChoice("evening", R.string.home_context_evening, Color(0xFF71416E)),
    WaveChoice("calm", R.string.home_wheel_calm, Color(0xFF226B70)),
    WaveChoice("sleep", R.string.home_wheel_sleep, Color(0xFF4A4277)),
    WaveChoice("pop", R.string.home_wheel_pop, Color(0xFF85406C)),
    WaveChoice("rock", R.string.home_wheel_rock, Color(0xFF91462E)),
    WaveChoice("hip_hop", R.string.home_wheel_hip_hop, Color(0xFF79572E)),
    WaveChoice("electronic", R.string.home_wheel_electronic, Color(0xFF3D4D99)),
    WaveChoice("jazz", R.string.home_wheel_jazz, Color(0xFF266B68)),
    WaveChoice("classical", R.string.home_wheel_classical, Color(0xFF635380)),
).associateBy(WaveChoice::id)

@Composable
internal fun HomeWaveWheel(
    order: List<String>, mixes: Map<String, HomeWheelMix>, playbackAllowed: Boolean, playing: Boolean,
    onPlay: (HomeWheelMix) -> Unit,
) {
    // Keep the visible order as soon as the listener engages, including across lazy item disposal.
    var frozen by rememberSaveable { mutableStateOf<List<String>?>(null) }
    val choices = (frozen ?: order).map(cards::getValue)
    val initial = (Int.MAX_VALUE / 2 / choices.size) * choices.size
    val wheel = rememberLazyListState(initialFirstVisibleItemIndex = initial)
    val scope = rememberCoroutineScope()
    val centerIndex by remember(wheel) {
        derivedStateOf {
            val layout = wheel.layoutInfo
            val middle = (layout.viewportStartOffset + layout.viewportEndOffset) / 2
            layout.visibleItemsInfo.minByOrNull { abs(it.offset + it.size / 2 - middle) }?.index
        }
    }
    LaunchedEffect(wheel.isScrollInProgress, playing) {
        if (frozen == null && (wheel.isScrollInProgress || playing)) frozen = order.toList()
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val width = maxWidth.coerceAtMost(480.dp) * .38f
            LazyRow(
                state = wheel, flingBehavior = rememberSnapFlingBehavior(wheel),
                modifier = Modifier.fillMaxWidth().testTag("home-wave-wheel"),
                contentPadding = PaddingValues(horizontal = (maxWidth - width) / 2, vertical = 22.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(Int.MAX_VALUE) { index ->
                    val choice = choices[index % choices.size]
                    val selected = index == centerIndex
                    Box(
                        Modifier.width(width).graphicsLayer {
                            val layout = wheel.layoutInfo
                            val cell = layout.visibleItemsInfo.firstOrNull { it.index == index }
                            val middle = (layout.viewportStartOffset + layout.viewportEndOffset) * .5f
                            val distance = if (cell == null) 0f else ((cell.offset + cell.size * .5f - middle) / size.width).coerceIn(-1.5f, 1.5f)
                            rotationZ = distance * 8f
                            translationY = abs(distance) * 14.dp.toPx()
                            scaleX = 1f - abs(distance) * .09f
                            scaleY = scaleX
                        }.clip(CircleShape)
                            .background(Brush.linearGradient(listOf(choice.tint, Color(0xFF242230)), start = Offset.Zero))
                            .then(if (selected) Modifier.border(2.dp, lerp(choice.tint, Color.White, .5f), CircleShape) else Modifier)
                            .selectable(selected, role = Role.RadioButton, onClick = {
                                if (frozen == null) frozen = order.toList()
                                scope.launch { wheel.animateScrollToItem(index) }
                            })
                            .heightIn(min = 84.dp).padding(horizontal = 6.dp, vertical = 14.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(stringResource(choice.label), style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center, color = Color.White)
                    }
                }
            }
        }
        val center = choices[(centerIndex ?: initial) % choices.size]
        val mix = mixes[center.id]
        Box(Modifier.heightIn(min = 48.dp), contentAlignment = Alignment.Center) {
            if (mix != null) {
                TextButton(enabled = playbackAllowed && !wheel.isScrollInProgress, onClick = {
                    if (frozen == null) frozen = order.toList()
                    onPlay(mix)
                }) {
                    Text(stringResource(R.string.home_wheel_play, stringResource(center.label)))
                }
            }
        }
    }
}
