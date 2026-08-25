package app.autplay.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat

/** User-selectable appearance values that can be persisted by the settings layer. */
public enum class AutPlayThemeMode {
    System,
    Light,
    Dark,
}

public enum class AutPlayAccent(
    public val lightPrimary: Color,
    public val darkPrimary: Color,
    public val lightContainer: Color,
    public val darkContainer: Color,
) {
    Coral(Color(0xFFFF5B35), Color(0xFFFF6B45), Color(0xFFFFE2D7), Color(0xFF3C211B)),
    Violet(Color(0xFF6246A8), Color(0xFFDCCEFF), Color(0xFFE8DEFF), Color(0xFF4B3A73)),
    Green(Color(0xFF176B3A), Color(0xFF83F8A6), Color(0xFFC2F8D1), Color(0xFF19562D)),
    Blue(Color(0xFF00639B), Color(0xFFA8D8FF), Color(0xFFCDE5FF), Color(0xFF174A68)),
}

public data class AutPlayAppearance(
    public val mode: AutPlayThemeMode = AutPlayThemeMode.System,
    public val accent: AutPlayAccent = AutPlayAccent.Coral,
)

@Immutable
public data class AutPlaySemanticColors(
    public val raisedSurface: Color,
    public val border: Color,
    public val mutedText: Color,
    public val softAccent: Color,
    public val glassSurface: Color,
    public val glassBorder: Color,
    public val miniPlayerSurface: Color,
    public val onMiniPlayer: Color,
    public val success: Color,
    public val info: Color,
)

@Immutable
public data class AutPlayDimensions(
    public val screenPadding: androidx.compose.ui.unit.Dp = 20.dp,
    public val sectionSpacing: androidx.compose.ui.unit.Dp = 28.dp,
    public val cardRadius: androidx.compose.ui.unit.Dp = 22.dp,
    public val compactRadius: androidx.compose.ui.unit.Dp = 14.dp,
    public val minimumTouchTarget: androidx.compose.ui.unit.Dp = 48.dp,
)

public object AutPlayTokens {
    public val colors: AutPlaySemanticColors
        @Composable get() = LocalAutPlaySemanticColors.current
    public val dimensions: AutPlayDimensions
        @Composable get() = LocalAutPlayDimensions.current
}

private val LocalAutPlaySemanticColors = staticCompositionLocalOf {
    semanticColors(dark = false, accent = AutPlayAccent.Coral)
}
private val LocalAutPlayDimensions = staticCompositionLocalOf { AutPlayDimensions() }

/**
 * Material 3-only theme for AutPlay surfaces. Parent state owns persistence and provides [appearance].
 */
@Composable
public fun AutPlayTheme(
    appearance: AutPlayAppearance = AutPlayAppearance(),
    content: @Composable () -> Unit,
) {
    val dark = when (appearance.mode) {
        AutPlayThemeMode.System -> isSystemInDarkTheme()
        AutPlayThemeMode.Light -> false
        AutPlayThemeMode.Dark -> true
    }
    AutPlaySystemBarIconAppearance(
        useDarkStatusBarIcons = !dark,
        useDarkNavigationBarIcons = !dark,
    )
    androidx.compose.runtime.CompositionLocalProvider(
        LocalAutPlaySemanticColors provides semanticColors(dark, appearance.accent),
        LocalAutPlayDimensions provides AutPlayDimensions(),
    ) {
        MaterialTheme(
            colorScheme = autPlayColorScheme(appearance.accent, dark),
            typography = AutPlayTypography,
            shapes = AutPlayShapes,
            content = content,
        )
    }
}

/** Route surfaces may override the theme default when edge-to-edge content is intentionally dark. */
@Composable
internal fun AutPlaySystemBarIconAppearance(
    useDarkStatusBarIcons: Boolean,
    useDarkNavigationBarIcons: Boolean,
) {
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = view.context.findActivity()?.window ?: return@SideEffect
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = useDarkStatusBarIcons
                isAppearanceLightNavigationBars = useDarkNavigationBarIcons
            }
        }
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private fun autPlayColorScheme(accent: AutPlayAccent, dark: Boolean): ColorScheme = if (dark) {
    darkColorScheme(
        primary = accent.darkPrimary,
        onPrimary = Color.Black,
        primaryContainer = accent.darkContainer,
        onPrimaryContainer = Color.White,
        secondary = Color(0xFFB8A7D9),
        tertiary = Color(0xFF78D79A),
        background = Color(0xFF08090B),
        onBackground = Color(0xFFF6F4F1),
        surface = Color(0xFF111216),
        onSurface = Color(0xFFF6F4F1),
        surfaceVariant = Color(0xFF1A1B20),
        onSurfaceVariant = Color(0xFFA7A5AB),
        outline = Color(0xFF45464E),
        error = Color(0xFFFFB4AB),
    )
} else {
    lightColorScheme(
        primary = accent.lightPrimary,
        onPrimary = Color.White,
        primaryContainer = accent.lightContainer,
        onPrimaryContainer = Color.Black,
        secondary = Color(0xFF6B56A6),
        tertiary = Color(0xFF247447),
        background = Color(0xFFF6F3ED),
        onBackground = Color(0xFF181818),
        surface = Color(0xFFFFFFFF),
        onSurface = Color(0xFF181818),
        surfaceVariant = Color(0xFFFAF8F4),
        onSurfaceVariant = Color(0xFF706D68),
        outline = Color(0xFF817D76),
        error = Color(0xFFB3261E),
    )
}

private fun semanticColors(dark: Boolean, accent: AutPlayAccent): AutPlaySemanticColors =
    if (dark) {
        AutPlaySemanticColors(
            raisedSurface = Color(0xFF191A1F),
            border = Color(0xFF34353D),
            mutedText = Color(0xFFA7A5AB),
            softAccent = accent.darkContainer,
            glassSurface = Color(0xB81B1C21),
            glassBorder = Color(0x3DFFFFFF),
            miniPlayerSurface = Color(0xF21A1B20),
            onMiniPlayer = Color(0xFFF6F4F1),
            success = Color(0xFF78D79A),
            info = Color(0xFF8FCBFF),
        )
    } else {
        AutPlaySemanticColors(
            raisedSurface = Color(0xFFFAF8F4),
            border = Color(0xFFE5E0D8),
            mutedText = Color(0xFF706D68),
            softAccent = accent.lightContainer,
            glassSurface = Color(0xD9FFFFFF),
            glassBorder = Color(0x33000000),
            miniPlayerSurface = Color(0xF224211F),
            onMiniPlayer = Color(0xFFF5F2EC),
            success = Color(0xFF247447),
            info = Color(0xFF21618C),
        )
    }

private val AutPlayShapes = Shapes(
    extraSmall = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
    small = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
    medium = androidx.compose.foundation.shape.RoundedCornerShape(20.dp),
    large = androidx.compose.foundation.shape.RoundedCornerShape(24.dp),
    extraLarge = androidx.compose.foundation.shape.RoundedCornerShape(30.dp),
)

private val AutPlayTypography = Typography(
    displaySmall = TextStyle(fontSize = 42.sp, lineHeight = 46.sp, fontWeight = FontWeight.Bold),
    headlineLarge = TextStyle(fontSize = 34.sp, lineHeight = 39.sp, fontWeight = FontWeight.Bold),
    headlineMedium = TextStyle(fontSize = 28.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold),
    headlineSmall = TextStyle(fontSize = 23.sp, lineHeight = 29.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.Normal),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp, fontWeight = FontWeight.Normal),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
)
