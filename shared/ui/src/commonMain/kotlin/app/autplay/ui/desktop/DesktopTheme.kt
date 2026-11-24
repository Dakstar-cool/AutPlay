package app.autplay.ui.desktop

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import app.autplay.ui.theme.AutPlayAccent
import app.autplay.ui.theme.autPlayColorScheme
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

public enum class DesktopThemeMode(public val settingsValue: String) {
    Light("LIGHT"), Dark("DARK"), System("SYSTEM"),
}

public typealias DesktopAccentPalette = AutPlayAccent

internal fun desktopUsesDarkTheme(mode: DesktopThemeMode, systemDark: Boolean): Boolean = when (mode) {
    DesktopThemeMode.Light -> false
    DesktopThemeMode.Dark -> true
    DesktopThemeMode.System -> systemDark
}

internal fun desktopColorScheme(dark: Boolean, accent: DesktopAccentPalette): ColorScheme =
    autPlayColorScheme(dark, accent)

/** Product palette and typography, with no Android window dependencies. */
@Composable
public fun AutPlayDesktopTheme(
    mode: DesktopThemeMode,
    accentPalette: DesktopAccentPalette = DesktopAccentPalette.Coral,
    content: @Composable () -> Unit,
): Unit {
    val dark = desktopUsesDarkTheme(mode, isSystemInDarkTheme())
    MaterialTheme(
        colorScheme = remember(dark, accentPalette) { desktopColorScheme(dark, accentPalette) },
        shapes = DesktopShapes,
        typography = DesktopTypography,
        content = content,
    )
}

/** Retains source compatibility for callers passing content as their second positional argument. */
@Composable
public fun AutPlayDesktopTheme(mode: DesktopThemeMode, content: @Composable () -> Unit): Unit =
    AutPlayDesktopTheme(mode, DesktopAccentPalette.Coral, content)

private val DesktopShapes = Shapes(
    extraSmall = RoundedCornerShape(12.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(20.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

private val DesktopTypography = Typography(
    headlineLarge = TextStyle(fontSize = 32.sp, lineHeight = 37.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.8).sp),
    headlineMedium = TextStyle(fontSize = 27.sp, lineHeight = 33.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
)
