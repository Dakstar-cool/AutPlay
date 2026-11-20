package app.autplay.ui.player

import android.view.WindowManager
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionOnScreen
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.util.lerp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.DialogWindowProvider
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import app.autplay.R

@Composable
internal fun FullscreenPlayerArtwork(
    title: String,
    painter: Painter?,
    coverOnScreen: Rect,
    animations: Boolean,
    onClosed: () -> Unit,
) {
    var closing by remember { mutableStateOf(false) }
    Dialog(
        onDismissRequest = { closing = true },
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        // Only this window goes immersive. Closing it restores the player's system bars.
        val view = LocalView.current
        DisposableEffect(view) {
            val window = (view.parent as DialogWindowProvider).window
            window.clearFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            window.setWindowAnimations(0)
            val controller = WindowCompat.getInsetsController(window, view)
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
            onDispose { }
        }

        val progress = remember { Animatable(if (animations) 0f else 1f) }
        var origin by remember { mutableStateOf<Offset?>(null) }
        LaunchedEffect(origin != null, closing, animations) {
            if (origin == null) return@LaunchedEffect
            val target = if (closing) 0f else 1f
            if (animations) progress.animateTo(target, tween(if (closing) 200 else 280))
            else progress.snapTo(target)
            if (closing) onClosed()
        }
        BoxWithConstraints(
            Modifier.fillMaxSize()
                .onGloballyPositioned { origin = it.positionOnScreen() }
                .drawBehind { drawRect(Color.Black, alpha = progress.value) }
                .pointerInput(Unit) { detectTapGestures { closing = true } }
                .testTag("fullscreen-artwork"),
            contentAlignment = Alignment.Center,
        ) {
            val center = Offset(constraints.maxWidth / 2f, constraints.maxHeight / 2f)
            PlayerArtwork(
                title = title,
                painter = painter,
                shape = RectangleShape,
                modifier = Modifier.aspectRatio(painter.artworkAspectRatio()).graphicsLayer {
                    val start = origin
                    if (start != null && !coverOnScreen.isEmpty && size.width > 0f) {
                        val t = progress.value
                        val scale = lerp(coverOnScreen.width / size.width, 1f, t)
                        scaleX = scale
                        scaleY = scale
                        val offset = (coverOnScreen.center - start - center) * (1f - t)
                        translationX = offset.x
                        translationY = offset.y
                    }
                    alpha = if (start != null) 1f else 0f
                }.testTag("fullscreen-artwork-image"),
            )
            IconButton(
                onClick = { closing = true },
                modifier = Modifier.align(Alignment.TopStart).windowInsetsPadding(WindowInsets.safeDrawing)
                    .padding(12.dp).graphicsLayer { alpha = progress.value }
                    .background(Color.Black.copy(alpha = 0.64f), CircleShape),
            ) {
                Icon(painterResource(R.drawable.ic_autplay_back), stringResource(R.string.player_close_artwork), tint = Color.White)
            }
        }
    }
}
