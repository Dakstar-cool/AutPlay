package app.autplay.application.artist

import java.util.Locale

private const val editLabel = """(?:sp(?:ed|eed)[ -]?up|speed|nightcore|(?:(?:super|ultra)\s+)?slow(?:ed)?(?:[ -]?down)?(?:\s*(?:\+|&|and)\s*reverb)?|(?:(?:high[ -]quality|hq)\s+)?instrumental)(?:\s+version)?"""
private val wholeLabel = Regex(editLabel, RegexOption.IGNORE_CASE)
private val editSuffix = Regex(
    """(\s*\(\s*|\s*\[\s*|\s*\|\s*|\s+[-–—]\s+|\s+)($editLabel)(\s*[\])])?$""", RegexOption.IGNORE_CASE)
private val literalWords = setOf("speed", "slow", "speed up", "slow down", "instrumental")
private val cyrillic = Regex("[\\u0400-\\u04ff]")

internal fun sourceEditTitle(title: String): String {
    val match = editSuffix.find(title) ?: return title
    val base = title.take(match.range.first).trimEnd()
    if (base.isEmpty() || wholeLabel.matches(title)) return title
    val opening = match.groupValues[1].trim()
    val closing = when (opening) { "(" -> ")"; "[" -> "]"; else -> "" }
    if (match.groupValues[3].trim() != closing) return title
    val label = musicDisplayTitle(match.groupValues[2]).lowercase(Locale.ROOT)
    if (opening.isEmpty() && label in literalWords && !cyrillic.containsMatchIn(base)) return title
    val value = label.removeSuffix(" version")
    val edition = when {
        value.endsWith("instrumental") -> "Instrumental"
        value.startsWith("sp") -> "Sped Up"
        value == "nightcore" -> "Nightcore"
        else -> {
            val prefix = when (value.substringBefore(' ')) { "super" -> "Super "; "ultra" -> "Ultra "; else -> "" }
            prefix + "Slowed" + if (value.endsWith("reverb")) " + Reverb" else ""
        }
    }
    return "$base ($edition)"
}

private val phrase = Regex("""\p{L}+(?:'\p{L}+)*(?: \p{L}+(?:'\p{L}+)*)+""")

internal fun compactSpelling(key: String): String? =
    if (key.length >= 12 && phrase.matches(key)) key.replace(" ", "").replace("'", "").takeIf { it.length >= 12 }
    else null

internal class TrackTitleQuery(query: String) {
    private val original = musicSearchKey(query)
    private val key = musicSearchKey(sourceEditTitle(sessionTitle(stripSourceSite(query)))).ifEmpty { original }
    private val joined = compactSpelling(key)

    fun matches(title: String?): Boolean {
        if (title == null) return false
        val name = musicSearchKey(title)
        if (original in name || key in name) return true
        val base = sourceEditTitle(name).substringBeforeLast(" (")
        if (joined != null && base.all(Char::isLetter) && joined in base) return true
        return key.length >= 12 && key.all(Char::isLetter) && compactSpelling(base) == key
    }
}
