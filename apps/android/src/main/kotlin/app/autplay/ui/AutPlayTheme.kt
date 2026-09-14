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
    Coral(Color(0xFFB73819), Color(0xFFFF7854), Color(0xFFFFE2D7), Color(0xFF47271F)),
    Violet(Color(0xFF6246A8), Color(0xFFDCCEFF), Color(0xFFE8DEFF), Color(0xFF4B3A73)),
    Green(Color(0xFF176B3A), Color(0xFF83F8A6), Color(0xFFC2F8D1), Color(0xFF19562D)),
    Blue(Color(0xFF00639B), Color(0xFFA8D8FF), Color(0xFFCDE5FF), Color(0xFF174A68)),
}

public data class AutPlayAppearance(
    public val mode: AutPlayThemeMode = AutPlayThemeMode.Dark,
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
    public val cardRadius: androidx.compose.ui.unit.Dp = 18.dp,
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
        onPrimary = Color(0xFF17120F),
        primaryContainer = accent.darkContainer,
        onPrimaryContainer = Color.White,
        secondary = Color(0xFFC4C6CD),
        onSecondary = Color(0xFF191B20),
        secondaryContainer = Color(0xFF30333A),
        onSecondaryContainer = Color(0xFFF1F1F4),
        tertiary = Color(0xFFBBDD82),
        background = Color(0xFF111215),
        onBackground = Color(0xFFF4F3EF),
        surface = Color(0xFF181A1F),
        onSurface = Color(0xFFF4F3EF),
        surfaceVariant = Color(0xFF24272E),
        onSurfaceVariant = Color(0xFFB2B4BD),
        surfaceDim = Color(0xFF111215),
        surfaceBright = Color(0xFF363941),
        surfaceContainerLowest = Color(0xFF0C0D10),
        surfaceContainerLow = Color(0xFF17191E),
        surfaceContainer = Color(0xFF1D1F25),
        surfaceContainerHigh = Color(0xFF272A31),
        surfaceContainerHighest = Color(0xFF33363E),
        surfaceTint = Color.Transparent,
        outline = Color(0xFF737780),
        outlineVariant = Color(0xFF32353C),
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
        surfaceContainerLowest = Color(0xFFFFFFFF),
        surfaceContainerLow = Color(0xFFFAF8F4),
        surfaceContainer = Color(0xFFF1EEE8),
        surfaceContainerHigh = Color(0xFFEAE6DE),
        surfaceContainerHighest = Color(0xFFE2DED6),
        surfaceTint = Color.Transparent,
        outline = Color(0xFF817D76),
        error = Color(0xFFB3261E),
    )
}

private fun semanticColors(dark: Boolean, accent: AutPlayAccent): AutPlaySemanticColors =
    if (dark) {
        AutPlaySemanticColors(
            raisedSurface = Color(0xFF202229),
            border = Color(0xFF32353C),
            mutedText = Color(0xFFB2B4BD),
            softAccent = accent.darkContainer,
            glassSurface = Color(0xF024272E),
            glassBorder = Color(0x24FFFFFF),
            miniPlayerSurface = Color(0xFF292C34),
            onMiniPlayer = Color(0xFFF4F3EF),
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
    small = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
    medium = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
    large = androidx.compose.foundation.shape.RoundedCornerShape(20.dp),
    extraLarge = androidx.compose.foundation.shape.RoundedCornerShape(28.dp),
)

private val AutPlayTypography = Typography(
    displaySmall = TextStyle(fontSize = 38.sp, lineHeight = 42.sp, fontWeight = FontWeight.Bold, letterSpacing = (-1).sp),
    headlineLarge = TextStyle(fontSize = 32.sp, lineHeight = 37.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.8).sp),
    headlineMedium = TextStyle(fontSize = 27.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
    headlineSmall = TextStyle(fontSize = 23.sp, lineHeight = 29.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.Normal),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp, fontWeight = FontWeight.Normal),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
)
