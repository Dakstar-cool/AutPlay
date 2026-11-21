package app.autplay.ui.desktop

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import app.autplay.ui.theme.AutPlayAccent
import app.autplay.ui.theme.autPlayColorScheme
import org.junit.Assert.assertTrue
import org.junit.Test

class AutPlayPaletteContrastTest {
    @Test
    fun readableRolesOnPlayerAndLibrarySurfaces() {
        for (accent in AutPlayAccent.entries) {
            for (dark in listOf(false, true)) {
                val colors = autPlayColorScheme(dark, accent)
                val surfaces = listOf(colors.background, colors.surface, colors.surfaceVariant, colors.surfaceDim,
                    colors.surfaceBright, colors.surfaceContainerLowest, colors.surfaceContainerLow,
                    colors.surfaceContainer, colors.surfaceContainerHigh, colors.surfaceContainerHighest,
                    colors.primaryContainer, colors.secondaryContainer)
                for (surface in surfaces) {
                    for (label in listOf(colors.onSurface, colors.onSurfaceVariant, colors.primary,
                        colors.secondary, colors.tertiary, colors.error)) {
                        assertContrast(label, surface, 4.5f)
                    }
                    assertContrast(colors.outline, surface, 3f)
                }
                for ((label, fill) in listOf(colors.onPrimary to colors.primary,
                    colors.onPrimaryContainer to colors.primaryContainer, colors.onSecondary to colors.secondary,
                    colors.onSecondaryContainer to colors.secondaryContainer, colors.onTertiary to colors.tertiary,
                    colors.onTertiaryContainer to colors.tertiaryContainer, colors.onError to colors.error,
                    colors.onErrorContainer to colors.errorContainer, colors.inverseOnSurface to colors.inverseSurface,
                    colors.inversePrimary to colors.inverseSurface, colors.onPrimaryFixed to colors.primaryFixedDim)) {
                    assertContrast(label, fill, 4.5f)
                }
                assertContrast(colors.primary, colors.outlineVariant, 3f) // played vs buffered
            }
        }
    }

    private fun assertContrast(foreground: Color, background: Color, minimum: Float) {
        val a = foreground.luminance() + 0.05f
        val b = background.luminance() + 0.05f
        val ratio = maxOf(a, b) / minOf(a, b)
        assertTrue("$foreground on $background has contrast $ratio (minimum $minimum)", ratio >= minimum)
    }
}
