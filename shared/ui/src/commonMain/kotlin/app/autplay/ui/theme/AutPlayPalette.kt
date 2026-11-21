package app.autplay.ui.theme

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp

internal object Twilight {
    val graphite = Color(0xFF292D35)
    val steel = Color(0xFF304B63)
    val copper = Color(0xFFB96B43)
    val pine = Color(0xFF486451)
    val violet = Color(0xFF76648E)
    val beige = Color(0xFFE6D9C5)
    val ink = Color(0xFF101215)
    val paper = Color(0xFFF9F5EF)
}

/** The Coral identifier and wire value are retained for existing appearance preferences. */
public enum class AutPlayAccent(
    public val settingsValue: String,
    public val lightPrimary: Color,
    public val darkPrimary: Color,
    public val lightContainer: Color,
    public val darkContainer: Color,
    internal val controlFill: Color,
) {
    Coral("CORAL", Color(0xFF854529), Color(0xFFF2AD84), Color(0xFFF3DECB), Color(0xFF4D382F), Twilight.copper),
    Violet("VIOLET", lerp(Twilight.violet, Twilight.graphite, 0.30f), Color(0xFFD3C0DE), Color(0xFFE9DFEF), Color(0xFF413749), Color(0xFFA48DB6)),
    Green("GREEN", Color(0xFF405A49), Color(0xFFB5CAB7), Color(0xFFDEE8DC), Color(0xFF314639), Color(0xFF86A38D)),
    Blue("BLUE", Twilight.steel, Color(0xFFB2CCDF), Color(0xFFDBE5EB), Color(0xFF293F52), Color(0xFF819EB5)),
}

internal fun autPlayColorScheme(dark: Boolean, accent: AutPlayAccent): ColorScheme = if (dark) {
    darkColorScheme(
        primary = accent.darkPrimary,
        onPrimary = Twilight.ink,
        primaryContainer = accent.darkContainer,
        onPrimaryContainer = Twilight.beige,
        inversePrimary = accent.lightPrimary,
        secondary = AutPlayAccent.Blue.darkPrimary,
        onSecondary = Twilight.graphite,
        secondaryContainer = Twilight.steel,
        onSecondaryContainer = Twilight.beige,
        tertiary = AutPlayAccent.Green.darkPrimary,
        onTertiary = Twilight.graphite,
        tertiaryContainer = Twilight.pine,
        onTertiaryContainer = Twilight.beige,
        background = Twilight.graphite,
        onBackground = Twilight.beige,
        surface = Color(0xFF2B333D),
        onSurface = Twilight.beige,
        surfaceVariant = Twilight.steel,
        onSurfaceVariant = Color(0xFFC6C0B6),
        inverseSurface = Twilight.beige,
        inverseOnSurface = Twilight.graphite,
        surfaceDim = Twilight.graphite,
        surfaceBright = Twilight.steel,
        surfaceContainerLowest = Color(0xFF1B222B),
        surfaceContainerLow = Color(0xFF2A313A),
        surfaceContainer = Color(0xFF2D3844),
        surfaceContainerHigh = Color(0xFF2E4051),
        surfaceContainerHighest = Twilight.steel,
        surfaceTint = Color.Transparent,
        outline = Color(0xFF9BA6AE),
        outlineVariant = Color(0xFF4B5D6E),
        error = Color(0xFFF4B5A8),
        onError = Color(0xFF401E1B),
        errorContainer = Color(0xFF59332F),
        onErrorContainer = Color(0xFFF9DAD3),
        primaryFixed = accent.darkPrimary,
        primaryFixedDim = accent.controlFill,
        onPrimaryFixed = Twilight.ink,
        onPrimaryFixedVariant = Twilight.ink,
    )
} else {
    lightColorScheme(
        primary = accent.lightPrimary,
        onPrimary = Twilight.paper,
        primaryContainer = accent.lightContainer,
        onPrimaryContainer = Twilight.graphite,
        inversePrimary = accent.darkPrimary,
        secondary = Twilight.steel,
        onSecondary = Twilight.paper,
        secondaryContainer = AutPlayAccent.Blue.lightContainer,
        onSecondaryContainer = Twilight.graphite,
        tertiary = Twilight.pine,
        onTertiary = Twilight.paper,
        tertiaryContainer = AutPlayAccent.Green.lightContainer,
        onTertiaryContainer = Twilight.graphite,
        background = Color(0xFFF4EFE7),
        onBackground = Twilight.graphite,
        surface = Twilight.paper,
        onSurface = Twilight.graphite,
        surfaceVariant = Color(0xFFE5E8E7),
        onSurfaceVariant = Color(0xFF515B64),
        inverseSurface = Twilight.graphite,
        inverseOnSurface = Twilight.beige,
        surfaceDim = Twilight.beige,
        surfaceBright = Twilight.paper,
        surfaceContainerLowest = Color(0xFFFFFCF7),
        surfaceContainerLow = Color(0xFFF2EEE7),
        surfaceContainer = Color(0xFFECEAE4),
        surfaceContainerHigh = Color(0xFFE2E5E5),
        surfaceContainerHighest = Color(0xFFD6DFE2),
        surfaceTint = Color.Transparent,
        outline = Color(0xFF677581),
        outlineVariant = Color(0xFFACBBC4),
        error = Color(0xFF923D32),
        onError = Twilight.paper,
        errorContainer = Color(0xFFF5DAD4),
        onErrorContainer = Color(0xFF51241E),
        primaryFixed = accent.darkPrimary,
        primaryFixedDim = accent.controlFill,
        onPrimaryFixed = Twilight.ink,
        onPrimaryFixedVariant = Twilight.ink,
    )
}
