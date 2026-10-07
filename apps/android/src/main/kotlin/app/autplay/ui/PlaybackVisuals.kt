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

internal fun playbackVisualPalette(seed: String): List<Color> = when (seed.hashCode().ushr(1) % 5) {
    0 -> listOf(Color(0xFFE86339), Color(0xFFF6B46C), Color(0xFF643129), Color(0xFFFFDED0))
    1 -> listOf(Color(0xFF376DAD), Color(0xFF81BBD1), Color(0xFF203950), Color(0xFFD9EBEB))
    2 -> listOf(Color(0xFF9070B5), Color(0xFFCBA8CE), Color(0xFF392C55), Color(0xFFF1DAE9))
    3 -> listOf(Color(0xFF55795A), Color(0xFFBBDD82), Color(0xFF253E38), Color(0xFFE3ECCB))
    else -> listOf(Color(0xFFDB6746), Color(0xFFF1A66A), Color(0xFF492C31), Color(0xFFFFE7CA))
}
