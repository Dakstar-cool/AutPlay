package app.autplay.ui

import android.animation.ValueAnimator
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import app.autplay.ui.theme.Twilight

@Composable
internal fun rememberSystemAnimationsEnabled(): Boolean {
    val context = LocalContext.current
    var enabled by remember(context) { mutableStateOf(ValueAnimator.areAnimatorsEnabled()) }
    DisposableEffect(context) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                enabled = ValueAnimator.areAnimatorsEnabled()
            }
        }
        val resolver = context.contentResolver
        resolver.registerContentObserver(
            Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE),
            false,
            observer,
        )
        enabled = ValueAnimator.areAnimatorsEnabled()
        onDispose { resolver.unregisterContentObserver(observer) }
    }
    return enabled
}

internal fun playbackVisualPalette(seed: String): List<Color> {
    val print = when (seed.hashCode().ushr(3) % 4) {
        0 -> Twilight.copper
        1 -> AutPlayAccent.Blue.controlFill
        else -> Twilight.steel
    }
    return listOf(Twilight.steel, print, Twilight.graphite, Twilight.beige)
}
