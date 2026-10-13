package app.autplay.ui.desktop

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

public enum class DesktopThemeMode { Light, Dark }

/** Coral palette and typography from the product theme, with no Android window dependencies. */
@Composable
public fun AutPlayDesktopTheme(mode: DesktopThemeMode, content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (mode == DesktopThemeMode.Dark) DesktopDarkColors else DesktopLightColors,
        shapes = DesktopShapes,
        typography = DesktopTypography,
        content = content,
    )
}

private val DesktopDarkColors = darkColorScheme(
    primary = Color(0xFFFF7854),
    onPrimary = Color(0xFF17120F),
    primaryContainer = Color(0xFF47271F),
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

private val DesktopLightColors = lightColorScheme(
    primary = Color(0xFFB73819),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFE2D7),
    onPrimaryContainer = Color.Black,
    secondary = Color(0xFF706D68),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFEAE6DE),
    onSecondaryContainer = Color(0xFF181818),
    tertiary = Color(0xFF247447),
    background = Color(0xFFF6F3ED),
    onBackground = Color(0xFF181818),
    surface = Color.White,
    onSurface = Color(0xFF181818),
    surfaceVariant = Color(0xFFFAF8F4),
    onSurfaceVariant = Color(0xFF706D68),
    surfaceContainerLowest = Color.White,
    surfaceContainerLow = Color(0xFFFAF8F4),
    surfaceContainer = Color(0xFFF1EEE8),
    surfaceContainerHigh = Color(0xFFEAE6DE),
    surfaceContainerHighest = Color(0xFFE2DED6),
    surfaceTint = Color.Transparent,
    outline = Color(0xFF817D76),
    outlineVariant = Color(0xFFE5E0D8),
    error = Color(0xFFB3261E),
)

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
