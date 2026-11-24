package app.autplay.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.view.WindowCompat
import app.autplay.ui.theme.autPlayColorScheme

/** User-selectable appearance values that can be persisted by the settings layer. */
public enum class AutPlayThemeMode {
    Light,
    Dark,
}

public typealias AutPlayAccent = app.autplay.ui.theme.AutPlayAccent

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
    public val miniPlayerScheme: ColorScheme,
    public val timerAccent: Color,
    public val success: Color,
    public val info: Color,
)

@Immutable
public data class AutPlayDimensions(
    public val screenPadding: androidx.compose.ui.unit.Dp = 20.dp,
    public val sectionSpacing: androidx.compose.ui.unit.Dp = 20.dp,
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
    val scheme = autPlayColorScheme(true, AutPlayAccent.Coral)
    semanticColors(scheme, true, AutPlayAccent.Coral)
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
        AutPlayThemeMode.Light -> false
        AutPlayThemeMode.Dark -> true
    }
    val scheme = remember(dark, appearance.accent) { autPlayColorScheme(dark, appearance.accent) }
    val semantic = remember(scheme) { semanticColors(scheme, dark, appearance.accent) }
    AutPlaySystemBarIconAppearance(
        useDarkStatusBarIcons = !dark,
        useDarkNavigationBarIcons = !dark,
    )
    androidx.compose.runtime.CompositionLocalProvider(
        LocalAutPlaySemanticColors provides semantic,
        LocalAutPlayDimensions provides AutPlayDimensions(),
    ) {
        MaterialTheme(
            colorScheme = scheme,
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

private fun semanticColors(scheme: ColorScheme, dark: Boolean, accent: AutPlayAccent) = AutPlaySemanticColors(
    raisedSurface = scheme.surfaceContainer,
    border = scheme.outlineVariant,
    mutedText = scheme.onSurfaceVariant,
    softAccent = scheme.primaryContainer,
    glassSurface = scheme.surfaceContainerHigh,
    glassBorder = scheme.outlineVariant,
    miniPlayerScheme = if (dark) scheme else autPlayColorScheme(true, accent),
    timerAccent = if (dark) AutPlayAccent.Violet.darkPrimary else AutPlayAccent.Violet.lightPrimary,
    success = scheme.tertiary,
    info = scheme.secondary,
)

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
