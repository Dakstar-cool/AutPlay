package app.autplay.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

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
    Coral(Color(0xFFB42318), Color(0xFFFFB4AB), Color(0xFFFFDAD5), Color(0xFF7F1D1D)),
    Violet(Color(0xFF6246A8), Color(0xFFDCCEFF), Color(0xFFE8DEFF), Color(0xFF4B3A73)),
    Green(Color(0xFF176B3A), Color(0xFF83F8A6), Color(0xFFC2F8D1), Color(0xFF19562D)),
    Blue(Color(0xFF00639B), Color(0xFFA8D8FF), Color(0xFFCDE5FF), Color(0xFF174A68)),
}

public data class AutPlayAppearance(
    public val mode: AutPlayThemeMode = AutPlayThemeMode.System,
    public val accent: AutPlayAccent = AutPlayAccent.Coral,
)

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
    MaterialTheme(colorScheme = autPlayColorScheme(appearance.accent, dark), content = content)
}

private fun autPlayColorScheme(accent: AutPlayAccent, dark: Boolean): ColorScheme = if (dark) {
    darkColorScheme(
        primary = accent.darkPrimary,
        onPrimary = Color.Black,
        primaryContainer = accent.darkContainer,
        onPrimaryContainer = Color.White,
        secondary = Color(0xFFE4BDB7),
        background = Color(0xFF141111),
        surface = Color(0xFF1C1919),
        surfaceVariant = Color(0xFF514341),
    )
} else {
    lightColorScheme(
        primary = accent.lightPrimary,
        onPrimary = Color.White,
        primaryContainer = accent.lightContainer,
        onPrimaryContainer = Color.Black,
        secondary = Color(0xFF765653),
        background = Color(0xFFFFF8F7),
        surface = Color(0xFFFFF8F7),
        surfaceVariant = Color(0xFFF6DDDA),
    )
}
