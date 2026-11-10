package app.autplay.desktop.presentation

import app.autplay.ui.desktop.DesktopCatalogEntry
import java.text.Normalizer
import java.util.Locale

/** A bounded process-only page filter. It does not replace or allocate the standalone FTS owner. */
internal class DesktopAccountCatalogQuery(query: String) {
    private val tokens: List<String> = words(query)
    private val active: Boolean = query.isNotBlank()

    fun includes(entry: DesktopCatalogEntry): Boolean {
        if (!active) return true
        if (tokens.isEmpty()) return false
        val values = words(listOfNotNull(entry.title, entry.subtitle).joinToString(" "))
        return tokens.all { token -> values.any { it.startsWith(token) } }
    }

    companion object {
        fun accepts(query: String): Boolean = query.length <= 256 && words(query).let { tokens ->
            (query.isBlank() || tokens.isNotEmpty()) && tokens.size <= 8 && tokens.all { it.length <= 48 }
        }

        private fun words(value: String): List<String> = Normalizer.normalize(value, Normalizer.Form.NFKC)
            .lowercase(Locale.ROOT).split(Regex("[^\\p{L}\\p{N}]+" )).filter { it.isNotEmpty() }
    }
}
