package app.autplay.ui.desktop

import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp

internal enum class DesktopGlyph(val resource: String) {
    Home("home"), Library("library"), List("list"), Play("play"), Pause("pause"),
    Previous("previous"), Next("next"), Like("favorite"), Liked("favorite_filled"),
    Dislike("thumb_down"), Shuffle("shuffle"), Repeat("repeat"), History("timer"),
    Settings("settings"), Search("search"), Expand("expand"), Close("chevron_down"), Wave("wave"),
}

internal data class DesktopGlyphPath(val data: String, val filled: Boolean, val strokeWidth: Float)
internal expect fun desktopGlyphPaths(glyph: DesktopGlyph): List<DesktopGlyphPath>

@Composable
internal fun DesktopIcon(glyph: DesktopGlyph, description: String?, modifier: Modifier = Modifier) {
    val vector = remember(glyph) {
        val solid = SolidColor(Color.Black)
        ImageVector.Builder(glyph.resource, 24.dp, 24.dp, 24f, 24f).apply {
            for (path in desktopGlyphPaths(glyph)) {
                addPath(pathData = PathParser().parsePathString(path.data).toNodes(),
                    fill = if (path.filled) solid else null,
                    stroke = if (path.strokeWidth > 0f) solid else null,
                    strokeLineWidth = path.strokeWidth, strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
            }
        }.build()
    }
    Icon(vector, description, modifier)
}
